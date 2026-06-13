"""Portfolio backtest engine — multi-symbol, concurrent positions.

Why this exists
---------------
The single-coin engine treats each symbol as its own $10K bankroll. That
over-counts variance: if 1 of 11 coins prints a moon trade, the "avg
return" looks great even though a real account couldn't have held all
11 at full size simultaneously.

This engine simulates a SINGLE account that:
  1. Looks at all symbols every bar.
  2. Can hold up to `max_concurrent` positions at once.
  3. Sizes each position as a fraction of CURRENT equity.
  4. Applies all the same rules as the per-symbol engine (stops, trail,
     TP, time stop, exit_trigger, per-bar funding).

Decisions encoded here (all documented for clarity):
  - When more entry triggers fire than slots available, we take them in
    deterministic alphabetical order. (A real strategy would rank by
    edge / signal strength.)
  - Position size = current_equity * position_fraction.
    If position_fraction = 1/max_concurrent, a fully-loaded portfolio
    deploys ~100% of capital.
  - One position per symbol at a time (no doubling up).
  - Equity curve is recorded at portfolio level (sum of cash + MtM of
    all open positions), so drawdown is portfolio drawdown.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from crypto_quant.backtest.engine import BacktestConfig, Trade


@dataclass
class PortfolioConfig:
    max_concurrent: int = 5
    # Sizing is the fraction of CURRENT equity per position.
    # Default leaves cash buffer (max_concurrent * position_fraction <= 1.0).
    position_fraction: float = 0.20  # 5 * 0.20 = 100% max deployed
    # When more entries fire than slots, how to break the tie:
    #   "alpha" -- alphabetical symbol order (deterministic, default)
    #   "rank"  -- highest `entry_strength` column first (must be in signal df)
    tiebreaker: str = "alpha"


@dataclass
class PortfolioResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    config: BacktestConfig = field(default_factory=BacktestConfig)
    portfolio_cfg: PortfolioConfig = field(default_factory=PortfolioConfig)
    per_symbol_trades: dict[str, int] = field(default_factory=dict)

    @property
    def num_trades(self) -> int:
        return len(self.trades)

    @property
    def total_return_pct(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        start = self.config.initial_capital
        end = float(self.equity_curve["equity"].iloc[-1])
        return (end / start - 1) * 100

    @property
    def win_rate_pct(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.total_pnl_usd > 0)
        return wins / len(self.trades) * 100


def _extract_per_bar(
    df: pd.DataFrame,
    bt_cfg: BacktestConfig,
) -> dict:
    """Pre-extract columns for fast per-bar lookup. Returns dict of lists."""
    n = len(df)
    return {
        "open": df["open"].astype(float).tolist(),
        "high": df["high"].astype(float).tolist(),
        "low": df["low"].astype(float).tolist(),
        "close": df["close"].astype(float).tolist(),
        "atr": df["atr"].astype(float).tolist(),
        "entry": df["entry_trigger"].astype(bool).tolist()
            if "entry_trigger" in df.columns
            else df["entry_signal"].astype(bool).tolist(),
        "exit": df["exit_trigger"].astype(bool).tolist()
            if "exit_trigger" in df.columns
            else [False] * n,
        "direction": df["direction"].astype(str).tolist()
            if "direction" in df.columns
            else ["long"] * n,
        "funding": (
            df["funding_rate_per_8h_pct"].fillna(0.0).astype(float).tolist()
            if "funding_rate_per_8h_pct" in df.columns
            else [bt_cfg.funding_rate_per_8h_pct] * n
        ),
        "strength": (
            df["entry_strength"].fillna(0.0).astype(float).tolist()
            if "entry_strength" in df.columns
            else [0.0] * n
        ),
    }


def run_portfolio_backtest(
    signals_by_symbol: dict[str, pd.DataFrame],
    *,
    strat_cfg,
    bt_cfg: BacktestConfig | None = None,
    pf_cfg: PortfolioConfig | None = None,
) -> PortfolioResult:
    """Run a portfolio backtest across multiple symbols.

    signals_by_symbol: dict of symbol -> signal df (output of strategy).
    Each df must include: timestamp, open, high, low, close, atr,
    entry_trigger (or entry_signal). Optional: direction, exit_trigger,
    funding_rate_per_8h_pct.

    All dfs are aligned on a unified timeline. Bars missing in a symbol
    are skipped for that symbol on that bar.
    """
    bt_cfg = bt_cfg or BacktestConfig()
    pf_cfg = pf_cfg or PortfolioConfig()

    # Build unified timeline (union of all timestamps), deterministically sorted.
    all_ts = sorted(
        {ts for df in signals_by_symbol.values() for ts in df["timestamp"]}
    )
    if not all_ts:
        return PortfolioResult(
            equity_curve=pd.DataFrame(), config=bt_cfg, portfolio_cfg=pf_cfg
        )

    # Build per-symbol bar-index lookup: ts -> i
    symbols = sorted(signals_by_symbol.keys())  # deterministic ordering
    per_sym: dict[str, dict] = {}
    for sym in symbols:
        df = signals_by_symbol[sym].sort_values("timestamp").reset_index(drop=True)
        ts_to_i = {ts: i for i, ts in enumerate(df["timestamp"].tolist())}
        per_sym[sym] = {
            "ts_to_i": ts_to_i,
            "data": _extract_per_bar(df, bt_cfg),
        }

    equity = bt_cfg.initial_capital
    open_positions: dict[str, dict] = {}  # sym -> position dict
    last_exit_i: dict[str, int] = {}      # sym -> last exit ts-index on unified line
    trades: list[Trade] = []
    equity_rows: list[dict] = []

    slip = bt_cfg.slippage_pct / 100
    max_bars = strat_cfg.max_hold_hours
    cooldown = strat_cfg.cooldown_bars
    atr_mult = strat_cfg.atr_stop_mult
    trail_frac = strat_cfg.trail_pct / 100 if strat_cfg.trail_pct > 0 else 0.0
    tp_pct = getattr(strat_cfg, "take_profit_pct", 0.0) or 0.0
    tp_frac = tp_pct / 100 if tp_pct > 0 else 0.0

    for global_i, ts in enumerate(all_ts):
        # --- Phase A: manage open positions (exits) ---
        to_close: list[tuple[str, float, str]] = []
        for sym, pos in open_positions.items():
            i = per_sym[sym]["ts_to_i"].get(ts)
            if i is None:
                continue
            d = per_sym[sym]["data"]
            sign = pos["sign"]
            bars_held = global_i - pos["entry_global_i"]

            # Trailing reference + stops
            trail_stop = (
                pos["extreme_close"] * (1 - sign * trail_frac)
                if trail_frac > 0
                else None
            )
            if trail_stop is None:
                active_stop = pos["initial_stop"]
            elif sign == 1:
                active_stop = max(pos["initial_stop"], trail_stop)
            else:
                active_stop = min(pos["initial_stop"], trail_stop)

            exit_px = None
            reason = ""
            high_i, low_i, close_i = d["high"][i], d["low"][i], d["close"][i]
            if sign == 1:
                if low_i <= active_stop:
                    exit_px = active_stop * (1 - slip)
                    reason = (
                        "trail_stop"
                        if trail_stop is not None and trail_stop > pos["initial_stop"]
                        else "initial_stop"
                    )
                elif pos["tp_level"] is not None and high_i >= pos["tp_level"]:
                    exit_px = pos["tp_level"] * (1 - slip)
                    reason = "take_profit"
            else:
                if high_i >= active_stop:
                    exit_px = active_stop * (1 + slip)
                    reason = (
                        "trail_stop"
                        if trail_stop is not None and trail_stop < pos["initial_stop"]
                        else "initial_stop"
                    )
                elif pos["tp_level"] is not None and low_i <= pos["tp_level"]:
                    exit_px = pos["tp_level"] * (1 + slip)
                    reason = "take_profit"

            if exit_px is None and bars_held >= max_bars:
                exit_px = close_i * (1 - sign * slip)
                reason = "max_hold"
            if exit_px is None and i > 0 and d["exit"][i - 1]:
                exit_px = d["open"][i] * (1 - sign * slip)
                reason = "exit_signal"

            if exit_px is not None:
                to_close.append((sym, exit_px, reason))

        for sym, exit_px, reason in to_close:
            pos = open_positions.pop(sym)
            sign = pos["sign"]
            entry_px = pos["entry_price"]
            size = pos["size"]
            gross_ret = sign * (exit_px - entry_px) / entry_px
            entry_notional = size
            exit_notional = size * (1 + gross_ret)
            fees = (entry_notional + exit_notional) * bt_cfg.fee_rate
            pnl_usd = size * gross_ret - fees
            equity += pnl_usd
            funding_usd = pos["funding_accum"]
            trades.append(
                Trade(
                    entry_time=pos["entry_time"],
                    exit_time=ts,
                    entry_price=entry_px,
                    exit_price=exit_px,
                    bars_held=global_i - pos["entry_global_i"],
                    pnl_pct=gross_ret * 100,
                    pnl_usd=pnl_usd,
                    exit_reason=reason,
                    direction=pos["direction"],
                    funding_usd=funding_usd,
                    total_pnl_usd=pnl_usd + funding_usd,
                )
            )
            trades[-1].symbol = sym  # tack on symbol (not in Trade schema)
            last_exit_i[sym] = global_i

        # --- Phase B: process entries (previous bar's triggers) ---
        # Only fire if a slot is available and we haven't doubled up.
        if global_i > 0 and len(open_positions) < pf_cfg.max_concurrent:
            candidates: list[tuple[str, int, float]] = []  # (sym, cur_i, strength)
            for sym in symbols:
                if sym in open_positions:
                    continue
                if global_i - last_exit_i.get(sym, -(10**9)) <= cooldown:
                    continue
                # Prev bar in this symbol's frame: need ts at global_i-1 to exist
                prev_ts = all_ts[global_i - 1]
                prev_i = per_sym[sym]["ts_to_i"].get(prev_ts)
                cur_i = per_sym[sym]["ts_to_i"].get(ts)
                if prev_i is None or cur_i is None:
                    continue
                d = per_sym[sym]["data"]
                if d["entry"][prev_i]:
                    candidates.append((sym, cur_i, d["strength"][prev_i]))
            # Tie-break: rank by signal strength (desc) when configured.
            if pf_cfg.tiebreaker == "rank":
                candidates.sort(key=lambda c: c[2], reverse=True)
            # Otherwise candidates are already in alphabetical order.
            slots = pf_cfg.max_concurrent - len(open_positions)
            for sym, cur_i, _strength in candidates[:slots]:
                d = per_sym[sym]["data"]
                prev_ts = all_ts[global_i - 1]
                prev_i = per_sym[sym]["ts_to_i"][prev_ts]
                direction = (
                    d["direction"][prev_i]
                    if d["direction"][prev_i] in ("long", "short")
                    else "long"
                )
                sign = 1 if direction == "long" else -1
                fill = d["open"][cur_i] * (1 + slip * sign)
                atr_at_entry = (
                    d["atr"][prev_i] if pd.notna(d["atr"][prev_i]) else 0.0
                )
                if atr_mult > 0 and atr_at_entry > 0:
                    initial_stop = fill - sign * atr_mult * atr_at_entry
                else:
                    fallback = max(getattr(strat_cfg, "stop_loss_pct", 0.0), 6.0) / 100
                    initial_stop = fill * (1 - sign * fallback)
                tp_level = fill * (1 + sign * tp_frac) if tp_frac > 0 else None
                size = equity * pf_cfg.position_fraction
                open_positions[sym] = {
                    "entry_global_i": global_i,
                    "entry_time": ts,
                    "entry_price": fill,
                    "size": size,
                    "initial_stop": initial_stop,
                    "extreme_close": fill,
                    "direction": direction,
                    "sign": sign,
                    "tp_level": tp_level,
                    "funding_accum": 0.0,
                }

        # --- Phase C: update trailing refs + funding + mark-to-market ---
        mtm_delta = 0.0
        for sym, pos in open_positions.items():
            i = per_sym[sym]["ts_to_i"].get(ts)
            if i is None:
                continue
            d = per_sym[sym]["data"]
            sign = pos["sign"]
            close_i = d["close"][i]
            if sign == 1 and close_i > pos["extreme_close"]:
                pos["extreme_close"] = close_i
            elif sign == -1 and close_i < pos["extreme_close"]:
                pos["extreme_close"] = close_i
            unreal = sign * (close_i - pos["entry_price"]) / pos["entry_price"]
            mtm_delta += pos["size"] * unreal

            rate = d["funding"][i]
            if (
                rate != 0
                and hasattr(ts, "hour")
                and ts.hour in (0, 8, 16)
            ):
                cur_notional = pos["size"] * (1 + unreal)
                funding_cost = cur_notional * rate / 100
                received = -sign * funding_cost
                equity += received
                pos["funding_accum"] += received

        equity_rows.append({"timestamp": ts, "equity": equity + mtm_delta})

    # Sym counts
    per_sym_counts: dict[str, int] = {}
    for t in trades:
        sym = getattr(t, "symbol", "?")
        per_sym_counts[sym] = per_sym_counts.get(sym, 0) + 1

    return PortfolioResult(
        trades=trades,
        equity_curve=pd.DataFrame(equity_rows),
        config=bt_cfg,
        portfolio_cfg=pf_cfg,
        per_symbol_trades=per_sym_counts,
    )


def format_portfolio_report(
    result: PortfolioResult,
    *,
    bnh_ret_pct: float | None = None,
    label: str = "",
) -> str:
    n = len(result.trades)
    if not n:
        return f"=== Portfolio [{label}] === no trades"
    total_pnl = sum(t.total_pnl_usd for t in result.trades)
    total_funding = sum(t.funding_usd for t in result.trades)
    wins = sum(1 for t in result.trades if t.total_pnl_usd > 0)
    ret_pct = result.total_return_pct
    eq = result.equity_curve["equity"]
    peak = eq.cummax()
    dd = ((eq - peak) / peak * 100).min() if len(eq) else 0.0
    lines = [
        f"=== Portfolio backtest [{label}] ===",
        f"  trades:        {n}    (wins {wins}/{n} = {wins/n*100:.1f}%)",
        f"  total return:  {ret_pct:+.2f}%",
        f"  total PnL:     ${total_pnl:+.2f}  (funding: ${total_funding:+.2f})",
        f"  max drawdown:  {dd:.2f}%",
        f"  max concur:    {result.portfolio_cfg.max_concurrent}, "
        f"per-pos: {result.portfolio_cfg.position_fraction*100:.0f}%",
    ]
    if bnh_ret_pct is not None:
        lines.append(f"  BnH benchmark: {bnh_ret_pct:+.2f}%")
        lines.append(f"  Edge vs BnH:   {ret_pct - bnh_ret_pct:+.2f}%")
    lines.append("")
    lines.append("Trades per symbol:")
    for sym, c in sorted(result.per_symbol_trades.items()):
        lines.append(f"  {sym:<24} {c}")
    return "\n".join(lines)

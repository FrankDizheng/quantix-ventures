"""Bar-by-bar backtest with realistic execution, ATR stop, and trailing exit.

Supports BOTH long and short positions.

Execution choices (documented for learning):
  - Entry fills at *next* bar's open after a trigger (no lookahead).
  - Slippage is symmetric vs trade direction
      long  entry: open * (1 + slip), exit: px * (1 - slip)
      short entry: open * (1 - slip), exit: px * (1 + slip)
  - Initial stop (ATR-based):
      long:  entry - atr_mult * ATR  (loss if price falls)
      short: entry + atr_mult * ATR  (loss if price rises)
  - Trailing stop:
      long:  highest_close_since_entry * (1 - trail_pct/100)
      short: lowest_close_since_entry  * (1 + trail_pct/100)
    Updated only AT bar close (no intra-bar lookahead).
  - Stop hit detection on each bar using H/L:
      long:  exited if low  <= active_stop
      short: exited if high >= active_stop
  - If both initial-stop and trail-stop are touched in the same bar, the
    binding one (closer to price in direction of harm) wins.
  - Funding rate (perpetuals): applied at 00/08/16 UTC if configured.
      LONG  pays positive funding to SHORT.
      So longs lose, shorts gain when funding_rate_per_8h_pct > 0.
    Per-bar override: if df has column `funding_rate_per_8h_pct` (in PERCENT),
    that overrides the constant. Required for FundingCarryStrategy.
  - Optional `exit_trigger` column: if True on bar i, position exits at
    NEXT bar's open (no lookahead). Engine still applies stops/TP/time
    stop first; exit_trigger is checked last.
  - One position at a time, fixed fraction of equity per trade.

Strategy direction is read from a column `direction` on the signal df
(values "long" or "short"). If absent, defaults to "long" so existing
single-direction strategies remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from crypto_quant.strategy.ignition import IgnitionConfig


@dataclass
class BacktestConfig:
    initial_capital: float = 10_000.0
    fee_rate: float = 0.0004        # per side (taker)
    slippage_pct: float = 0.05      # per side, in percent (0.05 = 5 bps)
    position_fraction: float = 1.0
    # Honest-mode costs (Gate 1). Defaults are 0 so old runs are unchanged.
    funding_rate_per_8h_pct: float = 0.0   # 0.03 ≈ 33%/yr longs in mild bull


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    bars_held: int
    pnl_pct: float
    pnl_usd: float           # price PnL only (excludes funding)
    exit_reason: str
    direction: str = "long"
    funding_usd: float = 0.0  # total funding paid (-) or received (+)
    total_pnl_usd: float = 0.0  # pnl_usd + funding_usd


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    config: BacktestConfig = field(default_factory=BacktestConfig)
    strategy_config: object = field(default_factory=IgnitionConfig)

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
        wins = sum(1 for t in self.trades if t.pnl_usd > 0)
        return wins / len(self.trades) * 100


def run_backtest(
    df: pd.DataFrame,
    *,
    strat_cfg,
    bt_cfg: BacktestConfig | None = None,
) -> BacktestResult:
    """Run a bar-by-bar backtest.

    `strat_cfg` must expose: max_hold_hours, cooldown_bars, atr_stop_mult,
    trail_pct, stop_loss_pct (fallback). Optional: take_profit_pct.

    `df` must include columns: timestamp, open, high, low, close, atr,
    plus `entry_trigger` (or `entry_signal`). Optional column `direction`
    with values "long"/"short" (defaults to "long").
    """
    bt_cfg = bt_cfg or BacktestConfig()
    trigger_col = "entry_trigger" if "entry_trigger" in df.columns else "entry_signal"
    if trigger_col not in df.columns:
        raise ValueError("DataFrame must include entry_trigger or entry_signal")
    if "atr" not in df.columns:
        raise ValueError(
            "DataFrame missing 'atr' column. Run strategy.generate_signals first."
        )

    equity = bt_cfg.initial_capital
    equity_rows: list[dict] = []
    trades: list[Trade] = []
    position: dict | None = None
    last_exit_i = -(10**9)

    slip = bt_cfg.slippage_pct / 100
    max_bars = strat_cfg.max_hold_hours
    cooldown = strat_cfg.cooldown_bars
    atr_mult = strat_cfg.atr_stop_mult
    trail_frac = strat_cfg.trail_pct / 100 if strat_cfg.trail_pct > 0 else 0.0
    # Optional fixed take-profit (useful for mean-reversion). 0 = disabled.
    tp_pct = getattr(strat_cfg, "take_profit_pct", 0.0) or 0.0
    tp_frac = tp_pct / 100 if tp_pct > 0 else 0.0

    triggers = df[trigger_col].astype(bool).tolist()
    opens = df["open"].astype(float).tolist()
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    closes = df["close"].astype(float).tolist()
    atr = df["atr"].astype(float).tolist()
    times = df["timestamp"].tolist()
    if "direction" in df.columns:
        directions = df["direction"].astype(str).tolist()
    else:
        directions = ["long"] * len(df)
    if "exit_trigger" in df.columns:
        exits = df["exit_trigger"].astype(bool).tolist()
    else:
        exits = [False] * len(df)
    if "funding_rate_per_8h_pct" in df.columns:
        funding_per_bar = (
            df["funding_rate_per_8h_pct"].fillna(0.0).astype(float).tolist()
        )
    else:
        funding_per_bar = [bt_cfg.funding_rate_per_8h_pct] * len(df)

    for i in range(len(df)):
        ts = times[i]

        # 1) Open from previous bar's trigger
        if (
            position is None
            and i > 0
            and triggers[i - 1]
            and (i - last_exit_i) > cooldown
        ):
            d = directions[i - 1] if directions[i - 1] in ("long", "short") else "long"
            sign = 1 if d == "long" else -1
            fill = opens[i] * (1 + slip * sign)
            atr_at_entry = atr[i - 1] if pd.notna(atr[i - 1]) else 0.0
            if atr_mult > 0 and atr_at_entry > 0:
                initial_stop = fill - sign * atr_mult * atr_at_entry
            else:
                fallback = max(strat_cfg.stop_loss_pct, 6.0) / 100
                initial_stop = fill * (1 - sign * fallback)
            # TP is in the direction of profit:
            #   long  -> entry * (1 + tp_frac)  (price rises = profit)
            #   short -> entry * (1 - tp_frac)  (price falls = profit)
            tp_level = fill * (1 + sign * tp_frac) if tp_frac > 0 else None
            position = {
                "entry_i": i,
                "entry_time": ts,
                "entry_price": fill,
                "size": equity * bt_cfg.position_fraction,
                "initial_stop": initial_stop,
                "extreme_close": fill,  # highest_close for long, lowest_close for short
                "direction": d,
                "sign": sign,
                "tp_level": tp_level,
                "funding_accum": 0.0,  # signed funding received over the hold
            }

        # 2) Manage position using THIS bar's H/L/C
        if position is not None:
            bars_held = i - position["entry_i"]
            entry_px = position["entry_price"]
            size = position["size"]
            sign = position["sign"]

            # Trailing stop reference: trails extreme_close.
            #   long:  stop = highest_close * (1 - trail_frac)
            #   short: stop = lowest_close  * (1 + trail_frac)
            trail_stop = (
                position["extreme_close"] * (1 - sign * trail_frac)
                if trail_frac > 0
                else None
            )
            # "Binding" stop: the one closer to current price in direction of harm.
            #   long:  active = max(initial, trail)   (higher stop = tighter)
            #   short: active = min(initial, trail)   (lower stop  = tighter)
            if trail_stop is None:
                active_stop = position["initial_stop"]
            elif sign == 1:
                active_stop = max(position["initial_stop"], trail_stop)
            else:
                active_stop = min(position["initial_stop"], trail_stop)

            exit_px: float | None = None
            reason = ""
            if sign == 1:
                # LONG: stop hits if price drops to stop; TP hits if price rises to tp
                if lows[i] <= active_stop:
                    exit_px = active_stop * (1 - slip)
                    reason = (
                        "trail_stop"
                        if trail_stop is not None and trail_stop > position["initial_stop"]
                        else "initial_stop"
                    )
                elif position["tp_level"] is not None and highs[i] >= position["tp_level"]:
                    exit_px = position["tp_level"] * (1 - slip)
                    reason = "take_profit"
            else:
                # SHORT: stop hits if price rises to stop; TP hits if price drops to tp
                if highs[i] >= active_stop:
                    exit_px = active_stop * (1 + slip)
                    reason = (
                        "trail_stop"
                        if trail_stop is not None and trail_stop < position["initial_stop"]
                        else "initial_stop"
                    )
                elif position["tp_level"] is not None and lows[i] <= position["tp_level"]:
                    exit_px = position["tp_level"] * (1 + slip)
                    reason = "take_profit"

            if exit_px is None and bars_held >= max_bars:
                exit_px = closes[i] * (1 - sign * slip)
                reason = "max_hold"
            # Strategy-driven exit (e.g. funding normalized). Acts on the
            # PREVIOUS bar's signal to avoid same-bar lookahead.
            if exit_px is None and i > 0 and exits[i - 1]:
                exit_px = opens[i] * (1 - sign * slip)
                reason = "exit_signal"

            if exit_px is not None:
                gross_ret = sign * (exit_px - entry_px) / entry_px
                entry_notional = size
                exit_notional = size * (1 + gross_ret)
                fees = (entry_notional + exit_notional) * bt_cfg.fee_rate
                pnl_usd = size * gross_ret - fees
                equity += pnl_usd
                funding_usd = position["funding_accum"]
                trades.append(
                    Trade(
                        entry_time=position["entry_time"],
                        exit_time=ts,
                        entry_price=entry_px,
                        exit_price=exit_px,
                        bars_held=bars_held,
                        pnl_pct=gross_ret * 100,
                        pnl_usd=pnl_usd,
                        exit_reason=reason,
                        direction=position["direction"],
                        funding_usd=funding_usd,
                        total_pnl_usd=pnl_usd + funding_usd,
                    )
                )
                position = None
                last_exit_i = i

        # 3) Update trailing reference & mark-to-market at bar close
        if position is not None:
            sign = position["sign"]
            if sign == 1 and closes[i] > position["extreme_close"]:
                position["extreme_close"] = closes[i]
            elif sign == -1 and closes[i] < position["extreme_close"]:
                position["extreme_close"] = closes[i]
            unreal = sign * (closes[i] - position["entry_price"]) / position["entry_price"]
            mtm = equity + position["size"] * unreal

            # 3a) Funding cost at 00/08/16 UTC.
            #     long  pays positive funding (equity -= cost)
            #     short receives it           (equity += cost)
            # Per-bar rate (from df column) overrides constant.
            rate = funding_per_bar[i]
            if rate != 0 and hasattr(ts, "hour") and ts.hour in (0, 8, 16):
                cur_notional = position["size"] * (1 + unreal)
                funding_cost = cur_notional * rate / 100
                # funding RECEIVED by the position (positive = good for us):
                #   long  with rate>0: pays   -> received = -funding_cost
                #   short with rate>0: receives -> received = +funding_cost
                received = -sign * funding_cost
                equity += received
                mtm += received
                position["funding_accum"] += received
        else:
            mtm = equity
        equity_rows.append({"timestamp": ts, "equity": mtm})

    return BacktestResult(
        trades=trades,
        equity_curve=pd.DataFrame(equity_rows),
        config=bt_cfg,
        strategy_config=strat_cfg,
    )

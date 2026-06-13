"""Honest backtest (Gate 1): out-of-sample hold-out + realistic costs.

Why this matters
-----------------
The standard `cq backtest-batch` reports in-sample numbers tuned on the
same data — which is mostly noise. This module:

  1. Holds out the last N days as OOS. The strategy still uses the full
     history for indicator warmup, but only trades ENTERED during OOS
     are counted in the report.

  2. Applies realistic costs that the optimistic engine skipped:
       - Spread (rolled into `slippage_pct`, default 0.15% per side)
       - Funding rate (`funding_rate_per_8h_pct`, applied every 8h)
       - Position fraction (default 25% per trade, not 100%)

  3. Compares OOS P&L to OOS buy-and-hold. Edge is what matters,
     not absolute return.

Pass criteria (rule of thumb):
  - OOS edge vs BnH > 0 on majority of symbols
  - At least 50 OOS trades total (otherwise sample too small)
  - Max drawdown < 30%
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from crypto_quant.backtest.engine import BacktestConfig, run_backtest
from crypto_quant.backtest.report import max_drawdown_pct
from crypto_quant.data.sync import ensure_funding, ensure_ohlcv
from crypto_quant.onchain.netflow import (
    DuneFilterConfig,
    ensure_netflow_for_symbol,
)
from crypto_quant.strategy import (
    FundingCarryConfig,
    FundingCarryStrategy,
    IgnitionConfig,
    IgnitionStrategy,
    MeanReversionConfig,
    MeanReversionStrategy,
    merge_funding_to_ohlcv,
)


@dataclass
class HonestSymbolResult:
    symbol: str
    total_bars: int
    oos_bars: int
    oos_trades: int
    oos_wins: int
    oos_win_rate_pct: float
    oos_return_pct: float
    oos_bnh_pct: float
    oos_edge_pct: float       # vs 100% buy-and-hold (for backward compat)
    oos_max_dd_pct: float
    direction: str = "long"


@dataclass
class HonestResult:
    summary: pd.DataFrame
    trades: pd.DataFrame
    oos_days: int
    cost_assumptions: dict = field(default_factory=dict)


def _buy_and_hold_pct(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    first, last = float(df["close"].iloc[0]), float(df["close"].iloc[-1])
    if first <= 0:
        return 0.0
    return (last / first - 1) * 100


def _build_btc_trend(
    out_root: Path,
    exchange: str,
    timeframe: str,
    total_days: int,
    ema_hours: int,
) -> pd.Series | None:
    """Return a bool Series indexed by timestamp: BTC close > EMA(ema_hours)."""
    try:
        btc = ensure_ohlcv(
            out_root,
            exchange=exchange,
            timeframe=timeframe,
            symbol="BTC/USDT:USDT",
            days=total_days,
            force=False,
        )
    except Exception as e:
        print(f"[honest] BTC fetch failed: {e}; regime filter will be inactive")
        return None
    if btc.empty:
        return None
    s = btc.set_index("timestamp")["close"]
    ema = s.ewm(span=ema_hours, adjust=False).mean()
    return (s > ema).astype(bool)


def _make_strategy(
    strategy_name: str,
    strat_cfg,
    dune_cfg: DuneFilterConfig | None,
):
    """Map strategy name to a generator instance + its direction default."""
    name = (strategy_name or "ignition").lower()
    if name == "ignition":
        return IgnitionStrategy(strat_cfg, dune_cfg), "long"
    if name in ("mean_reversion", "mean_rev", "meanrev", "mr"):
        return MeanReversionStrategy(strat_cfg), "short"
    if name in ("funding_carry", "funding", "carry", "fc"):
        return FundingCarryStrategy(strat_cfg), "short"
    raise ValueError(f"Unknown strategy: {strategy_name!r}")


def _needs_funding(strategy_name: str) -> bool:
    return (strategy_name or "").lower() in (
        "funding_carry", "funding", "carry", "fc"
    )


def run_honest_backtest(
    symbols: list[str],
    *,
    exchange: str,
    timeframe: str,
    total_days: int,
    oos_days: int,
    strat_cfg,
    bt_cfg: BacktestConfig,
    out_root: Path,
    dune_cfg: DuneFilterConfig | None = None,
    strategy_name: str = "ignition",
    log: bool = True,
) -> HonestResult:
    if oos_days >= total_days:
        raise ValueError("oos_days must be < total_days (need in-sample warmup)")

    btc_trend: pd.Series | None = None
    if isinstance(strat_cfg, IgnitionConfig) and getattr(
        strat_cfg, "require_btc_uptrend", False
    ):
        btc_trend = _build_btc_trend(
            out_root, exchange, timeframe, total_days,
            ema_hours=168,
        )

    strategy, direction = _make_strategy(strategy_name, strat_cfg, dune_cfg)
    needs_funding = _needs_funding(strategy_name)
    per_symbol: list[HonestSymbolResult] = []
    all_trades: list[dict] = []

    for sym in symbols:
        try:
            df = ensure_ohlcv(
                out_root,
                exchange=exchange,
                timeframe=timeframe,
                symbol=sym,
                days=total_days,
                force=False,
            )
        except Exception as e:
            if log:
                print(f"[honest] {sym}: fetch error: {e}")
            continue
        if df.empty or len(df) < 200:
            if log:
                print(f"[honest] {sym}: insufficient data ({len(df)} bars)")
            continue

        cutoff = df["timestamp"].max() - pd.Timedelta(days=oos_days)
        oos_df = df[df["timestamp"] >= cutoff].reset_index(drop=True)
        if oos_df.empty:
            continue

        netflow = None
        if isinstance(strategy, IgnitionStrategy) and dune_cfg and dune_cfg.enabled:
            try:
                netflow = ensure_netflow_for_symbol(
                    sym, out_root, days=max(total_days, dune_cfg.lookback_days),
                    use_cache=True,
                )
            except Exception:
                netflow = None

        funding = None
        if needs_funding:
            try:
                funding = ensure_funding(
                    out_root,
                    exchange=exchange,
                    symbol=sym,
                    days=total_days,
                )
            except Exception as e:
                if log:
                    print(f"[honest] {sym}: funding fetch error: {e}; skipping")
                continue
            if funding is None or funding.empty:
                if log:
                    print(f"[honest] {sym}: no funding history; skipping")
                continue
            df = merge_funding_to_ohlcv(df, funding)

        signals = strategy.generate_signals(
            df, netflow_daily=netflow, btc_trend=btc_trend
        )
        full_result = run_backtest(signals, strat_cfg=strat_cfg, bt_cfg=bt_cfg)

        oos_trades = [t for t in full_result.trades if t.entry_time >= cutoff]
        wins = sum(1 for t in oos_trades if t.pnl_usd > 0)
        oos_pnl = sum(t.pnl_usd for t in oos_trades)
        oos_return_pct = (oos_pnl / bt_cfg.initial_capital) * 100
        oos_bnh = _buy_and_hold_pct(oos_df)

        eq = full_result.equity_curve
        eq_oos = eq[eq["timestamp"] >= cutoff]["equity"]
        oos_dd = max_drawdown_pct(eq_oos) if not eq_oos.empty else 0.0

        per_symbol.append(
            HonestSymbolResult(
                symbol=sym,
                total_bars=len(df),
                oos_bars=len(oos_df),
                oos_trades=len(oos_trades),
                oos_wins=wins,
                oos_win_rate_pct=(wins / len(oos_trades) * 100) if oos_trades else 0.0,
                oos_return_pct=round(oos_return_pct, 2),
                oos_bnh_pct=round(oos_bnh, 2),
                oos_edge_pct=round(oos_return_pct - oos_bnh, 2),
                oos_max_dd_pct=round(oos_dd, 2),
                direction=direction,
            )
        )

        for t in oos_trades:
            d = asdict(t)
            d["symbol"] = sym
            all_trades.append(d)

        if log:
            print(
                f"[honest] {sym:<22} oos_trades={len(oos_trades):>3}  "
                f"ret={oos_return_pct:+6.2f}%  bnh={oos_bnh:+6.2f}%  "
                f"edge={oos_return_pct - oos_bnh:+6.2f}%"
            )

    summary = pd.DataFrame([asdict(r) for r in per_symbol])
    if not summary.empty:
        summary = summary.sort_values("oos_return_pct", ascending=False).reset_index(
            drop=True
        )
    return HonestResult(
        summary=summary,
        trades=pd.DataFrame(all_trades),
        oos_days=oos_days,
        cost_assumptions={
            "strategy": strategy_name,
            "direction": direction,
            "position_fraction": bt_cfg.position_fraction,
            "fee_rate_per_side": bt_cfg.fee_rate,
            "slippage_pct_per_side": bt_cfg.slippage_pct,
            "funding_per_8h_pct": bt_cfg.funding_rate_per_8h_pct,
            "approx_annual_funding_pct": round(
                bt_cfg.funding_rate_per_8h_pct * 3 * 365, 1
            ),
        },
    )


def format_honest_report(result: HonestResult) -> str:
    if result.summary.empty:
        return "(no symbols ran successfully)"
    s = result.summary
    direction = result.cost_assumptions.get("direction", "long")
    strategy = result.cost_assumptions.get("strategy", "ignition")

    avg_ret = s["oos_return_pct"].mean()
    avg_bnh = s["oos_bnh_pct"].mean()
    pos_frac = result.cost_assumptions.get("position_fraction", 1.0)
    fair_bnh = avg_bnh * pos_frac  # BnH at same exposure as our trades
    n_pos_ret = int((s["oos_return_pct"] > 0).sum())
    total_trades = int(s["oos_trades"].sum())
    avg_dd = s["oos_max_dd_pct"].mean()
    avg_win_rate = s["oos_win_rate_pct"].mean() if "oos_win_rate_pct" in s else 0.0

    lines = [
        f"=== Honest OOS Backtest — {strategy.upper()} ({direction})  "
        f"{result.oos_days}d hold-out ===",
        s.to_string(index=False),
        "",
        "Cost assumptions:",
        f"  position_fraction:    {pos_frac:.2f}",
        f"  fee per side:         {result.cost_assumptions['fee_rate_per_side']*100:.3f}%",
        f"  slippage per side:    {result.cost_assumptions['slippage_pct_per_side']:.3f}%",
        f"  funding per 8h:       {result.cost_assumptions['funding_per_8h_pct']:.3f}% "
        f"(~{result.cost_assumptions['approx_annual_funding_pct']:.0f}%/yr)",
        "",
        f"Avg OOS return:        {avg_ret:+.2f}%   "
        f"({n_pos_ret}/{len(s)} symbols profitable)",
        f"Avg OOS BnH (100%):    {avg_bnh:+.2f}%",
        f"Avg OOS BnH @ {int(pos_frac*100)}%:    {fair_bnh:+.2f}%   "
        f"(fair-exposure benchmark)",
        f"Avg OOS win rate:      {avg_win_rate:.1f}%",
        f"Avg OOS max DD:        {avg_dd:.2f}%",
        f"Total OOS trades:      {total_trades}",
        "",
    ]
    if total_trades < 50:
        lines.append(
            f"⚠️  Only {total_trades} OOS trades — too few for statistical confidence."
        )
    if direction == "long":
        edge = avg_ret - fair_bnh
        lines.append(
            f"Fair edge vs same-exposure BnH: {edge:+.2f}%"
        )
        if edge <= 0:
            lines.append(
                "⚠️  Negative fair edge: strategy did NOT beat passive holding "
                "at the same risk."
            )
    else:
        lines.append(
            "For SHORT strategies, success = positive return after costs "
            "(BnH is the wrong benchmark)."
        )
        if avg_ret <= 0:
            lines.append("⚠️  Avg return ≤ 0: short signal did not pay off OOS.")
    return "\n".join(lines)

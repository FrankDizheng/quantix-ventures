"""Walk-forward validation: run portfolio backtest on N non-overlapping windows.

Why this exists
---------------
A single 30-day OOS gives one data point. Could be a friendly window, could
be hostile. To trust an edge claim we need to see whether it holds across
DIFFERENT market regimes within the same year.

Design
------
Given total_days of cached OHLCV per symbol and a window_days size, we
build a sequence of non-overlapping OOS windows that all END on or before
"now":

    Window k OOS = [end_k - window_days, end_k]
    end_k        = (k+1) * window_days from the START of the data

Each window's strategy uses ALL data up to end_k for indicator warmup
(no future leak), then we only count trades entered within the window.
Identical to the existing honest backtest, just sliced.

Output
------
WalkForwardResult: per-window summary table + aggregate stats
  - n_windows
  - mean / std / min / max of return, edge, drawdown
  - hit_rate (% of windows with positive edge)
  - total trades across all windows
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from crypto_quant.backtest.engine import BacktestConfig
from crypto_quant.backtest.portfolio import (
    PortfolioConfig,
    run_portfolio_backtest,
)
from crypto_quant.data.sync import ensure_funding, ensure_ohlcv
from crypto_quant.strategy import merge_funding_to_ohlcv


@dataclass
class WindowResult:
    window_idx: int
    start: pd.Timestamp
    end: pd.Timestamp
    trades: int
    return_pct: float
    bnh_pct: float          # equal-weight avg across symbols
    edge_pct: float         # return - bnh
    max_dd_pct: float
    funding_pnl: float
    win_rate_pct: float


@dataclass
class WalkForwardResult:
    strategy: str
    windows: list[WindowResult] = field(default_factory=list)

    def summary(self) -> pd.DataFrame:
        return pd.DataFrame([w.__dict__ for w in self.windows])

    def aggregate(self) -> dict:
        if not self.windows:
            return {}
        s = self.summary()
        return {
            "n_windows": len(s),
            "total_trades": int(s["trades"].sum()),
            "mean_return": round(s["return_pct"].mean(), 2),
            "std_return": round(s["return_pct"].std(ddof=0), 2),
            "mean_edge": round(s["edge_pct"].mean(), 2),
            "std_edge": round(s["edge_pct"].std(ddof=0), 2),
            "min_edge": round(s["edge_pct"].min(), 2),
            "max_edge": round(s["edge_pct"].max(), 2),
            "hit_rate_pct": round((s["edge_pct"] > 0).mean() * 100, 1),
            "mean_dd": round(s["max_dd_pct"].mean(), 2),
            "worst_dd": round(s["max_dd_pct"].min(), 2),
        }


def _build_signals_for_window(
    strategy,
    symbols: list[str],
    *,
    out_root: Path,
    exchange: str,
    timeframe: str,
    total_days: int,
    window_end: pd.Timestamp,
    window_days: int,
    needs_funding: bool,
) -> tuple[dict[str, pd.DataFrame], dict[str, float], pd.Timestamp]:
    """For a given window-end, slice each cached df, run strategy, return OOS slice."""
    signals_by_sym: dict[str, pd.DataFrame] = {}
    bnh_by_sym: dict[str, float] = {}
    cutoff = window_end - pd.Timedelta(days=window_days)
    for sym in symbols:
        try:
            df = ensure_ohlcv(
                out_root, exchange=exchange, timeframe=timeframe,
                symbol=sym, days=total_days, force=False,
            )
        except Exception:
            continue
        if df.empty:
            continue
        # Constrain to data <= window_end (no future leak)
        df = df[df["timestamp"] <= window_end].reset_index(drop=True)
        if len(df) < 200:
            continue
        if needs_funding:
            try:
                fnd = ensure_funding(
                    out_root, exchange=exchange, symbol=sym, days=total_days
                )
            except Exception:
                fnd = None
            if fnd is None or fnd.empty:
                continue
            fnd = fnd[fnd["timestamp"] <= window_end]
            if fnd.empty:
                continue
            df = merge_funding_to_ohlcv(df, fnd)
        sig = strategy.generate_signals(df)
        sig_oos = sig[sig["timestamp"] >= cutoff].reset_index(drop=True)
        if sig_oos.empty:
            continue
        signals_by_sym[sym] = sig_oos
        c = sig_oos["close"]
        bnh_by_sym[sym] = (c.iloc[-1] / c.iloc[0] - 1) * 100
    return signals_by_sym, bnh_by_sym, cutoff


def run_walk_forward(
    strategy,
    symbols: list[str],
    *,
    out_root: Path,
    exchange: str,
    timeframe: str,
    total_days: int,
    window_days: int,
    n_windows: int,
    strat_cfg,
    bt_cfg: BacktestConfig,
    pf_cfg: PortfolioConfig,
    strategy_name: str = "strategy",
    needs_funding: bool = False,
    log: bool = True,
) -> WalkForwardResult:
    """Run N non-overlapping windows ending at most-recent cached data.

    Each window k has OOS = (end_k - window_days, end_k] where
    end_k = latest - k * window_days, k = 0 (most recent) .. n_windows-1.
    Reversed in output so window 0 is the OLDEST.
    """
    # Find the latest timestamp across all cached symbols (use first non-empty).
    latest: pd.Timestamp | None = None
    for sym in symbols:
        try:
            df = ensure_ohlcv(
                out_root, exchange=exchange, timeframe=timeframe,
                symbol=sym, days=total_days, force=False,
            )
            if not df.empty:
                ts = pd.to_datetime(df["timestamp"].max(), utc=True)
                if latest is None or ts > latest:
                    latest = ts
        except Exception:
            continue
    if latest is None:
        raise RuntimeError("No usable OHLCV cache for any symbol")

    result = WalkForwardResult(strategy=strategy_name)
    # Build window ends from most recent backwards
    window_ends = [
        latest - pd.Timedelta(days=window_days * k) for k in range(n_windows)
    ]
    window_ends.reverse()  # oldest first
    for idx, end_ts in enumerate(window_ends):
        signals, bnh_per_sym, start_ts = _build_signals_for_window(
            strategy, symbols,
            out_root=out_root, exchange=exchange, timeframe=timeframe,
            total_days=total_days,
            window_end=end_ts, window_days=window_days,
            needs_funding=needs_funding,
        )
        if not signals:
            if log:
                print(f"  window {idx} [{start_ts.date()} -> {end_ts.date()}]: no signals")
            continue
        pf_res = run_portfolio_backtest(
            signals, strat_cfg=strat_cfg, bt_cfg=bt_cfg, pf_cfg=pf_cfg,
        )
        if pf_res.equity_curve.empty:
            continue
        ret = pf_res.total_return_pct
        avg_bnh = sum(bnh_per_sym.values()) / len(bnh_per_sym)
        deployed = min(1.0, pf_cfg.max_concurrent * pf_cfg.position_fraction)
        fair_bnh = avg_bnh * deployed
        eq = pf_res.equity_curve["equity"]
        peak = eq.cummax()
        dd = ((eq - peak) / peak * 100).min() if len(eq) else 0.0
        funding_pnl = sum(t.funding_usd for t in pf_res.trades)
        wins = sum(1 for t in pf_res.trades if t.total_pnl_usd > 0)
        wr = wins / len(pf_res.trades) * 100 if pf_res.trades else 0.0
        wr_r = WindowResult(
            window_idx=idx,
            start=start_ts,
            end=end_ts,
            trades=len(pf_res.trades),
            return_pct=round(ret, 2),
            bnh_pct=round(fair_bnh, 2),
            edge_pct=round(ret - fair_bnh, 2),
            max_dd_pct=round(dd, 2),
            funding_pnl=round(funding_pnl, 2),
            win_rate_pct=round(wr, 1),
        )
        result.windows.append(wr_r)
        if log:
            print(
                f"  window {idx} [{start_ts.date()} -> {end_ts.date()}]:  "
                f"trades={len(pf_res.trades):>3}  ret={ret:+6.2f}%  "
                f"bnh={fair_bnh:+6.2f}%  edge={ret-fair_bnh:+6.2f}%  "
                f"dd={dd:.2f}%"
            )
    return result


def format_walk_forward_report(result: WalkForwardResult) -> str:
    if not result.windows:
        return f"=== Walk-forward [{result.strategy}] === (no windows had data)"
    s = result.summary()
    agg = result.aggregate()
    lines = [
        f"=== Walk-forward validation [{result.strategy}] ===",
        s[["window_idx", "start", "end", "trades", "return_pct",
           "bnh_pct", "edge_pct", "max_dd_pct", "win_rate_pct",
           "funding_pnl"]].to_string(index=False),
        "",
        "Aggregate:",
        f"  windows tested:      {agg['n_windows']}",
        f"  total trades:        {agg['total_trades']}",
        f"  mean return:         {agg['mean_return']:+.2f}%   "
        f"(std {agg['std_return']:.2f})",
        f"  mean edge vs BnH:    {agg['mean_edge']:+.2f}%   "
        f"(std {agg['std_edge']:.2f})",
        f"  edge range:          [{agg['min_edge']:+.2f}%, {agg['max_edge']:+.2f}%]",
        f"  hit rate (edge>0):   {agg['hit_rate_pct']:.1f}%  "
        f"({int(agg['hit_rate_pct']/100*agg['n_windows'])}/{agg['n_windows']} windows)",
        f"  mean / worst DD:     {agg['mean_dd']:.2f}% / {agg['worst_dd']:.2f}%",
        "",
    ]
    # Verdict heuristic (lightweight)
    pass_rate = agg["hit_rate_pct"]
    mean_edge = agg["mean_edge"]
    if pass_rate >= 67 and mean_edge >= 5 and agg["worst_dd"] > -25:
        lines.append("VERDICT: PROMISING — meets hit-rate, edge, and drawdown gates")
    elif pass_rate >= 50 and mean_edge >= 0:
        lines.append("VERDICT: BORDERLINE — positive but not robust enough yet")
    else:
        lines.append("VERDICT: REJECT — edge does not generalize across windows")
    return "\n".join(lines)

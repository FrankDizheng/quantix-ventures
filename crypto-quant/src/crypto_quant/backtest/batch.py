"""Batch backtest: run the same strategy across many symbols and aggregate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from crypto_quant.backtest.engine import BacktestConfig, run_backtest
from crypto_quant.backtest.report import (
    annualized_sharpe,
    buy_and_hold_pct,
    max_drawdown_pct,
)
from crypto_quant.data import CCXTFetcher
from crypto_quant.data.storage import load_dataframe, save_dataframe
from crypto_quant.onchain.netflow import (
    DuneFilterConfig,
    ensure_netflow_for_symbol,
)
from crypto_quant.strategy import IgnitionConfig, IgnitionStrategy


@dataclass
class BatchResult:
    summary: pd.DataFrame
    trades: pd.DataFrame


def _ohlcv_path(out_root: Path, exchange: str, timeframe: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    return out_root / "ccxt" / exchange / timeframe / f"{safe}.parquet"


def _ensure_ohlcv(
    out_root: Path,
    *,
    exchange: str,
    timeframe: str,
    symbol: str,
    days: int,
    use_cache: bool,
) -> pd.DataFrame:
    path = _ohlcv_path(out_root, exchange, timeframe, symbol)
    if use_cache and path.exists():
        return load_dataframe(path)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    df = CCXTFetcher(exchange, rate_limit_ms=200).fetch_ohlcv(symbol, timeframe, since=since)
    if not df.empty:
        save_dataframe(df, path)
    return df


def run_batch(
    symbols: list[str],
    *,
    exchange: str,
    timeframe: str,
    days: int,
    strat_cfg: IgnitionConfig,
    bt_cfg: BacktestConfig,
    out_root: Path,
    use_cache: bool = True,
    dune_cfg: DuneFilterConfig | None = None,
    log: bool = True,
) -> BatchResult:
    rows: list[dict] = []
    all_trades: list[dict] = []
    for sym in symbols:
        try:
            df = _ensure_ohlcv(
                out_root,
                exchange=exchange,
                timeframe=timeframe,
                symbol=sym,
                days=days,
                use_cache=use_cache,
            )
        except Exception as e:
            if log:
                print(f"[batch] {sym}: fetch error: {e}")
            continue
        if df.empty or len(df) < 200:
            if log:
                print(f"[batch] {sym}: insufficient data ({len(df)} bars)")
            continue

        netflow = None
        if dune_cfg and dune_cfg.enabled:
            lookback = max(days, dune_cfg.lookback_days)
            netflow = ensure_netflow_for_symbol(
                sym,
                out_root,
                days=lookback,
                use_cache=use_cache,
            )
            if netflow is None and not dune_cfg.skip_if_missing:
                if log:
                    print(f"[batch] {sym}: no token_map entry for Dune")
                continue
            if log and netflow is not None:
                print(f"[batch] {sym}: Dune netflow {len(netflow)} days")

        signals = IgnitionStrategy(strat_cfg, dune_cfg).generate_signals(
            df, netflow_daily=netflow
        )
        result = run_backtest(signals, strat_cfg=strat_cfg, bt_cfg=bt_cfg)
        eq = result.equity_curve["equity"]
        wins = sum(1 for t in result.trades if t.pnl_usd > 0)
        gross_profit = sum(t.pnl_usd for t in result.trades if t.pnl_usd > 0)
        gross_loss = abs(sum(t.pnl_usd for t in result.trades if t.pnl_usd <= 0))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        bnh = buy_and_hold_pct(df) or 0.0
        rows.append(
            {
                "symbol": sym,
                "bars": len(df),
                "trades": len(result.trades),
                "wins": wins,
                "win_rate_pct": (wins / len(result.trades) * 100) if result.trades else 0.0,
                "profit_factor": round(pf, 2) if pf != float("inf") else None,
                "total_return_pct": round(result.total_return_pct, 2),
                "buy_hold_pct": round(bnh, 2),
                "edge_vs_bnh_pct": round(result.total_return_pct - bnh, 2),
                "max_drawdown_pct": round(max_drawdown_pct(eq), 2),
                "sharpe_annual": (
                    round(annualized_sharpe(eq) or 0, 2) if not eq.empty else 0.0
                ),
            }
        )
        for t in result.trades:
            d = asdict(t)
            d["symbol"] = sym
            all_trades.append(d)
        if log:
            print(
                f"[batch] {sym:<22} trades={len(result.trades):>3}  "
                f"ret={result.total_return_pct:+6.2f}%  "
                f"bnh={bnh:+6.2f}%"
            )

    summary = pd.DataFrame(rows)
    trades = pd.DataFrame(all_trades)
    if not summary.empty:
        summary = summary.sort_values("edge_vs_bnh_pct", ascending=False).reset_index(drop=True)
    return BatchResult(summary=summary, trades=trades)


def format_batch_report(result: BatchResult) -> str:
    if result.summary.empty:
        return "(no symbols ran successfully)"
    s = result.summary
    avg_ret = s["total_return_pct"].mean()
    avg_bnh = s["buy_hold_pct"].mean()
    avg_edge = s["edge_vs_bnh_pct"].mean()
    avg_dd = s["max_drawdown_pct"].mean()
    win_avg = s["win_rate_pct"].mean()
    n_pos = int((s["edge_vs_bnh_pct"] > 0).sum())

    lines = [
        f"=== Batch Backtest: {len(s)} symbols ===",
        s.to_string(index=False),
        "",
        f"Avg total return:  {avg_ret:+.2f}%",
        f"Avg buy & hold:    {avg_bnh:+.2f}%",
        f"Avg edge vs BnH:   {avg_edge:+.2f}%   ({n_pos}/{len(s)} symbols beat BnH)",
        f"Avg win rate:      {win_avg:.1f}%",
        f"Avg max drawdown:  {avg_dd:.2f}%",
    ]
    return "\n".join(lines)

"""Tests for short-direction support in the backtest engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_quant.backtest.engine import BacktestConfig, run_backtest
from crypto_quant.strategy.mean_reversion import MeanReversionConfig


def _make_df(closes: list[float]) -> pd.DataFrame:
    """Build a minimal OHLCV df with an artificial entry trigger at bar 5."""
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "timestamp": idx,
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
            "volume": [100.0] * n,
            "atr": [c * 0.01 for c in closes],  # 1% ATR
        }
    )
    df["entry_trigger"] = False
    df.loc[4, "entry_trigger"] = True  # fill at bar 5's open
    return df


def test_short_wins_on_falling_price():
    """A short opened at $100 then price drops should produce positive PnL."""
    closes = [100.0] * 5 + list(np.linspace(100.0, 80.0, 30))
    df = _make_df(closes)
    df["direction"] = "short"
    cfg = MeanReversionConfig(
        atr_stop_mult=5.0,    # stop is wide so it won't trigger
        trail_pct=0.0,
        take_profit_pct=0.0,
        max_hold_hours=20,
        cooldown_bars=0,
    )
    bt = BacktestConfig(initial_capital=10_000, fee_rate=0.0, slippage_pct=0.0)
    res = run_backtest(df, strat_cfg=cfg, bt_cfg=bt)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "short"
    assert t.pnl_usd > 0
    assert t.exit_reason == "max_hold"


def test_short_take_profit_triggers():
    """TP at -5% should fire when price hits that level."""
    closes = [100.0] * 5 + [98, 96, 94, 92, 90, 88] + [88.0] * 10
    df = _make_df(closes)
    df["direction"] = "short"
    cfg = MeanReversionConfig(
        atr_stop_mult=10.0,
        trail_pct=0.0,
        take_profit_pct=5.0,  # tp at $95
        max_hold_hours=100,
        cooldown_bars=0,
    )
    bt = BacktestConfig(initial_capital=10_000, fee_rate=0.0, slippage_pct=0.0)
    res = run_backtest(df, strat_cfg=cfg, bt_cfg=bt)
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "take_profit"
    assert res.trades[0].pnl_usd > 0


def test_short_initial_stop_triggers_on_rip():
    """If price rips up after a short entry, the initial stop should hit."""
    closes = [100.0] * 5 + list(np.linspace(100.0, 130.0, 20))
    df = _make_df(closes)
    df["direction"] = "short"
    cfg = MeanReversionConfig(
        atr_stop_mult=2.0,    # stop at ~$102
        trail_pct=0.0,
        take_profit_pct=0.0,
        max_hold_hours=100,
        cooldown_bars=0,
    )
    bt = BacktestConfig(initial_capital=10_000, fee_rate=0.0, slippage_pct=0.0)
    res = run_backtest(df, strat_cfg=cfg, bt_cfg=bt)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "initial_stop"
    assert t.pnl_usd < 0


def test_long_still_works_after_refactor():
    """Regression: long path unchanged after adding short support."""
    closes = [100.0] * 5 + list(np.linspace(100.0, 120.0, 30))
    df = _make_df(closes)
    # No direction column -> defaults to long
    cfg = MeanReversionConfig(
        atr_stop_mult=5.0,
        trail_pct=0.0,
        take_profit_pct=0.0,
        max_hold_hours=20,
        cooldown_bars=0,
    )
    bt = BacktestConfig(initial_capital=10_000, fee_rate=0.0, slippage_pct=0.0)
    res = run_backtest(df, strat_cfg=cfg, bt_cfg=bt)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "long"
    assert t.pnl_usd > 0


def test_funding_inverts_for_short():
    """Positive funding should HURT long and HELP short of equal size."""
    closes = [100.0] * 60
    df_long = _make_df(closes)
    df_short = _make_df(closes)
    df_short["direction"] = "short"

    cfg = MeanReversionConfig(
        atr_stop_mult=10.0,
        trail_pct=0.0,
        take_profit_pct=0.0,
        max_hold_hours=48,  # crosses several funding windows
        cooldown_bars=0,
    )
    bt = BacktestConfig(
        initial_capital=10_000,
        fee_rate=0.0,
        slippage_pct=0.0,
        funding_rate_per_8h_pct=0.05,  # +0.05% per 8h, longs pay
    )
    long_res = run_backtest(df_long, strat_cfg=cfg, bt_cfg=bt)
    short_res = run_backtest(df_short, strat_cfg=cfg, bt_cfg=bt)
    assert long_res.trades and short_res.trades
    long_eq_end = float(long_res.equity_curve["equity"].iloc[-1])
    short_eq_end = float(short_res.equity_curve["equity"].iloc[-1])
    assert long_eq_end < 10_000   # long lost to funding
    assert short_eq_end > 10_000  # short gained from funding

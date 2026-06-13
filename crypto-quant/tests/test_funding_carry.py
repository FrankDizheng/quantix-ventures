"""Tests for FundingCarryStrategy + per-bar funding + exit_trigger."""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_quant.backtest.engine import BacktestConfig, run_backtest
from crypto_quant.strategy.funding_carry import (
    FundingCarryConfig,
    FundingCarryStrategy,
    merge_funding_to_ohlcv,
)


def _ohlcv(n: int = 200, flat_price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": idx,
            "open": [flat_price] * n,
            "high": [flat_price * 1.001] * n,
            "low": [flat_price * 0.999] * n,
            "close": [flat_price] * n,
            "volume": [100.0] * n,
        }
    )


def _funding(n_periods: int, rate_pct: float, start: str = "2026-01-01") -> pd.DataFrame:
    """Funding observations every 8h. rate_pct is in PERCENT (e.g. 0.05)."""
    idx = pd.date_range(start, periods=n_periods, freq="8h", tz="UTC")
    return pd.DataFrame(
        {"timestamp": idx, "funding_rate": [rate_pct / 100] * n_periods}
    )


def test_merge_funding_forward_fills_hourly():
    o = _ohlcv(24)
    f = _funding(3, rate_pct=0.05)  # 3 obs at hours 0, 8, 16
    merged = merge_funding_to_ohlcv(o, f)
    assert "funding_rate_per_8h_pct" in merged.columns
    # All hourly bars should pick up the same rate via forward fill.
    assert (merged["funding_rate_per_8h_pct"] == 0.05).all()


def test_carry_signals_fire_above_threshold():
    o = _ohlcv(200)
    f = _funding(75, rate_pct=0.10)  # high funding throughout
    cfg = FundingCarryConfig(
        entry_funding_threshold_pct=0.03,
        exit_funding_threshold_pct=0.005,
    )
    strat = FundingCarryStrategy(cfg)
    out = strat.generate_signals(o, funding=f)
    assert (out["direction"] == "short").all()
    assert out["entry_trigger"].sum() >= 1, "high funding must trigger entry"
    assert out["exit_trigger"].sum() == 0, "no exit while funding stays high"


def test_carry_exit_fires_when_funding_normalizes():
    o = _ohlcv(200)
    # First half: high funding (entry zone). Second half: low (exit zone).
    half = 100 // 8 + 1  # ~13 periods
    f_high = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=half, freq="8h", tz="UTC"),
            "funding_rate": [0.0010] * half,  # 0.10%
        }
    )
    f_low = pd.DataFrame(
        {
            "timestamp": pd.date_range(
                f_high["timestamp"].iloc[-1] + pd.Timedelta("8h"),
                periods=half,
                freq="8h",
                tz="UTC",
            ),
            "funding_rate": [0.00001] * half,  # 0.001%
        }
    )
    f = pd.concat([f_high, f_low], ignore_index=True)
    cfg = FundingCarryConfig(
        entry_funding_threshold_pct=0.03,
        exit_funding_threshold_pct=0.005,
    )
    out = FundingCarryStrategy(cfg).generate_signals(o, funding=f)
    assert out["exit_trigger"].sum() > 0, "exit must fire when funding normalizes"


def test_per_bar_funding_overrides_constant():
    """When df has funding_rate_per_8h_pct, it should override bt_cfg constant."""
    n = 50
    df = _ohlcv(n)
    df["atr"] = 1.0
    df["entry_trigger"] = False
    df.loc[4, "entry_trigger"] = True
    df["direction"] = "short"
    # Per-bar funding: +0.10% per 8h (favorable to short)
    df["funding_rate_per_8h_pct"] = 0.10

    cfg = FundingCarryConfig(
        atr_stop_mult=20.0,  # don't trigger
        max_hold_hours=40,
        cooldown_bars=0,
    )
    # Set the bt_cfg constant to NEGATIVE so we can detect which path is used.
    bt = BacktestConfig(
        initial_capital=10_000,
        fee_rate=0.0,
        slippage_pct=0.0,
        funding_rate_per_8h_pct=-1.0,  # would hurt short heavily
    )
    res = run_backtest(df, strat_cfg=cfg, bt_cfg=bt)
    assert len(res.trades) == 1
    t = res.trades[0]
    # Price didn't move, so price PnL is 0. Funding should be positive
    # (short receives positive funding) if per-bar override worked.
    # If bt_cfg's -1.0 constant had been used, funding_usd would be very negative.
    assert t.funding_usd > 0, "per-bar funding should override constant"
    assert t.total_pnl_usd > 0
    # Sanity: final equity reflects the funding gain.
    final_eq = float(res.equity_curve["equity"].iloc[-1])
    assert final_eq > 10_000


def test_exit_trigger_closes_position_at_next_open():
    n = 30
    df = _ohlcv(n)
    df["atr"] = 1.0
    df["entry_trigger"] = False
    df.loc[4, "entry_trigger"] = True  # enter at bar 5
    df["direction"] = "short"
    df["exit_trigger"] = False
    df.loc[10, "exit_trigger"] = True  # signal exit at bar 10 -> fills bar 11
    df["funding_rate_per_8h_pct"] = 0.0

    cfg = FundingCarryConfig(
        atr_stop_mult=20.0,
        max_hold_hours=100,
        cooldown_bars=0,
    )
    bt = BacktestConfig(initial_capital=10_000, fee_rate=0.0, slippage_pct=0.0)
    res = run_backtest(df, strat_cfg=cfg, bt_cfg=bt)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "exit_signal"
    assert t.bars_held == 11 - 5  # entered at bar 5, exited at bar 11

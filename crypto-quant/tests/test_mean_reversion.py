"""Tests for MeanReversionStrategy signal generation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_quant.strategy.mean_reversion import (
    MeanReversionConfig,
    MeanReversionStrategy,
)


def _synthetic_pump_df(n: int = 400) -> pd.DataFrame:
    """A coin that quietly trades sideways, then pumps +40% in 24h, then fades."""
    rng = np.random.default_rng(0)
    base = 1.0
    quiet = base + rng.normal(0, 0.005, n - 60).cumsum() * 0.05
    quiet = np.clip(quiet, 0.8, 1.2)
    pump = np.linspace(quiet[-1], quiet[-1] * 1.45, 24)
    fade = np.linspace(pump[-1], pump[-1] * 0.95, 36)
    closes = np.concatenate([quiet, pump, fade])
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "timestamp": idx,
            "open": closes,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.concatenate(
                [np.ones(n - 60) * 100, np.ones(24) * 500, np.ones(36) * 200]
            ),
        }
    )
    return df


def test_mean_reversion_emits_short_signal_on_pump():
    df = _synthetic_pump_df()
    # Use slightly relaxed thresholds: the synthetic pump volume gets
    # averaged into vol_ma quickly, which the default vol_mult=1.5 sometimes
    # outruns. The point of this test is signal-logic correctness, not
    # parameter realism — that lives in the live backtest.
    strat = MeanReversionStrategy(
        MeanReversionConfig(vol_mult=1.0, extension_pct=10.0, rsi_threshold=65.0)
    )
    out = strat.generate_signals(df)
    assert "entry_trigger" in out.columns
    assert "direction" in out.columns
    assert (out["direction"] == "short").all()
    assert out["entry_trigger"].sum() >= 1, "should fire at least once on a +40% pump"


def test_mean_reversion_no_signal_on_quiet_market():
    rng = np.random.default_rng(1)
    closes = 1.0 + rng.normal(0, 0.002, 400).cumsum() * 0.01
    closes = np.clip(closes, 0.9, 1.1)
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC"),
            "open": closes,
            "high": closes * 1.001,
            "low": closes * 0.999,
            "close": closes,
            "volume": np.ones(len(closes)) * 100,
        }
    )
    strat = MeanReversionStrategy(MeanReversionConfig())
    out = strat.generate_signals(df)
    assert out["entry_trigger"].sum() == 0, "quiet market must not trigger pump-fade"


def test_mean_reversion_short_circuits_on_insufficient_data():
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=50, freq="1h", tz="UTC"),
            "open": [1.0] * 50,
            "high": [1.0] * 50,
            "low": [1.0] * 50,
            "close": [1.0] * 50,
            "volume": [100.0] * 50,
        }
    )
    out = MeanReversionStrategy().generate_signals(df)
    assert out["entry_trigger"].sum() == 0
    assert (out["direction"] == "short").all()

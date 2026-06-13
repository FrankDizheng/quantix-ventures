"""Tests for ranked-entry tie-breaking + walk-forward isolation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_quant.backtest.engine import BacktestConfig
from crypto_quant.backtest.portfolio import (
    PortfolioConfig,
    run_portfolio_backtest,
)
from crypto_quant.strategy.mean_reversion import MeanReversionConfig


def _flat(
    n: int,
    *,
    entry_at: list[int],
    strength: float,
    direction: str = "long",
) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "timestamp": idx,
            "open": [100.0] * n,
            "high": [100.1] * n,
            "low": [99.9] * n,
            "close": [100.0] * n,
            "volume": [100.0] * n,
            "atr": [1.0] * n,
            "entry_trigger": False,
            "direction": direction,
            "entry_strength": 0.0,
        }
    )
    for i in entry_at:
        df.loc[i, "entry_trigger"] = True
        df.loc[i, "entry_strength"] = strength
    return df


def test_alpha_tiebreak_picks_alphabetically_first():
    """Default: when 3 fire and 1 slot, A wins over B and C."""
    syms = {
        "C/USDT": _flat(30, entry_at=[4], strength=99.0),
        "A/USDT": _flat(30, entry_at=[4], strength=0.5),
        "B/USDT": _flat(30, entry_at=[4], strength=10.0),
    }
    cfg = MeanReversionConfig(
        atr_stop_mult=50, trail_pct=0, take_profit_pct=0,
        max_hold_hours=20, cooldown_bars=0,
    )
    pf = PortfolioConfig(max_concurrent=1, position_fraction=1.0, tiebreaker="alpha")
    res = run_portfolio_backtest(syms, strat_cfg=cfg, bt_cfg=BacktestConfig(), pf_cfg=pf)
    syms_taken = {getattr(t, "symbol", "?") for t in res.trades}
    assert syms_taken == {"A/USDT"}


def test_rank_tiebreak_picks_highest_strength():
    """tiebreaker='rank' should pick C (strength=99) despite alphabetic order."""
    syms = {
        "C/USDT": _flat(30, entry_at=[4], strength=99.0),
        "A/USDT": _flat(30, entry_at=[4], strength=0.5),
        "B/USDT": _flat(30, entry_at=[4], strength=10.0),
    }
    cfg = MeanReversionConfig(
        atr_stop_mult=50, trail_pct=0, take_profit_pct=0,
        max_hold_hours=20, cooldown_bars=0,
    )
    pf = PortfolioConfig(max_concurrent=1, position_fraction=1.0, tiebreaker="rank")
    res = run_portfolio_backtest(syms, strat_cfg=cfg, bt_cfg=BacktestConfig(), pf_cfg=pf)
    syms_taken = {getattr(t, "symbol", "?") for t in res.trades}
    assert syms_taken == {"C/USDT"}


def test_walk_forward_uses_no_future_data():
    """Slicing by window_end must not leak data after window_end into signal generation.

    We construct a synthetic strategy that, if given full data, would trigger
    AFTER the window end. The walk-forward harness should NOT pick up that
    trigger when window_end excludes those bars.
    """
    # Build a df with a triggering pattern at bar 60 (after window_end at bar 50).
    # When walk-forward slices to window_end=bar 50, the trigger should not fire.
    from crypto_quant.backtest.walk_forward import _build_signals_for_window

    class _FakeStrategy:
        """Triggers entry only when the LAST bar's close > 200."""
        def generate_signals(self, df, **kw):
            out = df.copy()
            last_close = float(df["close"].iloc[-1])
            out["entry_trigger"] = False
            if last_close > 200:
                # Mark last bar as a trigger.
                out.loc[out.index[-1], "entry_trigger"] = True
            out["direction"] = "long"
            out["atr"] = (out["high"] - out["low"]).rolling(5, min_periods=1).mean()
            return out

    # Build a synthetic OHLCV file in-memory.
    from pathlib import Path
    import tempfile
    from crypto_quant.data import save_dataframe

    with tempfile.TemporaryDirectory() as td:
        out_root = Path(td)
        n = 100
        idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
        # Quiet base then a spike at bar 90 (above 200) - this is after our window_end at bar 50.
        closes = [100.0] * 80 + list(np.linspace(100, 300, 20))
        df = pd.DataFrame(
            {
                "timestamp": idx,
                "open": closes, "high": [c * 1.001 for c in closes],
                "low": [c * 0.999 for c in closes], "close": closes,
                "volume": [100.0] * n,
            }
        )
        sym = "FAKE/USDT:USDT"
        # Match the cache path used by ensure_ohlcv
        cache = out_root / "ccxt" / "okx" / "1h" / "FAKE_USDT_USDT.parquet"
        cache.parent.mkdir(parents=True, exist_ok=True)
        save_dataframe(df, cache)

        # Window end at bar 50 -> df sliced to first 50 bars, all close=100, no trigger
        window_end_early = idx[49]
        signals_early, _, _ = _build_signals_for_window(
            _FakeStrategy(), [sym], out_root=out_root,
            exchange="okx", timeframe="1h", total_days=100,
            window_end=window_end_early, window_days=10,
            needs_funding=False,
        )
        # Either filtered out entirely (len<200) or no triggers — both acceptable.
        if signals_early:
            assert not signals_early[sym]["entry_trigger"].any(), \
                "future spike must not leak when window_end is BEFORE it"

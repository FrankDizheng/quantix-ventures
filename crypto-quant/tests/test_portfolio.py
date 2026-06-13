"""Tests for the portfolio backtest engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_quant.backtest.engine import BacktestConfig
from crypto_quant.backtest.portfolio import (
    PortfolioConfig,
    run_portfolio_backtest,
)
from crypto_quant.strategy.mean_reversion import MeanReversionConfig


def _flat_df(
    n: int = 60, price: float = 100.0, entry_at: list[int] | None = None,
    direction: str = "long", trend: float = 0.0,
) -> pd.DataFrame:
    """Build a synthetic OHLCV with optional linear trend and entries."""
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    closes = np.array([price * (1 + trend * i) for i in range(n)])
    df = pd.DataFrame(
        {
            "timestamp": idx,
            "open": closes,
            "high": closes * 1.001,
            "low": closes * 0.999,
            "close": closes,
            "volume": [100.0] * n,
            "atr": closes * 0.01,
            "entry_trigger": False,
            "direction": direction,
        }
    )
    if entry_at:
        for i in entry_at:
            df.loc[i, "entry_trigger"] = True
    return df


def test_portfolio_respects_max_concurrent():
    """5 entries fire on same bar but max_concurrent=2 -> only 2 open."""
    symbols = {f"COIN{i}/USDT:USDT": _flat_df(n=40, entry_at=[4]) for i in range(5)}
    cfg = MeanReversionConfig(
        atr_stop_mult=50.0, trail_pct=0.0, take_profit_pct=0.0,
        max_hold_hours=30, cooldown_bars=0,
    )
    pf = PortfolioConfig(max_concurrent=2, position_fraction=0.5)
    res = run_portfolio_backtest(
        symbols, strat_cfg=cfg, bt_cfg=BacktestConfig(initial_capital=10_000), pf_cfg=pf,
    )
    assert len(res.trades) == 2, f"max_concurrent=2 should cap trades to 2, got {len(res.trades)}"
    # Both should be alphabetically first: COIN0, COIN1
    syms_taken = sorted({getattr(t, "symbol", "?") for t in res.trades})
    assert syms_taken == ["COIN0/USDT:USDT", "COIN1/USDT:USDT"]


def test_portfolio_per_position_sizing():
    """size = current_equity * position_fraction; final equity reflects scaling."""
    # One symbol, one trade, price up 10%, no fees/slip.
    sym = "AAA/USDT:USDT"
    df = _flat_df(n=30, entry_at=[4], trend=0.005)  # ~+15% over 30 bars
    cfg = MeanReversionConfig(
        atr_stop_mult=50.0, trail_pct=0.0, take_profit_pct=0.0,
        max_hold_hours=20, cooldown_bars=0,
    )
    pf = PortfolioConfig(max_concurrent=1, position_fraction=0.5)
    res = run_portfolio_backtest(
        {sym: df},
        strat_cfg=cfg,
        bt_cfg=BacktestConfig(initial_capital=10_000, fee_rate=0.0, slippage_pct=0.0),
        pf_cfg=pf,
    )
    assert len(res.trades) == 1
    # Entry at bar 5 (price ~1.025*100=102.5), exit at bar 25 (max_hold).
    t = res.trades[0]
    expected_size = 10_000 * 0.5
    # gross_ret ~ (close_25 - open_5)/open_5 - tiny slip = ~0.099
    expected_pnl_close = expected_size * t.pnl_pct / 100
    assert abs(t.pnl_usd - expected_pnl_close) < 0.01


def test_portfolio_slot_frees_after_exit():
    """When position closes, next eligible entry can take the slot."""
    a_df = _flat_df(n=60, entry_at=[4], trend=0.0)
    b_df = _flat_df(n=60, entry_at=[20], trend=0.0)
    cfg = MeanReversionConfig(
        atr_stop_mult=50.0, trail_pct=0.0, take_profit_pct=0.0,
        max_hold_hours=10, cooldown_bars=0,
    )
    pf = PortfolioConfig(max_concurrent=1, position_fraction=1.0)
    res = run_portfolio_backtest(
        {"A/USDT:USDT": a_df, "B/USDT:USDT": b_df},
        strat_cfg=cfg, bt_cfg=BacktestConfig(initial_capital=10_000), pf_cfg=pf,
    )
    syms_taken = [getattr(t, "symbol", "?") for t in res.trades]
    assert "A/USDT:USDT" in syms_taken
    # B's entry is at bar 20; A's exit (max_hold) is at bar 5+10=15.
    # So B should be able to enter at bar 21 (one bar after trigger).
    assert "B/USDT:USDT" in syms_taken


def test_portfolio_equity_curve_tracks_mark_to_market():
    """Equity curve should be non-empty and equal initial when no positions."""
    sym = "AAA/USDT:USDT"
    df = _flat_df(n=20)  # no entries
    cfg = MeanReversionConfig(atr_stop_mult=10, max_hold_hours=10, cooldown_bars=0)
    res = run_portfolio_backtest(
        {sym: df},
        strat_cfg=cfg, bt_cfg=BacktestConfig(initial_capital=10_000),
    )
    assert len(res.equity_curve) == 20
    # No trades, equity stays at 10000
    assert (res.equity_curve["equity"] == 10_000).all()


def test_portfolio_funding_inverts_for_long_vs_short():
    """Per-bar funding column applied; long pays, short receives."""
    sym = "AAA/USDT:USDT"
    # Build long and short variants of the same flat market.
    base = _flat_df(n=60, entry_at=[4], direction="long")
    base["funding_rate_per_8h_pct"] = 0.10  # high funding
    long_df = base.copy()
    short_df = base.copy()
    short_df["direction"] = "short"
    cfg = MeanReversionConfig(
        atr_stop_mult=50.0, max_hold_hours=50, cooldown_bars=0,
        trail_pct=0.0, take_profit_pct=0.0,
    )
    pf = PortfolioConfig(max_concurrent=1, position_fraction=1.0)
    bt = BacktestConfig(initial_capital=10_000, fee_rate=0.0, slippage_pct=0.0)
    long_res = run_portfolio_backtest({sym: long_df}, strat_cfg=cfg, bt_cfg=bt, pf_cfg=pf)
    short_res = run_portfolio_backtest({sym: short_df}, strat_cfg=cfg, bt_cfg=bt, pf_cfg=pf)
    assert long_res.trades[0].funding_usd < 0
    assert short_res.trades[0].funding_usd > 0

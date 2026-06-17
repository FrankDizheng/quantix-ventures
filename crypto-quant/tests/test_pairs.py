from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_quant.pairs import (
    PairResearchConfig,
    diagnose_pairs,
    evaluate_pair,
    rank_pairs,
)


def _ohlcv_from_log_price(log_price: np.ndarray, symbol: str) -> pd.DataFrame:
    close = np.exp(log_price)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(close), freq="1h", tz="UTC"),
            "open": close,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": np.ones(len(close)) * 1000,
            "symbol": symbol,
        }
    )


def _funding(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01", periods=len(values), freq="8h", tz="UTC"
            ),
            "funding_rate": values,
        }
    )


def _order_book(spread_bps: float, depth_usd: float) -> pd.DataFrame:
    mid = 1.0
    half = spread_bps / 20_000
    return pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2026-01-01", tz="UTC")],
            "symbol": ["AAA/USDT:USDT"],
            "best_bid": [mid * (1 - half)],
            "best_ask": [mid * (1 + half)],
            "mid": [mid],
            "spread_bps": [spread_bps],
            "bid_depth_25bps_usd": [depth_usd],
            "ask_depth_25bps_usd": [depth_usd],
            "bid_depth_50bps_usd": [depth_usd * 2],
            "ask_depth_50bps_usd": [depth_usd * 2],
            "levels": [50],
        }
    )


def test_evaluate_pair_finds_mean_reverting_spread():
    rng = np.random.default_rng(7)
    n = 900
    log_b = 2.0 + rng.normal(0, 0.01, n).cumsum()
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = 0.92 * spread[i - 1] + rng.normal(0, 0.035)
    log_a = 1.15 * log_b + spread

    cfg = PairResearchConfig(
        min_overlap=500,
        min_corr=0.45,
        min_rolling_corr=0.20,
        z_window=80,
        min_trades=2,
        max_half_life_hours=80,
    )
    stats = evaluate_pair(
        "AAA/USDT:USDT",
        _ohlcv_from_log_price(log_a, "AAA"),
        "BBB/USDT:USDT",
        _ohlcv_from_log_price(log_b, "BBB"),
        cfg,
    )

    assert stats is not None
    assert stats.hedge_beta > 0
    assert 0 < stats.half_life_hours < 80
    assert stats.opportunities >= 2
    assert stats.score > 0


def test_rank_pairs_filters_uncorrelated_noise():
    rng = np.random.default_rng(11)
    n = 500
    df_a = _ohlcv_from_log_price(rng.normal(0, 0.04, n).cumsum(), "AAA")
    df_b = _ohlcv_from_log_price(rng.normal(0, 0.04, n).cumsum(), "BBB")

    ranked = rank_pairs(
        {"AAA/USDT:USDT": df_a, "BBB/USDT:USDT": df_b},
        PairResearchConfig(min_overlap=400, min_corr=0.95),
    )

    assert ranked.empty


def test_diagnose_pairs_keeps_rejection_reasons():
    rng = np.random.default_rng(13)
    n = 500
    df_a = _ohlcv_from_log_price(rng.normal(0, 0.04, n).cumsum(), "AAA")
    df_b = _ohlcv_from_log_price(rng.normal(0, 0.04, n).cumsum(), "BBB")

    diagnostics = diagnose_pairs(
        {"AAA/USDT:USDT": df_a, "BBB/USDT:USDT": df_b},
        PairResearchConfig(min_overlap=400, min_corr=0.95),
    )

    assert len(diagnostics) == 1
    assert not bool(diagnostics["passed"].iloc[0])
    assert diagnostics["reject_reason"].iloc[0] == "low_full_sample_corr"


def test_diagnose_pairs_adds_funding_diagnostics():
    rng = np.random.default_rng(17)
    n = 900
    log_b = 2.0 + rng.normal(0, 0.01, n).cumsum()
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = 0.9 * spread[i - 1] + rng.normal(0, 0.03)
    log_a = 1.1 * log_b + spread

    cfg = PairResearchConfig(
        min_overlap=500,
        min_corr=0.45,
        min_rolling_corr=0.20,
        z_window=80,
        min_trades=2,
        max_half_life_hours=80,
    )
    diagnostics = diagnose_pairs(
        {
            "AAA/USDT:USDT": _ohlcv_from_log_price(log_a, "AAA"),
            "BBB/USDT:USDT": _ohlcv_from_log_price(log_b, "BBB"),
        },
        cfg,
        funding_by_symbol={
            "AAA/USDT:USDT": _funding([0.0002, 0.0003, 0.0002, 0.0001]),
            "BBB/USDT:USDT": _funding([0.0001, 0.00012, 0.00009, 0.00011]),
        },
    )

    row = diagnostics.iloc[0]
    assert row["funding_observations"] == 4
    assert row["mean_abs_funding_diff_pct"] > 0
    assert row["half_life_funding_drag_ratio"] > 0


def test_diagnose_pairs_adds_liquidity_diagnostics():
    rng = np.random.default_rng(19)
    n = 900
    log_b = 2.0 + rng.normal(0, 0.01, n).cumsum()
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = 0.9 * spread[i - 1] + rng.normal(0, 0.03)
    log_a = 1.1 * log_b + spread

    cfg = PairResearchConfig(
        min_overlap=500,
        min_corr=0.45,
        min_rolling_corr=0.20,
        z_window=80,
        min_trades=2,
        max_half_life_hours=80,
    )
    diagnostics = diagnose_pairs(
        {
            "AAA/USDT:USDT": _ohlcv_from_log_price(log_a, "AAA"),
            "BBB/USDT:USDT": _ohlcv_from_log_price(log_b, "BBB"),
        },
        cfg,
        order_book_by_symbol={
            "AAA/USDT:USDT": _order_book(4.0, 12_000),
            "BBB/USDT:USDT": _order_book(6.0, 8_000),
        },
    )

    row = diagnostics.iloc[0]
    assert row["pair_spread_bps"] == 10.0
    assert row["min_depth_25bps_usd"] == 8_000
    assert row["liquidity_cost_edge_ratio"] > 0

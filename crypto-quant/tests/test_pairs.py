from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_quant.pairs import PairResearchConfig, evaluate_pair, rank_pairs


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

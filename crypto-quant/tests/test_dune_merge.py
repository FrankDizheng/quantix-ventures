import pandas as pd

from crypto_quant.onchain.netflow import DuneFilterConfig, apply_dune_entry_filter, merge_netflow_to_ohlcv


def test_merge_and_filter() -> None:
    ohlcv = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC"),
            "close": [1.0] * 48,
            "volume": [100.0] * 48,
        }
    )
    daily = pd.DataFrame(
        {
            "day": pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC"),
            "net_inflow_usd": [1_000_000.0, 5_000_000.0, -500_000.0],
            "inflow_usd": [0, 0, 0],
            "outflow_usd": [0, 0, 0],
        }
    )
    merged = merge_netflow_to_ohlcv(ohlcv, daily, rolling_days=2)
    assert "net_inflow_roll_usd" in merged.columns
    cond = pd.Series([True] * len(merged))
    filtered = apply_dune_entry_filter(cond, merged, DuneFilterConfig(max_rolling_net_inflow_usd=3_000_000))
    assert filtered.sum() < len(filtered)

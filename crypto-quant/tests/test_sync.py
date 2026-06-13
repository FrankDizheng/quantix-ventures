"""Tests for local data cache helpers."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from crypto_quant.data.sync import cache_status, ohlcv_cache_path


def test_cache_status_missing(tmp_path: Path) -> None:
    path = tmp_path / "missing.parquet"
    needs, reason = cache_status(path, min_bars=100, max_stale_hours=24)
    assert needs is True
    assert reason == "missing"


def test_cache_status_fresh(tmp_path: Path) -> None:
    path = tmp_path / "fresh.parquet"
    ts = datetime.now(timezone.utc) - timedelta(hours=1)
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range(ts, periods=200, freq="h", tz="UTC"),
            "open": [1.0] * 200,
            "high": [1.0] * 200,
            "low": [1.0] * 200,
            "close": [1.0] * 200,
            "volume": [1.0] * 200,
        }
    )
    df.to_parquet(path, index=False)
    needs, reason = cache_status(path, min_bars=100, max_stale_hours=24)
    assert needs is False
    assert reason == "ok"


def test_ohlcv_cache_path() -> None:
    p = ohlcv_cache_path(Path("/data"), "okx", "1h", "PEPE/USDT:USDT")
    assert p.name == "PEPE_USDT_USDT.parquet"

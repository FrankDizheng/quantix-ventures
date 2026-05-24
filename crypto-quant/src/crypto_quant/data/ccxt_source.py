"""Fetch OHLCV and trades via CCXT (MIT) — unified public exchange APIs."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import ccxt
import pandas as pd


OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class CCXTFetcher:
    """Incremental historical OHLCV and recent trades from any CCXT-supported exchange."""

    def __init__(
        self,
        exchange_id: str = "binance",
        *,
        rate_limit_ms: int = 200,
        timeout_ms: int = 30_000,
    ) -> None:
        exchange_class = getattr(ccxt, exchange_id)
        self.exchange: ccxt.Exchange = exchange_class(
            {"enableRateLimit": True, "timeout": timeout_ms}
        )
        self.rate_limit_ms = rate_limit_ms

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Paginate OHLCV candles between since and until (UTC)."""
        self.exchange.load_markets()
        since_ms = self._to_ms(since) if since else None
        until_ms = self._to_ms(until) if until else None

        rows: list[list[Any]] = []
        cursor = since_ms
        while True:
            batch = self.exchange.fetch_ohlcv(
                symbol, timeframe, since=cursor, limit=limit
            )
            if not batch:
                break
            rows.extend(batch)
            last_ts = batch[-1][0]
            if until_ms and last_ts >= until_ms:
                break
            if len(batch) < limit:
                break
            cursor = last_ts + 1
            time.sleep(self.rate_limit_ms / 1000.0)

        df = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if until_ms:
            df = df[df["timestamp"] <= pd.Timestamp(until_ms, unit="ms", tz="UTC")]
        if since_ms:
            df = df[df["timestamp"] >= pd.Timestamp(since_ms, unit="ms", tz="UTC")]
        return df.reset_index(drop=True)

    def fetch_trades(
        self,
        symbol: str,
        *,
        since: datetime | None = None,
        limit: int = 1000,
        max_batches: int = 50,
    ) -> pd.DataFrame:
        """Fetch recent public trades (exchange-dependent depth)."""
        self.exchange.load_markets()
        since_ms = self._to_ms(since) if since else None
        rows: list[dict[str, Any]] = []
        cursor = since_ms
        for _ in range(max_batches):
            batch = self.exchange.fetch_trades(symbol, since=cursor, limit=limit)
            if not batch:
                break
            rows.extend(batch)
            cursor = batch[-1]["timestamp"] + 1
            if len(batch) < limit:
                break
            time.sleep(self.rate_limit_ms / 1000.0)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    @staticmethod
    def _to_ms(dt: datetime) -> int:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

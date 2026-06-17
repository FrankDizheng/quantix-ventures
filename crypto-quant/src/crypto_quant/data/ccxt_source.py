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
        opts: dict[str, Any] = {"enableRateLimit": True, "timeout": timeout_ms}
        if exchange_id in ("okx", "bybit", "binanceusdm", "bitget", "gate"):
            opts["options"] = {"defaultType": "swap"}
        self.exchange: ccxt.Exchange = exchange_class(opts)
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
        tf_ms = int(self.exchange.parse_timeframe(timeframe) * 1000)
        now_ms = self._to_ms(datetime.now(timezone.utc))

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
            # Stop when we've reached the latest bar (within one candle of now).
            if last_ts >= now_ms - tf_ms:
                break
            next_cursor = last_ts + 1
            if cursor is not None and next_cursor <= cursor:
                break
            cursor = next_cursor
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

    def fetch_funding_rate_history(
        self,
        symbol: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
        max_batches: int = 200,
    ) -> pd.DataFrame:
        """Paginate perp funding-rate history.

        Returns columns: timestamp, funding_rate (decimal, e.g. 0.0001 = 0.01%).
        Most exchanges (OKX, Binance, Bybit) publish funding every 8 hours.
        """
        if not self.exchange.has.get("fetchFundingRateHistory"):
            raise NotImplementedError(
                f"{self.exchange.id} does not expose fetchFundingRateHistory via CCXT"
            )
        self.exchange.load_markets()
        since_ms = self._to_ms(since) if since else None
        until_ms = self._to_ms(until) if until else None

        rows: list[dict[str, Any]] = []
        cursor = since_ms
        for _ in range(max_batches):
            batch = self.exchange.fetch_funding_rate_history(
                symbol, since=cursor, limit=limit
            )
            if not batch:
                break
            rows.extend(batch)
            last_ts = batch[-1]["timestamp"]
            if until_ms and last_ts >= until_ms:
                break
            next_cursor = last_ts + 1
            if cursor is not None and next_cursor <= cursor:
                break
            cursor = next_cursor
            time.sleep(self.rate_limit_ms / 1000.0)

        if not rows:
            return pd.DataFrame(columns=["timestamp", "funding_rate"])
        df = pd.DataFrame(rows)
        df = df[["timestamp", "fundingRate"]].rename(
            columns={"fundingRate": "funding_rate"}
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce")
        df = df.dropna(subset=["funding_rate"]).drop_duplicates(
            subset=["timestamp"], keep="last"
        )
        if since_ms:
            df = df[df["timestamp"] >= pd.Timestamp(since_ms, unit="ms", tz="UTC")]
        if until_ms:
            df = df[df["timestamp"] <= pd.Timestamp(until_ms, unit="ms", tz="UTC")]
        return df.sort_values("timestamp").reset_index(drop=True)

    def fetch_order_book_snapshot(
        self,
        symbol: str,
        *,
        limit: int = 50,
    ) -> pd.DataFrame:
        """Fetch one order-book snapshot and summarize executable depth.

        Depth fields are quote-notional USD approximations inside a distance
        from mid price. For USDT linear perps this is close enough for research
        diagnostics.
        """
        self.exchange.load_markets()
        book = self.exchange.fetch_order_book(symbol, limit=limit)
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        ts = book.get("timestamp") or self._to_ms(datetime.now(timezone.utc))
        if not bids or not asks:
            return pd.DataFrame()

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2
        if mid <= 0:
            return pd.DataFrame()

        def depth_within(levels: list[list[float]], max_bps: float, side: str) -> float:
            total = 0.0
            for price_raw, amount_raw, *_rest in levels:
                price = float(price_raw)
                amount = float(amount_raw)
                distance_bps = (
                    (mid - price) / mid * 10_000
                    if side == "bid"
                    else (price - mid) / mid * 10_000
                )
                if distance_bps <= max_bps:
                    total += price * amount
            return total

        row = {
            "timestamp": pd.to_datetime(ts, unit="ms", utc=True),
            "symbol": symbol,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid,
            "spread_bps": (best_ask - best_bid) / mid * 10_000,
            "bid_depth_10bps_usd": depth_within(bids, 10, "bid"),
            "ask_depth_10bps_usd": depth_within(asks, 10, "ask"),
            "bid_depth_25bps_usd": depth_within(bids, 25, "bid"),
            "ask_depth_25bps_usd": depth_within(asks, 25, "ask"),
            "bid_depth_50bps_usd": depth_within(bids, 50, "bid"),
            "ask_depth_50bps_usd": depth_within(asks, 50, "ask"),
            "levels": min(len(bids), len(asks)),
        }
        return pd.DataFrame([row])

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

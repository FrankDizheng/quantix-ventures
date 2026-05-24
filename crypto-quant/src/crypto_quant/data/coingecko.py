"""CoinGecko public API — market charts and coin metadata (no key for Demo tier)."""

from __future__ import annotations

import httpx
import pandas as pd

API_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoFetcher:
    """Free-tier market history (rate-limited ~10-30 req/min)."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.client = httpx.Client(
            base_url=API_BASE,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> CoinGeckoFetcher:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def market_chart(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        *,
        days: int | str = 30,
    ) -> pd.DataFrame:
        """OHLC-like price series: [timestamp_ms, price]."""
        resp = self.client.get(
            f"/coins/{coin_id}/market_chart",
            params={"vs_currency": vs_currency, "days": days},
        )
        resp.raise_for_status()
        data = resp.json()
        prices = data.get("prices", [])
        volumes = {int(v[0]): v[1] for v in data.get("total_volumes", [])}
        df = pd.DataFrame(prices, columns=["timestamp_ms", "price"])
        df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
        df["volume"] = df["timestamp_ms"].map(volumes)
        df["coin_id"] = coin_id
        df["vs_currency"] = vs_currency
        return df[["timestamp", "price", "volume", "coin_id", "vs_currency"]]

    def ohlc(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        *,
        days: int = 7,
    ) -> pd.DataFrame:
        """Daily OHLC candles (CoinGecko aggregates exchange data)."""
        resp = self.client.get(
            f"/coins/{coin_id}/ohlc",
            params={"vs_currency": vs_currency, "days": days},
        )
        resp.raise_for_status()
        rows = resp.json()
        df = pd.DataFrame(
            rows,
            columns=["timestamp_ms", "open", "high", "low", "close"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
        df["coin_id"] = coin_id
        return df

    def list_coins(self, limit: int = 100) -> pd.DataFrame:
        """Top coins by market cap rank."""
        resp = self.client.get(
            "/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": min(limit, 250),
                "page": 1,
            },
        )
        resp.raise_for_status()
        return pd.DataFrame(resp.json())

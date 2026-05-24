"""Download raw archives from Binance Data Collection (data.binance.vision).

Official public dumps — no API key. See:
https://github.com/binance/binance-public-data
"""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin

import httpx
import pandas as pd
from tqdm import tqdm

Market = Literal["spot", "um", "cm"]
DataType = Literal["klines", "aggTrades", "trades"]

BASE_URL = "https://data.binance.vision/data"

# Column headers from binance-public-data README (klines)
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]


class BinanceVisionFetcher:
    """Bulk historical klines / trades from Binance public S3-style archive."""

    def __init__(self, *, timeout: float = 120.0) -> None:
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> BinanceVisionFetcher:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def build_url(
        self,
        market: Market,
        data_type: DataType,
        symbol: str,
        *,
        interval: str | None = None,
        period: Literal["daily", "monthly"] = "monthly",
        year: int | None = None,
        month: int | None = None,
        day: date | None = None,
    ) -> str:
        """Build archive URL for a single zip file."""
        if period == "monthly":
            if year is None or month is None:
                raise ValueError("year and month required for monthly period")
            suffix = f"{year:04d}-{month:02d}"
        else:
            if day is None:
                raise ValueError("day required for daily period")
            suffix = day.strftime("%Y-%m-%d")

        parts = [BASE_URL, market, period, data_type, symbol]
        if data_type == "klines":
            if not interval:
                raise ValueError("interval required for klines")
            parts.append(interval)
        filename = f"{symbol}-{interval + '-' if data_type == 'klines' else ''}{suffix}.zip"
        return urljoin("/".join(parts) + "/", filename)

    def download_file(self, url: str, dest: Path) -> bool:
        """Download zip if it exists on the archive. Returns False on 404."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return True
        resp = self.client.get(url)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return True

    def read_klines_zip(self, zip_path: Path) -> pd.DataFrame:
        with zipfile.ZipFile(zip_path) as zf:
            name = zf.namelist()[0]
            with zf.open(name) as f:
                df = pd.read_csv(f, header=None, names=KLINE_COLUMNS)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
        for col in ("open", "high", "low", "close", "volume", "quote_volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def fetch_klines_range(
        self,
        symbol: str,
        interval: str,
        start: date,
        end: date,
        *,
        market: Market = "spot",
        cache_dir: Path | None = None,
    ) -> pd.DataFrame:
        """Download monthly (+ edge daily) kline archives and merge."""
        frames: list[pd.DataFrame] = []
        cache = cache_dir or Path("caches/binance_vision")
        months = _iter_months(start, end)
        for year, month in tqdm(months, desc=f"{symbol} {interval}"):
            url = self.build_url(
                market, "klines", symbol,
                interval=interval, period="monthly", year=year, month=month,
            )
            zip_name = url.rsplit("/", 1)[-1]
            dest = cache / market / "klines" / symbol / interval / zip_name
            if not self.download_file(url, dest):
                continue
            frames.append(self.read_klines_zip(dest))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time")
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
        mask = (df["open_time"] >= start_ts) & (df["open_time"] < end_ts)
        return df.loc[mask].reset_index(drop=True)


def _iter_months(start: date, end: date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out

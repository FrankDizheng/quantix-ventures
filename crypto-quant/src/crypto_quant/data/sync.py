"""Local data cache: ensure OHLCV and Dune netflow exist before backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from crypto_quant.data import CCXTFetcher, save_dataframe
from crypto_quant.data.storage import load_dataframe
from crypto_quant.onchain.netflow import ensure_netflow_for_symbol, load_token_map


def ohlcv_cache_path(out_root: Path, exchange: str, timeframe: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    return out_root / "ccxt" / exchange / timeframe / f"{safe}.parquet"


def funding_cache_path(out_root: Path, exchange: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    return out_root / "funding" / exchange / f"{safe}.parquet"


def order_book_cache_path(out_root: Path, exchange: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    return out_root / "orderbook" / exchange / f"{safe}_latest.parquet"


def ensure_order_book_snapshot(
    out_root: Path,
    *,
    exchange: str,
    symbol: str,
    max_stale_minutes: float = 15.0,
    force: bool = False,
    fetcher: CCXTFetcher | None = None,
) -> pd.DataFrame:
    """Load a recent order-book snapshot or fetch a fresh one."""
    path = order_book_cache_path(out_root, exchange, symbol)
    if not force and path.exists():
        try:
            cached = load_dataframe(path)
            if not cached.empty:
                ts = pd.to_datetime(cached["timestamp"].iloc[-1], utc=True)
                age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60
                if age_min <= max_stale_minutes:
                    return cached
        except Exception:
            pass

    fetcher = fetcher or CCXTFetcher(exchange, rate_limit_ms=200)
    df = fetcher.fetch_order_book_snapshot(symbol)
    if not df.empty:
        save_dataframe(df, path)
    return df


def ensure_funding(
    out_root: Path,
    *,
    exchange: str,
    symbol: str,
    days: int,
    max_stale_hours: float = 24.0,
    force: bool = False,
    fetcher: CCXTFetcher | None = None,
) -> pd.DataFrame:
    """Load cached funding-rate history or download when missing/stale.

    Returns columns: timestamp (UTC), funding_rate (decimal).
    Typical cadence is every 8h — so ~3*days rows expected.
    """
    path = funding_cache_path(out_root, exchange, symbol)
    # Funding cadence is every 8h, so min_bars ≈ 3 * days * 0.5 (loose).
    min_bars = max(3, int(days * 3 * 0.5))
    needs, _ = cache_status(
        path, min_bars=min_bars, max_stale_hours=max_stale_hours
    )
    if not force and not needs:
        return load_dataframe(path)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    fetcher = fetcher or CCXTFetcher(exchange, rate_limit_ms=200)
    df = fetcher.fetch_funding_rate_history(symbol, since=since)
    if not df.empty:
        save_dataframe(df, path)
    return df


def _timeframe_hours(timeframe: str) -> float:
    unit = timeframe[-1]
    n = int(timeframe[:-1])
    if unit == "h":
        return float(n)
    if unit == "d":
        return n * 24.0
    if unit == "m":
        return n / 60.0
    return 1.0


def cache_status(
    path: Path,
    *,
    min_bars: int,
    max_stale_hours: float,
) -> tuple[bool, str]:
    """Return (needs_refresh, reason)."""
    if not path.exists():
        return True, "missing"
    try:
        df = load_dataframe(path)
    except Exception:
        return True, "corrupt"
    if df.empty:
        return True, "empty"
    if len(df) < min_bars:
        return True, f"short ({len(df)}<{min_bars} bars)"
    last_ts = pd.to_datetime(df["timestamp"].iloc[-1], utc=True)
    age_h = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600
    if age_h > max_stale_hours:
        return True, f"stale ({age_h:.0f}h old)"
    return False, "ok"


@dataclass
class SyncResult:
    ohlcv_fetched: list[str] = field(default_factory=list)
    ohlcv_skipped: list[str] = field(default_factory=list)
    onchain_fetched: list[str] = field(default_factory=list)
    onchain_skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def ensure_ohlcv(
    out_root: Path,
    *,
    exchange: str,
    timeframe: str,
    symbol: str,
    days: int,
    max_stale_hours: float = 24.0,
    force: bool = False,
    fetcher: CCXTFetcher | None = None,
) -> pd.DataFrame:
    """Load cached OHLCV or download when missing / stale / insufficient."""
    path = ohlcv_cache_path(out_root, exchange, timeframe, symbol)
    tf_h = _timeframe_hours(timeframe)
    min_bars = int(days * 24 / tf_h * 0.9)

    needs, reason = cache_status(
        path, min_bars=min_bars, max_stale_hours=max_stale_hours
    )
    if not force and not needs:
        return load_dataframe(path)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    fetcher = fetcher or CCXTFetcher(exchange, rate_limit_ms=200)
    df = fetcher.fetch_ohlcv(symbol, timeframe, since=since)
    if not df.empty:
        save_dataframe(df, path)
    return df


def sync_symbols(
    symbols: list[str],
    *,
    out_root: Path,
    exchange: str,
    timeframe: str,
    days: int,
    dune_days: int | None = None,
    dune_enabled: bool = False,
    max_stale_hours: float = 24.0,
    force: bool = False,
    verbose: bool = True,
) -> SyncResult:
    """Download/update local parquet for OHLCV (+ optional Dune netflow)."""
    result = SyncResult()
    dune_days = dune_days or days
    fetcher = CCXTFetcher(exchange, rate_limit_ms=200)
    tf_h = _timeframe_hours(timeframe)
    min_bars = int(days * 24 / tf_h * 0.9)

    for sym in symbols:
        path = ohlcv_cache_path(out_root, exchange, timeframe, sym)
        needs, reason = cache_status(
            path, min_bars=min_bars, max_stale_hours=max_stale_hours
        )
        if force or needs:
            try:
                ensure_ohlcv(
                    out_root,
                    exchange=exchange,
                    timeframe=timeframe,
                    symbol=sym,
                    days=days,
                    max_stale_hours=max_stale_hours,
                    force=True,
                    fetcher=fetcher,
                )
                result.ohlcv_fetched.append(sym)
                if verbose:
                    n = len(load_dataframe(path))
                    print(f"[sync] OHLCV {sym}: fetched ({n} bars, was {reason})")
            except Exception as e:
                result.errors.append(f"{sym} ohlcv: {e}")
                if verbose:
                    print(f"[sync] OHLCV {sym}: FAIL {e}")
        else:
            result.ohlcv_skipped.append(sym)
            if verbose:
                print(f"[sync] OHLCV {sym}: cached")

        if dune_enabled:
            try:
                before_exists = _netflow_cached(out_root, sym, dune_days)
                df = ensure_netflow_for_symbol(
                    sym,
                    out_root,
                    days=dune_days,
                    use_cache=not force,
                )
                if df is None:
                    if verbose:
                        print(f"[sync] Dune {sym}: skip (no token_map)")
                elif force or not before_exists:
                    result.onchain_fetched.append(sym)
                    if verbose:
                        print(f"[sync] Dune {sym}: fetched ({len(df)} days)")
                else:
                    result.onchain_skipped.append(sym)
                    if verbose:
                        print(f"[sync] Dune {sym}: cached")
            except Exception as e:
                result.errors.append(f"{sym} dune: {e}")
                if verbose:
                    print(f"[sync] Dune {sym}: FAIL {e}")

    return result


def _netflow_cached(out_root: Path, symbol: str, days: int) -> bool:
    from crypto_quant.onchain.netflow import load_token_map, netflow_cache_path, symbol_base

    meta = load_token_map().get(symbol_base(symbol))
    if not meta:
        return False
    path = netflow_cache_path(
        out_root,
        meta.get("chain", "ethereum"),
        meta["address"],
        days,
    )
    return path.exists()


def resolve_pool_symbols(out_root: Path, exchange: str) -> list[str]:
    pool_dir = out_root / "pools"
    latest = pool_dir / f"pool_{exchange}_latest.csv"
    if not latest.exists():
        return []
    df = pd.read_csv(latest)
    return df["symbol"].tolist()


def resolve_token_map_symbols() -> list[str]:
    """Perp-style symbols for all token_map entries."""
    return [f"{base}/USDT:USDT" for base in load_token_map()]


@dataclass
class SyncConfig:
    exchange: str = "okx"
    timeframe: str = "1h"
    lookback_days: int = 90
    dune_lookback_days: int = 90
    dune_enabled: bool = True
    max_stale_hours: float = 24.0

    @classmethod
    def from_cfg(cls, cfg: dict) -> SyncConfig:
        sc = cfg.get("strategy", {})
        sync = cfg.get("sync", {})
        dune = sc.get("dune", {})
        return cls(
            exchange=sync.get("exchange") or sc.get("exchange", "okx"),
            timeframe=sync.get("timeframe") or sc.get("timeframe", "1h"),
            lookback_days=sync.get("lookback_days") or sc.get("lookback_days", 90),
            dune_lookback_days=sync.get("dune_lookback_days")
            or dune.get("lookback_days", 90),
            dune_enabled=dune.get("enabled", False),
            max_stale_hours=float(sync.get("max_stale_hours", 24)),
        )

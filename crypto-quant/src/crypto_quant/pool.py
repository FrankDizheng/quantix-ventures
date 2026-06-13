"""Build a candidate pool of small-cap perps with cost-zone metrics.

Pipeline (no API keys, single exchange for speed):
  1. List active USDT perpetuals on the chosen exchange.
  2. Filter by 24h quote volume range (drop too small / too large).
  3. For each candidate, fetch recent OHLCV.
  4. Compute cost-zone snapshot (VWAP, distance, stage label).
  5. Rank and write CSV.

The pool is **point-in-time**: it captures what the universe looks like
right now. Backtests run on each candidate's full OHLCV history afterwards.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from crypto_quant.data import CCXTFetcher
from crypto_quant.data.ticker_utils import quote_volume_usd
from crypto_quant.scan.perp_scanner import MAJOR_BASES
from crypto_quant.strategy.cost_zone import CostZoneConfig, latest_snapshot


@dataclass
class PoolConfig:
    exchange: str = "binanceusdm"
    timeframe: str = "1h"
    ohlcv_days: int = 21
    min_quote_volume_usd: float = 500_000.0
    max_quote_volume_usd: float = 80_000_000.0
    max_candidates: int = 30
    final_top: int = 10
    sleep_ms: int = 100
    cost_zone: CostZoneConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.cost_zone is None:
            self.cost_zone = CostZoneConfig()


def _list_candidate_symbols(fetcher: CCXTFetcher, cfg: PoolConfig) -> list[tuple[str, float]]:
    """Return [(symbol, 24h_quote_volume), ...] sorted by volume desc."""
    ex = fetcher.exchange
    ex.load_markets()
    candidates: list[str] = []
    for sym, m in ex.markets.items():
        if not m.get("active"):
            continue
        if m.get("quote") != "USDT":
            continue
        if not (m.get("swap") or m.get("linear") or m.get("future")):
            continue
        if m.get("base") in MAJOR_BASES:
            continue
        candidates.append(sym)

    if not ex.has.get("fetchTickers"):
        return [(s, 0.0) for s in candidates[: cfg.max_candidates]]

    tickers: dict[str, Any] | None = None
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            tickers = ex.fetch_tickers()
            break
        except Exception as e:
            last_err = e
            wait = 2 * (attempt + 1)
            print(f"[pool] fetch_tickers attempt {attempt + 1} failed: {e}; retrying in {wait}s")
            time.sleep(wait)
    if tickers is None:
        raise RuntimeError(f"fetch_tickers failed 3 times on {ex.id}") from last_err

    rows: list[tuple[str, float]] = []
    for sym in candidates:
        t = tickers.get(sym)
        if not t:
            continue
        qv = quote_volume_usd(t)
        if qv < cfg.min_quote_volume_usd or qv > cfg.max_quote_volume_usd:
            continue
        rows.append((sym, qv))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows[: cfg.max_candidates]


def build_pool(cfg: PoolConfig | None = None, *, verbose: bool = True) -> pd.DataFrame:
    cfg = cfg or PoolConfig()
    fetcher = CCXTFetcher(cfg.exchange, rate_limit_ms=cfg.sleep_ms)
    pre = _list_candidate_symbols(fetcher, cfg)
    if verbose:
        print(f"[pool] {len(pre)} candidates after liquidity filter")

    since = datetime.now(timezone.utc) - timedelta(days=cfg.ohlcv_days)
    rows: list[dict[str, Any]] = []
    for i, (sym, qv) in enumerate(pre):
        try:
            df = fetcher.fetch_ohlcv(sym, cfg.timeframe, since=since)
        except Exception as e:
            if verbose:
                print(f"[pool] {sym}: fetch error: {e}")
            continue
        if df.empty:
            continue
        snap = latest_snapshot(df, cfg.cost_zone)
        rows.append(
            {
                "symbol": sym,
                "exchange": cfg.exchange,
                "quote_vol_24h_usd": int(qv),
                "bars": len(df),
                "last_close": float(df["close"].iloc[-1]),
                **snap,
            }
        )
        if verbose and (i + 1) % 5 == 0:
            print(f"[pool] processed {i + 1}/{len(pre)}")
        time.sleep(cfg.sleep_ms / 1000.0)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["score"] = _pool_score(df)
    df = df.sort_values("score", ascending=False).head(cfg.final_top).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df


def _pool_score(df: pd.DataFrame) -> pd.Series:
    """Higher = more interesting. Favors stages that fit Ignition,
    penalizes "extended" (already too far above cost)."""
    stage_w = {
        "ignition": 1.0,
        "accumulation": 0.8,
        "unknown": 0.5,
        "distribution": 0.2,
        "extended": 0.1,
        "insufficient_history": 0.0,
    }
    stage_score = df["stage"].map(stage_w).fillna(0.4)
    vol_score = (df["quote_vol_24h_usd"].clip(lower=1).pipe(_log_norm))
    # Prefer "near cost": dist in [-5, +8] is sweet spot; punish far above.
    dist = df["dist_to_cost_pct"].fillna(50)
    near_cost = 1 - (dist.clip(-20, 30).abs().clip(upper=20) / 20.0)
    return (0.5 * stage_score + 0.3 * near_cost + 0.2 * vol_score).round(3)


def _log_norm(s: pd.Series) -> pd.Series:
    import numpy as np
    x = np.log1p(s.astype(float))
    rng = x.max() - x.min()
    if rng <= 0:
        return pd.Series([0.5] * len(s), index=s.index)
    return (x - x.min()) / rng

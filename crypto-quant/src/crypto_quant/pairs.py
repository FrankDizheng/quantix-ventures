"""Pair-research utilities for small-cap perpetuals.

The goal here is not to prove cointegration perfectly. It is to quickly
separate "maybe tradable relative-value relationships" from random pairs
using only cached OHLCV:

  - high overlap and correlation in log prices,
  - stable hedge ratio,
  - mean-reverting spread half-life,
  - enough historical z-score excursions,
  - decent convergence after those excursions.

This module deliberately avoids heavy statistics dependencies so the first
research loop stays easy to run in the existing project.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import pandas as pd


@dataclass
class PairResearchConfig:
    lookback_hours: int = 720
    min_overlap: int = 240
    min_corr: float = 0.55
    z_window: int = 120
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 3.5
    min_trades: int = 3
    min_half_life_hours: float = 4.0
    max_half_life_hours: float = 120.0


@dataclass
class PairStats:
    symbol_a: str
    symbol_b: str
    n_bars: int
    corr: float
    hedge_beta: float
    half_life_hours: float
    latest_z: float
    spread_vol: float
    opportunities: int
    convergence_rate_pct: float
    avg_spread_pnl: float
    score: float

    def as_dict(self) -> dict:
        return {
            "symbol_a": self.symbol_a,
            "symbol_b": self.symbol_b,
            "n_bars": self.n_bars,
            "corr": self.corr,
            "hedge_beta": self.hedge_beta,
            "half_life_hours": self.half_life_hours,
            "latest_z": self.latest_z,
            "spread_vol": self.spread_vol,
            "opportunities": self.opportunities,
            "convergence_rate_pct": self.convergence_rate_pct,
            "avg_spread_pnl": self.avg_spread_pnl,
            "score": self.score,
        }


def _log_close(df: pd.DataFrame, lookback_hours: int) -> pd.Series:
    if "timestamp" not in df.columns or "close" not in df.columns:
        raise ValueError("OHLCV dataframe must include timestamp and close columns")
    out = df[["timestamp", "close"]].copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    out = out.sort_values("timestamp").tail(lookback_hours)
    close = out["close"].astype(float)
    close = close[close > 0]
    out = out.loc[close.index]
    return close.map(math.log).set_axis(out["timestamp"])


def _estimate_beta(log_a: pd.Series, log_b: pd.Series) -> float:
    var_b = float(log_b.var())
    if not math.isfinite(var_b) or var_b <= 0:
        return 1.0
    beta = float(log_a.cov(log_b) / var_b)
    if not math.isfinite(beta) or beta <= 0:
        return 1.0
    return beta


def _estimate_half_life(spread: pd.Series) -> float:
    lagged = spread.shift(1).dropna()
    delta = spread.diff().dropna()
    aligned = pd.concat([lagged.rename("lagged"), delta.rename("delta")], axis=1).dropna()
    if len(aligned) < 20:
        return math.inf
    var_lagged = float(aligned["lagged"].var())
    if not math.isfinite(var_lagged) or var_lagged <= 0:
        return math.inf
    slope = float(aligned["lagged"].cov(aligned["delta"]) / var_lagged)
    if not math.isfinite(slope) or slope >= 0:
        return math.inf
    return float(-math.log(2) / slope)


def _rolling_z(spread: pd.Series, window: int) -> pd.Series:
    mean = spread.rolling(window).mean()
    std = spread.rolling(window).std()
    return ((spread - mean) / std).replace([math.inf, -math.inf], pd.NA)


def _scan_convergence(
    spread: pd.Series,
    z: pd.Series,
    *,
    entry_z: float,
    exit_z: float,
    stop_z: float,
) -> tuple[int, float, float]:
    """Return (completed trades, win-rate %, avg spread-unit pnl)."""
    in_trade = False
    side = 0
    entry_spread = 0.0
    pnls: list[float] = []

    for ts, z_val in z.dropna().items():
        spread_val = float(spread.loc[ts])
        z_float = float(z_val)
        if not in_trade:
            if z_float >= entry_z:
                in_trade = True
                side = -1  # short spread: expect spread to fall
                entry_spread = spread_val
            elif z_float <= -entry_z:
                in_trade = True
                side = 1  # long spread: expect spread to rise
                entry_spread = spread_val
            continue

        should_exit = abs(z_float) <= exit_z or abs(z_float) >= stop_z
        crossed_mean = (side == -1 and z_float <= 0) or (side == 1 and z_float >= 0)
        if should_exit or crossed_mean:
            pnl = side * (spread_val - entry_spread)
            pnls.append(float(pnl))
            in_trade = False
            side = 0

    if not pnls:
        return 0, 0.0, 0.0
    wins = sum(1 for pnl in pnls if pnl > 0)
    return len(pnls), wins / len(pnls) * 100, sum(pnls) / len(pnls)


def evaluate_pair(
    symbol_a: str,
    df_a: pd.DataFrame,
    symbol_b: str,
    df_b: pd.DataFrame,
    cfg: PairResearchConfig | None = None,
) -> PairStats | None:
    cfg = cfg or PairResearchConfig()
    log_a = _log_close(df_a, cfg.lookback_hours).rename("a")
    log_b = _log_close(df_b, cfg.lookback_hours).rename("b")
    aligned = pd.concat([log_a, log_b], axis=1, join="inner").dropna()
    if len(aligned) < cfg.min_overlap:
        return None

    corr = float(aligned["a"].corr(aligned["b"]))
    if not math.isfinite(corr) or corr < cfg.min_corr:
        return None

    beta = _estimate_beta(aligned["a"], aligned["b"])
    spread = aligned["a"] - beta * aligned["b"]
    spread_std = float(spread.std())
    if not math.isfinite(spread_std) or spread_std <= 0:
        return None

    half_life = _estimate_half_life(spread)
    if (
        not math.isfinite(half_life)
        or half_life < cfg.min_half_life_hours
        or half_life > cfg.max_half_life_hours
    ):
        return None

    z = _rolling_z(spread, cfg.z_window)
    opportunities, convergence_rate, avg_spread_pnl = _scan_convergence(
        spread,
        z,
        entry_z=cfg.entry_z,
        exit_z=cfg.exit_z,
        stop_z=cfg.stop_z,
    )
    if opportunities < cfg.min_trades:
        return None

    latest_z = float(z.dropna().iloc[-1]) if not z.dropna().empty else 0.0
    half_life_score = max(0.0, 1.0 - abs(half_life - 24.0) / cfg.max_half_life_hours)
    opportunity_score = min(1.0, opportunities / 10)
    convergence_score = convergence_rate / 100
    score = (
        0.35 * corr
        + 0.25 * convergence_score
        + 0.20 * half_life_score
        + 0.20 * opportunity_score
    )

    return PairStats(
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        n_bars=len(aligned),
        corr=round(corr, 4),
        hedge_beta=round(beta, 4),
        half_life_hours=round(half_life, 2),
        latest_z=round(latest_z, 2),
        spread_vol=round(spread_std, 6),
        opportunities=opportunities,
        convergence_rate_pct=round(convergence_rate, 1),
        avg_spread_pnl=round(avg_spread_pnl, 6),
        score=round(score, 4),
    )


def rank_pairs(
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    cfg: PairResearchConfig | None = None,
) -> pd.DataFrame:
    cfg = cfg or PairResearchConfig()
    rows: list[dict] = []
    for symbol_a, symbol_b in combinations(sorted(ohlcv_by_symbol), 2):
        stats = evaluate_pair(
            symbol_a,
            ohlcv_by_symbol[symbol_a],
            symbol_b,
            ohlcv_by_symbol[symbol_b],
            cfg,
        )
        if stats is not None:
            rows.append(stats.as_dict())
    if not rows:
        return pd.DataFrame(
            columns=[
                "symbol_a",
                "symbol_b",
                "n_bars",
                "corr",
                "hedge_beta",
                "half_life_hours",
                "latest_z",
                "spread_vol",
                "opportunities",
                "convergence_rate_pct",
                "avg_spread_pnl",
                "score",
            ]
        )
    return pd.DataFrame(rows).sort_values(
        ["score", "convergence_rate_pct", "opportunities"],
        ascending=[False, False, False],
    )


def format_pair_report(df: pd.DataFrame, top: int = 10) -> str:
    if df.empty:
        return "No pairs passed the current filters."
    show = df.head(top).copy()
    cols = [
        "symbol_a",
        "symbol_b",
        "score",
        "corr",
        "hedge_beta",
        "half_life_hours",
        "latest_z",
        "opportunities",
        "convergence_rate_pct",
        "avg_spread_pnl",
    ]
    return show[[c for c in cols if c in show.columns]].to_string(index=False)

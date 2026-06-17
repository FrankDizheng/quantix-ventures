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
    rolling_window_hours: int = 240
    min_rolling_corr: float = 0.35
    max_beta_cv: float = 1.0
    max_beta_drift_pct: float = 120.0
    min_convergence_rate_pct: float = 45.0
    min_cost_edge_ratio: float = 1.2
    fee_rate: float = 0.0004
    slippage_pct: float = 0.15
    active_min_score: float = 0.80
    active_min_stability: float = 0.65
    watchlist_min_score: float = 0.65
    funding_enabled: bool = True
    order_book_enabled: bool = True
    order_book_max_stale_minutes: float = 15.0
    min_depth_25bps_usd: float = 10_000.0
    min_liquidity_cost_edge_ratio: float = 1.5
    max_pair_spread_bps: float = 20.0


@dataclass
class PairStats:
    symbol_a: str
    symbol_b: str
    passed: bool
    status: str
    reject_reason: str
    n_bars: int
    corr: float
    return_corr: float
    hedge_beta: float
    half_life_hours: float
    latest_z: float
    spread_vol: float
    rolling_corr_p20: float
    rolling_corr_mean: float
    beta_cv: float
    beta_drift_pct: float
    stability_score: float
    opportunities: int
    convergence_rate_pct: float
    avg_spread_pnl: float
    cost_edge_ratio: float
    funding_observations: int
    funding_corr: float
    mean_abs_funding_diff_pct: float
    p95_abs_funding_diff_pct: float
    latest_funding_diff_pct: float
    half_life_funding_drag_ratio: float
    symbol_a_spread_bps: float
    symbol_b_spread_bps: float
    pair_spread_bps: float
    min_depth_25bps_usd: float
    min_depth_50bps_usd: float
    liquidity_cost_edge_ratio: float
    score: float

    def as_dict(self) -> dict:
        return {
            "symbol_a": self.symbol_a,
            "symbol_b": self.symbol_b,
            "passed": self.passed,
            "status": self.status,
            "reject_reason": self.reject_reason,
            "n_bars": self.n_bars,
            "corr": self.corr,
            "return_corr": self.return_corr,
            "hedge_beta": self.hedge_beta,
            "half_life_hours": self.half_life_hours,
            "latest_z": self.latest_z,
            "spread_vol": self.spread_vol,
            "rolling_corr_p20": self.rolling_corr_p20,
            "rolling_corr_mean": self.rolling_corr_mean,
            "beta_cv": self.beta_cv,
            "beta_drift_pct": self.beta_drift_pct,
            "stability_score": self.stability_score,
            "opportunities": self.opportunities,
            "convergence_rate_pct": self.convergence_rate_pct,
            "avg_spread_pnl": self.avg_spread_pnl,
            "cost_edge_ratio": self.cost_edge_ratio,
            "funding_observations": self.funding_observations,
            "funding_corr": self.funding_corr,
            "mean_abs_funding_diff_pct": self.mean_abs_funding_diff_pct,
            "p95_abs_funding_diff_pct": self.p95_abs_funding_diff_pct,
            "latest_funding_diff_pct": self.latest_funding_diff_pct,
            "half_life_funding_drag_ratio": self.half_life_funding_drag_ratio,
            "symbol_a_spread_bps": self.symbol_a_spread_bps,
            "symbol_b_spread_bps": self.symbol_b_spread_bps,
            "pair_spread_bps": self.pair_spread_bps,
            "min_depth_25bps_usd": self.min_depth_25bps_usd,
            "min_depth_50bps_usd": self.min_depth_50bps_usd,
            "liquidity_cost_edge_ratio": self.liquidity_cost_edge_ratio,
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


def _rolling_beta(log_a: pd.Series, log_b: pd.Series, window: int) -> pd.Series:
    if window < 20:
        window = 20
    var_b = log_b.rolling(window).var()
    beta = log_a.rolling(window).cov(log_b) / var_b
    beta = beta.replace([math.inf, -math.inf], pd.NA).dropna()
    return beta[beta > 0]


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


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _round(value: float, ndigits: int) -> float:
    return round(float(value), ndigits) if _finite(value) else math.nan


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if not _finite(value):
        return lo
    return min(hi, max(lo, float(value)))


def _round_trip_spread_cost(cfg: PairResearchConfig) -> float:
    """Approximate two-leg round-trip cost in log-spread units.

    This is intentionally conservative: two legs, entry and exit, with taker
    fee and slippage paid on each fill.
    """
    per_fill_cost = cfg.fee_rate + cfg.slippage_pct / 100
    return 4 * per_fill_cost


def _funding_series(funding: pd.DataFrame | None, lookback_hours: int) -> pd.Series:
    if funding is None or funding.empty:
        return pd.Series(dtype=float)
    if "timestamp" not in funding.columns or "funding_rate" not in funding.columns:
        return pd.Series(dtype=float)
    out = funding[["timestamp", "funding_rate"]].copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    out = out.sort_values("timestamp")
    if not out.empty:
        cutoff = out["timestamp"].iloc[-1] - pd.Timedelta(hours=lookback_hours)
        out = out[out["timestamp"] >= cutoff]
    rate_pct = pd.to_numeric(out["funding_rate"], errors="coerce") * 100
    return rate_pct.set_axis(out["timestamp"]).dropna()


def _funding_stats(
    funding_a: pd.DataFrame | None,
    funding_b: pd.DataFrame | None,
    cfg: PairResearchConfig,
    *,
    half_life_hours: float = math.nan,
    avg_spread_pnl: float = math.nan,
) -> dict[str, float | int]:
    empty = {
        "funding_observations": 0,
        "funding_corr": math.nan,
        "mean_abs_funding_diff_pct": math.nan,
        "p95_abs_funding_diff_pct": math.nan,
        "latest_funding_diff_pct": math.nan,
        "half_life_funding_drag_ratio": math.nan,
    }
    if not cfg.funding_enabled:
        return empty

    series_a = _funding_series(funding_a, cfg.lookback_hours).rename("a")
    series_b = _funding_series(funding_b, cfg.lookback_hours).rename("b")
    aligned = pd.concat([series_a, series_b], axis=1, join="inner").dropna()
    if aligned.empty:
        return empty

    diff = aligned["a"] - aligned["b"]
    mean_abs_diff = float(diff.abs().mean())
    drag_ratio = math.nan
    if (
        math.isfinite(mean_abs_diff)
        and math.isfinite(half_life_hours)
        and math.isfinite(avg_spread_pnl)
        and avg_spread_pnl > 0
    ):
        estimated_half_life_funding = (mean_abs_diff / 100) * max(1.0, half_life_hours / 8)
        drag_ratio = estimated_half_life_funding / avg_spread_pnl

    return {
        "funding_observations": int(len(aligned)),
        "funding_corr": float(aligned["a"].corr(aligned["b"])) if len(aligned) > 1 else math.nan,
        "mean_abs_funding_diff_pct": mean_abs_diff,
        "p95_abs_funding_diff_pct": float(diff.abs().quantile(0.95)),
        "latest_funding_diff_pct": float(diff.iloc[-1]),
        "half_life_funding_drag_ratio": drag_ratio,
    }


def _latest_liquidity_row(order_book: pd.DataFrame | None) -> dict[str, float]:
    if order_book is None or order_book.empty:
        return {}
    row = order_book.sort_values("timestamp").iloc[-1]
    out: dict[str, float] = {}
    for col in (
        "spread_bps",
        "bid_depth_25bps_usd",
        "ask_depth_25bps_usd",
        "bid_depth_50bps_usd",
        "ask_depth_50bps_usd",
    ):
        value = pd.to_numeric(pd.Series([row.get(col)]), errors="coerce").iloc[0]
        out[col] = float(value) if pd.notna(value) else math.nan
    return out


def _liquidity_stats(
    order_book_a: pd.DataFrame | None,
    order_book_b: pd.DataFrame | None,
    cfg: PairResearchConfig,
    *,
    avg_spread_pnl: float = math.nan,
) -> dict[str, float]:
    empty = {
        "symbol_a_spread_bps": math.nan,
        "symbol_b_spread_bps": math.nan,
        "pair_spread_bps": math.nan,
        "min_depth_25bps_usd": math.nan,
        "min_depth_50bps_usd": math.nan,
        "liquidity_cost_edge_ratio": math.nan,
    }
    if not cfg.order_book_enabled:
        return empty

    a = _latest_liquidity_row(order_book_a)
    b = _latest_liquidity_row(order_book_b)
    if not a or not b:
        return empty

    pair_spread_bps = a["spread_bps"] + b["spread_bps"]
    min_depth_25 = min(
        a["bid_depth_25bps_usd"],
        a["ask_depth_25bps_usd"],
        b["bid_depth_25bps_usd"],
        b["ask_depth_25bps_usd"],
    )
    min_depth_50 = min(
        a["bid_depth_50bps_usd"],
        a["ask_depth_50bps_usd"],
        b["bid_depth_50bps_usd"],
        b["ask_depth_50bps_usd"],
    )
    edge_ratio = math.nan
    if math.isfinite(avg_spread_pnl) and avg_spread_pnl > 0 and math.isfinite(pair_spread_bps):
        round_trip_fee_cost = 4 * cfg.fee_rate
        round_trip_spread_cost = pair_spread_bps / 10_000
        edge_ratio = avg_spread_pnl / (round_trip_fee_cost + round_trip_spread_cost)

    return {
        "symbol_a_spread_bps": a["spread_bps"],
        "symbol_b_spread_bps": b["spread_bps"],
        "pair_spread_bps": pair_spread_bps,
        "min_depth_25bps_usd": min_depth_25,
        "min_depth_50bps_usd": min_depth_50,
        "liquidity_cost_edge_ratio": edge_ratio,
    }


def _empty_stats(
    symbol_a: str,
    symbol_b: str,
    *,
    reason: str,
    n_bars: int = 0,
    corr: float = math.nan,
    return_corr: float = math.nan,
    beta: float = math.nan,
    half_life: float = math.nan,
    latest_z: float = math.nan,
    spread_vol: float = math.nan,
    rolling_corr_p20: float = math.nan,
    rolling_corr_mean: float = math.nan,
    beta_cv: float = math.nan,
    beta_drift_pct: float = math.nan,
    stability_score: float = math.nan,
    opportunities: int = 0,
    convergence_rate: float = math.nan,
    avg_spread_pnl: float = math.nan,
    cost_edge_ratio: float = math.nan,
    funding_stats: dict[str, float | int] | None = None,
    liquidity_stats: dict[str, float] | None = None,
) -> PairStats:
    funding_stats = funding_stats or {}
    liquidity_stats = liquidity_stats or {}
    return PairStats(
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        passed=False,
        status="rejected",
        reject_reason=reason,
        n_bars=n_bars,
        corr=_round(corr, 4),
        return_corr=_round(return_corr, 4),
        hedge_beta=_round(beta, 4),
        half_life_hours=_round(half_life, 2),
        latest_z=_round(latest_z, 2),
        spread_vol=_round(spread_vol, 6),
        rolling_corr_p20=_round(rolling_corr_p20, 4),
        rolling_corr_mean=_round(rolling_corr_mean, 4),
        beta_cv=_round(beta_cv, 4),
        beta_drift_pct=_round(beta_drift_pct, 1),
        stability_score=_round(stability_score, 4),
        opportunities=opportunities,
        convergence_rate_pct=_round(convergence_rate, 1),
        avg_spread_pnl=_round(avg_spread_pnl, 6),
        cost_edge_ratio=_round(cost_edge_ratio, 2),
        funding_observations=int(funding_stats.get("funding_observations", 0)),
        funding_corr=_round(funding_stats.get("funding_corr", math.nan), 4),
        mean_abs_funding_diff_pct=_round(
            funding_stats.get("mean_abs_funding_diff_pct", math.nan), 5
        ),
        p95_abs_funding_diff_pct=_round(
            funding_stats.get("p95_abs_funding_diff_pct", math.nan), 5
        ),
        latest_funding_diff_pct=_round(
            funding_stats.get("latest_funding_diff_pct", math.nan), 5
        ),
        half_life_funding_drag_ratio=_round(
            funding_stats.get("half_life_funding_drag_ratio", math.nan), 4
        ),
        symbol_a_spread_bps=_round(liquidity_stats.get("symbol_a_spread_bps", math.nan), 2),
        symbol_b_spread_bps=_round(liquidity_stats.get("symbol_b_spread_bps", math.nan), 2),
        pair_spread_bps=_round(liquidity_stats.get("pair_spread_bps", math.nan), 2),
        min_depth_25bps_usd=_round(liquidity_stats.get("min_depth_25bps_usd", math.nan), 0),
        min_depth_50bps_usd=_round(liquidity_stats.get("min_depth_50bps_usd", math.nan), 0),
        liquidity_cost_edge_ratio=_round(
            liquidity_stats.get("liquidity_cost_edge_ratio", math.nan), 2
        ),
        score=math.nan,
    )


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


def diagnose_pair(
    symbol_a: str,
    df_a: pd.DataFrame,
    symbol_b: str,
    df_b: pd.DataFrame,
    cfg: PairResearchConfig | None = None,
    *,
    funding_a: pd.DataFrame | None = None,
    funding_b: pd.DataFrame | None = None,
    order_book_a: pd.DataFrame | None = None,
    order_book_b: pd.DataFrame | None = None,
) -> PairStats:
    cfg = cfg or PairResearchConfig()
    log_a = _log_close(df_a, cfg.lookback_hours).rename("a")
    log_b = _log_close(df_b, cfg.lookback_hours).rename("b")
    aligned = pd.concat([log_a, log_b], axis=1, join="inner").dropna()
    if len(aligned) < cfg.min_overlap:
        return _empty_stats(
            symbol_a,
            symbol_b,
            reason="insufficient_overlap",
            n_bars=len(aligned),
        )

    corr = float(aligned["a"].corr(aligned["b"]))
    returns = aligned.diff().dropna()
    return_corr = float(returns["a"].corr(returns["b"])) if len(returns) else math.nan
    if not math.isfinite(corr) or corr < cfg.min_corr:
        return _empty_stats(
            symbol_a,
            symbol_b,
            reason="low_full_sample_corr",
            n_bars=len(aligned),
            corr=corr,
            return_corr=return_corr,
        )

    beta = _estimate_beta(aligned["a"], aligned["b"])
    spread = aligned["a"] - beta * aligned["b"]
    spread_std = float(spread.std())
    if not math.isfinite(spread_std) or spread_std <= 0:
        return _empty_stats(
            symbol_a,
            symbol_b,
            reason="invalid_spread",
            n_bars=len(aligned),
            corr=corr,
            return_corr=return_corr,
            beta=beta,
        )

    roll_window = min(cfg.rolling_window_hours, max(20, len(aligned) // 2))
    rolling_corr = (
        aligned["a"]
        .rolling(roll_window)
        .corr(aligned["b"])
        .replace([math.inf, -math.inf], pd.NA)
        .dropna()
    )
    rolling_corr_p20 = (
        float(rolling_corr.quantile(0.20)) if not rolling_corr.empty else math.nan
    )
    rolling_corr_mean = float(rolling_corr.mean()) if not rolling_corr.empty else math.nan

    beta_series = _rolling_beta(aligned["a"], aligned["b"], roll_window)
    if beta_series.empty:
        return _empty_stats(
            symbol_a,
            symbol_b,
            reason="no_rolling_beta",
            n_bars=len(aligned),
            corr=corr,
            return_corr=return_corr,
            beta=beta,
            spread_vol=spread_std,
            rolling_corr_p20=rolling_corr_p20,
            rolling_corr_mean=rolling_corr_mean,
        )

    beta_mean = float(beta_series.mean())
    beta_cv = float(beta_series.std(ddof=0) / abs(beta_mean)) if beta_mean else math.inf
    beta_median = float(beta_series.median())
    beta_drift_pct = (
        abs(float(beta_series.iloc[-1]) - beta_median) / abs(beta_median) * 100
        if beta_median
        else math.inf
    )
    corr_stability = _clamp(
        (rolling_corr_p20 - cfg.min_rolling_corr) / (1.0 - cfg.min_rolling_corr)
    )
    beta_cv_score = _clamp(1.0 - beta_cv / cfg.max_beta_cv)
    beta_drift_score = _clamp(1.0 - beta_drift_pct / cfg.max_beta_drift_pct)
    stability_score = (
        0.40 * corr_stability
        + 0.35 * beta_cv_score
        + 0.25 * beta_drift_score
    )

    if not math.isfinite(rolling_corr_p20) or rolling_corr_p20 < cfg.min_rolling_corr:
        return _empty_stats(
            symbol_a,
            symbol_b,
            reason="unstable_rolling_corr",
            n_bars=len(aligned),
            corr=corr,
            return_corr=return_corr,
            beta=beta,
            spread_vol=spread_std,
            rolling_corr_p20=rolling_corr_p20,
            rolling_corr_mean=rolling_corr_mean,
            beta_cv=beta_cv,
            beta_drift_pct=beta_drift_pct,
            stability_score=stability_score,
        )

    if beta_cv > cfg.max_beta_cv or beta_drift_pct > cfg.max_beta_drift_pct:
        return _empty_stats(
            symbol_a,
            symbol_b,
            reason="unstable_hedge_beta",
            n_bars=len(aligned),
            corr=corr,
            return_corr=return_corr,
            beta=beta,
            spread_vol=spread_std,
            rolling_corr_p20=rolling_corr_p20,
            rolling_corr_mean=rolling_corr_mean,
            beta_cv=beta_cv,
            beta_drift_pct=beta_drift_pct,
            stability_score=stability_score,
        )

    half_life = _estimate_half_life(spread)
    if (
        not math.isfinite(half_life)
        or half_life < cfg.min_half_life_hours
        or half_life > cfg.max_half_life_hours
    ):
        return _empty_stats(
            symbol_a,
            symbol_b,
            reason="half_life_out_of_range",
            n_bars=len(aligned),
            corr=corr,
            return_corr=return_corr,
            beta=beta,
            half_life=half_life,
            spread_vol=spread_std,
            rolling_corr_p20=rolling_corr_p20,
            rolling_corr_mean=rolling_corr_mean,
            beta_cv=beta_cv,
            beta_drift_pct=beta_drift_pct,
            stability_score=stability_score,
        )

    z = _rolling_z(spread, cfg.z_window)
    opportunities, convergence_rate, avg_spread_pnl = _scan_convergence(
        spread,
        z,
        entry_z=cfg.entry_z,
        exit_z=cfg.exit_z,
        stop_z=cfg.stop_z,
    )
    latest_z = float(z.dropna().iloc[-1]) if not z.dropna().empty else 0.0
    funding_diag = _funding_stats(
        funding_a,
        funding_b,
        cfg,
        half_life_hours=half_life,
        avg_spread_pnl=avg_spread_pnl,
    )
    liquidity_diag = _liquidity_stats(
        order_book_a,
        order_book_b,
        cfg,
        avg_spread_pnl=avg_spread_pnl,
    )
    if opportunities < cfg.min_trades:
        return _empty_stats(
            symbol_a,
            symbol_b,
            reason="insufficient_z_opportunities",
            n_bars=len(aligned),
            corr=corr,
            return_corr=return_corr,
            beta=beta,
            half_life=half_life,
            latest_z=latest_z,
            spread_vol=spread_std,
            rolling_corr_p20=rolling_corr_p20,
            rolling_corr_mean=rolling_corr_mean,
            beta_cv=beta_cv,
            beta_drift_pct=beta_drift_pct,
            stability_score=stability_score,
            opportunities=opportunities,
            convergence_rate=convergence_rate,
            avg_spread_pnl=avg_spread_pnl,
            funding_stats=funding_diag,
            liquidity_stats=liquidity_diag,
        )

    cost_edge_ratio = avg_spread_pnl / _round_trip_spread_cost(cfg)
    has_liquidity = math.isfinite(liquidity_diag.get("pair_spread_bps", math.nan))
    if convergence_rate < cfg.min_convergence_rate_pct:
        reject_reason = "weak_convergence"
    elif cost_edge_ratio < cfg.min_cost_edge_ratio:
        reject_reason = "cost_not_covered"
    elif has_liquidity and liquidity_diag["pair_spread_bps"] > cfg.max_pair_spread_bps:
        reject_reason = "wide_pair_spread"
    elif has_liquidity and liquidity_diag["min_depth_25bps_usd"] < cfg.min_depth_25bps_usd:
        reject_reason = "insufficient_liquidity"
    elif (
        has_liquidity
        and math.isfinite(liquidity_diag["liquidity_cost_edge_ratio"])
        and liquidity_diag["liquidity_cost_edge_ratio"] < cfg.min_liquidity_cost_edge_ratio
    ):
        reject_reason = "liquidity_cost_not_covered"
    else:
        reject_reason = ""
    if reject_reason:
        return _empty_stats(
            symbol_a,
            symbol_b,
            reason=reject_reason,
            n_bars=len(aligned),
            corr=corr,
            return_corr=return_corr,
            beta=beta,
            half_life=half_life,
            latest_z=latest_z,
            spread_vol=spread_std,
            rolling_corr_p20=rolling_corr_p20,
            rolling_corr_mean=rolling_corr_mean,
            beta_cv=beta_cv,
            beta_drift_pct=beta_drift_pct,
            stability_score=stability_score,
            opportunities=opportunities,
            convergence_rate=convergence_rate,
            avg_spread_pnl=avg_spread_pnl,
            cost_edge_ratio=cost_edge_ratio,
            funding_stats=funding_diag,
            liquidity_stats=liquidity_diag,
        )

    half_life_score = max(0.0, 1.0 - abs(half_life - 24.0) / cfg.max_half_life_hours)
    opportunity_score = min(1.0, opportunities / 10)
    convergence_score = convergence_rate / 100
    cost_score = _clamp(cost_edge_ratio / (cfg.min_cost_edge_ratio * 2))
    score = (
        0.25 * corr
        + 0.20 * convergence_score
        + 0.15 * half_life_score
        + 0.15 * opportunity_score
        + 0.20 * stability_score
        + 0.05 * cost_score
    )
    if score >= cfg.active_min_score and stability_score >= cfg.active_min_stability:
        status = "active_research"
    elif score >= cfg.watchlist_min_score:
        status = "watchlist"
    else:
        status = "quarantine"

    return PairStats(
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        passed=True,
        status=status,
        reject_reason="",
        n_bars=len(aligned),
        corr=round(corr, 4),
        return_corr=round(return_corr, 4),
        hedge_beta=round(beta, 4),
        half_life_hours=round(half_life, 2),
        latest_z=round(latest_z, 2),
        spread_vol=round(spread_std, 6),
        rolling_corr_p20=round(rolling_corr_p20, 4),
        rolling_corr_mean=round(rolling_corr_mean, 4),
        beta_cv=round(beta_cv, 4),
        beta_drift_pct=round(beta_drift_pct, 1),
        stability_score=round(stability_score, 4),
        opportunities=opportunities,
        convergence_rate_pct=round(convergence_rate, 1),
        avg_spread_pnl=round(avg_spread_pnl, 6),
        cost_edge_ratio=round(cost_edge_ratio, 2),
        funding_observations=int(funding_diag.get("funding_observations", 0)),
        funding_corr=_round(funding_diag.get("funding_corr", math.nan), 4),
        mean_abs_funding_diff_pct=_round(
            funding_diag.get("mean_abs_funding_diff_pct", math.nan), 5
        ),
        p95_abs_funding_diff_pct=_round(
            funding_diag.get("p95_abs_funding_diff_pct", math.nan), 5
        ),
        latest_funding_diff_pct=_round(
            funding_diag.get("latest_funding_diff_pct", math.nan), 5
        ),
        half_life_funding_drag_ratio=_round(
            funding_diag.get("half_life_funding_drag_ratio", math.nan), 4
        ),
        symbol_a_spread_bps=_round(
            liquidity_diag.get("symbol_a_spread_bps", math.nan), 2
        ),
        symbol_b_spread_bps=_round(
            liquidity_diag.get("symbol_b_spread_bps", math.nan), 2
        ),
        pair_spread_bps=_round(liquidity_diag.get("pair_spread_bps", math.nan), 2),
        min_depth_25bps_usd=_round(
            liquidity_diag.get("min_depth_25bps_usd", math.nan), 0
        ),
        min_depth_50bps_usd=_round(
            liquidity_diag.get("min_depth_50bps_usd", math.nan), 0
        ),
        liquidity_cost_edge_ratio=_round(
            liquidity_diag.get("liquidity_cost_edge_ratio", math.nan), 2
        ),
        score=round(score, 4),
    )


def evaluate_pair(
    symbol_a: str,
    df_a: pd.DataFrame,
    symbol_b: str,
    df_b: pd.DataFrame,
    cfg: PairResearchConfig | None = None,
    *,
    funding_a: pd.DataFrame | None = None,
    funding_b: pd.DataFrame | None = None,
    order_book_a: pd.DataFrame | None = None,
    order_book_b: pd.DataFrame | None = None,
) -> PairStats | None:
    stats = diagnose_pair(
        symbol_a,
        df_a,
        symbol_b,
        df_b,
        cfg,
        funding_a=funding_a,
        funding_b=funding_b,
        order_book_a=order_book_a,
        order_book_b=order_book_b,
    )
    return stats if stats.passed else None


def _pair_columns() -> list[str]:
    return [
        "symbol_a",
        "symbol_b",
        "passed",
        "status",
        "reject_reason",
        "n_bars",
        "corr",
        "return_corr",
        "hedge_beta",
        "half_life_hours",
        "latest_z",
        "spread_vol",
        "rolling_corr_p20",
        "rolling_corr_mean",
        "beta_cv",
        "beta_drift_pct",
        "stability_score",
        "opportunities",
        "convergence_rate_pct",
        "avg_spread_pnl",
        "cost_edge_ratio",
        "funding_observations",
        "funding_corr",
        "mean_abs_funding_diff_pct",
        "p95_abs_funding_diff_pct",
        "latest_funding_diff_pct",
        "half_life_funding_drag_ratio",
        "symbol_a_spread_bps",
        "symbol_b_spread_bps",
        "pair_spread_bps",
        "min_depth_25bps_usd",
        "min_depth_50bps_usd",
        "liquidity_cost_edge_ratio",
        "score",
    ]


def diagnose_pairs(
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    cfg: PairResearchConfig | None = None,
    *,
    candidate_pairs: list[tuple[str, str]] | None = None,
    funding_by_symbol: dict[str, pd.DataFrame] | None = None,
    order_book_by_symbol: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    cfg = cfg or PairResearchConfig()
    if candidate_pairs is None:
        candidate_pairs = list(combinations(sorted(ohlcv_by_symbol), 2))
    rows: list[dict] = []
    for symbol_a, symbol_b in candidate_pairs:
        if symbol_a not in ohlcv_by_symbol or symbol_b not in ohlcv_by_symbol:
            continue
        stats = diagnose_pair(
            symbol_a,
            ohlcv_by_symbol[symbol_a],
            symbol_b,
            ohlcv_by_symbol[symbol_b],
            cfg,
            funding_a=(funding_by_symbol or {}).get(symbol_a),
            funding_b=(funding_by_symbol or {}).get(symbol_b),
            order_book_a=(order_book_by_symbol or {}).get(symbol_a),
            order_book_b=(order_book_by_symbol or {}).get(symbol_b),
        )
        rows.append(stats.as_dict())
    if not rows:
        return pd.DataFrame(columns=_pair_columns())
    return pd.DataFrame(rows).sort_values(
        ["passed", "score", "stability_score", "convergence_rate_pct"],
        ascending=[False, False, False, False],
    )


def rank_pairs(
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    cfg: PairResearchConfig | None = None,
    *,
    candidate_pairs: list[tuple[str, str]] | None = None,
    funding_by_symbol: dict[str, pd.DataFrame] | None = None,
    order_book_by_symbol: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    cfg = cfg or PairResearchConfig()
    diagnostics = diagnose_pairs(
        ohlcv_by_symbol,
        cfg,
        candidate_pairs=candidate_pairs,
        funding_by_symbol=funding_by_symbol,
        order_book_by_symbol=order_book_by_symbol,
    )
    if diagnostics.empty:
        return pd.DataFrame(columns=_pair_columns())
    ranked = diagnostics[diagnostics["passed"]].copy()
    if ranked.empty:
        return pd.DataFrame(columns=_pair_columns())
    return ranked.sort_values(
        ["score", "stability_score", "convergence_rate_pct", "opportunities"],
        ascending=[False, False, False, False],
    )


def format_pair_report(df: pd.DataFrame, top: int = 10) -> str:
    if df.empty:
        return "No pairs passed the current filters."
    show = df.head(top).copy()
    cols = [
        "symbol_a",
        "symbol_b",
        "status",
        "score",
        "stability_score",
        "corr",
        "rolling_corr_p20",
        "hedge_beta",
        "beta_cv",
        "cost_edge_ratio",
        "mean_abs_funding_diff_pct",
        "half_life_funding_drag_ratio",
        "pair_spread_bps",
        "min_depth_25bps_usd",
        "liquidity_cost_edge_ratio",
        "half_life_hours",
        "latest_z",
        "opportunities",
        "convergence_rate_pct",
        "avg_spread_pnl",
    ]
    return show[[c for c in cols if c in show.columns]].to_string(index=False)

"""Cost-zone proxy from OHLCV (no on-chain data needed).

Idea
----
"Whale cost basis" cannot be observed without on-chain or order-book data.
We approximate it on public OHLCV with two simple, robust constructs:

  1. Rolling VWAP — typical-price * volume average over N bars.
     -> Approximation of "where most of the recent volume traded".
  2. Rolling mean ± k*std box — Bollinger-style range over the same window.
     -> Approximation of the trading range / accumulation box.

From these, we derive:
  - dist_to_cost_pct: how far current close is from the rolling VWAP.
    Negative = below cost basis (potential value), positive = extended.
  - stage label: a coarse market-structure classification useful as a
    filter, NOT as a signal on its own.

These are intentionally simple. They are *not* whale tracking; they are
the OHLCV proxy a beginner can compute and reason about.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CostZoneConfig:
    vwap_hours: int = 168          # 7 days on 1h bars
    box_hours: int = 168
    box_k: float = 2.0
    extended_pct: float = 15.0     # close > vwap by this much => "extended"
    accumulation_max_ret: float = 4.0   # |N-h return| below this => quiet
    accumulation_max_dist: float = 3.0  # |dist_to_cost| within this => near cost

    @classmethod
    def from_dict(cls, d: dict | None) -> CostZoneConfig:
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def add_cost_zone(df: pd.DataFrame, cfg: CostZoneConfig | None = None) -> pd.DataFrame:
    """Append cost-zone columns to df. Requires columns: high, low, close, volume."""
    cfg = cfg or CostZoneConfig()
    out = df.copy()

    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    pv = typical * out["volume"]
    rolled_pv = pv.rolling(cfg.vwap_hours, min_periods=cfg.vwap_hours).sum()
    rolled_v = out["volume"].rolling(cfg.vwap_hours, min_periods=cfg.vwap_hours).sum()
    out["vwap"] = rolled_pv / rolled_v
    out["dist_to_cost_pct"] = (out["close"] - out["vwap"]) / out["vwap"] * 100

    box_mean = out["close"].rolling(cfg.box_hours, min_periods=cfg.box_hours).mean()
    box_std = out["close"].rolling(cfg.box_hours, min_periods=cfg.box_hours).std()
    out["box_upper"] = box_mean + cfg.box_k * box_std
    out["box_lower"] = box_mean - cfg.box_k * box_std
    out["box_width_pct"] = (out["box_upper"] - out["box_lower"]) / box_mean * 100

    out["ret_window_pct"] = out["close"].pct_change(cfg.vwap_hours) * 100
    out["stage"] = _classify_stage(out, cfg)
    return out


def _classify_stage(df: pd.DataFrame, cfg: CostZoneConfig) -> pd.Series:
    close = df["close"]
    vwap = df["vwap"]
    bu = df["box_upper"]
    bl = df["box_lower"]
    dist = df["dist_to_cost_pct"]
    ret = df["ret_window_pct"]

    stage = np.full(len(df), "unknown", dtype=object)
    valid = vwap.notna() & bu.notna()
    stage[valid & (close > bu) & (dist > cfg.extended_pct)] = "extended"
    stage[valid & (close > bu) & (dist <= cfg.extended_pct) & (ret > 3)] = "ignition"
    stage[valid & (close < bl) & (ret < -5)] = "distribution"
    stage[
        valid
        & (dist.abs() <= cfg.accumulation_max_dist)
        & (ret.abs() <= cfg.accumulation_max_ret)
    ] = "accumulation"
    return pd.Series(stage, index=df.index, name="stage")


def latest_snapshot(df: pd.DataFrame, cfg: CostZoneConfig | None = None) -> dict:
    """Return cost-zone metrics for the *most recent* bar (used by build-pool)."""
    cfg = cfg or CostZoneConfig()
    if len(df) < cfg.vwap_hours + 1:
        return {
            "stage": "insufficient_history",
            "vwap": None,
            "dist_to_cost_pct": None,
            "box_width_pct": None,
            "ret_window_pct": None,
        }
    enriched = add_cost_zone(df, cfg)
    last = enriched.iloc[-1]
    return {
        "stage": str(last["stage"]),
        "vwap": float(last["vwap"]) if pd.notna(last["vwap"]) else None,
        "dist_to_cost_pct": (
            float(last["dist_to_cost_pct"]) if pd.notna(last["dist_to_cost_pct"]) else None
        ),
        "box_width_pct": (
            float(last["box_width_pct"]) if pd.notna(last["box_width_pct"]) else None
        ),
        "ret_window_pct": (
            float(last["ret_window_pct"]) if pd.notna(last["ret_window_pct"]) else None
        ),
    }

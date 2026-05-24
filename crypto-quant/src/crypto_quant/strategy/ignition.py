"""Ignition v3 — small-cap trend-following with breakout entry and trailing exit.

Why this design (vs v2's % return + fixed TP/SL)
-------------------------------------------------
v2 used "24h return between 5%-18%" + "fixed +12% TP / -6% SL".
On a small-cap pool of 8 symbols over 90 days it lost an average of
12% vs buy-and-hold. The autopsy:
  - Most signals fail (small caps fake out often).
  - Few signals trend hard. Fixed +12% TP capped those rare big winners.
  - Net expectancy was poor because winners were truncated.

Trend-following 101 says: in low-win-rate, fat-tailed regimes you must
"let winners run". This v3 implements the textbook recipe:

  Entry:
    - Donchian breakout: close > rolling N-bar high (excludes current bar)
      "ignition" interpreted as a real structural break, not just a %.
    - Volume confirmation (current vol > k * prior MA).
    - HTF trend filter (close > long SMA).
    - Cost-zone filter (close not too far above rolling VWAP).
    - Cooldown after exit.

  Exit:
    - Initial stop: ATR-based (entry - k * ATR).
    - Trailing stop: highest_close_since_entry * (1 - trail_pct).
    - Time stop: long backstop (e.g. 7 days) so dead trades don't tie up capital.
    - NO fixed take-profit — trail captures the trend's actual length.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from crypto_quant.onchain.netflow import (
    DuneFilterConfig,
    apply_dune_entry_filter,
    merge_netflow_to_ohlcv,
)
from crypto_quant.strategy.cost_zone import CostZoneConfig, add_cost_zone


@dataclass
class IgnitionConfig:
    # --- entry ---
    breakout_hours: int = 48           # close > N-bar high (Donchian)
    vol_ma_hours: int = 20
    vol_mult: float = 1.5
    htf_trend_hours: int = 96          # 4-day SMA filter; 0 disables
    vwap_hours: int = 168              # 7-day VWAP for cost-zone filter
    max_dist_to_cost_pct: float = 12.0 # 0 disables
    cooldown_bars: int = 12

    # --- risk / exit ---
    atr_hours: int = 24                # ATR window
    atr_stop_mult: float = 2.5         # initial stop: entry - mult * ATR
    trail_pct: float = 8.0             # trail stop: highest_close * (1 - x%)
    max_hold_hours: int = 168          # 7d time-stop backstop

    # --- legacy / unused (kept for backward-compat with older configs) ---
    ret_hours: int = 24
    ret_min_pct: float = 0.0
    ret_max_pct: float = 0.0
    trend_ma_hours: int = 0
    stop_loss_pct: float = 0.0         # ignored if atr_stop_mult > 0
    take_profit_pct: float = 0.0       # NOT used in v3 — trail replaces TP

    @classmethod
    def from_dict(cls, d: dict) -> IgnitionConfig:
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    """Wilder/SMA-style true range average. Returns ATR in price units."""
    h, l, c = df["high"], df["low"], df["close"]
    prev_close = c.shift(1)
    tr = pd.concat(
        [
            (h - l).abs(),
            (h - prev_close).abs(),
            (l - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def add_indicators(df: pd.DataFrame, cfg: IgnitionConfig) -> pd.DataFrame:
    out = df.copy()
    out["vol_ma"] = out["volume"].rolling(cfg.vol_ma_hours).mean().shift(1)
    # Donchian breakout level: highest close of prior N bars (exclude current).
    out["breakout_level"] = (
        out["close"].rolling(cfg.breakout_hours).max().shift(1)
    )
    out["atr"] = _atr(out, cfg.atr_hours)
    out["atr_pct"] = out["atr"] / out["close"] * 100

    if cfg.htf_trend_hours and cfg.htf_trend_hours > 0:
        out["htf_sma"] = out["close"].rolling(cfg.htf_trend_hours).mean()

    if cfg.max_dist_to_cost_pct > 0 and cfg.vwap_hours > 0:
        cz = add_cost_zone(out, CostZoneConfig(vwap_hours=cfg.vwap_hours))
        out["vwap"] = cz["vwap"]
        out["dist_to_cost_pct"] = cz["dist_to_cost_pct"]
    return out


class IgnitionStrategy:
    def __init__(
        self,
        cfg: IgnitionConfig | None = None,
        dune_cfg: DuneFilterConfig | None = None,
    ) -> None:
        self.cfg = cfg or IgnitionConfig()
        self.dune_cfg = dune_cfg

    def generate_signals(
        self,
        df: pd.DataFrame,
        *,
        netflow_daily: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        cfg = self.cfg
        min_bars = (
            max(
                cfg.breakout_hours,
                cfg.vol_ma_hours,
                cfg.atr_hours,
                cfg.htf_trend_hours or 0,
                cfg.vwap_hours if cfg.max_dist_to_cost_pct > 0 else 0,
            )
            + 2
        )
        if len(df) < min_bars:
            out = df.copy()
            out["entry_signal"] = False
            out["entry_trigger"] = False
            return out

        x = add_indicators(df, cfg)
        if self.dune_cfg and self.dune_cfg.enabled:
            x = merge_netflow_to_ohlcv(
                x,
                netflow_daily,
                rolling_days=self.dune_cfg.rolling_days,
            )
        cond = (
            (x["close"] > x["breakout_level"])
            & (x["volume"] > x["vol_ma"] * cfg.vol_mult)
            & x["breakout_level"].notna()
            & x["vol_ma"].notna()
            & x["atr"].notna()
        )
        if "htf_sma" in x.columns:
            cond = cond & (x["close"] > x["htf_sma"]) & x["htf_sma"].notna()
        if "dist_to_cost_pct" in x.columns and cfg.max_dist_to_cost_pct > 0:
            cond = (
                cond
                & x["dist_to_cost_pct"].notna()
                & (x["dist_to_cost_pct"] <= cfg.max_dist_to_cost_pct)
            )
        if self.dune_cfg and self.dune_cfg.enabled:
            cond = apply_dune_entry_filter(cond, x, self.dune_cfg)

        x["entry_signal"] = cond.fillna(False).astype(bool)
        x["entry_trigger"] = x["entry_signal"] & ~x["entry_signal"].shift(
            1, fill_value=False
        )
        return x

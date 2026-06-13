"""FundingCarry v1 — short perpetuals when funding is crowded-long.

Edge thesis (structural, not predictive)
----------------------------------------
On crypto perpetuals, funding rate is paid every 8 hours from the
"crowded" side to the other. In a bull/euphoric market, longs are
crowded and pay shorts. Typical rate: 0.01% per 8h (≈ 11%/yr).
At extremes (memes, new listings, narrative spikes) it can hit
0.1-0.5% per 8h (≈ 100-500% annualized) — for short stretches.

This strategy systematically takes the OTHER SIDE:
  When funding rate > entry_threshold, SHORT the perp.
  Hold until funding normalizes (< exit_threshold) or time-out.

Why it can have real edge (where Ignition/MR don't):
  - Edge source is a structural transfer, not a price prediction.
  - You get PAID to hold the position (when right about funding).
  - It is capacity-limited (you move the funding rate yourself at size)
    but extremely capacity-rich for retail-scale capital.

Naked risk (this version)
-------------------------
Naked short is exposed to upside risk. Funding earned must exceed
adverse price moves. We mitigate with:
  - ATR-based stop loss (cap the loss)
  - Time stop (don't hold indefinitely)
  - Position-fraction sizing

A FULL solution would hedge with spot (cash-and-carry: long spot +
short perp = market neutral, collect funding pure). That requires
spot-data infra and lives in a later sprint.

Entry (SHORT) requires:
  - funding_rate_per_8h_pct > entry_threshold (e.g. > 0.03%)
  - (optional) cooldown bars elapsed since last exit

Exit:
  - exit_trigger fires when funding_rate < exit_threshold (e.g. < 0.01%)
  - Initial stop: entry + atr_stop_mult * ATR (cap upside risk)
  - Time stop: max_hold_hours (e.g. 3 days)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from crypto_quant.strategy.ignition import _atr


@dataclass
class FundingCarryConfig:
    # --- entry / exit thresholds ---
    entry_funding_threshold_pct: float = 0.03  # short when funding > +threshold
    exit_funding_threshold_pct: float = 0.005   # exit when |funding| < this
    cooldown_bars: int = 8
    # Symmetric mode: also go LONG when funding < -threshold (longs receive).
    # Default off so old behavior (short-only) is preserved.
    symmetric: bool = False

    # --- risk / exit (mostly capping the downside on a naked carry trade) ---
    atr_hours: int = 24
    atr_stop_mult: float = 4.0          # WIDE — carry trades need room
    trail_pct: float = 0.0              # no trail (we don't predict price)
    take_profit_pct: float = 0.0        # no TP (we want to keep collecting)
    max_hold_hours: int = 72            # 3-day backstop

    # Engine-required fallback.
    stop_loss_pct: float = 0.0

    @classmethod
    def from_dict(cls, d: dict | None) -> FundingCarryConfig:
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def add_indicators(df: pd.DataFrame, cfg: FundingCarryConfig) -> pd.DataFrame:
    """Funding carry only needs ATR (for stop) — most logic is on funding."""
    out = df.copy()
    out["atr"] = _atr(out, cfg.atr_hours)
    return out


def merge_funding_to_ohlcv(
    ohlcv: pd.DataFrame,
    funding: pd.DataFrame,
) -> pd.DataFrame:
    """Forward-fill 8h funding onto hourly OHLCV.

    Adds column `funding_rate_per_8h_pct` (in PERCENT — matches engine's
    constant unit). Bars before the first funding observation get NaN.
    """
    out = ohlcv.copy()
    if funding is None or funding.empty:
        out["funding_rate_per_8h_pct"] = 0.0
        return out
    f = funding.copy().sort_values("timestamp")
    # CCXT funding_rate is in decimal (0.0001 = 0.01%); convert to percent.
    f["funding_rate_per_8h_pct"] = f["funding_rate"].astype(float) * 100
    f = f[["timestamp", "funding_rate_per_8h_pct"]]
    out = pd.merge_asof(
        out.sort_values("timestamp"),
        f,
        on="timestamp",
        direction="backward",
    )
    out["funding_rate_per_8h_pct"] = out["funding_rate_per_8h_pct"].fillna(0.0)
    return out


class FundingCarryStrategy:
    """Short the perp when funding is crowded-long; exit when it normalizes."""

    def __init__(self, cfg: FundingCarryConfig | None = None) -> None:
        self.cfg = cfg or FundingCarryConfig()

    def generate_signals(
        self,
        df: pd.DataFrame,
        *,
        funding: pd.DataFrame | None = None,
        netflow_daily=None,   # accepted for interface compat
        btc_trend=None,
    ) -> pd.DataFrame:
        cfg = self.cfg
        if "funding_rate_per_8h_pct" not in df.columns:
            if funding is None:
                raise ValueError(
                    "FundingCarryStrategy requires either df['funding_rate_per_8h_pct'] "
                    "or `funding` kwarg (DataFrame with timestamp + funding_rate)"
                )
            df = merge_funding_to_ohlcv(df, funding)
        x = add_indicators(df, cfg)
        rate = x["funding_rate_per_8h_pct"]
        # Determine direction per bar:
        #   funding > +threshold  -> SHORT (collect from crowded longs)
        #   funding < -threshold  -> LONG  (collect from crowded shorts, sym mode)
        short_cond = rate > cfg.entry_funding_threshold_pct
        if cfg.symmetric:
            long_cond = rate < -cfg.entry_funding_threshold_pct
        else:
            long_cond = pd.Series(False, index=x.index)
        entry_cond = (short_cond | long_cond) & x["atr"].notna()
        x["entry_signal"] = entry_cond.fillna(False).astype(bool)
        x["entry_trigger"] = x["entry_signal"] & ~x["entry_signal"].shift(
            1, fill_value=False
        )
        # Exit: |funding| is back inside the neutral band.
        x["exit_trigger"] = (rate.abs() < cfg.exit_funding_threshold_pct).fillna(False)
        # Direction column: short when funding is positive, long when negative.
        x["direction"] = "short"
        if cfg.symmetric:
            x.loc[long_cond, "direction"] = "long"
        return x

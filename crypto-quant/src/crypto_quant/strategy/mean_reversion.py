"""MeanReversion v1 — fade extreme small-cap pumps with a short.

Hypothesis (opposite of Ignition)
---------------------------------
Small-cap perpetuals frequently spike vertically (rumor, listing, narrative
rotation, liquidation cascade) and then partially revert within 1-3 days.
Ignition tries to BUY those breakouts; MeanReversion tries to SHORT them.

If neither has positive OOS edge on the same coins, the universe simply
isn't tradable directionally and we should pivot to non-directional plays
(funding carry, basis). If one has edge, we have something to build on.

Entry (SHORT) requires ALL:
  - 24h return >= pump_min_ret_pct      (e.g. +25%)
  - close > rolling 7d VWAP by >= extension_pct (e.g. +15%)
  - volume > vol_mult * vol_ma          (attention spike)
  - RSI(rsi_hours) > rsi_threshold      (overextension marker)
  - cooldown bars elapsed since last exit
  - optional: BTC NOT in strong uptrend (we don't fade BTC rips)

Exit:
  - Initial stop: entry + atr_stop_mult * ATR (above entry)
  - Trailing stop: lowest_close * (1 + trail_pct/100) — captures the dip
  - Take profit: entry - tp_pct% (mean-rev has a TARGET, unlike trend)
  - Time stop: max_hold_hours (mean rev fails fast or works fast)

Design notes
------------
We DO use a take-profit here, unlike Ignition. Mean reversion has a
target ("price returns to VWAP"); letting winners run forever is wrong
because once we hit reversion, the next move is often resumption of trend.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from crypto_quant.strategy.cost_zone import CostZoneConfig, add_cost_zone
from crypto_quant.strategy.ignition import _atr


@dataclass
class MeanReversionConfig:
    # --- entry ---
    pump_lookback_hours: int = 24      # how far back we measure "pump"
    pump_min_ret_pct: float = 25.0     # min return over lookback to qualify
    vwap_hours: int = 168              # 7d VWAP for extension reference
    extension_pct: float = 15.0        # close must exceed VWAP by this
    vol_ma_hours: int = 20
    vol_mult: float = 1.5              # vol spike multiplier
    rsi_hours: int = 14
    rsi_threshold: float = 75.0        # overextension
    cooldown_bars: int = 12

    # --- risk / exit ---
    atr_hours: int = 24
    atr_stop_mult: float = 2.0         # stop = entry + 2*ATR  (above)
    trail_pct: float = 8.0             # trail = lowest_close * (1 + 8%)
    take_profit_pct: float = 8.0       # exit at -8% from entry
    max_hold_hours: int = 48           # 2d backstop (pumps resolve fast)

    # Engine-required fallback (rarely used because atr_stop_mult > 0).
    stop_loss_pct: float = 0.0

    @classmethod
    def from_dict(cls, d: dict | None) -> MeanReversionConfig:
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder RSI. Standard implementation."""
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / n, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / n, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def add_indicators(df: pd.DataFrame, cfg: MeanReversionConfig) -> pd.DataFrame:
    out = df.copy()
    out["vol_ma"] = out["volume"].rolling(cfg.vol_ma_hours).mean().shift(1)
    out["atr"] = _atr(out, cfg.atr_hours)
    out["atr_pct"] = out["atr"] / out["close"] * 100
    out["pump_ret"] = out["close"].pct_change(cfg.pump_lookback_hours) * 100
    out["rsi"] = _rsi(out["close"], cfg.rsi_hours)
    cz = add_cost_zone(out, CostZoneConfig(vwap_hours=cfg.vwap_hours))
    out["vwap"] = cz["vwap"]
    out["dist_to_cost_pct"] = cz["dist_to_cost_pct"]
    return out


class MeanReversionStrategy:
    """Short on extreme pumps; exit via stop / trail / TP / time."""

    def __init__(self, cfg: MeanReversionConfig | None = None) -> None:
        self.cfg = cfg or MeanReversionConfig()

    def generate_signals(
        self,
        df: pd.DataFrame,
        *,
        netflow_daily=None,  # unused; accepted for interface compat
        btc_trend=None,      # unused; accepted for interface compat
    ) -> pd.DataFrame:
        cfg = self.cfg
        min_bars = (
            max(
                cfg.pump_lookback_hours,
                cfg.vwap_hours,
                cfg.vol_ma_hours,
                cfg.atr_hours,
                cfg.rsi_hours,
            )
            + 2
        )
        if len(df) < min_bars:
            out = df.copy()
            out["entry_signal"] = False
            out["entry_trigger"] = False
            out["direction"] = "short"
            return out

        x = add_indicators(df, cfg)
        cond = (
            (x["pump_ret"] >= cfg.pump_min_ret_pct)
            & (x["dist_to_cost_pct"] >= cfg.extension_pct)
            & (x["volume"] > x["vol_ma"] * cfg.vol_mult)
            & (x["rsi"] >= cfg.rsi_threshold)
            & x["vwap"].notna()
            & x["vol_ma"].notna()
            & x["atr"].notna()
            & x["rsi"].notna()
            & x["pump_ret"].notna()
        )

        x["entry_signal"] = cond.fillna(False).astype(bool)
        x["entry_trigger"] = x["entry_signal"] & ~x["entry_signal"].shift(
            1, fill_value=False
        )
        x["direction"] = "short"
        return x

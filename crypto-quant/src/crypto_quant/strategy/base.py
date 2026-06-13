"""Base strategy interface so the engine can run any signal generator.

Why a Protocol (not ABC):
  - Strategies are simple: a class with `generate_signals(df) -> df`.
  - We want IgnitionConfig / MeanReversionConfig to stay independent
    dataclasses (no shared inheritance pulling in unused fields).
  - The engine only needs DUCK-TYPED attributes:
        max_hold_hours, cooldown_bars, atr_stop_mult, trail_pct,
        stop_loss_pct (fallback), take_profit_pct (optional).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class Strategy(Protocol):
    """Minimal contract: take OHLCV, return df with entry_trigger + atr."""

    cfg: object  # strategy config; must expose engine-required fields

    def generate_signals(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        ...


def required_engine_fields() -> tuple[str, ...]:
    """Fields a strategy config must expose for the engine."""
    return (
        "max_hold_hours",
        "cooldown_bars",
        "atr_stop_mult",
        "trail_pct",
        "stop_loss_pct",
    )

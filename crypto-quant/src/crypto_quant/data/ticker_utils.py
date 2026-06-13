"""Exchange ticker helpers — normalize 24h quote volume across CCXT quirks."""

from __future__ import annotations

from typing import Any


def quote_volume_usd(ticker: dict[str, Any] | None) -> float:
    """Best-effort 24h quote volume in USD from a CCXT ticker dict."""
    if not ticker:
        return 0.0

    qv = ticker.get("quoteVolume")
    if qv is not None:
        val = float(qv)
        if val > 0:
            return val

    last = _price(ticker)
    base = float(ticker.get("baseVolume") or 0)
    if last > 0 and base > 0:
        est = base * last
        # OKX contract volume * price is often tiny; prefer info fields below.
        if est >= 50_000:
            return est

    info = ticker.get("info") or {}
    # OKX swap: volCcy24h is base-token amount; multiply by last for USD notional.
    vol_ccy = info.get("volCcy24h") or info.get("volCcy")
    if vol_ccy is not None and last > 0:
        return float(vol_ccy) * last

    # Gate / others sometimes expose quote turnover directly.
    for key in ("turnover24h", "quote_volume", "quoteVolume"):
        if info.get(key) is not None:
            val = float(info[key])
            if val > 0:
                return val

    return 0.0


def _price(ticker: dict[str, Any]) -> float:
    for key in ("last", "close", "markPrice", "indexPrice"):
        val = ticker.get(key)
        if val is not None:
            return float(val)
    return 0.0

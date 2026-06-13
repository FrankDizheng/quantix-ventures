"""Multi-exchange small-cap USDT perpetual scanner (exchange data only).

Mimics the *shape* of whale-follow dashboards (rank, stage, signals) using
public ticker + funding data. On-chain / paid APIs are a later layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import ccxt
import pandas as pd

from crypto_quant.data.ticker_utils import quote_volume_usd

# Large caps — excluded from "small coin" universe
MAJOR_BASES = frozenset(
    {
        "BTC",
        "ETH",
        "BNB",
        "SOL",
        "XRP",
        "ADA",
        "DOGE",
        "DOT",
        "AVAX",
        "LINK",
        "MATIC",
        "POL",
        "LTC",
        "BCH",
        "TRX",
        "TON",
        "ATOM",
        "NEAR",
        "APT",
        "OP",
        "ARB",
        "SUI",
        "FIL",
        "ICP",
        "ETC",
        "XLM",
        "UNI",
        "AAVE",
        "INJ",
        "STX",
        "IMX",
        "HBAR",
        "VET",
        "MKR",
        "RENDER",
        "RNDR",
        "TAO",
        "WLD",
        "PEPE",
        "SHIB",
        "1000PEPE",
        "1000SHIB",
    }
)

DEFAULT_EXCHANGES = ("binanceusdm", "bybit", "okx", "gate", "bitget")


@dataclass
class ScanConfig:
    min_quote_volume_usd: float = 500_000.0
    max_quote_volume_usd: float = 80_000_000.0
    max_funding_rate: float = 0.0008  # 0.08% per interval, rough crowded-long filter
    top_n: int = 20


class PerpScanner:
    def __init__(self, exchange_id: str) -> None:
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Unknown CCXT exchange: {exchange_id}")
        klass = getattr(ccxt, exchange_id)
        opts: dict[str, Any] = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
        self.exchange: ccxt.Exchange = klass(opts)
        self.exchange_id = exchange_id

    def list_small_cap_perps(self) -> list[str]:
        markets = self.exchange.load_markets()
        out: list[str] = []
        for sym, m in markets.items():
            if not m.get("active"):
                continue
            if m.get("quote") != "USDT":
                continue
            if not (m.get("swap") or m.get("linear") or m.get("future")):
                continue
            base = m.get("base", "")
            if base in MAJOR_BASES:
                continue
            out.append(sym)
        return sorted(out)

    def snapshot(self, symbols: list[str], cfg: ScanConfig) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()

        tickers = self._fetch_tickers(symbols)
        funding = self._fetch_funding_map(symbols)

        rows: list[dict[str, Any]] = []
        for sym in symbols:
            t = tickers.get(sym)
            if not t:
                continue
            qv = quote_volume_usd(t)
            if qv < cfg.min_quote_volume_usd or qv > cfg.max_quote_volume_usd:
                continue
            pct = t.get("percentage")
            if pct is None and t.get("open") and t.get("last"):
                pct = (float(t["last"]) - float(t["open"])) / float(t["open"]) * 100
            fr = funding.get(sym)
            stage, signals, filters = _classify(
                pct_change=float(pct or 0),
                funding_rate=fr,
                quote_volume=qv,
                cfg=cfg,
            )
            rows.append(
                {
                    "exchange": self.exchange_id,
                    "symbol": sym,
                    "state": "watchlist",
                    "stage": stage,
                    "chg_24h_pct": round(float(pct or 0), 2),
                    "quote_vol_usd": int(qv),
                    "funding_rate": fr,
                    "signals": signals,
                    "filters": filters,
                }
            )

        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["score"] = _rank_score(df)
        return df.sort_values("score", ascending=False).head(cfg.top_n).reset_index(drop=True)

    def _fetch_tickers(self, symbols: list[str]) -> dict[str, Any]:
        sym_set = set(symbols)
        if self.exchange.has.get("fetchTickers"):
            try:
                all_tickers = self.exchange.fetch_tickers()
                return {k: v for k, v in all_tickers.items() if k in sym_set}
            except Exception:
                pass
        out: dict[str, Any] = {}
        for sym in symbols[:100]:
            try:
                out[sym] = self.exchange.fetch_ticker(sym)
            except Exception:
                continue
            time.sleep(self.exchange.rateLimit / 1000.0 if self.exchange.rateLimit else 0.05)
        return out

    def _fetch_funding_map(self, symbols: list[str]) -> dict[str, float | None]:
        out: dict[str, float | None] = {s: None for s in symbols}
        if self.exchange.has.get("fetchFundingRates"):
            try:
                rates = self.exchange.fetch_funding_rates(symbols)
                for sym, r in rates.items():
                    out[sym] = _extract_funding(r)
                return out
            except Exception:
                pass
        if not self.exchange.has.get("fetchFundingRate"):
            return out
        for sym in symbols[:80]:  # cap per-exchange calls
            try:
                r = self.exchange.fetch_funding_rate(sym)
                out[sym] = _extract_funding(r)
            except Exception:
                continue
        return out


def scan_exchanges(
    exchange_ids: list[str] | None = None,
    *,
    cfg: ScanConfig | None = None,
) -> pd.DataFrame:
    cfg = cfg or ScanConfig()
    frames: list[pd.DataFrame] = []
    for ex_id in exchange_ids or list(DEFAULT_EXCHANGES):
        try:
            scanner = PerpScanner(ex_id)
            symbols = scanner.list_small_cap_perps()
            df = scanner.snapshot(symbols, cfg)
            if not df.empty:
                frames.append(df)
        except Exception as e:
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "exchange": ex_id,
                            "symbol": "ERROR",
                            "state": str(e)[:80],
                            "stage": "unknown",
                            "score": -1,
                        }
                    ]
                )
            )
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("score", ascending=False).reset_index(drop=True)
    combined.insert(0, "rank", range(1, len(combined) + 1))
    return combined


def _extract_funding(record: dict[str, Any] | None) -> float | None:
    if not record:
        return None
    for key in ("fundingRate", "funding_rate", "rate"):
        if record.get(key) is not None:
            return float(record[key])
    return None


def _classify(
    *,
    pct_change: float,
    funding_rate: float | None,
    quote_volume: float,
    cfg: ScanConfig,
) -> tuple[str, str, str]:
    signals: list[str] = []
    filters: list[str] = []

    if quote_volume < cfg.min_quote_volume_usd * 2:
        filters.append("tradability_below_min")
    else:
        signals.append("tradability_adequate")

    if funding_rate is not None and funding_rate > cfg.max_funding_rate:
        filters.append("funding_crowded_long")
    elif funding_rate is not None and funding_rate < -cfg.max_funding_rate:
        signals.append("funding_crowded_short")

    if pct_change > 8:
        stage = "ignition"
        signals.append("stage_ignition")
        if pct_change < 25:
            signals.append("ignition_building")
    elif pct_change < -5:
        stage = "distribution"
        signals.append("distribution_risk")
    elif abs(pct_change) < 3:
        stage = "accumulation"
        signals.append("strong_accumulation")
    else:
        stage = "unknown"

    if stage == "accumulation" and (funding_rate or 0) <= 0:
        signals.append("low_distribution_risk")

    return stage, ",".join(signals[:3]), ",".join(filters[:2])


def _rank_score(df: pd.DataFrame) -> pd.Series:
    """Higher = more interesting for beginners' watchlist (not investment advice)."""
    vol_score = (df["quote_vol_usd"] / df["quote_vol_usd"].max()).fillna(0)
    move = df["chg_24h_pct"].abs().clip(0, 30) / 30.0
    stage_bonus = df["stage"].map(
        {"ignition": 1.0, "accumulation": 0.7, "unknown": 0.4, "distribution": 0.2}
    ).fillna(0.3)
    fr = df["funding_rate"].fillna(0).abs().clip(0, 0.002) / 0.002
    return (0.35 * vol_score + 0.35 * move + 0.2 * stage_bonus + 0.1 * fr).round(3)


def format_scan_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(no symbols matched filters)"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = f"[MULTI-EXCHANGE] Small-Cap Perpetuals — {ts}\n"
    cols = [
        "rank",
        "exchange",
        "symbol",
        "state",
        "stage",
        "chg_24h_pct",
        "quote_vol_usd",
        "funding_rate",
        "signals",
        "filters",
    ]
    view = df[[c for c in cols if c in df.columns]]
    return title + view.to_string(index=False, max_colwidth=28)

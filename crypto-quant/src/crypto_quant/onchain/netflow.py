"""Dune on-chain features: daily CEX net flow merged onto OHLCV bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from crypto_quant.config import load_config, repo_root
from crypto_quant.data import DuneClient, save_dataframe
from crypto_quant.data.storage import load_dataframe


@dataclass
class DuneFilterConfig:
    enabled: bool = True
    lookback_days: int = 90
    rolling_days: int = 7
    max_rolling_net_inflow_usd: float = 2_000_000.0
    require_net_outflow: bool = False
    skip_if_missing: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> DuneFilterConfig:
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def symbol_base(perp_symbol: str) -> str:
    """WIF/USDT:USDT -> WIF"""
    return perp_symbol.split("/")[0].split(":")[0].upper()


def load_token_map(path: Path | None = None) -> dict[str, dict[str, str]]:
    p = path or repo_root() / "config" / "token_map.yaml"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return {str(k).upper(): v for k, v in raw.items() if isinstance(v, dict)}


def netflow_cache_path(
    out_root: Path,
    chain: str,
    address: str,
    days: int,
) -> Path:
    safe = address.replace("/", "_")[:64]
    return out_root / "onchain" / chain / f"{safe}_netflow_{days}d.parquet"


def _token_filter_clause(
    chain: str,
    address: str,
    *,
    token_symbol: str | None = None,
) -> str:
    """Build a WHERE fragment for cex.flows token matching."""
    if token_symbol:
        safe = token_symbol.replace("'", "''")
        return f"token_symbol = '{safe}'"
    if chain == "solana":
        safe = address.replace("'", "''")
        return f"cast(token_address as varchar) = '{safe}'"
    addr = address if address.startswith("0x") else f"0x{address}"
    safe = addr.replace("'", "''")
    return f"lower(cast(token_address as varchar)) = lower('{safe}')"


def _render_sql(
    chain: str,
    address: str,
    days: int,
    *,
    token_symbol: str | None = None,
) -> str:
    sql_path = repo_root() / "queries" / "dune" / "exchange_netflow_daily.sql"
    tpl = DuneClient.load_sql(sql_path)
    token_filter = _token_filter_clause(
        chain, address, token_symbol=token_symbol
    )
    return (
        tpl.replace("{{CHAIN}}", chain)
        .replace("{{TOKEN_FILTER}}", token_filter)
        .replace("{{DAYS}}", str(int(days)))
    )


def fetch_netflow_daily(
    chain: str,
    address: str,
    days: int,
    *,
    token_symbol: str | None = None,
) -> pd.DataFrame:
    sql = _render_sql(chain, address, days, token_symbol=token_symbol)
    with DuneClient() as client:
        df = client.execute_sql(sql)
    if df.empty:
        return pd.DataFrame(
            columns=["day", "inflow_usd", "outflow_usd", "net_inflow_usd"]
        )
    df["day"] = pd.to_datetime(df["day"], utc=True).dt.normalize()
    for col in ("inflow_usd", "outflow_usd", "net_inflow_usd"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df.sort_values("day").reset_index(drop=True)


def ensure_netflow_for_symbol(
    symbol: str,
    out_root: Path,
    *,
    days: int,
    use_cache: bool = True,
    token_map: dict[str, dict[str, str]] | None = None,
) -> pd.DataFrame | None:
    """Load or fetch daily netflow for a perp symbol; None if unmapped."""
    token_map = token_map or load_token_map()
    base = symbol_base(symbol)
    meta = token_map.get(base)
    if not meta:
        return None
    chain = meta.get("chain", "ethereum")
    address = meta["address"]
    token_symbol = meta.get("token_symbol") or meta.get("symbol")
    path = netflow_cache_path(out_root, chain, address, days)
    if use_cache and path.exists():
        return load_dataframe(path)
    df = fetch_netflow_daily(
        chain, address, days, token_symbol=token_symbol
    )
    if not df.empty:
        save_dataframe(df, path)
    return df


def merge_netflow_to_ohlcv(
    ohlcv: pd.DataFrame,
    netflow_daily: pd.DataFrame,
    *,
    rolling_days: int = 7,
) -> pd.DataFrame:
    """Attach rolling CEX net inflow (USD) to each bar by calendar day."""
    out = ohlcv.copy()
    if netflow_daily is None or netflow_daily.empty:
        out["net_inflow_usd"] = float("nan")
        out["net_inflow_roll_usd"] = float("nan")
        return out

    daily = netflow_daily.copy()
    daily["day"] = pd.to_datetime(daily["day"], utc=True).dt.normalize()
    daily = daily.set_index("day").sort_index()
    daily["net_inflow_roll_usd"] = daily["net_inflow_usd"].rolling(
        rolling_days, min_periods=1
    ).sum()

    out["bar_day"] = pd.to_datetime(out["timestamp"], utc=True).dt.normalize()
    merged = out.merge(
        daily[["net_inflow_usd", "net_inflow_roll_usd"]].reset_index(),
        left_on="bar_day",
        right_on="day",
        how="left",
    )
    merged["net_inflow_roll_usd"] = merged["net_inflow_roll_usd"].ffill()
    merged["net_inflow_usd"] = merged["net_inflow_usd"].ffill()
    return merged.drop(columns=["bar_day", "day"], errors="ignore")


def apply_dune_entry_filter(
    cond: pd.Series,
    df: pd.DataFrame,
    dune_cfg: DuneFilterConfig,
) -> pd.Series:
    """Filter entries by rolling CEX net inflow (whale distributing to exchanges)."""
    if "net_inflow_roll_usd" not in df.columns:
        if dune_cfg.skip_if_missing:
            return cond
        return cond & False

    roll = df["net_inflow_roll_usd"]
    if roll.notna().sum() == 0:
        if dune_cfg.skip_if_missing:
            return cond
        return cond & False

    # Bars without on-chain data pass through; mapped symbols filter when data exists.
    ok = roll.isna() | (roll <= dune_cfg.max_rolling_net_inflow_usd)
    if dune_cfg.require_net_outflow:
        ok = ok & (roll.isna() | (roll < 0))
    return cond & ok

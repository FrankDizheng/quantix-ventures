"""Tests for Dune netflow SQL rendering and merge logic."""

from crypto_quant.onchain.netflow import (
    _render_sql,
    _token_filter_clause,
    fetch_netflow_daily,
    merge_netflow_to_ohlcv,
)
import pandas as pd


def test_token_filter_evm_uses_lowercase_cast() -> None:
    clause = _token_filter_clause(
        "ethereum",
        "0x6982508145454Ce325ddbe47a25b4ec39f48a223",
    )
    assert "lower(cast(token_address as varchar))" in clause
    assert "0x6982508145454Ce325ddbe47a25b4ec39f48a223" in clause


def test_token_filter_prefers_symbol() -> None:
    clause = _token_filter_clause(
        "ethereum",
        "0xabc",
        token_symbol="PEPE",
    )
    assert clause == "token_symbol = 'PEPE'"


def test_render_sql_uses_inflow_outflow() -> None:
    sql = _render_sql("ethereum", "0xabc", 30, token_symbol="PEPE")
    assert "flow_type = 'Inflow'" in sql
    assert "flow_type = 'Outflow'" in sql
    assert "deposit" not in sql
    assert "token_symbol = 'PEPE'" in sql
    assert "interval '30' day" in sql


def test_fetch_netflow_empty_has_columns(monkeypatch) -> None:
    class FakeDuneClient:
        @staticmethod
        def load_sql(path):
            return path.read_text(encoding="utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute_sql(self, sql: str) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr("crypto_quant.onchain.netflow.DuneClient", FakeDuneClient)
    df = fetch_netflow_daily("ethereum", "0xabc", 7, token_symbol="PEPE")
    assert list(df.columns) == [
        "day",
        "inflow_usd",
        "outflow_usd",
        "net_inflow_usd",
    ]
    assert df.empty


def test_merge_empty_netflow() -> None:
    ohlcv = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=24, freq="h", tz="UTC"),
            "close": [1.0] * 24,
            "volume": [100.0] * 24,
        }
    )
    merged = merge_netflow_to_ohlcv(ohlcv, pd.DataFrame(), rolling_days=7)
    assert merged["net_inflow_roll_usd"].isna().all()

"""Dune Analytics API client (REST). API key from environment only."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from crypto_quant.config import load_config


class DuneClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_base: str | None = None,
        poll_interval_sec: float = 2.0,
        poll_timeout_sec: float = 300.0,
    ) -> None:
        cfg = load_config().get("dune", {})
        env_name = cfg.get("api_key_env", "DUNE_API_KEY")
        self.api_key = api_key or os.environ.get(env_name, "").strip()
        if not self.api_key:
            raise ValueError(
                f"Dune API key missing. Set {env_name} in .env "
                "(see .env.example, docs/DUNE.md)."
            )
        self.api_base = (
            api_base or cfg.get("api_base", "https://api.dune.com/api/v1")
        ).rstrip("/")
        self.sql_performance = cfg.get("sql_performance", "small")
        self.poll_interval_sec = poll_interval_sec or cfg.get("poll_interval_sec", 2)
        self.poll_timeout_sec = poll_timeout_sec or cfg.get("poll_timeout_sec", 300)
        self._http = httpx.Client(
            base_url=self.api_base,
            headers={"X-DUNE-API-KEY": self.api_key},
            timeout=60.0,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DuneClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def me(self) -> dict[str, Any]:
        """Lightweight auth check via `SELECT 1` (401 if key invalid)."""
        df = self.execute_sql("SELECT 1 AS ok", performance="small")
        return {"status": "ok", "rows": len(df)}

    def execute_sql(
        self,
        sql: str,
        *,
        performance: str = "small",
    ) -> pd.DataFrame:
        """Run ad-hoc SQL and return results as a DataFrame."""
        cfg = load_config().get("dune", {})
        perf = performance or cfg.get("sql_performance", "small")
        body: dict[str, Any] = {"sql": sql}
        if perf:
            body["performance"] = perf
        r = self._http.post("/sql/execute", json=body)
        r.raise_for_status()
        execution_id = r.json()["execution_id"]
        return self._wait_results(execution_id)

    def execute_query_id(self, query_id: int) -> pd.DataFrame:
        """Execute a saved Dune query by ID."""
        r = self._http.post(f"/query/{query_id}/execute")
        r.raise_for_status()
        execution_id = r.json()["execution_id"]
        return self._wait_results(execution_id)

    def _wait_results(self, execution_id: str) -> pd.DataFrame:
        deadline = time.time() + self.poll_timeout_sec
        while time.time() < deadline:
            st = self._http.get(f"/execution/{execution_id}/status")
            st.raise_for_status()
            state = st.json().get("state")
            if state == "QUERY_STATE_COMPLETED":
                break
            if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                raise RuntimeError(f"Dune execution {execution_id} ended: {state}")
            time.sleep(self.poll_interval_sec)
        else:
            raise TimeoutError(f"Dune execution {execution_id} timed out")

        res = self._http.get(f"/execution/{execution_id}/results")
        res.raise_for_status()
        payload = res.json()
        rows = payload.get("result", {}).get("rows", [])
        return pd.DataFrame(rows)

    @staticmethod
    def load_sql(path: Path) -> str:
        return path.read_text(encoding="utf-8")

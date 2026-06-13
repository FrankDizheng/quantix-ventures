from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "default.yaml"


def repo_root() -> Path:
    return _REPO_ROOT


def load_config(path: Path | None = None) -> dict[str, Any]:
    _load_dotenv()
    cfg_path = path or _DEFAULT_CONFIG
    with cfg_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_dotenv() -> None:
    """Load crypto-quant/.env if present (never committed)."""
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        pass


def data_dir(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config()
    raw = cfg.get("data_dir", "data")
    p = Path(raw)
    return p if p.is_absolute() else repo_root() / p


def dune_api_key(cfg: dict[str, Any] | None = None) -> str:
    """Read Dune API key from env (name configured in default.yaml)."""
    import os

    cfg = cfg or load_config()
    env_name = cfg.get("dune", {}).get("api_key_env", "DUNE_API_KEY")
    return os.environ.get(env_name, "").strip()

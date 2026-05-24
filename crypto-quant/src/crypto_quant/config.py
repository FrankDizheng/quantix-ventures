from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "default.yaml"


def repo_root() -> Path:
    return _REPO_ROOT


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or _DEFAULT_CONFIG
    with cfg_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def data_dir(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config()
    raw = cfg.get("data_dir", "data")
    p = Path(raw)
    return p if p.is_absolute() else repo_root() / p

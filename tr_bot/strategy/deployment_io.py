"""Atomic JSON writes and deployment pointer paths under ARTIFACTS_DIR."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEPLOYMENT_SUBDIR = "deployment"
STRATEGY_PROFILES_SUBDIR = "strategy_profiles"
PROMOTION_SUBDIR = "promotion"

POINTER_NAMES = {
    "paper": "paper_active.json",
    "live_canary": "live_canary_active.json",
    "live_full": "live_full_active.json",
}


def deployment_dir(artifacts_root: Path) -> Path:
    root = artifacts_root.resolve()
    d = root / DEPLOYMENT_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def pointer_path(artifacts_root: Path, tier: str) -> Path:
    name = POINTER_NAMES.get(tier)
    if not name:
        raise ValueError(f"unknown deployment tier: {tier!r}")
    return deployment_dir(artifacts_root) / name


def read_pointer(artifacts_root: Path, tier: str) -> dict[str, Any] | None:
    p = pointer_path(artifacts_root, tier)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def strategy_profile_path(artifacts_root: Path, profile_id: str) -> Path:
    if not profile_id or ".." in profile_id or "/" in profile_id or "\\" in profile_id:
        raise ValueError("invalid profile_id")
    root = artifacts_root.resolve()
    base = (root / STRATEGY_PROFILES_SUBDIR).resolve()
    p = (base / f"{profile_id}.json").resolve()
    try:
        p.relative_to(base)
    except ValueError as e:
        raise ValueError("invalid profile path") from e
    return p


def promotion_log_path(artifacts_root: Path) -> Path:
    root = artifacts_root.resolve()
    d = root / PROMOTION_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d / "log.jsonl"


def freeze_promotions_path(artifacts_root: Path) -> Path:
    return deployment_dir(artifacts_root) / "freeze_promotions"


def atomic_write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, indent=indent, default=str)
    tmp.write_text(text + ("\n" if indent is not None else ""), encoding="utf-8")
    os.replace(tmp, path)


def read_json_file(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None

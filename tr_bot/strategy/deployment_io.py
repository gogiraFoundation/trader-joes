"""Atomic JSON writes and deployment pointer paths under ARTIFACTS_DIR."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
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


def commit_candidate_profile(
    artifacts_root: Path,
    profile_doc: dict[str, Any],
    *,
    tier: str = "paper",
    update_pointer: bool = True,
    pointer_source: str = "candidate_commit",
) -> dict[str, Any]:
    """
    Validate and atomically write profile; optionally update deployment pointer.
    Returns {ok, profile_id, config_hash, pointer?, errors?}.
    """
    from tr_bot.strategy.profile import validate_candidate_profile

    profile_id = str(profile_doc.get("profile_id") or "").strip()
    if not profile_id:
        return {"ok": False, "errors": ["missing profile_id"]}

    validation = validate_candidate_profile(profile_doc, expected_profile_id=profile_id)
    if not validation["ok"]:
        return {"ok": False, "errors": validation["errors"], "profile_id": profile_id}

    # Ensure config_hash is embedded
    doc = dict(profile_doc)
    doc["config_hash"] = validation["config_hash"]

    prof_path = strategy_profile_path(artifacts_root, profile_id)
    atomic_write_json(prof_path, doc)

    result: dict[str, Any] = {
        "ok": True,
        "profile_id": profile_id,
        "config_hash": validation["config_hash"],
        "profile_path": str(prof_path),
    }

    if update_pointer:
        now = datetime.now(timezone.utc).isoformat()
        ptr = {
            "profile_id": profile_id,
            "activated_at": now,
            "source": pointer_source,
            "config_hash": validation["config_hash"],
        }
        atomic_write_json(pointer_path(artifacts_root, tier), ptr)
        result["pointer"] = ptr

    return result

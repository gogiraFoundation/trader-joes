"""Persist / load concatenated training frames (parquet + manifest)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path

logger = logging.getLogger("tr_bot.training.dataset")

REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume", "source"}


def _artifact_root() -> Path:
    return Path(config.ARTIFACTS_DIR).resolve()


def dataset_file_path() -> Path:
    rel = config.TRAIN_DATASET_PATH.strip() or "training/datasets/latest.parquet"
    p = Path(rel)
    if p.is_absolute():
        return p
    return safe_artifact_path(_artifact_root(), rel)


def manifest_path() -> Path:
    return dataset_file_path().with_name("manifest.json")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def save_training_dataset(
    df: pd.DataFrame,
    *,
    extra_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write parquet + manifest; optional versioned snapshot."""
    path = dataset_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)

    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(df)),
        "columns": list(df.columns),
        "train_sources": config.TRAIN_SOURCES,
        "sha256": _sha256_file(path),
    }
    if extra_manifest:
        manifest.update(extra_manifest)

    mpath = manifest_path()
    mtmp = mpath.with_suffix(".json.tmp")
    mtmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    mtmp.replace(mpath)

    logger.info("Saved training dataset to %s (%s rows)", path.resolve(), len(df))

    snap_path: str | None = None
    if config.TRAIN_DATASET_VERSION_SNAPSHOT:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snap = safe_artifact_path(path.parent, f"snapshot_{ts}.parquet")
        snap.write_bytes(path.read_bytes())
        snap_path = str(snap.resolve())
        logger.info("Version snapshot: %s", snap_path)

    return {"dataset_path": str(path.resolve()), "manifest": manifest, "snapshot": snap_path}


def load_training_dataset() -> tuple[pd.DataFrame, dict[str, Any]]:
    path = dataset_file_path()
    if not path.is_file():
        raise FileNotFoundError(f"Training dataset not found: {path}")
    df = pd.read_parquet(path)
    _validate_schema(df)
    mpath = manifest_path()
    meta: dict[str, Any] = {}
    if mpath.is_file():
        meta = json.loads(mpath.read_text(encoding="utf-8"))
    return df, meta


def _validate_schema(df: pd.DataFrame) -> None:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Cached dataset missing columns: {sorted(missing)}")


def cache_exists() -> bool:
    return dataset_file_path().is_file()


def cache_is_stale() -> bool:
    """True if file is older than TRAIN_DATASET_MAX_AGE_SECONDS (0 = never stale)."""
    max_age = int(config.TRAIN_DATASET_MAX_AGE_SECONDS)
    if max_age <= 0:
        return False
    path = dataset_file_path()
    if not path.is_file():
        return True
    age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    return age > max_age

"""Aggregate training artifacts and config for the dashboard API."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.managers.security import (
    collapse_artifact_dir_duplication,
    resolve_path_under_artifact_dir,
    safe_artifact_path,
)
from tr_bot.training.dataset import dataset_file_path, manifest_path
from tr_bot.training.last_run import read_training_last_run

MAX_VERSIONED_MODELS = 5


def _artifact_base() -> Path:
    return Path(config.ARTIFACTS_DIR).resolve()


def _file_stat(p: Path) -> dict[str, Any]:
    if not p.is_file():
        return {
            "exists": False,
            "path": str(p.resolve()) if p.parts else "",
            "size_bytes": None,
            "mtime_utc": None,
        }
    st = p.stat()
    return {
        "exists": True,
        "path": str(p.resolve()),
        "size_bytes": st.st_size,
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


def _resolve_under_artifacts(rel_or_abs: str) -> Path:
    return resolve_path_under_artifact_dir(_artifact_base(), rel_or_abs)


def _sanitize_last_run_paths(lr: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize doubled artifacts/ segments in persisted path strings."""
    if not lr:
        return None
    base = _artifact_base()
    out = dict(lr)
    for key in ("artifact", "version", "promoted_to"):
        v = out.get(key)
        if isinstance(v, str) and v.strip():
            try:
                out[key] = str(collapse_artifact_dir_duplication(base, Path(v)))
            except OSError:
                pass
    return out


def build_training_dashboard_payload() -> dict[str, Any]:
    base = _artifact_base()
    cfg_out = {
        "train_sources": config.TRAIN_SOURCES,
        "train_dataset_mode": config.TRAIN_DATASET_MODE,
        "train_dataset_path": config.TRAIN_DATASET_PATH,
        "min_train_rows_validate": config.MIN_TRAIN_ROWS_VALIDATE,
        "training_promote_on_success": config.TRAINING_PROMOTE_ON_SUCCESS,
        "regime_model_path": config.REGIME_MODEL_PATH or "",
        "train_output_path": config.TRAIN_OUTPUT_PATH,
        "train_run_validation": config.TRAIN_RUN_VALIDATION,
        "train_ts_splits": config.TRAIN_TS_SPLITS,
        "train_min_rows_per_fold": config.TRAIN_MIN_ROWS_PER_FOLD,
        "train_min_oof_f1": config.TRAIN_MIN_OOF_F1,
        "train_max_train_val_gap": config.TRAIN_MAX_TRAIN_VAL_GAP,
        "train_validation_strict": config.TRAIN_VALIDATION_STRICT,
        "train_skip_validation_gates": config.TRAIN_SKIP_VALIDATION_GATES,
        "train_validation_per_group": config.TRAIN_VALIDATION_PER_GROUP,
        "train_backtest_gate": config.TRAIN_BACKTEST_GATE,
        "train_backtest_symbol": config.TRAIN_BACKTEST_SYMBOL or config.SYMBOL,
        "train_backtest_bars": config.TRAIN_BACKTEST_BARS or config.BACKTEST_BARS,
        "train_backtest_timeframe": config.TRAIN_BACKTEST_TIMEFRAME or config.TIMEFRAME,
    }

    parquet_path = dataset_file_path()
    dataset_payload: dict[str, Any] = {
        "parquet_path": str(parquet_path.resolve()),
        "parquet_exists": parquet_path.is_file(),
    }
    if parquet_path.is_file():
        st = parquet_path.stat()
        dataset_payload["parquet_size_bytes"] = st.st_size
        dataset_payload["parquet_mtime_utc"] = datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc
        ).isoformat()
    else:
        dataset_payload["parquet_size_bytes"] = None
        dataset_payload["parquet_mtime_utc"] = None

    mpath = manifest_path()
    manifest_obj: dict[str, Any] | None = None
    manifest_err: str | None = None
    if mpath.is_file():
        try:
            manifest_obj = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            manifest_err = str(e)
    dataset_payload["manifest"] = manifest_obj
    if manifest_err:
        dataset_payload["manifest_error"] = manifest_err

    versioned: list[dict[str, Any]] = []
    try:
        models_dir = safe_artifact_path(base, "models")
        if models_dir.is_dir():
            cands = list(models_dir.glob("regime_*.joblib"))
            cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for p in cands[:MAX_VERSIONED_MODELS]:
                st = p.stat()
                versioned.append(
                    {
                        "name": p.name,
                        "path": str(p.resolve()),
                        "size_bytes": st.st_size,
                        "mtime_utc": datetime.fromtimestamp(
                            st.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    }
                )
    except (OSError, ValueError):
        pass

    regime = (config.REGIME_MODEL_PATH or "").strip()
    if regime:
        prod_path = _resolve_under_artifacts(regime)
        production: dict[str, Any] = {"configured": True, **_file_stat(prod_path)}
    else:
        production = {
            "configured": False,
            "exists": False,
            "path": "",
            "size_bytes": None,
            "mtime_utc": None,
        }

    train_out = _resolve_under_artifacts(config.TRAIN_OUTPUT_PATH)
    train_output_stat = _file_stat(train_out)

    return {
        "config": cfg_out,
        "dataset": dataset_payload,
        "models": {
            "versioned": versioned,
            "production": production,
            "train_output": train_output_stat,
        },
        "last_run": _sanitize_last_run_paths(read_training_last_run()),
    }

"""Training job: validate, version, atomic promote to production model path."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.managers.security import (
    collapse_artifact_dir_duplication,
    resolve_path_under_artifact_dir,
    safe_artifact_path,
)
from tr_bot.notifications import events as notify_events
from tr_bot.notifications.facade import notify
from tr_bot.training.last_run import write_training_last_run
from tr_bot.training.pipeline import run_training_pipeline

logger = logging.getLogger("tr_bot.training_manager")


def _artifact_base() -> Path:
    return Path(config.ARTIFACTS_DIR).resolve()


def run_training_job(
    *,
    output_path: Path | None = None,
    promote: bool | None = None,
) -> dict[str, Any]:
    """
    Train regime model, enforce MIN_TRAIN_ROWS_VALIDATE, optionally version + atomic promote.
    """
    promote = config.TRAINING_PROMOTE_ON_SUCCESS if promote is None else promote
    base = _artifact_base()
    out = output_path
    if out and not out.is_absolute():
        out = resolve_path_under_artifact_dir(base, str(out))
    elif out:
        out = collapse_artifact_dir_duplication(base, out)

    try:
        result = run_training_pipeline(output_path=out)
    except Exception as e:
        notify(notify_events.TRAINING_FAILED, stage="pipeline", error=str(e)[:500])
        write_training_last_run(
            {
                "ok": False,
                "stage": "pipeline",
                "error": str(e)[:800],
            }
        )
        raise

    n = int(result["rows"])
    if n < config.MIN_TRAIN_ROWS_VALIDATE:
        err = (
            f"Training validation failed: {n} rows < MIN_TRAIN_ROWS_VALIDATE={config.MIN_TRAIN_ROWS_VALIDATE}"
        )
        notify(notify_events.TRAINING_FAILED, stage="validation", error=err)
        write_training_last_run(
            {
                "ok": False,
                "stage": "validation",
                "error": err,
                "feature_rows": n,
            }
        )
        raise RuntimeError(err)

    artifact = Path(result["path"])
    version_dir = safe_artifact_path(_artifact_base(), "models")
    version_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    vpath = safe_artifact_path(_artifact_base(), f"models/regime_{ts}.joblib")
    shutil.copy2(artifact, vpath)
    logger.info("Versioned copy: %s", vpath)

    gates_ok = bool(result.get("validation_gates_passed", True))
    want_promote = promote and bool(config.REGIME_MODEL_PATH.strip())
    if want_promote and not gates_ok and not config.TRAIN_SKIP_VALIDATION_GATES:
        logger.warning(
            "TRAINING_PROMOTE_ON_SUCCESS: skipping promote to REGIME_MODEL_PATH (validation gates failed)"
        )
        want_promote = False

    promoted_to = None
    if want_promote:
        rp = config.REGIME_MODEL_PATH.strip()
        base = _artifact_base()
        prod = (
            resolve_path_under_artifact_dir(base, rp)
            if not Path(rp).is_absolute()
            else collapse_artifact_dir_duplication(base, Path(rp))
        )
        prod.parent.mkdir(parents=True, exist_ok=True)
        tmp = prod.with_suffix(".joblib.tmp")
        shutil.copy2(artifact, tmp)
        os.replace(tmp, prod)
        promoted_to = str(prod.resolve())
        logger.info("Promoted model to %s", promoted_to)

    notify(
        notify_events.TRAINING_SUCCESS,
        rows=n,
        artifact=str(artifact),
        version=str(vpath.resolve()),
        promoted_to=promoted_to,
        sources=result.get("sources"),
        dataset_from_cache=result.get("dataset_from_cache"),
        validation_gates_passed=gates_ok,
    )

    write_training_last_run(
        {
            "ok": True,
            "stage": "complete",
            "feature_rows": n,
            "artifact": str(artifact.resolve()),
            "version": str(vpath.resolve()),
            "promoted_to": promoted_to,
            "sources": result.get("sources"),
            "dataset_from_cache": result.get("dataset_from_cache"),
            "manifest": result.get("manifest"),
            "validation": result.get("validation"),
            "validation_gates_passed": gates_ok,
            "validation_gate_reasons": result.get("validation_gate_reasons"),
        }
    )

    return {
        "rows": n,
        "artifact": str(artifact),
        "sources": result.get("sources"),
        "version": str(vpath.resolve()),
        "promoted_to": promoted_to,
        "dataset_from_cache": result.get("dataset_from_cache"),
        "manifest": result.get("manifest"),
        "validation": result.get("validation"),
        "validation_gates_passed": gates_ok,
        "validation_gate_reasons": result.get("validation_gate_reasons"),
    }

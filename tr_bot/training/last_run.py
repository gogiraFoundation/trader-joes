"""Persist last training outcome for the dashboard (training/last_run.json)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path

logger = logging.getLogger("tr_bot.training.last_run")

LAST_RUN_REL = "training/last_run.json"


def _last_run_path() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    return safe_artifact_path(root, LAST_RUN_REL)


def write_training_last_run(payload: dict[str, Any]) -> None:
    """Atomic JSON write under ARTIFACTS_DIR."""
    path = _last_run_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    if "finished_utc" not in body:
        body["finished_utc"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not write training last_run: %s", e)


def read_training_last_run() -> dict[str, Any] | None:
    path = _last_run_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.debug("Could not read training last_run: %s", e)
        return None

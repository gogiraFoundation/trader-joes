"""Durable JSON store for currently open positions."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path
from tr_bot.position import OpenPosition

logger = logging.getLogger("tr_bot.open_positions_state")

OPEN_POSITIONS_FILENAME = "open_positions.json"


def _path() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return safe_artifact_path(root, OPEN_POSITIONS_FILENAME)


def load_open_positions() -> dict[str, OpenPosition]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw.get("positions") if isinstance(raw, dict) else None
        if not isinstance(rows, dict):
            return {}
        out: dict[str, OpenPosition] = {}
        for symbol, payload in rows.items():
            if not isinstance(payload, dict):
                continue
            try:
                out[symbol] = OpenPosition(**payload)
            except (TypeError, ValueError):
                continue
        return out
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("Could not load open positions: %s", e)
        return {}


def save_open_positions(positions: dict[str, OpenPosition | None]) -> None:
    path = _path()
    rows: dict[str, dict] = {}
    for symbol, pos in positions.items():
        if pos is None:
            continue
        rows[symbol] = asdict(pos)
    body = {"positions": rows}
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not save open positions: %s", e)


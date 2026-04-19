"""Append-only promotion audit log (artifacts/promotion/log.jsonl)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tr_bot.strategy import deployment_io as dep

logger = logging.getLogger("tr_bot.promotion.log")


def append_promotion_event(artifacts_root: Path, entry: dict[str, Any]) -> None:
    path = dep.promotion_log_path(artifacts_root)
    line = json.dumps(entry, default=str) + "\n"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("Could not append promotion log: %s", e)


def read_promotion_log_tail(artifacts_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    path = dep.promotion_log_path(artifacts_root)
    if not path.is_file() or limit <= 0:
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Could not read promotion log: %s", e)
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    tail = lines[-limit:]
    out: list[dict[str, Any]] = []
    for ln in reversed(tail):
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


def find_last_success_profile(artifacts_root: Path) -> str | None:
    """Scan log from end for successful promotion with profile_id."""
    for row in reversed(read_promotion_log_tail(artifacts_root, limit=500)):
        if row.get("action") == "promote" and row.get("passed") is True:
            pid = row.get("profile_id")
            if isinstance(pid, str) and pid:
                return pid
    return None

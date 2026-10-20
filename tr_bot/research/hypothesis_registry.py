"""Hypothesis registry under ARTIFACTS_DIR/research/hypotheses.jsonl."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config

logger = logging.getLogger("tr_bot.research.hypothesis")

REL = "research/hypotheses.jsonl"


def _path() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    p = root / REL
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append_hypothesis(
    *,
    description: str,
    category: str = "strategy",
    priority: str = "medium",
    source: str = "manual",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    hid = f"HYP-{now.strftime('%Y%m%d%H%M%S')}"
    rec = {
        "id": hid,
        "created_utc": now.isoformat(),
        "description": description[:2000],
        "category": category,
        "priority": priority,
        "source": source,
        "status": "under_review",
        "meta": meta or {},
    }
    path = _path()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


def list_hypotheses(*, limit: int = 50) -> list[dict[str, Any]]:
    path = _path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-limit:]

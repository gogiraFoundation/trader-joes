"""Structured logging channel (always safe for log aggregation)."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("tr_bot.notify")


def send(title: str, event: str, payload: dict[str, Any]) -> None:
    line = json.dumps({"title": title, "event": event, "payload": payload}, default=str)
    logger.info("NOTIFY %s", line)

"""UTC trading-session classification (Asia / London / New York / overlap)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def classify_session(now: Any = None) -> str:
    """
    Approximate session windows (UTC):
      asia: 00:00–08:00
      london: 08:00–16:00
      new_york: 13:00–21:00
      london_ny_overlap: 13:00–16:00
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif getattr(now, "tzinfo", None) is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    hour = now.hour
    if 13 <= hour < 16:
        return "london_ny_overlap"
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 13:
        return "london"
    if 16 <= hour < 21:
        return "new_york"
    return "off_hours"


def session_size_adjuster(session: str) -> float:
    return {
        "asia": 0.85,
        "london": 1.0,
        "new_york": 1.05,
        "london_ny_overlap": 1.15,
        "off_hours": 0.8,
        "unknown": 1.0,
    }.get(session, 1.0)

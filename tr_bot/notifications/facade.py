"""Single entrypoint for the rest of the codebase."""

from __future__ import annotations

from typing import Any

from tr_bot.notifications.manager import NotificationManager


def notify(event: str, **payload: Any) -> None:
    NotificationManager.instance().send(event, dict(payload))

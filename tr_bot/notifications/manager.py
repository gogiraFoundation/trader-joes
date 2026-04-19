"""Dispatch notifications with per-event throttling."""

from __future__ import annotations

import time
from typing import Any

from tr_bot import config
from tr_bot.notifications import events as ev
from tr_bot.notifications.backends import console, webhook
from tr_bot.notifications.sanitize import sanitize


def _title_for_event(event: str) -> str:
    return {
        ev.TRADE_OPENED: "Trade opened",
        ev.TRADE_CLOSED: "Trade closed",
        ev.FUND_HALT: "Fund halt",
        ev.CIRCUIT_TRIPPED: "Circuit breaker",
        ev.TRAINING_SUCCESS: "Training complete",
        ev.TRAINING_FAILED: "Training failed",
        ev.BACKTEST_SUCCESS: "Backtest complete",
        ev.BACKTEST_FAILED: "Backtest failed",
        ev.BOT_ERROR: "Bot error",
        ev.DEPENDENCIES_DEGRADED: "Dependencies degraded",
    }.get(event, event)


class NotificationManager:
    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        raw = (config.NOTIFY_CHANNELS or "console").lower()
        self._channels = [x.strip() for x in raw.split(",") if x.strip()]

    def _throttle_key(self, event: str, payload: dict[str, Any]) -> str:
        if event in (
            ev.FUND_HALT,
            ev.CIRCUIT_TRIPPED,
            ev.BOT_ERROR,
            ev.DEPENDENCIES_DEGRADED,
            ev.BACKTEST_SUCCESS,
            ev.BACKTEST_FAILED,
        ):
            return event
        sym = payload.get("symbol") or ""
        return f"{event}:{sym}"

    def send(self, event: str, payload: dict[str, Any]) -> None:
        if not config.NOTIFY_ENABLED:
            return
        key = self._throttle_key(event, payload)
        now = time.time()
        interval = float(
            config.NOTIFY_DEPS_INTERVAL_SECONDS
            if event == ev.DEPENDENCIES_DEGRADED
            else config.NOTIFY_MIN_INTERVAL_SECONDS
        )
        last = self._last.get(key, 0.0)
        if now - last < interval:
            if event not in (
                ev.TRAINING_SUCCESS,
                ev.TRAINING_FAILED,
                ev.BACKTEST_SUCCESS,
                ev.BACKTEST_FAILED,
            ):
                return
        self._last[key] = now

        clean = sanitize(payload)
        title = _title_for_event(event)
        if "console" in self._channels:
            console.send(title, event, clean)
        if "webhook" in self._channels:
            webhook.send(title, event, clean)

    @classmethod
    def instance(cls) -> NotificationManager:
        if not hasattr(cls, "_singleton"):
            cls._singleton = cls()
        return cls._singleton

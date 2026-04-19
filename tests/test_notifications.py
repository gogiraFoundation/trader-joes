"""Notification manager and sanitization."""

from __future__ import annotations

from tr_bot.notifications.events import TRADE_OPENED
from tr_bot.notifications.sanitize import sanitize


def test_sanitize_redacts_secret_key() -> None:
    out = sanitize({"symbol": "BTC/USDT", "api_key": "SHOULD_NOT_LEAK"})
    assert out["api_key"] == "<redacted>"


def test_sanitize_nested() -> None:
    out = sanitize({"nested": {"Authorization": "bearer x"}})
    assert out["nested"]["Authorization"] == "<redacted>"


def test_notify_respects_disabled(monkeypatch) -> None:
    from tr_bot import config
    from tr_bot.notifications.facade import notify

    monkeypatch.setattr(config, "NOTIFY_ENABLED", False)
    notify(TRADE_OPENED, symbol="X", side="buy")  # should no-op

"""Scheduler interval logic and inactive exit."""

from __future__ import annotations

from unittest.mock import patch

from tr_bot import config
from tr_bot.scheduler import due_for_interval, run_forever


def test_due_for_interval_never_when_zero() -> None:
    assert due_for_interval(None, 0, 1_000.0) is False
    assert due_for_interval(500.0, 0, 9_999.0) is False


def test_due_for_interval_first_time() -> None:
    assert due_for_interval(None, 60, 1_000.0) is True


def test_due_for_interval_respects_elapsed() -> None:
    assert due_for_interval(1_000.0, 60, 1_000.0) is False
    assert due_for_interval(1_000.0, 60, 1_059.0) is False
    assert due_for_interval(1_000.0, 60, 1_060.0) is True


def test_run_forever_noop_when_nothing_configured() -> None:
    with (
        patch.object(config, "SCHEDULER_ENABLED", False),
        patch.object(config, "SCHEDULER_TRAINING_INTERVAL_SECONDS", 0),
        patch.object(config, "SCHEDULER_BACKTEST_INTERVAL_SECONDS", 0),
        patch.object(config, "SCHEDULER_PROMOTION_INTERVAL_SECONDS", 0),
        patch.object(config, "ADAPTATION_INTERVAL_SECONDS", 0),
        patch.object(config, "SCHEDULER_COINDESK_ASSET_TOP_LIST_INTERVAL_SECONDS", 0),
        patch.object(config, "SCHEDULER_DAILY_REPORT_INTERVAL_SECONDS", 0),
        patch.object(config, "SCHEDULER_AGENTS_INTERVAL_SECONDS", 0),
    ):
        run_forever()

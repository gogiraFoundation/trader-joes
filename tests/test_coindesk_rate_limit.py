"""Unit tests: CoinDesk rate limit + payload helpers."""

from __future__ import annotations

from tr_bot.coindesk.rate_limit import RateLimitController, RateLimitSnapshot, coindesk_payload_messages


def test_rate_limit_snapshot_headers() -> None:
    snap = RateLimitSnapshot()
    snap.update_from_headers(
        {
            "X-RateLimit-Limit": "100",
            "x-ratelimit-remaining": "3",
            "X-Ratelimit-Reset": "2000000000",
        }
    )
    assert snap.limit == 100
    assert snap.remaining == 3
    assert snap.reset_epoch == 2000000000


def test_rate_limit_controller_slow_path() -> None:
    ctrl = RateLimitController(remaining_threshold=10, slow_delay_s=0.0)
    ctrl.last_snapshot.remaining = 2
    ctrl.maybe_slow_down()  # no sleep when delay 0


def test_coindesk_payload_messages() -> None:
    errs, warns = coindesk_payload_messages({"Err": ["bad"], "Warn": "mild"})
    assert "bad" in errs
    assert "mild" in warns
    errs2, _ = coindesk_payload_messages({"status": {"error_message": "x"}})
    assert "x" in errs2

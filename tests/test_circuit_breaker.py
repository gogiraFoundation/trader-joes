"""Circuit breaker persistence and trip behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tr_bot.circuit_breaker import CIRCUIT_STATE_FILENAME, CircuitBreaker


def test_circuit_breaker_record_win_resets_streak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.circuit_breaker._artifacts_root", lambda: tmp_path)
    cb = CircuitBreaker(max_consecutive_losses=3)
    cb.record_outcome(False)
    cb.record_outcome(False)
    assert cb.consecutive_losses == 2
    cb.record_outcome(True)
    assert cb.consecutive_losses == 0


def test_circuit_breaker_trips_at_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.circuit_breaker._artifacts_root", lambda: tmp_path)
    cb = CircuitBreaker(max_consecutive_losses=2)
    assert cb.tripped is False
    cb.record_outcome(False)
    assert cb.tripped is False
    cb.record_outcome(False)
    assert cb.tripped is True


def test_circuit_state_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.circuit_breaker._artifacts_root", lambda: tmp_path)
    cb = CircuitBreaker(max_consecutive_losses=5)
    cb.record_outcome(False)
    cb.record_outcome(False)
    path = tmp_path / CIRCUIT_STATE_FILENAME
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["consecutive_losses"] == 2


def test_read_circuit_state_snapshot_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.circuit_breaker._artifacts_root", lambda: tmp_path)
    from tr_bot.circuit_breaker import read_circuit_state_snapshot

    snap = read_circuit_state_snapshot()
    assert snap["file_exists"] is False


def test_reset_for_api_writes_nonce_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot.circuit_breaker import reset_for_api

    marker = tmp_path / ".circuit_reset"
    monkeypatch.setattr("tr_bot.circuit_breaker._reset_file_path", lambda: marker)
    assert not marker.exists()
    reset_for_api()
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip()


def test_reset_for_api_resets_running_circuit_breaker_on_next_tripped_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors the real cross-process mechanism: nonce marker polled via `.tripped`."""
    monkeypatch.setattr("tr_bot.circuit_breaker._artifacts_root", lambda: tmp_path)
    marker = tmp_path / ".circuit_reset"
    monkeypatch.setattr("tr_bot.circuit_breaker._reset_file_path", lambda: marker)

    cb = CircuitBreaker(max_consecutive_losses=2)
    cb.record_outcome(False)
    cb.record_outcome(False)
    assert cb.tripped is True

    from tr_bot.circuit_breaker import reset_for_api

    reset_for_api()
    assert cb.tripped is False
    assert cb.consecutive_losses == 0
    assert not marker.exists()


def test_consumed_reset_marker_does_not_clear_new_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tr_bot.circuit_breaker._artifacts_root", lambda: tmp_path)
    marker = tmp_path / ".circuit_reset"
    monkeypatch.setattr("tr_bot.circuit_breaker._reset_file_path", lambda: marker)

    from tr_bot.circuit_breaker import reset_for_api

    cb1 = CircuitBreaker(max_consecutive_losses=2)
    cb1.record_outcome(False)
    cb1.record_outcome(False)
    assert cb1.tripped is True
    reset_for_api()
    assert cb1.tripped is False
    assert not marker.exists()

    cb1.record_outcome(False)
    cb1.record_outcome(False)
    assert cb1.tripped is True

    cb2 = CircuitBreaker(max_consecutive_losses=2)
    assert cb2.tripped is True
    assert cb2.consecutive_losses == 2


def test_two_resets_after_retrip_both_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tr_bot.circuit_breaker._artifacts_root", lambda: tmp_path)
    marker = tmp_path / ".circuit_reset"
    monkeypatch.setattr("tr_bot.circuit_breaker._reset_file_path", lambda: marker)

    from tr_bot.circuit_breaker import reset_for_api

    cb = CircuitBreaker(max_consecutive_losses=2)
    cb.record_outcome(False)
    cb.record_outcome(False)
    reset_for_api()
    assert cb.tripped is False

    cb.record_outcome(False)
    cb.record_outcome(False)
    assert cb.tripped is True
    reset_for_api()
    assert cb.tripped is False
    assert cb.consecutive_losses == 0

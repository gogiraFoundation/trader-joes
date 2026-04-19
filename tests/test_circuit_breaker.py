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

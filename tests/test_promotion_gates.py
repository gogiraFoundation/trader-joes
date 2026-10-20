"""Promotion gates and service (isolated ARTIFACTS_DIR)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tr_bot.promotion import log as promotion_log


def test_promotions_frozen_file(tmp_path: Path) -> None:
    from tr_bot.promotion import gates
    from tr_bot.strategy import deployment_io as dep

    p = dep.freeze_promotions_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    assert gates.promotions_frozen(tmp_path) is True


def test_gate_min_trades() -> None:
    from tr_bot.promotion import gates

    rows = [{"outcome": "win", "utc": "2025-01-01T00:00:00+00:00"}] * 25
    g = gates.gate_min_trades(rows, 20)
    assert g.passed is True


def test_find_last_success(tmp_path: Path) -> None:
    from tr_bot.strategy import deployment_io as dep

    p = dep.promotion_log_path(tmp_path)
    p.write_text(
        json.dumps(
            {
                "action": "promote",
                "passed": True,
                "profile_id": "good1",
                "utc": "2025-01-01T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert promotion_log.find_last_success_profile(tmp_path) == "good1"


def test_input_hash_stable() -> None:
    from tr_bot.promotion import gates

    h1 = gates.build_input_hash("p", "paper", [])
    h2 = gates.build_input_hash("p", "paper", [])
    assert h1 == h2
    assert len(h1) == 16


def test_ledger_rows_require_exact_hash() -> None:
    from tr_bot.promotion import gates

    rows = [
        {"profile_id": "p1", "profile_hash": "abc", "outcome": "win"},
        {"profile_id": "p1", "profile_hash": "", "outcome": "win"},
        {"profile_id": "p1", "outcome": "loss"},
        {"profile_id": "p1", "profile_hash": "zzz", "outcome": "win"},
        {"profile_id": "other", "profile_hash": "abc", "outcome": "win"},
        {"outcome": "win"},  # legacy untagged
    ]
    matched = gates._ledger_rows_for_profile(rows, "p1", profile_hash="abc", strict=True)
    assert len(matched) == 1
    assert matched[0]["profile_hash"] == "abc"


def test_ledger_rows_missing_hash_excluded_when_hash_requested() -> None:
    from tr_bot.promotion import gates

    rows = [
        {"profile_id": "p1", "outcome": "win"},
        {"profile_id": "p1", "profile_hash": None, "outcome": "loss"},
    ]
    matched = gates._ledger_rows_for_profile(rows, "p1", profile_hash="abc", strict=False)
    assert matched == []


def test_ledger_rows_without_hash_still_match_id() -> None:
    from tr_bot.promotion import gates

    rows = [
        {"profile_id": "p1", "outcome": "win"},
        {"profile_id": "p1", "profile_hash": "x", "outcome": "loss"},
    ]
    matched = gates._ledger_rows_for_profile(rows, "p1", profile_hash=None, strict=True)
    assert len(matched) == 2


def test_gate_equity_drawdown_no_points_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot.promotion import gates

    monkeypatch.setattr(
        "tr_bot.promotion.gates.read_equity_history",
        lambda: {"points": []},
    )
    g = gates.gate_equity_drawdown(0.25)
    assert g.passed is False
    assert "no_equity" in g.detail.lower() or "nav" in g.detail.lower()


def test_gate_equity_drawdown_single_point_passes_zero_dd(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot.promotion import gates

    monkeypatch.setattr(
        "tr_bot.promotion.gates.read_equity_history",
        lambda: {"points": [{"nav": 100.0}]},
    )
    g = gates.gate_equity_drawdown(0.25)
    assert g.passed is True
    assert "single_point" in g.detail.lower()


def test_gate_equity_drawdown_two_points_evaluates(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot.promotion import gates

    monkeypatch.setattr(
        "tr_bot.promotion.gates.read_equity_history",
        lambda: {
            "points": [
                {"nav": 100.0},
                {"nav": 90.0},
            ]
        },
    )
    g = gates.gate_equity_drawdown(0.25)
    assert g.passed is True
    assert "max_dd=" in g.detail

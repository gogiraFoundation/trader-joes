"""LLM proposal validation allowlist and bounds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tr_bot.strategy.proposal_validate import (
    load_and_validate_proposal_file,
    validate_llm_proposal,
)


def test_validate_llm_proposal_ok() -> None:
    raw = {
        "schema_version": 1,
        "params": {"REGIME_MIN_CONFIDENCE": 0.52},
        "rationale": "test",
        "risk_notes": "paper",
    }
    out = validate_llm_proposal(raw)
    assert out["ok"] is True
    assert out["coerced_params"]["REGIME_MIN_CONFIDENCE"] == 0.52


def test_validate_rejects_unknown_key() -> None:
    raw = {"schema_version": 1, "params": {"NOT_A_REAL_KEY": 1}}
    out = validate_llm_proposal(raw)
    assert out["ok"] is False
    assert any("not allowed" in e for e in out["errors"])


def test_validate_rsi_order(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = {
        "schema_version": 1,
        "params": {"SIGNAL_RSI_OVERSOLD": 60, "SIGNAL_RSI_OVERBOUGHT": 40},
    }
    out = validate_llm_proposal(raw)
    assert out["ok"] is False
    assert any("OVERSOLD must be" in e for e in out["errors"])


def test_load_fixture(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    p = root / "tests" / "fixtures" / "llm_proposal_valid.json"
    out = load_and_validate_proposal_file(p)
    assert out["ok"] is True

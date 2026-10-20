"""ActionPlan schema validation."""

from __future__ import annotations

from tr_bot.agents.schema import noop_plan, validate_action_plan


def test_noop_plan_shape() -> None:
    p = noop_plan("supervisor", summary="x", llm_error="e")
    assert p["role"] == "supervisor"
    assert p["actions"][0]["type"] == "noop"
    assert p["llm_error"] == "e"


def test_validate_ok_minimal() -> None:
    raw = {
        "schema_version": 1,
        "role": "supervisor",
        "summary": "all good",
        "severity": "info",
        "actions": [{"type": "noop", "reason": "ok", "confidence": 1.0}],
        "proposal": None,
    }
    out = validate_action_plan(raw, expected_role="supervisor")
    assert out["ok"] is True
    assert out["plan"]["actions"][0]["type"] == "noop"


def test_reject_unknown_role() -> None:
    out = validate_action_plan(
        {
            "schema_version": 1,
            "role": "trader",
            "severity": "info",
            "actions": [{"type": "noop", "confidence": 1}],
        }
    )
    assert out["ok"] is False


def test_drop_unknown_action_type() -> None:
    out = validate_action_plan(
        {
            "schema_version": 1,
            "role": "supervisor",
            "severity": "warning",
            "summary": "x",
            "actions": [
                {"type": "open_trade", "confidence": 0.9, "reason": "nope"},
                {"type": "notify", "confidence": 0.9, "reason": "alert"},
            ],
        }
    )
    assert out["ok"] is True
    types = [a["type"] for a in out["plan"]["actions"]]
    assert "open_trade" not in types
    assert "notify" in types
    assert any("unknown type" in e for e in out["errors"])


def test_emit_proposal_requires_valid_proposal() -> None:
    out = validate_action_plan(
        {
            "schema_version": 1,
            "role": "fund_manager",
            "severity": "warning",
            "summary": "tighten",
            "actions": [{"type": "emit_proposal", "confidence": 0.8, "reason": "x"}],
            "proposal": None,
        },
        expected_role="fund_manager",
    )
    assert out["ok"] is False


def test_emit_proposal_with_allowlisted_params() -> None:
    out = validate_action_plan(
        {
            "schema_version": 1,
            "role": "fund_manager",
            "severity": "warning",
            "summary": "tighten rsi",
            "actions": [{"type": "emit_proposal", "confidence": 0.85, "reason": "losses"}],
            "proposal": {
                "schema_version": 1,
                "params": {"SIGNAL_RSI_OVERSOLD": 20},
                "rationale": "fewer false longs",
                "risk_notes": "may reduce trade count",
            },
        },
        expected_role="fund_manager",
    )
    assert out["ok"] is True, out.get("errors")
    assert out["plan"]["proposal"]["params"]["SIGNAL_RSI_OVERSOLD"] == 20.0


def test_reject_bad_confidence() -> None:
    out = validate_action_plan(
        {
            "schema_version": 1,
            "role": "supervisor",
            "severity": "info",
            "actions": [{"type": "notify", "confidence": 1.5, "reason": "x"}],
        }
    )
    # Invalid action dropped → noop fallback
    assert out["ok"] is True
    assert out["plan"]["actions"][0]["type"] == "noop"


def test_at_most_one_mutating_action() -> None:
    out = validate_action_plan(
        {
            "schema_version": 1,
            "role": "supervisor",
            "severity": "warning",
            "summary": "x",
            "actions": [
                {"type": "pause_entries", "confidence": 0.9, "reason": "a", "params": {}},
                {"type": "resume_entries", "confidence": 0.9, "reason": "b", "params": {}},
            ],
        }
    )
    assert out["ok"] is True
    mutating = [a["type"] for a in out["plan"]["actions"] if a["type"] not in ("noop", "notify")]
    assert mutating == ["pause_entries"]
    assert any("at most 1 mutating" in e for e in out["errors"])


def test_reject_nonempty_action_params() -> None:
    out = validate_action_plan(
        {
            "schema_version": 1,
            "role": "supervisor",
            "severity": "info",
            "summary": "x",
            "actions": [
                {"type": "notify", "confidence": 0.9, "reason": "a", "params": {"x": 1}},
            ],
        }
    )
    assert out["ok"] is True
    assert out["plan"]["actions"][0]["type"] == "noop"
    assert any("params must be empty" in e for e in out["errors"])


def test_reject_bool_overlay_keys() -> None:
    out = validate_action_plan(
        {
            "schema_version": 1,
            "role": "fund_manager",
            "severity": "warning",
            "summary": "x",
            "actions": [{"type": "emit_proposal", "confidence": 0.9, "reason": "x", "params": {}}],
            "proposal": {
                "schema_version": 1,
                "params": {"USE_SIGNAL_STRENGTH_FILTER": False},
            },
        },
        expected_role="fund_manager",
    )
    assert out["ok"] is False
    assert any("not allowed" in e for e in (out.get("errors") or []))

"""Validate agent ActionPlan JSON (v1)."""

from __future__ import annotations

from typing import Any

from tr_bot.strategy.proposal_validate import validate_llm_proposal

ACTION_PLAN_SCHEMA_VERSION = 1
MAX_ACTIONS = 3
MAX_MUTATING_ACTIONS = 1

ROLES = frozenset({"supervisor", "fund_manager"})
SEVERITIES = frozenset({"info", "warning", "critical"})
ACTION_TYPES = frozenset(
    {
        "noop",
        "notify",
        "pause_entries",
        "resume_entries",
        "reset_circuit",
        "emit_proposal",
        "rebalance_scan",
    }
)
MUTATING_TYPES = frozenset(
    {
        "pause_entries",
        "resume_entries",
        "reset_circuit",
        "emit_proposal",
        "rebalance_scan",
    }
)


def noop_plan(
    role: str,
    *,
    summary: str = "noop",
    llm_error: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": ACTION_PLAN_SCHEMA_VERSION,
        "role": role if role in ROLES else "supervisor",
        "summary": (summary or "noop")[:500],
        "severity": "info",
        "actions": [{"type": "noop", "reason": "noop", "confidence": 1.0, "params": {}}],
        "proposal": None,
    }
    if llm_error:
        out["llm_error"] = str(llm_error)[:500]
    return out


def validate_action_plan(raw: Any, *, expected_role: str | None = None) -> dict[str, Any]:
    """
    Returns { ok, plan?, errors: list[str] }.

    Unknown action types are dropped (recorded in errors) without failing the plan
    when at least one valid action remains. Missing/invalid proposal with
    emit_proposal fails the plan.
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {"ok": False, "errors": ["root must be a JSON object"]}

    try:
        ver = int(raw.get("schema_version", ACTION_PLAN_SCHEMA_VERSION))
    except (TypeError, ValueError):
        return {"ok": False, "errors": ["schema_version invalid"]}
    if ver != ACTION_PLAN_SCHEMA_VERSION:
        return {"ok": False, "errors": [f"schema_version must be {ACTION_PLAN_SCHEMA_VERSION}"]}

    role = str(raw.get("role", "")).strip().lower()
    if role not in ROLES:
        return {"ok": False, "errors": ["role must be supervisor|fund_manager"]}
    if expected_role and role != expected_role:
        return {"ok": False, "errors": [f"role must be {expected_role}"]}

    severity = str(raw.get("severity", "info")).strip().lower()
    if severity not in SEVERITIES:
        return {"ok": False, "errors": ["severity must be info|warning|critical"]}

    summary = str(raw.get("summary") or "")[:500]

    actions_in = raw.get("actions")
    if actions_in is None:
        actions_in = []
    if not isinstance(actions_in, list):
        return {"ok": False, "errors": ["actions must be a list"]}

    seen_mutating: set[str] = set()
    mutating_count = 0
    actions: list[dict[str, Any]] = []
    for i, a in enumerate(actions_in):
        if len(actions) >= MAX_ACTIONS:
            break
        if not isinstance(a, dict):
            errors.append(f"actions[{i}] must be an object")
            continue
        at = str(a.get("type", "")).strip().lower()
        if at not in ACTION_TYPES:
            errors.append(f"actions[{i}]: unknown type {at!r}")
            continue
        if at in MUTATING_TYPES:
            if at in seen_mutating:
                errors.append(f"actions[{i}]: duplicate mutating type {at}")
                continue
            if mutating_count >= MAX_MUTATING_ACTIONS:
                errors.append(f"actions[{i}]: at most {MAX_MUTATING_ACTIONS} mutating action(s)")
                continue
            seen_mutating.add(at)
            mutating_count += 1
        try:
            conf = float(a.get("confidence", 0.0))
        except (TypeError, ValueError):
            errors.append(f"actions[{i}]: confidence invalid")
            continue
        if not (0.0 <= conf <= 1.0):
            errors.append(f"actions[{i}]: confidence must be in [0, 1]")
            continue
        raw_params = a.get("params")
        if raw_params is None:
            params: dict[str, Any] = {}
        elif not isinstance(raw_params, dict):
            errors.append(f"actions[{i}]: params must be an object or omitted")
            continue
        elif raw_params:
            # Action params are unused today; reject free-form keys (proposal lives on plan).
            errors.append(f"actions[{i}]: params must be empty (use plan.proposal for overlays)")
            continue
        else:
            params = {}
        actions.append(
            {
                "type": at,
                "reason": str(a.get("reason") or "")[:500],
                "confidence": conf,
                "params": params,
            }
        )

    if not actions:
        actions = [{"type": "noop", "reason": "no valid actions", "confidence": 1.0, "params": {}}]

    proposal_out: dict[str, Any] | None = None
    prop_raw = raw.get("proposal")
    wants_proposal = any(a["type"] == "emit_proposal" for a in actions)

    if wants_proposal:
        if not isinstance(prop_raw, dict):
            return {"ok": False, "errors": ["emit_proposal requires a valid proposal object"]}
        prop = dict(prop_raw)
        prop.setdefault("schema_version", 1)
        v = validate_llm_proposal(prop)
        if not v.get("ok"):
            return {
                "ok": False,
                "errors": [f"proposal: {e}" for e in (v.get("errors") or ["invalid"])],
            }
        proposal_out = {
            "schema_version": 1,
            "params": v.get("coerced_params") or {},
            "rationale": str(v.get("rationale") or "")[:500],
            "risk_notes": str(v.get("risk_notes") or "")[:500],
        }
    elif prop_raw is not None:
        if not isinstance(prop_raw, dict):
            return {"ok": False, "errors": ["proposal must be an object or null"]}
        prop = dict(prop_raw)
        prop.setdefault("schema_version", 1)
        v = validate_llm_proposal(prop)
        if not v.get("ok"):
            return {
                "ok": False,
                "errors": [f"proposal: {e}" for e in (v.get("errors") or ["invalid"])],
            }
        proposal_out = {
            "schema_version": 1,
            "params": v.get("coerced_params") or {},
            "rationale": str(v.get("rationale") or "")[:500],
            "risk_notes": str(v.get("risk_notes") or "")[:500],
        }

    plan = {
        "schema_version": ACTION_PLAN_SCHEMA_VERSION,
        "role": role,
        "summary": summary or "agent plan",
        "severity": severity,
        "actions": actions,
        "proposal": proposal_out,
    }
    return {"ok": True, "plan": plan, "errors": errors}

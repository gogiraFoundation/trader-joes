"""Supervisor agent: ops posture (circuit, halt, heartbeat) — advisory + allowlisted control."""

from __future__ import annotations

import json
import logging
from typing import Any

from tr_bot import config
from tr_bot.agents.llm_client import LLMClientError, chat_json
from tr_bot.agents.schema import noop_plan, validate_action_plan

logger = logging.getLogger("tr_bot.agents.supervisor")

_SYSTEM = """You are the ops Supervisor for a paper/live crypto trading bot (tr_bot).
You advise and may propose allowlisted control actions. You NEVER open or close trades.
You NEVER invent buy/sell signals. The statistical/SMC engine owns entries; ExitCoordinator owns exits.

Return a single JSON object:
{
  "schema_version": 1,
  "role": "supervisor",
  "summary": "string <= 500 chars",
  "severity": "info|warning|critical",
  "actions": [
    {"type": "noop|notify|pause_entries|resume_entries|reset_circuit|emit_proposal|rebalance_scan",
     "reason": "string", "confidence": 0.0-1.0, "params": {}}
  ],
  "proposal": null
}

Rules:
- Max 3 actions; at most one mutating action; action params must be {}.
- Prefer notify + pause_entries when circuit is tripped or losses are streaking.
- Do NOT reset_circuit unless losses have clearly stopped and severity warrants it; prefer leaving it tripped.
- resume_entries only to clear an agents_supervisor_pause, never to override an operator halt.
- emit_proposal is usually for the fund_manager role; use noop/notify instead when unsure.
- If everything looks healthy, return severity info and a single noop.
"""


def plan_supervisor(context: dict[str, Any]) -> dict[str, Any]:
    """Call LLM and return { ok, plan, errors?, llm_error? }."""
    model = (getattr(config, "AGENTS_MODEL", None) or "").strip() or config.LLM_MODEL
    timeout = float(getattr(config, "AGENTS_TIMEOUT_SECONDS", 30.0))
    user = (
        "Given this ops context JSON, produce an ActionPlan.\n\n"
        + json.dumps(context, default=str)[:14000]
    )
    try:
        raw = chat_json(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            model=model,
            timeout=timeout,
        )
    except LLMClientError as e:
        logger.warning("supervisor LLM failed: %s", e)
        plan = noop_plan("supervisor", summary="LLM unavailable", llm_error=str(e))
        return {"ok": True, "plan": plan, "errors": [], "llm_error": str(e)}
    except Exception as e:
        logger.warning("supervisor LLM unexpected: %s", e)
        plan = noop_plan("supervisor", summary="LLM unexpected error", llm_error=str(e))
        return {"ok": True, "plan": plan, "errors": [], "llm_error": str(e)}

    # Force role if model drifted
    if isinstance(raw, dict):
        raw.setdefault("role", "supervisor")
        raw["role"] = "supervisor"
        raw.setdefault("schema_version", 1)

    v = validate_action_plan(raw, expected_role="supervisor")
    if not v.get("ok") or not v.get("plan"):
        plan = noop_plan(
            "supervisor",
            summary="invalid plan from model",
            llm_error="; ".join(v.get("errors") or ["validate failed"]),
        )
        return {"ok": True, "plan": plan, "errors": v.get("errors") or [], "llm_error": plan.get("llm_error")}
    return {"ok": True, "plan": v["plan"], "errors": v.get("errors") or []}

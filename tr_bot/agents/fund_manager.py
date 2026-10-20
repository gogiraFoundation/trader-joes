"""Fund manager agent: capital posture — advisory + allowlisted control."""

from __future__ import annotations

import json
import logging
from typing import Any

from tr_bot import config
from tr_bot.agents.llm_client import LLMClientError, chat_json
from tr_bot.agents.schema import noop_plan, validate_action_plan

logger = logging.getLogger("tr_bot.agents.fund_manager")

_SYSTEM = """You are the Fund Manager advisor for a crypto trading bot (tr_bot).
You assess NAV, drawdown, fee drag, and loss streaks. You may propose allowlisted
strategy param overlays or pause entries. You NEVER open trades or size positions.
You NEVER call rebalance trim — only rebalance_scan is allowed.

Return a single JSON object:
{
  "schema_version": 1,
  "role": "fund_manager",
  "summary": "string <= 500 chars",
  "severity": "info|warning|critical",
  "actions": [
    {"type": "noop|notify|pause_entries|resume_entries|reset_circuit|emit_proposal|rebalance_scan",
     "reason": "string", "confidence": 0.0-1.0, "params": {}}
  ],
  "proposal": null | {
    "schema_version": 1,
    "params": { allowlisted overlay keys only },
    "rationale": "string",
    "risk_notes": "string"
  }
}

Allowlisted proposal keys (subset): REGIME_MIN_CONFIDENCE, SIGNAL_MIN_ADX_MARGIN,
SIGNAL_MIN_RSI_DISTANCE, SIGNAL_RSI_OVERSOLD, SIGNAL_RSI_OVERBOUGHT,
ADX_MAX_FOR_MEAN_REVERSION, ADX_MIN_FOR_TREND.

Rules:
- After consecutive losses / circuit trip: prefer notify or pause_entries; tighten filters via emit_proposal rather than increasing risk.
- Do not recommend loosening risk or disabling filters after a losing streak.
- rebalance_scan only when portfolio drift/lag evidence is present in context.
- Max 3 actions; at most one mutating action; action params must be {}.
- If healthy, return severity info and noop.
"""


def plan_fund_manager(context: dict[str, Any]) -> dict[str, Any]:
    """Call LLM and return { ok, plan, errors?, llm_error? }."""
    model = (getattr(config, "AGENTS_MODEL", None) or "").strip() or config.LLM_MODEL
    timeout = float(getattr(config, "AGENTS_TIMEOUT_SECONDS", 30.0))
    user = (
        "Given this fund context JSON, produce an ActionPlan.\n\n"
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
        logger.warning("fund_manager LLM failed: %s", e)
        plan = noop_plan("fund_manager", summary="LLM unavailable", llm_error=str(e))
        return {"ok": True, "plan": plan, "errors": [], "llm_error": str(e)}
    except Exception as e:
        logger.warning("fund_manager LLM unexpected: %s", e)
        plan = noop_plan("fund_manager", summary="LLM unexpected error", llm_error=str(e))
        return {"ok": True, "plan": plan, "errors": [], "llm_error": str(e)}

    if isinstance(raw, dict):
        raw.setdefault("role", "fund_manager")
        raw["role"] = "fund_manager"
        raw.setdefault("schema_version", 1)

    v = validate_action_plan(raw, expected_role="fund_manager")
    if not v.get("ok") or not v.get("plan"):
        plan = noop_plan(
            "fund_manager",
            summary="invalid plan from model",
            llm_error="; ".join(v.get("errors") or ["validate failed"]),
        )
        return {
            "ok": True,
            "plan": plan,
            "errors": v.get("errors") or [],
            "llm_error": plan.get("llm_error"),
        }
    return {"ok": True, "plan": v["plan"], "errors": v.get("errors") or []}

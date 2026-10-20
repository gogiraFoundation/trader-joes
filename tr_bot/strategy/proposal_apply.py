"""Apply a validated LLM proposal through the same isolated backtest gate as adaptation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot.strategy.effective_params import effective_params_from_config_and_profile
from tr_bot.strategy.no_trade_adaptation import apply_overlay_candidate_after_backtest, overlay_params_subset
from tr_bot.strategy.proposal_validate import validate_llm_proposal


def _merge_params(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    out.update(delta)
    return out


def apply_llm_proposal_from_dict(
    raw: dict[str, Any],
    *,
    proposal_only: bool | None = None,
) -> dict[str, Any]:
    v = validate_llm_proposal(raw)
    if not v.get("ok"):
        return v

    delta = v.get("coerced_params") or {}
    if not delta:
        return {"ok": False, "errors": ["params object is empty after validation"]}

    base = effective_params_from_config_and_profile()
    candidate = overlay_params_subset(_merge_params(base, delta))
    profile_id = f"proposal_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    hyp_raw = v.get("rationale") or "LLM proposal"
    hypothesis = str(hyp_raw)[:500]

    try:
        from tr_bot.research.hypothesis_registry import append_hypothesis

        append_hypothesis(
            description=hypothesis,
            category="llm_proposal",
            source="proposal_apply",
            meta={"proposed_keys": list(delta.keys())},
        )
    except Exception:
        pass

    extra = {
        "rationale": v.get("rationale"),
        "risk_notes": v.get("risk_notes"),
        "proposed_keys": list(delta.keys()),
    }
    return apply_overlay_candidate_after_backtest(
        profile_id,
        candidate,
        source="llm_proposal",
        extra_meta=extra,
        hypothesis=hypothesis,
        promotion_action="strategy_proposal",
        proposal_only=proposal_only,
    )


def apply_llm_proposal_from_path(
    path: Path,
    *,
    proposal_only: bool | None = None,
) -> dict[str, Any]:
    try:
        raw_file = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        return {"ok": False, "errors": [f"read failed: {e}"]}
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [f"invalid json: {e}"]}
    if not isinstance(raw_file, dict):
        return {"ok": False, "errors": ["root must be object"]}
    return apply_llm_proposal_from_dict(raw_file, proposal_only=proposal_only)

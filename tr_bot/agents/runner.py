"""Orchestrate supervisor + fund manager agent job."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.agents.context import build_fund_context, build_ops_context
from tr_bot.agents.execute import execute_plan
from tr_bot.agents.fund_manager import plan_fund_manager
from tr_bot.agents.schema import noop_plan
from tr_bot.agents.supervisor import plan_supervisor
from tr_bot.managers.security import safe_artifact_path
from tr_bot.notifications import events as notify_events
from tr_bot.notifications import notify

logger = logging.getLogger("tr_bot.agents.runner")


def _agents_root() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    d = safe_artifact_path(root, "agents")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _append_actions_jsonl(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path = safe_artifact_path(_agents_root(), "actions.jsonl")
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def _effective_dry_run(cli_dry_run: bool | None) -> bool:
    if cli_dry_run is True:
        return True
    if cli_dry_run is False:
        # Explicit CLI --execute still respects AGENTS_DRY_RUN master safety default
        # unless AGENTS_DRY_RUN is false.
        return bool(getattr(config, "AGENTS_DRY_RUN", True))
    return bool(getattr(config, "AGENTS_DRY_RUN", True))


def read_agents_dashboard_payload() -> dict[str, Any]:
    """Compact last-run artifacts for dashboard summary."""
    root = _agents_root()
    out: dict[str, Any] = {
        "enabled": bool(getattr(config, "AGENTS_ENABLED", False)),
        "dry_run": bool(getattr(config, "AGENTS_DRY_RUN", True)),
        "supervisor_last": None,
        "fund_manager_last": None,
    }
    for key, name in (
        ("supervisor_last", "supervisor_last.json"),
        ("fund_manager_last", "fund_manager_last.json"),
    ):
        path = safe_artifact_path(root, name)
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else raw
                out[key] = {
                    "updated_utc": raw.get("updated_utc"),
                    "severity": plan.get("severity") if isinstance(plan, dict) else None,
                    "summary": plan.get("summary") if isinstance(plan, dict) else None,
                    "role": plan.get("role") if isinstance(plan, dict) else None,
                    "dry_run": raw.get("dry_run"),
                    "llm_error": raw.get("llm_error") or (plan.get("llm_error") if isinstance(plan, dict) else None),
                }
        except (OSError, json.JSONDecodeError) as e:
            out[key] = {"error": str(e)}
    return out


def run_agents_job(
    *,
    role: str = "both",
    dry_run: bool | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Run supervisor and/or fund manager agents.

    Always returns ok=True for scheduler safety unless a local I/O failure is fatal.
    LLM failures become noop plans with llm_error set.
    """
    now = datetime.now(timezone.utc).isoformat()
    enabled = bool(getattr(config, "AGENTS_ENABLED", False))
    eff_dry = _effective_dry_run(dry_run)
    # force may bypass AGENTS_ENABLED only for dry-run briefs; never for mutating runs.
    if not enabled and not (force and eff_dry):
        return {"ok": True, "skipped": "AGENTS_ENABLED_false", "updated_utc": now}

    roles = (role or "both").strip().lower()
    want_sup = roles in ("both", "supervisor")
    want_fund = roles in ("both", "fund_manager")

    results: dict[str, Any] = {
        "ok": True,
        "updated_utc": now,
        "dry_run": eff_dry,
        "roles": [],
    }

    all_action_rows: list[dict[str, Any]] = []

    if want_sup:
        ctx = build_ops_context()
        planned = plan_supervisor(ctx)
        plan = planned.get("plan") or noop_plan("supervisor")
        exec_rows = execute_plan(plan, dry_run=eff_dry)
        all_action_rows.extend(exec_rows)
        artifact = {
            "updated_utc": now,
            "dry_run": eff_dry,
            "plan": plan,
            "execution": exec_rows,
            "errors": planned.get("errors") or [],
            "llm_error": planned.get("llm_error"),
        }
        path = safe_artifact_path(_agents_root(), "supervisor_last.json")
        _write_json(path, artifact)
        results["roles"].append(
            {
                "role": "supervisor",
                "path": str(path),
                "severity": plan.get("severity"),
                "summary": plan.get("summary"),
                "llm_error": planned.get("llm_error"),
                "execution": exec_rows,
            }
        )

    if want_fund:
        ctx = build_fund_context()
        planned = plan_fund_manager(ctx)
        plan = planned.get("plan") or noop_plan("fund_manager")
        exec_rows = execute_plan(plan, dry_run=eff_dry)
        all_action_rows.extend(exec_rows)
        artifact = {
            "updated_utc": now,
            "dry_run": eff_dry,
            "plan": plan,
            "execution": exec_rows,
            "errors": planned.get("errors") or [],
            "llm_error": planned.get("llm_error"),
        }
        path = safe_artifact_path(_agents_root(), "fund_manager_last.json")
        _write_json(path, artifact)
        results["roles"].append(
            {
                "role": "fund_manager",
                "path": str(path),
                "severity": plan.get("severity"),
                "summary": plan.get("summary"),
                "llm_error": planned.get("llm_error"),
                "execution": exec_rows,
            }
        )

    _append_actions_jsonl(all_action_rows)

    # Brief notification (throttled by notification manager)
    try:
        summaries = [
            f"{r.get('role')}:{r.get('severity')}:{r.get('summary')}"
            for r in results["roles"]
        ]
        notify(
            notify_events.AGENTS_BRIEF,
            dry_run=eff_dry,
            summaries=summaries[:4],
        )
    except Exception as e:
        logger.warning("agents brief notify failed: %s", e)

    return results

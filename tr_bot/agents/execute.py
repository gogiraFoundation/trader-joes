"""Gated execution of validated agent actions."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from tr_bot import config
from tr_bot.agents.schema import MUTATING_TYPES
from tr_bot.circuit_breaker import read_circuit_state_snapshot, reset_for_api
from tr_bot.managers.fund import FundManager
from tr_bot.managers.security import safe_artifact_path
from tr_bot.notifications import events as notify_events
from tr_bot.notifications import notify
from tr_bot.strategy.proposal_apply import apply_llm_proposal_from_dict

logger = logging.getLogger("tr_bot.agents.execute")


def _agents_root() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    d = safe_artifact_path(root, "agents")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cooldown_path() -> Path:
    return safe_artifact_path(_agents_root(), "cooldown_state.json")


def _cooldown_lock_path() -> Path:
    return safe_artifact_path(_agents_root(), "cooldown_state.json.lock")


@contextmanager
def _cooldown_lock() -> Iterator[None]:
    lock_path = _cooldown_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def _read_last_mutate_mono() -> float:
    path = _cooldown_path()
    if not path.is_file():
        return 0.0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return float(raw.get("last_mutate_unix", 0.0) or 0.0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def _write_last_mutate() -> None:
    path = _cooldown_path()
    payload = {
        "last_mutate_unix": time.time(),
        "updated_utc": datetime.now(timezone.utc).isoformat(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _cooldown_blocks() -> bool:
    cool = float(getattr(config, "AGENTS_COOLDOWN_SECONDS", 3600.0) or 0.0)
    if cool <= 0:
        return False
    last = _read_last_mutate_mono()
    if last <= 0:
        return False
    return (time.time() - last) < cool


def _flag_for_action(action_type: str) -> bool:
    """Per-action auto flags. emit_proposal requires AGENTS_AUTO_EMIT_PROPOSAL."""
    if action_type in ("notify", "noop"):
        return True
    mapping = {
        "pause_entries": bool(getattr(config, "AGENTS_AUTO_PAUSE", False)),
        "resume_entries": bool(getattr(config, "AGENTS_AUTO_RESUME", False)),
        "reset_circuit": bool(getattr(config, "AGENTS_AUTO_RESET_CIRCUIT", False)),
        "emit_proposal": bool(getattr(config, "AGENTS_AUTO_EMIT_PROPOSAL", False)),
        "rebalance_scan": bool(getattr(config, "AGENTS_AUTO_REBALANCE_SCAN", False)),
    }
    return bool(mapping.get(action_type, False))


def _fund_dd_halted(fund: FundManager) -> bool:
    st = fund.get_state()
    if st is None:
        return False
    if st.halted and "max_drawdown" in (st.halt_reason or "").lower():
        return True
    return False


def execute_action(
    action: dict[str, Any],
    *,
    plan: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    """Execute one action; returns result dict with status fields."""
    at = str(action.get("type") or "noop")
    reason = str(action.get("reason") or "")
    conf = float(action.get("confidence") or 0.0)
    min_conf = float(getattr(config, "AGENTS_MIN_CONFIDENCE", 0.7))
    role = str(plan.get("role") or "")
    ts = datetime.now(timezone.utc).isoformat()

    base: dict[str, Any] = {
        "ts": ts,
        "role": role,
        "action": at,
        "reason": reason,
        "confidence": conf,
        "dry_run": dry_run,
    }

    if at == "noop":
        return {**base, "ok": True, "skipped": "noop"}

    if at in MUTATING_TYPES and conf < min_conf:
        return {**base, "ok": True, "skipped": "low_confidence"}

    if dry_run:
        return {**base, "ok": True, "would_execute": True, "skipped": "dry_run"}

    if at in MUTATING_TYPES and not _flag_for_action(at):
        return {**base, "ok": True, "skipped": "flag_off"}

    if at in MUTATING_TYPES:
        # Claim cooldown under lock only — never hold the flock across backtest/scan I/O.
        with _cooldown_lock():
            if _cooldown_blocks():
                return {**base, "ok": True, "skipped": "cooldown"}
            _write_last_mutate()
        return _execute_mutating(
            at,
            base=base,
            plan=plan,
            reason=reason,
            role=role,
            record_cooldown=False,
        )

    try:
        if at == "notify":
            notify(
                notify_events.AGENTS_ACTION,
                role=role,
                action=at,
                summary=plan.get("summary"),
                reason=reason,
                severity=plan.get("severity"),
            )
            return {**base, "ok": True, "executed": True}
        return {**base, "ok": False, "error": f"unknown_action:{at}"}
    except Exception as e:
        logger.exception("execute_action %s failed", at)
        return {**base, "ok": False, "error": str(e)}


def _execute_mutating(
    at: str,
    *,
    base: dict[str, Any],
    plan: dict[str, Any],
    reason: str,
    role: str,
    record_cooldown: bool = True,
) -> dict[str, Any]:
    """Run one mutating action. Cooldown may already be claimed by the caller."""

    def _maybe_record() -> None:
        if record_cooldown:
            with _cooldown_lock():
                _write_last_mutate()

    try:
        if at == "pause_entries":
            if not bool(getattr(config, "AGENTS_AUTO_PAUSE", False)):
                return {**base, "ok": True, "skipped": "flag_off"}
            halt_reason = str(getattr(config, "AGENTS_HALT_REASON", "agents_supervisor_pause"))
            fund = FundManager()
            if not fund.set_manual_halt_if_inactive(halt_reason, owner="agent"):
                return {
                    **base,
                    "ok": True,
                    "skipped": "halt_already_active",
                    "halt_reason": fund.manual_halt_reason_text(),
                    "halt_owner": fund.manual_halt_owner(),
                }
            _maybe_record()
            notify(
                notify_events.AGENTS_ACTION,
                role=role,
                action=at,
                reason=halt_reason,
            )
            return {**base, "ok": True, "executed": True, "halt_reason": halt_reason}

        if at == "resume_entries":
            if not bool(getattr(config, "AGENTS_AUTO_RESUME", False)):
                return {**base, "ok": True, "skipped": "flag_off"}
            fund = FundManager()
            if _fund_dd_halted(fund):
                return {**base, "ok": True, "skipped": "fund_dd_halted"}
            snap = read_circuit_state_snapshot()
            if snap.get("tripped") is True:
                return {**base, "ok": True, "skipped": "circuit_tripped"}
            halt_reason = str(getattr(config, "AGENTS_HALT_REASON", "agents_supervisor_pause"))
            cleared = fund.clear_manual_halt_if_agent(halt_reason)
            if cleared:
                _maybe_record()
            notify(
                notify_events.AGENTS_ACTION,
                role=role,
                action=at,
                cleared=cleared,
            )
            return {**base, "ok": True, "executed": cleared, "cleared": cleared}

        if at == "reset_circuit":
            if not bool(getattr(config, "AGENTS_AUTO_RESET_CIRCUIT", False)):
                return {**base, "ok": True, "skipped": "flag_off"}
            fund = FundManager()
            if _fund_dd_halted(fund):
                return {**base, "ok": True, "skipped": "fund_dd_halted"}
            snap = read_circuit_state_snapshot()
            if snap.get("tripped") is not True:
                n = snap.get("consecutive_losses")
                m = snap.get("max_consecutive_losses") or config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES
                if n is None or int(n) < int(m):
                    return {**base, "ok": True, "skipped": "circuit_not_tripped"}
            reset_for_api()
            _maybe_record()
            notify(notify_events.AGENTS_ACTION, role=role, action=at, reason=reason)
            return {**base, "ok": True, "executed": True}

        if at == "emit_proposal":
            if not bool(getattr(config, "AGENTS_AUTO_EMIT_PROPOSAL", False)):
                return {**base, "ok": True, "skipped": "flag_off"}
            prop = plan.get("proposal")
            if not isinstance(prop, dict) or not prop.get("params"):
                return {**base, "ok": False, "error": "missing_proposal"}
            proposals_dir = safe_artifact_path(_agents_root(), "proposals")
            proposals_dir.mkdir(parents=True, exist_ok=True)
            fname = f"proposal_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
            path = safe_artifact_path(proposals_dir, fname)
            path.write_text(json.dumps(prop, indent=2), encoding="utf-8")
            applied = False
            apply_out: dict[str, Any] | None = None
            auto = bool(getattr(config, "AGENTS_AUTO_APPLY_PROPOSAL", False))
            tier = str(config.DEPLOYMENT_TIER or "paper")
            if auto and tier == "paper":
                # AGENTS_AUTO_APPLY_PROPOSAL alone controls apply (not ADAPTATION_AUTO_APPLY_PAPER).
                apply_out = apply_llm_proposal_from_dict(prop, proposal_only=False)
                applied = bool(apply_out.get("ok")) and not apply_out.get("errors")
            _maybe_record()
            notify(
                notify_events.AGENTS_ACTION,
                role=role,
                action=at,
                path=str(path),
                applied=applied,
            )
            return {
                **base,
                "ok": True,
                "executed": True,
                "path": str(path),
                "applied": applied,
                "apply_result": apply_out,
            }

        if at == "rebalance_scan":
            if not bool(getattr(config, "AGENTS_AUTO_REBALANCE_SCAN", False)):
                return {**base, "ok": True, "skipped": "flag_off"}
            from tr_bot.managers.rebalancer import scan_job

            # Agents never execute trims — force the scan artifact flag off.
            out = scan_job(force_execute_trims=False)
            _maybe_record()
            notify(
                notify_events.AGENTS_ACTION,
                role=role,
                action=at,
                scan_ok=out.get("ok"),
            )
            return {**base, "ok": True, "executed": True, "scan": out}

        return {**base, "ok": False, "error": f"unknown_action:{at}"}
    except Exception as e:
        logger.exception("execute_action %s failed", at)
        return {**base, "ok": False, "error": str(e)}


def execute_plan(plan: dict[str, Any], *, dry_run: bool) -> list[dict[str, Any]]:
    """Execute plan actions; at most one mutating action is processed per run."""
    results: list[dict[str, Any]] = []
    mutating_done = False
    for action in plan.get("actions") or []:
        if not isinstance(action, dict):
            continue
        at = str(action.get("type") or "noop")
        if at in MUTATING_TYPES and mutating_done:
            results.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "role": str(plan.get("role") or ""),
                    "action": at,
                    "reason": str(action.get("reason") or ""),
                    "confidence": float(action.get("confidence") or 0.0),
                    "dry_run": dry_run,
                    "ok": True,
                    "skipped": "max_mutating_actions",
                }
            )
            continue
        row = execute_action(action, plan=plan, dry_run=dry_run)
        results.append(row)
        if at in MUTATING_TYPES:
            mutating_done = True
    return results

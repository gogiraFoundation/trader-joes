"""strategy-evaluate / strategy-promote / strategy-status implementations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.notifications import events as notify_events
from tr_bot.notifications.facade import notify
from tr_bot.strategy import deployment_io as dep
from tr_bot.promotion import gates
from tr_bot.promotion import log as promotion_log

logger = logging.getLogger("tr_bot.promotion.service")


def _root() -> Path:
    return Path(config.ARTIFACTS_DIR).resolve()


def strategy_status() -> dict[str, Any]:
    root = _root()
    pointers: dict[str, Any] = {}
    for t in ("paper", "live_canary", "live_full"):
        pointers[t] = dep.read_pointer(root, t)
    return {
        "deployment_tier": config.DEPLOYMENT_TIER,
        "active_strategy_profile_id": config.ACTIVE_STRATEGY_PROFILE_ID or None,
        "pointers": pointers,
        "promotions_frozen": gates.promotions_frozen(root),
        "promotion_log_tail": promotion_log.read_promotion_log_tail(root, 30),
    }


def strategy_evaluate(
    profile_id: str | None = None,
    tier: str | None = None,
) -> dict[str, Any]:
    root = _root()
    pid = (profile_id or "").strip() or (config.ACTIVE_STRATEGY_PROFILE_ID or None)
    t = (tier or config.DEPLOYMENT_TIER or "paper").strip()
    profile_hash = None
    if pid:
        prof_doc = dep.read_json_file(dep.strategy_profile_path(root, pid))
        if isinstance(prof_doc, dict):
            profile_hash = prof_doc.get("config_hash")
    report = gates.run_gates(root, profile_id=pid, tier=t, profile_hash=profile_hash)
    return {
        "profile_id": pid,
        "tier": t,
        "passed": report.all_passed,
        "outcomes": [
            {"name": o.name, "passed": o.passed, "detail": o.detail} for o in report.outcomes
        ],
        "input_hash": report.input_hash,
        "benchmark_note": report.benchmark_note,
    }


def strategy_promote(
    to_tier: str,
    profile_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = _root()
    pid = profile_id.strip()
    if to_tier not in dep.POINTER_NAMES:
        return {"ok": False, "reason": f"invalid tier: {to_tier}"}
    if not pid:
        return {"ok": False, "reason": "empty profile_id"}

    prof_path = dep.strategy_profile_path(root, pid)
    if not prof_path.is_file():
        return {"ok": False, "reason": "profile_file_missing", "path": str(prof_path)}

    prof_doc = dep.read_json_file(prof_path)
    profile_hash = prof_doc.get("config_hash") if isinstance(prof_doc, dict) else None

    if gates.promotions_frozen(root):
        out = {"ok": False, "reason": "promotions_frozen"}
        _append_log(root, out, pid, to_tier, passed=False)
        notify(
            notify_events.PROMOTION_REJECTED,
            reason="frozen",
            to_tier=to_tier,
            profile_id=pid,
        )
        return out

    report = gates.run_gates(root, profile_id=pid, tier=to_tier, profile_hash=profile_hash)
    if not report.all_passed:
        out = {
            "ok": False,
            "reason": "gates_failed",
            "report": strategy_evaluate(profile_id=pid, tier=to_tier),
        }
        _append_log(root, out, pid, to_tier, passed=False)
        notify(
            notify_events.PROMOTION_REJECTED,
            reason="gates_failed",
            to_tier=to_tier,
            profile_id=pid,
            outcomes=out["report"]["outcomes"],
        )
        return out

    comparator_result: dict[str, Any] | None = None
    if bool(getattr(config, "PROMOTION_REQUIRE_COMPARATOR", False)):
        from tr_bot.promotion.comparator import comparator_passes, compare_engines

        comparator_result = compare_engines(write_artifacts=True)
        if not comparator_passes(comparator_result):
            out = {
                "ok": False,
                "reason": "comparator_failed",
                "comparator": comparator_result,
                "report": strategy_evaluate(profile_id=pid, tier=to_tier),
            }
            _append_log(root, out, pid, to_tier, passed=False)
            notify(
                notify_events.PROMOTION_REJECTED,
                reason="comparator_failed",
                to_tier=to_tier,
                profile_id=pid,
            )
            return out

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "profile_id": pid,
            "to_tier": to_tier,
            "report": strategy_evaluate(profile_id=pid, tier=to_tier),
            "comparator": comparator_result,
        }

    now = datetime.now(timezone.utc).isoformat()
    ptr = {"profile_id": pid, "activated_at": now}
    try:
        dep.atomic_write_json(dep.pointer_path(root, to_tier), ptr)
    except OSError as e:
        logger.exception("promote pointer write failed")
        return {"ok": False, "reason": "pointer_write_failed", "error": str(e)}

    out = {
        "ok": True,
        "profile_id": pid,
        "to_tier": to_tier,
        "activated_at": now,
        "input_hash": report.input_hash,
    }
    _append_log(root, out, pid, to_tier, passed=True)
    notify(
        notify_events.PROMOTION_PASSED,
        to_tier=to_tier,
        profile_id=pid,
        input_hash=report.input_hash,
    )
    return out


def strategy_revert_to_last_good(to_tier: str) -> dict[str, Any]:
    """Read promotion log and restore last successful profile pointer for tier."""
    root = _root()
    last = promotion_log.find_last_success_profile(root)
    if not last:
        return {"ok": False, "reason": "no_success_in_log"}
    prof_path = dep.strategy_profile_path(root, last)
    if not prof_path.is_file():
        return {"ok": False, "reason": "profile_file_missing", "profile_id": last}
    now = datetime.now(timezone.utc).isoformat()
    ptr = {"profile_id": last, "activated_at": now, "reverted": True}
    dep.atomic_write_json(dep.pointer_path(root, to_tier), ptr)
    promotion_log.append_promotion_event(
        root,
        {
            "action": "revert",
            "utc": now,
            "to_tier": to_tier,
            "profile_id": last,
            "passed": True,
        },
    )
    notify(notify_events.PROMOTION_REVERTED, to_tier=to_tier, profile_id=last)
    return {"ok": True, "profile_id": last, "to_tier": to_tier}


def _append_log(
    root: Path,
    payload: dict[str, Any],
    profile_id: str,
    to_tier: str,
    *,
    passed: bool,
) -> None:
    line = {
        "action": "promote",
        "utc": datetime.now(timezone.utc).isoformat(),
        "profile_id": profile_id,
        "to_tier": to_tier,
        "passed": passed,
        "payload": payload,
    }
    promotion_log.append_promotion_event(root, line)

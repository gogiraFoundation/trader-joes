"""Write actions for the dashboard (Phase 3): gated, audited, best-effort-safe.

Disabled by default (`DASHBOARD_WRITES_ENABLED=false`). When enabled, every
route here additionally requires a bearer/`X-Dashboard-Token` matching
`DASHBOARD_WRITE_TOKEN` (falls back to `DASHBOARD_TOKEN` if unset) -- the same
header shapes accepted by the read-only dashboard token.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from tr_bot import config
from tr_bot.circuit_breaker import reset_for_api
from tr_bot.dashboard_jobs import JobAlreadyRunningError, get_current_job, get_job, start_job

logger = logging.getLogger("tr_bot.dashboard_actions")

router = APIRouter(prefix="/api/dashboard", tags=["dashboard-actions"])


def require_dashboard_writes(
    authorization: str | None = Header(None),
    x_dashboard_token: str | None = Header(None, alias="X-Dashboard-Token"),
) -> None:
    if not config.DASHBOARD_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Dashboard write actions are disabled (set DASHBOARD_WRITES_ENABLED=true)",
        )
    expected = (config.DASHBOARD_WRITE_TOKEN or config.DASHBOARD_TOKEN or "").strip()
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Dashboard write actions require DASHBOARD_WRITE_TOKEN or DASHBOARD_TOKEN when enabled",
        )
    got: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        got = authorization[7:].strip()
    elif x_dashboard_token:
        got = x_dashboard_token.strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing dashboard write token")


class PauseRequest(BaseModel):
    reason: str = ""


class TrainingRunRequest(BaseModel):
    promote: bool | None = None


class PromoteRequest(BaseModel):
    to_tier: str
    profile_id: str
    dry_run: bool = True
    confirm: bool = False


@router.post("/actions/circuit-reset")
def action_circuit_reset(_: None = Depends(require_dashboard_writes)) -> dict[str, Any]:
    reset_for_api()
    return {
        "ok": True,
        "message": "Reset requested; the running engine picks it up on its next loop.",
    }


@router.post("/actions/trading-pause")
def action_trading_pause(
    body: PauseRequest = PauseRequest(),
    _: None = Depends(require_dashboard_writes),
) -> dict[str, Any]:
    from tr_bot.managers.fund import FundManager

    FundManager().set_manual_halt(body.reason or "manual pause via dashboard")
    logger.info("Dashboard trading-pause requested (reason=%r)", body.reason)
    return {"ok": True, "halted": True}


@router.post("/actions/trading-resume")
def action_trading_resume(_: None = Depends(require_dashboard_writes)) -> dict[str, Any]:
    from tr_bot.managers.fund import FundManager

    FundManager().clear_manual_halt()
    logger.info("Dashboard trading-resume requested")
    return {"ok": True, "halted": False}


@router.post("/actions/backtest-run")
def action_backtest_run(_: None = Depends(require_dashboard_writes)) -> dict[str, Any]:
    from tr_bot.backtest.runner import run_backtest_job

    try:
        job = start_job("backtest", run_backtest_job)
    except JobAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return job


@router.post("/actions/training-run")
def action_training_run(
    body: TrainingRunRequest = TrainingRunRequest(),
    _: None = Depends(require_dashboard_writes),
) -> dict[str, Any]:
    from tr_bot.managers.training_manager import run_training_job

    try:
        job = start_job("training", lambda: run_training_job(promote=body.promote))
    except JobAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return job


@router.post("/actions/backtest-compare")
def action_backtest_compare(_: None = Depends(require_dashboard_writes)) -> dict[str, Any]:
    from tr_bot.backtest.runner import run_backtest_compare_engines

    try:
        job = start_job("backtest_compare", lambda: run_backtest_compare_engines())
    except JobAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return job


@router.post("/actions/strategy-promote")
def action_strategy_promote(
    body: PromoteRequest,
    _: None = Depends(require_dashboard_writes),
) -> dict[str, Any]:
    from tr_bot.promotion.service import strategy_promote

    if not body.dry_run and not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to run a non-dry-run promotion",
        )
    result = strategy_promote(body.to_tier, body.profile_id, dry_run=body.dry_run)
    logger.info(
        "Dashboard strategy-promote to_tier=%s profile_id=%s dry_run=%s -> %s",
        body.to_tier,
        body.profile_id,
        body.dry_run,
        result.get("ok"),
    )
    return result


@router.get("/jobs/current")
def dashboard_job_current(_: None = Depends(require_dashboard_writes)) -> dict[str, Any] | None:
    return get_current_job()


@router.get("/jobs/{job_id}")
def dashboard_job_status(
    job_id: str,
    _: None = Depends(require_dashboard_writes),
) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job

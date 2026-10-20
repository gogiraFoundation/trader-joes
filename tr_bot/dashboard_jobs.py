"""Background job runner for dashboard-triggered actions (Phase 3).

Long operations (force-train, force-backtest, engine comparison) run on a
background thread so the triggering API request can return immediately with
a job id to poll. Only one dashboard job may be queued/running at a time
(queue depth 1) to avoid overlapping heavy jobs. Job records are also
persisted under ``ARTIFACTS_DIR/dashboard_jobs/<id>.json`` as a simple audit
trail; scheduling state itself lives in-memory, so a dashboard API restart
naturally ends any in-flight background thread too.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path

logger = logging.getLogger("tr_bot.dashboard_jobs")

JOBS_SUBDIR = "dashboard_jobs"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_current_job_id: str | None = None


class JobAlreadyRunningError(RuntimeError):
    """Raised when a new job is requested while another is queued/running."""


def _jobs_dir() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve() / JOBS_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _job_path(job_id: str) -> Path:
    return safe_artifact_path(_jobs_dir(), f"{job_id}.json")


def _persist(job: dict[str, Any]) -> None:
    try:
        path = _job_path(job["id"])
    except ValueError as e:
        logger.warning("Could not resolve path for dashboard job %s: %s", job.get("id"), e)
        return
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not persist dashboard job %s: %s", job.get("id"), e)


def get_job(job_id: str) -> dict[str, Any] | None:
    """Look up a job by id, preferring the in-memory copy (fresher) over disk."""
    with _lock:
        cached = _jobs.get(job_id)
        if cached is not None:
            return dict(cached)
    try:
        path = _job_path(job_id)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def get_current_job() -> dict[str, Any] | None:
    """Return the most recently started job (any status), or a stale disk record.

    After an API process restart the in-memory queue is empty. If the newest
    audit JSON under ``ARTIFACTS_DIR/dashboard_jobs/`` is still ``queued`` or
    ``running``, return that record with ``stale: true`` so the UI can warn
    without restarting the thread.
    """
    with _lock:
        job_id = _current_job_id
    if job_id:
        job = get_job(job_id)
        if job is not None:
            return job
    return _newest_stale_disk_job()


def _newest_stale_disk_job() -> dict[str, Any] | None:
    try:
        root = _jobs_dir()
    except OSError:
        return None
    newest: tuple[float, Path] | None = None
    try:
        for path in root.glob("*.json"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, path)
    except OSError:
        return None
    if newest is None:
        return None
    try:
        data = json.loads(newest[1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    status = str(data.get("status") or "")
    if status not in (STATUS_QUEUED, STATUS_RUNNING):
        return None
    out = dict(data)
    out["stale"] = True
    return out


def start_job(
    kind: str,
    fn: Callable[[], dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Start `fn` on a background thread as a job of type `kind`.

    Raises JobAlreadyRunningError if another dashboard job is currently queued
    or running. Returns the initial (queued) job record; poll `get_job` with
    the returned id for status/result/error.
    """
    global _current_job_id
    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "id": job_id,
        "kind": kind,
        "status": STATUS_QUEUED,
        "meta": meta or {},
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "started_utc": None,
        "finished_utc": None,
        "result": None,
        "error": None,
    }
    with _lock:
        current = _jobs.get(_current_job_id) if _current_job_id else None
        if current is not None and current.get("status") in (STATUS_QUEUED, STATUS_RUNNING):
            raise JobAlreadyRunningError(
                f"job {current['id']} ({current['kind']}) is already {current['status']}"
            )
        _jobs[job_id] = job
        _current_job_id = job_id
    _persist(job)

    def _run() -> None:
        with _lock:
            job["status"] = STATUS_RUNNING
            job["started_utc"] = datetime.now(timezone.utc).isoformat()
        _persist(job)
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - surface any job failure to the dashboard
            logger.exception("Dashboard job %s (%s) failed", job_id, kind)
            with _lock:
                job["status"] = STATUS_ERROR
                job["error"] = str(e)
                job["finished_utc"] = datetime.now(timezone.utc).isoformat()
        else:
            with _lock:
                job["status"] = STATUS_DONE
                job["result"] = result
                job["finished_utc"] = datetime.now(timezone.utc).isoformat()
        _persist(job)

    threading.Thread(target=_run, name=f"dashboard-job-{kind}", daemon=True).start()
    return dict(job)

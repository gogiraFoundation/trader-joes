"""Background job runner for dashboard write actions (Phase 3): queue depth 1."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tr_bot import dashboard_jobs as jobs


@pytest.fixture(autouse=True)
def _artifacts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(jobs.config, "ARTIFACTS_DIR", str(tmp_path))
    # Each test gets a clean scheduling slate regardless of module-level state
    # left behind by other tests (jobs are otherwise process-global by design).
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(jobs, "_current_job_id", None)
    return tmp_path


def _wait_for(job_id: str, *, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = jobs.get_job(job_id)
        if job is not None and job["status"] in (jobs.STATUS_DONE, jobs.STATUS_ERROR):
            return job
        time.sleep(0.01)
    raise TimeoutError(f"job {job_id} did not finish in time")


def test_start_job_runs_and_completes() -> None:
    job = jobs.start_job("test", lambda: {"ok": True, "value": 7})
    assert job["kind"] == "test"
    final = _wait_for(job["id"])
    assert final["status"] == jobs.STATUS_DONE
    assert final["result"] == {"ok": True, "value": 7}
    assert final["error"] is None
    assert final["started_utc"] is not None
    assert final["finished_utc"] is not None


def test_start_job_captures_exception_as_error() -> None:
    def boom() -> dict:
        raise RuntimeError("kaboom")

    job = jobs.start_job("failing", boom)
    final = _wait_for(job["id"])
    assert final["status"] == jobs.STATUS_ERROR
    assert "kaboom" in (final["error"] or "")
    assert final["result"] is None


def test_start_job_rejects_second_job_while_running() -> None:
    started = jobs.threading.Event()
    release = jobs.threading.Event()

    def slow() -> dict:
        started.set()
        release.wait(timeout=2.0)
        return {"ok": True}

    job1 = jobs.start_job("slow", slow)
    assert started.wait(timeout=1.0)

    with pytest.raises(jobs.JobAlreadyRunningError):
        jobs.start_job("other", lambda: {"ok": True})

    release.set()
    final = _wait_for(job1["id"])
    assert final["status"] == jobs.STATUS_DONE


def test_new_job_allowed_after_previous_completes() -> None:
    job1 = jobs.start_job("first", lambda: {"n": 1})
    _wait_for(job1["id"])

    job2 = jobs.start_job("second", lambda: {"n": 2})
    final2 = _wait_for(job2["id"])
    assert final2["result"] == {"n": 2}


def test_get_current_job_tracks_latest() -> None:
    job1 = jobs.start_job("first", lambda: {"n": 1})
    _wait_for(job1["id"])
    current = jobs.get_current_job()
    assert current is not None
    assert current["id"] == job1["id"]

    job2 = jobs.start_job("second", lambda: {"n": 2})
    _wait_for(job2["id"])
    current2 = jobs.get_current_job()
    assert current2 is not None
    assert current2["id"] == job2["id"]


def test_get_job_unknown_id_returns_none() -> None:
    assert jobs.get_job("does-not-exist") is None


def test_get_current_job_none_when_nothing_started() -> None:
    assert jobs.get_current_job() is None


def test_get_current_job_stale_from_disk_after_memory_cleared(tmp_path: Path) -> None:
    """API restart leaves disk audit but empties in-memory queue."""
    import json
    from datetime import datetime, timezone

    jobs_dir = tmp_path / jobs.JOBS_SUBDIR
    jobs_dir.mkdir(parents=True, exist_ok=True)
    stale = {
        "id": "abc123stale01",
        "kind": "training-run",
        "status": jobs.STATUS_RUNNING,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "finished_utc": None,
        "result": None,
        "error": None,
    }
    (jobs_dir / f"{stale['id']}.json").write_text(json.dumps(stale), encoding="utf-8")
    current = jobs.get_current_job()
    assert current is not None
    assert current["id"] == stale["id"]
    assert current.get("stale") is True
    assert current["status"] == jobs.STATUS_RUNNING


def test_get_current_job_ignores_finished_disk_when_memory_empty(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timezone

    jobs_dir = tmp_path / jobs.JOBS_SUBDIR
    jobs_dir.mkdir(parents=True, exist_ok=True)
    done = {
        "id": "donejob00001",
        "kind": "backtest-run",
        "status": jobs.STATUS_DONE,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "result": {"ok": True},
        "error": None,
    }
    (jobs_dir / f"{done['id']}.json").write_text(json.dumps(done), encoding="utf-8")
    assert jobs.get_current_job() is None


def test_job_is_persisted_to_disk(tmp_path: Path) -> None:
    job = jobs.start_job("persisted", lambda: {"ok": True})
    _wait_for(job["id"])
    path = tmp_path / jobs.JOBS_SUBDIR / f"{job['id']}.json"
    assert path.is_file()

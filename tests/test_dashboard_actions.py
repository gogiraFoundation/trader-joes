"""Dashboard write actions (Phase 3): gating, jobs, circuit reset, halt, promote."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tr_bot import config, dashboard_jobs
from tr_bot.health import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_job_state() -> None:
    dashboard_jobs._jobs.clear()
    dashboard_jobs._current_job_id = None
    yield
    dashboard_jobs._jobs.clear()
    dashboard_jobs._current_job_id = None


def _wait_job(client: TestClient, job_id: str, *, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(
            f"/api/dashboard/jobs/{job_id}",
            headers={"Authorization": "Bearer secret"},
        )
        body = r.json()
        if body.get("status") in ("done", "error"):
            return body
        time.sleep(0.01)
    raise TimeoutError("job did not finish in time")


def test_actions_403_when_writes_disabled(client: TestClient) -> None:
    with patch.object(config, "DASHBOARD_WRITES_ENABLED", False):
        r = client.post("/api/dashboard/actions/circuit-reset")
    assert r.status_code == 403


def test_actions_rejected_when_writes_enabled_without_any_token(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", ""),
        patch.object(config, "DASHBOARD_TOKEN", ""),
    ):
        r = client.post("/api/dashboard/actions/circuit-reset")
    assert r.status_code == 403
    assert "require DASHBOARD_WRITE_TOKEN or DASHBOARD_TOKEN" in r.json()["detail"]


def test_actions_401_with_wrong_write_token(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
    ):
        r = client.post(
            "/api/dashboard/actions/circuit-reset",
            headers={"X-Dashboard-Token": "wrong"},
        )
    assert r.status_code == 401


def test_actions_200_with_correct_write_token(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
    ):
        r = client.post(
            "/api/dashboard/actions/circuit-reset",
            headers={"Authorization": "Bearer secret"},
        )
    assert r.status_code == 200


def test_write_token_falls_back_to_dashboard_token(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", ""),
        patch.object(config, "DASHBOARD_TOKEN", "read-token"),
    ):
        r = client.post(
            "/api/dashboard/actions/circuit-reset",
            headers={"X-Dashboard-Token": "read-token"},
        )
    assert r.status_code == 200


def test_circuit_reset_touches_marker_file(client: TestClient, tmp_path: Path) -> None:
    marker = tmp_path / ".circuit_reset"
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
        patch.object(config, "DASHBOARD_TOKEN", ""),
        patch("tr_bot.circuit_breaker._reset_file_path", lambda: marker),
    ):
        r = client.post(
            "/api/dashboard/actions/circuit-reset",
            headers={"Authorization": "Bearer secret"},
        )
    assert r.status_code == 200
    assert marker.is_file()


def test_trading_pause_and_resume(client: TestClient, tmp_path: Path) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
        patch.object(config, "ARTIFACTS_DIR", str(tmp_path)),
    ):
        headers = {"Authorization": "Bearer secret"}
        r = client.post("/api/dashboard/actions/trading-pause", json={"reason": "test pause"}, headers=headers)
        assert r.status_code == 200
        assert r.json() == {"ok": True, "halted": True}
        halt_file = tmp_path / config.TRADING_HALT_FILENAME
        assert halt_file.is_file()
        assert "test pause" in halt_file.read_text(encoding="utf-8")

        r2 = client.post("/api/dashboard/actions/trading-resume", headers=headers)
        assert r2.status_code == 200
        assert r2.json() == {"ok": True, "halted": False}
        assert not halt_file.is_file()


def test_backtest_run_starts_job_and_second_call_conflicts(client: TestClient) -> None:
    started = dashboard_jobs.threading.Event()
    release = dashboard_jobs.threading.Event()

    def fake_backtest() -> dict:
        started.set()
        release.wait(timeout=2.0)
        return {"ok": True}

    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
        patch("tr_bot.backtest.runner.run_backtest_job", fake_backtest),
    ):
        headers = {"Authorization": "Bearer secret"}
        r = client.post("/api/dashboard/actions/backtest-run", headers=headers)
        assert r.status_code == 200
        job_id = r.json()["id"]
        assert started.wait(timeout=1.0)

        r2 = client.post("/api/dashboard/actions/backtest-run", headers=headers)
        assert r2.status_code == 409

        release.set()
        final = _wait_job(client, job_id)
        assert final["status"] == "done"
        assert final["result"] == {"ok": True}


def test_training_run_passes_promote_flag(client: TestClient) -> None:
    captured: dict = {}

    def fake_training(*, promote=None):
        captured["promote"] = promote
        return {"ok": True}

    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
        patch("tr_bot.managers.training_manager.run_training_job", fake_training),
    ):
        r = client.post(
            "/api/dashboard/actions/training-run",
            json={"promote": True},
            headers={"Authorization": "Bearer secret"},
        )
        assert r.status_code == 200
        job_id = r.json()["id"]
        final = _wait_job(client, job_id)
    assert final["status"] == "done"
    assert captured["promote"] is True


def test_backtest_compare_starts_job(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
        patch("tr_bot.backtest.runner.run_backtest_compare_engines", lambda: {"ok": True}),
    ):
        r = client.post(
            "/api/dashboard/actions/backtest-compare",
            headers={"Authorization": "Bearer secret"},
        )
        assert r.status_code == 200
        job_id = r.json()["id"]
        final = _wait_job(client, job_id)
    assert final["status"] == "done"
    assert final["result"] == {"ok": True}


def test_strategy_promote_dry_run_default(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
        patch(
            "tr_bot.promotion.service.strategy_promote",
            return_value={"ok": True, "dry_run": True},
        ) as mock_promote,
    ):
        r = client.post(
            "/api/dashboard/actions/strategy-promote",
            json={"to_tier": "canary", "profile_id": "abc123"},
            headers={"Authorization": "Bearer secret"},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "dry_run": True}
    mock_promote.assert_called_once_with("canary", "abc123", dry_run=True)


def test_strategy_promote_requires_confirm_for_live_run(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
    ):
        r = client.post(
            "/api/dashboard/actions/strategy-promote",
            json={"to_tier": "canary", "profile_id": "abc123", "dry_run": False},
            headers={"Authorization": "Bearer secret"},
        )
    assert r.status_code == 400


def test_strategy_promote_live_run_with_confirm(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
        patch(
            "tr_bot.promotion.service.strategy_promote",
            return_value={"ok": True},
        ) as mock_promote,
    ):
        r = client.post(
            "/api/dashboard/actions/strategy-promote",
            json={
                "to_tier": "canary",
                "profile_id": "abc123",
                "dry_run": False,
                "confirm": True,
            },
            headers={"Authorization": "Bearer secret"},
        )
    assert r.status_code == 200
    mock_promote.assert_called_once_with("canary", "abc123", dry_run=False)


def test_jobs_current_none_initially(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
    ):
        r = client.get("/api/dashboard/jobs/current", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json() is None


def test_job_status_404_when_unknown(client: TestClient) -> None:
    with (
        patch.object(config, "DASHBOARD_WRITES_ENABLED", True),
        patch.object(config, "DASHBOARD_WRITE_TOKEN", "secret"),
    ):
        r = client.get("/api/dashboard/jobs/does-not-exist", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 404


def test_jobs_endpoints_gated_by_writes_enabled(client: TestClient) -> None:
    with patch.object(config, "DASHBOARD_WRITES_ENABLED", False):
        r = client.get("/api/dashboard/jobs/current")
    assert r.status_code == 403

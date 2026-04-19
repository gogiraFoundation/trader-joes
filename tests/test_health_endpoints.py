"""FastAPI /health and /ready."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tr_bot import config
from tr_bot.health import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_has_expected_shape(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert body["status"] in ("ready", "degraded")
    assert "redis" in body and isinstance(body["redis"], dict)
    assert "postgres" in body and isinstance(body["postgres"], dict)
    assert "readiness" in body
    assert body["readiness"].get("postgres_required_for_ready") is config.readiness_require_postgres()
    assert body["readiness"].get("mode") == config.READINESS_MODE
    assert "managers" in body
    assert "artifacts_dir" in body


def test_ready_degraded_without_services(client: TestClient) -> None:
    """Without local Redis/Postgres, readiness is typically degraded."""
    r = client.get("/ready")
    body = r.json()
    assert body["status"] == "degraded" or body["status"] == "ready"


@patch("tr_bot.health._check_redis", return_value={"ok": True})
@patch("tr_bot.health._check_postgres", return_value={"ok": True})
def test_ready_when_dependencies_up(
    _pg: object, _redis: object, client: TestClient
) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


@patch.object(config, "READINESS_REQUIRE_POSTGRES", False)
@patch.object(config, "READINESS_MODE", "auto")
@patch("tr_bot.health._check_redis", return_value={"ok": True})
@patch(
    "tr_bot.health._check_postgres",
    return_value={"ok": False, "error": "connection refused"},
)
def test_ready_ready_when_postgres_not_required(
    _pg: object, _redis: object, client: TestClient
) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["readiness"]["postgres_required_for_ready"] is False

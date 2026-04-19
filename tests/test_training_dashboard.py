"""Training block on dashboard summary and last_run artifact."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tr_bot import config
from tr_bot.health import app
from tr_bot.training.dashboard_info import build_training_dashboard_payload
from tr_bot.training.last_run import write_training_last_run


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_build_training_dashboard_payload_includes_last_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path))
    write_training_last_run({"ok": True, "stage": "complete", "feature_rows": 42})
    p = build_training_dashboard_payload()
    assert p["last_run"] is not None
    assert p["last_run"]["ok"] is True
    assert p["last_run"]["feature_rows"] == 42


def test_dashboard_summary_training_surfaces_last_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path))
    write_training_last_run({"ok": True, "feature_rows": 99})
    with patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    lr = r.json()["training"]["last_run"]
    assert lr is not None
    assert lr["ok"] is True
    assert lr["feature_rows"] == 99

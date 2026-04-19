"""Dashboard API and root redirect."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tr_bot import config
from tr_bot import dashboard_api
from tr_bot.health import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_favicon_no_404(client: TestClient) -> None:
    r = client.get("/favicon.ico")
    assert r.status_code == 204


def test_dashboard_edit_redirects(client: TestClient) -> None:
    r = client.get("/dashboard/edit", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location") == "/dashboard/"


def test_root_serves_dashboard_html(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "text/html" in (r.headers.get("content-type") or "")
    assert "tr_bot" in r.text


def test_dashboard_summary_shape(client: TestClient) -> None:
    with patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ready", "degraded")
    assert "dependencies" in body
    assert "redis" in body["dependencies"]
    assert "postgres" in body["dependencies"]
    assert "config" in body
    assert "portfolio" in body
    assert "fund" in body
    assert "best_fit" in body
    assert "circuit" in body
    assert "ops" in body
    assert "equity_history" in body
    assert isinstance(body["equity_history"].get("points"), list)
    assert "equity_history_display" in body
    assert isinstance(body["equity_history_display"].get("points"), list)
    assert "trading" in body
    assert "by_symbol" in body["trading"]
    assert isinstance(body["trading"]["by_symbol"], dict)
    assert "training" in body
    assert "backtest" in body
    assert "pnl" in body
    assert "trade_summary" in body["pnl"]
    assert "cost_assumptions" in body["pnl"]
    assert "last_run" in body["backtest"]
    assert "config" in body["backtest"]
    assert "scheduler" in body
    assert "training_interval_seconds" in body["scheduler"]
    assert "strategy_promotion" in body
    assert "pointers" in body["strategy_promotion"]
    assert "startup_pipeline" in body
    trn = body["training"]
    assert "config" in trn
    assert "dataset" in trn
    assert "models" in trn
    assert "last_run" in trn
    assert "train_sources" in trn["config"]
    assert "versioned" in trn["models"]
    assert "production" in trn["models"]
    assert "train_output" in trn["models"]
    assert "symbol" in body["config"]
    assert "paper_trading" in body["config"]
    assert "risk_per_trade" in body["config"]
    assert "public_base_url" in body["config"]
    dep = body["dependencies"]
    assert "readiness" in dep
    assert dep["readiness"].get("postgres_required_for_ready") is config.readiness_require_postgres()
    assert dep["readiness"].get("mode") == config.READINESS_MODE
    assert "latency_ms" in dep["redis"]
    assert "latency_ms" in dep["postgres"]


def test_dashboard_summary_401_when_token_required(client: TestClient) -> None:
    with patch.object(config, "DASHBOARD_TOKEN", "secret-token"):
        r = client.get("/api/dashboard/summary")
    assert r.status_code == 401


def test_dashboard_summary_with_bearer_token(client: TestClient) -> None:
    with patch.object(config, "DASHBOARD_TOKEN", "secret-token"):
        r = client.get(
            "/api/dashboard/summary",
            headers={"Authorization": "Bearer secret-token"},
        )
    assert r.status_code == 200


def test_dashboard_summary_with_x_header_token(client: TestClient) -> None:
    with patch.object(config, "DASHBOARD_TOKEN", "secret-token"):
        r = client.get(
            "/api/dashboard/summary",
            headers={"X-Dashboard-Token": "secret-token"},
        )
    assert r.status_code == 200


@patch("tr_bot.dashboard_api.check_redis", return_value={"ok": True})
@patch("tr_bot.dashboard_api.check_postgres", return_value={"ok": True})
def test_dashboard_ready_status_when_deps_up(
    _pg: object, _redis: object, client: TestClient
) -> None:
    with patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


@patch.object(config, "READINESS_REQUIRE_POSTGRES", False)
@patch.object(config, "READINESS_MODE", "auto")
@patch("tr_bot.dashboard_api.check_redis", return_value={"ok": True})
@patch(
    "tr_bot.dashboard_api.check_postgres",
    return_value={"ok": False, "error": "connection refused"},
)
def test_dashboard_ready_when_postgres_not_required(
    _pg: object, _redis: object, client: TestClient
) -> None:
    with patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["dependencies"]["readiness"]["postgres_required_for_ready"] is False


def test_enrich_backtest_last_run_flattens_ablation_matrix() -> None:
    raw = {
        "ok": True,
        "symbol": "BTC/USDT",
        "timeframe": "5m",
        "ablations": {
            "neutral_ta_on": {
                "ok": True,
                "total_return_pct": -11.2,
                "max_drawdown_pct": 12.3,
                "trades": 54,
                "wins": 17,
                "losses": 37,
                "bars_total": 984,
            }
        },
    }
    with patch.object(config, "BACKTEST_SENTIMENT_MODE", "neutral"):
        with patch.object(config, "BACKTEST_SKIP_TA_FILTER", False):
            out = dashboard_api._enrich_backtest_last_run_for_dashboard(raw)
    assert out is not None
    assert out["total_return_pct"] == -11.2
    assert out["max_drawdown_pct"] == 12.3
    assert out["trades"] == 54
    assert out["primary_ablation_key"] == "neutral_ta_on"


def test_enrich_backtest_last_run_passthrough_when_flat() -> None:
    raw = {"ok": True, "total_return_pct": 5.0, "trades": 10}
    assert dashboard_api._enrich_backtest_last_run_for_dashboard(raw) == raw


def test_enrich_backtest_last_run_works_without_ok_flag() -> None:
    raw = {
        "ok": True,
        "ablations": {
            "neutral_ta_on": {
                "total_return_pct": 1.5,
                "max_drawdown_pct": 2.1,
                "wins": 3,
                "losses": 2,
            }
        },
    }
    out = dashboard_api._enrich_backtest_last_run_for_dashboard(raw)
    assert out is not None
    assert out["total_return_pct"] == 1.5
    assert out["max_drawdown_pct"] == 2.1
    assert out["trades"] == 5


def test_enrich_backtest_last_run_reads_nested_metrics() -> None:
    raw = {
        "ok": True,
        "ablations": {
            "neutral_ta_on": {
                "metrics": {
                    "total_return_pct": -3.0,
                    "max_drawdown_pct": 5.5,
                    "trades": 7,
                }
            }
        },
    }
    out = dashboard_api._enrich_backtest_last_run_for_dashboard(raw)
    assert out is not None
    assert out["total_return_pct"] == -3.0
    assert out["max_drawdown_pct"] == 5.5
    assert out["trades"] == 7


def test_dashboard_best_fit_winner_json_missing(client: TestClient, tmp_path: Path) -> None:
    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)), patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get("/api/dashboard/best-fit/winner.json")
    assert r.status_code == 404


def test_dashboard_best_fit_winner_json_ok(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path
    p = root / "sweep_best_fit" / "winner.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mean_score": 0.1234, "env_updates": {"TIMEFRAME": "5m"}}), encoding="utf-8")
    with patch.object(config, "ARTIFACTS_DIR", str(root)), patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get("/api/dashboard/best-fit/winner.json")
    assert r.status_code == 200
    assert "application/json" in (r.headers.get("content-type") or "")
    assert r.json().get("mean_score") == 0.1234

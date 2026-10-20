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


def test_root_serves_v1_when_dashboard_ui_v2_but_no_v2_file(client: TestClient) -> None:
    """Rollback safety net: DASHBOARD_UI=v2 without index.v2.html falls back to v1."""
    with patch.object(config, "DASHBOARD_UI", "v2"):
        r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "tr_bot" in r.text


def test_dashboard_index_path_prefers_v2_file_when_present(tmp_path: Path) -> None:
    from tr_bot import health

    v2 = tmp_path / "index.v2.html"
    v2.write_text("<html>v2</html>", encoding="utf-8")
    with (
        patch.object(config, "DASHBOARD_UI", "v2"),
        patch.object(health, "_DASHBOARD_INDEX_V2", v2),
    ):
        assert health._dashboard_index_path() == v2


def test_dashboard_index_path_prefers_v3_file_when_present(tmp_path: Path) -> None:
    from tr_bot import health

    v3 = tmp_path / "index.v3.html"
    v3.write_text("<html>v3</html>", encoding="utf-8")
    with (
        patch.object(config, "DASHBOARD_UI", "v3"),
        patch.object(health, "_DASHBOARD_INDEX_V3", v3),
    ):
        assert health._dashboard_index_path() == v3


def test_dashboard_index_path_v3_falls_back_to_v2(tmp_path: Path) -> None:
    from tr_bot import health

    v2 = tmp_path / "index.v2.html"
    v2.write_text("<html>v2</html>", encoding="utf-8")
    missing_v3 = tmp_path / "index.v3.html"
    with (
        patch.object(config, "DASHBOARD_UI", "v3"),
        patch.object(health, "_DASHBOARD_INDEX_V3", missing_v3),
        patch.object(health, "_DASHBOARD_INDEX_V2", v2),
    ):
        assert health._dashboard_index_path() == v2


def test_root_serves_v2_when_enabled_and_file_exists(client: TestClient, tmp_path: Path) -> None:
    from tr_bot import health

    v2 = tmp_path / "index.v2.html"
    v2.write_text("<html>dashboard v2</html>", encoding="utf-8")
    with (
        patch.object(config, "DASHBOARD_UI", "v2"),
        patch.object(health, "_DASHBOARD_INDEX_V2", v2),
    ):
        r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "dashboard v2" in r.text


def test_root_serves_v3_when_enabled_and_file_exists(client: TestClient, tmp_path: Path) -> None:
    from tr_bot import health

    v3 = tmp_path / "index.v3.html"
    v3.write_text("<html>dashboard v3</html>", encoding="utf-8")
    with (
        patch.object(config, "DASHBOARD_UI", "v3"),
        patch.object(health, "_DASHBOARD_INDEX_V3", v3),
    ):
        r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "dashboard v3" in r.text


def test_dashboard_index_path_defaults_to_v1() -> None:
    from tr_bot import health

    with patch.object(config, "DASHBOARD_UI", "v1"):
        assert health._dashboard_index_path() == health._DASHBOARD_INDEX_V1


def test_dashboard_metrics_path_defaults_to_v1() -> None:
    from tr_bot import health

    with patch.object(config, "DASHBOARD_UI", "v1"):
        assert health._dashboard_metrics_path() == health._DASHBOARD_METRICS_V1


def test_dashboard_metrics_path_prefers_v2_when_present(tmp_path: Path) -> None:
    from tr_bot import health

    v2 = tmp_path / "metrics.v2.html"
    v2.write_text("<html>metrics v2</html>", encoding="utf-8")
    with (
        patch.object(config, "DASHBOARD_UI", "v2"),
        patch.object(health, "_DASHBOARD_METRICS_V2", v2),
    ):
        assert health._dashboard_metrics_path() == v2


def test_dashboard_metrics_path_prefers_v3_when_present(tmp_path: Path) -> None:
    from tr_bot import health

    v3 = tmp_path / "metrics.v3.html"
    v3.write_text("<html>metrics v3</html>", encoding="utf-8")
    with (
        patch.object(config, "DASHBOARD_UI", "v3"),
        patch.object(health, "_DASHBOARD_METRICS_V3", v3),
    ):
        assert health._dashboard_metrics_path() == v3


def test_dashboard_metrics_path_falls_back_when_v2_missing(tmp_path: Path) -> None:
    from tr_bot import health

    missing = tmp_path / "metrics.v2.html"
    with (
        patch.object(config, "DASHBOARD_UI", "v2"),
        patch.object(health, "_DASHBOARD_METRICS_V2", missing),
    ):
        assert health._dashboard_metrics_path() == health._DASHBOARD_METRICS_V1


def test_dashboard_metrics_path_v3_falls_back_to_v2(tmp_path: Path) -> None:
    from tr_bot import health

    v2 = tmp_path / "metrics.v2.html"
    v2.write_text("<html>metrics v2</html>", encoding="utf-8")
    missing_v3 = tmp_path / "metrics.v3.html"
    with (
        patch.object(config, "DASHBOARD_UI", "v3"),
        patch.object(health, "_DASHBOARD_METRICS_V3", missing_v3),
        patch.object(health, "_DASHBOARD_METRICS_V2", v2),
    ):
        assert health._dashboard_metrics_path() == v2


def test_metrics_route_serves_v2_when_enabled(client: TestClient, tmp_path: Path) -> None:
    from tr_bot import health

    v2 = tmp_path / "metrics.v2.html"
    v2.write_text("<html>metrics cockpit v2</html>", encoding="utf-8")
    with (
        patch.object(config, "DASHBOARD_UI", "v2"),
        patch.object(health, "_DASHBOARD_METRICS_V2", v2),
    ):
        r = client.get("/dashboard/metrics.html", follow_redirects=False)
    assert r.status_code == 200
    assert "metrics cockpit v2" in r.text


def test_metrics_v2_page_contract_ids() -> None:
    """Static contract: metrics.v2.html wires required element ids + scripts."""
    from tr_bot import health

    path = health._DASHBOARD_METRICS_V2
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    for needle in (
        'id="chart-nav"',
        'id="chart-dd"',
        'id="chart-trades"',
        'id="perf-tbody"',
        'id="bt-compare-open"',
        'id="compare-modal"',
        "/dashboard/js/common.js",
        "/dashboard/js/metrics-core.js",
        "/dashboard/js/metrics.js",
    ):
        assert needle in text


def test_metrics_v3_page_contract_ids() -> None:
    """Static contract: metrics.v3.html keeps the v2 cockpit ids + scripts."""
    from tr_bot import health

    path = health._DASHBOARD_METRICS_V3
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    for needle in (
        'id="chart-nav"',
        'id="chart-dd"',
        'id="chart-trades"',
        'id="perf-tbody"',
        'id="bt-compare-open"',
        'id="compare-modal"',
        "dash-panel",
        "/dashboard/js/common.js",
        "/dashboard/js/metrics-core.js",
        "/dashboard/js/metrics.js",
    ):
        assert needle in text


def test_index_v3_page_contract_ids() -> None:
    """Static contract: Overview v3 KPI strip + chart + shared scripts."""
    from tr_bot import health

    path = health._DASHBOARD_INDEX_V3
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    for needle in (
        'id="ops-section"',
        "kpi-strip",
        'id="val-nav"',
        'id="val-dd"',
        'id="chart-equity"',
        'id="qa-circuit-reset"',
        "qa-danger-cluster",
        "/dashboard/js/common.js",
        "/dashboard/js/overview-core.js",
    ):
        assert needle in text


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
    assert "metrics" in body
    assert "drawdown_history" in body["metrics"]
    assert isinstance(body["metrics"]["drawdown_history"].get("points"), list)
    assert "trading" in body
    assert "by_symbol" in body["trading"]
    assert isinstance(body["trading"]["by_symbol"], dict)
    assert "training" in body
    assert "backtest" in body
    assert "pnl" in body
    assert "trade_summary" in body["pnl"]
    assert "closed_trade_stats" in body["pnl"]
    assert "cost_assumptions" in body["pnl"]
    assert "last_run" in body["backtest"]
    assert "config" in body["backtest"]
    assert "scheduler" in body
    assert "training_interval_seconds" in body["scheduler"]
    assert "strategy_promotion" in body
    assert "pointers" in body["strategy_promotion"]
    assert "startup_pipeline" in body
    assert "alerts" in body
    assert isinstance(body["alerts"], list)
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
    assert "dashboard_writes_enabled" in body["config"]
    assert body["config"]["dashboard_ui"] in ("v1", "v2", "v3")
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


def test_dashboard_summary_sections_subset(client: TestClient) -> None:
    with patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get(
            "/api/dashboard/summary",
            params={"sections": "circuit,config,fund"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ready", "degraded")
    assert "dependencies" in body
    assert "circuit" in body
    assert "config" in body
    assert "fund" in body
    assert "training" not in body
    assert "backtest" not in body
    assert "pnl" not in body


def test_dashboard_summary_sections_unknown_400(client: TestClient) -> None:
    with patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get(
            "/api/dashboard/summary",
            params={"sections": "circuit,not_a_real_section"},
        )
    assert r.status_code == 400
    assert "not_a_real_section" in str(r.json().get("detail", ""))


def test_dashboard_summary_alerts_without_strategy_promotion_key(client: TestClient) -> None:
    """Overview requests alerts but not strategy_promotion; alerts must still build."""
    with patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get(
            "/api/dashboard/summary",
            params={"sections": "alerts,ops,circuit,fund,pnl"},
        )
    assert r.status_code == 200
    body = r.json()
    assert "alerts" in body
    assert isinstance(body["alerts"], list)
    assert "strategy_promotion" not in body


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


def test_dashboard_trades_empty(client: TestClient, tmp_path: Path) -> None:
    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)), patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get("/api/dashboard/trades")
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == []
    assert body["total_matched"] == 0


def test_dashboard_trades_filters_and_pagination(client: TestClient, tmp_path: Path) -> None:
    from tr_bot.trade_ledger import append_closed_trade

    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)):
        append_closed_trade(
            {
                "utc": "2026-07-01T00:00:00+00:00",
                "symbol": "BTC/USDT",
                "position_side": "long",
                "outcome": "win",
                "net_pnl_usdt": 5.0,
                "paper": True,
            }
        )
        append_closed_trade(
            {
                "utc": "2026-07-02T00:00:00+00:00",
                "symbol": "ETH/USDT",
                "position_side": "short",
                "outcome": "loss",
                "net_pnl_usdt": -2.0,
                "paper": True,
            }
        )
    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)), patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get("/api/dashboard/trades", params={"symbol": "BTC/USDT"})
    assert r.status_code == 200
    body = r.json()
    assert body["total_matched"] == 1
    assert body["rows"][0]["symbol"] == "BTC/USDT"


def test_dashboard_trades_csv_export(client: TestClient, tmp_path: Path) -> None:
    from tr_bot.trade_ledger import append_closed_trade

    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)):
        append_closed_trade(
            {
                "utc": "2026-07-01T00:00:00+00:00",
                "symbol": "BTC/USDT",
                "position_side": "long",
                "outcome": "win",
                "net_pnl_usdt": 5.0,
                "paper": True,
            }
        )
    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)), patch.object(config, "DASHBOARD_TOKEN", ""):
        r = client.get("/api/dashboard/trades", params={"format": "csv"})
    assert r.status_code == 200
    assert "text/csv" in (r.headers.get("content-type") or "")
    assert "BTC/USDT" in r.text


def test_dashboard_trades_401_when_token_required(client: TestClient) -> None:
    with patch.object(config, "DASHBOARD_TOKEN", "secret-token"):
        r = client.get("/api/dashboard/trades")
    assert r.status_code == 401


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

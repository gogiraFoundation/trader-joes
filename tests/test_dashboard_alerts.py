"""Unit tests for tr_bot.dashboard_alerts.build_alerts (pure function, no I/O)."""

from __future__ import annotations

from tr_bot.dashboard_alerts import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    build_alerts,
)


def _ids(alerts: list[dict]) -> list[str]:
    return [a["id"] for a in alerts]


def test_no_alerts_when_all_healthy() -> None:
    alerts = build_alerts(
        circuit={"tripped": False, "consecutive_losses": 0, "max_consecutive_losses": 3},
        fund={"enabled": True, "halt_file": False, "state": {"halted": False, "last_nav": 10_000, "peak_nav": 10_000}},
        ops={"heartbeat_ok": True, "file_exists": True},
        pnl={"recent_trades": []},
        strategy_promotion={"last_evaluate": {"passed": True}},
        max_drawdown_pct=0.2,
    )
    assert alerts == []


def test_agents_critical_alert() -> None:
    alerts = build_alerts(
        circuit={"tripped": False},
        fund=None,
        ops=None,
        pnl=None,
        strategy_promotion=None,
        agents={
            "supervisor_last": {
                "role": "supervisor",
                "severity": "critical",
                "summary": "circuit streak — pause recommended",
            }
        },
    )
    assert "agents_critical" in _ids(alerts)
    assert alerts[0]["id"] == "agents_critical"
    assert alerts[0]["severity"] == SEVERITY_CRITICAL


def test_circuit_tripped_is_critical_and_first() -> None:
    alerts = build_alerts(
        circuit={"tripped": True, "consecutive_losses": 3, "max_consecutive_losses": 3},
        fund=None,
        ops=None,
        pnl={"recent_trades": [{"outcome": "loss"}, {"outcome": "loss"}, {"outcome": "win"}]},
        strategy_promotion=None,
    )
    assert _ids(alerts) == ["circuit_tripped"]
    assert alerts[0]["severity"] == SEVERITY_CRITICAL
    assert "3/3" in alerts[0]["message"]
    assert alerts[0]["href"] == "/dashboard/risk.html"
    assert "Recent contiguous loss streak" in alerts[0]["details"]


def test_manual_halt_file_triggers_critical_alert() -> None:
    alerts = build_alerts(
        circuit={"tripped": False},
        fund={"enabled": True, "halt_file": True, "state": {"halted": False}},
        ops=None,
        pnl=None,
        strategy_promotion=None,
    )
    assert _ids(alerts) == ["trading_halted"]
    assert "manual halt file" in alerts[0]["message"]


def test_fund_halt_reason_included_in_message() -> None:
    alerts = build_alerts(
        circuit=None,
        fund={
            "enabled": True,
            "halt_file": False,
            "state": {"halted": True, "halt_reason": "max_drawdown_exceeded"},
        },
        ops=None,
        pnl=None,
        strategy_promotion=None,
    )
    assert _ids(alerts) == ["trading_halted"]
    assert "max_drawdown_exceeded" in alerts[0]["message"]


def test_nav_drawdown_severe_when_over_configured_threshold() -> None:
    alerts = build_alerts(
        circuit=None,
        fund={"enabled": True, "halt_file": False, "state": {"last_nav": 8_000, "peak_nav": 10_000}},
        ops=None,
        pnl=None,
        strategy_promotion=None,
        max_drawdown_pct=0.1,
    )
    assert _ids(alerts) == ["nav_drawdown"]
    assert alerts[0]["severity"] == SEVERITY_CRITICAL
    assert "20.0%" in alerts[0]["message"]
    assert "limit 10%" in alerts[0]["message"]
    assert "Current NAV" in alerts[0]["details"]


def test_nav_drawdown_warning_below_configured_threshold_but_above_soft_floor() -> None:
    alerts = build_alerts(
        circuit=None,
        fund={"enabled": True, "halt_file": False, "state": {"last_nav": 9_400, "peak_nav": 10_000}},
        ops=None,
        pnl=None,
        strategy_promotion=None,
        max_drawdown_pct=0.5,
    )
    assert _ids(alerts) == ["nav_drawdown"]
    assert alerts[0]["severity"] == SEVERITY_WARNING


def test_nav_drawdown_ignored_when_tiny_and_no_threshold() -> None:
    alerts = build_alerts(
        circuit=None,
        fund={"enabled": True, "halt_file": False, "state": {"last_nav": 9_990, "peak_nav": 10_000}},
        ops=None,
        pnl=None,
        strategy_promotion=None,
        max_drawdown_pct=None,
    )
    assert alerts == []


def test_nav_drawdown_skipped_when_fund_disabled() -> None:
    alerts = build_alerts(
        circuit=None,
        fund={"enabled": False},
        ops=None,
        pnl=None,
        strategy_promotion=None,
        max_drawdown_pct=0.01,
    )
    assert alerts == []


def test_gates_failed_lists_failing_gate_names() -> None:
    alerts = build_alerts(
        circuit=None,
        fund=None,
        ops=None,
        pnl=None,
        strategy_promotion={
            "last_evaluate": {
                "passed": False,
                "outcomes": [
                    {"name": "min_trades", "passed": False, "detail": "Need at least 20 trades"},
                    {"name": "max_drawdown", "passed": True},
                    {"name": "soak_days", "passed": False, "detail": "Need 7 calendar days"},
                ],
            }
        },
    )
    assert _ids(alerts) == ["gates_failed"]
    assert alerts[0]["severity"] == SEVERITY_WARNING
    assert "min_trades" in alerts[0]["message"]
    assert "soak_days" in alerts[0]["message"]
    assert "max_drawdown" not in alerts[0]["message"].split("(")[1]
    assert "Need at least 20 trades" in alerts[0]["details"]


def test_gates_passed_produces_no_alert() -> None:
    alerts = build_alerts(
        circuit=None,
        fund=None,
        ops=None,
        pnl=None,
        strategy_promotion={"last_evaluate": {"passed": True, "outcomes": []}},
    )
    assert alerts == []


def test_heartbeat_stale_warns_only_if_file_exists() -> None:
    alerts = build_alerts(
        circuit=None,
        fund=None,
        ops={"heartbeat_ok": False, "file_exists": True, "heartbeat_stale_seconds": 125.4},
        pnl=None,
        strategy_promotion=None,
    )
    assert _ids(alerts) == ["heartbeat_stale"]
    assert "125s" in alerts[0]["message"]


def test_heartbeat_stale_ignored_when_never_run() -> None:
    alerts = build_alerts(
        circuit=None,
        fund=None,
        ops={"heartbeat_ok": False, "file_exists": False},
        pnl=None,
        strategy_promotion=None,
    )
    assert alerts == []


def test_multiple_alerts_sorted_critical_first() -> None:
    alerts = build_alerts(
        circuit={"tripped": False},
        fund={"enabled": True, "halt_file": False, "state": {"last_nav": 8_000, "peak_nav": 10_000}},
        ops={"heartbeat_ok": False, "file_exists": True, "heartbeat_stale_seconds": 60},
        pnl=None,
        strategy_promotion={"last_evaluate": {"passed": False, "outcomes": []}},
        max_drawdown_pct=0.1,
    )
    assert _ids(alerts) == ["nav_drawdown", "gates_failed", "heartbeat_stale"]
    assert alerts[0]["severity"] == SEVERITY_CRITICAL
    assert alerts[1]["severity"] == SEVERITY_WARNING
    assert alerts[2]["severity"] == SEVERITY_WARNING


def test_all_none_inputs_produce_no_alerts() -> None:
    assert build_alerts(circuit=None, fund=None, ops=None, pnl=None, strategy_promotion=None) == []

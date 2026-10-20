"""Agent runner dry-run with mocked LLM."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tr_bot.agents.runner import read_agents_dashboard_payload, run_agents_job


def test_run_agents_job_skipped_when_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_ENABLED", False)
    out = run_agents_job(force=False)
    assert out["ok"] is True
    assert out.get("skipped") == "AGENTS_ENABLED_false"


def test_force_without_dry_run_still_requires_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_ENABLED", False)
    monkeypatch.setattr("tr_bot.config.AGENTS_DRY_RUN", False)
    out = run_agents_job(force=True, dry_run=False)
    assert out.get("skipped") == "AGENTS_ENABLED_false"


def test_force_dry_run_allowed_when_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_ENABLED", False)
    monkeypatch.setattr("tr_bot.config.AGENTS_DRY_RUN", True)
    monkeypatch.setattr("tr_bot.config.OPENAI_API_KEY", "test-key")

    with (
        patch(
            "tr_bot.agents.supervisor.chat_json",
            return_value={
                "schema_version": 1,
                "role": "supervisor",
                "summary": "ok",
                "severity": "info",
                "actions": [{"type": "noop", "reason": "ok", "confidence": 1.0, "params": {}}],
                "proposal": None,
            },
        ),
        patch("tr_bot.agents.context.read_circuit_state_snapshot", return_value={}),
        patch("tr_bot.agents.context.ops_view_for_api", return_value={}),
        patch("tr_bot.agents.context.read_trading_snapshot", return_value={}),
        patch("tr_bot.agents.context.read_recent_trades", return_value=[]),
        patch("tr_bot.agents.runner.notify"),
    ):
        out = run_agents_job(role="supervisor", dry_run=True, force=True)
    assert out["ok"] is True
    assert out.get("skipped") is None
    assert out["dry_run"] is True


def test_run_agents_dry_run_writes_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_ENABLED", True)
    monkeypatch.setattr("tr_bot.config.AGENTS_DRY_RUN", True)
    monkeypatch.setattr("tr_bot.config.OPENAI_API_KEY", "test-key")

    fake_plan = {
        "schema_version": 1,
        "role": "supervisor",
        "summary": "circuit tripped",
        "severity": "critical",
        "actions": [
            {"type": "notify", "reason": "streak", "confidence": 0.9, "params": {}},
            {"type": "pause_entries", "reason": "streak", "confidence": 0.9, "params": {}},
        ],
        "proposal": None,
    }

    def _chat(messages, **kwargs):
        # Return role matching last system/user — supervisor first then fund
        sys_msg = messages[0]["content"] if messages else ""
        if "Fund Manager" in sys_msg:
            p = dict(fake_plan)
            p["role"] = "fund_manager"
            p["severity"] = "warning"
            p["summary"] = "fee drag"
            p["actions"] = [{"type": "noop", "reason": "ok", "confidence": 1.0, "params": {}}]
            return p
        return dict(fake_plan)

    with (
        patch("tr_bot.agents.supervisor.chat_json", side_effect=_chat),
        patch("tr_bot.agents.fund_manager.chat_json", side_effect=_chat),
        patch("tr_bot.agents.context.read_circuit_state_snapshot", return_value={"tripped": True}),
        patch("tr_bot.agents.context.ops_view_for_api", return_value={}),
        patch("tr_bot.agents.context.read_trading_snapshot", return_value={}),
        patch("tr_bot.agents.context.read_recent_trades", return_value=[]),
        patch("tr_bot.agents.runner.notify"),
    ):
        out = run_agents_job(role="both", dry_run=True, force=True)

    assert out["ok"] is True
    assert out["dry_run"] is True
    sup = tmp_path / "agents" / "supervisor_last.json"
    fund = tmp_path / "agents" / "fund_manager_last.json"
    assert sup.is_file()
    assert fund.is_file()
    body = json.loads(sup.read_text(encoding="utf-8"))
    assert body["plan"]["severity"] == "critical"
    assert any(r.get("would_execute") for r in body["execution"] if r.get("action") == "pause_entries")

    dash = read_agents_dashboard_payload()
    assert dash["supervisor_last"]["severity"] == "critical"


def test_llm_failure_becomes_noop(tmp_path: Path, monkeypatch) -> None:
    from tr_bot.agents.llm_client import LLMClientError

    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_ENABLED", True)
    monkeypatch.setattr("tr_bot.config.OPENAI_API_KEY", "test-key")

    with (
        patch(
            "tr_bot.agents.supervisor.chat_json",
            side_effect=LLMClientError("down"),
        ),
        patch("tr_bot.agents.context.read_circuit_state_snapshot", return_value={}),
        patch("tr_bot.agents.context.ops_view_for_api", return_value={}),
        patch("tr_bot.agents.context.read_trading_snapshot", return_value={}),
        patch("tr_bot.agents.context.read_recent_trades", return_value=[]),
        patch("tr_bot.agents.runner.notify"),
    ):
        out = run_agents_job(role="supervisor", dry_run=True, force=True)

    assert out["ok"] is True
    assert out["roles"][0]["llm_error"]
    plan = json.loads((tmp_path / "agents" / "supervisor_last.json").read_text())["plan"]
    assert plan["actions"][0]["type"] == "noop"

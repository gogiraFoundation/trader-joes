"""Gated agent action execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tr_bot.agents.execute import execute_action, execute_plan


def _plan(**kwargs):
    base = {
        "schema_version": 1,
        "role": "supervisor",
        "summary": "t",
        "severity": "warning",
        "actions": [],
        "proposal": None,
    }
    base.update(kwargs)
    return base


def test_dry_run_marks_would_execute(monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_PAUSE", True)
    r = execute_action(
        {"type": "pause_entries", "reason": "x", "confidence": 0.9, "params": {}},
        plan=_plan(),
        dry_run=True,
    )
    assert r["skipped"] == "dry_run"
    assert r["would_execute"] is True


def test_flag_off_skips_pause(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_DRY_RUN", False)
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_PAUSE", False)
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr("tr_bot.config.AGENTS_COOLDOWN_SECONDS", 0)
    r = execute_action(
        {"type": "pause_entries", "reason": "x", "confidence": 0.9, "params": {}},
        plan=_plan(),
        dry_run=False,
    )
    assert r["skipped"] == "flag_off"


def test_pause_and_resume_reason_match(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_HALT_REASON", "agents_supervisor_pause")
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_PAUSE", True)
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_RESUME", True)
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr("tr_bot.config.AGENTS_COOLDOWN_SECONDS", 0)

    with patch("tr_bot.agents.execute.notify"):
        r1 = execute_action(
            {"type": "pause_entries", "reason": "loss", "confidence": 0.95, "params": {}},
            plan=_plan(),
            dry_run=False,
        )
        assert r1.get("executed") is True
        from tr_bot import config

        halt = tmp_path / config.TRADING_HALT_FILENAME
        assert halt.is_file()
        body = halt.read_text(encoding="utf-8")
        assert "agents_supervisor_pause" in body
        assert '"owner":"agent"' in body.replace(" ", "")

        # Second pause must not overwrite existing halt
        r_over = execute_action(
            {"type": "pause_entries", "reason": "again", "confidence": 0.95, "params": {}},
            plan=_plan(),
            dry_run=False,
        )
        assert r_over.get("skipped") == "halt_already_active"

        # Operator halt must not be cleared by agent resume
        halt.write_text("manual pause via dashboard\n", encoding="utf-8")
        r2 = execute_action(
            {"type": "resume_entries", "reason": "ok", "confidence": 0.95, "params": {}},
            plan=_plan(),
            dry_run=False,
        )
        assert r2.get("cleared") is False
        assert halt.is_file()

        # Plain-text agent reason without owner=agent is not cleared
        halt.write_text("agents_supervisor_pause\n", encoding="utf-8")
        r_plain = execute_action(
            {"type": "resume_entries", "reason": "ok", "confidence": 0.95, "params": {}},
            plan=_plan(),
            dry_run=False,
        )
        assert r_plain.get("cleared") is False

        halt.write_text('{"owner":"agent","reason":"agents_supervisor_pause"}\n', encoding="utf-8")
        r3 = execute_action(
            {"type": "resume_entries", "reason": "ok", "confidence": 0.95, "params": {}},
            plan=_plan(),
            dry_run=False,
        )
        assert r3.get("cleared") is True
        assert not halt.is_file()


def test_resume_skips_when_circuit_tripped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_HALT_REASON", "agents_supervisor_pause")
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_RESUME", True)
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr("tr_bot.config.AGENTS_COOLDOWN_SECONDS", 0)
    from tr_bot import config

    halt = tmp_path / config.TRADING_HALT_FILENAME
    halt.write_text('{"owner":"agent","reason":"agents_supervisor_pause"}\n', encoding="utf-8")

    with (
        patch(
            "tr_bot.agents.execute.read_circuit_state_snapshot",
            return_value={"tripped": True},
        ),
        patch("tr_bot.agents.execute.notify"),
    ):
        r = execute_action(
            {"type": "resume_entries", "reason": "ok", "confidence": 0.95, "params": {}},
            plan=_plan(),
            dry_run=False,
        )
    assert r.get("skipped") == "circuit_tripped"
    assert halt.is_file()


def test_resume_skips_when_fund_dd_halted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_HALT_REASON", "agents_supervisor_pause")
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_RESUME", True)
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr("tr_bot.config.AGENTS_COOLDOWN_SECONDS", 0)
    from tr_bot import config

    halt = tmp_path / config.TRADING_HALT_FILENAME
    halt.write_text('{"owner":"agent","reason":"agents_supervisor_pause"}\n', encoding="utf-8")

    with (
        patch("tr_bot.agents.execute.FundManager") as FM,
        patch(
            "tr_bot.agents.execute.read_circuit_state_snapshot",
            return_value={"tripped": False},
        ),
        patch("tr_bot.agents.execute.notify"),
    ):
        FM.return_value.get_state.return_value = MagicMock(
            halted=True, halt_reason="max_drawdown_exceeded (0.3>=0.25)"
        )
        FM.return_value.clear_manual_halt_if_agent.return_value = True
        r = execute_action(
            {"type": "resume_entries", "reason": "ok", "confidence": 0.95, "params": {}},
            plan=_plan(),
            dry_run=False,
        )
    assert r.get("skipped") == "fund_dd_halted"
    FM.return_value.clear_manual_halt_if_agent.assert_not_called()


def test_reset_circuit_calls_reset_for_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_RESET_CIRCUIT", True)
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr("tr_bot.config.AGENTS_COOLDOWN_SECONDS", 0)

    with (
        patch("tr_bot.agents.execute.reset_for_api") as reset,
        patch(
            "tr_bot.agents.execute.read_circuit_state_snapshot",
            return_value={"tripped": True, "consecutive_losses": 4, "max_consecutive_losses": 3},
        ),
        patch("tr_bot.agents.execute.FundManager") as FM,
        patch("tr_bot.agents.execute.notify"),
    ):
        FM.return_value.get_state.return_value = MagicMock(halted=False, halt_reason="")
        r = execute_action(
            {"type": "reset_circuit", "reason": "cooled", "confidence": 0.9, "params": {}},
            plan=_plan(),
            dry_run=False,
        )
    assert r.get("executed") is True
    reset.assert_called_once()


def test_low_confidence_skips(monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.8)
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_PAUSE", True)
    r = execute_action(
        {"type": "pause_entries", "reason": "x", "confidence": 0.5, "params": {}},
        plan=_plan(),
        dry_run=False,
    )
    assert r["skipped"] == "low_confidence"


def test_execute_plan_multiple(monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.5)
    rows = execute_plan(
        _plan(
            actions=[
                {"type": "noop", "reason": "", "confidence": 1.0, "params": {}},
                {"type": "notify", "reason": "hi", "confidence": 0.9, "params": {}},
            ]
        ),
        dry_run=True,
    )
    assert len(rows) == 2


def test_execute_plan_skips_second_mutating(monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.5)
    rows = execute_plan(
        _plan(
            actions=[
                {"type": "pause_entries", "reason": "a", "confidence": 0.9, "params": {}},
                {"type": "resume_entries", "reason": "b", "confidence": 0.9, "params": {}},
            ]
        ),
        dry_run=True,
    )
    assert rows[0].get("would_execute") is True
    assert rows[1].get("skipped") == "max_mutating_actions"


def test_emit_proposal_requires_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_EMIT_PROPOSAL", False)
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr("tr_bot.config.AGENTS_COOLDOWN_SECONDS", 0)
    r = execute_action(
        {"type": "emit_proposal", "reason": "x", "confidence": 0.9, "params": {}},
        plan=_plan(proposal={"schema_version": 1, "params": {"SIGNAL_RSI_OVERSOLD": 20}}),
        dry_run=False,
    )
    assert r.get("skipped") == "flag_off"


def test_rebalance_scan_forces_trims_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.config.AGENTS_AUTO_REBALANCE_SCAN", True)
    monkeypatch.setattr("tr_bot.config.AGENTS_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr("tr_bot.config.AGENTS_COOLDOWN_SECONDS", 0)

    with (
        patch("tr_bot.managers.rebalancer.scan_job", return_value={"ok": True, "execute_trims": False}) as scan,
        patch("tr_bot.agents.execute.notify"),
    ):
        r = execute_action(
            {"type": "rebalance_scan", "reason": "drift", "confidence": 0.9, "params": {}},
            plan=_plan(),
            dry_run=False,
        )
    assert r.get("executed") is True
    scan.assert_called_once_with(force_execute_trims=False)

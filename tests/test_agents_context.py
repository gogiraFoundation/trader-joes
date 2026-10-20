"""Context builders — fixture-based, no network LLM."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tr_bot.agents.context import build_fund_context, build_ops_context


def test_build_ops_context_shape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    from tr_bot import config

    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path))
    with (
        patch("tr_bot.agents.context.read_circuit_state_snapshot", return_value={"tripped": False}),
        patch("tr_bot.agents.context.ops_view_for_api", return_value={"heartbeat_ok": True}),
        patch("tr_bot.agents.context.read_trading_snapshot", return_value={}),
        patch("tr_bot.agents.context.read_recent_trades", return_value=[]),
    ):
        ctx = build_ops_context()
    assert ctx["role_context"] == "ops"
    assert "circuit" in ctx
    assert "fund" in ctx
    assert "config" in ctx
    assert "OPENAI_API_KEY" not in str(ctx)


def test_build_fund_context_includes_weights(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tr_bot.config.ARTIFACTS_DIR", str(tmp_path))
    with (
        patch("tr_bot.agents.context.read_circuit_state_snapshot", return_value={}),
        patch("tr_bot.agents.context.ops_view_for_api", return_value={}),
        patch("tr_bot.agents.context.read_trading_snapshot", return_value={}),
        patch("tr_bot.agents.context.read_recent_trades", return_value=[]),
        patch("tr_bot.agents.context.parse_portfolio_weights", return_value={"BTC/USDT": 1.0}),
    ):
        ctx = build_fund_context()
    assert ctx["role_context"] == "fund"
    assert ctx["portfolio_weights"] == {"BTC/USDT": 1.0}

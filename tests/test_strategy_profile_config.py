"""Strategy profile pointer + merge into config."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


def _reload_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> object:
    """Fresh import of tr_bot.config with isolated env and cwd."""
    import dotenv

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)

    for name in list(sys.modules.keys()):
        if name == "tr_bot.config" or name.startswith("tr_bot.strategy"):
            del sys.modules[name]
    import tr_bot.config as cfg

    return cfg


def test_no_pointer_env_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RISK_PER_TRADE", "0.02")
    monkeypatch.setenv("PAPER_TRADING", "true")
    cfg = _reload_config(monkeypatch, tmp_path)
    assert cfg.ACTIVE_STRATEGY_PROFILE_ID == ""
    assert cfg.RISK_PER_TRADE == pytest.approx(0.02)
    assert cfg.DEPLOYMENT_TIER == "paper"


def test_pointer_and_profile_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RISK_PER_TRADE", "0.02")
    monkeypatch.setenv("PAPER_TRADING", "true")
    dep = tmp_path / "deployment"
    dep.mkdir(parents=True)
    prof = tmp_path / "strategy_profiles"
    prof.mkdir(parents=True)
    (dep / "paper_active.json").write_text(
        json.dumps({"profile_id": "p1", "activated_at": "2025-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    (prof / "p1.json").write_text(
        json.dumps(
            {
                "profile_id": "p1",
                "schema_version": 1,
                "created_at": "2025-01-01T00:00:00Z",
                "params": {"RISK_PER_TRADE": 0.05, "SIGNAL_RSI_OVERSOLD": 28.0},
            }
        ),
        encoding="utf-8",
    )
    cfg = _reload_config(monkeypatch, tmp_path)
    assert cfg.ACTIVE_STRATEGY_PROFILE_ID == "p1"
    assert cfg.RISK_PER_TRADE == pytest.approx(0.05)
    assert cfg.SIGNAL_RSI_OVERSOLD == pytest.approx(28.0)


def test_deployment_tier_infer_live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.delenv("DEPLOYMENT_TIER", raising=False)
    cfg = _reload_config(monkeypatch, tmp_path)
    assert cfg.DEPLOYMENT_TIER == "live_full"

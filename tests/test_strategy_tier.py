"""Canary tier symbol filter and notional cap."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _reload_tier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    tier: str,
    canary_symbols: str = "",
    canary_cap: float = 0.0,
    mult: float = 1.0,
):
    import dotenv

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DEPLOYMENT_TIER", tier)
    monkeypatch.setenv("CANARY_SYMBOLS", canary_symbols)
    monkeypatch.setenv("CANARY_MAX_NOTIONAL_USDT", str(canary_cap))
    monkeypatch.setenv("CANARY_RISK_MULTIPLIER", str(mult))
    for name in list(sys.modules.keys()):
        if name == "tr_bot.config" or name.startswith("tr_bot.strategy"):
            del sys.modules[name]
    import tr_bot.config  # noqa: F401

    import tr_bot.strategy.tier as tier_mod

    return importlib.reload(tier_mod)


def test_canary_allows_all_when_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tm = _reload_tier(monkeypatch, tmp_path, tier="live_canary", canary_symbols="")
    assert tm.canary_allows_symbol("ETH/USDT") is True


def test_canary_symbol_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tm = _reload_tier(monkeypatch, tmp_path, tier="live_canary", canary_symbols="BTC/USDT, ETH/USDT")
    assert tm.canary_allows_symbol("BTC/USDT") is True
    assert tm.canary_allows_symbol("DOGE/USDT") is False


def test_canary_ignored_for_paper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tm = _reload_tier(monkeypatch, tmp_path, tier="paper", canary_symbols="BTC/USDT")
    assert tm.canary_allows_symbol("DOGE/USDT") is True


def test_canary_notional_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tm = _reload_tier(monkeypatch, tmp_path, tier="live_canary", canary_cap=100.0)
    out = tm.apply_canary_notional_cap(10.0, 50_000.0, 1.0)
    assert out == pytest.approx(100.0 / 50_000.0)


def test_effective_risk_canary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import dotenv

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DEPLOYMENT_TIER", "live_canary")
    monkeypatch.setenv("RISK_PER_TRADE", "0.02")
    monkeypatch.setenv("CANARY_RISK_MULTIPLIER", "0.5")
    for name in list(sys.modules.keys()):
        if name == "tr_bot.config" or name.startswith("tr_bot.strategy"):
            del sys.modules[name]
    import tr_bot.config as cfg

    assert cfg.effective_risk_per_trade() == pytest.approx(0.01)

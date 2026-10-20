"""Runtime fail-closed validation for live/paper config."""

from __future__ import annotations

import pytest

from tr_bot import runtime_validation as rv


def test_paper_software_polled_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rv.config, "PAPER_TRADING", True)
    monkeypatch.setattr(rv.config, "DEPLOYMENT_TIER", "paper")
    monkeypatch.setattr(rv.config, "EXIT_POLICY", "software_polled")
    assert rv.validate_runtime_config() == []


def test_exchange_native_rejected_even_in_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rv.config, "PAPER_TRADING", True)
    monkeypatch.setattr(rv.config, "DEPLOYMENT_TIER", "paper")
    monkeypatch.setattr(rv.config, "EXIT_POLICY", "exchange_native")
    errs = rv.validate_runtime_config()
    assert any("exit_policy_exchange_native_disabled" in e for e in errs)
    with pytest.raises(RuntimeError, match="runtime_config_unsafe"):
        rv.assert_runtime_config_safe()


def test_live_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rv.config, "PAPER_TRADING", False)
    monkeypatch.setattr(rv.config, "DEPLOYMENT_TIER", "live_full")
    monkeypatch.setattr(rv.config, "EXIT_POLICY", "software_polled")
    monkeypatch.setattr(rv.config, "BINANCE_API_KEY", "")
    monkeypatch.setattr(rv.config, "BINANCE_SECRET", "")
    monkeypatch.setattr(rv.config, "LIVE_MAX_NOTIONAL_USDT", 100.0)
    monkeypatch.setattr(rv, "active_symbols", lambda: ["BTC/USDT"])
    errs = rv.validate_runtime_config()
    assert "live_missing_binance_api_key" in errs
    assert "live_missing_binance_secret" in errs


def test_live_requires_notional_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rv.config, "PAPER_TRADING", False)
    monkeypatch.setattr(rv.config, "DEPLOYMENT_TIER", "live_full")
    monkeypatch.setattr(rv.config, "EXIT_POLICY", "software_polled")
    monkeypatch.setattr(rv.config, "BINANCE_API_KEY", "k")
    monkeypatch.setattr(rv.config, "BINANCE_SECRET", "s")
    monkeypatch.setattr(rv.config, "LIVE_MAX_NOTIONAL_USDT", 0.0)
    monkeypatch.setattr(rv, "active_symbols", lambda: ["BTC/USDT"])
    errs = rv.validate_runtime_config()
    assert "live_max_notional_usdt_required" in errs


def test_live_rejects_non_ccxt_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rv.config, "PAPER_TRADING", False)
    monkeypatch.setattr(rv.config, "DEPLOYMENT_TIER", "live_full")
    monkeypatch.setattr(rv.config, "EXIT_POLICY", "software_polled")
    monkeypatch.setattr(rv.config, "BINANCE_API_KEY", "k")
    monkeypatch.setattr(rv.config, "BINANCE_SECRET", "s")
    monkeypatch.setattr(rv.config, "LIVE_MAX_NOTIONAL_USDT", 50.0)
    monkeypatch.setattr(rv, "active_symbols", lambda: ["SPY"])
    errs = rv.validate_runtime_config()
    assert any(e.startswith("live_requires_ccxt_pair:") for e in errs)


def test_canary_requires_safe_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rv.config, "PAPER_TRADING", False)
    monkeypatch.setattr(rv.config, "DEPLOYMENT_TIER", "live_canary")
    monkeypatch.setattr(rv.config, "EXIT_POLICY", "software_polled")
    monkeypatch.setattr(rv.config, "BINANCE_API_KEY", "k")
    monkeypatch.setattr(rv.config, "BINANCE_SECRET", "s")
    monkeypatch.setattr(rv.config, "LIVE_MAX_NOTIONAL_USDT", 50.0)
    monkeypatch.setattr(rv.config, "CANARY_SYMBOLS", "")
    monkeypatch.setattr(rv.config, "CANARY_RISK_MULTIPLIER", 1.0)
    monkeypatch.setattr(rv.config, "CANARY_MAX_NOTIONAL_USDT", 0.0)
    monkeypatch.setattr(rv, "active_symbols", lambda: ["BTC/USDT"])
    errs = rv.validate_runtime_config()
    assert "live_canary_requires_canary_symbols" in errs
    assert any("live_canary_risk_multiplier_out_of_range" in e for e in errs)
    assert any("live_canary_max_notional_out_of_range" in e for e in errs)


def test_canary_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rv.config, "PAPER_TRADING", False)
    monkeypatch.setattr(rv.config, "DEPLOYMENT_TIER", "live_canary")
    monkeypatch.setattr(rv.config, "EXIT_POLICY", "software_polled")
    monkeypatch.setattr(rv.config, "BINANCE_API_KEY", "k")
    monkeypatch.setattr(rv.config, "BINANCE_SECRET", "s")
    monkeypatch.setattr(rv.config, "LIVE_MAX_NOTIONAL_USDT", 50.0)
    monkeypatch.setattr(rv.config, "CANARY_SYMBOLS", "BTC/USDT")
    monkeypatch.setattr(rv.config, "CANARY_RISK_MULTIPLIER", 0.25)
    monkeypatch.setattr(rv.config, "CANARY_MAX_NOTIONAL_USDT", 50.0)
    monkeypatch.setattr(rv, "active_symbols", lambda: ["BTC/USDT"])
    assert rv.validate_runtime_config() == []


def test_preflight_includes_runtime_errors(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from tr_bot.startup import preflight

    monkeypatch.setattr(preflight.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(preflight.config, "DEPLOYMENT_TIER", "paper")
    monkeypatch.setattr(preflight.config, "PAPER_TRADING", True)
    monkeypatch.setattr(preflight.config, "EXIT_POLICY", "exchange_native")
    monkeypatch.setattr(preflight.config, "ACTIVE_STRATEGY_PROFILE_ID", "")
    monkeypatch.setattr(preflight.config, "RISK_PER_TRADE", 0.01)
    monkeypatch.setattr(preflight.config, "MAX_TRADES_PER_DAY", 3)
    monkeypatch.setattr(preflight.config, "REDIS_URL", "redis://x")
    monkeypatch.setattr(preflight.config, "DATABASE_URL", "postgres://x")
    monkeypatch.setattr(preflight.config, "readiness_require_postgres", lambda: False)
    monkeypatch.setattr(preflight.config, "COINDESK_REFERENCE_CHECK_ENABLED", False)
    monkeypatch.setattr(preflight.config, "coindesk_runtime_validation_errors", lambda: [])
    monkeypatch.setattr(preflight, "check_redis", lambda _: {"ok": True})
    monkeypatch.setattr(preflight, "check_postgres", lambda _: {"ok": True})
    out = preflight.run_preflight_checks()
    assert out["ok"] is False
    assert any("exit_policy_exchange_native_disabled" in str(e) for e in out["errors"])

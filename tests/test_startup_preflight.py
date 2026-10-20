from __future__ import annotations

from pathlib import Path

from tr_bot.startup import preflight
from tr_bot.strategy import deployment_io as dep


def _base_preflight_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(preflight.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(preflight.config, "DEPLOYMENT_TIER", "paper")
    monkeypatch.setattr(preflight.config, "PAPER_TRADING", True)
    monkeypatch.setattr(preflight.config, "EXIT_POLICY", "software_polled")
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


def test_preflight_warns_live_twap(monkeypatch, tmp_path: Path) -> None:
    _base_preflight_env(monkeypatch, tmp_path)
    monkeypatch.setattr(preflight.config, "DEPLOYMENT_TIER", "live_full")
    monkeypatch.setattr(preflight.config, "PAPER_TRADING", False)
    monkeypatch.setattr(preflight.config, "EXECUTION_STYLE", "twap")
    monkeypatch.setattr(preflight.config, "BINANCE_API_KEY", "k")
    monkeypatch.setattr(preflight.config, "BINANCE_SECRET", "s")
    monkeypatch.setattr(preflight.config, "LIVE_MAX_NOTIONAL_USDT", 100.0)
    monkeypatch.setattr("tr_bot.runtime_validation.active_symbols", lambda: ["BTC/USDT"])
    out = preflight.run_preflight_checks()
    assert out["ok"] is True
    assert any("binance_live_twap" in w for w in (out.get("warnings") or []))


def test_preflight_ok_with_mocked_deps(monkeypatch, tmp_path: Path):
    _base_preflight_env(monkeypatch, tmp_path)
    out = preflight.run_preflight_checks()
    assert out["ok"] is True
    assert out["errors"] == []
    assert "cmc" in out
    assert out["cmc"]["train_sources_includes_coinmarketcap"] is False


def test_preflight_cmc_warns_coinmarketcap_without_key(monkeypatch, tmp_path: Path) -> None:
    _base_preflight_env(monkeypatch, tmp_path)
    monkeypatch.setattr(preflight.config, "TRAIN_SOURCES", "yahoo,coinmarketcap")
    monkeypatch.setattr(preflight.config, "CMC_API_KEY", "")
    monkeypatch.setattr(preflight.config, "CMC_FEAR_GREED_ENABLED", False)
    monkeypatch.setattr(preflight.config, "CMC_ALTCOIN_SEASON_ENABLED", False)
    monkeypatch.setattr(preflight.config, "CMC_TRAIN_MACRO_MERGE_ENABLED", False)
    monkeypatch.setattr(preflight.config, "TRAIN_REGIME_MACRO_FEATURES", False)

    out = preflight.run_preflight_checks()
    assert out["ok"] is True
    assert out["cmc"]["train_sources_includes_coinmarketcap"] is True
    assert any("coinmarketcap" in w and "CMC_API_KEY" in w for w in (out.get("warnings") or []))


def test_preflight_flags_missing_active_profile(monkeypatch, tmp_path: Path):
    _base_preflight_env(monkeypatch, tmp_path)
    dep.atomic_write_json(dep.pointer_path(tmp_path, "paper"), {"profile_id": "missing", "activated_at": "now"})

    out = preflight.run_preflight_checks()
    assert out["ok"] is False
    assert any(str(e).startswith("active_profile_missing:") for e in out["errors"])


def test_preflight_active_strategy_profile_id_prefers_pointer(
    monkeypatch, tmp_path: Path
) -> None:
    """Report profile id from tier pointer (on-disk), not stale in-process config."""
    profiles = tmp_path / "strategy_profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    pid = "from_pointer"
    (profiles / f"{pid}.json").write_text(
        '{"profile_id": "from_pointer", "schema_version": 1, "params": {}}',
        encoding="utf-8",
    )
    dep.atomic_write_json(
        dep.pointer_path(tmp_path, "paper"),
        {"profile_id": pid, "activated_at": "now"},
    )

    _base_preflight_env(monkeypatch, tmp_path)
    monkeypatch.setattr(preflight.config, "ACTIVE_STRATEGY_PROFILE_ID", "stale_config_id")

    out = preflight.run_preflight_checks()
    assert out["ok"] is True
    assert out["active_strategy_profile_id"] == pid

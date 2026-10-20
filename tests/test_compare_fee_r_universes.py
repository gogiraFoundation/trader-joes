"""Smoke tests for scripts/compare_fee_r_universes.py (mocked backtests)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "compare_fee_r_universes.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("compare_fee_r_universes", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rank_and_recommendation(tmp_path: Path) -> None:
    mod = _load_mod()

    def fake_run(symbol: str, *, timeframe: str = "1h") -> dict:
        table = {
            "BTC/USDT": {"ok": True, "profit_factor": 1.1, "total_return_pct": 2.0, "trades": 10},
            "ETH/USDT": {"ok": True, "profit_factor": 1.4, "total_return_pct": 5.0, "trades": 12},
            "SOL/USDT": {"ok": True, "profit_factor": 0.9, "total_return_pct": -1.0, "trades": 8},
        }
        row = dict(table[symbol])
        row["symbol"] = symbol
        row["timeframe"] = timeframe
        return row

    payload = mod.build_compare_payload(run_fn=fake_run, applied={"TIMEFRAME": "1h"})
    ranked = payload["ranked"]
    assert ranked[0]["symbol"] == "ETH/USDT"
    assert ranked[1]["symbol"] == "BTC/USDT"
    weights = payload["recommendation"]["portfolio_weights"]
    assert weights == {"ETH/USDT": 0.5, "BTC/USDT": 0.5}

    path = mod.write_compare(payload, artifacts_dir=tmp_path)
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["recommendation"]["portfolio_weights"]["ETH/USDT"] == 0.5


def test_all_fail_recommendation() -> None:
    mod = _load_mod()

    def fail(symbol: str, *, timeframe: str = "1h") -> dict:
        return {"ok": False, "symbol": symbol, "timeframe": timeframe, "error": "boom"}

    payload = mod.build_compare_payload(run_fn=fail)
    assert payload["recommendation"]["portfolio_weights"] is None


def test_soak_defaults_force_min_atr_over_env_002(monkeypatch) -> None:
    """Bake-off must not keep a leftover MIN_ATR_PCT=0.002 from .env."""
    from tr_bot import config

    mod = _load_mod()
    monkeypatch.setattr(config, "MIN_ATR_PCT", 0.002)
    monkeypatch.setattr(config, "LIVE_FEE_BPS", 10.0)
    monkeypatch.setattr(config, "BACKTEST_FEE_BPS", 10.0)
    monkeypatch.setattr(config, "LIVE_FEE_TAKER_BPS", 12.0)
    monkeypatch.setattr(config, "BACKTEST_TAKER_BPS", 12.0)
    applied = mod._apply_soak_cost_defaults()
    assert applied["MIN_ATR_PCT"] == 0.0045
    assert config.MIN_ATR_PCT == 0.0045
    assert config.LIVE_FEE_BPS == 0.0
    assert config.BACKTEST_FEE_BPS == 0.0
    assert config.LIVE_FEE_TAKER_BPS == 0.0
    assert config.BACKTEST_TAKER_BPS == 0.0
    assert config.BINANCE_PAY_FEES_WITH_BNB is True
    assert config.LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS is True


def test_main_dry_run_no_network(tmp_path: Path, monkeypatch) -> None:
    from tr_bot import config

    mod = _load_mod()
    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path))
    rc = mod.main(["--dry-run"])
    assert rc == 0
    out = tmp_path / "research" / "fee_r_universe_compare.json"
    assert out.is_file()
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["config_applied"].get("dry_run") is True
    assert all(r.get("error") == "dry_run" for r in raw["results"])

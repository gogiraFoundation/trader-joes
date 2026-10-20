"""MAX_OPEN_POSITIONS portfolio concurrency gate."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from tr_bot import engine
from tr_bot.strategy.entry_gates import open_positions_at_limit


class _EndLoop(Exception):
    pass


class _DummyRegimeModel:
    def train(self, _df: pd.DataFrame) -> None:
        return None

    def predict_with_confidence(self, _df: pd.DataFrame) -> tuple[str, float]:
        return "range", 0.9


class _DummyCircuit:
    tripped = False

    def record_outcome(self, _won: bool) -> None:
        return None


def test_open_positions_at_limit_helper() -> None:
    assert open_positions_at_limit({}, 0) is False
    assert open_positions_at_limit({}, 1) is False
    assert open_positions_at_limit({"A": object()}, 1) is True
    assert open_positions_at_limit({"A": object()}, 2) is False
    assert open_positions_at_limit({"A": object(), "B": None}, 1) is True


def test_max_open_blocks_second_symbol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sym_a = "ETH/USDT"
    sym_b = "SOL/USDT"
    base_row = {
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 10.0,
        "rsi": 20.0,
        "ema20": 105.0,
        "ema50": 110.0,
        "atr": 2.0,
    }
    df = pd.DataFrame([dict(base_row) for _ in range(25)])

    monkeypatch.setattr(engine.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(engine.config, "TRADING_HALT_FILENAME", "TRADING_HALT")
    monkeypatch.setattr(engine.config, "MANAGERS_ENABLED", False)
    monkeypatch.setattr(engine.config, "PAPER_TRADING", True)
    monkeypatch.setattr(engine.config, "PAPER_EQUITY_USDT", 10_000.0)
    monkeypatch.setattr(engine.config, "REGIME_MODEL_PATH", "")
    monkeypatch.setattr(engine.config, "ADVISORY_WORKER_IN_PROCESS", False)
    monkeypatch.setattr(engine.config, "USE_LLM", False)
    monkeypatch.setattr(engine.config, "POLL_SECONDS", 0.0)
    monkeypatch.setattr(engine.config, "DECISION_AUDIT_JSONL_PATH", "")
    monkeypatch.setattr(engine.config, "ADAPTATION_ENABLED", False)
    monkeypatch.setattr(engine.config, "USE_EXTENDED_TA", False)
    monkeypatch.setattr(engine.config, "USE_SIGNAL_STRENGTH_FILTER", False)
    monkeypatch.setattr(engine.config, "USE_MICROSTRUCTURE_GUARD", False)
    monkeypatch.setattr(engine.config, "MICRO_USE_SPREAD_DEPTH_GUARD", False)
    monkeypatch.setattr(engine.config, "USE_FUNDAMENTALS_FILTER", False)
    monkeypatch.setattr(engine.config, "REGIME_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr(engine.config, "TIMEFRAME", "1h")
    monkeypatch.setattr(engine.config, "INITIAL_BARS", 50)
    monkeypatch.setattr(engine.config, "LOOP_BARS", 50)
    monkeypatch.setattr(engine.config, "EXIT_POLICY", "software_polled")
    monkeypatch.setattr(engine.config, "EXIT_MODE", "atr")
    monkeypatch.setattr(engine.config, "STRATEGY_ENGINE", "legacy")
    monkeypatch.setattr(engine.config, "DEPLOYMENT_TIER", "paper")
    monkeypatch.setattr(engine.config, "ACTIVE_STRATEGY_PROFILE_ID", "test_profile")
    monkeypatch.setattr(engine.config, "ACTIVE_STRATEGY_CONFIG_HASH", "abc")
    monkeypatch.setattr(engine.config, "MAX_TRADES_PER_DAY", 5)
    monkeypatch.setattr(engine.config, "RISK_PER_TRADE", 0.01)
    monkeypatch.setattr(engine.config, "MAX_OPEN_POSITIONS", 1)
    monkeypatch.setattr(engine.config, "MIN_ATR_PCT", 0.0)
    monkeypatch.setattr(engine.config, "effective_risk_per_trade", lambda: 0.01)

    existing = SimpleNamespace(
        side="long",
        entry_price=100.0,
        size=1.0,
        stop_price=98.0,
        take_price=103.0,
        check_bar=lambda _bar: (False, None),
    )

    monkeypatch.setattr(engine, "active_symbols", lambda: [sym_a, sym_b])
    monkeypatch.setattr(engine, "load_open_positions", lambda: {sym_a: existing})
    monkeypatch.setattr(engine, "save_open_positions", lambda _p: None)
    monkeypatch.setattr(engine, "fetch_ohlcv", lambda *_a, **_k: df.copy())
    monkeypatch.setattr(engine, "compute_indicators", lambda d: d)
    monkeypatch.setattr(engine, "compute_all_indicators", lambda d: d)
    monkeypatch.setattr(engine, "check_redis", lambda _url: {"ok": True})
    monkeypatch.setattr(engine, "check_postgres", lambda _url: {"ok": True})
    monkeypatch.setattr(engine, "get_global_metrics_snapshot", lambda: {})
    monkeypatch.setattr(engine, "get_fear_greed_snapshot", lambda: {})
    monkeypatch.setattr(engine, "get_altcoin_season_snapshot", lambda: {})
    monkeypatch.setattr(engine, "append_equity_point", lambda _nav: None)
    monkeypatch.setattr(engine, "maybe_write_daily_rollup", lambda: None)
    monkeypatch.setattr(engine, "write_runtime_error", lambda _e: None)
    monkeypatch.setattr(engine, "write_runtime_success", lambda: None)
    monkeypatch.setattr(engine, "weight_for", lambda _s: 0.5)
    monkeypatch.setattr(engine, "apply_canary_notional_cap", lambda size, *_a, **_k: size)
    monkeypatch.setattr(engine, "apply_paper_notional_cap", lambda size, *_a, **_k: size)
    monkeypatch.setattr(engine, "canary_allows_symbol", lambda _s: True)
    monkeypatch.setattr(engine, "usdt_per_quote_unit", lambda _q: 1.0)
    monkeypatch.setattr(engine, "notify", lambda *_a, **_k: None)

    import tr_bot.runtime_validation as rtv
    import tr_bot.exit_coordinator as exit_coord
    import tr_bot.volatility_regime as vol_reg

    monkeypatch.setattr(rtv, "assert_runtime_config_safe", lambda: None)
    monkeypatch.setattr(exit_coord, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(vol_reg, "classify_volatility", lambda _df: ("normal", 0.0))

    opened: list[str] = []

    def _exec(sym, side, size, *, client_order_id=None):
        opened.append(sym)
        raise AssertionError(f"should not open when at max_open_positions: {sym}")

    monkeypatch.setattr(engine, "execute_trade", _exec)
    monkeypatch.setattr(
        engine,
        "_evaluate_symbol_legacy",
        lambda *_a, **_k: {
            "regime": "range",
            "regime_confidence": 0.9,
            "signal": "buy",
            "sentiment": "neutral",
            "ta_ok": True,
            "fundamentals_ok": True,
            "decision": "buy",
            "reason": None,
            "signal_branch": "mean_reversion",
            "smc_proposal": None,
        },
    )
    monkeypatch.setattr(engine, "RegimeModel", _DummyRegimeModel)
    monkeypatch.setattr(engine, "CircuitBreaker", _DummyCircuit)

    snapshots: list[dict] = []
    monkeypatch.setattr(engine, "write_trading_snapshot", lambda payload: snapshots.append(payload))

    def stop_after_one_loop(_seconds: float) -> None:
        raise _EndLoop()

    monkeypatch.setattr(engine.time, "sleep", stop_after_one_loop)

    with pytest.raises(_EndLoop):
        engine.run_forever()

    assert not opened
    assert snapshots
    view_b = snapshots[-1]["by_symbol"][sym_b]
    assert view_b.get("reason") == "max_open_positions"
    assert view_b.get("action") == "blocked"
    # Holding symbol still reported as holding
    assert snapshots[-1]["by_symbol"][sym_a].get("holding") is True


def test_max_open_zero_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine.config, "MAX_OPEN_POSITIONS", 0)
    assert open_positions_at_limit({"A": object(), "B": object()}, 0) is False


def test_max_open_blocks_second_symbol_same_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Opening A mid-loop must count toward the cap before B is evaluated."""
    sym_a = "ETH/USDT"
    sym_b = "SOL/USDT"
    base_row = {
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 10.0,
        "rsi": 20.0,
        "ema20": 105.0,
        "ema50": 110.0,
        "atr": 2.0,
    }
    df = pd.DataFrame([dict(base_row) for _ in range(25)])

    monkeypatch.setattr(engine.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(engine.config, "TRADING_HALT_FILENAME", "TRADING_HALT")
    monkeypatch.setattr(engine.config, "MANAGERS_ENABLED", False)
    monkeypatch.setattr(engine.config, "PAPER_TRADING", True)
    monkeypatch.setattr(engine.config, "PAPER_EQUITY_USDT", 10_000.0)
    monkeypatch.setattr(engine.config, "REGIME_MODEL_PATH", "")
    monkeypatch.setattr(engine.config, "ADVISORY_WORKER_IN_PROCESS", False)
    monkeypatch.setattr(engine.config, "USE_LLM", False)
    monkeypatch.setattr(engine.config, "POLL_SECONDS", 0.0)
    monkeypatch.setattr(engine.config, "DECISION_AUDIT_JSONL_PATH", "")
    monkeypatch.setattr(engine.config, "ADAPTATION_ENABLED", False)
    monkeypatch.setattr(engine.config, "USE_EXTENDED_TA", False)
    monkeypatch.setattr(engine.config, "USE_SIGNAL_STRENGTH_FILTER", False)
    monkeypatch.setattr(engine.config, "USE_MICROSTRUCTURE_GUARD", False)
    monkeypatch.setattr(engine.config, "MICRO_USE_SPREAD_DEPTH_GUARD", False)
    monkeypatch.setattr(engine.config, "USE_FUNDAMENTALS_FILTER", False)
    monkeypatch.setattr(engine.config, "REGIME_MIN_CONFIDENCE", 0.5)
    monkeypatch.setattr(engine.config, "TIMEFRAME", "1h")
    monkeypatch.setattr(engine.config, "INITIAL_BARS", 50)
    monkeypatch.setattr(engine.config, "LOOP_BARS", 50)
    monkeypatch.setattr(engine.config, "EXIT_POLICY", "software_polled")
    monkeypatch.setattr(engine.config, "EXIT_MODE", "atr")
    monkeypatch.setattr(engine.config, "STRATEGY_ENGINE", "legacy")
    monkeypatch.setattr(engine.config, "DEPLOYMENT_TIER", "paper")
    monkeypatch.setattr(engine.config, "ACTIVE_STRATEGY_PROFILE_ID", "test_profile")
    monkeypatch.setattr(engine.config, "ACTIVE_STRATEGY_CONFIG_HASH", "abc")
    monkeypatch.setattr(engine.config, "MAX_TRADES_PER_DAY", 5)
    monkeypatch.setattr(engine.config, "RISK_PER_TRADE", 0.01)
    monkeypatch.setattr(engine.config, "MAX_OPEN_POSITIONS", 1)
    monkeypatch.setattr(engine.config, "MIN_ATR_PCT", 0.0)
    monkeypatch.setattr(engine.config, "effective_risk_per_trade", lambda: 0.01)

    monkeypatch.setattr(engine, "active_symbols", lambda: [sym_a, sym_b])
    monkeypatch.setattr(engine, "load_open_positions", lambda: {})
    monkeypatch.setattr(engine, "save_open_positions", lambda _p: None)
    monkeypatch.setattr(engine, "fetch_ohlcv", lambda *_a, **_k: df.copy())
    monkeypatch.setattr(engine, "compute_indicators", lambda d: d)
    monkeypatch.setattr(engine, "compute_all_indicators", lambda d: d)
    monkeypatch.setattr(engine, "check_redis", lambda _url: {"ok": True})
    monkeypatch.setattr(engine, "check_postgres", lambda _url: {"ok": True})
    monkeypatch.setattr(engine, "get_global_metrics_snapshot", lambda: {})
    monkeypatch.setattr(engine, "get_fear_greed_snapshot", lambda: {})
    monkeypatch.setattr(engine, "get_altcoin_season_snapshot", lambda: {})
    monkeypatch.setattr(engine, "append_equity_point", lambda _nav: None)
    monkeypatch.setattr(engine, "maybe_write_daily_rollup", lambda: None)
    monkeypatch.setattr(engine, "write_runtime_error", lambda _e: None)
    monkeypatch.setattr(engine, "write_runtime_success", lambda: None)
    monkeypatch.setattr(engine, "weight_for", lambda _s: 0.5)
    monkeypatch.setattr(engine, "apply_canary_notional_cap", lambda size, *_a, **_k: size)
    monkeypatch.setattr(engine, "apply_paper_notional_cap", lambda size, *_a, **_k: size)
    monkeypatch.setattr(engine, "canary_allows_symbol", lambda _s: True)
    monkeypatch.setattr(engine, "usdt_per_quote_unit", lambda _q: 1.0)
    monkeypatch.setattr(engine, "notify", lambda *_a, **_k: None)

    import tr_bot.runtime_validation as rtv
    import tr_bot.exit_coordinator as exit_coord
    import tr_bot.volatility_regime as vol_reg

    monkeypatch.setattr(rtv, "assert_runtime_config_safe", lambda: None)
    monkeypatch.setattr(exit_coord, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(vol_reg, "classify_volatility", lambda _df: ("normal", 0.0))

    opened: list[str] = []

    def _exec(sym, side, size, *, client_order_id=None):
        opened.append(sym)
        return {
            "id": client_order_id or f"paper-{sym}",
            "status": "closed",
            "average": 100.5,
            "filled": size,
            "amount": size,
            "success": True,
            "entry_execution": "market",
            "clientOrderId": client_order_id,
        }

    monkeypatch.setattr(engine, "execute_trade", _exec)
    monkeypatch.setattr(
        engine,
        "_evaluate_symbol_legacy",
        lambda *_a, **_k: {
            "regime": "range",
            "regime_confidence": 0.9,
            "signal": "buy",
            "sentiment": "neutral",
            "ta_ok": True,
            "fundamentals_ok": True,
            "decision": "buy",
            "reason": None,
            "signal_branch": "mean_reversion",
            "smc_proposal": None,
        },
    )
    monkeypatch.setattr(engine, "RegimeModel", _DummyRegimeModel)
    monkeypatch.setattr(engine, "CircuitBreaker", _DummyCircuit)

    snapshots: list[dict] = []
    monkeypatch.setattr(engine, "write_trading_snapshot", lambda payload: snapshots.append(payload))

    def stop_after_one_loop(_seconds: float) -> None:
        raise _EndLoop()

    monkeypatch.setattr(engine.time, "sleep", stop_after_one_loop)

    with pytest.raises(_EndLoop):
        engine.run_forever()

    assert opened == [sym_a], f"expected only {sym_a} opened, got {opened}"
    assert snapshots
    view_b = snapshots[-1]["by_symbol"][sym_b]
    assert view_b.get("reason") == "max_open_positions"
    assert view_b.get("action") == "blocked"

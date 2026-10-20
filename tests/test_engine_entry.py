"""Engine successful-entry path: fill price, notify, snapshot, no runtime error."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tr_bot import engine
from tr_bot.notifications import events as notify_events


class _EndLoop(Exception):
    pass


class _DummyRegimeModel:
    def train(self, _df: pd.DataFrame) -> None:
        return None

    def predict_with_confidence(self, _df: pd.DataFrame) -> tuple[str, float]:
        return "range", 0.9


class _DummyCircuit:
    def __init__(self) -> None:
        self.tripped = False

    def record_outcome(self, _won: bool) -> None:
        return None


def test_successful_entry_uses_entry_px_and_completes_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    symbol = "BTC/USDT"
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
    rows = [dict(base_row) for _ in range(25)]
    df = pd.DataFrame(rows)

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
    monkeypatch.setattr(engine.config, "TIMEFRAME", "5m")
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
    monkeypatch.setattr(engine.config, "effective_risk_per_trade", lambda: 0.01)

    monkeypatch.setattr(engine, "active_symbols", lambda: [symbol])
    monkeypatch.setattr(engine, "load_open_positions", lambda: {})
    saved: list[dict] = []

    def _save(positions: dict) -> None:
        saved.append({k: v for k, v in positions.items() if v is not None})

    monkeypatch.setattr(engine, "save_open_positions", _save)
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
    monkeypatch.setattr(engine, "write_runtime_error", lambda _e: (_ for _ in ()).throw(AssertionError(f"runtime error: {_e}")))
    monkeypatch.setattr(engine, "weight_for", lambda _s: 1.0)
    monkeypatch.setattr(engine, "apply_canary_notional_cap", lambda size, *_a, **_k: size)
    monkeypatch.setattr(engine, "canary_allows_symbol", lambda _s: True)
    monkeypatch.setattr(engine, "usdt_per_quote_unit", lambda _q: 1.0)

    # Patch runtime validation import path used inside run_forever
    import tr_bot.runtime_validation as rtv
    import tr_bot.exit_coordinator as exit_coord
    import tr_bot.volatility_regime as vol_reg

    monkeypatch.setattr(rtv, "assert_runtime_config_safe", lambda: None)
    monkeypatch.setattr(exit_coord, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(exit_coord, "append_closed_trade", lambda _row: None)
    monkeypatch.setattr(exit_coord, "live_fee_bps_for_symbol", lambda _s: 0.0)
    monkeypatch.setattr(exit_coord.config, "PAPER_TRADING", True)
    monkeypatch.setattr(vol_reg, "classify_volatility", lambda _df: ("normal", 0.0))

    notify_calls: list[tuple] = []
    monkeypatch.setattr(
        engine,
        "notify",
        lambda event, **payload: notify_calls.append((event, payload)),
    )

    snapshots: list[dict] = []
    monkeypatch.setattr(engine, "write_trading_snapshot", lambda payload: snapshots.append(payload))
    runtime_ok: list[bool] = []
    monkeypatch.setattr(engine, "write_runtime_success", lambda: runtime_ok.append(True))

    monkeypatch.setattr(
        engine,
        "execute_trade",
        lambda sym, side, size, *, client_order_id=None: {
            "id": client_order_id or "paper-fill-1",
            "status": "closed",
            "average": 100.5,
            "filled": size,
            "amount": size,
            "success": True,
            "entry_execution": "market",
            "clientOrderId": client_order_id,
        },
    )
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

    def stop_after_one_loop(_seconds: float) -> None:
        raise _EndLoop()

    monkeypatch.setattr(engine.time, "sleep", stop_after_one_loop)

    with pytest.raises(_EndLoop):
        engine.run_forever()

    assert saved, "open position should be persisted"
    pos = next(iter(saved[-1].values()))
    assert float(pos.entry_price) == pytest.approx(100.5)
    assert float(pos.size) > 0

    opened = [c for c in notify_calls if c[0] == notify_events.TRADE_OPENED]
    assert opened, "TRADE_OPENED notification expected"
    assert opened[0][1]["entry"] == pytest.approx(100.5)

    assert snapshots, "trading snapshot should be published"
    view = snapshots[-1]["by_symbol"][symbol]
    assert view.get("action") == "opened"
    assert view.get("entry") == pytest.approx(100.5)
    assert runtime_ok, "cycle should complete with write_runtime_success"

"""Engine pause semantics: manual halt blocks new entries but still manages exits."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tr_bot import engine
from tr_bot.position import OpenPosition


class _EndLoop(Exception):
    pass


class _DummyRegimeModel:
    def train(self, _df: pd.DataFrame) -> None:
        return None


class _DummyCircuit:
    def __init__(self) -> None:
        self.tripped = False
        self.recorded: list[bool] = []

    def record_outcome(self, won: bool) -> None:
        self.recorded.append(bool(won))


def test_manual_halt_allows_exit_management_but_blocks_new_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    symbol = "BTC/USDT"
    halt_file = tmp_path / "TRADING_HALT"
    halt_file.write_text("pause\n", encoding="utf-8")

    base_row = {
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1.0,
        "rsi": 50.0,
        "ema20": 100.0,
        "ema50": 100.0,
        "atr": 5.0,
    }
    rows = [dict(base_row) for _ in range(19)]
    rows.append(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 94.0,
            "close": 95.0,
            "volume": 1.0,
            "rsi": 45.0,
            "ema20": 99.0,
            "ema50": 100.0,
            "atr": 5.0,
        }
    )
    df = pd.DataFrame(rows)
    pos = OpenPosition(symbol=symbol, side="long", entry_price=100.0, atr=5.0, size=1.0)

    monkeypatch.setattr(engine.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(engine.config, "TRADING_HALT_FILENAME", "TRADING_HALT")
    monkeypatch.setattr(engine.config, "MANAGERS_ENABLED", False)
    monkeypatch.setattr(engine.config, "PAPER_TRADING", True)
    monkeypatch.setattr(engine.config, "PAPER_EQUITY_USDT", 1000.0)
    monkeypatch.setattr(engine.config, "REGIME_MODEL_PATH", "")
    monkeypatch.setattr(engine.config, "ADVISORY_WORKER_IN_PROCESS", False)
    monkeypatch.setattr(engine.config, "USE_LLM", False)
    monkeypatch.setattr(engine.config, "POLL_SECONDS", 0.0)
    monkeypatch.setattr(engine.config, "DECISION_AUDIT_JSONL_PATH", "")
    monkeypatch.setattr(engine.config, "ADAPTATION_ENABLED", False)
    monkeypatch.setattr(engine.config, "USE_EXTENDED_TA", False)
    monkeypatch.setattr(engine.config, "TIMEFRAME", "5m")
    monkeypatch.setattr(engine.config, "INITIAL_BARS", 50)
    monkeypatch.setattr(engine.config, "LOOP_BARS", 50)

    monkeypatch.setattr(engine, "active_symbols", lambda: [symbol])
    monkeypatch.setattr(engine, "load_open_positions", lambda: {symbol: pos})
    monkeypatch.setattr(engine, "save_open_positions", lambda _positions: None)
    monkeypatch.setattr(engine, "fetch_ohlcv", lambda *_a, **_k: df.copy())
    monkeypatch.setattr(engine, "compute_indicators", lambda d: d)
    monkeypatch.setattr(engine, "compute_all_indicators", lambda d: d)
    monkeypatch.setattr(engine, "check_redis", lambda _url: {"ok": True})
    monkeypatch.setattr(engine, "check_postgres", lambda _url: {"ok": True})
    monkeypatch.setattr(engine, "get_global_metrics_snapshot", lambda: {})
    monkeypatch.setattr(engine, "get_fear_greed_snapshot", lambda: {})
    monkeypatch.setattr(engine, "get_altcoin_season_snapshot", lambda: {})
    monkeypatch.setattr(engine, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "append_equity_point", lambda _nav: None)
    monkeypatch.setattr(engine, "write_runtime_success", lambda: None)
    monkeypatch.setattr(engine, "maybe_write_daily_rollup", lambda: None)
    monkeypatch.setattr(engine, "write_runtime_error", lambda _e: None)

    closed_rows: list[dict] = []
    snapshots: list[dict] = []
    trades: list[tuple[str, str, float]] = []
    circuit_holder: dict[str, _DummyCircuit] = {}

    import tr_bot.exit_coordinator as exit_coord
    import tr_bot.runtime_validation as rtv

    monkeypatch.setattr(rtv, "assert_runtime_config_safe", lambda: None)
    monkeypatch.setattr(exit_coord, "append_closed_trade", lambda row: closed_rows.append(row))
    monkeypatch.setattr(exit_coord, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(exit_coord, "live_fee_bps_for_symbol", lambda _s: 0.0)
    monkeypatch.setattr(exit_coord.config, "PAPER_TRADING", True)
    monkeypatch.setattr(exit_coord.config, "LIVE_SLIPPAGE_BPS", 0.0)
    monkeypatch.setattr(exit_coord.config, "LOSS_COOLDOWN_BARS", 4)
    monkeypatch.setattr(
        engine,
        "write_trading_snapshot",
        lambda payload: snapshots.append(payload),
    )
    monkeypatch.setattr(
        exit_coord,
        "execute_trade",
        lambda sym, side, size, *, client_order_id=None: trades.append((sym, side, size))
        or {
            "status": "simulated",
            "filled": size,
            "amount": size,
            "average": 95.0,
            "id": client_order_id or "paper",
            "clientOrderId": client_order_id,
        },
    )
    monkeypatch.setattr(
        engine,
        "execute_trade",
        lambda sym, side, size, *, client_order_id=None: trades.append((sym, side, size))
        or {
            "status": "simulated",
            "filled": size,
            "amount": size,
            "average": 95.0,
            "id": client_order_id or "paper",
            "clientOrderId": client_order_id,
        },
    )
    monkeypatch.setattr(engine, "RegimeModel", _DummyRegimeModel)
    monkeypatch.setattr(
        engine,
        "CircuitBreaker",
        lambda: circuit_holder.setdefault("circuit", _DummyCircuit()),
    )
    monkeypatch.setattr(
        engine,
        "_evaluate_symbol_legacy",
        lambda *_a, **_k: pytest.fail("new-entry evaluation should not run while manually paused"),
    )

    def stop_after_one_loop(_seconds: float) -> None:
        raise _EndLoop()

    monkeypatch.setattr(engine.time, "sleep", stop_after_one_loop)

    with pytest.raises(_EndLoop):
        engine.run_forever()

    assert trades == [(symbol, "sell", 1.0)]
    assert len(closed_rows) == 1
    assert closed_rows[0]["outcome"] == "loss"
    assert circuit_holder["circuit"].recorded == [False]
    assert snapshots, "engine should still publish a trading snapshot while paused"
    assert snapshots[-1]["note"] == "new_entries_paused_manual_halt"
    assert snapshots[-1]["by_symbol"][symbol]["reason"] == "manual_halt_no_new_entries"

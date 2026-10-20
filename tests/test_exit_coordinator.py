"""ExitCoordinator idempotency and partial-fill behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from tr_bot.exit_coordinator import ExitCoordinator
from tr_bot.position import OpenPosition


class _Circuit:
    def __init__(self) -> None:
        self.recorded: list[bool] = []

    def record_outcome(self, won: bool) -> None:
        self.recorded.append(bool(won))


def test_request_exit_closes_and_records(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tr_bot import exit_coordinator as ec

    monkeypatch.setattr(ec.config, "PAPER_TRADING", True)
    monkeypatch.setattr(ec.config, "LIVE_SLIPPAGE_BPS", 0.0)
    monkeypatch.setattr(ec.config, "LOSS_COOLDOWN_BARS", 2)
    monkeypatch.setattr(ec, "live_fee_bps_for_symbol", lambda _s: 0.0)
    monkeypatch.setattr(ec, "notify", lambda *_a, **_k: None)

    closed: list[dict] = []
    monkeypatch.setattr(ec, "append_closed_trade", lambda row: closed.append(row))

    saved: list[dict] = []

    def _save(positions: dict) -> None:
        saved.append(dict(positions))

    symbol = "BTC/USDT"
    positions: dict = {
        symbol: OpenPosition(symbol=symbol, side="long", entry_price=100.0, atr=5.0, size=1.0)
    }
    circuit = _Circuit()
    cooldown = {symbol: 0}
    coord = ExitCoordinator(positions, circuit=circuit, cooldown_bars_remaining=cooldown, save_fn=_save)

    res = coord.request_exit(symbol, outcome="loss", reason="test", fallback_price=95.0)
    assert res["status"] == "closed"
    assert positions[symbol] is None
    assert len(closed) == 1
    assert closed[0]["outcome"] == "loss"
    assert circuit.recorded == [False]
    assert cooldown[symbol] == 2


def test_duplicate_pending_does_not_double_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot import exit_coordinator as ec

    monkeypatch.setattr(ec.config, "PAPER_TRADING", True)
    monkeypatch.setattr(ec.config, "LIVE_SLIPPAGE_BPS", 0.0)
    monkeypatch.setattr(ec, "live_fee_bps_for_symbol", lambda _s: 0.0)
    monkeypatch.setattr(ec, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(ec, "append_closed_trade", lambda _row: None)

    calls: list[tuple] = []

    def _exec(symbol, side, size, *, client_order_id=None, fill_price=None):
        calls.append((symbol, side, size, client_order_id))
        order = {
            "id": client_order_id or "x",
            "status": "simulated",
            "filled": size,
            "amount": size,
            "average": 95.0 if fill_price is None else float(fill_price),
            "clientOrderId": client_order_id,
        }
        return order

    monkeypatch.setattr(ec, "execute_trade", _exec)

    symbol = "BTC/USDT"
    pos = OpenPosition(
        symbol=symbol,
        side="long",
        entry_price=100.0,
        atr=5.0,
        size=1.0,
        exit_state="pending",
        exit_client_order_id="txabc",
        exit_requested_size=1.0,
        exit_outcome="loss",
        exit_reason="bar",
    )
    positions = {symbol: pos}
    coord = ExitCoordinator(
        positions,
        circuit=_Circuit(),
        cooldown_bars_remaining={symbol: 0},
        save_fn=lambda _p: None,
    )
    # Second request while pending should reconcile (one execute) not create a new client id path blindly.
    res = coord.request_exit(symbol, outcome="loss", reason="dup", fallback_price=95.0)
    assert res["status"] == "closed"
    assert len(calls) == 1
    assert calls[0][3] == "txabc"


def test_watchdog_stop_breach(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot import exit_watchdog as ew
    from tr_bot.exit_plan import build_exit_plan

    monkeypatch.setattr(ew.config, "PAPER_TRADING", True)
    plan = build_exit_plan(side="long", entry_price=100.0, atr=5.0)
    symbol = "BTC/USDT"
    positions = {
        symbol: OpenPosition(
            symbol=symbol,
            side="long",
            entry_price=100.0,
            atr=5.0,
            size=1.0,
            exit_plan_dict=plan.to_dict(),
            exit_mode="atr",
            exit_policy="software_polled",
        )
    }

    requested: list[str] = []

    class _Coord:
        def request_exit(self, sym, *, outcome, reason, fallback_price):
            requested.append(sym)
            return {"status": "closed"}

        def reconcile_pending(self):
            return {}

    out = ew.run_exit_watchdog_once(
        _Coord(),  # type: ignore[arg-type]
        positions,
        price_by_symbol={symbol: 90.0},  # below stop 95
    )
    assert symbol in out
    assert requested == [symbol]

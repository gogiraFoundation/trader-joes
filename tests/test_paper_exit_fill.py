"""Paper exit fills at simulated stop/take, not bar close."""

from __future__ import annotations

import pandas as pd
import pytest

from tr_bot.exit_coordinator import ExitCoordinator
from tr_bot.exit_plan import build_exit_plan, simulate_software_polled_exit_price
from tr_bot.position import OpenPosition


class _Circuit:
    def __init__(self) -> None:
        self.recorded: list[bool] = []

    def record_outcome(self, won: bool) -> None:
        self.recorded.append(bool(won))


def test_simulate_long_loss_fill_is_stop_not_close() -> None:
    plan = build_exit_plan(side="long", entry_price=100.0, atr=2.0)
    bar = pd.Series({"open": 99.5, "high": 100.0, "low": 97.5, "close": 99.5})
    fill = simulate_software_polled_exit_price(
        side="long",
        stop_price=plan.stop_price,
        take_price=plan.take_price,
        bar=bar,
        outcome="loss",
    )
    assert fill == pytest.approx(plan.stop_price)
    assert fill != pytest.approx(float(bar["close"]))


def test_paper_exit_uses_simulated_stop_via_fill_price(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot import exit_coordinator as ec

    monkeypatch.setattr(ec.config, "PAPER_TRADING", True)
    monkeypatch.setattr(ec.config, "LIVE_SLIPPAGE_BPS", 0.0)
    monkeypatch.setattr(ec.config, "LOSS_COOLDOWN_BARS", 0)
    monkeypatch.setattr(ec, "live_fee_bps_for_symbol", lambda _s: 0.0)
    monkeypatch.setattr(ec, "notify", lambda *_a, **_k: None)

    closed: list[dict] = []
    monkeypatch.setattr(ec, "append_closed_trade", lambda row: closed.append(row))

    plan = build_exit_plan(side="long", entry_price=100.0, atr=2.0)
    bar = pd.Series({"open": 99.5, "high": 100.0, "low": 97.5, "close": 99.5})
    fill_px = simulate_software_polled_exit_price(
        side="long",
        stop_price=plan.stop_price,
        take_price=plan.take_price,
        bar=bar,
        outcome="loss",
    )

    symbol = "SOL/USDT"
    positions: dict = {
        symbol: OpenPosition(
            symbol=symbol,
            side="long",
            entry_price=100.0,
            atr=2.0,
            size=10.0,
            exit_plan_dict=plan.to_dict(),
        )
    }
    coord = ExitCoordinator(positions, circuit=_Circuit(), cooldown_bars_remaining={}, save_fn=lambda _p: None)
    res = coord.request_exit(
        symbol,
        outcome="loss",
        reason="bar_stop_or_take",
        fallback_price=float(fill_px),
    )
    assert res["status"] == "closed"
    row = closed[0]
    assert row["exit_price"] == pytest.approx(98.0)
    assert row["exit_price_assumption"] == "order_fill"
    assert row["trigger_level"] == pytest.approx(plan.stop_price)


def test_paper_execute_trade_sets_average(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot import execution as ex

    monkeypatch.setattr(ex.config, "PAPER_TRADING", True)
    monkeypatch.setattr(ex.config, "EXECUTION_STYLE", "market")
    order = ex.execute_trade("SOL/USDT", "sell", 1.0, fill_price=77.43)
    assert order["average"] == pytest.approx(77.43)
    px, src = ex.resolve_fill_price(order, fallback=99.0)
    assert px == pytest.approx(77.43)
    assert src == "order_fill"

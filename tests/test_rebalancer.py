"""Portfolio rebalancer: drift, lag, plan, halt, circuit isolation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tr_bot.exit_coordinator import ExitCoordinator
from tr_bot.managers.fund import FundManager
from tr_bot.managers import rebalancer as rb
from tr_bot.position import OpenPosition


def _price_history(symbols: list[str], days: int = 20) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=days, freq="D", tz="UTC")
    data = {}
    for i, s in enumerate(symbols):
        # Distinct paths: first asset flat, others drift up.
        base = 100.0 + i * 10
        if i == 0:
            closes = base * np.linspace(1.0, 0.85, days)  # lagging
        else:
            closes = base * np.linspace(1.0, 1.10, days)
        data[s] = closes
    return pd.DataFrame(data, index=idx)


def test_drift_triggers_band(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rb.config, "REBALANCE_BAND_PCT", 0.05)
    snapshot = {
        "legs": {
            "ETH/USDT": {"weight_drift": 0.06},
            "SOL/USDT": {"weight_drift": 0.01},
        }
    }
    trig = rb.drift_triggers(snapshot)
    assert trig["ETH/USDT"] is True
    assert trig["SOL/USDT"] is False


def test_generate_plan_lag_is_trim_not_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rb.config, "REBALANCER_ENABLED", True)
    monkeypatch.setattr(rb.config, "REBALANCE_BAND_PCT", 0.05)
    snapshot = {
        "total_equity_usdt": 10_000.0,
        "legs": {
            "ETH/USDT": {
                "actual_weight": 0.20,
                "target_weight": 0.25,
                "weight_drift": -0.05,
                "base_total": 1.0,
                "base_free": 1.0,
                "last_price": 2000.0,
                "value_usdt": 2000.0,
            },
            "SOL/USDT": {
                "actual_weight": 0.40,
                "target_weight": 0.25,
                "weight_drift": 0.15,
                "base_total": 10.0,
                "base_free": 10.0,
                "last_price": 100.0,
                "value_usdt": 4000.0,
            },
        },
    }
    lag = {
        "ETH/USDT": {"lag_trigger": True},
        "SOL/USDT": {"lag_trigger": False},
    }
    action, plan = rb.generate_plan(snapshot, lag)
    assert action is True
    actions = {p["asset"]: p["action"] for p in plan}
    assert actions["ETH/USDT"] == "TRIM"
    assert actions["SOL/USDT"] == "TRIM"
    assert all(p["action"] != "BUY_UNDERWEIGHT" for p in plan)


def test_generate_plan_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rb.config, "REBALANCER_ENABLED", False)
    action, plan = rb.generate_plan(
        {
            "total_equity_usdt": 1000.0,
            "legs": {
                "ETH/USDT": {
                    "actual_weight": 0.9,
                    "target_weight": 0.25,
                    "weight_drift": 0.65,
                    "base_total": 1,
                    "base_free": 1,
                    "last_price": 1,
                    "value_usdt": 900,
                }
            },
        },
        {"ETH/USDT": {"lag_trigger": True}},
    )
    assert action is False
    assert plan == []


def test_structural_lag_excludes_self_from_basket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rb.config, "LAG_LOOKBACK_DAYS", 10)
    monkeypatch.setattr(rb.config, "LAG_RELATIVE_RETURN_THRESHOLD", -0.05)
    monkeypatch.setattr(rb.config, "LAG_REQUIRE_MDD_DIVERGENCE", True)
    symbols = ["ETH/USDT", "SOL/USDT", "XRP/USDT"]
    hist = _price_history(symbols, days=20)
    lag = rb.structural_lag(symbols, price_history=hist, require_mdd_divergence=True)
    assert "ETH/USDT" in lag
    assert "ETH/USDT" not in lag["ETH/USDT"]["peers"]
    assert set(lag["ETH/USDT"]["peers"]) == {"SOL/USDT", "XRP/USDT"}
    assert lag["ETH/USDT"]["relative_vs_basket"] < -0.05
    assert lag["ETH/USDT"]["mdd_divergent"] is True
    assert lag["ETH/USDT"]["lag_trigger"] is True
    # Peers that rallied should not be flagged as dragging.
    assert lag["SOL/USDT"]["lag_trigger"] is False


def test_clear_manual_halt_if_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.managers.fund.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.managers.fund.config.TRADING_HALT_FILENAME", "TRADING_HALTED")
    fm = FundManager()
    fm.set_manual_halt("operator_pause")
    assert fm.clear_manual_halt_if_reason("rebalancer_active") is False
    assert fm.manual_halt_file_active() is True
    fm.clear_manual_halt()
    fm.set_manual_halt("rebalancer_active")
    assert fm.clear_manual_halt_if_reason("rebalancer_active") is True
    assert fm.manual_halt_file_active() is False


def test_cooldown_blocks_symbol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rb.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(rb.config, "REBALANCE_COOLDOWN_HOURS", 24.0)
    state = {
        "per_symbol": {
            "ETH/USDT": {
                "last_action_utc": datetime.now(timezone.utc).isoformat(),
            }
        }
    }
    assert rb._in_cooldown("ETH/USDT", state) is True
    state["per_symbol"]["ETH/USDT"]["last_action_utc"] = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat()
    assert rb._in_cooldown("ETH/USDT", state) is False


def test_build_snapshot_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    holdings = {
        "total_equity_usdt": 1000.0,
        "quote_asset": "USDT",
        "quote_free": 250.0,
        "quote_total": 250.0,
        "paper": True,
        "legs": {
            "ETH/USDT": {
                "base_total": 0.1,
                "base_free": 0.1,
                "last_price": 2500.0,
                "value_usdt": 250.0,
                "actual_weight": 0.25,
            },
            "SOL/USDT": {
                "base_total": 5.0,
                "base_free": 5.0,
                "last_price": 100.0,
                "value_usdt": 500.0,
                "actual_weight": 0.5,
            },
        },
    }
    tw = {"ETH/USDT": 0.25, "SOL/USDT": 0.25}
    snap = rb.build_snapshot(tw, holdings=holdings)
    assert snap["legs"]["ETH/USDT"]["weight_drift"] == pytest.approx(0.0)
    assert snap["legs"]["SOL/USDT"]["weight_drift"] == pytest.approx(0.25)


def test_rebalance_exit_skips_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot import exit_coordinator as ec

    monkeypatch.setattr(ec.config, "PAPER_TRADING", True)
    monkeypatch.setattr(ec.config, "LIVE_SLIPPAGE_BPS", 0.0)
    monkeypatch.setattr(ec.config, "LOSS_COOLDOWN_BARS", 2)
    monkeypatch.setattr(ec, "live_fee_bps_for_symbol", lambda _s: 0.0)
    monkeypatch.setattr(ec, "notify", lambda *_a, **_k: None)

    closed: list[dict] = []
    monkeypatch.setattr(ec, "append_closed_trade", lambda row: closed.append(row))

    class _Circuit:
        def __init__(self) -> None:
            self.recorded: list[bool] = []

        def record_outcome(self, won: bool) -> None:
            self.recorded.append(bool(won))

    symbol = "BTC/USDT"
    positions: dict = {
        symbol: OpenPosition(symbol=symbol, side="long", entry_price=100.0, atr=5.0, size=1.0)
    }
    circuit = _Circuit()
    cooldown = {symbol: 0}
    coord = ExitCoordinator(
        positions, circuit=circuit, cooldown_bars_remaining=cooldown, save_fn=lambda _p: None
    )
    res = coord.request_exit(
        symbol,
        outcome="loss",
        reason="rebalance_trim",
        fallback_price=90.0,
        execution_context="REBALANCE_TRIM",
    )
    assert res["status"] == "closed"
    assert circuit.recorded == []
    assert cooldown[symbol] == 0
    assert closed[0]["execution_context"] == "REBALANCE_TRIM"
    assert closed[0]["affects_circuit"] is False


def test_holdings_snapshot_paper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tr_bot import config
    from tr_bot.open_positions_state import save_open_positions

    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(config, "PAPER_EQUITY_USDT", 10_000.0)
    monkeypatch.setattr(config, "PORTFOLIO_MODE", True)
    monkeypatch.setattr(config, "PORTFOLIO_WEIGHTS", "ETH/USDT=0.5,SOL/USDT=0.5")
    monkeypatch.setattr("tr_bot.managers.fund.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.managers.fund.config.PAPER_TRADING", True)
    monkeypatch.setattr("tr_bot.managers.fund.config.PAPER_EQUITY_USDT", 10_000.0)
    monkeypatch.setattr("tr_bot.trade_ledger.sum_closed_net_pnl_usdt", lambda paper=True: 0.0)
    monkeypatch.setattr(
        "tr_bot.managers.fund.sum_closed_net_pnl_usdt", lambda paper=True: 0.0
    )

    pos = {
        "ETH/USDT": OpenPosition(
            symbol="ETH/USDT", side="long", entry_price=2000.0, atr=10.0, size=1.0
        )
    }
    save_open_positions(pos)
    fm = FundManager()
    snap = fm.holdings_snapshot()
    assert snap["paper"] is True
    assert snap["legs"]["ETH/USDT"]["value_usdt"] == pytest.approx(2000.0)
    assert snap["total_equity_usdt"] == pytest.approx(10_000.0)

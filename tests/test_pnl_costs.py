"""Realized P&L breakdown."""

from __future__ import annotations

from tr_bot.pnl_costs import fee_usdt_round_trip, realized_pnl_breakdown


def test_realized_pnl_long_no_costs() -> None:
    r = realized_pnl_breakdown("long", 100.0, 102.0, 1.0, 0.0, 0.0)
    assert r["net_pnl_usdt"] == 2.0
    assert r["gross_pnl_usdt"] == 2.0
    assert r["fee_usdt"] == 0.0


def test_realized_pnl_short_no_costs() -> None:
    r = realized_pnl_breakdown("short", 100.0, 98.0, 1.0, 0.0, 0.0)
    assert abs(r["net_pnl_usdt"] - 2.0) < 1e-9
    assert r["gross_pnl_usdt"] == 2.0


def test_fee_round_trip() -> None:
    f = fee_usdt_round_trip(100.0, 102.0, 1.0, 10.0)
    assert abs(f - 0.202) < 1e-6


def test_slippage_reduces_long_profit() -> None:
    r0 = realized_pnl_breakdown("long", 100.0, 102.0, 1.0, 0.0, 0.0)
    r1 = realized_pnl_breakdown("long", 100.0, 102.0, 1.0, 0.0, 10.0)
    assert r1["net_pnl_usdt"] < r0["net_pnl_usdt"]

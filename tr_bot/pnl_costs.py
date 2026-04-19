"""Realized P&L with fees and slippage (aligned with backtest runner formulas)."""

from __future__ import annotations

from typing import Any


def _long_pnl_with_costs(
    entry_px: float,
    exit_px: float,
    size: float,
    fee_bps: float,
    slip_bps: float,
) -> float:
    e = entry_px * (1.0 + slip_bps / 10_000.0)
    x = exit_px * (1.0 - slip_bps / 10_000.0)
    gross = (x - e) * size
    fees = (fee_bps / 10_000.0) * (entry_px * size + exit_px * size)
    return gross - fees


def _short_pnl_with_costs(
    entry_px: float,
    exit_px: float,
    size: float,
    fee_bps: float,
    slip_bps: float,
) -> float:
    e = entry_px * (1.0 - slip_bps / 10_000.0)
    x = exit_px * (1.0 + slip_bps / 10_000.0)
    gross = (e - x) * size
    fees = (fee_bps / 10_000.0) * (entry_px * size + exit_px * size)
    return gross - fees


def fee_usdt_round_trip(
    entry_px: float,
    exit_px: float,
    size: float,
    fee_bps: float,
) -> float:
    return (fee_bps / 10_000.0) * (entry_px * size + exit_px * size)


def gross_pnl_mid_prices(side: str, entry_px: float, exit_px: float, size: float) -> float:
    """Price move only (no slippage/fees), for ledger display."""
    if side == "long":
        return (exit_px - entry_px) * size
    return (entry_px - exit_px) * size


def realized_pnl_breakdown(
    side: str,
    entry_px: float,
    exit_px: float,
    size: float,
    fee_bps: float,
    slip_bps: float,
) -> dict[str, Any]:
    """
    Net P&L in quote units (USDT for USDT pairs) using same cost model as backtest.
    """
    side_l = side.lower()
    if side_l == "long":
        net = _long_pnl_with_costs(entry_px, exit_px, size, fee_bps, slip_bps)
    elif side_l == "short":
        net = _short_pnl_with_costs(entry_px, exit_px, size, fee_bps, slip_bps)
    else:
        raise ValueError(f"side must be long or short, got {side!r}")
    fee = fee_usdt_round_trip(entry_px, exit_px, size, fee_bps)
    gross = gross_pnl_mid_prices(side_l, entry_px, exit_px, size)
    return {
        "net_pnl_usdt": float(net),
        "gross_pnl_usdt": float(gross),
        "fee_usdt": float(fee),
        "exit_price_assumption": "bar_close",
    }

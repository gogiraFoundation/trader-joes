"""Reconcile exchange-native protective stop orders with open positions."""

from __future__ import annotations

import logging
from typing import Any

from tr_bot import config
from tr_bot.position import OpenPosition

logger = logging.getLogger("tr_bot.protective_reconcile")

_TERMINAL = frozenset({"closed", "filled", "canceled", "cancelled", "expired", "rejected"})


def _paper_simulate_filled(pos: OpenPosition, *, outcome: str) -> dict[str, Any]:
    plan = pos.exit_plan()
    fill = float(plan.stop_price) if outcome == "loss" else float(plan.take_price)
    return {
        "status": "closed",
        "filled": float(pos.size),
        "average": fill,
        "outcome": outcome,
        "simulated": True,
    }


def reconcile_protective_orders(
    positions: dict[str, OpenPosition | None],
) -> dict[str, dict[str, Any]]:
    """
    Poll protective orders for exchange_native positions.
    Returns symbol -> {filled, average, status, outcome?} when terminal.
    """
    results: dict[str, dict[str, Any]] = {}
    for symbol, pos in positions.items():
        if pos is None:
            continue
        if str(getattr(pos, "exit_policy", "software_polled")) != "exchange_native":
            continue
        if not pos.protective_order_id and not pos.protective_client_order_id:
            continue

        if config.PAPER_TRADING:
            # Paper: treat as still open; engine software path handles bar exits for TP.
            continue

        try:
            from tr_bot.binance_rest import call_ccxt_with_retries
            from tr_bot.exchange import get_ccxt_exchange

            exchange = get_ccxt_exchange()
            oid = pos.protective_order_id or "0"
            params = {}
            if pos.protective_client_order_id:
                params["origClientOrderId"] = pos.protective_client_order_id
            order = call_ccxt_with_retries(
                exchange,
                lambda: exchange.fetch_order(oid, symbol, params),
                context=f"fetch_protective {symbol}",
            )
            status = str(order.get("status") or "").lower()
            if status in _TERMINAL:
                filled = float(order.get("filled") or order.get("amount") or 0)
                avg = order.get("average") or order.get("price")
                results[symbol] = {
                    "status": status,
                    "filled": filled,
                    "average": float(avg) if avg else None,
                    "order": order,
                }
        except Exception as e:
            logger.warning("Protective reconcile failed %s: %s", symbol, e)
    return results

"""
Poll Binance SAPI for spot TWAP algo lifecycle (open + recent historical).

On terminal failure/cancel we set `twap_algo_state` only; the trading loop blocks the
symbol until an operator adjusts state — we do not auto-flatten (see plan).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import ccxt

from tr_bot import config
from tr_bot.binance_rest import call_ccxt_with_retries
from tr_bot.position import OpenPosition

logger = logging.getLogger("tr_bot.twap_reconcile")

_last_reconcile_mono: float = 0.0

_TERMINAL_OK = frozenset({"FINISHED", "COMPLETED", "FILLED", "DONE", "SUCCESS"})
_TERMINAL_FAIL = frozenset({"FAILED", "REJECTED", "FAILURE"})
_TERMINAL_CANCEL = frozenset({"CANCELLED", "CANCELED", "EXPIRED"})


def _norm(raw: Any) -> str:
    return str(raw or "").strip().upper()


def _map_algo_status(remote: str) -> str:
    r = _norm(remote)
    if r in _TERMINAL_OK:
        return "completed"
    if r in _TERMINAL_FAIL:
        return "failed"
    if r in _TERMINAL_CANCEL:
        return "canceled"
    if r in ("WORKING", "PENDING", "NEW", "RUNNING", "PARTIALLY_FILLED"):
        return "working"
    if r:
        return "working"
    return "unknown"


def _maybe_update_filled_size(pos: OpenPosition, executed_qty: Any) -> None:
    try:
        if executed_qty is None:
            return
        q = float(executed_qty)
        if q > 0:
            pos.size = q
    except (TypeError, ValueError):
        pass


def reconcile_open_twap_positions(exchange: ccxt.Exchange, positions: dict[str, OpenPosition | None]) -> None:
    """
    Mutate positions in place. Throttled by TWAP_RECONCILE_MIN_INTERVAL_S when > 0.
    No-op in paper (caller should skip) or when no pending TWAP rows.
    """
    global _last_reconcile_mono
    now = time.monotonic()
    min_iv = float(config.TWAP_RECONCILE_MIN_INTERVAL_S)
    if min_iv > 0 and (now - _last_reconcile_mono) < min_iv:
        return

    pending: list[tuple[str, OpenPosition]] = []
    for sym, pos in positions.items():
        if pos is None:
            continue
        if getattr(pos, "entry_execution", "market") != "twap":
            continue
        cid = (getattr(pos, "twap_client_algo_id", "") or "").strip()
        if not cid:
            continue
        st = (getattr(pos, "twap_algo_state", "") or "").strip().lower()
        if st in ("completed", "failed", "canceled"):
            continue
        pending.append((sym, pos))
    if not pending:
        return

    _last_reconcile_mono = now
    exchange.load_markets()

    for symbol, pos in pending:
        try:
            _reconcile_one(exchange, symbol, pos)
        except Exception as e:  # noqa: BLE001
            logger.warning("TWAP reconcile failed for %s: %s", symbol, e)
            if (pos.twap_algo_state or "").lower() not in ("completed", "failed", "canceled"):
                pos.twap_algo_state = "unknown"


def _reconcile_one(exchange: ccxt.Exchange, symbol: str, pos: OpenPosition) -> None:
    cid = pos.twap_client_algo_id.strip()
    market_id = exchange.market_id(symbol)

    def _open_orders() -> Any:
        return exchange.request("algo/spot/openOrders", "sapi", "GET", {})

    raw_open = call_ccxt_with_retries(exchange, _open_orders, context=f"twap_openOrders {symbol}")
    orders_open: list[Any] = []
    if isinstance(raw_open, dict):
        orders_open = list(raw_open.get("orders") or [])

    matched: dict[str, Any] | None = None
    for o in orders_open:
        if isinstance(o, dict) and str(o.get("clientAlgoId") or "") == cid:
            matched = o
            break

    if matched is not None:
        pos.twap_algo_state = _map_algo_status(matched.get("algoStatus"))
        _maybe_update_filled_size(pos, matched.get("executedQty"))
        return

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - 7 * 86400 * 1000
    hparams: dict[str, Any] = {
        "symbol": market_id,
        "startTime": start_ms,
        "endTime": end_ms,
        "page": 1,
        "pageSize": 50,
    }

    def _historical() -> Any:
        return exchange.request("algo/spot/historicalOrders", "sapi", "GET", hparams)

    raw_h = call_ccxt_with_retries(exchange, _historical, context=f"twap_historical {symbol}")
    orders_h: list[Any] = []
    if isinstance(raw_h, dict):
        orders_h = list(raw_h.get("orders") or [])

    for o in orders_h:
        if isinstance(o, dict) and str(o.get("clientAlgoId") or "") == cid:
            pos.twap_algo_state = _map_algo_status(o.get("algoStatus"))
            _maybe_update_filled_size(pos, o.get("executedQty"))
            return

    prev = (pos.twap_algo_state or "").strip().lower()
    if prev == "submitted":
        return
    pos.twap_algo_state = "unknown"

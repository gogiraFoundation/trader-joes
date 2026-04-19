"""Order placement: paper or live spot via ccxt (Section 4.8)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import ccxt

from tr_bot import config
from tr_bot.binance_rest import call_ccxt_with_retries
from tr_bot.exchange import get_ccxt_exchange

logger = logging.getLogger("tr_bot")

# TWAP submits slices over time; bar-close entry remains provisional until twap_reconcile updates size/state.
_ALGO_ERR_HINTS: dict[int, str] = {
    -20194: "duration_too_short_for_quantity",
    -20195: "total_size_too_small",
    -20196: "total_size_too_large",
    -20198: "max_open_algo_orders",
    -20204: "notional_usd_out_of_bounds",
}


def _twap_client_algo_id() -> str:
    return uuid.uuid4().hex


def _spot_new_client_order_id() -> str:
    """Binance spot clientOrderId: max 36 chars; [A-Za-z0-9_-]."""
    return "tb" + uuid.uuid4().hex[:32]


def _unknown_execution_result(message: str) -> dict[str, Any]:
    return {"status": "unknown_execution", "success": False, "error": message}


def _normalize_twap_response(raw: dict[str, Any]) -> dict[str, Any]:
    cid = str(raw.get("clientAlgoId") or "")
    out: dict[str, Any] = {
        "id": cid or None,
        "status": "submitted",
        "success": bool(raw.get("success")),
        "clientAlgoId": cid,
        "info": raw,
        "entry_execution": "twap",
        "twap_client_algo_id": cid,
        "twap_algo_state": "submitted",
    }
    return out


def _twap_error_result(exc: Exception) -> dict[str, Any]:
    code: int | None = None
    if isinstance(exc, ccxt.ExchangeError):
        body = getattr(exc, "body", None) or str(exc)
        if isinstance(body, str) and '"code":' in body:
            try:
                parsed = json.loads(body) if body.strip().startswith("{") else None
                if isinstance(parsed, dict):
                    code = int(parsed["code"]) if parsed.get("code") is not None else None
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    hint = _ALGO_ERR_HINTS.get(code, "") if code is not None else ""
    if hint:
        logger.error("Spot TWAP rejected (%s): %s", hint, exc)
    else:
        logger.error("Spot TWAP rejected: %s", exc)
    return {"status": "rejected", "success": False, "error": str(exc), "code": code}


def _twap_transient_unknown(exc: BaseException) -> dict[str, Any] | None:
    if isinstance(exc, (ccxt.InvalidNonce, ccxt.ChecksumError)):
        return None
    if isinstance(
        exc,
        (
            ccxt.DDoSProtection,
            ccxt.RateLimitExceeded,
            ccxt.ExchangeNotAvailable,
            ccxt.OnMaintenance,
            ccxt.RequestTimeout,
        ),
    ):
        return _unknown_execution_result(str(exc))
    if isinstance(exc, ccxt.NetworkError):
        return _unknown_execution_result(str(exc))
    return None


def execute_trade(symbol: str, side: str, size: float) -> dict[str, Any]:
    """
    Spot market order, or Binance Spot TWAP when EXECUTION_STYLE=twap.
    side: 'buy' (acquire base with quote) or 'sell' (sell base for quote).
    For paper trading, logs and returns a mock order without calling the API.
    """
    if size <= 0:
        logger.warning("execute_trade skipped: size <= 0")
        return {"status": "skipped", "reason": "zero_size"}

    if config.PAPER_TRADING:
        if config.EXECUTION_STYLE == "twap":
            cid = _twap_client_algo_id()
            order = {
                "id": cid,
                "symbol": symbol,
                "side": side,
                "amount": size,
                "status": "submitted",
                "success": True,
                "clientAlgoId": cid,
                "entry_execution": "twap",
                "twap_client_algo_id": cid,
                "twap_algo_state": "submitted",
            }
            logger.info("Paper spot TWAP (simulated submit): %s", order)
            return order
        order = {
            "id": "paper",
            "symbol": symbol,
            "side": side,
            "amount": size,
            "status": "simulated",
        }
        logger.info("Paper spot trade: %s", order)
        return order

    exchange = get_ccxt_exchange()
    exchange.load_markets()
    amount = float(exchange.amount_to_precision(symbol, size))
    if amount <= 0:
        logger.warning("execute_trade skipped: amount rounds to zero")
        return {"status": "skipped", "reason": "precision_zero"}

    market_side = "buy" if side == "buy" else "sell"
    use_twap = config.EXECUTION_STYLE == "twap"
    if use_twap and config.TWAP_MIN_NOTIONAL_USDT is not None and config.TWAP_MIN_NOTIONAL_USDT > 0:
        try:
            ticker = call_ccxt_with_retries(
                exchange,
                lambda: exchange.fetch_ticker(symbol),
                context=f"fetch_ticker {symbol}",
            )
            last = float(ticker.get("last") or ticker.get("close") or 0)
            if last > 0 and amount * last < float(config.TWAP_MIN_NOTIONAL_USDT):
                use_twap = False
                logger.info(
                    "TWAP skipped (notional below TWAP_MIN_NOTIONAL_USDT); using market order for %s",
                    symbol,
                )
        except Exception as e:
            logger.warning("Could not fetch ticker for TWAP notional check; using market: %s", e)
            use_twap = False

    if not use_twap:
        client_oid = _spot_new_client_order_id()
        params: dict[str, Any] = {"newClientOrderId": client_oid}
        try:
            order = call_ccxt_with_retries(
                exchange,
                lambda: exchange.create_order(symbol, "market", market_side, amount, None, params),
                context=f"create_order {symbol}",
            )
        except ccxt.DuplicateOrderId:
            try:
                order = exchange.fetch_order("0", symbol, {"origClientOrderId": client_oid})
            except ccxt.BaseError as e2:
                logger.error("Market order duplicate recover failed %s: %s", symbol, e2)
                return _unknown_execution_result(str(e2))
        except ccxt.BaseError as e:
            unk = _twap_transient_unknown(e)
            if unk is not None:
                return unk
            logger.error("Spot market order failed %s: %s", symbol, e)
            return {"status": "rejected", "success": False, "error": str(e)}
        logger.info("Spot trade executed: %s", order)
        return order

    market_id = exchange.market_id(symbol)
    qty_str = exchange.amount_to_precision(symbol, size)
    client_algo_id = _twap_client_algo_id()
    params: dict[str, Any] = {
        "symbol": market_id,
        "side": market_side.upper(),
        "quantity": qty_str,
        "duration": int(config.TWAP_DURATION_SECONDS),
        "clientAlgoId": client_algo_id,
    }
    lp = config.TWAP_LIMIT_PRICE
    if lp is not None and lp > 0:
        params["limitPrice"] = exchange.price_to_precision(symbol, float(lp))

    try:
        raw = call_ccxt_with_retries(
            exchange,
            lambda: exchange.request("algo/spot/newOrderTwap", "sapi", "POST", params),
            context=f"newOrderTwap {symbol}",
        )
    except ccxt.BaseError as e:
        unk = _twap_transient_unknown(e)
        if unk is not None:
            return unk
        return _twap_error_result(e)
    except Exception as e:
        return _twap_error_result(e)

    if not isinstance(raw, dict):
        logger.error("Unexpected TWAP response type: %s", type(raw))
        return {"status": "rejected", "success": False, "error": "unexpected_response"}

    if not raw.get("success"):
        c = raw.get("code")
        logger.error("TWAP API failure: code=%s msg=%s", c, raw.get("msg"))
        return {"status": "rejected", "success": False, "info": raw}

    normalized = _normalize_twap_response(raw)
    logger.info("Spot TWAP submitted: %s", normalized)
    return normalized

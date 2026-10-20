"""Order placement: paper or live spot via ccxt (Section 4.8)."""

from __future__ import annotations

import json
import logging
import math
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


def _unknown_execution_result(message: str, *, client_order_id: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "unknown_execution", "success": False, "error": message}
    if client_order_id:
        out["clientOrderId"] = client_order_id
    return out


def _rejected(reason: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "rejected", "success": False, "reason": reason, "error": reason}
    out.update(extra)
    return out


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


def _twap_transient_unknown(exc: BaseException, *, client_order_id: str | None = None) -> dict[str, Any] | None:
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
        return _unknown_execution_result(str(exc), client_order_id=client_order_id)
    if isinstance(exc, ccxt.NetworkError):
        return _unknown_execution_result(str(exc), client_order_id=client_order_id)
    return None


def resolve_fill_price(order: dict[str, Any], *, fallback: float | None = None) -> tuple[float | None, str]:
    """
    Extract average fill price from ccxt order dict.
    Returns (price, source) where source is order_fill | fallback | unavailable.
    """
    if not isinstance(order, dict):
        return fallback, "fallback" if fallback is not None else "unavailable"
    for key in ("average", "price"):
        try:
            v = order.get(key)
            if v is not None and float(v) > 0:
                return float(v), "order_fill"
        except (TypeError, ValueError):
            continue
    try:
        cost = float(order.get("cost") or 0)
        filled = float(order.get("filled") or order.get("amount") or 0)
        if filled > 0 and cost > 0:
            return cost / filled, "order_fill"
    except (TypeError, ValueError):
        pass
    if fallback is not None and fallback > 0:
        return float(fallback), "fallback"
    return None, "unavailable"


def resolve_fill_qty(order: dict[str, Any], *, requested: float) -> tuple[float | None, str]:
    """
    Resolve filled base quantity from an order response.
    Returns (qty, source) where source is order_fill | requested | unavailable.
    """
    if not isinstance(order, dict):
        return None, "unavailable"
    for key in ("filled", "amount"):
        try:
            v = order.get(key)
            if v is not None and float(v) > 0:
                return float(v), "order_fill"
        except (TypeError, ValueError):
            continue
    status = str(order.get("status") or "").strip().lower()
    if status in ("simulated", "submitted") and requested > 0:
        return float(requested), "requested"
    return None, "unavailable"


def exchange_supports_protective_stops() -> bool:
    """Capability check for exchange-native stop orders."""
    if config.PAPER_TRADING:
        return True
    try:
        exchange = get_ccxt_exchange()
        exchange.load_markets()
        return hasattr(exchange, "create_order")
    except Exception:
        return False


def place_protective_stop(
    symbol: str,
    *,
    side: str,
    size: float,
    stop_price: float,
) -> dict[str, Any]:
    """
    Place a protective stop-loss order (spot stop-loss limit/market style via ccxt).
    Paper mode returns a deterministic simulated order.
    """
    if size <= 0 or stop_price <= 0:
        return {"status": "skipped", "reason": "invalid_params"}

    if config.PAPER_TRADING:
        cid = _spot_new_client_order_id()
        order = {
            "id": f"paper-stop-{cid}",
            "symbol": symbol,
            "side": side,
            "amount": size,
            "stopPrice": stop_price,
            "status": "open",
            "success": True,
            "protective": True,
            "clientOrderId": cid,
        }
        logger.info("Paper protective stop: %s", order)
        return order

    if not exchange_supports_protective_stops():
        return {"status": "rejected", "success": False, "error": "exchange_unsupported"}

    exchange = get_ccxt_exchange()
    exchange.load_markets()
    amount = float(exchange.amount_to_precision(symbol, size))
    price = float(exchange.price_to_precision(symbol, stop_price))
    if amount <= 0:
        return {"status": "skipped", "reason": "precision_zero"}

    client_oid = _spot_new_client_order_id()
    params: dict[str, Any] = {
        "stopPrice": price,
        "newClientOrderId": client_oid,
    }
    try:
        order = call_ccxt_with_retries(
            exchange,
            lambda: exchange.create_order(symbol, "stop_loss_limit", side, amount, price, params),
            context=f"protective_stop {symbol}",
        )
        order["protective"] = True
        order["clientOrderId"] = client_oid
        logger.info("Protective stop placed: %s", order)
        return order
    except ccxt.BaseError as e:
        logger.error("Protective stop failed %s: %s", symbol, e)
        return {"status": "rejected", "success": False, "error": str(e)}


def _estimate_buy_cost_usdt(
    exchange: ccxt.Exchange,
    symbol: str,
    amount: float,
) -> tuple[float | None, str | None]:
    """Estimate quote notional in USDT for a market buy (price * amount * quote_usdt)."""
    try:
        ticker = call_ccxt_with_retries(
            exchange,
            lambda: exchange.fetch_ticker(symbol),
            context=f"precheck_ticker {symbol}",
        )
        px = float(ticker.get("ask") or ticker.get("last") or ticker.get("close") or 0)
        if px <= 0:
            return None, "ticker_price_unavailable"
    except Exception as e:
        return None, f"ticker_unavailable:{e}"

    from tr_bot.quote_usdt import QuoteUsdtRateError, usdt_per_quote_unit
    from tr_bot.symbols import split_pair

    try:
        _, quote = split_pair(symbol)
        quote_usdt = float(usdt_per_quote_unit(quote))
    except (QuoteUsdtRateError, ValueError) as e:
        return None, f"quote_usdt_unavailable:{e}"
    if quote_usdt <= 0:
        return None, "quote_usdt_nonpositive"
    return amount * px * quote_usdt, None


def _validate_live_buy(
    exchange: ccxt.Exchange,
    symbol: str,
    amount: float,
) -> dict[str, Any] | None:
    """
    Pre-submit live buy checks. Returns a rejected result dict or None if OK.
    Does not silently resize; caller must size within limits beforehand.
    """
    market = exchange.market(symbol) if hasattr(exchange, "market") else {}
    limits = (market or {}).get("limits") or {}
    min_amt = ((limits.get("amount") or {}).get("min")) if isinstance(limits, dict) else None
    min_cost = ((limits.get("cost") or {}).get("min")) if isinstance(limits, dict) else None
    try:
        if min_amt is not None and amount < float(min_amt):
            return _rejected("below_min_amount", amount=amount, min_amount=float(min_amt))
    except (TypeError, ValueError):
        pass

    notional_usdt, err = _estimate_buy_cost_usdt(exchange, symbol, amount)
    if notional_usdt is None:
        return _rejected(err or "notional_estimate_failed")

    live_cap = float(getattr(config, "LIVE_MAX_NOTIONAL_USDT", 0.0) or 0.0)
    if live_cap > 0 and notional_usdt > live_cap + 1e-9:
        return _rejected(
            "exceeds_live_max_notional",
            notional_usdt=notional_usdt,
            live_max_notional_usdt=live_cap,
        )

    if config.DEPLOYMENT_TIER == "live_canary":
        canary_cap = float(config.CANARY_MAX_NOTIONAL_USDT)
        if canary_cap > 0 and notional_usdt > canary_cap + 1e-9:
            return _rejected(
                "exceeds_canary_max_notional",
                notional_usdt=notional_usdt,
                canary_max_notional_usdt=canary_cap,
            )

    try:
        if min_cost is not None and notional_usdt < float(min_cost):
            return _rejected(
                "below_min_notional",
                notional_usdt=notional_usdt,
                min_cost=float(min_cost),
            )
    except (TypeError, ValueError):
        pass

    reserve_bps = float(getattr(config, "LIVE_BALANCE_RESERVE_BPS", 0.0) or 0.0)
    required = notional_usdt * (1.0 + reserve_bps / 10_000.0)
    from tr_bot.account import fetch_free_quote_amount

    free_quote = float(fetch_free_quote_amount(symbol))
    if free_quote + 1e-12 < required:
        return _rejected(
            "insufficient_quote_balance",
            free_quote=free_quote,
            required_quote=required,
            notional_usdt=notional_usdt,
            reserve_bps=reserve_bps,
        )
    return None


def _paper_fill_price(fill_price: float | None) -> float | None:
    if fill_price is None:
        return None
    try:
        px = float(fill_price)
    except (TypeError, ValueError):
        return None
    if px > 0 and math.isfinite(px):
        return px
    return None


def execute_trade(
    symbol: str,
    side: str,
    size: float,
    *,
    client_order_id: str | None = None,
    fill_price: float | None = None,
) -> dict[str, Any]:
    """
    Spot market order, or Binance Spot TWAP when EXECUTION_STYLE=twap.
    side: 'buy' (acquire base with quote) or 'sell' (sell base for quote).
    For paper trading, logs and returns a mock order without calling the API.
    Optional client_order_id is used for live market orders (idempotent exits).
    Optional fill_price is applied to paper mocks as average/price (order_fill).
    """
    if size <= 0:
        logger.warning("execute_trade skipped: size <= 0")
        return {"status": "skipped", "reason": "zero_size"}

    if config.PAPER_TRADING:
        px = _paper_fill_price(fill_price)
        if config.EXECUTION_STYLE == "twap":
            cid = _twap_client_algo_id()
            order: dict[str, Any] = {
                "id": cid,
                "symbol": symbol,
                "side": side,
                "amount": size,
                "filled": size,
                "status": "submitted",
                "success": True,
                "clientAlgoId": cid,
                "entry_execution": "twap",
                "twap_client_algo_id": cid,
                "twap_algo_state": "submitted",
            }
            if px is not None:
                order["average"] = px
                order["price"] = px
            logger.info("Paper spot TWAP (simulated submit): %s", order)
            return order
        oid = client_order_id or "paper"
        order = {
            "id": oid if oid != "paper" else "paper",
            "symbol": symbol,
            "side": side,
            "amount": size,
            "filled": size,
            "status": "simulated",
            "success": True,
            "clientOrderId": client_order_id or oid,
            "entry_execution": "market",
        }
        if px is not None:
            order["average"] = px
            order["price"] = px
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
        if market_side == "buy":
            rejected = _validate_live_buy(exchange, symbol, amount)
            if rejected is not None:
                logger.warning("Live buy rejected precheck %s: %s", symbol, rejected.get("reason"))
                return rejected

        client_oid = (client_order_id or "").strip() or _spot_new_client_order_id()
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
                return _unknown_execution_result(str(e2), client_order_id=client_oid)
        except ccxt.BaseError as e:
            unk = _twap_transient_unknown(e, client_order_id=client_oid)
            if unk is not None:
                return unk
            logger.error("Spot market order failed %s: %s", symbol, e)
            return {
                "status": "rejected",
                "success": False,
                "error": str(e),
                "clientOrderId": client_oid,
            }
        if isinstance(order, dict):
            order.setdefault("clientOrderId", client_oid)
            order.setdefault("entry_execution", "market")
            # Normalize filled for callers when exchange omits it on full fills.
            if order.get("filled") is None and order.get("amount") is not None:
                st = str(order.get("status") or "").lower()
                if st in ("closed", "filled"):
                    order["filled"] = order.get("amount")
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

"""Idempotent exit coordination: sole authority for submitting close orders."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from tr_bot import config
from tr_bot.account import fetch_free_base_amount
from tr_bot.execution import execute_trade, resolve_fill_price, resolve_fill_qty
from tr_bot.fees import live_fee_bps_for_symbol
from tr_bot.notifications import events as notify_events
from tr_bot.notifications.facade import notify
from tr_bot.open_positions_state import save_open_positions
from tr_bot.pnl_costs import realized_pnl_breakdown
from tr_bot.position import OpenPosition
from tr_bot.trade_ledger import append_closed_trade

logger = logging.getLogger("tr_bot.exit_coordinator")

_TERMINAL_ACK = frozenset(
    {"skipped", "rejected", "canceled", "cancelled", "expired", "unknown_execution", "unknown"}
)


def _new_exit_client_order_id() -> str:
    return "tx" + uuid.uuid4().hex[:32]


def _is_ccxt_symbol(symbol: str) -> bool:
    return "/" in symbol


class ExitCoordinator:
    """
    Single authority for closing open positions.

    Persist exit_state=pending with a stable clientOrderId before submission,
    then finalize, mark unknown, or keep partially_filled based on the response.
    """

    def __init__(
        self,
        positions: dict[str, OpenPosition | None],
        *,
        circuit: Any,
        cooldown_bars_remaining: dict[str, int],
        save_fn: Callable[[dict[str, OpenPosition | None]], None] | None = None,
    ) -> None:
        self.positions = positions
        self.circuit = circuit
        self.cooldown_bars_remaining = cooldown_bars_remaining
        self._save = save_fn or save_open_positions
        self._locks: dict[str, threading.Lock] = {}
        self._global = threading.Lock()

    def _lock_for(self, symbol: str) -> threading.Lock:
        with self._global:
            if symbol not in self._locks:
                self._locks[symbol] = threading.Lock()
            return self._locks[symbol]

    def request_exit(
        self,
        symbol: str,
        *,
        outcome: str,
        reason: str,
        fallback_price: float,
        execution_context: str = "STRATEGY_SIGNAL",
    ) -> dict[str, Any]:
        """
        Request a software exit for ``symbol``.
        outcome: win | loss (for circuit / trigger level).
        execution_context: STRATEGY_SIGNAL | REBALANCE_TRIM | ...; REBALANCE_* skips circuit.
        Returns a result dict with keys: status, closed?, remaining?, reason?
        """
        lock = self._lock_for(symbol)
        with lock:
            return self._request_exit_locked(
                symbol,
                outcome=outcome,
                reason=reason,
                fallback_price=fallback_price,
                execution_context=execution_context,
            )

    def reconcile_pending(self) -> dict[str, dict[str, Any]]:
        """Reconcile positions stuck in pending/unknown/partially_filled."""
        out: dict[str, dict[str, Any]] = {}
        for symbol, pos in list(self.positions.items()):
            if pos is None:
                continue
            st = str(getattr(pos, "exit_state", "active") or "active").lower()
            if st not in ("pending", "unknown", "partially_filled"):
                continue
            lock = self._lock_for(symbol)
            with lock:
                res = self._reconcile_one_locked(symbol)
                if res:
                    out[symbol] = res
        return out

    def _request_exit_locked(
        self,
        symbol: str,
        *,
        outcome: str,
        reason: str,
        fallback_price: float,
        execution_context: str = "STRATEGY_SIGNAL",
    ) -> dict[str, Any]:
        pos = self.positions.get(symbol)
        if pos is None:
            return {"status": "noop", "reason": "no_position"}

        st = str(getattr(pos, "exit_state", "active") or "active").lower()
        if st in ("pending", "unknown"):
            # Already in flight — reconcile rather than double-submit.
            return self._reconcile_one_locked(symbol) or {
                "status": "pending",
                "reason": "exit_already_in_flight",
            }

        close_side = "sell" if pos.side == "long" else "buy"
        size_close = float(pos.size)
        ctx = str(execution_context or "STRATEGY_SIGNAL").strip() or "STRATEGY_SIGNAL"
        if (
            not config.PAPER_TRADING
            and pos.side == "long"
            and _is_ccxt_symbol(symbol)
        ):
            free = fetch_free_base_amount(symbol)
            size_close = min(size_close, free)
            if size_close <= 0:
                # Base already gone — treat as closed at fallback if we never recorded a fill.
                logger.warning("Exit: no free base for %s; clearing position as already flat", symbol)
                return self._finalize_flat_without_order(
                    symbol,
                    pos,
                    outcome=outcome,
                    reason="already_flat_no_base",
                    fallback_price=fallback_price,
                    execution_context=ctx,
                )

        client_oid = str(getattr(pos, "exit_client_order_id", "") or "").strip()
        if not client_oid:
            client_oid = _new_exit_client_order_id()

        pos.exit_state = "pending"
        pos.exit_reason = reason
        pos.exit_outcome = outcome
        pos.exit_client_order_id = client_oid
        pos.exit_requested_utc = datetime.now(timezone.utc).isoformat()
        pos.exit_requested_size = float(size_close)
        try:
            fp = float(fallback_price)
            pos.exit_fill_price = fp if fp > 0 else 0.0
        except (TypeError, ValueError):
            pos.exit_fill_price = 0.0
        pos.execution_context = ctx
        self.positions[symbol] = pos
        self._save(self.positions)

        res = execute_trade(
            symbol,
            close_side,
            size_close,
            client_order_id=client_oid,
            fill_price=float(fallback_price) if fallback_price else None,
        )
        return self._handle_submit_result(
            symbol,
            res,
            outcome=outcome,
            reason=reason,
            fallback_price=fallback_price,
            size_requested=size_close,
            execution_context=ctx,
        )

    def _reconcile_one_locked(self, symbol: str) -> dict[str, Any] | None:
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        client_oid = str(getattr(pos, "exit_client_order_id", "") or "").strip()
        outcome = str(getattr(pos, "exit_outcome", "") or "loss")
        reason = str(getattr(pos, "exit_reason", "") or "reconcile")
        ctx = str(getattr(pos, "execution_context", "") or "STRATEGY_SIGNAL").strip() or "STRATEGY_SIGNAL"
        stored_fill = float(getattr(pos, "exit_fill_price", 0.0) or 0.0)
        if stored_fill > 0:
            fallback = stored_fill
        else:
            plan = pos.exit_plan()
            fallback = float(plan.stop_price) if outcome == "loss" else float(plan.take_price)
        size_req = float(getattr(pos, "exit_requested_size", 0.0) or pos.size)

        if config.PAPER_TRADING:
            # Paper: re-submit with same client id (deterministic mock).
            close_side = "sell" if pos.side == "long" else "buy"
            res = execute_trade(
                symbol,
                close_side,
                size_req if size_req > 0 else float(pos.size),
                client_order_id=client_oid or _new_exit_client_order_id(),
                fill_price=fallback,
            )
            return self._handle_submit_result(
                symbol,
                res,
                outcome=outcome,
                reason=reason,
                fallback_price=fallback,
                size_requested=size_req if size_req > 0 else float(pos.size),
                execution_context=ctx,
            )

        # Live: try fetch by client order id; if missing and base is zero, finalize flat.
        if not config.PAPER_TRADING and pos.side == "long" and _is_ccxt_symbol(symbol):
            free = fetch_free_base_amount(symbol)
            if free <= 1e-12 and float(pos.size) > 0:
                return self._finalize_flat_without_order(
                    symbol,
                    pos,
                    outcome=outcome,
                    reason="reconcile_already_flat",
                    fallback_price=fallback,
                    execution_context=ctx,
                )

        if client_oid:
            try:
                from tr_bot.binance_rest import call_ccxt_with_retries
                from tr_bot.exchange import get_ccxt_exchange

                exchange = get_ccxt_exchange()
                order = call_ccxt_with_retries(
                    exchange,
                    lambda: exchange.fetch_order("0", symbol, {"origClientOrderId": client_oid}),
                    context=f"reconcile_exit {symbol}",
                )
                return self._handle_submit_result(
                    symbol,
                    order if isinstance(order, dict) else {"status": "unknown"},
                    outcome=outcome,
                    reason=reason,
                    fallback_price=fallback,
                    size_requested=size_req if size_req > 0 else float(pos.size),
                    execution_context=ctx,
                )
            except Exception as e:
                logger.warning("Exit reconcile fetch failed %s: %s", symbol, e)
                pos.exit_state = "unknown"
                self.positions[symbol] = pos
                self._save(self.positions)
                return {"status": "unknown", "reason": str(e)}

        # No client id — re-request a fresh exit.
        return self._request_exit_locked(
            symbol,
            outcome=outcome or "loss",
            reason=reason or "reconcile_retry",
            fallback_price=fallback,
            execution_context=ctx,
        )

    def _handle_submit_result(
        self,
        symbol: str,
        res: dict[str, Any],
        *,
        outcome: str,
        reason: str,
        fallback_price: float,
        size_requested: float,
        execution_context: str = "STRATEGY_SIGNAL",
    ) -> dict[str, Any]:
        pos = self.positions.get(symbol)
        if pos is None:
            return {"status": "noop", "reason": "no_position"}

        status = str(res.get("status", "")).strip().lower()
        if status in _TERMINAL_ACK and status != "closed":
            if status in ("unknown_execution", "unknown"):
                pos.exit_state = "unknown"
                self.positions[symbol] = pos
                self._save(self.positions)
                logger.error("Exit unknown for %s: %s", symbol, res.get("error") or status)
                return {"status": "unknown", "reason": status}
            # Rejected / skipped — revert to active so a later loop can retry.
            pos.exit_state = "active"
            pos.exit_client_order_id = ""
            pos.exit_requested_size = 0.0
            self.positions[symbol] = pos
            self._save(self.positions)
            logger.error("Exit not acknowledged for %s: %s", symbol, status)
            return {"status": status, "reason": status}

        filled, _ = resolve_fill_qty(res, requested=size_requested)
        if filled is None or filled <= 0:
            pos.exit_state = "unknown"
            self.positions[symbol] = pos
            self._save(self.positions)
            return {"status": "unknown", "reason": "zero_or_missing_fill"}

        exit_px, fill_src = resolve_fill_price(res, fallback=fallback_price)
        if exit_px is None:
            exit_px = float(fallback_price)
            fill_src = "fallback"

        ctx = str(
            execution_context
            or getattr(pos, "execution_context", "")
            or "STRATEGY_SIGNAL"
        ).strip() or "STRATEGY_SIGNAL"
        return self._finalize_fill(
            symbol,
            pos,
            filled=float(filled),
            exit_px=float(exit_px),
            fill_src=fill_src,
            outcome=outcome,
            reason=reason,
            execution_context=ctx,
        )

    def _finalize_flat_without_order(
        self,
        symbol: str,
        pos: OpenPosition,
        *,
        outcome: str,
        reason: str,
        fallback_price: float,
        execution_context: str = "STRATEGY_SIGNAL",
    ) -> dict[str, Any]:
        filled = float(pos.size)
        return self._finalize_fill(
            symbol,
            pos,
            filled=filled,
            exit_px=float(fallback_price),
            fill_src="reconcile_flat",
            outcome=outcome,
            reason=reason,
            execution_context=execution_context,
        )

    def _finalize_fill(
        self,
        symbol: str,
        pos: OpenPosition,
        *,
        filled: float,
        exit_px: float,
        fill_src: str,
        outcome: str,
        reason: str,
        execution_context: str = "STRATEGY_SIGNAL",
    ) -> dict[str, Any]:
        exit_policy = str(getattr(pos, "exit_policy", "software_polled") or "software_polled")
        ctx = str(
            execution_context
            or getattr(pos, "execution_context", "")
            or "STRATEGY_SIGNAL"
        ).strip() or "STRATEGY_SIGNAL"
        affects_circuit = not ctx.startswith("REBALANCE_")
        br = realized_pnl_breakdown(
            pos.side,
            float(pos.entry_price),
            exit_px,
            float(filled),
            live_fee_bps_for_symbol(symbol),
            config.LIVE_SLIPPAGE_BPS,
            exit_price_assumption=fill_src,
        )
        plan = pos.exit_plan()
        trigger = float(plan.stop_price) if outcome == "loss" else float(plan.take_price)
        remaining = max(0.0, float(pos.size) - float(filled))
        row = {
            "utc": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "position_side": pos.side,
            "outcome": outcome,
            "entry_price": float(pos.entry_price),
            "exit_price": exit_px,
            "size": float(filled),
            "closed_size": float(filled),
            "remaining_size": remaining,
            "gross_pnl_usdt": br["gross_pnl_usdt"],
            "fee_usdt": br["fee_usdt"],
            "net_pnl_usdt": br["net_pnl_usdt"],
            "paper": config.PAPER_TRADING,
            "exit_price_assumption": br.get("exit_price_assumption"),
            "profile_id": pos.profile_id or None,
            "profile_hash": pos.profile_hash or None,
            "deployment_tier": pos.deployment_tier or config.DEPLOYMENT_TIER,
            "regime_at_entry": pos.regime_at_entry or None,
            "regime_confidence_at_entry": pos.regime_confidence_at_entry,
            "signal_branch": pos.signal_branch or None,
            "filters_passed": pos.filters_passed,
            "exit_mode": getattr(pos, "exit_mode", None) or "atr",
            "exit_policy": exit_policy,
            "trigger_level": trigger,
            "intended_risk_usdt": pos.intended_risk_usdt,
            "trade_score": getattr(pos, "trade_score", None),
            "score_components": getattr(pos, "score_components", None),
            "exit_reason": reason,
            "exit_client_order_id": getattr(pos, "exit_client_order_id", "") or None,
            "execution_context": ctx,
            "affects_circuit": affects_circuit,
        }
        append_closed_trade(row)
        if affects_circuit and self.circuit is not None:
            self.circuit.record_outcome(outcome == "win")
        if affects_circuit and outcome != "win" and self.cooldown_bars_remaining is not None:
            self.cooldown_bars_remaining[symbol] = max(
                int(config.LOSS_COOLDOWN_BARS),
                self.cooldown_bars_remaining.get(symbol, 0),
            )
        notify(
            notify_events.TRADE_CLOSED,
            symbol=symbol,
            outcome=outcome,
            position_side=pos.side,
            paper=config.PAPER_TRADING,
            reason=reason,
            execution_context=ctx,
        )

        if remaining > 1e-12:
            pos.size = remaining
            pos.cumulative_closed_size = float(pos.cumulative_closed_size or 0) + float(filled)
            pos.exit_filled_size = float(getattr(pos, "exit_filled_size", 0.0) or 0.0) + float(filled)
            pos.exit_state = "partially_filled"
            # Clear client id so a follow-up exit gets a new idempotency key for the remainder.
            pos.exit_client_order_id = ""
            pos.exit_requested_size = 0.0
            self.positions[symbol] = pos
            self._save(self.positions)
            return {
                "status": "partial",
                "closed": float(filled),
                "remaining": remaining,
                "outcome": outcome,
            }

        self.positions[symbol] = None
        self._save(self.positions)
        return {"status": "closed", "closed": float(filled), "remaining": 0.0, "outcome": outcome}

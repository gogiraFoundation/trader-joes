"""Single-threaded REST exit watchdog between strategy poll cycles.

Paper fills remain simulated and do not measure Binance slippage.
The watchdog supplements OHLCV high/low checks for sustained stop breaches;
it does not replace candle-wick detection.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from tr_bot import config
from tr_bot.binance_rest import call_ccxt_with_retries
from tr_bot.exchange import get_ccxt_exchange
from tr_bot.exit_coordinator import ExitCoordinator
from tr_bot.position import OpenPosition

logger = logging.getLogger("tr_bot.exit_watchdog")


def _ticker_last(symbol: str) -> float | None:
    if config.PAPER_TRADING:
        return None
    try:
        exchange = get_ccxt_exchange()
        t = call_ccxt_with_retries(
            exchange,
            lambda: exchange.fetch_ticker(symbol),
            context=f"exit_watchdog_ticker {symbol}",
        )
        px = float(t.get("last") or t.get("close") or 0)
        return px if px > 0 else None
    except Exception as e:
        logger.debug("exit watchdog ticker failed %s: %s", symbol, e)
        return None


def fetch_last_prices(
    symbols: list[str],
    *,
    ticker_fn: Callable[[str], float | None] | None = None,
) -> dict[str, float]:
    """Batch-ish last prices for active symbols (sequential REST; canary is typically one symbol)."""
    fn = ticker_fn or _ticker_last
    out: dict[str, float] = {}
    for sym in symbols:
        px = fn(sym)
        if px is not None and px > 0:
            out[sym] = float(px)
    return out


def run_exit_watchdog_once(
    coordinator: ExitCoordinator,
    positions: dict[str, OpenPosition | None],
    *,
    price_by_symbol: dict[str, float] | None = None,
    ticker_fn: Callable[[str], float | None] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Check active software-polled positions against last price for stop breaches only.
    Take-profit remains on the OHLCV high/low path in the main loop.
    """
    results: dict[str, dict[str, Any]] = {}
    active_syms = [
        s
        for s, p in positions.items()
        if p is not None
        and str(getattr(p, "exit_policy", "software_polled") or "software_polled") != "exchange_native"
        and str(getattr(p, "exit_state", "active") or "active").lower()
        in ("active", "partially_filled")
    ]
    if not active_syms:
        return results

    prices = price_by_symbol
    if prices is None:
        if config.PAPER_TRADING:
            # Paper: no REST; caller may inject price_by_symbol for tests.
            return results
        prices = fetch_last_prices(active_syms, ticker_fn=ticker_fn)

    for symbol in active_syms:
        pos = positions.get(symbol)
        if pos is None:
            continue
        px = prices.get(symbol)
        if px is None or px <= 0:
            continue
        plan = pos.exit_plan()
        stop = float(plan.stop_price)
        breached = False
        if pos.side == "long" and px <= stop:
            breached = True
        elif pos.side == "short" and px >= stop:
            breached = True
        if not breached:
            continue
        results[symbol] = coordinator.request_exit(
            symbol,
            outcome="loss",
            reason="watchdog_stop_breach",
            fallback_price=float(px),
        )
    return results


def sleep_with_exit_watchdog(
    total_seconds: float,
    *,
    coordinator: ExitCoordinator,
    positions: dict[str, OpenPosition | None],
    interval_s: float | None = None,
    ticker_fn: Callable[[str], float | None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> None:
    """
    Sleep for ``total_seconds`` while invoking the exit watchdog every ``interval_s``.
    Strategy evaluation cadence remains POLL_SECONDS; this only accelerates stop checks.
    """
    sleep = sleep_fn or time.sleep
    interval = float(
        interval_s
        if interval_s is not None
        else getattr(config, "EXIT_WATCHDOG_INTERVAL_S", 5.0) or 5.0
    )
    interval = max(1.0, interval)
    remaining = max(0.0, float(total_seconds))
    if remaining <= 0:
        run_exit_watchdog_once(coordinator, positions, ticker_fn=ticker_fn)
        # Allow test harnesses that patch sleep to interrupt after a cycle.
        sleep(0)
        return

    deadline = time.monotonic() + remaining
    while True:
        now = time.monotonic()
        left = deadline - now
        if left <= 0:
            break
        chunk = min(interval, left)
        sleep(chunk)
        try:
            run_exit_watchdog_once(coordinator, positions, ticker_fn=ticker_fn)
            coordinator.reconcile_pending()
        except Exception as e:
            logger.warning("exit watchdog step failed: %s", e)

"""Convert quote asset amounts to USDT for position sizing (ATR × rate = USDT per base)."""

from __future__ import annotations

import logging
import time
from typing import Any

from tr_bot import config
from tr_bot.exchange import get_ccxt_exchange

logger = logging.getLogger("tr_bot.quote_usdt")

# Treat as 1 USDT per 1 unit for sizing (spot stablecoins)
USDT_PEGGED: frozenset[str] = frozenset(
    {"USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP"}
)


class QuoteUsdtRateError(Exception):
    """Could not resolve USDT value for a quote currency (no market or bad price)."""


_cache: dict[str, tuple[float, float]] = {}


def clear_quote_usdt_cache() -> None:
    """Test helper: drop cached quote rates."""
    _cache.clear()


def _ttl() -> float:
    return max(1.0, float(config.QUOTE_USDT_CACHE_SECONDS))


def _cache_get(quote: str) -> float | None:
    now = time.monotonic()
    ent = _cache.get(quote)
    if ent is None:
        return None
    rate, ts = ent
    if now - ts > _ttl():
        del _cache[quote]
        return None
    return rate


def _cache_set(quote: str, rate: float) -> None:
    _cache[quote] = (rate, time.monotonic())


def usdt_per_quote_unit(quote: str, *, _exchange: Any | None = None) -> float:
    """
    How many USDT one unit of `quote` is worth (e.g. ETH -> ~3000).

    Used to express ATR (quote per base) as USDT per base: atr_usdt = atr * rate.

    Raises QuoteUsdtRateError if no Binance spot market resolves to a positive price.
    """
    q = (quote or "").strip().upper()
    if not q:
        raise QuoteUsdtRateError("empty quote asset")

    if q in USDT_PEGGED:
        return 1.0

    hit = _cache_get(q)
    if hit is not None:
        return hit

    ex = _exchange if _exchange is not None else get_ccxt_exchange()
    ex.load_markets()

    direct = f"{q}/USDT"
    inverted = f"USDT/{q}"

    last: float | None = None
    try:
        if direct in ex.markets:
            t = ex.fetch_ticker(direct)
            last = float(t.get("last") or t.get("close") or 0.0)
            if last > 0:
                _cache_set(q, last)
                return last
    except Exception as e:
        logger.debug("ticker %s: %s", direct, e)

    try:
        if inverted in ex.markets:
            t = ex.fetch_ticker(inverted)
            px = float(t.get("last") or t.get("close") or 0.0)
            if px > 0:
                last = 1.0 / px
                _cache_set(q, last)
                return last
    except Exception as e:
        logger.debug("ticker %s: %s", inverted, e)

    err = QuoteUsdtRateError(
        f"no USDT cross rate for quote={q!r} (tried {direct!r} and {inverted!r})"
    )
    logger.warning("%s", err)
    raise err

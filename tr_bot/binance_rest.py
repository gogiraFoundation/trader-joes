"""Shared Binance/ccxt REST backoff: 429, 418, 5xx, timeouts (Retry-After aware)."""

from __future__ import annotations

import logging
import random
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable, TypeVar

import ccxt

from tr_bot import config

logger = logging.getLogger("tr_bot.binance_rest")

T = TypeVar("T")

_HTTP_STATUS_RE = re.compile(r"\b(?:binance|binanceus)\s+(\d{3})\b", re.I)
_FLOOR_418_S = 60.0
_FLOOR_429_S = 2.0


def _header_get(headers: Any, name: str) -> str | None:
    if not headers or not isinstance(headers, dict):
        return None
    ln = name.lower()
    for k, v in headers.items():
        if str(k).lower() == ln:
            return str(v) if v is not None else None
    return None


def retry_after_seconds(headers: Any) -> float | None:
    """Parse Retry-After header (seconds or HTTP-date). Returns None if absent/invalid."""
    raw = _header_get(headers, "Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        sec = int(raw)
        return float(max(0, sec))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            delta = dt.timestamp() - time.time()
        else:
            delta = dt.timestamp() - time.time()
        return max(0.0, float(delta))
    except (TypeError, ValueError, OverflowError):
        return None


def _status_from_message(msg: str) -> int | None:
    m = _HTTP_STATUS_RE.search(msg)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    if " 418 " in msg:
        return 418
    if " 429 " in msg:
        return 429
    return None


def classify_ccxt_network_exc(exc: BaseException, exchange: Any) -> str:
    """
    Coarse bucket for backoff policy.
    Returns: ban_418 | throttle_429 | server_5xx | timeout | transient_network | other
    """
    if isinstance(exc, ccxt.RequestTimeout):
        return "timeout"
    if isinstance(exc, (ccxt.ExchangeNotAvailable, ccxt.OnMaintenance)):
        return "server_5xx"
    if isinstance(exc, (ccxt.DDoSProtection, ccxt.RateLimitExceeded)):
        msg = str(exc)
        st = _status_from_message(msg)
        if st == 418:
            return "ban_418"
        if st == 429:
            return "throttle_429"
        if " 418 " in msg:
            return "ban_418"
        return "throttle_429"
    if isinstance(exc, ccxt.NetworkError):
        if isinstance(exc, (ccxt.InvalidNonce, ccxt.ChecksumError)):
            return "other"
        return "transient_network"
    return "other"


def sleep_seconds_for_transient(
    kind: str,
    exchange: Any,
    attempt: int,
    *,
    retry_after_cap: float | None = None,
) -> float:
    """Monotonic sleep duration before retry `attempt` (0-based)."""
    base = float(config.BINANCE_HTTP_BASE_DELAY_S)
    cap = float(config.BINANCE_HTTP_MAX_DELAY_S)
    headers = getattr(exchange, "last_response_headers", None)
    ra = retry_after_seconds(headers)
    if retry_after_cap is not None and ra is not None:
        ra = min(ra, retry_after_cap)

    if kind == "ban_418":
        floor = _FLOOR_418_S
        t = max(floor, ra or 0.0)
        return min(cap, t)

    if kind == "throttle_429":
        exp = base * (2**attempt) + random.uniform(0, 0.35)  # nosec B311
        floor = _FLOOR_429_S
        t = max(floor, ra or 0.0, exp)
        return min(cap, t)

    if kind in ("server_5xx", "timeout", "transient_network"):
        exp = base * (2**attempt) + random.uniform(0, 0.35)  # nosec B311
        t = max(0.25, ra or 0.0, exp)
        return min(cap, t)

    exp = base * (2**attempt) + random.uniform(0, 0.25)  # nosec B311
    return min(cap, max(0.1, exp))


def call_ccxt_with_retries(
    exchange: ccxt.Exchange,
    fn: Callable[[], T],
    *,
    context: str = "",
    max_retries: int | None = None,
) -> T:
    """
    Run a ccxt call with bounded retries on throttles, 5xx-class unavailability, and timeouts.
    Does not swallow business errors (e.g. InsufficientFunds).
    """
    n = max(1, int(max_retries if max_retries is not None else config.BINANCE_HTTP_MAX_RETRIES))
    last_exc: BaseException | None = None
    for attempt in range(n):
        try:
            return fn()
        except (
            ccxt.DDoSProtection,
            ccxt.RateLimitExceeded,
            ccxt.ExchangeNotAvailable,
            ccxt.OnMaintenance,
            ccxt.RequestTimeout,
        ) as e:
            last_exc = e
            kind = classify_ccxt_network_exc(e, exchange)
            if attempt == n - 1:
                raise
            delay = sleep_seconds_for_transient(kind, exchange, attempt)
            logger.warning(
                "%s transient %s (%s) attempt %s/%s; sleeping %.1fs",
                context or "ccxt",
                type(e).__name__,
                kind,
                attempt + 1,
                n,
                delay,
            )
            time.sleep(delay)
        except ccxt.NetworkError as e:
            if isinstance(e, (ccxt.InvalidNonce, ccxt.ChecksumError, ccxt.DDoSProtection, ccxt.RequestTimeout)):
                raise
            last_exc = e
            kind = classify_ccxt_network_exc(e, exchange)
            if kind == "other":
                raise
            if attempt == n - 1:
                raise
            delay = sleep_seconds_for_transient(kind, exchange, attempt)
            if kind == "transient_network":
                logger.debug(
                    "%s network %s (%s) attempt %s/%s; sleeping %.1fs",
                    context or "ccxt",
                    type(e).__name__,
                    kind,
                    attempt + 1,
                    n,
                    delay,
                )
            else:
                logger.warning(
                    "%s network %s (%s) attempt %s/%s; sleeping %.1fs",
                    context or "ccxt",
                    type(e).__name__,
                    kind,
                    attempt + 1,
                    n,
                    delay,
                )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc

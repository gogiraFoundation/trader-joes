"""Advisory cache (Redis) + refresh worker; LLM remains non-authoritative."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from tr_bot import config
from tr_bot.news import news_for_symbol
from tr_bot.sentiment import get_sentiment

logger = logging.getLogger("tr_bot.advise")

_ADVISORY_SCHEMA = {"sentiment", "confidence", "ts", "symbol"}
_symbol_locks: dict[str, threading.Lock] = {}
_symbol_locks_guard = threading.Lock()


def _symbol_lock(symbol: str) -> threading.Lock:
    """Serialize LLM + news fetch per symbol (worker vs trading loop)."""
    with _symbol_locks_guard:
        if symbol not in _symbol_locks:
            _symbol_locks[symbol] = threading.Lock()
        return _symbol_locks[symbol]


def _redis():
    import redis

    return redis.from_url(
        config.REDIS_URL,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
    )


def advisory_key(symbol: str) -> str:
    safe = symbol.replace(":", "_").replace("/", "_")[:80]
    return f"tr_bot:advisory:v1:{safe}"


def _validate_payload(obj: dict[str, Any]) -> dict[str, Any] | None:
    if not _ADVISORY_SCHEMA.issubset(set(obj.keys())):
        return None
    s = str(obj["sentiment"]).lower()
    if s not in ("bullish", "bearish", "neutral"):
        return None
    return {
        "sentiment": s,
        "confidence": float(obj.get("confidence", 0.7)),
        "ts": str(obj["ts"]),
        "symbol": str(obj["symbol"]),
    }


def refresh_advisory(symbol: str) -> dict[str, Any] | None:
    """Fetch news + LLM; store in Redis with TTL. Returns payload or None."""
    if not config.USE_LLM:
        return None
    with _symbol_lock(symbol):
        news = news_for_symbol(symbol)
        sentiment = get_sentiment(news)
        conf = 0.75
        payload = {
            "sentiment": sentiment,
            "confidence": conf,
            "ts": str(int(time.time())),
            "symbol": symbol,
        }
        if not config.MANAGERS_ENABLED or not config.ADVISORY_USE_REDIS:
            return payload
        try:
            r = _redis()
            key = advisory_key(symbol)
            r.setex(
                key,
                config.ADVISORY_TTL_SECONDS,
                json.dumps(payload),
            )
        except Exception as e:
            logger.warning("Advisory Redis write failed (fallback to live LLM): %s", e)
        return payload


def get_cached_advisory(symbol: str) -> dict[str, Any] | None:
    if not config.MANAGERS_ENABLED or not config.ADVISORY_USE_REDIS:
        return None
    try:
        r = _redis()
        raw = r.get(advisory_key(symbol))
        if not raw:
            return None
        obj = json.loads(raw.decode("utf-8"))
        v = _validate_payload(obj)
        return v
    except Exception as e:
        logger.debug("Advisory cache miss: %s", e)
        return None


def get_sentiment_for_symbol(symbol: str) -> str:
    """Prefer cache when enabled; else live pipeline."""
    if not config.USE_LLM:
        return "neutral"
    if config.MANAGERS_ENABLED and config.ADVISORY_USE_REDIS:
        c = get_cached_advisory(symbol)
        if c and c.get("confidence", 0) >= config.LLM_MIN_CONFIDENCE:
            return str(c["sentiment"])
    with _symbol_lock(symbol):
        if config.MANAGERS_ENABLED and config.ADVISORY_USE_REDIS:
            c = get_cached_advisory(symbol)
            if c and c.get("confidence", 0) >= config.LLM_MIN_CONFIDENCE:
                return str(c["sentiment"])
        news = news_for_symbol(symbol)
        return get_sentiment(news)


def _worker_loop(stop: threading.Event) -> None:
    symbols = __import__("tr_bot.managers.portfolio", fromlist=["active_symbols"]).active_symbols()
    while not stop.wait(timeout=config.ADVISORY_WORKER_INTERVAL_SECONDS):
        for sym in symbols:
            if stop.is_set():
                break
            try:
                refresh_advisory(sym)
            except Exception as e:
                logger.exception("Advisory refresh failed for %s: %s", sym, e)


def start_advisory_worker() -> tuple[threading.Thread, threading.Event]:
    """Background refresher (in-process). For production prefer separate process + cron."""
    stop = threading.Event()
    t = threading.Thread(
        target=_worker_loop,
        args=(stop,),
        name="advise-worker",
        daemon=True,
    )
    t.start()
    return t, stop

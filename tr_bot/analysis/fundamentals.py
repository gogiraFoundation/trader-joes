"""Fundamental snapshots (equities via yfinance, crypto via CoinGecko) with Redis cache."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from tr_bot import config

logger = logging.getLogger("tr_bot.fundamentals")

_CACHE_PREFIX = "tr_bot:fundamentals:v1:"


def _redis():
    import redis

    return redis.from_url(
        config.REDIS_URL,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
    )


def _cache_key(symbol: str) -> str:
    safe = symbol.replace(":", "_").replace("/", "_")[:120]
    return f"{_CACHE_PREFIX}{safe}"


def _parse_coingecko_ids() -> dict[str, str]:
    out: dict[str, str] = {}
    raw = (config.COINGECKO_IDS or "").strip()
    if not raw:
        out["BTC"] = "bitcoin"
        out["ETH"] = "ethereum"
        return out
    for part in raw.split(","):
        p = part.strip()
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().upper()] = v.strip()
    return out


def _fetch_crypto_fundamentals(symbol: str) -> dict[str, Any]:
    base = symbol.split("/")[0].upper() if "/" in symbol else symbol.upper()
    cid = _parse_coingecko_ids().get(base, "bitcoin")
    url = f"https://api.coingecko.com/api/v3/coins/{cid}"
    r = requests.get(
        url,
        params={"localization": "false", "tickers": "false", "market_data": "true"},
        timeout=config.FUNDAMENTALS_HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    md = data.get("market_data") or {}
    mc = md.get("market_cap") or {}
    return {
        "asset_class": "crypto",
        "symbol": symbol,
        "coingecko_id": cid,
        "market_cap_rank": data.get("market_cap_rank"),
        "market_cap_usd": mc.get("usd"),
    }


def _fetch_equity_fundamentals(ticker: str) -> dict[str, Any]:
    import yfinance as yf

    t = yf.Ticker(ticker)
    info = t.info or {}
    cal = getattr(t, "calendar", None)
    earnings_dates = None
    if cal is not None and not getattr(cal, "empty", True):
        try:
            earnings_dates = str(cal)
        except Exception:
            earnings_dates = None
    return {
        "asset_class": "equity",
        "symbol": ticker,
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "sector": info.get("sector"),
        "earnings_timestamp": info.get("earningsTimestamp"),
        "earnings_calendar": earnings_dates,
    }


def fetch_fundamentals_snapshot(symbol: str) -> dict[str, Any]:
    """Return JSON-serializable fundamentals; cached in Redis when enabled."""
    if not config.USE_FUNDAMENTALS_FILTER:
        return {"disabled": True}

    if config.FUNDAMENTALS_USE_REDIS:
        try:
            r = _redis()
            raw = r.get(_cache_key(symbol))
            if raw:
                return json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.debug("Fundamentals cache read failed: %s", e)

    snap: dict[str, Any]
    try:
        if "/" in symbol:
            snap = _fetch_crypto_fundamentals(symbol)
        else:
            snap = _fetch_equity_fundamentals(symbol)
        snap["fetched_ts"] = int(time.time())
    except Exception as e:
        logger.warning("Fundamentals fetch failed for %s: %s", symbol, e)
        snap = {"error": str(e), "symbol": symbol, "fetched_ts": int(time.time())}

    if config.FUNDAMENTALS_USE_REDIS and "error" not in snap:
        try:
            r = _redis()
            r.setex(
                _cache_key(symbol),
                config.FUNDAMENTALS_CACHE_TTL_SECONDS,
                json.dumps(snap),
            )
        except Exception as e:
            logger.debug("Fundamentals cache write failed: %s", e)

    return snap


def allows_fundamentals(symbol: str, side: str, snap: dict[str, Any] | None) -> bool:
    """Non-authoritative vetoes (earnings window, illiquid crypto). Fail-open on errors."""
    if not config.USE_FUNDAMENTALS_FILTER:
        return True
    if snap is None:
        return True
    if snap.get("disabled"):
        return True
    if snap.get("error"):
        return True

    if snap.get("asset_class") == "equity" and side == "buy":
        ts = snap.get("earnings_timestamp")
        if ts is not None:
            now = time.time()
            et = float(ts)
            if et > 1e12:
                et /= 1000.0
            window = float(config.EARNINGS_BLOCK_SECONDS)
            if abs(et - now) <= window:
                logger.info("Fundamentals veto: earnings window for %s", symbol)
                return False

    if snap.get("asset_class") == "crypto":
        rank = snap.get("market_cap_rank")
        if rank is not None and int(rank) > int(config.MAX_CRYPTO_MARKET_CAP_RANK):
            logger.info("Fundamentals veto: market cap rank too weak for %s", symbol)
            return False

    return True

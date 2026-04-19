"""Lazy ccxt exchange (Binance spot by default)."""

from __future__ import annotations

from urllib.parse import urlparse

import ccxt

from tr_bot import config

_exchange: ccxt.Exchange | None = None


def reset_ccxt_exchange() -> None:
    """Drop cached client so the next call builds a fresh requests session."""
    global _exchange
    _exchange = None


def _binance_urls_override() -> dict | None:
    """
    Merge custom api/sapi origins into ccxt's nested urls['api'] map.
    BINANCE_API_BASE_URL: origin without path, e.g. https://api-gcp.binance.com
    BINANCE_SAPI_BASE_URL: full base including /sapi/v1, e.g. https://api.binance.com/sapi/v1
    """
    api_origin = (config.BINANCE_API_BASE_URL or "").strip().rstrip("/")
    sapi_full = (config.BINANCE_SAPI_BASE_URL or "").strip().rstrip("/")
    if not api_origin and not sapi_full:
        return None
    if api_origin and not sapi_full:
        sapi_full = f"{api_origin}/sapi/v1"
    if sapi_full and not api_origin:
        p = urlparse(sapi_full)
        path = (p.path or "").rstrip("/")
        if path.endswith("/sapi/v1"):
            api_origin = f"{p.scheme}://{p.netloc}".rstrip("/")
    if not api_origin:
        return None
    api_block = {
        "public": f"{api_origin}/api/v3",
        "private": f"{api_origin}/api/v3",
        "v1": f"{api_origin}/api/v1",
        "sapi": sapi_full or f"{api_origin}/sapi/v1",
    }
    # Keep sibling sapi versions on same host as default ccxt layout.
    api_block["sapiV2"] = f"{api_origin}/sapi/v2"
    api_block["sapiV3"] = f"{api_origin}/sapi/v3"
    api_block["sapiV4"] = f"{api_origin}/sapi/v4"
    return {"api": api_block}


def get_ccxt_exchange() -> ccxt.Exchange:
    global _exchange
    if _exchange is not None:
        return _exchange
    spot_opts: dict = {"defaultType": "spot"}
    if config.BINANCE_RECV_WINDOW_MS is not None:
        spot_opts["recvWindow"] = int(config.BINANCE_RECV_WINDOW_MS)
    opts: dict = {
        "enableRateLimit": True,
        "timeout": 30_000,
        "options": spot_opts,
    }
    url_patch = _binance_urls_override()
    if url_patch is not None:
        opts["urls"] = url_patch
    # Paper runs should not attach API keys: it can trigger signed SAPI endpoints
    # (e.g. /sapi/v1/capital/config/getall) and fail due to permissions/IP/time drift.
    if (not config.PAPER_TRADING) and config.BINANCE_API_KEY and config.BINANCE_SECRET:
        opts["apiKey"] = config.BINANCE_API_KEY
        opts["secret"] = config.BINANCE_SECRET
    _exchange = ccxt.binance(opts)
    return _exchange

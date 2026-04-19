"""aiohttp fetch for the real-time poller (async I/O)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import aiohttp

from tr_bot import config
from tr_bot.coindesk.auth import build_auth_headers
from tr_bot.coindesk.normalize import parse_latest_ticks

logger = logging.getLogger("tr_bot.coindesk.async_fetch")


async def fetch_latest_ticks_aiohttp(session: aiohttp.ClientSession) -> list[dict[str, Any]]:
    """GET latest ticks using an existing ClientSession (reuse connections across polls)."""
    path = config.COINDESK_LATEST_TICK_PATH
    base = config.COINDESK_BASE_URL.rstrip("/")
    url = path if path.startswith("http") else urljoin(base + "/", path.lstrip("/"))
    params: dict[str, Any] = {
        "market": config.COINDESK_DEFAULT_MARKET,
        "instruments": config.COINDESK_INSTRUMENTS,
        "apply_mapping": str(config.COINDESK_APPLY_MAPPING).lower(),
    }
    grp = config.COINDESK_TICK_GROUPS.strip()
    if grp:
        params["groups"] = grp
    headers = build_auth_headers()
    async with session.get(url, params=params, headers=headers) as resp:
        if resp.status == 429:
            logger.warning("CoinDesk aiohttp 429")
            return []
        resp.raise_for_status()
        payload = await resp.json()
    if not isinstance(payload, dict):
        return []
    return parse_latest_ticks(payload, market=config.COINDESK_DEFAULT_MARKET)

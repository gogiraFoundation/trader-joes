"""Shared CoinMarketCap Pro REST GET with 429 / Retry-After handling."""

from __future__ import annotations

import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any

import requests

logger = logging.getLogger("tr_bot.analysis.cmc_client")

CMC_PRO_BASE = "https://pro-api.coinmarketcap.com"


def _retry_after_seconds(response: requests.Response, attempt: int) -> float:
    raw = (response.headers.get("Retry-After") or "").strip()
    if raw.isdigit():
        return min(float(raw), 120.0)
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            delta = (dt.timestamp() - time.time())
            if delta > 0:
                return min(delta, 120.0)
    except (TypeError, ValueError, OSError):
        pass
    return min(2.0**attempt, 60.0)


def cmc_pro_get(
    url: str,
    *,
    api_key: str,
    params: dict[str, Any] | None = None,
    timeout: float = 60.0,
    max_attempts: int = 5,
) -> requests.Response:
    """
    GET with X-CMC_PRO_API_KEY. Retries on HTTP 429 with Retry-After or capped exponential backoff.
    Returns the last response (caller checks status / JSON).
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError("CoinMarketCap API key is required")

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "deflate, gzip",
        "X-CMC_PRO_API_KEY": key,
    }
    last: requests.Response | None = None
    for attempt in range(max_attempts):
        r = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
        last = r
        if r.status_code != 429:
            return r
        if attempt + 1 >= max_attempts:
            logger.warning("CMC GET %s HTTP 429 after %s attempts", url, max_attempts)
            return r
        wait = _retry_after_seconds(r, attempt)
        logger.warning(
            "CMC GET %s HTTP 429; sleeping %.1fs (attempt %s/%s)",
            url,
            wait,
            attempt + 1,
            max_attempts,
        )
        time.sleep(wait)
    assert last is not None
    return last

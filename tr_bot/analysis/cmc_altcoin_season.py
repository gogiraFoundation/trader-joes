"""CoinMarketCap Altcoin Season Index (latest) with in-process TTL cache."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from tr_bot import config
from tr_bot.analysis.cmc_client import CMC_PRO_BASE, cmc_pro_get

logger = logging.getLogger("tr_bot.analysis.cmc_altcoin_season")

ALTS_LATEST_URL = f"{CMC_PRO_BASE}/v1/altcoin-season-index/latest"

_cache_mono: float = 0.0
_cache_snapshot: dict[str, Any] | None = None


def get_altcoin_season_snapshot() -> dict[str, Any]:
    """
    Latest altcoin season index. Refreshed at most every CMC_ALTCOIN_SEASON_TTL_S (>= 15m recommended).
    """
    ttl = max(900, int(config.CMC_ALTCOIN_SEASON_TTL_S))
    key = (config.CMC_API_KEY or "").strip()
    if not config.CMC_ALTCOIN_SEASON_ENABLED:
        return {
            "ok": False,
            "error": "disabled",
            "fetched_utc": None,
            "ttl_s": ttl,
            "data": None,
        }
    if not key:
        return {
            "ok": False,
            "error": "missing_api_key",
            "fetched_utc": None,
            "ttl_s": ttl,
            "data": None,
        }

    now_m = time.monotonic()
    global _cache_mono, _cache_snapshot
    if _cache_snapshot is not None and (now_m - _cache_mono) < float(ttl):
        snap = dict(_cache_snapshot)
        snap["ttl_s"] = ttl
        return snap

    try:
        r = cmc_pro_get(ALTS_LATEST_URL, api_key=key, params={}, timeout=30.0)
    except (OSError, ValueError) as e:
        logger.warning("CMC altcoin season request failed: %s", e)
        return {
            "ok": False,
            "error": str(e)[:240],
            "fetched_utc": None,
            "ttl_s": ttl,
            "data": None,
        }

    if r.status_code != 200:
        logger.warning("CMC altcoin season HTTP %s: %s", r.status_code, r.text[:400])
        return {
            "ok": False,
            "error": f"HTTP {r.status_code}",
            "fetched_utc": None,
            "ttl_s": ttl,
            "data": None,
        }

    try:
        payload = r.json()
    except ValueError as e:
        return {
            "ok": False,
            "error": f"invalid_json:{e}"[:240],
            "fetched_utc": None,
            "ttl_s": ttl,
            "data": None,
        }

    st = payload.get("status") or {}
    if st.get("error_code") not in (None, 0):
        msg = st.get("error_message") or str(st.get("error_code"))
        return {
            "ok": False,
            "error": str(msg)[:240],
            "fetched_utc": None,
            "ttl_s": ttl,
            "data": None,
        }

    raw = payload.get("data")
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "error": "unexpected_payload",
            "fetched_utc": None,
            "ttl_s": ttl,
            "data": None,
        }

    fetched_utc = datetime.now(timezone.utc).isoformat()
    data = {
        "value": raw.get("value") if raw.get("value") is not None else raw.get("index"),
        "timestamp": raw.get("timestamp") or raw.get("last_updated"),
    }
    out: dict[str, Any] = {
        "ok": True,
        "error": None,
        "fetched_utc": fetched_utc,
        "ttl_s": ttl,
        "data": data,
    }
    _cache_snapshot = dict(out)
    _cache_mono = now_m
    return dict(out)

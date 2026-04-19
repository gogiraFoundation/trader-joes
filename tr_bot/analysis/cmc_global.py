"""CoinMarketCap global metrics (latest) with in-process TTL cache."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from tr_bot import config
from tr_bot.analysis.cmc_client import cmc_pro_get

logger = logging.getLogger("tr_bot.analysis.cmc_global")

CMC_GLOBAL_METRICS_URL = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"

_cache_mono: float = 0.0
_cache_snapshot: dict[str, Any] | None = None


def _normalize_quote_data(raw: dict[str, Any], convert: str) -> dict[str, Any]:
    """Flatten dominance + quote totals for one fiat/crypto convert code."""
    cu = convert.upper().strip() or "USD"
    out: dict[str, Any] = {
        "btc_dominance": raw.get("btc_dominance"),
        "eth_dominance": raw.get("eth_dominance"),
        "active_cryptocurrencies": raw.get("active_cryptocurrencies"),
        "active_exchanges": raw.get("active_exchanges"),
        "last_updated": raw.get("last_updated"),
    }
    quote = raw.get("quote") if isinstance(raw.get("quote"), dict) else {}
    q = quote.get(cu) if isinstance(quote, dict) else {}
    if not isinstance(q, dict):
        q = {}
    tmc = q.get("total_market_cap")
    tv = q.get("total_volume_24h")
    if tmc is None:
        tmc = raw.get("total_market_cap")
    if tv is None:
        tv = raw.get("total_volume_24h")
    out["total_market_cap"] = tmc
    out["total_volume_24h"] = tv
    return out


def get_global_metrics_snapshot() -> dict[str, Any]:
    """
    Latest global metrics from CMC, refreshed at most every CMC_GLOBAL_METRICS_TTL_S.
    Returns: ok, error, fetched_utc, ttl_s, data (normalized dict or None).
    """
    ttl = max(1, int(config.CMC_GLOBAL_METRICS_TTL_S))
    key = (config.CMC_API_KEY or "").strip()
    if not config.CMC_GLOBAL_METRICS_ENABLED:
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

    convert = (config.CMC_CONVERT or "USD").strip() or "USD"
    params = {"convert": convert}
    try:
        r = cmc_pro_get(
            CMC_GLOBAL_METRICS_URL,
            api_key=key,
            params=params,
            timeout=30.0,
        )
    except (OSError, ValueError) as e:
        logger.warning("CMC global metrics request failed: %s", e)
        return {
            "ok": False,
            "error": str(e)[:240],
            "fetched_utc": None,
            "ttl_s": ttl,
            "data": None,
        }

    if r.status_code != 200:
        logger.warning("CMC global metrics HTTP %s: %s", r.status_code, r.text[:400])
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
    code = st.get("error_code")
    if code not in (None, 0):
        msg = st.get("error_message") or str(code)
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
    data = _normalize_quote_data(raw, convert)
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

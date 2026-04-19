"""CoinDesk Asset Top List and on-chain catalog endpoints (not index OHLC)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.coindesk.client import CoinDeskClient

logger = logging.getLogger("tr_bot.coindesk.asset_catalog")

_mem_cache: dict[str, tuple[float, Any]] = {}


def _path(p: str) -> str:
    p = p.strip()
    return p if p.startswith("/") else f"/{p}"


def _ttl_s() -> float:
    return max(0.0, float(config.COINDESK_ASSET_TOP_LIST_CACHE_TTL_S))


def _mem_get(key: str) -> Any | None:
    ttl = _ttl_s()
    if ttl <= 0:
        return None
    hit = _mem_cache.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.monotonic() - ts > ttl:
        del _mem_cache[key]
        return None
    return val


def _mem_set(key: str, val: Any) -> None:
    if _ttl_s() <= 0:
        return
    _mem_cache[key] = (time.monotonic(), val)


def _redis_key(payload_key: str) -> str:
    return f"coindesk:asset:{payload_key}"


def _redis_get(key: str) -> Any | None:
    if not config.COINDESK_ASSET_TOP_LIST_USE_REDIS:
        return None
    try:
        import redis

        r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
        raw = r.get(_redis_key(key))
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.debug("CoinDesk asset Redis get skipped: %s", e)
    return None


def _redis_set(key: str, val: Any) -> None:
    if not config.COINDESK_ASSET_TOP_LIST_USE_REDIS:
        return
    try:
        import redis

        r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
        ttl = max(1, int(_ttl_s()) or 30)
        r.setex(_redis_key(key), ttl, json.dumps(val, default=str))
    except Exception as e:
        logger.debug("CoinDesk asset Redis set skipped: %s", e)


def _params_key(params: dict[str, Any]) -> str:
    raw = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def fetch_asset_top_list(
    client: CoinDeskClient | None = None,
    *,
    page: int | None = None,
    page_size: int | None = None,
    sort_by: str | None = None,
    groups: str | None = None,
) -> dict[str, Any]:
    """
    GET /asset/v1/top/list — cross-asset rankings (pagination, sort_by, optional groups).

    Cached in-process and optionally Redis (same TTL). Not a substitute for cadli index OHLC.
    """
    c = client or CoinDeskClient()
    pg = max(1, int(page if page is not None else config.COINDESK_ASSET_TOP_LIST_PAGE))
    ps = max(10, min(100, int(page_size if page_size is not None else config.COINDESK_ASSET_TOP_LIST_PAGE_SIZE)))
    sb = (sort_by or config.COINDESK_ASSET_TOP_LIST_SORT_BY).strip()
    params: dict[str, Any] = {"page": pg, "page_size": ps, "sort_by": sb}
    grp = (groups if groups is not None else config.COINDESK_ASSET_TOP_LIST_GROUPS).strip()
    if grp:
        params["groups"] = grp
    key = "toplist:" + _params_key(params)
    hit = _mem_get(key)
    if hit is not None:
        return hit
    rh = _redis_get(key)
    if rh is not None:
        _mem_set(key, rh)
        return rh
    path = _path(config.COINDESK_ASSET_TOP_LIST_PATH)
    payload = c.get(path, params, endpoint_metric="asset_top_list")
    _mem_set(key, payload)
    _redis_set(key, payload)
    return payload


def fetch_onchain_by_address(
    client: CoinDeskClient | None = None,
    *,
    address: str,
    chain: str | None = None,
    groups: str | None = None,
) -> dict[str, Any]:
    """
    GET /onchain/v2/data/by/address — token metadata for a contract (expand portfolios beyond spot BTC).

    ``chain`` is passed when the API expects it (e.g. chain slug or id); see CoinDesk docs for your asset.
    """
    c = client or CoinDeskClient()
    params: dict[str, Any] = {"address": address.strip()}
    if chain:
        params["chain"] = chain.strip()
    if groups:
        params["groups"] = groups.strip()
    path = _path(config.COINDESK_ONCHAIN_BY_ADDRESS_PATH)
    return c.get(path, params, endpoint_metric="onchain_by_address")


def fetch_chain_summary_by_chain(
    client: CoinDeskClient | None = None,
    *,
    chain: str,
    groups: str | None = None,
) -> dict[str, Any]:
    """
    GET /onchain/v3/summary/by/chain — chain-level catalog (discovery; 300s cache in docs — call sparingly).
    """
    c = client or CoinDeskClient()
    params: dict[str, Any] = {"chain": chain.strip()}
    if groups:
        params["groups"] = groups.strip()
    path = _path(config.COINDESK_ONCHAIN_CHAIN_SUMMARY_PATH)
    return c.get(path, params, endpoint_metric="onchain_chain_summary")


def watch_symbols_for_top_list() -> list[str]:
    raw = config.COINDESK_ASSET_TOP_LIST_WATCH_SYMBOLS.strip()
    if raw:
        return [x.strip().upper() for x in raw.split(",") if x.strip()]
    out: list[str] = []
    for part in config.COINDESK_INSTRUMENTS.split(","):
        p = part.strip().upper()
        if not p:
            continue
        if "-" in p:
            out.append(p.split("-")[0])
        elif "/" in p:
            out.append(p.split("/")[0])
        else:
            out.append(p)
    return out or ["BTC", "ETH"]


def _list_rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("Data") or payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        inner = data.get("list") or data.get("List") or data.get("data") or data.get("Data")
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
    return []


def _row_symbol(row: dict[str, Any]) -> str | None:
    for k in ("SYMBOL", "symbol", "BASE_ASSET", "base_asset", "ASSET_SYMBOL", "asset_symbol"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    return None


def _row_rank(row: dict[str, Any]) -> int | None:
    for k in ("TOPLIST_RANK", "toplist_rank", "RANK", "rank"):
        v = row.get(k)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return None


def build_top_list_snapshot_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize API payload to a small structure for JSON file + dashboard."""
    rows = _list_rows_from_payload(payload)
    watch = set(watch_symbols_for_top_list())
    top_rows: list[dict[str, Any]] = []
    watched_ranks: dict[str, int | None] = {}
    lim = max(1, int(config.COINDESK_ASSET_TOP_LIST_PAGE_SIZE))
    for row in rows[:lim]:
        sym = _row_symbol(row)
        rk = _row_rank(row)
        if sym is None:
            continue
        if rk is not None:
            top_rows.append({"symbol": sym, "rank": rk})
        if sym in watch:
            watched_ranks[sym] = rk
    for w in watch:
        if w not in watched_ranks:
            watched_ranks[w] = None
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sort_by": config.COINDESK_ASSET_TOP_LIST_SORT_BY,
        "page_size": config.COINDESK_ASSET_TOP_LIST_PAGE_SIZE,
        "row_count": len(rows),
        "top_rows": top_rows[:lim],
        "watched_ranks": watched_ranks,
        "watch_symbols": sorted(watch),
    }


def asset_top_list_snapshot_path() -> Path:
    return Path(config.ARTIFACTS_DIR).resolve() / "coindesk" / "asset_top_list_snapshot.json"


def refresh_asset_top_list_snapshot(
    client: CoinDeskClient | None = None,
) -> dict[str, Any]:
    """Fetch top list and write ``artifacts/.../asset_top_list_snapshot.json``."""
    if not config.COINDESK_API_KEY.strip():
        return {"ok": False, "error": "no_api_key"}
    if not config.COINDESK_ASSET_TOP_LIST_ENABLED:
        return {"ok": False, "error": "disabled"}
    try:
        payload = fetch_asset_top_list(client)
        snap = build_top_list_snapshot_from_payload(payload)
        path = asset_top_list_snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
        return {"ok": True, "snapshot": snap, "path": str(path)}
    except Exception as e:
        logger.warning("CoinDesk asset top list snapshot failed: %s", e)
        return {"ok": False, "error": str(e)}


def read_asset_top_list_snapshot() -> dict[str, Any]:
    """Read cached snapshot from disk (written by scheduler or manual refresh)."""
    path = asset_top_list_snapshot_path()
    if not path.is_file():
        return {"available": False}
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
        return {"available": True, "snapshot": snap, "path": str(path)}
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        return {"available": False, "error": str(e)}

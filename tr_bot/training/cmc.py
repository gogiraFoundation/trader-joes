"""CoinMarketCap historical OHLCV (Pro API v2) — id/slug preferred over symbol."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tr_bot.analysis.cmc_client import CMC_PRO_BASE, cmc_pro_get

logger = logging.getLogger("tr_bot.training.cmc")

CMC_OHLCV_URL = f"{CMC_PRO_BASE}/v2/cryptocurrency/ohlcv/historical"
CMC_MAP_URL = f"{CMC_PRO_BASE}/v1/cryptocurrency/map"

_map_cache: dict[str, dict[str, int]] = {}


def clear_cmc_map_cache() -> None:
    """Test helper: reset in-process symbol->id map cache."""
    _map_cache.clear()


def _cmc_time_period(interval: str) -> str:
    """
    Map training timeframe strings to CMC `time_period` (see Pro API OHLCV historical).
    """
    m = {
        "1h": "1h",
        "4h": "4h",
        "1d": "daily",
        "1D": "daily",
        "daily": "daily",
    }
    return m.get(interval, "daily")


def resolve_train_symbols_to_ids(symbols: list[str], api_key: str) -> dict[str, int]:
    """
    One-shot /v1/cryptocurrency/map lookup: uppercase symbol -> CMC numeric id.
    Cached in-process per sorted symbol tuple to avoid duplicate map calls in one run.
    """
    norm = [s.upper().strip() for s in symbols if s.strip()]
    if not norm:
        return {}
    cache_key = ",".join(sorted(norm))
    if cache_key in _map_cache:
        return dict(_map_cache[cache_key])

    r = cmc_pro_get(
        CMC_MAP_URL,
        api_key=api_key,
        params={"symbol": ",".join(norm)},
        timeout=60.0,
    )
    if r.status_code != 200:
        logger.error("CMC map HTTP %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()

    payload = r.json()
    st = payload.get("status") or {}
    if st.get("error_code") not in (None, 0):
        raise RuntimeError(f"CMC map API error: {st}")

    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("CMC map: unexpected data payload")

    out: dict[str, int] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper().strip()
        cid = row.get("id")
        if sym and cid is not None:
            try:
                out[sym] = int(cid)
            except (TypeError, ValueError):
                continue

    missing = set(norm) - set(out)
    if missing:
        logger.warning("CMC map: no id returned for symbols: %s", sorted(missing))

    _map_cache[cache_key] = dict(out)
    return out


def _identity_params(
    *,
    cmc_id: int | None,
    slug: str | None,
    symbol: str | None,
) -> tuple[dict[str, Any], str]:
    """Build query fragment for exactly one identity; returns (params_fragment, asset_label)."""
    if cmc_id is not None:
        return {"id": str(int(cmc_id))}, str(int(cmc_id))
    if slug and slug.strip():
        s = slug.strip().lower()
        return {"slug": s}, s
    if symbol and symbol.strip():
        sym = symbol.upper().strip()
        return {"symbol": sym}, sym
    raise ValueError("CoinMarketCap OHLCV requires cmc_id, slug, or symbol")


def fetch_cmc_ohlcv(
    *,
    convert: str,
    interval: str,
    count: int,
    api_key: str,
    cmc_id: int | None = None,
    slug: str | None = None,
    symbol: str | None = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV from CoinMarketCap Pro API (v2 historical).
    Prefer ``cmc_id`` or ``slug`` over ``symbol`` to avoid ambiguous tickers.
    """
    id_params, asset_label = _identity_params(cmc_id=cmc_id, slug=slug, symbol=symbol)
    cu = (convert or "USD").strip().upper() or "USD"
    params: dict[str, Any] = {
        **id_params,
        "convert": cu,
        "time_period": _cmc_time_period(interval),
        "count": min(max(int(count), 1), 10_000),
    }

    r = cmc_pro_get(CMC_OHLCV_URL, api_key=api_key, params=params, timeout=60.0)
    if r.status_code != 200:
        logger.error("CMC OHLCV HTTP %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()

    payload = r.json()
    st = payload.get("status") or {}
    code = st.get("error_code")
    if code not in (None, 0):
        raise RuntimeError(f"CMC API error: {st}")

    data = payload.get("data")
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError(f"CMC: unexpected data payload: {type(data)}")
    quotes = data.get("quotes")
    if not quotes:
        raise RuntimeError(f"CMC: no quotes in response for identity {id_params!r}")

    rows: list[dict[str, Any]] = []
    for q in quotes:
        quote = (q.get("quote") or {}).get(cu) or (q.get("quote") or {}).get(convert)
        if not quote:
            for v in (q.get("quote") or {}).values():
                if isinstance(v, dict) and "open" in v:
                    quote = v
                    break
        if not quote:
            continue
        t_open = q.get("time_open") or quote.get("timestamp")
        if t_open is None:
            continue
        ts = pd.Timestamp(t_open)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        sec = int(ts.timestamp())
        rows.append(
            {
                "timestamp": sec,
                "open": float(quote["open"]),
                "high": float(quote["high"]),
                "low": float(quote["low"]),
                "close": float(quote["close"]),
                "volume": float(quote.get("volume") or 0.0),
            }
        )

    if not rows:
        raise RuntimeError(f"CMC: could not parse OHLCV rows for identity {id_params!r}")

    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
    df["source"] = "coinmarketcap"
    df["asset"] = asset_label
    return df.reset_index(drop=True)


def fetch_cmc_training(
    *,
    interval: str,
    count: int,
    api_key: str,
    convert: str = "USD",
    cmc_id: int | None = None,
    slug: str | None = None,
    symbol: str | None = None,
) -> pd.DataFrame:
    return fetch_cmc_ohlcv(
        convert=convert,
        interval=interval,
        count=count,
        api_key=api_key,
        cmc_id=cmc_id,
        slug=slug,
        symbol=symbol,
    )

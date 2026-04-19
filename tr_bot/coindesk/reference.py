"""CoinDesk index reference data: markets metadata, mapped instruments, optional EOD day bars."""

from __future__ import annotations

import logging
import time
from typing import Any

from tr_bot import config
from tr_bot.coindesk.client import CoinDeskClient

logger = logging.getLogger("tr_bot.coindesk.reference")

_cache: dict[str, tuple[float, Any]] = {}


def _cache_ttl_s() -> float:
    return max(0.0, float(config.COINDESK_REFERENCE_CACHE_TTL_S))


def _cache_get(key: str) -> Any | None:
    ttl = _cache_ttl_s()
    if ttl <= 0:
        return None
    hit = _cache.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.monotonic() - ts > ttl:
        del _cache[key]
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    if _cache_ttl_s() <= 0:
        return
    _cache[key] = (time.monotonic(), val)


def _normalize_markets_param(markets: str) -> str:
    return ",".join(x.strip() for x in markets.split(",") if x.strip())


def _path(p: str) -> str:
    p = p.strip()
    return p if p.startswith("/") else f"/{p}"


def fetch_index_markets(
    client: CoinDeskClient | None = None,
    *,
    markets: str | None = None,
) -> dict[str, Any]:
    """GET /index/cc/v2/markets — index family metadata (status, counts)."""
    c = client or CoinDeskClient()
    m = _normalize_markets_param(markets or config.COINDESK_DEFAULT_MARKET)
    key = f"markets:{m}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    path = _path(config.coindesk_effective_index_markets_path())
    payload = c.get(path, {"markets": m}, endpoint_metric="index_markets_v2")
    _cache_set(key, payload)
    return payload


def _symbol_from_instrument_row(row: dict[str, Any]) -> str | None:
    for k in (
        "MAPPED_INSTRUMENT",
        "MappedInstrument",
        "instrument",
        "INSTRUMENT",
        "Instrument",
        "symbol",
        "Symbol",
        "SYMBOL",
        "NAME",
        "Name",
    ):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _normalize_instrument_sym(s: str) -> str:
    """Uppercase and unify `-` / `_` / `/` so config matches API naming (ccix / index)."""
    t = s.strip().upper().replace("_", "-").replace("/", "-")
    while "--" in t:
        t = t.replace("--", "-")
    return t


def _chunk_size() -> int:
    return max(1, int(config.COINDESK_MAPPED_INSTRUMENTS_CHUNK))


def _is_likely_instrument_row(v: Any) -> bool:
    if not isinstance(v, dict) or not v:
        return False
    if _symbol_from_instrument_row(v) is not None:
        return True
    for k in v:
        ku = str(k).upper()
        if ku in ("INSTRUMENT_STATUS", "MAPPED_INSTRUMENT", "INSTRUMENT_ID", "STATUS"):
            return True
    return False


# Keys that wrap the real instrument map but are not instrument ids themselves.
_WRAPPER_KEY_LOWER: frozenset[str] = frozenset(
    {
        "data",
        "instruments",
        "mapped_instruments",
        "instrument",
        "result",
        "results",
        "payload",
        "body",
        "response",
        "metadata",
        "meta",
        "message",
    }
)


def _keys_are_only_wrappers(d: dict[str, Any]) -> bool:
    if not d:
        return False
    return all(str(k).strip().lower() in _WRAPPER_KEY_LOWER for k in d)


def _peel_wrapper_shells(d: dict[str, Any], market: str, *, max_steps: int = 8) -> dict[str, Any]:
    """Strip ``data`` / ``instruments``-style single-key shells until we hit substance or max depth."""
    cur = dict(d)
    mk = market.strip().lower()
    for _ in range(max_steps):
        cur = _merge_instrument_wrapper_keys(cur, market)
        if len(cur) != 1:
            break
        sole_k, sole_v = next(iter(cur.items()))
        if not isinstance(sole_v, dict) or not sole_v:
            break
        sk = str(sole_k).strip().lower()
        if sk in _WRAPPER_KEY_LOWER or sk == mk:
            cur = dict(sole_v)
            continue
        break
    return cur


def _unwrap_market_nested_instrument_map(data: dict[str, Any], market: str) -> dict[str, Any]:
    """
    Some index responses nest ``{ "<market>": { "<instrument>": { ... row ... } } }`` instead of a flat map.
    Normalize to ``instrument -> row`` so validation and chunk merging behave consistently.

    Also handles: inner ``instruments`` as a list, scalar metadata next to rows, and extra shells
    like ``{ "data": { ... } }`` under the market key (where every value is a dict but keys are
    not instrument ids).
    """
    if not data:
        return data
    mk = market.strip().lower()

    def _materialize_inner(v: dict[str, Any]) -> dict[str, Any] | None:
        if not v:
            return None
        v2 = _peel_wrapper_shells(dict(v), mk)
        if not v2:
            return None
        dict_vals = [x for x in v2.values() if isinstance(x, dict)]
        if not dict_vals:
            return None
        if all(isinstance(x, dict) for x in v2.values()) and not _keys_are_only_wrappers(v2):
            return dict(v2)
        if all(isinstance(x, dict) for x in v2.values()) and _keys_are_only_wrappers(v2):
            merged_wr: dict[str, Any] = {}
            for iv in dict_vals:
                sub = _materialize_inner(iv)
                if sub:
                    merged_wr.update(sub)
            return merged_wr if merged_wr else None
        out: dict[str, Any] = {}
        for ik, iv in v2.items():
            if isinstance(iv, dict) and _is_likely_instrument_row(iv):
                out[str(ik)] = iv
        for pk in ("Data", "data"):
            node = v2.get(pk)
            if isinstance(node, dict) and node:
                sub = _materialize_inner(node)
                if sub:
                    out.update(sub)
        return out or None

    nested: dict[str, Any] | None = None
    if len(data) == 1:
        k, v = next(iter(data.items()))
        if isinstance(k, str) and k.strip().lower() == mk and isinstance(v, dict) and v:
            nested = _materialize_inner(v)
    if nested is None:
        for k, v in data.items():
            if isinstance(k, str) and k.strip().lower() == mk and isinstance(v, dict) and v:
                nested = _materialize_inner(v)
                break
    if nested is None:
        return data
    out = dict(nested)
    for k, v in data.items():
        if isinstance(k, str) and k.strip().lower() == mk:
            continue
        if isinstance(v, dict) and _is_likely_instrument_row(v):
            out[str(k)] = v
    return out


def _merge_instrument_wrapper_keys(data: dict[str, Any], market: str) -> dict[str, Any]:
    """Some payloads nest under ``instruments`` / ``mapped_instruments`` instead of a flat map."""
    d = dict(data)
    for w in ("instruments", "Instruments", "INSTRUMENTS", "mapped_instruments", "MappedInstruments"):
        inner = d.pop(w, None)
        if isinstance(inner, dict):
            inner = _unwrap_market_nested_instrument_map(inner, market)
            for ik, iv in inner.items():
                if isinstance(ik, str) and isinstance(iv, dict):
                    d[ik] = iv
        elif isinstance(inner, list):
            for item in inner:
                if isinstance(item, dict):
                    sym = _symbol_from_instrument_row(item)
                    if sym:
                        d[sym] = item
    return d


def _enrich_symbol_index_from_rows(data: dict[str, Any]) -> None:
    """When top-level keys are opaque IDs, rows still carry ``MAPPED_INSTRUMENT`` / ``instrument`` — index by those."""
    for v in list(data.values()):
        if not isinstance(v, dict):
            continue
        sym = _symbol_from_instrument_row(v)
        if sym:
            data.setdefault(sym, v)


def _deep_merge_symbols_from_nested_maps(data: dict[str, Any]) -> None:
    """
    Walk nested dict/list structures and surface any instrument rows by ``MAPPED_INSTRUMENT`` /
    ``instrument`` so opaque trees still validate after shallow unwrap.
    """
    acc: dict[str, Any] = {}

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if _is_likely_instrument_row(obj):
                sym = _symbol_from_instrument_row(obj)
                if sym:
                    acc.setdefault(sym, obj)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    for sym, row in acc.items():
        data.setdefault(sym, row)


def _normalize_mapped_instrument_dict(data: dict[str, Any], market: str) -> dict[str, Any]:
    """Unwrap market nesting, ``instruments`` wrappers, and symbol fields inside rows."""
    if not data:
        return data
    d = _merge_instrument_wrapper_keys(data, market)
    d = _unwrap_market_nested_instrument_map(d, market)
    _enrich_symbol_index_from_rows(d)
    _deep_merge_symbols_from_nested_maps(d)
    return d


def fetch_mapped_instruments(
    client: CoinDeskClient | None = None,
    *,
    market: str | None = None,
    instruments: list[str],
    instrument_status: str = "ACTIVE",
    groups: str | None = None,
) -> dict[str, Any]:
    """
    Mapped instruments for a market (index: default ``/index/cc/v1/markets/instruments``;
    spot exchange slug: ``/spot/v1/markets/instruments`` — see ``coindesk_effective_mapped_instruments_path``).

    Requests are split into chunks of up to COINDESK_MAPPED_INSTRUMENTS_CHUNK (default 25).
    """
    c = client or CoinDeskClient()
    mkt = (market or config.COINDESK_DEFAULT_MARKET).strip()
    cleaned = [x.strip() for x in instruments if x.strip()]
    if not cleaned:
        return {"Data": {}}
    key = f"mapped:{mkt}:{','.join(sorted(cleaned))}:{instrument_status}:{groups or ''}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    path = _path(config.coindesk_effective_mapped_instruments_path())
    merged: dict[str, Any] = {}
    chunk = _chunk_size()
    for i in range(0, len(cleaned), chunk):
        part_syms = cleaned[i : i + chunk]
        params: dict[str, Any] = {
            "market": mkt,
            "instruments": ",".join(part_syms),
            "instrument_status": instrument_status,
        }
        if groups:
            params["groups"] = groups
        part = c.get(path, params, endpoint_metric="mapped_instruments")
        data = part.get("Data") or part.get("data")
        if isinstance(data, dict):
            flat = _normalize_mapped_instrument_dict(data, mkt)
            merged.update(flat)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    sym = _symbol_from_instrument_row(item)
                    if sym:
                        merged[sym] = item
    out: dict[str, Any] = {"Data": merged}
    _cache_set(key, out)
    return out


def _extract_market_row(markets_payload: dict[str, Any], market: str) -> dict[str, Any] | None:
    data = markets_payload.get("Data") or markets_payload.get("data")
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            name = row.get("MARKET") or row.get("market") or row.get("name") or row.get("NAME")
            if name is not None and str(name).strip().lower() == market.strip().lower():
                return row
    elif isinstance(data, dict):
        row = data.get(market) or data.get(market.upper())
        if isinstance(row, dict):
            return row
        for k, v in data.items():
            if isinstance(k, str) and k.strip().lower() == market.strip().lower() and isinstance(v, dict):
                return v
    return None


def _instrument_status_from_row(row: dict[str, Any]) -> str | None:
    for k in ("INSTRUMENT_STATUS", "instrument_status", "STATUS", "status"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    return None


def _market_family_status_from_row(row: dict[str, Any] | None) -> str | None:
    if not isinstance(row, dict):
        return None
    for k in ("STATUS", "status", "MARKET_STATUS", "market_status"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    return None


def _lookup_instrument_row(data: dict[str, Any], sym: str) -> dict[str, Any] | None:
    norm_want = _normalize_instrument_sym(sym)
    if sym in data:
        r = data[sym]
        return r if isinstance(r, dict) else None
    su = sym.strip().upper()
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        ku = k.strip().upper()
        if ku == su or _normalize_instrument_sym(k) == norm_want:
            return v
    for v in data.values():
        if not isinstance(v, dict):
            continue
        mapped = _symbol_from_instrument_row(v)
        if mapped and _normalize_instrument_sym(mapped) == norm_want:
            return v
    return None


def validate_configured_instruments(
    client: CoinDeskClient | None = None,
    *,
    market: str | None = None,
    instruments: list[str] | None = None,
    instrument_status: str = "ACTIVE",
) -> dict[str, Any]:
    """
    Compare COINDESK_INSTRUMENTS (or ``instruments``) to mapped-instrument API data.

    Returns ``ok`` when every configured symbol is present and ACTIVE (when status is present).
    """
    mkt = (market or config.COINDESK_DEFAULT_MARKET).strip()
    want = [x.strip() for x in (instruments or [x.strip() for x in config.COINDESK_INSTRUMENTS.split(",") if x.strip()])]
    if not want:
        return {
            "ok": True,
            "issues": [],
            "market": mkt,
            "market_row": None,
            "market_family_status": None,
            "instruments_found": [],
            "instruments_missing": [],
            "mapped_instrument_keys_sample": [],
        }
    c = client or CoinDeskClient()
    markets_payload = fetch_index_markets(c, markets=mkt)
    mr = _extract_market_row(markets_payload, mkt)
    mapped = fetch_mapped_instruments(c, market=mkt, instruments=want, instrument_status=instrument_status)
    data = mapped.get("Data") or mapped.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    data = _normalize_mapped_instrument_dict(data, mkt)
    issues: list[str] = []
    found: list[str] = []
    missing: list[str] = []
    mfs = _market_family_status_from_row(mr)
    bad_family = {"RETIRED", "IGNORED", "DISABLED", "INACTIVE"}
    if mfs and mfs in bad_family:
        issues.append(f"market_family:{mkt}:{mfs}")

    for sym in want:
        row = _lookup_instrument_row(data, sym)
        if row is None:
            missing.append(sym)
            issues.append(f"missing:{sym}")
            continue
        found.append(sym)
        st = _instrument_status_from_row(row)
        if st and st != "ACTIVE":
            issues.append(f"status:{sym}:{st}")

    if missing:
        logger.debug(
            "CoinDesk reference mapped instrument keys (sample, market=%s): %s",
            mkt,
            list(data.keys())[:25],
        )

    ok = len(issues) == 0
    keys_sample = [str(k) for k in list(data.keys())[:25]]
    return {
        "ok": ok,
        "issues": issues,
        "market": mkt,
        "market_row": mr,
        "market_family_status": mfs,
        "instruments_found": found,
        "instruments_missing": missing,
        "mapped_instrument_keys_sample": keys_sample if not ok else [],
    }


def coindesk_reference_ready_snapshot() -> dict[str, Any]:
    """Compact payload for /ready (informational; does not gate Redis/Postgres)."""
    if not config.COINDESK_REFERENCE_CHECK_ENABLED or not bool(config.COINDESK_API_KEY.strip()):
        return {"skipped": True, "reason": "disabled_or_no_api_key"}
    try:
        v = validate_configured_instruments()
        return {
            "skipped": False,
            "market": v.get("market"),
            "instruments_ok": v.get("ok"),
            "issues": v.get("issues") or [],
            "market_family_status": v.get("market_family_status"),
            "instruments_missing": v.get("instruments_missing") or [],
        }
    except Exception as e:
        cfg_file = str(getattr(config, "__file__", "") or "")
        dist_ver: str | None = None
        try:
            from importlib.metadata import version

            dist_ver = version("tr_bot")
        except Exception:
            pass
        extra = f" config_module={cfg_file!s}"
        if dist_ver:
            extra += f" tr_bot_distribution_version={dist_ver}"
        logger.warning("CoinDesk reference snapshot failed: %s%s", e, extra)
        return {
            "skipped": False,
            "error": str(e),
            "config_module": cfg_file or None,
            "tr_bot_distribution_version": dist_ver,
        }


def fetch_eod_day_bars(
    client: CoinDeskClient | None = None,
    *,
    market: str,
    instrument: str,
    limit: int = 30,
    to_ts: str | None = None,
    fill: bool | None = None,
    apply_mapping: bool | None = None,
    response_format: str | None = None,
) -> dict[str, Any]:
    """
    GET .../historical/days/eod — EOD index family bars (e.g. cdifti-style markets and BTIUSF-USD).

    This is **not** the same product as cadli daily OHLC from ``default_historical_ohlcv_path`` and
    ``build_historical_ohlcv_params`` (generic index historical days). Use distinct column names
    when merging into features or P&L attribution.
    """
    c = client or CoinDeskClient()
    lim = max(1, min(int(limit), 50))
    params: dict[str, Any] = {
        "market": market,
        "instrument": instrument,
        "limit": lim,
        "fill": str(fill if fill is not None else config.COINDESK_HISTORICAL_FILL).lower(),
        "apply_mapping": str(
            apply_mapping if apply_mapping is not None else config.COINDESK_APPLY_MAPPING
        ).lower(),
        "response_format": (response_format or config.COINDESK_HISTORICAL_RESPONSE_FORMAT).strip() or "JSON",
    }
    if to_ts:
        params["to_ts"] = to_ts
    path = _path(config.COINDESK_EOD_DAYS_PATH)
    return c.get(path, params, endpoint_metric="eod_days")

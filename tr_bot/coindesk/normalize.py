"""Map CoinDesk API JSON to unified rows (ticks, OHLCV, order books)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("tr_bot.coindesk.normalize")


def _ts_ms_from_iso_string(s: str) -> int | None:
    raw = s.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _ts_ms_from_value(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        x = int(v)
        if x > 1_000_000_000_000:  # already ms (epoch ms ~1.7e12 in 2020s)
            return x
        if x > 1_000_000_000:  # unix seconds
            return x * 1000
        return None
    if isinstance(v, str):
        try:
            return _ts_ms_from_value(float(v))
        except ValueError:
            return _ts_ms_from_iso_string(v)
    return None


def _float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _walk_tick_objects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect instrument tick dicts from various response shapes."""
    out: list[dict[str, Any]] = []
    data = payload.get("Data") or payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                out.append(item)
        return out
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                merged = dict(v)
                if isinstance(k, str) and k.strip() and not merged.get("instrument") and not merged.get(
                    "symbol"
                ):
                    # e.g. Data: { "BTC-USD": { "PRICE": ..., "CC": {...} } }
                    if "-" in k or "/" in k:
                        merged["instrument"] = k.replace("/", "-")
                out.append(merged)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        out.append(item)
    raw = payload.get("RAW") or payload.get("Raw")
    if isinstance(raw, dict):
        for _k, v in raw.items():
            if isinstance(v, dict):
                out.append(v)
    if not out and isinstance(payload.get("instrument"), str):
        out.append(payload)
    return out


_TS_KEY_CANDIDATES = (
    "timestamp",
    "Timestamp",
    "TIMESTAMP",
    "time",
    "Time",
    "TIME",
    "TS",
    "ts",
    "LASTUPDATE",
    "LAST_UPDATE",
    "LastUpdate",
    "lastUpdate",
    "lastupdate",
    "last_update",
    "UPDATEDON",
    "UpdatedOn",
    "updated_on",
    "LASTTRADETIME",
    "LAST_TRADE_TIME",
    "last_trade_time",
    "RECEIVED_TIME",
    "received_time",
    "EVENT_TIME",
    "event_time",
    "SERVER_TIME",
    "server_time",
)


def _ts_ms_from_ns(v: Any) -> int | None:
    if not isinstance(v, (int, float)):
        return None
    ns = int(v)
    if ns > 10**16:  # clearly ns since epoch
        return ns // 1_000_000
    if ns > 10**13:  # micros → ms
        return ns // 1000
    return None


def _ts_ms_from_seconds_plus_ns_part(obj: dict[str, Any], sec_key: str, ns_key: str) -> int | None:
    """
    CoinDesk Spot tick: TS fields are unix seconds; *_TS_NS is the fractional nanoseconds part.
    See PRICE_LAST_UPDATE_TS + PRICE_LAST_UPDATE_TS_NS in API schema.
    """
    sec = _int_or_none(obj.get(sec_key))
    if sec is None:
        return None
    ns_part = _int_or_none(obj.get(ns_key)) or 0
    return sec * 1000 + max(0, ns_part) // 1_000_000


def _extract_coindesk_spot_tick_ts_ms(obj: dict[str, Any]) -> int | None:
    """Official Spot Latest Tick time fields (preferred order)."""
    pairs = (
        ("VALUE_LAST_UPDATE_TS", "VALUE_LAST_UPDATE_TS_NS"),
        ("PRICE_LAST_UPDATE_TS", "PRICE_LAST_UPDATE_TS_NS"),
        ("LAST_TRADE_RECEIVED_TS", "LAST_TRADE_RECEIVED_TS_NS"),
        ("LAST_PROCESSED_TRADE_TS", "LAST_PROCESSED_TRADE_TS_NS"),
        ("LAST_PROCESSED_TRADE_RECEIVED_TS", "LAST_PROCESSED_TRADE_RECEIVED_TS_NS"),
        ("BEST_BID_LAST_UPDATE_TS", "BEST_BID_LAST_UPDATE_TS_NS"),
        ("BEST_ASK_LAST_UPDATE_TS", "BEST_ASK_LAST_UPDATE_TS_NS"),
    )
    for sk, nk in pairs:
        t = _ts_ms_from_seconds_plus_ns_part(obj, sk, nk)
        if t is not None:
            return t
    return None


def _extract_tick_timestamp_ms(obj: dict[str, Any], *, _depth: int = 0) -> int | None:
    if _depth > 3:
        return None
    t0 = _extract_coindesk_spot_tick_ts_ms(obj)
    if t0 is not None:
        return t0
    for k in _TS_KEY_CANDIDATES:
        t = _ts_ms_from_value(obj.get(k))
        if t is not None:
            return t
    for nk in ("LAST_UPDATE_NS", "LASTUPDATE_NS", "TIMESTAMP_NS", "timestamp_ns", "TIME_NS"):
        t = _ts_ms_from_ns(obj.get(nk))
        if t is not None:
            return t
    for nest_key in ("CC", "cc", "RAW", "raw", "Tick", "tick", "VALUE", "value", "quote", "Quote"):
        nested = obj.get(nest_key)
        if isinstance(nested, dict):
            inner = _extract_tick_timestamp_ms(nested, _depth=_depth + 1)
            if inner is not None:
                return inner
    for k, v in obj.items():
        if not isinstance(k, str):
            continue
        kl = k.lower()
        if kl in ("updatetype", "type", "market", "instrument", "symbol"):
            continue
        if "update" in kl or kl.endswith("_at") or kl.endswith("_time") or kl.endswith("_ts"):
            t = _ts_ms_from_value(v)
            if t is not None:
                return t
    return None


def _extract_tick_price(obj: dict[str, Any], *, _depth: int = 0) -> float | None:
    if _depth > 3:
        return None
    for k in (
        "price",
        "PRICE",
        "VALUE",
        "close",
        "CLOSE",
        "last",
        "LAST",
        "Last",
        "LASTTRADE",
        "LASTTRADEPRICE",
        "lasttradeprice",
        "LAST_PRICE",
        "last_price",
        "MID",
        "mid",
        "LAST_PROCESSED_TRADE_PRICE",
        "last_processed_trade_price",
    ):
        p = _float_or_none(obj.get(k))
        if p is not None and p > 0:
            return p
    for nest_key in ("CC", "cc", "RAW", "raw", "Tick", "tick", "VALUE", "value", "quote", "Quote"):
        nested = obj.get(nest_key)
        if isinstance(nested, dict):
            p = _extract_tick_price(nested, _depth=_depth + 1)
            if p is not None:
                return p
    return None


def _extract_tick_volume(obj: dict[str, Any], *, _depth: int = 0) -> float:
    if _depth > 3:
        return 0.0
    for k in (
        "volume",
        "VOLUME",
        "last_volume",
        "LASTVOLUME",
        "LASTVOLUMETO",
        "LASTVOLUMEFROM",
        "LAST_TRADE_QUANTITY",
        "last_trade_quantity",
        "VOLUMEHOUR",
        "VOLUMEDAY",
    ):
        v = _float_or_none(obj.get(k))
        if v is not None:
            return float(v)
    for nest_key in ("CC", "cc", "RAW", "raw", "Tick", "tick"):
        nested = obj.get(nest_key)
        if isinstance(nested, dict):
            return _extract_tick_volume(nested, _depth=_depth + 1)
    return 0.0


def parse_latest_ticks(
    payload: dict[str, Any],
    *,
    market: str,
    default_instrument: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for obj in _walk_tick_objects(payload):
        inst = (
            obj.get("MAPPED_INSTRUMENT")
            or obj.get("mapped_instrument")
            or obj.get("instrument")
            or obj.get("INSTRUMENT")
            or obj.get("symbol")
            or obj.get("Symbol")
            or default_instrument
            or ""
        )
        if not isinstance(inst, str):
            inst = str(inst)
        if not inst.strip():
            fs = obj.get("FROMSYMBOL") or obj.get("from_symbol") or obj.get("BASE")
            ts_sym = obj.get("TOSYMBOL") or obj.get("to_symbol") or obj.get("QUOTE")
            if fs and ts_sym:
                inst = f"{fs}-{ts_sym}"
            elif fs:
                inst = str(fs)

        ts = _extract_tick_timestamp_ms(obj)
        price = _extract_tick_price(obj)
        vol = _extract_tick_volume(obj)

        if ts is None and price is not None:
            ts = int(datetime.now(timezone.utc).timestamp() * 1000)
            logger.debug(
                "CoinDesk tick %s: no API timestamp field; using server ingest time (ms=%s)",
                inst or "?",
                ts,
            )
        if ts is None:
            logger.warning("CoinDesk tick missing timestamp for %s; skipping", inst or "?")
            continue
        ccseq = _int_or_none(
            obj.get("CCSEQ") or obj.get("ccseq") or obj.get("LAST_TRADE_CCSEQ") or obj.get("seq")
        )
        rows.append(
            {
                "timestamp_ms": ts,
                "market": market,
                "instrument": inst,
                "price": price,
                "volume": vol,
                "ccseq": ccseq,
                "source": "coindesk",
                "raw": obj,
            }
        )
    return rows


def _candle_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("Data") or payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        inner = data.get("Data") or data.get("data")
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
    return []


def parse_ohlcv_payload(
    payload: dict[str, Any],
    *,
    market: str,
    instrument: str,
    timeframe: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in _candle_list(payload):
        ts = (
            _ts_ms_from_value(c.get("time"))
            or _ts_ms_from_value(c.get("timestamp"))
            or _ts_ms_from_value(c.get("TIMESTAMP"))
        )
        if ts is None:
            continue
        o = _float_or_none(c.get("open") or c.get("OPEN"))
        h = _float_or_none(c.get("high") or c.get("HIGH"))
        low_v = _float_or_none(c.get("low") or c.get("LOW"))
        cl = _float_or_none(c.get("close") or c.get("CLOSE"))
        vol = (
            _float_or_none(c.get("volumeto"))
            or _float_or_none(c.get("volumefrom"))
            or _float_or_none(c.get("volume"))
            or _float_or_none(c.get("VOLUME"))
            or 0.0
        )
        ccseq = _int_or_none(c.get("ccseq") or c.get("CCSEQ"))
        if o is None or h is None or low_v is None or cl is None:
            continue
        rows.append(
            {
                "timestamp_ms": ts,
                "open": o,
                "high": h,
                "low": low_v,
                "close": cl,
                "volume": vol,
                "market": market,
                "instrument": instrument,
                "timeframe": timeframe,
                "ccseq": ccseq,
                "source": "coindesk",
            }
        )
    rows.sort(key=lambda r: r["timestamp_ms"])
    return rows


def parse_orderbook_payload(
    payload: dict[str, Any],
    *,
    market: str,
    instrument: str,
    depth: int | None = None,
) -> dict[str, Any] | None:
    data = payload.get("Data") or payload.get("data") or payload
    if not isinstance(data, dict):
        return None
    bids_raw = data.get("bids") or data.get("Bids") or data.get("buy")
    asks_raw = data.get("asks") or data.get("Asks") or data.get("sell")
    ts = (
        _ts_ms_from_value(data.get("timestamp"))
        or _ts_ms_from_value(data.get("LASTUPDATE"))
        or _ts_ms_from_value(data.get("time"))
    )
    if ts is None:
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    def _side(rows: Any) -> list[list[float]]:
        out: list[list[float]] = []
        if not isinstance(rows, list):
            return out
        for row in rows:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                p, s = _float_or_none(row[0]), _float_or_none(row[1])
                if p is not None and s is not None:
                    out.append([p, s])
            elif isinstance(row, dict):
                p = _float_or_none(row.get("price") or row.get("PRICE"))
                s = _float_or_none(row.get("quantity") or row.get("size") or row.get("amount"))
                if p is not None and s is not None:
                    out.append([p, s])
        if depth is not None:
            out = out[: max(0, int(depth))]
        return out

    bids = _side(bids_raw)
    asks = _side(asks_raw)
    return {
        "timestamp_ms": ts,
        "market": market,
        "instrument": instrument,
        "bids": bids,
        "asks": asks,
        "depth": depth,
        "source": "coindesk",
    }


def parse_instrument_metadata(payload: dict[str, Any], *, market: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    data = payload.get("Data") or payload.get("data") or payload.get("instruments")
    items: list[Any]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = list(data.values())
    else:
        items = []
    for obj in items:
        if not isinstance(obj, dict):
            continue
        iid = obj.get("instrument_id") or obj.get("id") or obj.get("InstrumentId")
        sym = obj.get("symbol") or obj.get("Symbol") or obj.get("instrument") or ""
        base = obj.get("base") or obj.get("BASE") or obj.get("base_asset")
        quote = obj.get("quote") or obj.get("QUOTE") or obj.get("quote_asset")
        typ = obj.get("instrument_type") or obj.get("type") or obj.get("TYPE")
        rows.append(
            {
                "instrument_id": str(iid) if iid is not None else None,
                "symbol": str(sym) if sym else None,
                "base_asset": str(base) if base else None,
                "quote_asset": str(quote) if quote else None,
                "market": market,
                "instrument_type": str(typ) if typ else None,
                "raw": obj,
            }
        )
    return rows

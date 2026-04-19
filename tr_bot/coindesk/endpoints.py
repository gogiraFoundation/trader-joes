"""High-level CoinDesk API calls (paths configurable via tr_bot.config)."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tr_bot import config
from tr_bot.coindesk.client import CoinDeskClient
from tr_bot.coindesk.normalize import (
    parse_instrument_metadata,
    parse_latest_ticks,
    parse_ohlcv_payload,
    parse_orderbook_payload,
)

logger = logging.getLogger("tr_bot.coindesk.endpoints")

# Map timeframe -> (URL segment, aggregate). Segments must be plural per CoinDesk Data API
# (e.g. /index/cc/v1/historical/days — not .../historical/day).
_TIMEFRAME_HIST: dict[str, tuple[str, int]] = {
    "1m": ("minutes", 1),
    "3m": ("minutes", 3),
    "5m": ("minutes", 5),
    "15m": ("minutes", 15),
    "30m": ("minutes", 30),
    "1h": ("hours", 1),
    "4h": ("hours", 4),
    "1d": ("days", 1),
    "1D": ("days", 1),
    "daily": ("days", 1),
}


def _default_ohlcv_path(timeframe: str) -> str:
    suffix, _agg = _TIMEFRAME_HIST.get(timeframe, ("days", 1))
    return f"/index/cc/v1/historical/{suffix}"


def default_historical_ohlcv_path(timeframe: str) -> str:
    """Public helper for backfill / custom clients."""
    return _default_ohlcv_path(timeframe)


def build_historical_ohlcv_params(
    market: str,
    instrument: str,
    timeframe: str,
    limit: int,
    to_ts: str | None,
) -> dict[str, Any]:
    _, agg = _TIMEFRAME_HIST.get(timeframe, ("days", 1))
    # Index CC historical: `instrument` + `groups` (see CoinDesk index_cc_v1_historical_* docs).
    params: dict[str, Any] = {
        "market": market,
        "instrument": instrument,
        "limit": max(1, min(int(limit), 5000)),
        "aggregate": agg,
        "fill": str(config.COINDESK_HISTORICAL_FILL).lower(),
        "apply_mapping": str(config.COINDESK_APPLY_MAPPING).lower(),
        "response_format": config.COINDESK_HISTORICAL_RESPONSE_FORMAT,
    }
    grp = config.COINDESK_OHLCV_GROUPS.strip()
    if grp:
        params["groups"] = grp
    if to_ts:
        params["to_ts"] = to_ts
    return params


def fetch_latest_ticks(
    client: CoinDeskClient | None = None,
    *,
    market: str | None = None,
    instruments: str | None = None,
) -> list[dict[str, Any]]:
    c = client or CoinDeskClient()
    mkt = market or config.COINDESK_DEFAULT_MARKET
    inst = instruments or config.COINDESK_INSTRUMENTS
    path = config.COINDESK_LATEST_TICK_PATH
    params: dict[str, Any] = {
        "market": mkt,
        "instruments": inst,
        "apply_mapping": str(config.COINDESK_APPLY_MAPPING).lower(),
    }
    grp = config.COINDESK_TICK_GROUPS.strip()
    if grp:
        params["groups"] = grp
    payload = c.get(path, params, endpoint_metric="latest_tick")
    return parse_latest_ticks(payload, market=mkt)


def fetch_training_ohlcv(
    instrument: str,
    *,
    timeframe: str | None = None,
    limit: int | None = None,
    market: str | None = None,
    to_ts: str | None = None,
    client: CoinDeskClient | None = None,
) -> pd.DataFrame:
    """Return OHLCV DataFrame: timestamp (ms), open, high, low, close, volume, source, asset, market, instrument, timeframe."""
    c = client or CoinDeskClient()
    tf = timeframe or config.TRAIN_TIMEFRAME
    mkt = market or config.COINDESK_DEFAULT_MARKET
    lim = int(limit if limit is not None else config.COINDESK_TRAIN_LIMIT)
    ts_to = to_ts if to_ts is not None else (config.COINDESK_TRAIN_TO_TS or None)
    path = config.COINDESK_OHLCV_PATH.strip() or default_historical_ohlcv_path(tf)
    params = build_historical_ohlcv_params(mkt, instrument, tf, lim, ts_to)
    payload = c.get(path, params, endpoint_metric="ohlcv")
    rows = parse_ohlcv_payload(payload, market=mkt, instrument=instrument, timeframe=tf)
    if not rows:
        logger.warning("CoinDesk OHLCV returned no rows for %s %s", instrument, tf)
        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "source",
                "asset",
                "market",
                "instrument",
                "timeframe",
            ]
        )
    df = pd.DataFrame(rows)
    df["timestamp"] = df["timestamp_ms"].astype("int64")
    df = df.drop(columns=["timestamp_ms"], errors="ignore")
    df["source"] = "coindesk"
    df["asset"] = instrument.replace("-", "_").replace("/", "_")
    df["market"] = mkt
    df["instrument"] = instrument
    df["timeframe"] = tf
    return df[
        [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source",
            "asset",
            "market",
            "instrument",
            "timeframe",
        ]
    ].reset_index(drop=True)


def fetch_orderbook(
    *,
    instrument: str,
    market: str | None = None,
    depth: int | None = None,
    client: CoinDeskClient | None = None,
) -> dict[str, Any] | None:
    path = config.COINDESK_ORDERBOOK_PATH.strip()
    if not path:
        logger.warning("COINDESK_ORDERBOOK_PATH not set; skip order book fetch")
        return None
    c = client or CoinDeskClient()
    mkt = market or config.COINDESK_DEFAULT_MARKET
    params: dict[str, Any] = {"market": mkt, "instruments": instrument}
    if depth is not None:
        params["depth"] = depth
    payload = c.get(path, params, endpoint_metric="orderbook")
    return parse_orderbook_payload(payload, market=mkt, instrument=instrument, depth=depth)


def fetch_instruments_metadata(
    *,
    market: str | None = None,
    client: CoinDeskClient | None = None,
) -> list[dict[str, Any]]:
    path = config.COINDESK_INSTRUMENTS_METADATA_PATH.strip()
    if not path:
        return []
    c = client or CoinDeskClient()
    mkt = market or config.COINDESK_DEFAULT_MARKET
    payload = c.get(path, {"market": mkt}, endpoint_metric="instruments")
    return parse_instrument_metadata(payload, market=mkt)

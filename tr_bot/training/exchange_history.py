"""Paginated ccxt OHLCV for Binance-scale history (multiple fetch_ohlcv calls)."""

from __future__ import annotations

import logging
import time
from typing import Any

import ccxt
import pandas as pd

from tr_bot import config
from tr_bot.binance_rest import call_ccxt_with_retries
from tr_bot.exchange import get_ccxt_exchange, reset_ccxt_exchange

logger = logging.getLogger("tr_bot.training.exchange_history")


def fetch_exchange_ohlcv_range(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    *,
    chunk_limit: int | None = None,
    sleep_s: float | None = None,
) -> pd.DataFrame:
    """
    Walk forward with `since` until candles reach end_ms or exchange returns no more rows.
    Spot Binance via ccxt; requires a pair symbol like BTC/USDT.
    """
    if "/" not in symbol:
        raise ValueError(f"exchange training expects a ccxt pair symbol, got {symbol!r}")

    lim = min(int(chunk_limit or config.TRAIN_EXCHANGE_CHUNK_LIMIT), 1000)
    lim = max(1, lim)
    delay = float(sleep_s if sleep_s is not None else config.TRAIN_EXCHANGE_FETCH_SLEEP_S)
    delay = max(0.0, delay)

    if start_ms >= end_ms:
        raise ValueError(f"start_ms ({start_ms}) must be < end_ms ({end_ms})")

    exchange = get_ccxt_exchange()
    all_rows: list[list[Any]] = []
    since = int(start_ms)
    max_retries = max(1, config.CCXT_FETCH_MAX_RETRIES)
    base_delay = max(0.1, config.CCXT_FETCH_RETRY_DELAY_S)

    while since < end_ms:
        batch: list[list[Any]] | None = None
        for attempt in range(max_retries):
            try:
                batch = call_ccxt_with_retries(
                    exchange,
                    lambda: exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=lim),
                    context=f"fetch_ohlcv_range {symbol} since={since}",
                )
                break
            except ccxt.BaseError as e:
                if attempt == max_retries - 1:
                    raise
                wait = base_delay * (2**attempt)
                logger.warning(
                    "fetch_ohlcv range %s since=%s attempt %s/%s: %s; retry in %.1fs",
                    symbol,
                    since,
                    attempt + 1,
                    max_retries,
                    e,
                    wait,
                )
                if attempt >= 1:
                    reset_ccxt_exchange()
                    exchange = get_ccxt_exchange()
                time.sleep(wait)

        if not batch:
            break

        last_ts = int(batch[-1][0])
        for row in batch:
            ts = int(row[0])
            if ts <= end_ms:
                all_rows.append(row)

        if last_ts >= end_ms:
            break
        next_since = last_ts + 1
        if next_since <= since:
            break
        since = next_since
        if len(batch) < lim:
            break
        if delay:
            time.sleep(delay)

    if not all_rows:
        raise RuntimeError(
            f"No OHLCV returned for {symbol} {timeframe} in range {start_ms}–{end_ms}"
        )

    seen: set[int] = set()
    unique: list[list[Any]] = []
    for row in sorted(all_rows, key=lambda r: r[0]):
        ts = int(row[0])
        if ts not in seen:
            seen.add(ts)
            unique.append(row)

    df = pd.DataFrame(unique, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df.reset_index(drop=True)

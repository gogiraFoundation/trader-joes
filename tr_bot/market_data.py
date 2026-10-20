"""OHLCV from ccxt (crypto) or yfinance (equities-style symbols)."""

from __future__ import annotations

import logging
import random
import time

import ccxt
import pandas as pd

from tr_bot import config
from tr_bot.binance_rest import call_ccxt_with_retries
from tr_bot.exchange import get_ccxt_exchange, reset_ccxt_exchange
from tr_bot.ohlcv import timeframe_to_yfinance_interval, yfinance_history_to_ohlcv

logger = logging.getLogger("tr_bot")


def _use_yfinance(symbol: str) -> bool:
    s = symbol.upper()
    return "/" not in s and not s.endswith("USDT")


def _fetch_ccxt_ohlcv_with_retry(
    symbol: str, timeframe: str, limit: int
) -> list[list]:
    max_rounds = max(1, config.CCXT_FETCH_MAX_RETRIES)
    base = max(0.1, config.CCXT_FETCH_RETRY_DELAY_S)
    for round_i in range(max_rounds):
        exchange = get_ccxt_exchange()
        try:
            return call_ccxt_with_retries(
                exchange,
                lambda: exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
                context=f"fetch_ohlcv {symbol} {timeframe}",
            )
        except ccxt.BaseError as e:
            if round_i == max_rounds - 1:
                raise
            delay = base * (2**round_i) + random.uniform(0, 0.35)  # nosec B311
            logger.warning(
                "fetch_ohlcv %s %s round %s/%s after retries: %s; reset session, sleep %.1fs",
                symbol,
                timeframe,
                round_i + 1,
                max_rounds,
                e,
                delay,
            )
            reset_ccxt_exchange()
            time.sleep(delay)
    raise RuntimeError("fetch_ohlcv: unexpected exit without data")


def timeframe_to_ms(timeframe: str) -> int:
    """Convert a ccxt-style timeframe string to milliseconds per bar."""
    seconds = int(ccxt.Exchange.parse_timeframe(timeframe))
    if seconds <= 0:
        raise ValueError(f"invalid timeframe for ms conversion: {timeframe!r}")
    return seconds * 1000


def fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    if (
        config.USE_COINDESK_OHLCV_FROM_DB
        and config.PAPER_TRADING
        and config.COINDESK_INSTRUMENTS
    ):
        candidates = [x.strip() for x in config.COINDESK_INSTRUMENTS.split(",") if x.strip()]
        base = symbol.split("/")[0].upper() if "/" in symbol else symbol.upper()
        instrument = next(
            (c for c in candidates if c.upper().startswith(base)),
            candidates[0],
        )
        tf = config.COINDESK_OHLCV_DB_TIMEFRAME.strip() or timeframe
        try:
            from tr_bot.coindesk.loader import load_ohlcv_for_backtest

            df = load_ohlcv_for_backtest(
                instrument=instrument,
                timeframe=tf,
                limit=limit,
            )
            if len(df) >= min(20, limit):
                logger.info(
                    "OHLCV from CoinDesk DB: instrument=%s timeframe=%s rows=%s",
                    instrument,
                    tf,
                    len(df),
                )
                return df
            logger.warning(
                "CoinDesk DB OHLCV short (%s rows); falling back to exchange/yahoo",
                len(df),
            )
        except Exception as e:
            logger.warning("CoinDesk DB OHLCV failed (%s); falling back", e)

    if _use_yfinance(symbol):
        import yfinance as yf

        interval = timeframe_to_yfinance_interval(timeframe)
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="60d", interval=interval, auto_adjust=False)
        if df.empty:
            raise RuntimeError(f"yfinance returned no data for {symbol}")
        df = yfinance_history_to_ohlcv(df).tail(limit)
        return df.reset_index(drop=True)

    data = _fetch_ccxt_ohlcv_with_retry(symbol, timeframe, limit)
    return pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )


def fetch_ohlcv_deep(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """
    Fetch up to `limit` bars. For ccxt pairs when limit exceeds one page
    (~TRAIN_EXCHANGE_CHUNK_LIMIT), paginate via fetch_exchange_ohlcv_range.
    Yahoo / CoinDesk DB / short limits use fetch_ohlcv unchanged.
    """
    lim = max(1, int(limit))
    chunk = max(1, min(int(config.TRAIN_EXCHANGE_CHUNK_LIMIT), 1000))
    if _use_yfinance(symbol) or lim <= chunk or "/" not in symbol:
        return fetch_ohlcv(symbol, timeframe, lim)

    from tr_bot.training.exchange_history import fetch_exchange_ohlcv_range

    bar_ms = timeframe_to_ms(timeframe)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - lim * bar_ms - bar_ms
    logger.info(
        "OHLCV deep fetch: %s %s limit=%s range_ms=%s..%s",
        symbol,
        timeframe,
        lim,
        start_ms,
        end_ms,
    )
    df = fetch_exchange_ohlcv_range(symbol, timeframe, start_ms, end_ms)
    if df.empty:
        raise RuntimeError(f"deep OHLCV empty for {symbol} {timeframe}")
    return df.tail(lim).reset_index(drop=True)

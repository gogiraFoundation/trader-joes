"""Load CoinDesk-persisted OHLCV for backtesting."""

from __future__ import annotations

from typing import Any

import pandas as pd

from tr_bot import config
from tr_bot.db import coindesk_repo


def load_ohlcv_for_backtest(
    *,
    instrument: str,
    timeframe: str | None = None,
    market: str | None = None,
    limit: int | None = None,
    conn: Any | None = None,
) -> pd.DataFrame:
    """
    Return OHLCV with columns timestamp, open, high, low, close, volume (same as ccxt fetch_ohlcv).
    """
    tf = timeframe or config.COINDESK_OHLCV_DB_TIMEFRAME or config.TIMEFRAME
    lim = int(limit if limit is not None else config.BACKTEST_BARS)
    own = conn is None
    if own:
        conn = coindesk_repo.connect()
    try:
        df = coindesk_repo.load_ohlcv_dataframe(
            conn,
            instrument=instrument,
            timeframe=tf,
            market=market,
            limit=lim,
        )
    finally:
        if own and conn is not None:
            conn.close()
    return df

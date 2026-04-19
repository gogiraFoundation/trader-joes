"""Shared OHLCV DataFrame schema for all data sources."""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("tr_bot.ohlcv")


def timeframe_to_yfinance_interval(tf: str) -> str:
    """
    Map ccxt-style timeframe to yfinance `interval=`.

    Unknown values used to default to 5m, which breaks long `period=` requests
    (Yahoo only serves intraday data for the last ~60 days).
    """
    raw = (tf or "").strip().lower()
    m = {
        "1m": "1m",
        "3m": "3m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "2h": "1h",
        "4h": "4h",
        "1d": "1d",
        # Yahoo has no multi-day bar named "3d"; daily is appropriate for multi-year training.
        "2d": "1d",
        "3d": "1d",
        "5d": "5d",
        "1w": "1wk",
        "1wk": "1wk",
    }
    if raw in m:
        return m[raw]
    logger.warning(
        "Unknown timeframe %r for yfinance; using 5m (only valid for short lookback periods)",
        tf,
    )
    return "5m"


def yfinance_history_to_ohlcv(history: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance `Ticker.history` output to timestamp + OHLCV columns."""
    if history.empty:
        raise RuntimeError("yfinance history is empty")
    df = history.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df = df.reset_index()
    time_col = df.columns[0]
    ts = pd.to_datetime(df[time_col], utc=True)
    df["timestamp"] = (ts.astype(np.int64) // 10**9).astype(np.int64)
    out = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    return out.reset_index(drop=True)

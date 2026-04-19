"""CoinDesk Data API client, normalization, and ingestion helpers."""

from tr_bot.coindesk.client import CoinDeskClient
from tr_bot.coindesk.endpoints import (
    fetch_instruments_metadata,
    fetch_latest_ticks,
    fetch_orderbook,
    fetch_training_ohlcv,
)
from tr_bot.coindesk.loader import load_ohlcv_for_backtest

__all__ = [
    "CoinDeskClient",
    "fetch_latest_ticks",
    "fetch_training_ohlcv",
    "fetch_orderbook",
    "fetch_instruments_metadata",
    "load_ohlcv_for_backtest",
]

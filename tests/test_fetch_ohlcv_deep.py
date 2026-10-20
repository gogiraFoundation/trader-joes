"""Deep OHLCV fetch: paginate when limit exceeds one ccxt page."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from tr_bot.market_data import fetch_ohlcv_deep, timeframe_to_ms


def test_timeframe_to_ms_5m() -> None:
    assert timeframe_to_ms("5m") == 300_000


def test_fetch_ohlcv_deep_short_limit_uses_single_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    single = MagicMock(
        return_value=pd.DataFrame(
            {
                "timestamp": [1, 2],
                "open": [1.0, 2.0],
                "high": [1.0, 2.0],
                "low": [1.0, 2.0],
                "close": [1.0, 2.0],
                "volume": [1.0, 2.0],
            }
        )
    )
    monkeypatch.setattr("tr_bot.market_data.fetch_ohlcv", single)
    monkeypatch.setattr("tr_bot.market_data.config.TRAIN_EXCHANGE_CHUNK_LIMIT", 1000)

    df = fetch_ohlcv_deep("BTC/USDT", "5m", 500)
    assert len(df) == 2
    single.assert_called_once_with("BTC/USDT", "5m", 500)


def test_fetch_ohlcv_deep_paginates_when_over_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tr_bot.market_data.config.TRAIN_EXCHANGE_CHUNK_LIMIT", 1000)
    rows = pd.DataFrame(
        {
            "timestamp": list(range(2500)),
            "open": [1.0] * 2500,
            "high": [1.0] * 2500,
            "low": [1.0] * 2500,
            "close": [1.0] * 2500,
            "volume": [1.0] * 2500,
        }
    )
    mock_range = MagicMock(return_value=rows)
    monkeypatch.setattr(
        "tr_bot.training.exchange_history.fetch_exchange_ohlcv_range",
        mock_range,
    )
    single = MagicMock()
    monkeypatch.setattr("tr_bot.market_data.fetch_ohlcv", single)

    df = fetch_ohlcv_deep("BTC/USDT", "5m", 2000)
    assert len(df) == 2000
    assert list(df["timestamp"]) == list(range(500, 2500))
    single.assert_not_called()
    mock_range.assert_called_once()
    args = mock_range.call_args[0]
    assert args[0] == "BTC/USDT"
    assert args[1] == "5m"
    assert args[2] < args[3]

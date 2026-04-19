"""Paginated exchange fetch + training dataset parquet cache."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from tr_bot.training.dataset import (
    load_training_dataset,
    save_training_dataset,
)
from tr_bot.training.exchange_history import fetch_exchange_ohlcv_range


def test_fetch_exchange_ohlcv_range_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []

    class MockEx:
        def fetch_ohlcv(
            self, symbol: str, timeframe: str, since: int, limit: int
        ) -> list[list]:
            calls.append((since, limit))
            if since == 1_000_000:
                return [
                    [1_000_000, 1, 1, 1, 1, 1],
                    [2_000_000, 2, 2, 2, 2, 2],
                ]
            if since == 2_000_001:
                return [[3_000_000, 3, 3, 3, 3, 3]]
            if since == 3_000_001:
                return []
            raise AssertionError(f"unexpected since={since}")

    monkeypatch.setattr(
        "tr_bot.training.exchange_history.get_ccxt_exchange",
        lambda: MockEx(),
    )
    monkeypatch.setattr("tr_bot.training.exchange_history.time.sleep", lambda _: None)
    # Page size 2: first page full (2==limit), second page short (1<limit) ends pagination.
    monkeypatch.setattr("tr_bot.training.exchange_history.config.TRAIN_EXCHANGE_CHUNK_LIMIT", 2)

    df = fetch_exchange_ohlcv_range(
        "BTC/USDT",
        "1d",
        1_000_000,
        5_000_000,
        chunk_limit=2,
    )
    assert len(df) == 3
    assert list(df["timestamp"]) == [1_000_000, 2_000_000, 3_000_000]
    assert len(calls) >= 2


def test_dataset_save_load_roundtrip(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tr_bot.training.dataset as ds

    monkeypatch.setattr(ds.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(ds.config, "TRAIN_DATASET_PATH", "training/datasets/latest.parquet")
    monkeypatch.setattr(ds.config, "TRAIN_DATASET_VERSION_SNAPSHOT", False)
    monkeypatch.setattr(ds.config, "TRAIN_SOURCES", "yahoo")

    df = pd.DataFrame(
        {
            "timestamp": [1, 2],
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.0, 2.0],
            "volume": [10.0, 20.0],
            "source": ["yahoo", "yahoo"],
            "asset": ["X", "X"],
        }
    )
    save_training_dataset(df, extra_manifest={"test": True})
    df2, meta = load_training_dataset()
    assert len(df2) == 2
    assert meta.get("test") is True
    assert meta.get("rows") == 2


def test_parse_sources_binance_alias() -> None:
    from tr_bot.training.pipeline import _parse_sources

    assert _parse_sources("yahoo,binance") == {"yahoo", "exchange"}
    assert _parse_sources("exchange,binance") == {"exchange"}
    assert _parse_sources("yahoo,coindesk") == {"yahoo", "coindesk"}


def test_exchange_range_ms_lookback(monkeypatch: pytest.MonkeyPatch) -> None:
    """collect_training_frames uses paginated fetch when lookback > 0."""
    import tr_bot.training.pipeline as pipe

    monkeypatch.setattr(pipe.config, "TRAIN_SOURCES", "exchange")
    monkeypatch.setattr(pipe.config, "TRAIN_EXCHANGE_SYMBOL", "BTC/USDT")
    monkeypatch.setattr(pipe.config, "TRAIN_TIMEFRAME", "1d")
    monkeypatch.setattr(pipe.config, "TRAIN_EXCHANGE_LOOKBACK_DAYS", 7)
    monkeypatch.setattr(pipe.config, "TRAIN_EXCHANGE_START_MS", "")
    monkeypatch.setattr(pipe.config, "TRAIN_EXCHANGE_END_MS", "")
    monkeypatch.setattr(pipe.config, "TRAIN_EXCHANGE_FETCH_SLEEP_S", 0.0)
    monkeypatch.setattr(pipe.config, "TRAIN_EXCHANGE_CHUNK_LIMIT", 1000)

    mock_df = pd.DataFrame(
        {
            "timestamp": [1000],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1.0],
        }
    )
    mock_range = MagicMock(return_value=mock_df)
    monkeypatch.setattr(pipe, "fetch_exchange_ohlcv_range", mock_range)

    df = pipe.collect_training_frames()
    assert len(df) == 1
    assert df["source"].iloc[0] == "exchange"
    mock_range.assert_called_once()
    args, kwargs = mock_range.call_args
    assert args[0] == "BTC/USDT"
    assert args[1] == "1d"
    assert args[2] < args[3]

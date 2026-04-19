from __future__ import annotations

import pandas as pd
import pytest


def test_collect_training_frames_skips_bad_yahoo_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch pipeline.config (not tr_bot.config): strategy_profile tests reload tr_bot.config,
    # but pipeline still references the config object from its first import.
    import tr_bot.training.pipeline as pipe

    monkeypatch.setattr(pipe.config, "TRAIN_SOURCES", "yahoo")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_TICKERS", "GOOD,BAD")
    monkeypatch.setattr(pipe.config, "TRAIN_TIMEFRAME", "1d")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_PERIOD", "2y")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_FAIL_ON_SKIP", False)

    def _mock_fetch(ticker: str, timeframe: str, period: str) -> pd.DataFrame:
        if ticker == "BAD":
            raise RuntimeError("no data")
        return pd.DataFrame(
            {
                "timestamp": [1, 2],
                "open": [1.0, 2.0],
                "high": [1.1, 2.1],
                "low": [0.9, 1.9],
                "close": [1.0, 2.0],
                "volume": [10.0, 20.0],
                "source": ["yahoo", "yahoo"],
                "asset": [ticker, ticker],
            }
        )

    monkeypatch.setattr(pipe, "fetch_yahoo_training", _mock_fetch)

    out = pipe.collect_training_frames()
    assert len(out) == 2
    assert set(out["asset"].unique()) == {"GOOD"}


def test_collect_training_frames_yahoo_fail_on_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    import tr_bot.training.pipeline as pipe

    monkeypatch.setattr(pipe.config, "TRAIN_SOURCES", "yahoo")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_TICKERS", "GOOD,BAD")
    monkeypatch.setattr(pipe.config, "TRAIN_TIMEFRAME", "1d")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_PERIOD", "2y")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_FAIL_ON_SKIP", True)

    def _mock_fetch(ticker: str, timeframe: str, period: str) -> pd.DataFrame:
        if ticker == "BAD":
            raise RuntimeError("no data")
        return pd.DataFrame(
            {
                "timestamp": [1, 2],
                "open": [1.0, 2.0],
                "high": [1.1, 2.1],
                "low": [0.9, 1.9],
                "close": [1.0, 2.0],
                "volume": [10.0, 20.0],
                "source": ["yahoo", "yahoo"],
                "asset": [ticker, ticker],
            }
        )

    monkeypatch.setattr(pipe, "fetch_yahoo_training", _mock_fetch)

    with pytest.raises(RuntimeError, match="YAHOO_TRAIN_FAIL_ON_SKIP"):
        pipe.collect_training_frames()


def test_collect_training_frames_skips_non_runtimeerror_yahoo_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tr_bot.training.pipeline as pipe

    monkeypatch.setattr(pipe.config, "TRAIN_SOURCES", "yahoo")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_TICKERS", "GOOD,BAD")
    monkeypatch.setattr(pipe.config, "TRAIN_TIMEFRAME", "1d")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_PERIOD", "2y")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_FAIL_ON_SKIP", False)

    def _mock_fetch(ticker: str, timeframe: str, period: str) -> pd.DataFrame:
        if ticker == "BAD":
            raise TypeError("'NoneType' object is not subscriptable")
        return pd.DataFrame(
            {
                "timestamp": [1, 2],
                "open": [1.0, 2.0],
                "high": [1.1, 2.1],
                "low": [0.9, 1.9],
                "close": [1.0, 2.0],
                "volume": [10.0, 20.0],
                "source": ["yahoo", "yahoo"],
                "asset": [ticker, ticker],
            }
        )

    monkeypatch.setattr(pipe, "fetch_yahoo_training", _mock_fetch)

    out = pipe.collect_training_frames()
    assert len(out) == 2
    assert set(out["asset"].unique()) == {"GOOD"}


def test_collect_training_frames_non_runtimeerror_respects_fail_on_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tr_bot.training.pipeline as pipe

    monkeypatch.setattr(pipe.config, "TRAIN_SOURCES", "yahoo")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_TICKERS", "GOOD,BAD")
    monkeypatch.setattr(pipe.config, "TRAIN_TIMEFRAME", "1d")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_PERIOD", "2y")
    monkeypatch.setattr(pipe.config, "YAHOO_TRAIN_FAIL_ON_SKIP", True)

    def _mock_fetch(ticker: str, timeframe: str, period: str) -> pd.DataFrame:
        if ticker == "BAD":
            raise TypeError("upstream yahoo parser error")
        return pd.DataFrame(
            {
                "timestamp": [1, 2],
                "open": [1.0, 2.0],
                "high": [1.1, 2.1],
                "low": [0.9, 1.9],
                "close": [1.0, 2.0],
                "volume": [10.0, 20.0],
                "source": ["yahoo", "yahoo"],
                "asset": [ticker, ticker],
            }
        )

    monkeypatch.setattr(pipe, "fetch_yahoo_training", _mock_fetch)

    with pytest.raises(RuntimeError, match="YAHOO_TRAIN_FAIL_ON_SKIP"):
        pipe.collect_training_frames()

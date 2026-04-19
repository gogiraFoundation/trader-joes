from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from tr_bot.training.yahoo import warn_yahoo_training_quality


def test_warn_stablecoin_usd_pair() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [1, 2],
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [1.0, 1.0],
        }
    )
    with patch("tr_bot.training.yahoo.logger") as log:
        warn_yahoo_training_quality(
            df,
            "USDT-USD",
            min_daily_return_stddev=0.0,
            min_close_range_pct=0.0,
        )
    log.warning.assert_called_once()
    assert "stablecoin" in log.warning.call_args[0][0].lower()


def test_warn_low_daily_return_std() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [1, 2, 3],
            "open": [100.0, 100.01, 100.02],
            "high": [100.02, 100.03, 100.04],
            "low": [99.99, 100.0, 100.01],
            "close": [100.0, 100.01, 100.02],
            "volume": [1.0, 1.0, 1.0],
        }
    )
    with patch("tr_bot.training.yahoo.logger") as log:
        warn_yahoo_training_quality(
            df,
            "FAKE-USD",
            min_daily_return_stddev=0.01,
            min_close_range_pct=0.0,
        )
    log.warning.assert_called_once()
    assert "MIN_DAILY_RETURN_STDDEV" in log.warning.call_args[0][0]


def test_warn_tight_close_range() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [1, 2],
            "open": [100.0, 100.0],
            "high": [100.001, 100.001],
            "low": [99.999, 99.999],
            "close": [100.0, 100.0],
            "volume": [1.0, 1.0],
        }
    )
    with patch("tr_bot.training.yahoo.logger") as log:
        warn_yahoo_training_quality(
            df,
            "FAKE-USD",
            min_daily_return_stddev=0.0,
            min_close_range_pct=0.1,
        )
    log.warning.assert_called_once()
    assert "MIN_CLOSE_RANGE_PCT" in log.warning.call_args[0][0]

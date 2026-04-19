"""Trend continuation signal."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tr_bot.signals import trend_continuation_signal


def test_trend_buy_bull_stack() -> None:
    df = pd.DataFrame(
        {
            "close": [102.0],
            "ema20": [101.0],
            "ema50": [100.0],
        }
    )
    assert trend_continuation_signal(df) == "buy"


def test_trend_sell_bear_stack() -> None:
    df = pd.DataFrame(
        {
            "close": [98.0],
            "ema20": [99.0],
            "ema50": [100.0],
        }
    )
    assert trend_continuation_signal(df) == "sell"


def test_trend_hold_chop() -> None:
    df = pd.DataFrame(
        {
            "close": [100.0],
            "ema20": [99.0],
            "ema50": [100.0],
        }
    )
    assert trend_continuation_signal(df) == "hold"


def test_trend_hold_nan() -> None:
    df = pd.DataFrame(
        {
            "close": [np.nan],
            "ema20": [101.0],
            "ema50": [100.0],
        }
    )
    assert trend_continuation_signal(df) == "hold"

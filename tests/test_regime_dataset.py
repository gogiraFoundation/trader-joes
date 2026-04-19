"""Regime feature/label helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tr_bot.indicators import compute_indicators
from tr_bot.training.regime_dataset import build_regime_xy, sort_training_frame


def test_sort_training_frame_stable_order() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [3000, 1000, 2000],
            "source": ["yahoo", "yahoo", "yahoo"],
            "asset": ["SPY", "SPY", "SPY"],
            "x": [1, 2, 3],
        }
    )
    out = sort_training_frame(df)
    assert out["timestamp"].tolist() == [1000, 2000, 3000]


def test_build_regime_xy_aligns_with_regime_train() -> None:
    rng = np.random.default_rng(42)
    n = 120
    close = 50 + np.cumsum(rng.normal(0, 0.2, n))
    df = pd.DataFrame(
        {
            "timestamp": np.arange(n),
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": rng.integers(500, 1000, n),
        }
    )
    df = compute_indicators(df).dropna(subset=["rsi", "ema20", "ema50", "atr"])
    X, y = build_regime_xy(df)
    assert len(X) == len(y)
    assert list(X.columns) == ["rsi", "ema20", "ema50", "atr"]
    assert y.isin((0, 1)).all()


def test_build_regime_xy_too_small() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [1],
            "rsi": [50.0],
            "ema20": [1.0],
            "ema50": [1.0],
            "atr": [1.0],
            "close": [1.0],
        }
    )
    with pytest.raises(ValueError, match="Not enough rows"):
        build_regime_xy(df)

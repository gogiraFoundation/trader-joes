"""Regime model train/predict smoke."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tr_bot.indicators import compute_indicators
from tr_bot.regime import RegimeModel


def test_regime_train_predict() -> None:
    rng = np.random.default_rng(7)
    n = 200
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
    m = RegimeModel()
    m.train(df)
    out = m.predict(df)
    assert out in ("range", "trend")

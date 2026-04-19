"""Indicators + extended TA."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tr_bot.indicators import compute_all_indicators, compute_indicators
from tr_bot.ta_extended import extend_ta, mean_reversion_ta_allows, trend_regime_ta_allows


def _sample_ohlcv(n: int = 150) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0, 0.3, n))
    return pd.DataFrame(
        {
            "timestamp": np.arange(n),
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": rng.integers(1000, 2000, n),
        }
    )


def test_compute_indicators_columns() -> None:
    df = compute_indicators(_sample_ohlcv()).dropna()
    for col in ("ema20", "ema50", "rsi", "atr"):
        assert col in df.columns


def test_extend_ta_adds_adx_macd() -> None:
    df = extend_ta(compute_indicators(_sample_ohlcv())).dropna()
    for col in ("adx", "macd", "bb_upper", "stoch_k", "volume_z"):
        assert col in df.columns


def test_mean_reversion_ta_allows_default() -> None:
    df = compute_all_indicators(_sample_ohlcv()).dropna()
    row = df.iloc[-1]
    assert isinstance(mean_reversion_ta_allows(row), bool)


def test_trend_regime_ta_allows_respects_adx_min(monkeypatch: pytest.MonkeyPatch) -> None:
    import tr_bot.config as cfg

    monkeypatch.setattr(cfg, "USE_TA_FILTER", True)
    monkeypatch.setattr(cfg, "ADX_MIN_FOR_TREND", 25.0)
    row_low = pd.Series({"adx": 10.0})
    row_ok = pd.Series({"adx": 30.0})
    assert trend_regime_ta_allows(row_low) is False
    assert trend_regime_ta_allows(row_ok) is True

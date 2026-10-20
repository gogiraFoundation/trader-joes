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


def test_compute_indicators_grouped_no_cross_asset_bleed() -> None:
    """Interleaved multi-asset frame must match per-asset solo computation."""
    n = 80
    a = _sample_ohlcv(n)
    a["source"] = "exchange"
    a["asset"] = "AAA_USDT"
    a["close"] = 100 + np.arange(n, dtype=float)
    a["open"] = a["close"]
    a["high"] = a["close"] + 0.5
    a["low"] = a["close"] - 0.5

    b = _sample_ohlcv(n)
    b["source"] = "exchange"
    b["asset"] = "BBB_USDT"
    b["timestamp"] = a["timestamp"]
    # Very different price path so ungrouped EMA would diverge sharply from solo A.
    b["close"] = 1000 + np.arange(n, dtype=float) * 10
    b["open"] = b["close"]
    b["high"] = b["close"] + 1.0
    b["low"] = b["close"] - 1.0

    # Interleave by timestamp (A,B,A,B,...) — worst case for ungrouped rolling.
    mixed = (
        pd.concat([a, b], ignore_index=True)
        .sort_values(["timestamp", "asset"], kind="mergesort")
        .reset_index(drop=True)
    )
    grouped = compute_indicators(mixed)
    solo_a = compute_indicators(a.drop(columns=["source", "asset"]))

    got = grouped.loc[grouped["asset"] == "AAA_USDT"].sort_values("timestamp").reset_index(drop=True)
    exp = solo_a.sort_values("timestamp").reset_index(drop=True)
    for col in ("ema20", "ema50", "rsi", "atr"):
        np.testing.assert_allclose(
            got[col].to_numpy(dtype=float),
            exp[col].to_numpy(dtype=float),
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
            err_msg=col,
        )


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

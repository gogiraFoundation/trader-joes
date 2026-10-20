"""Session / correlation smoke tests."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from tr_bot.correlation import correlation_size_multiplier, pearson_matrix, rolling_returns
from tr_bot.market_session import classify_session
from tr_bot.position import OpenPosition
from tr_bot.volatility_regime import classify_volatility


def test_classify_session_overlap() -> None:
    ts = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    assert classify_session(ts) == "london_ny_overlap"
    assert classify_session(datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)) == "asia"


def test_correlation_multiplier() -> None:
    assert correlation_size_multiplier(0.9) == 0.5
    assert correlation_size_multiplier(0.2) == 1.0


def test_pearson_and_vol() -> None:
    n = 80
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 0.3, n))
    df = pd.DataFrame(
        {
            "timestamp": np.arange(n) * 60_000,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1000,
        }
    )
    df2 = df.copy()
    df2["close"] = close * 1.01 + rng.normal(0, 0.05, n)
    m = pearson_matrix({"A": rolling_returns(df), "B": rolling_returns(df2)})
    assert "A" in m and "B" in m["A"]
    vr, pct = classify_volatility(df)
    assert vr in ("low", "normal", "high", "extreme")


def test_structural_exit_mode() -> None:
    pos = OpenPosition(
        symbol="BTC/USDT",
        side="long",
        entry_price=100.0,
        atr=2.0,
        size=1.0,
        exit_mode="structural",
        structural_stop=95.0,
        structural_target=110.0,
    )
    bar = pd.Series({"low": 94.0, "high": 101.0, "close": 96.0})
    exited, outcome = pos.check_bar(bar)
    assert exited is True
    assert outcome == "loss"

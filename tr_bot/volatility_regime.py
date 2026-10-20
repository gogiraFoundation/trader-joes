"""ATR percentile volatility regime classification."""

from __future__ import annotations


import pandas as pd


def _atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    if "atr" in df.columns:
        return df["atr"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev).abs(), (low - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def classify_volatility(
    df: pd.DataFrame,
    *,
    period: int = 14,
    lookback: int = 100,
) -> tuple[str, float | None]:
    """
    Returns (regime, percentile) where regime in
    low | normal | high | extreme.
    """
    if df is None or len(df) < max(period + 5, 20):
        return "normal", None
    atr = _atr_series(df, period=period).dropna()
    if atr.empty:
        return "normal", None
    window = atr.tail(lookback)
    last = float(window.iloc[-1])
    pct = float((window <= last).mean() * 100.0)
    if pct >= 95:
        return "extreme", pct
    if pct >= 80:
        return "high", pct
    if pct <= 20:
        return "low", pct
    return "normal", pct


def volatility_size_adjuster(regime: str) -> float:
    return {
        "low": 1.1,
        "normal": 1.0,
        "high": 0.8,
        "extreme": 0.5,
    }.get(regime, 1.0)

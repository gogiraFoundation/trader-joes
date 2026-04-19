"""Mean reversion signals (Section 4.4)."""

from __future__ import annotations

import pandas as pd

from tr_bot import config


def mean_reversion_signal(df: pd.DataFrame) -> str:
    last = df.iloc[-1]
    if pd.isna(last.get("rsi")) or pd.isna(last.get("ema20")):
        return "hold"
    lo = float(config.SIGNAL_RSI_OVERSOLD)
    hi = float(config.SIGNAL_RSI_OVERBOUGHT)
    if last["rsi"] < lo and last["close"] < last["ema20"]:
        return "buy"
    if last["rsi"] > hi and last["close"] > last["ema20"]:
        return "sell"
    return "hold"


def trend_continuation_signal(df: pd.DataFrame) -> str:
    """
    Momentum / continuation: trade with EMA stack (close vs ema20 vs ema50).
    Buy when bullish stack, sell when bearish; else hold.
    """
    last = df.iloc[-1]
    c = last.get("close")
    e20 = last.get("ema20")
    e50 = last.get("ema50")
    if c is None or e20 is None or e50 is None:
        return "hold"
    if pd.isna(c) or pd.isna(e20) or pd.isna(e50):
        return "hold"
    c, e20, e50 = float(c), float(e20), float(e50)
    if c > e20 > e50:
        return "buy"
    if c < e20 < e50:
        return "sell"
    return "hold"

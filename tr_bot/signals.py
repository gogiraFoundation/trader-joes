"""Mean reversion signals (Section 4.4) + TA confirmation helpers for SMC."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from tr_bot import config


def ta_confirmation_bias(df: pd.DataFrame) -> Literal["bullish", "bearish", "neutral"]:
    """
    Confirmation-only bias for SMC scoring (does not generate standalone entries).
    Uses RSI vs mid + EMA stack when columns available.
    """
    if df is None or df.empty:
        return "neutral"
    last = df.iloc[-1]
    rsi = last.get("rsi")
    c = last.get("close")
    e20 = last.get("ema20")
    e50 = last.get("ema50")
    votes_bull = 0
    votes_bear = 0
    if rsi is not None and not pd.isna(rsi):
        if float(rsi) < 45:
            votes_bull += 1
        elif float(rsi) > 55:
            votes_bear += 1
    if c is not None and e20 is not None and not pd.isna(c) and not pd.isna(e20):
        if float(c) > float(e20):
            votes_bull += 1
        else:
            votes_bear += 1
    if e20 is not None and e50 is not None and not pd.isna(e20) and not pd.isna(e50):
        if float(e20) > float(e50):
            votes_bull += 1
        elif float(e20) < float(e50):
            votes_bear += 1
    if votes_bull >= 2 and votes_bull > votes_bear:
        return "bullish"
    if votes_bear >= 2 and votes_bear > votes_bull:
        return "bearish"
    return "neutral"


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

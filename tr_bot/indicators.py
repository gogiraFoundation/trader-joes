"""Technical indicators (Section 4.2)."""

from __future__ import annotations

import pandas as pd


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = out["close"].ewm(span=20).mean()
    out["ema50"] = out["close"].ewm(span=50).mean()

    delta = out["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    out["rsi"] = 100 - (100 / (1 + rs))

    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder-style ATR smoothing for more stable volatility-aware sizing.
    out["atr"] = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()
    return out


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Base indicators plus optional extended TA (see `ta_extended`)."""
    from tr_bot import config
    from tr_bot.ta_extended import extend_ta

    out = compute_indicators(df)
    if config.USE_EXTENDED_TA:
        out = extend_ta(out)
    return out

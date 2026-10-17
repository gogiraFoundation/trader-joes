"""Order block detection from displacement candles."""

from __future__ import annotations

import pandas as pd

from tr_bot.smc.types import SmcFinding


def detect_order_blocks(
    df: pd.DataFrame,
    *,
    lookback: int = 60,
    body_atr_mult: float = 1.2,
) -> list[SmcFinding]:
    """
    Bullish OB: last down/small candle before a strong bullish displacement.
    Bearish OB: last up/small candle before a strong bearish displacement.
    Strength proxied by body size vs rolling ATR-like range.
    """
    n = len(df)
    if n < 5:
        return []
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    lows = df["low"].astype(float)
    c = df["close"].astype(float)
    rng = (h - lows).rolling(14, min_periods=3).mean().fillna((h - lows).mean())
    start = max(3, n - lookback)
    findings: list[SmcFinding] = []

    for i in range(start, n):
        body = abs(float(c.iloc[i]) - float(o.iloc[i]))
        atr_like = float(rng.iloc[i]) or 1e-9
        if body < body_atr_mult * atr_like:
            continue
        bull = float(c.iloc[i]) > float(o.iloc[i])
        # look back up to 3 bars for opposite-color origin
        for k in range(1, 4):
            j = i - k
            if j < 0:
                break
            bull_j = float(c.iloc[j]) > float(o.iloc[j])
            if bull and not bull_j:
                lo = min(float(o.iloc[j]), float(c.iloc[j]))
                hi = max(float(o.iloc[j]), float(c.iloc[j]))
                mitigated = any(
                    float(lows.iloc[t]) <= hi and float(h.iloc[t]) >= lo for t in range(i + 1, n)
                )
                findings.append(
                    SmcFinding(
                        kind="order_block",
                        direction="bullish",
                        index=j,
                        price_low=lo,
                        price_high=hi,
                        mitigated=mitigated,
                        confidence=min(0.9, 0.5 + body / (atr_like * 4)),
                        meta={"displacement_index": i},
                    )
                )
                break
            if (not bull) and bull_j:
                lo = min(float(o.iloc[j]), float(c.iloc[j]))
                hi = max(float(o.iloc[j]), float(c.iloc[j]))
                mitigated = any(
                    float(lows.iloc[t]) <= hi and float(h.iloc[t]) >= lo for t in range(i + 1, n)
                )
                findings.append(
                    SmcFinding(
                        kind="order_block",
                        direction="bearish",
                        index=j,
                        price_low=lo,
                        price_high=hi,
                        mitigated=mitigated,
                        confidence=min(0.9, 0.5 + body / (atr_like * 4)),
                        meta={"displacement_index": i},
                    )
                )
                break
    return findings

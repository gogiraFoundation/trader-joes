"""Fair Value Gap (imbalance) detection."""

from __future__ import annotations

import pandas as pd

from tr_bot.smc.types import SmcFinding


def detect_fvgs(
    df: pd.DataFrame,
    *,
    lookback: int = 80,
    min_gap_pct: float = 0.0,
) -> list[SmcFinding]:
    """
    Bullish FVG: bar[i-2].high < bar[i].low (gap up).
    Bearish FVG: bar[i-2].low > bar[i].high (gap down).
    Mitigation: later close enters the gap zone.
    """
    n = len(df)
    if n < 3:
        return []
    start = max(2, n - lookback)
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    closes = df["close"].astype(float)
    findings: list[SmcFinding] = []

    for i in range(start, n):
        h0 = float(highs.iloc[i - 2])
        l0 = float(lows.iloc[i - 2])
        hi = float(highs.iloc[i])
        lo = float(lows.iloc[i])
        mid = (float(closes.iloc[i - 2]) + float(closes.iloc[i])) / 2.0 or 1.0

        if lo > h0:
            gap = lo - h0
            if mid > 0 and (gap / mid) * 100.0 >= min_gap_pct:
                mitigated = any(
                    float(lows.iloc[j]) <= h0 or float(closes.iloc[j]) <= lo
                    for j in range(i + 1, n)
                )
                # also if later price trades into [h0, lo]
                mitigated = mitigated or any(
                    float(lows.iloc[j]) <= lo and float(highs.iloc[j]) >= h0
                    for j in range(i + 1, n)
                )
                findings.append(
                    SmcFinding(
                        kind="fvg",
                        direction="bullish",
                        index=i,
                        price_low=h0,
                        price_high=lo,
                        mitigated=mitigated,
                        confidence=0.65,
                    )
                )
        if hi < l0:
            gap = l0 - hi
            if mid > 0 and (gap / mid) * 100.0 >= min_gap_pct:
                mitigated = any(
                    float(highs.iloc[j]) >= l0 or float(closes.iloc[j]) >= hi
                    for j in range(i + 1, n)
                )
                mitigated = mitigated or any(
                    float(lows.iloc[j]) <= l0 and float(highs.iloc[j]) >= hi
                    for j in range(i + 1, n)
                )
                findings.append(
                    SmcFinding(
                        kind="fvg",
                        direction="bearish",
                        index=i,
                        price_low=hi,
                        price_high=l0,
                        mitigated=mitigated,
                        confidence=0.65,
                    )
                )
    return findings

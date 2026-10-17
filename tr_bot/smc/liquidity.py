"""Liquidity pools and sweep heuristics."""

from __future__ import annotations

import pandas as pd

from tr_bot.smc.structure import detect_swings
from tr_bot.smc.types import SmcFinding


def detect_liquidity_pools(
    df: pd.DataFrame,
    *,
    left: int = 2,
    right: int = 2,
    cluster_pct: float = 0.15,
) -> list[SmcFinding]:
    """
    Cluster swing highs/lows within cluster_pct of price into equal-high/low pools.
    """
    sh, sl = detect_swings(df, left=left, right=right)
    findings: list[SmcFinding] = []
    close = float(df["close"].iloc[-1]) if len(df) else 1.0
    tol = max(abs(close) * (cluster_pct / 100.0), 1e-9)

    def _cluster(points: list[tuple[int, float]], direction: str) -> None:
        if not points:
            return
        used = [False] * len(points)
        for i, (idx, px) in enumerate(points):
            if used[i]:
                continue
            members = [(idx, px)]
            used[i] = True
            for j in range(i + 1, len(points)):
                if used[j]:
                    continue
                if abs(points[j][1] - px) <= tol:
                    members.append(points[j])
                    used[j] = True
            if len(members) < 2:
                continue
            prices = [m[1] for m in members]
            findings.append(
                SmcFinding(
                    kind="liquidity_pool",
                    direction=direction,
                    index=members[-1][0],
                    price_low=min(prices),
                    price_high=max(prices),
                    confidence=min(0.9, 0.4 + 0.15 * len(members)),
                    meta={"count": len(members)},
                )
            )

    _cluster(sh, "bearish")  # stops above highs
    _cluster(sl, "bullish")  # stops below lows
    return findings


def detect_sweeps(
    df: pd.DataFrame,
    *,
    left: int = 2,
    right: int = 2,
) -> list[SmcFinding]:
    """
    Sweep: wick takes out last swing then closes back inside.
    Bullish sweep: low < last swing low, close > last swing low.
    Bearish sweep: high > last swing high, close < last swing high.
    """
    if len(df) < left + right + 3:
        return []
    sh, sl = detect_swings(df.iloc[:-1], left=left, right=right)
    i = len(df) - 1
    bar_h = float(df["high"].iloc[-1])
    bar_l = float(df["low"].iloc[-1])
    close = float(df["close"].iloc[-1])
    findings: list[SmcFinding] = []
    if sl:
        level = sl[-1][1]
        if bar_l < level and close > level:
            findings.append(
                SmcFinding(
                    kind="liquidity_sweep",
                    direction="bullish",
                    index=i,
                    price_low=bar_l,
                    price_high=level,
                    confidence=0.7,
                    meta={"swept_level": level},
                )
            )
    if sh:
        level = sh[-1][1]
        if bar_h > level and close < level:
            findings.append(
                SmcFinding(
                    kind="liquidity_sweep",
                    direction="bearish",
                    index=i,
                    price_low=level,
                    price_high=bar_h,
                    confidence=0.7,
                    meta={"swept_level": level},
                )
            )
    return findings

"""Swing detection and market structure bias (HH/HL vs LH/LL)."""

from __future__ import annotations

import pandas as pd

from tr_bot.smc.types import StructureBias, StructureState


def detect_swings(
    df: pd.DataFrame,
    *,
    left: int = 2,
    right: int = 2,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """
    Fractal swings: high[i] is swing high if greater than neighbours;
    low[i] is swing low if lower than neighbours.
    Returns (swing_highs, swing_lows) as (index, price) lists.
    """
    if len(df) < left + right + 1:
        return [], []
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    sh: list[tuple[int, float]] = []
    sl: list[tuple[int, float]] = []
    for i in range(left, len(df) - right):
        window_h = highs[i - left : i + right + 1]
        window_l = lows[i - left : i + right + 1]
        if highs[i] >= max(window_h) and highs[i] == max(window_h):
            sh.append((i, highs[i]))
        if lows[i] <= min(window_l) and lows[i] == min(window_l):
            sl.append((i, lows[i]))
    return sh, sl


def structure_state(
    df: pd.DataFrame,
    *,
    left: int = 2,
    right: int = 2,
) -> StructureState:
    sh, sl = detect_swings(df, left=left, right=right)
    bias = StructureBias.NEUTRAL
    if len(sh) >= 2 and len(sl) >= 2:
        hh = sh[-1][1] > sh[-2][1]
        hl = sl[-1][1] > sl[-2][1]
        lh = sh[-1][1] < sh[-2][1]
        ll = sl[-1][1] < sl[-2][1]
        if hh and hl:
            bias = StructureBias.BULLISH
        elif lh and ll:
            bias = StructureBias.BEARISH
    return StructureState(
        bias=bias,
        last_swing_high=sh[-1][1] if sh else None,
        last_swing_low=sl[-1][1] if sl else None,
        last_swing_high_idx=sh[-1][0] if sh else None,
        last_swing_low_idx=sl[-1][0] if sl else None,
        swing_highs=sh,
        swing_lows=sl,
    )

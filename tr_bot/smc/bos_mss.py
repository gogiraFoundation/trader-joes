"""Break of Structure (BOS) and Market Structure Shift (MSS)."""

from __future__ import annotations

import pandas as pd

from tr_bot.smc.structure import structure_state
from tr_bot.smc.types import SmcFinding, StructureBias


def detect_bos_mss(
    df: pd.DataFrame,
    *,
    left: int = 2,
    right: int = 2,
) -> list[SmcFinding]:
    """
    BOS: close beyond last swing in the direction of prevailing bias (continuation).
    MSS: close beyond last swing against prior bias (potential reversal).
    Uses last bar relative to prior swings.
    """
    if len(df) < left + right + 3:
        return []
    # Structure on all but last bar so the last close can break swings.
    state = structure_state(df.iloc[:-1], left=left, right=right)
    close = float(df["close"].iloc[-1])
    i = len(df) - 1
    findings: list[SmcFinding] = []

    if state.last_swing_high is not None and close > state.last_swing_high:
        if state.bias == StructureBias.BULLISH:
            findings.append(
                SmcFinding(
                    kind="bos",
                    direction="bullish",
                    index=i,
                    price_low=state.last_swing_high,
                    price_high=close,
                    confidence=0.7,
                    meta={"broken_level": state.last_swing_high},
                )
            )
        else:
            findings.append(
                SmcFinding(
                    kind="mss",
                    direction="bullish",
                    index=i,
                    price_low=state.last_swing_high,
                    price_high=close,
                    confidence=0.75,
                    meta={"broken_level": state.last_swing_high, "prior_bias": state.bias.value},
                )
            )
    if state.last_swing_low is not None and close < state.last_swing_low:
        if state.bias == StructureBias.BEARISH:
            findings.append(
                SmcFinding(
                    kind="bos",
                    direction="bearish",
                    index=i,
                    price_low=close,
                    price_high=state.last_swing_low,
                    confidence=0.7,
                    meta={"broken_level": state.last_swing_low},
                )
            )
        else:
            findings.append(
                SmcFinding(
                    kind="mss",
                    direction="bearish",
                    index=i,
                    price_low=close,
                    price_high=state.last_swing_low,
                    confidence=0.75,
                    meta={"broken_level": state.last_swing_low, "prior_bias": state.bias.value},
                )
            )
    return findings

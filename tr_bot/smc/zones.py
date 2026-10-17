"""Premium/discount and Optimal Trade Entry (OTE) zones."""

from __future__ import annotations

from typing import Any

import pandas as pd

from tr_bot.smc.structure import structure_state
from tr_bot.smc.types import SmcFinding, StructureBias


def premium_discount_zone(
    swing_low: float,
    swing_high: float,
    price: float,
) -> tuple[str, float]:
    """
    Midpoint of range: above = premium, below = discount.
    Returns (zone_name, distance_from_mid_pct).
    """
    if swing_high <= swing_low:
        return "equilibrium", 0.0
    mid = (swing_high + swing_low) / 2.0
    span = swing_high - swing_low
    dist = (price - mid) / span
    if price >= mid:
        return "premium", float(dist)
    return "discount", float(dist)


def ote_zone(swing_low: float, swing_high: float, *, bullish: bool) -> tuple[float, float]:
    """
    OTE typically 0.618–0.79 retracement of the impulse leg.
    Bullish: buy pullback toward fib from low→high.
    Bearish: sell pullback toward fib from high→low.
    """
    span = swing_high - swing_low
    if span <= 0:
        return swing_low, swing_high
    if bullish:
        # Retracement from high toward low: 61.8% and 79% of the move up
        hi = swing_high - 0.618 * span
        lo = swing_high - 0.79 * span
        return min(lo, hi), max(lo, hi)
    hi = swing_low + 0.79 * span
    lo = swing_low + 0.618 * span
    return min(lo, hi), max(lo, hi)


def analyze_zones(df: pd.DataFrame, *, left: int = 2, right: int = 2) -> list[SmcFinding]:
    state = structure_state(df, left=left, right=right)
    if state.last_swing_high is None or state.last_swing_low is None:
        return []
    price = float(df["close"].iloc[-1])
    zone, dist = premium_discount_zone(state.last_swing_low, state.last_swing_high, price)
    findings: list[SmcFinding] = [
        SmcFinding(
            kind="premium_discount",
            direction="neutral",
            index=len(df) - 1,
            price_low=state.last_swing_low,
            price_high=state.last_swing_high,
            confidence=0.6,
            meta={"zone": zone, "dist_from_mid": dist},
        )
    ]
    bullish = state.bias != StructureBias.BEARISH
    ote_lo, ote_hi = ote_zone(state.last_swing_low, state.last_swing_high, bullish=bullish)
    in_ote = ote_lo <= price <= ote_hi
    findings.append(
        SmcFinding(
            kind="ote",
            direction="bullish" if bullish else "bearish",
            index=len(df) - 1,
            price_low=ote_lo,
            price_high=ote_hi,
            confidence=0.7 if in_ote else 0.4,
            meta={"in_zone": in_ote, "price": price},
        )
    )
    return findings


def zone_summary(df: pd.DataFrame) -> dict[str, Any]:
    findings = analyze_zones(df)
    out: dict[str, Any] = {"premium_discount": None, "ote": None}
    for f in findings:
        out[f.kind] = f.to_dict()
    return out

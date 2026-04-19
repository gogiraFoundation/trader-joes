"""Decision aggregation (Section 4.6)."""

from __future__ import annotations


def decision_engine(
    regime: str,
    signal: str,
    sentiment: str,
    *,
    ta_ok: bool = True,
    fundamentals_ok: bool = True,
    trend_strategy_enabled: bool = False,
) -> str:
    if not ta_ok or not fundamentals_ok:
        return "hold"

    if regime == "range":
        if signal == "buy" and sentiment != "bearish":
            return "buy"
        if signal == "sell" and sentiment != "bullish":
            return "sell"
        return "hold"

    if regime == "trend" and trend_strategy_enabled:
        if signal == "buy" and sentiment != "bearish":
            return "buy"
        if signal == "sell" and sentiment != "bullish":
            return "sell"
        return "hold"

    return "hold"

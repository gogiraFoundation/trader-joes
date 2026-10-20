"""Shared post-decision entry gates for the live engine (and tests).

Generators propose buy/sell/hold. These gates may only turn buy/sell into hold.
They do not flip hold into an entry. Live-only blocks that build audit rows and
``continue`` (``risk_limit_reached``, ``live_sell_disabled``, ``sell_requires_ccxt``)
stay in ``engine.run_forever``.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from tr_bot import config


def open_positions_at_limit(
    positions: dict[str, Any] | None,
    max_open: int | None = None,
) -> bool:
    """
    True when a new entry must be blocked because open count >= MAX_OPEN_POSITIONS.

    ``max_open`` 0 / unset means unlimited (never at limit). Counts non-None values
    in ``positions`` (strategy opens only).
    """
    cap = int(config.MAX_OPEN_POSITIONS if max_open is None else max_open)
    if cap <= 0:
        return False
    n = 0
    for pos in (positions or {}).values():
        if pos is not None:
            n += 1
    return n >= cap


def _safe_float(x: Any) -> float | None:
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def signal_strength_allows(regime: str, signal: str, last: Any) -> tuple[bool, str | None]:
    """RSI distance / ADX margin strength filter (same rules as the engine)."""
    if not config.USE_SIGNAL_STRENGTH_FILTER:
        return True, None
    if signal not in ("buy", "sell"):
        return True, None
    rsi = _safe_float(last.get("rsi"))
    if rsi is None:
        return False, "missing_rsi"
    if signal == "buy":
        dist = float(config.SIGNAL_RSI_OVERSOLD) - rsi
    else:
        dist = rsi - float(config.SIGNAL_RSI_OVERBOUGHT)
    if dist < float(config.SIGNAL_MIN_RSI_DISTANCE):
        return False, "rsi_distance_too_small"
    adx = _safe_float(last.get("adx"))
    if adx is None:
        return True, None
    if regime == "range":
        max_adx = float(config.ADX_MAX_FOR_MEAN_REVERSION) - float(config.SIGNAL_MIN_ADX_MARGIN)
        if adx > max_adx:
            return False, "adx_too_high_for_range"
    elif regime == "trend":
        min_adx = float(config.ADX_MIN_FOR_TREND) + float(config.SIGNAL_MIN_ADX_MARGIN)
        if adx < min_adx:
            return False, "adx_too_low_for_trend"
    return True, None


def microstructure_allows(last: Any, prev: Any) -> tuple[bool, str | None]:
    if not config.USE_MICROSTRUCTURE_GUARD:
        return True, None
    close = _safe_float(last.get("close"))
    high = _safe_float(last.get("high"))
    low = _safe_float(last.get("low"))
    vol = _safe_float(last.get("volume"))
    prev_close = _safe_float(prev.get("close")) if prev is not None else None
    if close is None or close <= 0 or high is None or low is None:
        return False, "micro_missing_ohlc"
    range_pct = ((high - low) / close) * 100.0
    if range_pct > float(config.MICRO_MAX_BAR_RANGE_PCT):
        return False, "micro_range_too_wide"
    if prev_close is not None and prev_close > 0:
        jump_pct = abs((close / prev_close) - 1.0) * 100.0
        if jump_pct > float(config.MICRO_MAX_BAR_JUMP_PCT):
            return False, "micro_jump_too_large"
    if vol is None or vol < float(config.MICRO_MIN_BAR_VOLUME):
        return False, "micro_volume_too_low"
    return True, None


def spread_depth_allows(symbol: str) -> tuple[bool, str | None]:
    if not config.MICRO_USE_SPREAD_DEPTH_GUARD:
        return True, None
    try:
        from tr_bot.exchange import get_ccxt_exchange

        ex = get_ccxt_exchange()
        ob = ex.fetch_order_book(symbol, limit=5)
    except Exception:
        return False, "micro_orderbook_unavailable"
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    if not bids or not asks:
        return False, "micro_orderbook_empty"
    bid = float(bids[0][0])
    ask = float(asks[0][0])
    if bid <= 0 or ask <= 0 or ask < bid:
        return False, "micro_invalid_top_of_book"
    mid = (bid + ask) / 2.0
    spread_pct = ((ask - bid) / mid) * 100.0 if mid > 0 else 100.0
    if spread_pct > float(config.MICRO_MAX_SPREAD_PCT):
        return False, "micro_spread_too_wide"
    depth = 0.0
    for px, amt in bids[:5]:
        depth += float(px) * float(amt)
    for px, amt in asks[:5]:
        depth += float(px) * float(amt)
    if depth < float(config.MICRO_MIN_TOP_DEPTH_NOTIONAL):
        return False, "micro_depth_too_thin"
    return True, None


def apply_post_decision_gates(
    *,
    decision: str,
    reason: str | None,
    engine_mode: str,
    regime: str,
    signal: str,
    regime_confidence: float,
    cooldown_active: bool,
    last: Any,
    prev: Any,
    df: Any,
    symbol: str,
    paper_trading: bool | None = None,
    signal_strength_fn: Callable[[str, str, Any], tuple[bool, str | None]] | None = None,
    microstructure_fn: Callable[[Any, Any], tuple[bool, str | None]] | None = None,
    spread_depth_fn: Callable[[str], tuple[bool, str | None]] | None = None,
    classify_volatility_fn: Callable[[Any], tuple[str, Any]] | None = None,
) -> tuple[str, str | None]:
    """
    Apply shared post-generator gates. Returns ``(decision, reason)``.

    Reason strings (stable for tests/audit):
    ``low_regime_confidence``, ``weak_signal:…``, ``cooldown_active``,
    microstructure reasons / ``microstructure_guard_blocked``,
    spread reasons / ``spread_depth_guard_blocked``, ``vol_extreme_suppress``.
    """
    paper = config.PAPER_TRADING if paper_trading is None else bool(paper_trading)
    strength_fn = signal_strength_fn or signal_strength_allows
    micro_fn = microstructure_fn or microstructure_allows
    spread_fn = spread_depth_fn or spread_depth_allows

    mode = str(engine_mode or "legacy").lower()
    out_decision = decision
    out_reason = reason

    if mode != "smc":
        if float(regime_confidence) < float(config.REGIME_MIN_CONFIDENCE):
            out_decision = "hold"
            out_reason = "low_regime_confidence"

        strength_ok, strength_reason = strength_fn(regime, signal, last)
        if out_decision != "hold" and not strength_ok:
            out_decision = "hold"
            out_reason = (
                f"weak_signal:{strength_reason}" if strength_reason else "weak_signal"
            )

    if out_decision != "hold" and cooldown_active:
        out_decision = "hold"
        out_reason = "cooldown_active"

    if out_decision != "hold":
        micro_ok, micro_reason = micro_fn(last, prev)
        if not micro_ok:
            out_decision = "hold"
            out_reason = micro_reason or "microstructure_guard_blocked"
        elif not paper:
            spread_ok, spread_reason = spread_fn(symbol)
            if not spread_ok:
                out_decision = "hold"
                out_reason = spread_reason or "spread_depth_guard_blocked"
        if out_decision != "hold":
            try:
                if classify_volatility_fn is not None:
                    vr, _ = classify_volatility_fn(df)
                else:
                    from tr_bot.volatility_regime import classify_volatility

                    vr, _ = classify_volatility(df)
                if vr == "extreme":
                    out_decision = "hold"
                    out_reason = "vol_extreme_suppress"
            except Exception:
                pass

    return out_decision, out_reason

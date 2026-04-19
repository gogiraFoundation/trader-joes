"""Deployment tier helpers: canary symbol subset and notional caps."""

from __future__ import annotations

from tr_bot import config


def canary_allows_symbol(symbol: str) -> bool:
    """When live_canary and CANARY_SYMBOLS is non-empty, only listed symbols trade."""
    if config.DEPLOYMENT_TIER != "live_canary":
        return True
    raw = (config.CANARY_SYMBOLS or "").strip()
    if not raw:
        return True
    allowed = {s.strip() for s in raw.split(",") if s.strip()}
    return symbol in allowed


def apply_canary_notional_cap(size: float, close_px: float, quote_usdt: float) -> float:
    """
    Cap position size in base units so estimated notional in USDT does not exceed
    CANARY_MAX_NOTIONAL_USDT (live_canary only; 0 = disabled).
    """
    cap = float(config.CANARY_MAX_NOTIONAL_USDT)
    if config.DEPLOYMENT_TIER != "live_canary" or cap <= 0:
        return size
    if size <= 0 or close_px <= 0 or quote_usdt <= 0:
        return size
    nu = size * close_px * quote_usdt
    if nu <= cap:
        return size
    return cap / (close_px * quote_usdt)

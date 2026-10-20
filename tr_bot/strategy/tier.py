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


def _cap_size_by_notional(
    size: float,
    close_px: float,
    quote_usdt: float,
    cap_usdt: float,
) -> float:
    """Shrink base size so size * close * quote_usdt <= cap_usdt. No-op if cap <= 0."""
    if cap_usdt <= 0 or size <= 0 or close_px <= 0 or quote_usdt <= 0:
        return size
    nu = size * close_px * quote_usdt
    if nu <= cap_usdt:
        return size
    return cap_usdt / (close_px * quote_usdt)


def atr_meets_min_pct(atr: float, close_px: float, min_atr_pct: float | None = None) -> bool:
    """
    True if ATR is usable for costing (atr/close >= MIN_ATR_PCT).
    When min_atr_pct is 0 / unset, always True. Invalid atr/close → False.
    """
    floor = float(config.MIN_ATR_PCT if min_atr_pct is None else min_atr_pct)
    if floor <= 0:
        return True
    if atr <= 0 or close_px <= 0:
        return False
    return (atr / close_px) >= floor


def apply_canary_notional_cap(size: float, close_px: float, quote_usdt: float) -> float:
    """
    Cap position size in base units so estimated notional in USDT does not exceed
    CANARY_MAX_NOTIONAL_USDT (live_canary only; 0 = disabled).
    """
    if config.DEPLOYMENT_TIER != "live_canary":
        return size
    return _cap_size_by_notional(size, close_px, quote_usdt, float(config.CANARY_MAX_NOTIONAL_USDT))


def apply_live_notional_cap(size: float, close_px: float, quote_usdt: float) -> float:
    """
    Cap position size so estimated notional in USDT does not exceed LIVE_MAX_NOTIONAL_USDT.
    No-op when paper or when LIVE_MAX_NOTIONAL_USDT <= 0.
    """
    if config.PAPER_TRADING:
        return size
    cap = float(getattr(config, "LIVE_MAX_NOTIONAL_USDT", 0.0) or 0.0)
    return _cap_size_by_notional(size, close_px, quote_usdt, cap)


def apply_paper_notional_cap(size: float, close_px: float, quote_usdt: float) -> float:
    """
    Cap position size so estimated notional in USDT does not exceed PAPER_MAX_NOTIONAL_USDT.
    Paper only; 0 = disabled.
    """
    if not config.PAPER_TRADING:
        return size
    cap = float(getattr(config, "PAPER_MAX_NOTIONAL_USDT", 0.0) or 0.0)
    return _cap_size_by_notional(size, close_px, quote_usdt, cap)


def apply_backtest_notional_cap(size: float, close_px: float, quote_usdt: float) -> float:
    """
    Apply PAPER_MAX_NOTIONAL_USDT during backtest when set (>0) for paper-soak parity.
    """
    cap = float(getattr(config, "PAPER_MAX_NOTIONAL_USDT", 0.0) or 0.0)
    return _cap_size_by_notional(size, close_px, quote_usdt, cap)

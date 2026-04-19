"""Multi-symbol target weights and per-symbol risk budget."""

from __future__ import annotations

import logging

from tr_bot import config
from tr_bot.managers.security import is_valid_ccxt_spot_symbol

logger = logging.getLogger("tr_bot.portfolio")


def parse_portfolio_weights() -> dict[str, float]:
    """
    PORTFOLIO_WEIGHTS format: BTC/USDT=0.6,ETH/USDT=0.4
    Weights are normalized to sum 1.0.
    """
    raw = (config.PORTFOLIO_WEIGHTS or "").strip()
    if not config.PORTFOLIO_MODE or not raw:
        sym = config.SYMBOL.strip()
        if not is_valid_ccxt_spot_symbol(sym):
            logger.warning("Invalid SYMBOL for portfolio validation: %s", sym)
        return {sym: 1.0}

    out: dict[str, float] = {}
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        if "=" not in p:
            raise ValueError(f"Invalid portfolio segment (need SYMBOL=weight): {p!r}")
        sym, w_s = p.split("=", 1)
        sym = sym.strip()
        w = float(w_s.strip())
        if w < 0:
            raise ValueError(f"Negative weight: {p!r}")
        if not is_valid_ccxt_spot_symbol(sym):
            raise ValueError(f"Invalid CCXT spot symbol: {sym!r}")
        out[sym] = w

    if not out:
        raise ValueError("PORTFOLIO_WEIGHTS produced empty map")

    s = sum(out.values())
    if s <= 0:
        raise ValueError("Portfolio weights sum to zero")
    return {k: v / s for k, v in out.items()}


def active_symbols() -> list[str]:
    return list(parse_portfolio_weights().keys())


def weight_for(symbol: str) -> float:
    return parse_portfolio_weights().get(symbol, 0.0)

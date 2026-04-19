"""Spot balances via ccxt."""

from __future__ import annotations

from tr_bot.exchange import get_ccxt_exchange
from tr_bot.symbols import split_pair


def fetch_free_base_amount(symbol: str) -> float:
    """Free balance of the base asset (e.g. BTC for BTC/USDT)."""
    exchange = get_ccxt_exchange()
    base, _ = split_pair(symbol)
    exchange.load_markets()
    bal = exchange.fetch_balance()
    if base not in bal:
        return 0.0
    b = bal[base]
    if isinstance(b, dict):
        return float(b.get("free") or 0.0)
    return float(b or 0.0)


def fetch_total_base_amount(symbol: str) -> float:
    """Total balance of the base asset (free + used) for reconciliation."""
    exchange = get_ccxt_exchange()
    base, _ = split_pair(symbol)
    exchange.load_markets()
    bal = exchange.fetch_balance()
    if base not in bal:
        return 0.0
    b = bal[base]
    if isinstance(b, dict):
        return float(b.get("total") or 0.0)
    return float(b or 0.0)

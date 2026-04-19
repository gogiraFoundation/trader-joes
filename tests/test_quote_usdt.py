"""Quote→USDT helpers for position sizing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tr_bot.quote_usdt import (
    USDT_PEGGED,
    QuoteUsdtRateError,
    clear_quote_usdt_cache,
    usdt_per_quote_unit,
)


def test_usdt_pegged_is_one() -> None:
    for q in USDT_PEGGED:
        assert usdt_per_quote_unit(q) == 1.0


def test_usdt_pegged_case_insensitive() -> None:
    clear_quote_usdt_cache()
    assert usdt_per_quote_unit("usdt") == 1.0


def test_direct_quote_usdt_pair() -> None:
    clear_quote_usdt_cache()
    ex = MagicMock()
    ex.markets = {"ETH/USDT": {}}

    def fetch_ticker(sym: str) -> dict:
        assert sym == "ETH/USDT"
        return {"last": 2500.0, "close": 2500.0}

    ex.fetch_ticker = fetch_ticker
    ex.load_markets = MagicMock()
    assert usdt_per_quote_unit("ETH", _exchange=ex) == 2500.0


def test_inverted_usdt_quote_pair() -> None:
    clear_quote_usdt_cache()
    ex = MagicMock()
    ex.markets = {"USDT/JPY": {}}

    def fetch_ticker(sym: str) -> dict:
        assert sym == "USDT/JPY"
        return {"last": 150.0, "close": 150.0}

    ex.fetch_ticker = fetch_ticker
    ex.load_markets = MagicMock()
    # JPY per USDT = 150 → USDT per JPY = 1/150
    assert usdt_per_quote_unit("JPY", _exchange=ex) == pytest.approx(1.0 / 150.0)


def test_no_market_raises() -> None:
    clear_quote_usdt_cache()
    ex = MagicMock()
    ex.markets = {}
    ex.fetch_ticker = MagicMock()
    ex.load_markets = MagicMock()
    with pytest.raises(QuoteUsdtRateError):
        usdt_per_quote_unit("XYZUNKNOWN", _exchange=ex)


def test_cache_returns_same_instance() -> None:
    clear_quote_usdt_cache()
    ex = MagicMock()
    ex.markets = {"ETH/USDT": {}}
    calls: list[int] = []

    def fetch_ticker(sym: str) -> dict:
        calls.append(1)
        return {"last": 100.0}

    ex.fetch_ticker = fetch_ticker
    ex.load_markets = MagicMock()
    assert usdt_per_quote_unit("ETH", _exchange=ex) == 100.0
    assert usdt_per_quote_unit("ETH", _exchange=ex) == 100.0
    assert len(calls) == 1

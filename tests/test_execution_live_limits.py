"""Live buy prechecks, fill resolution, and paper execution helpers."""

from __future__ import annotations

from typing import Any

import pytest

from tr_bot import execution as ex


class _FakeExchange:
    def __init__(self, *, free_quote: float = 1000.0, ask: float = 100.0) -> None:
        self._free_quote = free_quote
        self._ask = ask
        self.orders: list[dict[str, Any]] = []

    def load_markets(self) -> dict:
        return {}

    def market(self, symbol: str) -> dict:
        return {
            "limits": {
                "amount": {"min": 0.0001},
                "cost": {"min": 5.0},
            }
        }

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return f"{amount:.8f}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        return f"{price:.2f}"

    def fetch_ticker(self, symbol: str) -> dict:
        return {"ask": self._ask, "last": self._ask, "close": self._ask}

    def fetch_balance(self) -> dict:
        return {"USDT": {"free": self._free_quote, "total": self._free_quote}}

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        order = {
            "id": "oid-1",
            "symbol": symbol,
            "type": type_,
            "side": side,
            "amount": amount,
            "filled": amount,
            "average": self._ask,
            "status": "closed",
            "clientOrderId": (params or {}).get("newClientOrderId"),
        }
        self.orders.append(order)
        return order


def test_resolve_fill_qty_prefers_filled() -> None:
    qty, src = ex.resolve_fill_qty({"filled": 0.5, "amount": 1.0}, requested=1.0)
    assert qty == pytest.approx(0.5)
    assert src == "order_fill"


def test_paper_execute_includes_filled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex.config, "PAPER_TRADING", True)
    monkeypatch.setattr(ex.config, "EXECUTION_STYLE", "market")
    order = ex.execute_trade("BTC/USDT", "buy", 0.01, client_order_id="tbtest")
    assert order["status"] == "simulated"
    assert order["filled"] == pytest.approx(0.01)
    assert order["clientOrderId"] == "tbtest"


def test_live_buy_rejects_insufficient_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeExchange(free_quote=1.0, ask=100.0)
    monkeypatch.setattr(ex.config, "PAPER_TRADING", False)
    monkeypatch.setattr(ex.config, "EXECUTION_STYLE", "market")
    monkeypatch.setattr(ex.config, "DEPLOYMENT_TIER", "live_full")
    monkeypatch.setattr(ex.config, "LIVE_MAX_NOTIONAL_USDT", 500.0)
    monkeypatch.setattr(ex.config, "LIVE_BALANCE_RESERVE_BPS", 20.0)
    monkeypatch.setattr(ex, "get_ccxt_exchange", lambda: fake)
    monkeypatch.setattr(ex, "call_ccxt_with_retries", lambda _ex, fn, **_k: fn())

    import tr_bot.account as account

    monkeypatch.setattr(account, "get_ccxt_exchange", lambda: fake)
    monkeypatch.setattr(
        "tr_bot.quote_usdt.usdt_per_quote_unit",
        lambda _q: 1.0,
    )

    # amount 0.1 * 100 = 10 USDT needed; free=1 -> reject
    out = ex.execute_trade("BTC/USDT", "buy", 0.1)
    assert out["status"] == "rejected"
    assert out["reason"] == "insufficient_quote_balance"
    assert fake.orders == []


def test_live_buy_rejects_over_live_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeExchange(free_quote=10_000.0, ask=100.0)
    monkeypatch.setattr(ex.config, "PAPER_TRADING", False)
    monkeypatch.setattr(ex.config, "EXECUTION_STYLE", "market")
    monkeypatch.setattr(ex.config, "DEPLOYMENT_TIER", "live_full")
    monkeypatch.setattr(ex.config, "LIVE_MAX_NOTIONAL_USDT", 50.0)
    monkeypatch.setattr(ex.config, "LIVE_BALANCE_RESERVE_BPS", 0.0)
    monkeypatch.setattr(ex, "get_ccxt_exchange", lambda: fake)
    monkeypatch.setattr(ex, "call_ccxt_with_retries", lambda _ex, fn, **_k: fn())
    monkeypatch.setattr(
        "tr_bot.quote_usdt.usdt_per_quote_unit",
        lambda _q: 1.0,
    )
    out = ex.execute_trade("BTC/USDT", "buy", 1.0)  # 100 USDT notional
    assert out["status"] == "rejected"
    assert out["reason"] == "exceeds_live_max_notional"


def test_live_buy_passes_and_sets_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeExchange(free_quote=10_000.0, ask=100.0)
    monkeypatch.setattr(ex.config, "PAPER_TRADING", False)
    monkeypatch.setattr(ex.config, "EXECUTION_STYLE", "market")
    monkeypatch.setattr(ex.config, "DEPLOYMENT_TIER", "live_full")
    monkeypatch.setattr(ex.config, "LIVE_MAX_NOTIONAL_USDT", 500.0)
    monkeypatch.setattr(ex.config, "LIVE_BALANCE_RESERVE_BPS", 0.0)
    monkeypatch.setattr(ex, "get_ccxt_exchange", lambda: fake)
    monkeypatch.setattr(ex, "call_ccxt_with_retries", lambda _ex, fn, **_k: fn())

    import tr_bot.account as account

    monkeypatch.setattr(account, "get_ccxt_exchange", lambda: fake)
    monkeypatch.setattr(
        "tr_bot.quote_usdt.usdt_per_quote_unit",
        lambda _q: 1.0,
    )
    out = ex.execute_trade("BTC/USDT", "buy", 0.1, client_order_id="tbcustom123")
    assert out["status"] == "closed"
    assert out["clientOrderId"] == "tbcustom123"
    assert out["filled"] == pytest.approx(0.1)


def test_apply_live_notional_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot.strategy import tier

    monkeypatch.setattr(tier.config, "PAPER_TRADING", False)
    monkeypatch.setattr(tier.config, "LIVE_MAX_NOTIONAL_USDT", 50.0)
    out = tier.apply_live_notional_cap(10.0, 100.0, 1.0)
    assert out == pytest.approx(0.5)

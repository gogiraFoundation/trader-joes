from __future__ import annotations

from unittest.mock import MagicMock

import ccxt

from tr_bot.binance_rest import (
    call_ccxt_with_retries,
    classify_ccxt_network_exc,
    retry_after_seconds,
    sleep_seconds_for_transient,
)
from tr_bot import execution
from tr_bot import config as cfg


def test_retry_after_seconds_int_header() -> None:
    assert retry_after_seconds({"Retry-After": "90"}) == 90.0


def test_retry_after_seconds_missing() -> None:
    assert retry_after_seconds(None) is None
    assert retry_after_seconds({}) is None


def test_classify_throttle_429() -> None:
    ex = MagicMock()
    ex.last_response_headers = {}
    e = ccxt.DDoSProtection("binance 429 Too Many Requests {}")
    assert classify_ccxt_network_exc(e, ex) == "throttle_429"


def test_classify_ban_418() -> None:
    ex = MagicMock()
    e = ccxt.DDoSProtection("binance 418 unused {}")
    assert classify_ccxt_network_exc(e, ex) == "ban_418"


def test_classify_server_5xx() -> None:
    ex = MagicMock()
    e = ccxt.ExchangeNotAvailable("binance 503 Service Unavailable")
    assert classify_ccxt_network_exc(e, ex) == "server_5xx"


def test_sleep_seconds_ban_uses_floor(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "BINANCE_HTTP_BASE_DELAY_S", 1.0)
    monkeypatch.setattr(cfg, "BINANCE_HTTP_MAX_DELAY_S", 300.0)
    ex = MagicMock()
    ex.last_response_headers = None
    s = sleep_seconds_for_transient("ban_418", ex, 0)
    assert s >= 60.0


def test_call_ccxt_with_retries_succeeds_second_attempt(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "BINANCE_HTTP_MAX_RETRIES", 3)
    monkeypatch.setattr(cfg, "BINANCE_HTTP_BASE_DELAY_S", 0.01)
    monkeypatch.setattr(cfg, "BINANCE_HTTP_MAX_DELAY_S", 1.0)
    ex = MagicMock()
    ex.last_response_headers = {}
    n = {"i": 0}

    def fn():
        n["i"] += 1
        if n["i"] < 2:
            raise ccxt.DDoSProtection("binance 429 test")
        return "ok"

    assert call_ccxt_with_retries(ex, fn, context="test") == "ok"


def test_execute_trade_market_passes_new_client_order_id(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "PAPER_TRADING", False)
    monkeypatch.setattr(cfg, "EXECUTION_STYLE", "market")
    captured: dict = {}

    class Ex:
        def load_markets(self) -> None:
            return None

        def amount_to_precision(self, sym: str, a: float) -> str:
            return str(a)

        def create_order(self, symbol, typ, side, amount, price, params):
            captured.update(params or {})
            return {"id": "42", "status": "closed", "filled": amount}

    monkeypatch.setattr(execution, "get_ccxt_exchange", lambda: Ex())
    monkeypatch.setattr(
        "tr_bot.binance_rest.call_ccxt_with_retries",
        lambda exchange, fn, context="", max_retries=None: fn(),
    )
    out = execution.execute_trade("BTC/USDT", "buy", 0.001)
    assert captured.get("newClientOrderId", "").startswith("tb")
    assert len(str(captured.get("newClientOrderId", ""))) <= 36
    assert out.get("status") == "closed"

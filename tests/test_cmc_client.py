"""CoinMarketCap shared HTTP client."""

from __future__ import annotations

import pytest

from tr_bot.analysis.cmc_client import cmc_pro_get


def test_cmc_pro_get_requires_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        cmc_pro_get("https://pro-api.coinmarketcap.com/v1/test", api_key="", params={})


def test_cmc_pro_get_retries_429_then_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot.analysis import cmc_client

    calls = {"n": 0}

    class Resp:
        def __init__(self, code: int) -> None:
            self.status_code = code
            self.headers = {"Retry-After": "0"}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 2:
            return Resp(429)
        r = Resp(200)
        r.headers = {}
        return r

    monkeypatch.setattr(cmc_client.requests, "get", fake_get)
    monkeypatch.setattr(cmc_client.time, "sleep", lambda _x: None)

    out = cmc_pro_get(
        "https://pro-api.coinmarketcap.com/v1/ping",
        api_key="k",
        params={},
        max_attempts=5,
    )
    assert out.status_code == 200
    assert calls["n"] == 2

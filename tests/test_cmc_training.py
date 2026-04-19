"""CoinMarketCap training OHLCV client."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from tr_bot.training.cmc import clear_cmc_map_cache, fetch_cmc_ohlcv, resolve_train_symbols_to_ids


@pytest.fixture(autouse=True)
def _clear_map_cache() -> None:
    clear_cmc_map_cache()
    yield
    clear_cmc_map_cache()


def test_fetch_cmc_ohlcv_uses_time_period_and_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "status": {"error_code": 0},
                "data": {
                    "quotes": [
                        {
                            "time_open": "2024-01-01T00:00:00.000Z",
                            "quote": {
                                "USD": {
                                    "open": 1,
                                    "high": 2,
                                    "low": 0.5,
                                    "close": 1.5,
                                    "volume": 100,
                                }
                            },
                        }
                    ]
                },
            }

    def fake_cmc_pro_get(url: str, *, api_key: str, params: dict[str, Any] | None, timeout: float):
        captured["url"] = url
        captured["params"] = dict(params or {})
        captured["api_key"] = api_key
        return Resp()

    monkeypatch.setattr("tr_bot.training.cmc.cmc_pro_get", fake_cmc_pro_get)

    df = fetch_cmc_ohlcv(
        convert="USD",
        interval="1h",
        count=10,
        api_key="secret",
        cmc_id=1,
    )
    assert captured["params"].get("time_period") == "1h"
    assert captured["params"].get("id") == "1"
    assert "symbol" not in captured["params"]
    assert isinstance(df, pd.DataFrame)
    assert df["asset"].iloc[0] == "1"
    assert df["source"].iloc[0] == "coinmarketcap"


def test_resolve_train_symbols_to_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "status": {"error_code": 0},
                "data": [
                    {"id": 1, "symbol": "BTC", "slug": "bitcoin"},
                    {"id": 1027, "symbol": "ETH", "slug": "ethereum"},
                ],
            }

    monkeypatch.setattr("tr_bot.training.cmc.cmc_pro_get", lambda *a, **k: Resp())

    m = resolve_train_symbols_to_ids(["BTC", "ETH"], "key")
    assert m["BTC"] == 1
    assert m["ETH"] == 1027


def test_fetch_cmc_ohlcv_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "status": {"error_code": 0},
                "data": {
                    "quotes": [
                        {
                            "time_open": "2024-01-02T00:00:00.000Z",
                            "quote": {"USD": {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}},
                        }
                    ]
                },
            }

    def fake_get(*_a, **kw):
        captured["params"] = dict(kw.get("params") or {})
        return Resp()

    monkeypatch.setattr("tr_bot.training.cmc.cmc_pro_get", fake_get)

    df = fetch_cmc_ohlcv(
        convert="USD",
        interval="1d",
        count=5,
        api_key="k",
        slug="bitcoin",
    )
    assert captured["params"].get("slug") == "bitcoin"
    assert captured["params"].get("time_period") == "daily"
    assert df["asset"].iloc[0] == "bitcoin"

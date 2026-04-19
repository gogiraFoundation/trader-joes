"""Unit tests: CoinDesk JSON normalization."""

from __future__ import annotations

from tr_bot.coindesk.normalize import parse_latest_ticks, parse_ohlcv_payload


def test_parse_latest_ticks_cc_style() -> None:
    payload = {
        "Data": {
            "BTC-USD": {
                "instrument": "BTC-USD",
                "timestamp": 1_700_000_000_000,
                "price": 42000.5,
                "volume": 1.5,
                "ccseq": 10,
            }
        }
    }
    rows = parse_latest_ticks(payload, market="ccix")
    assert len(rows) == 1
    assert rows[0]["instrument"] == "BTC-USD"
    assert rows[0]["ccseq"] == 10
    assert rows[0]["timestamp_ms"] == 1_700_000_000_000


def test_parse_latest_ticks_cc_style_last_update_underscore() -> None:
    payload = {
        "Data": {
            "BTC-USD": {
                "instrument": "BTC-USD",
                "LAST_UPDATE": 1_704_000_000,
                "PRICE": 99.0,
            }
        }
    }
    rows = parse_latest_ticks(payload, market="ccix")
    assert len(rows) == 1
    assert rows[0]["timestamp_ms"] == 1_704_000_000_000


def test_parse_latest_ticks_nested_cc_timestamp() -> None:
    payload = {
        "Data": {
            "ETH-USD": {
                "CC": {"LASTUPDATE": 1_705_000_000, "PRICE": 2000.0},
            }
        }
    }
    rows = parse_latest_ticks(payload, market="ccix")
    assert len(rows) == 1
    assert rows[0]["price"] == 2000.0


def test_parse_spot_latest_tick_official_schema() -> None:
    """Shape from https://developers.coindesk.com/documentation/data-api/spot_v1_latest_tick"""
    payload = {
        "Data": {
            "BTC-USD": {
                "TYPE": "706",
                "MARKET": "coinbase",
                "INSTRUMENT": "BTC-USD",
                "MAPPED_INSTRUMENT": "BTC-USD",
                "CCSEQ": 42,
                "PRICE": 97234.5,
                "PRICE_LAST_UPDATE_TS": 1_704_000_000,
                "PRICE_LAST_UPDATE_TS_NS": 123_456_789,
                "LAST_TRADE_QUANTITY": 0.01,
            },
            "ETH-USD": {
                "MAPPED_INSTRUMENT": "ETH-USD",
                "CCSEQ": 43,
                "PRICE": 3500.0,
                "PRICE_LAST_UPDATE_TS": 1_704_000_001,
                "PRICE_LAST_UPDATE_TS_NS": 0,
                "LAST_TRADE_QUANTITY": 0.5,
            },
        }
    }
    rows = parse_latest_ticks(payload, market="coinbase")
    assert len(rows) == 2
    by_inst = {r["instrument"]: r for r in rows}
    assert by_inst["BTC-USD"]["timestamp_ms"] == 1_704_000_000_000 + 123_456_789 // 1_000_000
    assert by_inst["BTC-USD"]["ccseq"] == 42
    assert by_inst["BTC-USD"]["price"] == 97234.5
    assert by_inst["BTC-USD"]["volume"] == 0.01
    assert by_inst["ETH-USD"]["ccseq"] == 43


def test_parse_latest_ticks_fallback_ingest_time_when_price_only() -> None:
    payload = {
        "Data": {
            "BTC-USD": {"instrument": "BTC-USD", "PRICE": 50000.0},
        }
    }
    rows = parse_latest_ticks(payload, market="ccix")
    assert len(rows) == 1
    assert rows[0]["price"] == 50000.0
    assert rows[0]["timestamp_ms"] > 1_000_000_000_000


def test_parse_ohlcv_histominute_style() -> None:
    payload = {
        "Data": [
            {
                "time": 1_700_000_000,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volumefrom": 100,
            }
        ]
    }
    rows = parse_ohlcv_payload(payload, market="ccix", instrument="BTC-USD", timeframe="1d")
    assert len(rows) == 1
    assert rows[0]["close"] == 1.5
    assert rows[0]["volume"] == 100.0

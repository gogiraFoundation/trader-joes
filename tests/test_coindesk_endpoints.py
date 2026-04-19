"""Regression tests for CoinDesk OHLCV URL segments and query params."""

from tr_bot import config
from tr_bot.coindesk.endpoints import (
    build_historical_ohlcv_params,
    default_historical_ohlcv_path,
)


def test_default_historical_path_uses_plural_segment_not_singular_day() -> None:
    # CoinDesk Index CC uses /historical/days|hours|minutes — singular .../day returns 404.
    p = default_historical_ohlcv_path("1d")
    assert p.endswith("/historical/days")


def test_default_historical_path_minutes_aggregate() -> None:
    p = default_historical_ohlcv_path("5m")
    assert "/historical/minutes" in p


def test_build_historical_ohlcv_params_index_style() -> None:
    params = build_historical_ohlcv_params("cadli", "BTC-USD", "1d", 2000, None)
    assert params["market"] == "cadli"
    assert params["instrument"] == "BTC-USD"
    assert params["limit"] == 2000
    assert params["aggregate"] == 1
    assert "groups" in params  # from default COINDESK_OHLCV_GROUPS
    assert params["fill"] == str(config.COINDESK_HISTORICAL_FILL).lower()
    assert params["apply_mapping"] == str(config.COINDESK_APPLY_MAPPING).lower()
    assert params["response_format"] == config.COINDESK_HISTORICAL_RESPONSE_FORMAT


def test_build_historical_ohlcv_params_caps_limit_at_5000() -> None:
    params = build_historical_ohlcv_params("cadli", "BTC-USD", "1d", 9000, None)
    assert params["limit"] == 5000


def test_build_historical_ohlcv_params_includes_to_ts() -> None:
    params = build_historical_ohlcv_params("ccix", "ETH-USD", "1h", 500, "1733702400")
    assert params["to_ts"] == "1733702400"
    assert params["aggregate"] == 1
    assert params["fill"] == str(config.COINDESK_HISTORICAL_FILL).lower()
    assert params["apply_mapping"] == str(config.COINDESK_APPLY_MAPPING).lower()
    assert params["response_format"] == config.COINDESK_HISTORICAL_RESPONSE_FORMAT

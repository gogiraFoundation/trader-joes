"""Unit tests for CoinDesk index reference data (markets, mapped instruments, EOD)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import tr_bot.config as config
from tr_bot.coindesk import reference as ref
from tr_bot.startup.preflight import run_preflight_checks


@pytest.fixture(autouse=True)
def _clear_ref_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    ref._cache.clear()
    monkeypatch.setattr(config, "COINDESK_REFERENCE_CACHE_TTL_S", 0.0)


def test_fetch_mapped_instruments_batches_30_into_two_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_MAPPED_INSTRUMENTS_CHUNK", 25)
    calls: list[tuple[str, dict]] = []

    class Fake:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            calls.append((path, dict(params)))
            insts = params["instruments"].split(",")
            return {
                "Data": {
                    i: {"MAPPED_INSTRUMENT": i, "INSTRUMENT_STATUS": "ACTIVE"} for i in insts
                }
            }

    syms = [f"S{i}" for i in range(30)]
    out = ref.fetch_mapped_instruments(Fake(), market="cadli", instruments=syms)
    assert len(calls) == 2
    assert len(calls[0][1]["instruments"].split(",")) == 25
    assert len(calls[1][1]["instruments"].split(",")) == 5
    data = out.get("Data") or {}
    assert len(data) == 30


def test_validate_configured_instruments_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "cadli")
    monkeypatch.setattr(config, "COINDESK_INSTRUMENTS", "BTC-USD")

    class Fake:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            if "v2/markets" in path:
                return {"Data": [{"MARKET": "cadli", "STATUS": "ACTIVE"}]}
            return {"Data": {"BTC-USD": {"MAPPED_INSTRUMENT": "BTC-USD", "INSTRUMENT_STATUS": "ACTIVE"}}}

    v = ref.validate_configured_instruments(Fake(), instruments=["BTC-USD"])
    assert v["ok"] is True
    assert v["issues"] == []


def test_fetch_mapped_instruments_flattens_market_nested_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "ccix")

    class Fake:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            if "instruments" not in params:
                return {"Data": [{"MARKET": "ccix", "STATUS": "ACTIVE"}]}
            return {
                "Data": {
                    "ccix": {
                        "BTC-USD": {"MAPPED_INSTRUMENT": "BTC-USD", "INSTRUMENT_STATUS": "ACTIVE"},
                        "ETH-USD": {"MAPPED_INSTRUMENT": "ETH-USD", "INSTRUMENT_STATUS": "ACTIVE"},
                    }
                }
            }

    out = ref.fetch_mapped_instruments(Fake(), market="ccix", instruments=["BTC-USD", "ETH-USD"])
    data = out.get("Data") or {}
    assert "BTC-USD" in data and "ETH-USD" in data
    assert "ccix" not in data
    v = ref.validate_configured_instruments(Fake(), market="ccix", instruments=["BTC-USD", "ETH-USD"])
    assert v["ok"] is True
    assert v["issues"] == []


def test_validate_ccix_instruments_list_under_market_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "ccix")

    class Fake:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            if "instruments" not in params:
                return {"Data": [{"MARKET": "ccix", "STATUS": "ACTIVE"}]}
            return {
                "Data": {
                    "ccix": {
                        "instruments": [
                            {"MAPPED_INSTRUMENT": "BTC-USD", "INSTRUMENT_STATUS": "ACTIVE"},
                            {"MAPPED_INSTRUMENT": "ETH-USD", "INSTRUMENT_STATUS": "ACTIVE"},
                        ],
                        "schema_version": 1,
                    }
                }
            }

    v = ref.validate_configured_instruments(
        Fake(), market="ccix", instruments=["BTC-USD", "ETH-USD"]
    )
    assert v["ok"] is True
    out = ref.fetch_mapped_instruments(Fake(), market="ccix", instruments=["BTC-USD", "ETH-USD"])
    data = out.get("Data") or {}
    assert "BTC-USD" in data and "ETH-USD" in data
    assert "ccix" not in data


def test_validate_ccix_data_wrapper_and_scalar_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "ccix")

    class Fake:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            if "instruments" not in params:
                return {"Data": [{"MARKET": "ccix", "STATUS": "ACTIVE"}]}
            return {
                "Data": {
                    "ccix": {
                        "data": {
                            "BTC-USD": {"MAPPED_INSTRUMENT": "BTC-USD", "INSTRUMENT_STATUS": "ACTIVE"},
                            "ETH-USD": {"MAPPED_INSTRUMENT": "ETH-USD", "INSTRUMENT_STATUS": "ACTIVE"},
                        },
                        "version": "1",
                    }
                }
            }

    v = ref.validate_configured_instruments(
        Fake(), market="ccix", instruments=["BTC-USD", "ETH-USD"]
    )
    assert v["ok"] is True


def test_validate_ccix_instrument_key_and_mapped_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """API may use BTC_USD keys or lowercase mapped symbols while .env uses BTC-USD."""
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "ccix")

    class Fake:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            if "instruments" not in params:
                return {"Data": [{"MARKET": "ccix", "STATUS": "ACTIVE"}]}
            return {
                "Data": {
                    "BTC_USD": {"MAPPED_INSTRUMENT": "btc-usd", "INSTRUMENT_STATUS": "ACTIVE"},
                }
            }

    v = ref.validate_configured_instruments(Fake(), market="ccix", instruments=["BTC-USD"])
    assert v["ok"] is True
    assert v["issues"] == []


def test_validate_finds_instruments_under_wrapper_and_opaque_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "ccix")

    class Fake:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            if "instruments" not in params:
                return {"Data": [{"MARKET": "ccix", "STATUS": "ACTIVE"}]}
            return {
                "Data": {
                    "instruments": {
                        "id-1": {"MAPPED_INSTRUMENT": "BTC-USD", "INSTRUMENT_STATUS": "ACTIVE"},
                        "id-2": {"MAPPED_INSTRUMENT": "ETH-USD", "INSTRUMENT_STATUS": "ACTIVE"},
                    }
                }
            }

    v = ref.validate_configured_instruments(
        Fake(), market="ccix", instruments=["BTC-USD", "ETH-USD"]
    )
    assert v["ok"] is True
    assert v["mapped_instrument_keys_sample"] == []


def test_validate_configured_instruments_missing() -> None:
    class Fake:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            if "v2/markets" in path:
                return {"Data": [{"MARKET": "cadli", "STATUS": "ACTIVE"}]}
            return {"Data": {}}

    v = ref.validate_configured_instruments(Fake(), market="cadli", instruments=["BTC-USD"])
    assert v["ok"] is False
    assert any("missing:BTC-USD" in x for x in v["issues"])


def test_fetch_eod_day_bars_clamps_limit() -> None:
    captured: dict = {}

    class C:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            captured.update(params)
            return {"Data": []}

    ref.fetch_eod_day_bars(C(), market="m", instrument="i", limit=90)
    assert captured["limit"] == 50


def test_effective_reference_paths_index_ccix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "ccix")
    monkeypatch.setattr(config, "COINDESK_INDEX_MARKETS_PATH_EXPLICIT", False)
    monkeypatch.setattr(config, "COINDESK_MAPPED_INSTRUMENTS_PATH_EXPLICIT", False)
    assert config.coindesk_effective_index_markets_path() == "/index/cc/v2/markets"
    assert config.coindesk_effective_mapped_instruments_path() == "/index/cc/v1/markets/instruments"


def test_effective_reference_paths_spot_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "coinbase")
    monkeypatch.setattr(config, "COINDESK_REFERENCE_API_MODE", "auto")
    monkeypatch.setattr(config, "COINDESK_INDEX_MARKETS_PATH_EXPLICIT", False)
    monkeypatch.setattr(config, "COINDESK_MAPPED_INSTRUMENTS_PATH_EXPLICIT", False)
    assert config.coindesk_effective_index_markets_path() == "/spot/v1/markets"
    assert config.coindesk_effective_mapped_instruments_path() == "/spot/v1/markets/instruments"


def test_ccix_strict_rejects_spot_tick_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "ccix")
    monkeypatch.setattr(config, "COINDESK_LATEST_TICK_PATH", "/spot/v1/latest/tick")
    monkeypatch.setattr(config, "COINDESK_OHLCV_PATH", "")
    monkeypatch.setattr(config, "COINDESK_CCIX_STRICT_VALIDATION", True)
    errs = config.coindesk_runtime_validation_errors()
    assert any("ccix_requires_index_tick_path" in e for e in errs)


def test_ccix_strict_rejects_reference_mode_spot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_DEFAULT_MARKET", "ccix")
    monkeypatch.setattr(config, "COINDESK_REFERENCE_API_MODE", "spot")
    monkeypatch.setattr(config, "COINDESK_LATEST_TICK_PATH", "/index/cc/v1/latest/tick")
    monkeypatch.setattr(config, "COINDESK_OHLCV_PATH", "")
    monkeypatch.setattr(config, "COINDESK_CCIX_STRICT_VALIDATION", True)
    errs = config.coindesk_runtime_validation_errors()
    assert any("spot_incompatible_with_index_market" in e for e in errs)


def test_preflight_strict_coindesk_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "COINDESK_REFERENCE_CHECK_ENABLED", True)
    monkeypatch.setattr(config, "COINDESK_API_KEY", "test-key")
    monkeypatch.setattr(config, "COINDESK_REFERENCE_STRICT", True)
    monkeypatch.setattr(config, "DEPLOYMENT_TIER", "paper")
    monkeypatch.setattr(config, "RISK_PER_TRADE", 0.01)
    monkeypatch.setattr(config, "MAX_TRADES_PER_DAY", 5)
    monkeypatch.setattr(
        "tr_bot.startup.preflight.check_redis",
        lambda url: {"ok": True, "latency_ms": 0.0},
    )
    monkeypatch.setattr(
        "tr_bot.startup.preflight.check_postgres",
        lambda url: {"ok": True, "latency_ms": 0.0},
    )
    monkeypatch.setattr(config, "readiness_require_postgres", lambda: False)

    with patch.object(
        ref,
        "validate_configured_instruments",
        return_value={"ok": False, "issues": ["missing:BTC-USD"]},
    ):
        out = run_preflight_checks()

    assert "coindesk_instruments_not_active" in (out.get("errors") or [])
    assert any("coindesk_reference" in w for w in (out.get("warnings") or []))

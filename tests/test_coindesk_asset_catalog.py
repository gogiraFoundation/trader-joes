"""Tests for CoinDesk asset top list snapshot helpers."""

from __future__ import annotations

import pytest

import tr_bot.config as config
from tr_bot.coindesk import asset_catalog as ac


def test_build_top_list_snapshot_from_payload() -> None:
    payload = {
        "Data": [
            {"SYMBOL": "BTC", "TOPLIST_RANK": 1},
            {"SYMBOL": "ETH", "TOPLIST_RANK": 2},
            {"SYMBOL": "SOL", "TOPLIST_RANK": 3},
        ]
    }
    snap = ac.build_top_list_snapshot_from_payload(payload)
    assert snap["row_count"] == 3
    assert len(snap["top_rows"]) >= 1
    assert snap["watched_ranks"].get("BTC") == 1
    assert snap["watched_ranks"].get("ETH") == 2


def test_watch_symbols_from_instruments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_INSTRUMENTS", "BTC-USD,ETH-USD")
    monkeypatch.setattr(config, "COINDESK_ASSET_TOP_LIST_WATCH_SYMBOLS", "")
    syms = ac.watch_symbols_for_top_list()
    assert "BTC" in syms and "ETH" in syms


def test_fetch_asset_top_list_uses_clamped_page_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_ASSET_TOP_LIST_PAGE_SIZE", 50)
    captured: dict = {}

    class Fake:
        def get(self, path: str, params: dict, endpoint_metric: str = "") -> dict:
            captured.update(params)
            return {"Data": []}

    ac.fetch_asset_top_list(Fake(), page_size=200)
    assert captured["page_size"] == 100


def test_refresh_snapshot_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COINDESK_API_KEY", "k")
    monkeypatch.setattr(config, "COINDESK_ASSET_TOP_LIST_ENABLED", False)
    out = ac.refresh_asset_top_list_snapshot()
    assert out.get("ok") is False
    assert out.get("error") == "disabled"

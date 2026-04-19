"""Optional live CoinDesk API smoke test (requires COINDESK_API_KEY)."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.coindesk_integration


@pytest.mark.skipif(not os.environ.get("COINDESK_API_KEY"), reason="COINDESK_API_KEY not set")
def test_live_latest_tick_smoke() -> None:
    from tr_bot.coindesk.client import CoinDeskClient
    from tr_bot.coindesk.endpoints import fetch_latest_ticks

    client = CoinDeskClient(api_key=os.environ["COINDESK_API_KEY"])
    rows = fetch_latest_ticks(client=client)
    assert isinstance(rows, list)

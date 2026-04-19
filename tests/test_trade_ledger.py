"""Trade ledger JSONL."""

from __future__ import annotations

from pathlib import Path

import pytest

from tr_bot import config
from tr_bot.trade_ledger import append_closed_trade, read_recent_trades


def test_append_and_read_recent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path))
    append_closed_trade({"id": 1, "net_pnl_usdt": 1.0})
    append_closed_trade({"id": 2, "net_pnl_usdt": 2.0})
    rows = read_recent_trades(10)
    assert len(rows) == 2
    assert rows[0]["id"] == 2
    assert rows[1]["id"] == 1

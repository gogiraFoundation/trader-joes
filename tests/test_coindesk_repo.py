"""Unit tests for tr_bot.db.coindesk_repo (connection probe + OHLCV upsert chunking)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tr_bot.db import coindesk_repo


def test_ping_propagates_rollback_failure() -> None:
    import psycopg2

    cur_cm = MagicMock()
    cur = MagicMock()
    cur_cm.__enter__ = MagicMock(return_value=cur)
    cur_cm.__exit__ = MagicMock(return_value=None)
    conn = MagicMock()
    conn.cursor.return_value = cur_cm
    conn.rollback.side_effect = psycopg2.InterfaceError("connection already closed")

    with pytest.raises(psycopg2.InterfaceError):
        coindesk_repo.ping(conn)

    cur.execute.assert_called_once_with("SELECT 1")
    conn.rollback.assert_called_once()


def test_ping_rollback_success() -> None:
    cur_cm = MagicMock()
    cur = MagicMock()
    cur_cm.__enter__ = MagicMock(return_value=cur)
    cur_cm.__exit__ = MagicMock(return_value=None)
    conn = MagicMock()
    conn.cursor.return_value = cur_cm

    coindesk_repo.ping(conn)

    conn.rollback.assert_called_once()


def test_upsert_ohlcv_rows_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.COINDESK_OHLCV_UPSERT_CHUNK", 2)

    rows = [
        {
            "timestamp_ms": 1_700_000_000_000 + i * 60_000,
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "volume": 10.0,
            "market": "cadli",
            "instrument": "BTC-USD",
            "timeframe": "1h",
            "ccseq": None,
            "source": "coindesk",
        }
        for i in range(5)
    ]

    batch_sizes: list[int] = []

    def fake_execute_values(cur: object, sql: str, batch: list) -> None:
        batch_sizes.append(len(batch))

    conn = MagicMock()
    cur_cm = MagicMock()
    cur = MagicMock()
    cur_cm.__enter__ = MagicMock(return_value=cur)
    cur_cm.__exit__ = MagicMock(return_value=None)
    conn.cursor.return_value = cur_cm

    with patch.object(coindesk_repo, "ensure_schema", lambda c: None):
        with patch.object(coindesk_repo, "record_rows_written", lambda *a, **k: None):
            with patch("psycopg2.extras.execute_values", side_effect=fake_execute_values):
                n = coindesk_repo.upsert_ohlcv_rows(conn, rows)

    assert n == 5
    assert batch_sizes == [2, 2, 1]
    assert conn.commit.call_count == 3

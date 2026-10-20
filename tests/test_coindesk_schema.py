"""CoinDesk schema: plain PostgreSQL when Timescale is not installed."""

from __future__ import annotations

from unittest.mock import MagicMock

from tr_bot.db import coindesk_schema


def test_ensure_schema_skips_create_extension_when_not_installed() -> None:
    coindesk_schema.reset_timescale_probe_cache()

    cur = MagicMock()
    cur.fetchone.return_value = None  # timescaledb not in pg_extension
    cur_cm = MagicMock()
    cur_cm.__enter__ = MagicMock(return_value=cur)
    cur_cm.__exit__ = MagicMock(return_value=None)
    conn = MagicMock()
    conn.cursor.return_value = cur_cm

    coindesk_schema.ensure_coindesk_schema(conn)

    executed = [c.args[0].strip() for c in cur.execute.call_args_list if c.args]
    assert not any("CREATE EXTENSION" in sql for sql in executed)
    assert not any("create_hypertable" in sql for sql in executed)
    assert conn.commit.call_count >= 1


def test_ensure_schema_creates_hypertables_when_extension_installed() -> None:
    coindesk_schema.reset_timescale_probe_cache()

    cur = MagicMock()
    cur.fetchone.return_value = (1,)
    cur_cm = MagicMock()
    cur_cm.__enter__ = MagicMock(return_value=cur)
    cur_cm.__exit__ = MagicMock(return_value=None)
    conn = MagicMock()
    conn.cursor.return_value = cur_cm

    coindesk_schema.ensure_coindesk_schema(conn)

    hypertable_calls = [
        c
        for c in cur.execute.call_args_list
        if c.args and "create_hypertable" in str(c.args[0])
    ]
    assert len(hypertable_calls) == 3


def test_timescale_probe_cached_after_first_check() -> None:
    coindesk_schema.reset_timescale_probe_cache()

    cur = MagicMock()
    cur.fetchone.return_value = None
    cur_cm = MagicMock()
    cur_cm.__enter__ = MagicMock(return_value=cur)
    cur_cm.__exit__ = MagicMock(return_value=None)
    conn = MagicMock()
    conn.cursor.return_value = cur_cm

    assert coindesk_schema._timescale_extension_installed(conn) is False
    assert coindesk_schema._timescale_extension_installed(conn) is False
    # pg_extension probe runs once; second call uses cache (no extra fetchone).
    assert cur.execute.call_count == 1

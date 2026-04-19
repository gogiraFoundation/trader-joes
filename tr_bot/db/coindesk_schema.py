"""DDL and TimescaleDB hypertables for CoinDesk market data."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tr_bot.db.coindesk_schema")

_TABLE_DDL = [
    """
CREATE TABLE IF NOT EXISTS coindesk_ticks (
    time TIMESTAMPTZ NOT NULL,
    market TEXT NOT NULL,
    instrument TEXT NOT NULL,
    price DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    ccseq BIGINT,
    source TEXT NOT NULL DEFAULT 'coindesk',
    raw JSONB
)
""",
    "CREATE INDEX IF NOT EXISTS idx_coindesk_ticks_mkt_inst_time ON coindesk_ticks (market, instrument, time DESC)",
    """
CREATE TABLE IF NOT EXISTS coindesk_ohlcv (
    time TIMESTAMPTZ NOT NULL,
    market TEXT NOT NULL,
    instrument TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL DEFAULT 0,
    ccseq BIGINT,
    source TEXT NOT NULL DEFAULT 'coindesk'
)
""",
    """
CREATE UNIQUE INDEX IF NOT EXISTS uq_coindesk_ohlcv
ON coindesk_ohlcv (time, market, instrument, timeframe)
""",
    "CREATE INDEX IF NOT EXISTS idx_coindesk_ohlcv_inst_tf ON coindesk_ohlcv (instrument, timeframe, time DESC)",
    """
CREATE TABLE IF NOT EXISTS coindesk_orderbooks (
    time TIMESTAMPTZ NOT NULL,
    market TEXT NOT NULL,
    instrument TEXT NOT NULL,
    depth INTEGER,
    bids JSONB NOT NULL DEFAULT '[]'::jsonb,
    asks JSONB NOT NULL DEFAULT '[]'::jsonb,
    source TEXT NOT NULL DEFAULT 'coindesk'
)
""",
    "CREATE INDEX IF NOT EXISTS idx_coindesk_ob_inst_time ON coindesk_orderbooks (market, instrument, time DESC)",
    """
CREATE TABLE IF NOT EXISTS coindesk_instruments (
    id BIGSERIAL PRIMARY KEY,
    market TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    instrument_id TEXT,
    symbol TEXT,
    base_asset TEXT,
    quote_asset TEXT,
    instrument_type TEXT,
    raw JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dedupe_key)
)
""",
    """
CREATE TABLE IF NOT EXISTS coindesk_rate_limit_events (
    id BIGSERIAL PRIMARY KEY,
    ts_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    path TEXT,
    status_code INTEGER,
    limit_hdr TEXT,
    remaining_hdr TEXT,
    reset_hdr TEXT,
    retry_after TEXT
)
""",
    """
CREATE TABLE IF NOT EXISTS coindesk_ccseq_state (
    market TEXT NOT NULL,
    instrument TEXT NOT NULL,
    stream_type TEXT NOT NULL DEFAULT 'tick',
    last_ccseq BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (market, instrument, stream_type)
)
""",
]

_HYPERTABLES = [
    ("coindesk_ticks", "time"),
    ("coindesk_ohlcv", "time"),
    ("coindesk_orderbooks", "time"),
]


def ensure_coindesk_schema(conn: Any) -> None:
    """Create tables; enable Timescale hypertables when extension exists."""
    with conn.cursor() as cur:
        for stmt in _TABLE_DDL:
            cur.execute(stmt.strip())
    conn.commit()

    ext_ok = False
    with conn.cursor() as cur:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
            conn.commit()
            ext_ok = True
        except Exception as e:
            conn.rollback()
            logger.warning("TimescaleDB extension unavailable (%s); using plain PostgreSQL tables", e)

    if ext_ok:
        # One hypertable per transaction so a failure does not leave the session aborted.
        for table, time_col in _HYPERTABLES:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT create_hypertable('{table}', '{time_col}', if_not_exists => TRUE)"
                    )
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.warning("create_hypertable %s failed: %s", table, e)

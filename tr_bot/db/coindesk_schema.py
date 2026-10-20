"""DDL and TimescaleDB hypertables for CoinDesk market data."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tr_bot.db.coindesk_schema")

# None = unknown; True = pg_extension has timescaledb; False = plain PostgreSQL only.
_TIMESCALE_INSTALLED: bool | None = None


def _safe_rollback(conn: Any) -> None:
    try:
        if getattr(conn, "closed", 0):
            return
        conn.rollback()
    except Exception:
        pass


def _timescale_extension_installed(conn: Any) -> bool:
    """True when timescaledb is already installed (never attempt runtime CREATE EXTENSION)."""
    global _TIMESCALE_INSTALLED
    if _TIMESCALE_INSTALLED is not None:
        return _TIMESCALE_INSTALLED
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        _TIMESCALE_INSTALLED = cur.fetchone() is not None
    if not _TIMESCALE_INSTALLED:
        logger.info(
            "TimescaleDB extension not installed; CoinDesk tables use plain PostgreSQL "
            "(install timescaledb once via DB init to enable hypertables)"
        )
    return _TIMESCALE_INSTALLED


def reset_timescale_probe_cache() -> None:
    """Test helper: clear cached Timescale probe."""
    global _TIMESCALE_INSTALLED
    _TIMESCALE_INSTALLED = None


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
    """Create tables; enable Timescale hypertables only when extension is already installed.

    Runtime ``CREATE EXTENSION timescaledb`` can crash PostgreSQL when shared_preload_libraries
    is not configured (common on mis-provisioned hosts). Plain tables work without hypertables.
    """
    with conn.cursor() as cur:
        for stmt in _TABLE_DDL:
            cur.execute(stmt.strip())
    conn.commit()

    if not _timescale_extension_installed(conn):
        return

    for table, time_col in _HYPERTABLES:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT create_hypertable('{table}', '{time_col}', if_not_exists => TRUE)"
                )
            conn.commit()
        except Exception as e:
            _safe_rollback(conn)
            logger.warning("create_hypertable %s failed: %s", table, e)

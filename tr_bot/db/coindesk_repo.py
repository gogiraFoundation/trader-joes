"""Insert and query CoinDesk rows in PostgreSQL / TimescaleDB."""

from __future__ import annotations

import logging
import weakref
from datetime import datetime, timezone
from typing import Any, Sequence

from tr_bot import config
from tr_bot.coindesk.metrics import record_rows_written
from tr_bot.db.coindesk_schema import ensure_coindesk_schema

logger = logging.getLogger("tr_bot.db.coindesk_repo")

_SCHEMA_READY: weakref.WeakSet[Any] = weakref.WeakSet()


def connect() -> Any:
    import psycopg2

    # TCP keepalives reduce idle drops behind NAT / poolers while OHLCV rows are built or HTTP runs elsewhere.
    return psycopg2.connect(
        config.DATABASE_URL,
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def ping(conn: Any) -> None:
    """Cheap liveness check; fails fast if the session is unusable."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
    # Rollback ends the implicit read-only transaction; do not swallow errors — a failed rollback means dead session.
    conn.rollback()


def safe_close(conn: Any | None) -> None:
    if conn is None:
        return
    try:
        if getattr(conn, "closed", 0):
            return
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def is_recoverable_connection_error(exc: BaseException) -> bool:
    """True when a fresh connection + retry may succeed (pooler blip, server reset)."""
    import psycopg2

    if isinstance(exc, psycopg2.InterfaceError):
        return True
    if isinstance(exc, psycopg2.OperationalError):
        msg = str(exc).lower()
        if "connection" in msg and ("closed" in msg or "lost" in msg or "terminated" in msg):
            return True
    msg = str(exc).lower()
    return "connection already closed" in msg


def ensure_schema(conn: Any) -> None:
    """Run idempotent DDL once per connection (not process-global — avoids stale skip on new sessions)."""
    if conn in _SCHEMA_READY:
        return
    ensure_coindesk_schema(conn)
    _SCHEMA_READY.add(conn)


def log_rate_limit_event(
    conn: Any,
    *,
    path: str,
    status_code: int | None,
    headers: dict[str, str] | None,
) -> None:
    ensure_schema(conn)
    h = headers or {}

    def _ci(name: str) -> str | None:
        for k, v in h.items():
            if k.lower() == name.lower():
                return str(v)
        return None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO coindesk_rate_limit_events
            (path, status_code, limit_hdr, remaining_hdr, reset_hdr, retry_after)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                path,
                status_code,
                _ci("X-Ratelimit-Limit"),
                _ci("X-Ratelimit-Remaining"),
                _ci("X-Ratelimit-Reset"),
                _ci("Retry-After"),
            ),
        )
    conn.commit()


def insert_ticks(conn: Any, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    ensure_schema(conn)
    from psycopg2.extras import Json, execute_values

    batch: list[tuple[Any, ...]] = []
    for r in rows:
        ts_ms = int(r["timestamp_ms"])
        t = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        raw = r.get("raw")
        batch.append(
            (
                t,
                str(r["market"]),
                str(r["instrument"]),
                r.get("price"),
                r.get("volume"),
                r.get("ccseq"),
                str(r.get("source") or "coindesk"),
                Json(raw) if raw is not None else None,
            )
        )
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO coindesk_ticks (time, market, instrument, price, volume, ccseq, source, raw)
            VALUES %s
            """,
            batch,
        )
    conn.commit()
    record_rows_written("coindesk_ticks", len(batch))
    logger.info("Inserted %s CoinDesk ticks", len(batch))
    return len(batch)


def training_ohlcv_rows_from_dataframe(df: Any) -> list[dict[str, Any]]:
    """Turn a training-style OHLCV DataFrame into row dicts (no DB — call before connect to avoid idle timeouts)."""
    import pandas as pd

    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        cs = r.get("ccseq")
        rows.append(
            {
                "timestamp_ms": int(r["timestamp"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r.get("volume") or 0),
                "market": str(r.get("market") or config.COINDESK_DEFAULT_MARKET),
                "instrument": str(r["instrument"]),
                "timeframe": str(r.get("timeframe") or config.TRAIN_TIMEFRAME),
                "ccseq": int(cs) if cs is not None and not (isinstance(cs, float) and pd.isna(cs)) else None,
                "source": str(r.get("source") or "coindesk"),
            }
        )
    return rows


def upsert_ohlcv_from_dataframe(conn: Any, df: Any) -> int:
    """Upsert from training-style DataFrame (timestamp ms, OHLCV, market, instrument, timeframe, source)."""
    return upsert_ohlcv_rows(conn, training_ohlcv_rows_from_dataframe(df))


def upsert_ohlcv_rows(conn: Any, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    ensure_schema(conn)
    from psycopg2.extras import execute_values

    chunk_size = max(1, int(config.COINDESK_OHLCV_UPSERT_CHUNK))
    batch: list[tuple[Any, ...]] = []
    for r in rows:
        ts_ms = int(r["timestamp_ms"])
        t = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        batch.append(
            (
                t,
                str(r["market"]),
                str(r["instrument"]),
                str(r["timeframe"]),
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                float(r.get("volume") or 0),
                r.get("ccseq"),
                str(r.get("source") or "coindesk"),
            )
        )
    insert_sql = """
            INSERT INTO coindesk_ohlcv (
                time, market, instrument, timeframe, open, high, low, close, volume, ccseq, source
            ) VALUES %s
            ON CONFLICT (time, market, instrument, timeframe) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                ccseq = COALESCE(EXCLUDED.ccseq, coindesk_ohlcv.ccseq)
            """
    total = 0
    for i in range(0, len(batch), chunk_size):
        part = batch[i : i + chunk_size]
        with conn.cursor() as cur:
            execute_values(cur, insert_sql, part)
        conn.commit()
        record_rows_written("coindesk_ohlcv", len(part))
        total += len(part)
    return total


def insert_orderbook(conn: Any, ob: dict[str, Any]) -> None:
    ensure_schema(conn)
    from psycopg2.extras import Json

    ts_ms = int(ob["timestamp_ms"])
    t = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO coindesk_orderbooks
            (time, market, instrument, depth, bids, asks, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                t,
                str(ob["market"]),
                str(ob["instrument"]),
                ob.get("depth"),
                Json(ob.get("bids") or []),
                Json(ob.get("asks") or []),
                str(ob.get("source") or "coindesk"),
            ),
        )
    conn.commit()
    record_rows_written("coindesk_orderbooks", 1)


def _instrument_dedupe_key(market: str, symbol: str | None, instrument_id: str | None) -> str:
    return f"{market}|{symbol or ''}|{instrument_id or ''}"


def upsert_instruments(conn: Any, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    ensure_schema(conn)
    from psycopg2.extras import Json

    n = 0
    with conn.cursor() as cur:
        for r in rows:
            mkt = str(r["market"])
            sym = r.get("symbol")
            iid = r.get("instrument_id")
            dk = _instrument_dedupe_key(mkt, sym, iid)
            cur.execute(
                """
                INSERT INTO coindesk_instruments
                (market, dedupe_key, instrument_id, symbol, base_asset, quote_asset, instrument_type, raw, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (dedupe_key)
                DO UPDATE SET
                    base_asset = EXCLUDED.base_asset,
                    quote_asset = EXCLUDED.quote_asset,
                    instrument_type = EXCLUDED.instrument_type,
                    raw = EXCLUDED.raw,
                    updated_at = NOW()
                """,
                (
                    mkt,
                    dk,
                    iid,
                    sym,
                    r.get("base_asset"),
                    r.get("quote_asset"),
                    r.get("instrument_type"),
                    Json(r.get("raw")) if r.get("raw") is not None else None,
                ),
            )
            n += 1
    conn.commit()
    record_rows_written("coindesk_instruments", n)
    return n


def get_ccseq_state(conn: Any, market: str, instrument: str, stream_type: str) -> int | None:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT last_ccseq FROM coindesk_ccseq_state
            WHERE market = %s AND instrument = %s AND stream_type = %s
            """,
            (market, instrument, stream_type),
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return int(row[0])


def set_ccseq_state(conn: Any, market: str, instrument: str, stream_type: str, ccseq: int) -> None:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO coindesk_ccseq_state (market, instrument, stream_type, last_ccseq, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (market, instrument, stream_type)
            DO UPDATE SET last_ccseq = EXCLUDED.last_ccseq, updated_at = NOW()
            """,
            (market, instrument, stream_type, ccseq),
        )
    conn.commit()


def load_ohlcv_dataframe(
    conn: Any,
    *,
    instrument: str,
    timeframe: str,
    market: str | None = None,
    limit: int = 500,
) -> Any:
    """Return pandas DataFrame: timestamp (ms), open, high, low, close, volume."""
    import pandas as pd

    ensure_schema(conn)
    mkt = market or config.COINDESK_DEFAULT_MARKET
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXTRACT(EPOCH FROM time) * 1000 AS ts_ms,
                   open, high, low, close, volume
            FROM coindesk_ohlcv
            WHERE market = %s AND instrument = %s AND timeframe = %s
            ORDER BY time DESC
            LIMIT %s
            """,
            (mkt, instrument, timeframe, max(1, int(limit))),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = df["timestamp"].astype("int64")
    return df.iloc[::-1].reset_index(drop=True)


def export_ohlcv_parquet(
    conn: Any,
    path: str,
    *,
    instrument: str,
    timeframe: str,
    market: str | None = None,
    limit: int = 50_000,
) -> str:

    df = load_ohlcv_dataframe(conn, instrument=instrument, timeframe=timeframe, market=market, limit=limit)
    df["source"] = "coindesk"
    df["asset"] = instrument.replace("-", "_")
    p = path
    df.to_parquet(p, index=False)
    logger.info("Exported %s rows to %s", len(df), p)
    return p

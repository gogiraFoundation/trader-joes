"""CCSEQ handling: monotonic state for polls; optional strict gap backfill for streaming."""

from __future__ import annotations

import logging
from typing import Any

from tr_bot import config
from tr_bot.coindesk.client import CoinDeskClient
from tr_bot.coindesk.endpoints import build_historical_ohlcv_params
from tr_bot.coindesk.metrics import record_ccseq_gap
from tr_bot.coindesk.normalize import parse_ohlcv_payload
from tr_bot.db import coindesk_repo as repo

logger = logging.getLogger("tr_bot.coindesk.ccseq")


def _backfill_around_gap(
    market: str,
    instrument: str,
    tick_row: dict[str, Any],
    timeframe: str,
    client: CoinDeskClient,
) -> bool:
    """Fetch a bounded OHLCV window ending near this tick (requires COINDESK_OHLCV_PATH).

    Uses a fresh DB connection for upsert so the main session is not held across HTTP
    (avoids stale/closed connections after long fetches). Returns True if rows were written.
    """
    path = config.COINDESK_OHLCV_PATH.strip()
    if not path:
        logger.warning(
            "CCSEQ backfill skipped for %s/%s: set COINDESK_OHLCV_PATH to a valid historical endpoint",
            market,
            instrument,
        )
        return False
    try:
        to_ts = str(int(tick_row["timestamp_ms"] // 1000))
        params = build_historical_ohlcv_params(
            market,
            instrument,
            timeframe,
            min(500, config.COINDESK_TRAIN_LIMIT),
            to_ts,
        )
        payload = client.get(path, params, endpoint_metric="ohlcv_backfill")
        parsed = parse_ohlcv_payload(payload, market=market, instrument=instrument, timeframe=timeframe)
        if not parsed:
            return False
        nconn = repo.connect()
        try:
            repo.upsert_ohlcv_rows(nconn, parsed)
        finally:
            nconn.close()
        logger.info("Backfilled %s OHLCV rows for %s/%s", len(parsed), market, instrument)
        return True
    except Exception as e:
        logger.warning("CCSEQ backfill failed for %s/%s: %s", market, instrument, e)
        return False


def _reconnect(conn: Any) -> Any:
    try:
        conn.close()
    except Exception:
        pass
    return repo.connect()


def process_ticks_with_ccseq(
    conn_holder: list[Any],
    rows: list[dict[str, Any]],
    *,
    stream_type: str = "tick",
    backfill_timeframe: str | None = None,
    client: CoinDeskClient | None = None,
) -> None:
    """
    Update CCSEQ state and insert ticks.

    ``conn_holder`` is a single-element list so the connection can be replaced after
    long HTTP backfills (Postgres may close idle sessions).

    - **Poll mode (default)**: each request returns the latest tick; CCSEQ advances by many between
      polls — that is normal. We only persist the latest CCSEQ monotonically (no gap warnings).
    - **Strict / streaming**: set COINDESK_CCSEQ_POLL_MODE=false and optionally
      COINDESK_CCSEQ_BACKFILL_ON_GAP=true with COINDESK_OHLCV_PATH set for gap fill.
    """
    if not rows:
        return
    conn = conn_holder[0]
    tf = backfill_timeframe or config.COINDESK_OHLCV_DB_TIMEFRAME or config.TRAIN_TIMEFRAME
    c = client or CoinDeskClient()
    poll_mode = config.COINDESK_CCSEQ_POLL_MODE
    backfill_on_gap = config.COINDESK_CCSEQ_BACKFILL_ON_GAP

    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        k = (str(r["market"]), str(r["instrument"]))
        by_key.setdefault(k, []).append(r)

    for (market, instrument), tick_rows in by_key.items():
        tick_rows.sort(key=lambda x: int(x["timestamp_ms"]))
        last_stored = repo.get_ccseq_state(conn, market, instrument, stream_type)
        last_seq: int | None = last_stored

        for tr in tick_rows:
            ccseq = tr.get("ccseq")
            if ccseq is None:
                continue
            cur = int(ccseq)

            if last_seq is None:
                pass
            elif poll_mode:
                if cur < last_seq:
                    logger.warning(
                        "CoinDesk CCSEQ decreased for %s/%s: %s → %s (sequence reset?)",
                        market,
                        instrument,
                        last_seq,
                        cur,
                    )
                elif cur > last_seq + 1:
                    logger.debug(
                        "CoinDesk CCSEQ poll jump %s/%s: %s → %s (normal between REST polls)",
                        market,
                        instrument,
                        last_seq,
                        cur,
                    )
            else:
                # Strict: expect +1 per tick in a stream
                if cur < last_seq:
                    logger.warning(
                        "CoinDesk CCSEQ decreased for %s/%s: %s → %s",
                        market,
                        instrument,
                        last_seq,
                        cur,
                    )
                elif cur != last_seq + 1:
                    logger.warning(
                        "CoinDesk CCSEQ gap for %s/%s: expected %s got %s",
                        market,
                        instrument,
                        last_seq + 1,
                        cur,
                    )
                    record_ccseq_gap(market, instrument)
                    if backfill_on_gap:
                        # Main session sits idle during HTTP + secondary upsert; poolers may close it.
                        _backfill_around_gap(market, instrument, tr, tf, c)
                        conn = _reconnect(conn)
                        conn_holder[0] = conn

            last_seq = cur

        if last_seq is not None:
            repo.set_ccseq_state(conn, market, instrument, stream_type, last_seq)

    repo.insert_ticks(conn, rows)

"""Persist no-trade decisions to PostgreSQL for analytics and future model training."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from tr_bot import config

logger = logging.getLogger("tr_bot.db.decision_log")

_DDL_STATEMENTS = [
    """
CREATE TABLE IF NOT EXISTS decision_events (
    id BIGSERIAL PRIMARY KEY,
    ts_utc TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    regime TEXT,
    signal TEXT,
    sentiment TEXT,
    decision TEXT NOT NULL,
    reason TEXT,
    regime_confidence DOUBLE PRECISION,
    ta_ok BOOLEAN,
    fundamentals_ok BOOLEAN,
    action TEXT NOT NULL DEFAULT 'no_trade',
    loop_idx INTEGER,
    features_json JSONB
)
""",
    "CREATE INDEX IF NOT EXISTS idx_decision_events_ts ON decision_events (ts_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_decision_events_symbol_ts ON decision_events (symbol, ts_utc)",
    "CREATE INDEX IF NOT EXISTS idx_decision_events_reason ON decision_events (reason)",
]

_table_ensured = False


def _safe_float_series(last: Any, key: str) -> float | None:
    try:
        v = last.get(key) if hasattr(last, "get") else getattr(last, key, None)
        if v is None:
            return None
        x = float(v)
        return None if x != x else x  # NaN
    except (TypeError, ValueError):
        return None


def _features_snapshot(
    last: Any,
    cmc_snapshot: dict[str, Any] | None = None,
    cmc_fear_greed_snapshot: dict[str, Any] | None = None,
    cmc_altcoin_season_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "profile_id": config.ACTIVE_STRATEGY_PROFILE_ID or None,
        "deployment_tier": config.DEPLOYMENT_TIER,
    }
    for k in ("close", "rsi", "atr", "adx"):
        v = _safe_float_series(last, k)
        if v is not None:
            out[k] = v
    if cmc_snapshot and cmc_snapshot.get("ok") and isinstance(
        cmc_snapshot.get("data"), dict
    ):
        d = cmc_snapshot["data"]
        cg: dict[str, Any] = {}
        for key in (
            "btc_dominance",
            "eth_dominance",
            "total_market_cap",
            "total_volume_24h",
        ):
            if d.get(key) is not None:
                cg[key] = d.get(key)
        if cmc_snapshot.get("fetched_utc") is not None:
            cg["fetched_utc"] = cmc_snapshot.get("fetched_utc")
        if cg:
            out["cmc_global"] = cg
    if cmc_fear_greed_snapshot and cmc_fear_greed_snapshot.get("ok") and isinstance(
        cmc_fear_greed_snapshot.get("data"), dict
    ):
        fd = cmc_fear_greed_snapshot["data"]
        fg: dict[str, Any] = {}
        if fd.get("value") is not None:
            fg["value"] = fd.get("value")
        if fd.get("value_classification") is not None:
            fg["value_classification"] = fd.get("value_classification")
        if cmc_fear_greed_snapshot.get("fetched_utc") is not None:
            fg["fetched_utc"] = cmc_fear_greed_snapshot.get("fetched_utc")
        if fg:
            out["cmc_fear_greed"] = fg
    if cmc_altcoin_season_snapshot and cmc_altcoin_season_snapshot.get("ok") and isinstance(
        cmc_altcoin_season_snapshot.get("data"), dict
    ):
        ad = cmc_altcoin_season_snapshot["data"]
        ag: dict[str, Any] = {}
        if ad.get("value") is not None:
            ag["value"] = ad.get("value")
        if cmc_altcoin_season_snapshot.get("fetched_utc") is not None:
            ag["fetched_utc"] = cmc_altcoin_season_snapshot.get("fetched_utc")
        if ag:
            out["cmc_altcoin_season"] = ag
    return out


def ensure_decision_events_table(conn: Any) -> None:
    global _table_ensured
    if _table_ensured:
        return
    with conn.cursor() as cur:
        for stmt in _DDL_STATEMENTS:
            cur.execute(stmt.strip())
    conn.commit()
    _table_ensured = True


def insert_decision_event(
    conn: Any,
    *,
    ts_utc: datetime,
    symbol: str,
    regime: str,
    signal: str,
    sentiment: str,
    decision: str,
    reason: str | None,
    regime_confidence: float,
    ta_ok: bool,
    fundamentals_ok: bool,
    action: str,
    loop_idx: int,
    features_json: dict[str, Any] | None,
) -> None:
    from psycopg2.extras import Json

    ensure_decision_events_table(conn)
    sql = """
        INSERT INTO decision_events (
            ts_utc, symbol, regime, signal, sentiment, decision, reason,
            regime_confidence, ta_ok, fundamentals_ok, action, loop_idx, features_json
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """
    fj: Any = Json(features_json) if features_json is not None else None
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                ts_utc,
                symbol,
                regime,
                signal,
                sentiment,
                decision,
                reason,
                regime_confidence,
                ta_ok,
                fundamentals_ok,
                action,
                loop_idx,
                fj,
            ),
        )
    conn.commit()


def maybe_log_no_trade_decision(
    *,
    loop_idx: int,
    symbol: str,
    regime: str,
    signal: str,
    sentiment: str,
    decision: str,
    reason: str | None,
    regime_confidence: float,
    ta_ok: bool,
    fundamentals_ok: bool,
    last: Any,
    cmc_snapshot: dict[str, Any] | None = None,
    cmc_fear_greed_snapshot: dict[str, Any] | None = None,
    cmc_altcoin_season_snapshot: dict[str, Any] | None = None,
) -> None:
    if not config.DECISION_LOG_DB_ENABLED:
        return
    url = (config.DATABASE_URL or "").strip()
    if not url:
        return
    try:
        import psycopg2
    except ImportError:
        logger.warning("DECISION_LOG_DB_ENABLED but psycopg2 not available")
        return
    ts = datetime.now(timezone.utc)
    features = _features_snapshot(
        last,
        cmc_snapshot=cmc_snapshot,
        cmc_fear_greed_snapshot=cmc_fear_greed_snapshot,
        cmc_altcoin_season_snapshot=cmc_altcoin_season_snapshot,
    )
    try:
        conn = psycopg2.connect(url, connect_timeout=3)
        try:
            insert_decision_event(
                conn,
                ts_utc=ts,
                symbol=symbol,
                regime=regime,
                signal=signal,
                sentiment=sentiment,
                decision=decision,
                reason=reason,
                regime_confidence=float(regime_confidence),
                ta_ok=bool(ta_ok),
                fundamentals_ok=bool(fundamentals_ok),
                action="no_trade",
                loop_idx=loop_idx,
                features_json=features,
            )
        finally:
            conn.close()
    except Exception as e:
        logger.warning("decision_events insert failed: %s", e)

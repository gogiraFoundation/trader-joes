-- Optional: apply manually or mount under docker-entrypoint-initdb.d
-- The bot also creates this table on first insert when DECISION_LOG_DB_ENABLED=true.

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
);

CREATE INDEX IF NOT EXISTS idx_decision_events_ts ON decision_events (ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_decision_events_symbol_ts ON decision_events (symbol, ts_utc);
CREATE INDEX IF NOT EXISTS idx_decision_events_reason ON decision_events (reason);

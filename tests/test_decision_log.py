"""Tests for decision_events logging."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tr_bot.db import decision_log as dl


def test_maybe_log_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl.config, "DECISION_LOG_DB_ENABLED", False)
    with patch("psycopg2.connect") as m:
        dl.maybe_log_no_trade_decision(
            loop_idx=1,
            symbol="BTC/USDT",
            regime="range",
            signal="hold",
            sentiment="neutral",
            decision="hold",
            reason="hold",
            regime_confidence=0.9,
            ta_ok=True,
            fundamentals_ok=True,
            last={},
        )
    m.assert_not_called()


def test_maybe_log_connect_failure_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl.config, "DECISION_LOG_DB_ENABLED", True)
    monkeypatch.setattr(dl.config, "DATABASE_URL", "postgresql://x")
    with patch("psycopg2.connect", side_effect=RuntimeError("boom")):
        # must not raise
        dl.maybe_log_no_trade_decision(
            loop_idx=1,
            symbol="BTC/USDT",
            regime="range",
            signal="hold",
            sentiment="neutral",
            decision="hold",
            reason="low_regime_confidence",
            regime_confidence=0.1,
            ta_ok=True,
            fundamentals_ok=True,
            last={"close": 1.0, "rsi": 50.0},
        )


def test_ensure_runs_ddl_once(monkeypatch: pytest.MonkeyPatch) -> None:
    dl._table_ensured = False
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    n = len(dl._DDL_STATEMENTS)
    dl.ensure_decision_events_table(conn)
    assert cur.execute.call_count == n
    dl.ensure_decision_events_table(conn)
    assert cur.execute.call_count == n
    dl._table_ensured = False


def test_insert_decision_event_builds_query(monkeypatch: pytest.MonkeyPatch) -> None:
    dl._table_ensured = True
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc)
    dl.insert_decision_event(
        conn,
        ts_utc=ts,
        symbol="ETH/USDT",
        regime="trend",
        signal="buy",
        sentiment="neutral",
        decision="hold",
        reason="weak_signal:rsi_distance_too_small",
        regime_confidence=0.7,
        ta_ok=True,
        fundamentals_ok=True,
        action="no_trade",
        loop_idx=42,
        features_json={"close": 100.0, "rsi": 55.0},
    )
    cur.execute.assert_called_once()
    args = cur.execute.call_args[0]
    assert "INSERT INTO decision_events" in args[0]
    assert args[1][0] is ts
    assert args[1][1] == "ETH/USDT"
    assert args[1][6] == "weak_signal:rsi_distance_too_small"
    dl._table_ensured = False


def test_features_snapshot_extracts_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl.config, "ACTIVE_STRATEGY_PROFILE_ID", "p1")
    monkeypatch.setattr(dl.config, "DEPLOYMENT_TIER", "paper")
    class _Last:
        def get(self, k: str, default=None):
            d = {"close": 1.5, "rsi": 40.0, "atr": 0.01, "adx": 20.0}
            return d.get(k, default)

    fs = dl._features_snapshot(_Last())
    assert fs["close"] == 1.5
    assert fs["rsi"] == 40.0
    assert fs["profile_id"] == "p1"

def test_features_snapshot_includes_cmc_when_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl.config, "ACTIVE_STRATEGY_PROFILE_ID", None)
    monkeypatch.setattr(dl.config, "DEPLOYMENT_TIER", "paper")
    class _Last:
        def get(self, k: str, default=None):
            return {"close": 2.0}.get(k, default)

    cmc = {
        "ok": True,
        "fetched_utc": "2026-01-01T00:00:00+00:00",
        "data": {
            "btc_dominance": 57.5,
            "eth_dominance": 10.2,
            "total_market_cap": 2.3e12,
            "total_volume_24h": 89e9,
        },
    }
    fs = dl._features_snapshot(_Last(), cmc_snapshot=cmc)
    assert fs["close"] == 2.0
    assert fs["cmc_global"]["btc_dominance"] == 57.5
    assert fs["cmc_global"]["fetched_utc"] == "2026-01-01T00:00:00+00:00"


def test_features_snapshot_includes_cmc_fear_greed_and_altcoin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl.config, "ACTIVE_STRATEGY_PROFILE_ID", None)
    monkeypatch.setattr(dl.config, "DEPLOYMENT_TIER", "paper")

    class _Last:
        def get(self, k: str, default=None):
            return {"close": 3.0}.get(k, default)

    fg = {
        "ok": True,
        "fetched_utc": "2026-01-02T00:00:00+00:00",
        "data": {"value": 42, "value_classification": "Fear"},
    }
    ac = {
        "ok": True,
        "fetched_utc": "2026-01-02T12:00:00+00:00",
        "data": {"value": 35},
    }
    fs = dl._features_snapshot(
        _Last(),
        cmc_fear_greed_snapshot=fg,
        cmc_altcoin_season_snapshot=ac,
    )
    assert fs["cmc_fear_greed"]["value"] == 42
    assert fs["cmc_fear_greed"]["value_classification"] == "Fear"
    assert fs["cmc_altcoin_season"]["value"] == 35


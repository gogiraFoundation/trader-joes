"""Daily report UTC window filtering and aggregations."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from tr_bot.reporting.daily import (
    build_daily_report_payload,
    load_audit_records_in_window,
    load_ledger_rows_in_window,
    render_daily_report_markdown,
    utc_day_window,
)


def test_utc_day_window() -> None:
    start, end = utc_day_window(date(2024, 6, 15))
    assert start == datetime(2024, 6, 15, tzinfo=timezone.utc)
    assert end == datetime(2024, 6, 16, tzinfo=timezone.utc)


def test_load_audit_ledger_filters_by_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.reporting.daily.config.ARTIFACTS_DIR", str(tmp_path))
    root = tmp_path
    day = date(2024, 6, 15)
    start, end = utc_day_window(day)

    audit = root / "decision_audit.jsonl"
    rec_in = {
        "schema_version": 1,
        "loop": 1,
        "utc": "2024-06-15T12:00:00+00:00",
        "by_symbol": {
            "BTC/USDT": {
                "action": "no_trade",
                "decision": "hold",
                "regime": "range",
                "regime_confidence": 0.5,
                "reason": "low_regime_confidence",
            }
        },
    }
    rec_out = {
        "schema_version": 1,
        "loop": 2,
        "utc": "2024-06-16T00:00:00+00:00",
        "by_symbol": {"BTC/USDT": {"action": "opened", "decision": "buy"}},
    }
    audit.write_text(json.dumps(rec_in) + "\n" + json.dumps(rec_out) + "\n", encoding="utf-8")

    rows = load_audit_records_in_window(audit, start, end)
    assert len(rows) == 1
    assert rows[0]["loop"] == 1

    led = root / "trade_ledger.jsonl"
    t_in = {
        "utc": "2024-06-15T23:59:59+00:00",
        "symbol": "BTC/USDT",
        "net_pnl_usdt": 1.0,
        "fee_usdt": 0.1,
    }
    t_out = {"utc": "2024-06-14T12:00:00+00:00", "symbol": "BTC/USDT", "net_pnl_usdt": 99.0}
    led.write_text(json.dumps(t_in) + "\n" + json.dumps(t_out) + "\n", encoding="utf-8")
    lt = load_ledger_rows_in_window(led, start, end)
    assert len(lt) == 1
    assert lt[0]["net_pnl_usdt"] == 1.0


def test_build_daily_report_payload_histogram(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tr_bot.reporting.daily.config.ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("tr_bot.reporting.daily.config.SYMBOL", "BTC/USDT")
    monkeypatch.setattr("tr_bot.reporting.daily.config.TIMEFRAME", "5m")
    monkeypatch.setattr("tr_bot.reporting.daily.config.DEPLOYMENT_TIER", "paper")
    monkeypatch.setattr("tr_bot.reporting.daily.config.PAPER_TRADING", True)
    monkeypatch.setattr("tr_bot.reporting.daily.config.ACTIVE_STRATEGY_PROFILE_ID", "p1")
    monkeypatch.setattr("tr_bot.reporting.daily.config.MAX_TRADES_PER_DAY", 5)
    monkeypatch.setattr("tr_bot.reporting.daily.config.LOSS_COOLDOWN_BARS", 4)
    monkeypatch.setattr("tr_bot.reporting.daily.config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES", 3)
    monkeypatch.setattr("tr_bot.reporting.daily.config.DECISION_AUDIT_JSONL_PATH", "decision_audit.jsonl")

    root = tmp_path
    audit = root / "decision_audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "utc": "2024-06-15T10:00:00+00:00",
                "by_symbol": {
                    "BTC/USDT": {
                        "action": "no_trade",
                        "decision": "hold",
                        "regime": "range",
                        "regime_confidence": 0.4,
                        "reason": "low_regime_confidence",
                        "daily_trades": 1,
                        "max_daily_trades": 5,
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_daily_report_payload(date(2024, 6, 15), artifacts_root=root, include_preflight=False)
    assert payload["schema_version"] == 1
    assert payload["meta"]["day_utc"] == "2024-06-15"
    hist = payload["loops"]["no_trade_reason_histogram"]
    assert hist.get("low_regime_confidence") == 1
    assert payload["regime"]["regime_counts"].get("range") == 1
    md = render_daily_report_markdown(payload)
    assert "Daily trading report" in md
    assert "low_regime_confidence" in md

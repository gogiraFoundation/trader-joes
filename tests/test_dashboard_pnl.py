"""P&L dashboard trade summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tr_bot.dashboard_pnl import (
    _equity_window,
    _trade_summary,
    build_drawdown_history_payload,
    build_pnl_dashboard_payload,
    build_equity_history_display_payload,
    build_metrics_dashboard_payload,
)
from tr_bot import config as config_mod


def test_trade_summary_net_positive_pct() -> None:
    trades = [
        {"outcome": "win", "net_pnl_usdt": -0.1, "fee_usdt": 0.05},
        {"outcome": "loss", "net_pnl_usdt": -0.5, "fee_usdt": 0.02},
        {"outcome": "win", "net_pnl_usdt": 0.02, "fee_usdt": 0.01},
    ]
    s = _trade_summary(trades)
    assert s["closed_trades"] == 3
    assert s["wins"] == 2
    assert s["losses"] == 1
    assert s["win_rate_pct"] is not None and abs(s["win_rate_pct"] - (2 / 3 * 100)) < 0.01
    assert s["net_positive_pct"] is not None and s["net_positive_pct"] == round(100 / 3, 2)


def test_trade_summary_empty() -> None:
    s = _trade_summary([])
    assert s["net_positive_pct"] is None
    assert s["win_rate_pct"] is None


def test_trade_summary_includes_avg_and_best_worst() -> None:
    trades = [
        {"outcome": "win", "net_pnl_usdt": 4.0, "fee_usdt": 0.1, "symbol": "BTC/USDT"},
        {"outcome": "loss", "net_pnl_usdt": -2.0, "fee_usdt": 0.2, "symbol": "ETH/USDT"},
        {"outcome": "win", "net_pnl_usdt": 1.0, "fee_usdt": 0.3, "symbol": "SOL/USDT"},
    ]
    s = _trade_summary(trades)
    assert s["avg_win_usdt"] == 2.5
    assert s["avg_loss_usdt"] == -2.0
    assert s["best_trade_usdt"] == 4.0
    assert s["worst_trade_usdt"] == -2.0
    assert s["best_trade"]["symbol"] == "BTC/USDT"
    assert s["worst_trade"]["symbol"] == "ETH/USDT"


def test_equity_window_median_outlier_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_mod, "PAPER_TRADING", True)
    monkeypatch.setattr(config_mod, "PAPER_EQUITY_USDT", 10_000.0)
    pts = [{"t": "2026-04-01T00:00:00Z", "nav": 100.0}]
    for i in range(50):
        pts.append({"t": f"2026-04-02T{i:02d}:00:00Z", "nav": 10_000.0})
    w = _equity_window(pts)
    assert w["ok"] is True
    assert w["outliers_dropped"] == 1
    assert abs(float(w["first_nav"]) - 10_000.0) < 1e-6
    assert abs(float(w["last_nav"]) - 10_000.0) < 1e-6
    assert w["return_pct"] is not None and abs(w["return_pct"]) < 1e-3
    assert w["max_drawdown_pct"] is not None and w["max_drawdown_pct"] < 1.0


def test_build_equity_history_display_drops_two_point_spike(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config_mod, "PAPER_TRADING", True)
    monkeypatch.setattr(config_mod, "PAPER_EQUITY_USDT", 10_000.0)
    monkeypatch.setattr(config_mod, "ARTIFACTS_DIR", str(tmp_path))
    (tmp_path / "equity_history.json").write_text(
        json.dumps(
            {
                "points": [
                    {"t": "2026-04-01T00:00:00Z", "nav": 100.0},
                    {"t": "2026-04-05T00:00:00Z", "nav": 10_000.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    out = build_equity_history_display_payload()
    assert out["outliers_dropped"] == 1
    assert len(out["points"]) == 1
    assert float(out["points"][0]["nav"]) == 10_000.0


def test_equity_window_two_point_corrupt_low(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_mod, "PAPER_TRADING", True)
    monkeypatch.setattr(config_mod, "PAPER_EQUITY_USDT", 10_000.0)
    pts = [
        {"t": "2026-04-01T00:00:00Z", "nav": 100.0},
        {"t": "2026-04-05T00:00:00Z", "nav": 10_000.0},
    ]
    w = _equity_window(pts)
    assert w["ok"] is True
    assert w["outliers_dropped"] == 1
    assert w["first_nav"] == w["last_nav"] == 10_000.0
    assert w["return_pct"] == 0.0


def test_build_pnl_dashboard_payload_includes_full_ledger_closed_trade_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tr_bot.dashboard_pnl.read_equity_history",
        lambda: {"points": []},
    )
    monkeypatch.setattr(
        "tr_bot.dashboard_pnl.read_backtest_last_run",
        lambda: None,
    )
    monkeypatch.setattr(
        "tr_bot.dashboard_pnl.read_recent_trades",
        lambda _limit=200: [{"outcome": "win", "net_pnl_usdt": 1.0, "fee_usdt": 0.1}],
    )
    monkeypatch.setattr(
        "tr_bot.dashboard_pnl.closed_trade_stats",
        lambda *, paper: {
            "scope": "full_ledger",
            "paper_mode": "paper" if paper else "live",
            "closed_trades": 25,
            "sum_net_pnl_usdt": -2010.58,
            "avg_win_usdt": 151.23,
            "avg_loss_usdt": -226.15,
            "best_trade_usdt": 681.22,
            "worst_trade_usdt": -913.8,
        },
    )
    monkeypatch.setattr(config_mod, "PAPER_TRADING", True)

    out = build_pnl_dashboard_payload()
    assert out["closed_trade_stats"]["scope"] == "full_ledger"
    assert out["closed_trade_stats"]["closed_trades"] == 25
    assert out["closed_trade_stats"]["sum_net_pnl_usdt"] == -2010.58


def test_drawdown_history_anchors_to_fund_peak_when_absent_from_series() -> None:
    """Historical peak missing from equity points still yields fund-matching DD."""
    points = [
        {"t": "2026-07-16T10:00:00+00:00", "nav": 7989.42194},
        {"t": "2026-07-16T10:05:00+00:00", "nav": 7989.42194},
    ]
    peak = 10207.747626742077
    last = 7989.42194
    expected = round((peak - last) / peak * 100.0, 4)
    out = build_drawdown_history_payload(
        points=points,
        peak_anchor=peak,
        last_nav=last,
        as_of_utc="2026-07-16T10:05:00+00:00",
    )
    assert out["source"] == "fund_peak_anchored"
    assert out["peak_anchor"] == peak
    assert abs(float(out["current_drawdown_pct"]) - expected) < 1e-6
    assert abs(float(out["points"][-1]["drawdown_pct"]) - expected) < 1e-6
    # No synthetic peak NAV row — only equity timestamps.
    assert all(float(p["nav"]) == last for p in out["points"])


def test_drawdown_history_appends_newer_fund_nav() -> None:
    points = [{"t": "2026-07-16T10:00:00+00:00", "nav": 8000.0}]
    out = build_drawdown_history_payload(
        points=points,
        peak_anchor=10_000.0,
        last_nav=7500.0,
        as_of_utc="2026-07-16T11:00:00+00:00",
    )
    assert len(out["points"]) == 2
    assert out["points"][-1]["nav"] == 7500.0
    assert abs(float(out["points"][-1]["drawdown_pct"]) - 25.0) < 1e-6


def test_drawdown_history_new_high_resets_to_zero() -> None:
    points = [
        {"t": "2026-07-16T10:00:00+00:00", "nav": 9000.0},
        {"t": "2026-07-16T11:00:00+00:00", "nav": 11_000.0},
    ]
    out = build_drawdown_history_payload(
        points=points,
        peak_anchor=10_000.0,
        last_nav=11_000.0,
        as_of_utc="2026-07-16T11:00:00+00:00",
    )
    assert abs(float(out["points"][0]["drawdown_pct"]) - 10.0) < 1e-6
    assert abs(float(out["points"][-1]["drawdown_pct"])) < 1e-6


def test_drawdown_history_empty_points_uses_fund_current() -> None:
    out = build_drawdown_history_payload(
        points=[],
        peak_anchor=10_000.0,
        last_nav=8000.0,
        as_of_utc="2026-07-16T12:00:00+00:00",
    )
    assert len(out["points"]) == 1
    assert abs(float(out["current_drawdown_pct"]) - 20.0) < 1e-6


def test_build_metrics_dashboard_payload_prefers_fund_drawdown_pct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fund = {
        "enabled": True,
        "state": {
            "last_nav": 7989.42194,
            "peak_nav": 10207.747626742077,
            "last_update_utc": "2026-07-16T10:40:52+00:00",
            "drawdown_pct": 21.731,
        },
    }
    monkeypatch.setattr(
        "tr_bot.dashboard_pnl.read_equity_history",
        lambda: {"points": [{"t": "2026-07-16T10:00:00+00:00", "nav": 7989.42194}]},
    )
    out = build_metrics_dashboard_payload(fund=fund)
    assert "drawdown_history" in out
    assert abs(float(out["drawdown_history"]["current_drawdown_pct"]) - 21.731) < 1e-6

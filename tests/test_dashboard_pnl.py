"""P&L dashboard trade summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tr_bot.dashboard_pnl import (
    _equity_window,
    _trade_summary,
    build_equity_history_display_payload,
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

"""Unit tests for no-trade streak + adaptation policy + backtest gate."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tr_bot.strategy import no_trade_adaptation as nta


def _audit_record(by_symbol: dict) -> dict:
    return {"by_symbol": by_symbol}


def test_build_reason_histogram_counts_no_trade_only() -> None:
    recs = [
        _audit_record(
            {
                "BTC": {"action": "no_trade", "reason": "low_regime_confidence"},
                "ETH": {"action": "hold", "reason": "hold"},
            }
        ),
        _audit_record({"BTC": {"action": "opened", "reason": "x"}}),
    ]
    h = nta.build_reason_histogram(recs)
    assert h["low_regime_confidence"] == 1
    assert "hold" not in h


def test_dominant_reason_prefers_strong_second_over_hold() -> None:
    from collections import Counter

    h = Counter({"hold": 20, "low_regime_confidence": 5})
    assert nta._dominant_reason(h) == "low_regime_confidence"
    h2 = Counter({"hold": 100, "low_regime_confidence": 2})
    assert nta._dominant_reason(h2) == "hold"


def test_propose_param_delta_regime_confidence() -> None:
    with patch.object(nta.config, "REGIME_MIN_CONFIDENCE", 0.55):
        with patch.object(nta.config, "ADAPTATION_REGIME_CONFIDENCE_STEP", 0.05):
            with patch.object(nta.config, "ADAPTATION_REGIME_CONFIDENCE_FLOOR", 0.35):
                d = nta.propose_param_delta("low_regime_confidence")
                assert d.get("REGIME_MIN_CONFIDENCE") == pytest.approx(0.5)


def test_propose_param_delta_at_floor_no_change() -> None:
    with patch.object(nta.config, "REGIME_MIN_CONFIDENCE", 0.35):
        with patch.object(nta.config, "ADAPTATION_REGIME_CONFIDENCE_STEP", 0.05):
            with patch.object(nta.config, "ADAPTATION_REGIME_CONFIDENCE_FLOOR", 0.35):
                assert nta.propose_param_delta("low_regime_confidence") == {}


def test_propose_param_delta_weak_signal_rsi_distance() -> None:
    with patch.object(nta.config, "SIGNAL_MIN_RSI_DISTANCE", 4.0):
        with patch.object(nta.config, "ADAPTATION_RSI_DISTANCE_STEP", 0.5):
            with patch.object(nta.config, "ADAPTATION_RSI_DISTANCE_FLOOR", 0.0):
                d = nta.propose_param_delta("weak_signal:rsi_distance_too_small")
                assert d.get("SIGNAL_MIN_RSI_DISTANCE") == pytest.approx(3.5)


def test_propose_param_delta_hold_trend_flag() -> None:
    with patch.object(nta.config, "ADAPTATION_ENABLE_TREND_ON_HOLD", True):
        with patch.object(nta.config, "TREND_STRATEGY_ENABLED", False):
            d = nta.propose_param_delta("hold")
            assert d == {"TREND_STRATEGY_ENABLED": True}


def test_evaluate_adaptation_backtest_gate() -> None:
    m = {"total_return_pct": 1.0, "max_drawdown_pct": 5.0, "trades": 10}
    with patch.dict(
        os.environ,
        {
            "ADAPT_BACKTEST_MIN_RETURN_PCT": "0",
            "ADAPT_BACKTEST_MAX_DD_PCT": "10",
            "ADAPT_BACKTEST_MIN_TRADES": "5",
        },
        clear=False,
    ):
        g = nta.evaluate_adaptation_backtest_gate(m)
        assert g["passed"] is True
    with patch.dict(
        os.environ,
        {"ADAPT_BACKTEST_MIN_TRADES": "100"},
        clear=False,
    ):
        g2 = nta.evaluate_adaptation_backtest_gate(m)
        assert g2["passed"] is False


def test_record_loop_without_open_resets_on_open(tmp_path: Path) -> None:
    art = tmp_path / "art"
    art.mkdir()
    with (
        patch.object(nta.config, "ARTIFACTS_DIR", str(art)),
        patch.object(nta.config, "ADAPTATION_ENABLED", True),
    ):
        nta.record_loop_without_open({"S": {"action": "no_trade", "reason": "x"}}, 1)
        st = json.loads((art / "adaptation" / "no_trade_state.json").read_text())
        assert st["consecutive_loops_no_open"] == 1
        nta.record_loop_without_open({"S": {"action": "opened"}}, 2)
        st2 = json.loads((art / "adaptation" / "no_trade_state.json").read_text())
        assert st2["consecutive_loops_no_open"] == 0


def test_record_loop_skips_duplicate_loop_idx(tmp_path: Path) -> None:
    art = tmp_path / "art"
    art.mkdir()
    with (
        patch.object(nta.config, "ARTIFACTS_DIR", str(art)),
        patch.object(nta.config, "ADAPTATION_ENABLED", True),
    ):
        nta.record_loop_without_open({"S": {"action": "no_trade"}}, 1)
        nta.record_loop_without_open({"S": {"action": "no_trade"}}, 1)
        st = json.loads((art / "adaptation" / "no_trade_state.json").read_text())
        assert st["consecutive_loops_no_open"] == 1


def test_run_adaptation_job_disabled() -> None:
    with patch.object(nta.config, "ADAPTATION_ENABLED", False):
        out = nta.run_adaptation_job()
        assert out.get("skipped") == "ADAPTATION_ENABLED=false"


def test_run_adaptation_job_streak_below_min(tmp_path: Path) -> None:
    art = tmp_path / "art"
    (art / "adaptation").mkdir(parents=True)
    (art / "adaptation" / "no_trade_state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "consecutive_loops_no_open": 2,
                "last_loop_idx": 10,
                "last_processed_loop_idx": 10,
                "last_adaptation_utc": None,
                "last_adaptation_profile_id": None,
            }
        ),
        encoding="utf-8",
    )
    with (
        patch.object(nta.config, "ARTIFACTS_DIR", str(art)),
        patch.object(nta.config, "ADAPTATION_ENABLED", True),
        patch.object(nta.config, "ADAPTATION_MIN_LOOPS_NO_OPEN", 48),
    ):
        out = nta.run_adaptation_job()
        assert out.get("skipped") == "streak_below_min"

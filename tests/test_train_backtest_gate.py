"""Post-train backtest gate evaluation + frozen regime in backtest."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tr_bot import config
from tr_bot.backtest.runner import run_backtest_on_dataframe
from tr_bot.indicators import compute_indicators
from tr_bot.regime import RegimeModel
from tr_bot.training.backtest_gate import evaluate_train_backtest_gate

from tests.test_backtest import _synthetic_ohlcv


def test_evaluate_train_backtest_gate_min_return(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT", 0.0)
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MAX_DRAWDOWN_PCT", None)
    ok, reasons = evaluate_train_backtest_gate({"total_return_pct": -1.0, "max_drawdown_pct": 5.0})
    assert ok is False
    assert reasons


def test_evaluate_train_backtest_gate_max_dd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT", None)
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MAX_DRAWDOWN_PCT", 10.0)
    ok, reasons = evaluate_train_backtest_gate({"total_return_pct": 5.0, "max_drawdown_pct": 20.0})
    assert ok is False
    assert reasons


def test_evaluate_train_backtest_gate_permissive_defaults_when_no_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both unset: same permissive defaults as startup gate; normal metrics pass."""
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT", None)
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MAX_DRAWDOWN_PCT", None)
    ok, reasons = evaluate_train_backtest_gate({"total_return_pct": 10.0, "max_drawdown_pct": 1.0})
    assert ok is True
    assert not reasons


def test_evaluate_train_backtest_gate_defaults_still_fail_on_extreme_dd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT", None)
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MAX_DRAWDOWN_PCT", None)
    ok, reasons = evaluate_train_backtest_gate({"total_return_pct": 0.0, "max_drawdown_pct": 150.0})
    assert ok is False
    assert reasons


def test_evaluate_train_backtest_gate_defaults_fail_negative_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT", None)
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MAX_DRAWDOWN_PCT", None)
    ok, reasons = evaluate_train_backtest_gate({"total_return_pct": -2.0, "max_drawdown_pct": 1.0})
    assert ok is False
    assert reasons


def test_evaluate_train_backtest_gate_defaults_allow_small_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT", None)
    monkeypatch.setattr(config, "TRAIN_BACKTEST_MAX_DRAWDOWN_PCT", None)
    ok, reasons = evaluate_train_backtest_gate(
        {"total_return_pct": -0.0374, "max_drawdown_pct": 1.99}
    )
    assert ok is True
    assert not reasons
    ok2, reasons2 = evaluate_train_backtest_gate(
        {"total_return_pct": -0.5199, "max_drawdown_pct": 1.99}
    )
    assert ok2 is True
    assert not reasons2


def test_run_backtest_with_frozen_regime_model() -> None:
    df = _synthetic_ohlcv(420)
    feat = compute_indicators(df.copy()).dropna(subset=["rsi", "ema20", "ema50", "atr"])
    m = RegimeModel()
    m.train(feat.iloc[:300])
    with patch.object(config, "INITIAL_BARS", 220), patch.object(config, "USE_EXTENDED_TA", True):
        r = run_backtest_on_dataframe(
            df,
            symbol="TEST/USDT",
            timeframe="1m",
            regime_model=m,
            include_buy_hold=False,
        )
    assert r["ok"] is True
    notes = " ".join(r["policy"]["notes"])
    assert "frozen" in notes.lower()

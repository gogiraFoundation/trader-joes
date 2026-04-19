"""Time-series regime validation and training pipeline subsampling."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tr_bot.training import pipeline as training_pipeline
from tr_bot.training.regime_validation import (
    _time_series_cv_report,
    validation_gates_pass,
)


def test_validation_gates_pass_when_disabled_thresholds() -> None:
    report = {"oof_f1": 0.2, "mean_train_val_gap_accuracy": 0.4}
    ok, reasons = validation_gates_pass(
        report,
        min_oof_f1=0.0,
        max_train_val_gap=1.0,
    )
    assert ok is True
    assert reasons == []


def test_validation_gates_fail_low_f1() -> None:
    report = {"oof_f1": 0.1, "mean_train_val_gap_accuracy": 0.05}
    ok, reasons = validation_gates_pass(
        report,
        min_oof_f1=0.5,
        max_train_val_gap=1.0,
    )
    assert ok is False
    assert any("oof_f1" in r for r in reasons)


def test_validation_gates_fail_high_gap() -> None:
    report = {"oof_f1": 0.9, "mean_train_val_gap_accuracy": 0.5}
    ok, reasons = validation_gates_pass(
        report,
        min_oof_f1=0.0,
        max_train_val_gap=0.15,
    )
    assert ok is False
    assert any("gap" in r for r in reasons)


def test_time_series_cv_report_runs() -> None:
    rng = np.random.default_rng(7)
    n = 200
    X = pd.DataFrame(
        {
            "rsi": rng.normal(0, 1, n),
            "ema20": rng.normal(0, 1, n),
            "ema50": rng.normal(0, 1, n),
            "atr": rng.uniform(0.1, 2.0, n),
        }
    )
    y = pd.Series(rng.integers(0, 2, n))

    def builder():
        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(
            n_estimators=10,
            max_depth=4,
            random_state=0,
        )

    r = _time_series_cv_report(
        X,
        y,
        n_splits=5,
        min_rows_per_fold=80,
        model_builder=builder,
    )
    assert r["oof_f1"] >= 0.0
    assert r["mean_train_val_gap_accuracy"] is not None
    assert r["baseline_majority_accuracy"] >= 0.5
    assert len(r["folds"]) >= 1


def test_apply_train_max_rows_time_ordered_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot import config

    monkeypatch.setattr(config, "TRAIN_MAX_ROWS", 3)
    df = pd.DataFrame(
        {
            "timestamp": [10, 20, 30, 40, 50],
            "source": ["yahoo"] * 5,
        }
    )
    out = training_pipeline._apply_train_max_rows(df)
    assert len(out) == 3
    assert out["timestamp"].tolist() == [30, 40, 50]

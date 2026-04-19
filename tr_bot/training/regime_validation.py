"""Time-series CV and OOF metrics for regime classifier (overfitting diagnostics)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    f1_score,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit

from tr_bot.training.regime_dataset import build_regime_xy, sort_training_frame


def _majority_class_accuracy(y: np.ndarray) -> float:
    if len(y) == 0:
        return 0.0
    _, counts = np.unique(y, return_counts=True)
    return float(np.max(counts) / len(y))


def _always_zero_accuracy(y: np.ndarray) -> float:
    """Baseline: always predict class 0 (range)."""
    if len(y) == 0:
        return 0.0
    return float(np.mean(y == 0))


def _time_series_cv_report(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    n_splits: int,
    min_rows_per_fold: int,
    model_builder: Callable[[], RandomForestClassifier],
) -> dict[str, Any]:
    n = len(X)
    if n < min_rows_per_fold:
        raise ValueError(
            f"Not enough rows for regime validation: {n} < TRAIN_MIN_ROWS_PER_FOLD={min_rows_per_fold}"
        )
    if n < n_splits + 2:
        raise ValueError(
            f"Not enough rows for TimeSeriesSplit(n_splits={n_splits}): need at least n_splits+2"
        )

    tscv = TimeSeriesSplit(n_splits=n_splits)
    train_scores: list[float] = []
    val_scores: list[float] = []
    val_balanced: list[float] = []
    gaps: list[float] = []
    fold_rows: list[dict[str, Any]] = []

    oof_pred = np.full(n, np.nan)
    oof_proba: np.ndarray | None = None

    for fold_idx, (train_idx, val_idx) in enumerate(tscv.split(X)):
        if len(val_idx) == 0:
            continue
        clf = model_builder()
        clf.fit(X.iloc[train_idx], y.iloc[train_idx])
        y_tr = y.iloc[train_idx].values
        y_va = y.iloc[val_idx].values
        p_tr = clf.predict(X.iloc[train_idx])
        p_va = clf.predict(X.iloc[val_idx])
        acc_tr = accuracy_score(y_tr, p_tr)
        acc_va = accuracy_score(y_va, p_va)
        b_va = balanced_accuracy_score(y_va, p_va)
        train_scores.append(acc_tr)
        val_scores.append(acc_va)
        val_balanced.append(b_va)
        gaps.append(acc_tr - acc_va)
        oof_pred[val_idx] = p_va
        if hasattr(clf, "predict_proba"):
            try:
                proba = clf.predict_proba(X.iloc[val_idx])
                if oof_proba is None:
                    oof_proba = np.full((n, proba.shape[1]), np.nan)
                if proba.shape[1] == oof_proba.shape[1]:
                    oof_proba[val_idx] = proba
            except (ValueError, IndexError):
                pass
        fold_rows.append(
            {
                "fold": fold_idx,
                "train_size": len(train_idx),
                "val_size": len(val_idx),
                "train_accuracy": round(acc_tr, 6),
                "val_accuracy": round(acc_va, 6),
                "val_balanced_accuracy": round(b_va, 6),
                "gap_accuracy": round(acc_tr - acc_va, 6),
            }
        )

    valid = ~np.isnan(oof_pred)
    y_oof = y.values[valid]
    p_oof = oof_pred[valid].astype(int)

    oof_f1 = float(f1_score(y_oof, p_oof, average="binary", zero_division=0))
    oof_accuracy = float(accuracy_score(y_oof, p_oof))
    oof_balanced = float(balanced_accuracy_score(y_oof, p_oof))

    oof_auc: float | None = None
    if oof_proba is not None and oof_proba.shape[1] >= 2:
        pr = oof_proba[valid]
        if len(np.unique(y_oof)) >= 2:
            try:
                oof_auc = float(roc_auc_score(y_oof, pr[:, 1]))
            except ValueError:
                oof_auc = None

    y_all = y.values
    maj = _majority_class_accuracy(y_all)
    z0 = _always_zero_accuracy(y_all)

    return {
        "mode": "pooled",
        "n_rows": n,
        "n_splits": n_splits,
        "folds": fold_rows,
        "mean_train_accuracy": float(np.mean(train_scores)) if train_scores else None,
        "mean_val_accuracy": float(np.mean(val_scores)) if val_scores else None,
        "mean_val_balanced_accuracy": float(np.mean(val_balanced)) if val_balanced else None,
        "mean_train_val_gap_accuracy": float(np.mean(gaps)) if gaps else None,
        "oof_accuracy": oof_accuracy,
        "oof_balanced_accuracy": oof_balanced,
        "oof_f1": oof_f1,
        "oof_roc_auc": oof_auc,
        "baseline_majority_accuracy": maj,
        "baseline_always_range_accuracy": z0,
    }


def _validate_per_group(
    feat: pd.DataFrame,
    *,
    n_splits: int,
    min_rows_per_fold: int,
    model_builder: Callable[[], RandomForestClassifier],
) -> dict[str, Any]:
    if "source" not in feat.columns:
        raise ValueError("TRAIN_VALIDATION_PER_GROUP requires a source column")
    key = ["source", "asset"] if "asset" in feat.columns else ["source"]
    group_reports: list[dict[str, Any]] = []
    for keyval, sub in feat.groupby(key):
        sub = sub.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
        try:
            X, y = build_regime_xy(sub)
        except ValueError:
            continue
        if len(X) < min_rows_per_fold:
            continue
        rep = _time_series_cv_report(
            X,
            y,
            n_splits=n_splits,
            min_rows_per_fold=min_rows_per_fold,
            model_builder=model_builder,
        )
        rep["group"] = keyval if isinstance(keyval, tuple) else (keyval,)
        group_reports.append(rep)

    if not group_reports:
        raise ValueError(
            "Per-group validation: no series had enough rows for TRAIN_MIN_ROWS_PER_FOLD and CV"
        )

    oof_f1s = [float(r["oof_f1"]) for r in group_reports]
    gaps = [float(r["mean_train_val_gap_accuracy"] or 0) for r in group_reports]

    return {
        "mode": "per_group",
        "n_groups": len(group_reports),
        "groups": group_reports,
        "oof_f1": float(np.median(oof_f1s)),
        "oof_accuracy": float(np.median([float(r["oof_accuracy"]) for r in group_reports])),
        "oof_balanced_accuracy": float(
            np.median([float(r["oof_balanced_accuracy"]) for r in group_reports])
        ),
        "mean_train_val_gap_accuracy": float(np.median(gaps)),
        "mean_train_accuracy": float(
            np.median([float(r["mean_train_accuracy"] or 0) for r in group_reports])
        ),
        "mean_val_accuracy": float(
            np.median([float(r["mean_val_accuracy"] or 0) for r in group_reports])
        ),
        "oof_roc_auc": None,
        "n_rows": int(sum(int(r["n_rows"]) for r in group_reports)),
    }


def validate_regime_frame(
    feat: pd.DataFrame,
    model_builder: Callable[[], RandomForestClassifier],
    *,
    n_splits: int,
    min_rows_per_fold: int,
    per_group: bool,
) -> dict[str, Any]:
    feat = sort_training_frame(feat)
    if per_group:
        return _validate_per_group(
            feat,
            n_splits=n_splits,
            min_rows_per_fold=min_rows_per_fold,
            model_builder=model_builder,
        )
    X, y = build_regime_xy(feat)
    return _time_series_cv_report(
        X,
        y,
        n_splits=n_splits,
        min_rows_per_fold=min_rows_per_fold,
        model_builder=model_builder,
    )


def validation_gates_pass(
    report: dict[str, Any],
    *,
    min_oof_f1: float,
    max_train_val_gap: float,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    oof_f1 = float(report.get("oof_f1") or 0.0)
    gap = report.get("mean_train_val_gap_accuracy")
    gap_f = float(gap) if gap is not None else 0.0

    if min_oof_f1 > 0.0 and oof_f1 < min_oof_f1:
        reasons.append(f"oof_f1={oof_f1:.4f} < TRAIN_MIN_OOF_F1={min_oof_f1}")
    if max_train_val_gap < 1.0 and gap_f > max_train_val_gap:
        reasons.append(
            f"mean_train_val_gap_accuracy={gap_f:.4f} > TRAIN_MAX_TRAIN_VAL_GAP={max_train_val_gap}"
        )
    return len(reasons) == 0, reasons


def compact_validation_report(report: dict[str, Any]) -> dict[str, Any]:
    """Small summary for last_run / dashboard (omit per-fold tables)."""
    keys = (
        "mode",
        "n_rows",
        "n_groups",
        "n_splits",
        "oof_f1",
        "oof_accuracy",
        "oof_balanced_accuracy",
        "oof_roc_auc",
        "mean_train_accuracy",
        "mean_val_accuracy",
        "mean_train_val_gap_accuracy",
        "baseline_majority_accuracy",
        "baseline_always_range_accuracy",
    )
    out = {k: report[k] for k in keys if k in report and report[k] is not None}
    return out


"""ML regime detection (Section 4.3)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from tr_bot.training.regime_dataset import build_regime_xy


class RegimeModel:
    def __init__(
        self,
        *,
        n_estimators: int = 100,
        random_state: int = 42,
        max_depth: int | None = 8,
        min_samples_leaf: int = 1,
        max_features: str | float | None = None,
    ) -> None:
        kw: dict = dict(
            n_estimators=n_estimators,
            random_state=random_state,
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
        )
        if max_features is not None:
            kw["max_features"] = max_features
        self.model = RandomForestClassifier(**kw)
        self.feature_columns_: tuple[str, ...] | None = None

    def train(self, df: pd.DataFrame) -> None:
        X, y = build_regime_xy(df)
        self.feature_columns_ = tuple(X.columns)
        self.model.fit(X, y)

    def predict(self, df: pd.DataFrame) -> str:
        regime, _confidence = self.predict_with_confidence(df)
        return regime

    def predict_with_confidence(self, df: pd.DataFrame) -> tuple[str, float]:
        cols = getattr(self, "feature_columns_", None) or ("rsi", "ema20", "ema50", "atr")
        missing = [c for c in cols if c not in df.columns]
        if missing:
            return "range", 0.5
        row = df[list(cols)].tail(1)
        if row.isna().any(axis=None):
            return "range", 0.5
        regime = int(self.model.predict(row)[0])
        confidence = 0.5
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(row)[0]
            confidence = float(max(proba)) if len(proba) else 0.5
        return ("trend" if regime == 1 else "range"), confidence

    def save(self, path: str | Path) -> None:
        import joblib

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, p)

    @classmethod
    def load(cls, path: str | Path) -> RegimeModel:
        import joblib

        return joblib.load(path)

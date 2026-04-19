"""Feature matrix and labels for regime training (shared with validation)."""

from __future__ import annotations

import pandas as pd

from tr_bot import config

FEATURE_COLS = ("rsi", "ema20", "ema50", "atr")
MACRO_FEATURE_COLS = (
    "cmc_macro_total_market_cap",
    "cmc_macro_total_volume_24h",
    "cmc_macro_btc_dominance",
    "cmc_macro_fng_value",
    "cmc_macro_altcoin_season",
)
REQUIRED_FOR_XY = (*FEATURE_COLS, "close")


def effective_regime_feature_columns(df: pd.DataFrame) -> tuple[str, ...]:
    """Base TA columns plus optional CMC macro columns when enabled and present."""
    base = list(FEATURE_COLS)
    if not config.TRAIN_REGIME_MACRO_FEATURES:
        return tuple(base)
    extra = [c for c in MACRO_FEATURE_COLS if c in df.columns]
    return tuple(base + extra)


def sort_training_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Chronological order for time-series validation (stable tie-break)."""
    cols = ["timestamp"]
    for c in ("source", "asset"):
        if c in df.columns:
            cols.append(c)
    return df.sort_values(cols, kind="mergesort").reset_index(drop=True)


def build_regime_xy(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """
    Same logic as RegimeModel.train(): indicators + rolling label vs EMA20.
    Raises if too few rows after cleaning (matches training contract).
    """
    work = df.dropna(subset=list(REQUIRED_FOR_XY)).copy()
    if len(work) < 50:
        raise ValueError("Not enough rows to train regime model")
    feat_cols = list(effective_regime_feature_columns(work))
    X = work[feat_cols]
    y = (
        (work["close"] > work["ema20"]).rolling(3).mean().fillna(0) > 0.5
    ).astype(int)
    mask = ~(X.isna().any(axis=1) | y.isna())
    X = X.loc[mask]
    y = y.loc[mask]
    if len(X) < 20:
        raise ValueError("Not enough valid labels for regime training")
    return X, y

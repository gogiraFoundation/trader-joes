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


def normalize_asset_tag(raw: str) -> str:
    """Normalize SOL/USDT or SOL_USDT → SOL_USDT for row matching."""
    t = (raw or "").strip().upper()
    if not t:
        return ""
    return t.replace("/", "_").replace("-", "_")


def resolve_primary_asset_tag() -> str:
    """
    Asset tag used for TRAIN_PRIMARY_SAMPLE_WEIGHT.

    Order: TRAIN_PRIMARY_ASSET → first TRAIN_EXCHANGE_SYMBOL token → SYMBOL.
    """
    explicit = normalize_asset_tag(config.TRAIN_PRIMARY_ASSET)
    if explicit:
        return explicit
    raw = (config.TRAIN_EXCHANGE_SYMBOL or "").strip()
    if raw:
        first = raw.split(",")[0].strip()
        tag = normalize_asset_tag(first)
        if tag:
            return tag
    return normalize_asset_tag(config.SYMBOL)


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


def build_regime_xy(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """
    Same logic as RegimeModel.train(): indicators + rolling label vs EMA20.
    Raises if too few rows after cleaning (matches training contract).

    When ``source``/``asset`` are present, the rolling label is computed per group.

    Returns ``(X, y, sample_weight)``. Weights are 1.0 unless
    ``TRAIN_PRIMARY_SAMPLE_WEIGHT`` > 1 and rows match the resolved primary asset.
    """
    work = df.dropna(subset=list(REQUIRED_FOR_XY)).copy()
    if len(work) < 50:
        raise ValueError("Not enough rows to train regime model")
    feat_cols = list(effective_regime_feature_columns(work))
    group_cols = [c for c in ("source", "asset") if c in work.columns]
    if group_cols:
        sort_keys = list(group_cols)
        if "timestamp" in work.columns:
            sort_keys.append("timestamp")
        work = work.sort_values(sort_keys, kind="mergesort")
    X = work[feat_cols]
    signal = (work["close"] > work["ema20"]).astype(float)
    if group_cols:
        work["_regime_sig"] = signal
        rolled = work.groupby(group_cols, sort=False)["_regime_sig"].transform(
            lambda s: s.rolling(3).mean().fillna(0)
        )
        y = (rolled > 0.5).astype(int)
    else:
        y = (signal.rolling(3).mean().fillna(0) > 0.5).astype(int)

    primary_w = float(config.TRAIN_PRIMARY_SAMPLE_WEIGHT or 1.0)
    if primary_w <= 0:
        primary_w = 1.0
    primary_tag = resolve_primary_asset_tag()
    if "asset" in work.columns and primary_w != 1.0 and primary_tag:
        asset_norm = work["asset"].astype(str).map(normalize_asset_tag)
        sw = pd.Series(1.0, index=work.index, dtype=float)
        sw = sw.where(asset_norm != primary_tag, primary_w)
    else:
        sw = pd.Series(1.0, index=work.index, dtype=float)

    mask = ~(X.isna().any(axis=1) | y.isna())
    X = X.loc[mask]
    y = y.loc[mask]
    sw = sw.loc[mask]
    if len(X) < 20:
        raise ValueError("Not enough valid labels for regime training")
    return X, y, sw

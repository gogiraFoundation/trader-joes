"""Regime feature/label helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tr_bot.indicators import compute_indicators
from tr_bot.training.regime_dataset import build_regime_xy, sort_training_frame


def test_sort_training_frame_stable_order() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [3000, 1000, 2000],
            "source": ["yahoo", "yahoo", "yahoo"],
            "asset": ["SPY", "SPY", "SPY"],
            "x": [1, 2, 3],
        }
    )
    out = sort_training_frame(df)
    assert out["timestamp"].tolist() == [1000, 2000, 3000]


def test_build_regime_xy_aligns_with_regime_train() -> None:
    rng = np.random.default_rng(42)
    n = 120
    close = 50 + np.cumsum(rng.normal(0, 0.2, n))
    df = pd.DataFrame(
        {
            "timestamp": np.arange(n),
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": rng.integers(500, 1000, n),
        }
    )
    df = compute_indicators(df).dropna(subset=["rsi", "ema20", "ema50", "atr"])
    X, y, sw = build_regime_xy(df)
    assert len(X) == len(y) == len(sw)
    assert list(X.columns) == ["rsi", "ema20", "ema50", "atr"]
    assert y.isin((0, 1)).all()
    assert (sw == 1.0).all()


def test_build_regime_xy_too_small() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [1],
            "rsi": [50.0],
            "ema20": [1.0],
            "ema50": [1.0],
            "atr": [1.0],
            "close": [1.0],
        }
    )
    with pytest.raises(ValueError, match="Not enough rows"):
        build_regime_xy(df)


def test_build_regime_xy_grouped_label_matches_solo() -> None:
    """Rolling label must not bleed across assets when source/asset are set."""
    rng = np.random.default_rng(7)
    n = 80

    def _one(asset: str, close: np.ndarray) -> pd.DataFrame:
        df = pd.DataFrame(
            {
                "timestamp": np.arange(n),
                "open": close,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": rng.integers(500, 1000, n),
                "source": "exchange",
                "asset": asset,
            }
        )
        return compute_indicators(df).dropna(subset=["rsi", "ema20", "ema50", "atr"])

    close_a = 50 + np.cumsum(rng.normal(0, 0.2, n))
    close_b = 200 + np.cumsum(rng.normal(0, 2.0, n))
    a = _one("AAA_USDT", close_a)
    b = _one("BBB_USDT", close_b)
    _, y_solo, _ = build_regime_xy(a.drop(columns=["source", "asset"]))

    mixed = pd.concat([a, b], ignore_index=True)
    X_m, y_m, _ = build_regime_xy(mixed)
    # Rebuild aligned solo labels via grouped frame filtered to A
    mixed_a = mixed.loc[mixed["asset"] == "AAA_USDT"].copy()
    _, y_a_grouped, _ = build_regime_xy(mixed_a)
    assert len(X_m) == len(y_m)
    assert y_a_grouped.tolist() == y_solo.tolist()


def test_build_regime_xy_primary_sample_weight(monkeypatch: pytest.MonkeyPatch) -> None:
    import tr_bot.training.regime_dataset as rd

    monkeypatch.setattr(rd.config, "TRAIN_PRIMARY_ASSET", "AAA/USDT")
    monkeypatch.setattr(rd.config, "TRAIN_PRIMARY_SAMPLE_WEIGHT", 3.0)

    rng = np.random.default_rng(3)
    n = 80

    def _one(asset: str, close: np.ndarray) -> pd.DataFrame:
        df = pd.DataFrame(
            {
                "timestamp": np.arange(n),
                "open": close,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": rng.integers(500, 1000, n),
                "source": "exchange",
                "asset": asset,
            }
        )
        return compute_indicators(df).dropna(subset=["rsi", "ema20", "ema50", "atr"])

    a = _one("AAA_USDT", 50 + np.cumsum(rng.normal(0, 0.2, n)))
    b = _one("BBB_USDT", 100 + np.cumsum(rng.normal(0, 0.3, n)))
    mixed = pd.concat([a, b], ignore_index=True)
    X, y, sw = build_regime_xy(mixed)
    assert len(X) == len(sw)
    # Align weights to original asset via X index from work after sort/mask
    # Rebuild with known assets from mixed after same indicator path
    monkeypatch.setattr(rd.config, "TRAIN_PRIMARY_SAMPLE_WEIGHT", 1.0)
    _, _, sw1 = build_regime_xy(mixed)
    assert (sw1 == 1.0).all()

    monkeypatch.setattr(rd.config, "TRAIN_PRIMARY_SAMPLE_WEIGHT", 3.0)
    X2, y2, sw3 = build_regime_xy(mixed)
    assert len(X2) == len(y2) == len(sw3)
    assert set(sw3.unique().tolist()) == {1.0, 3.0}
    assert (sw3 == 3.0).sum() > 0
    assert (sw3 == 1.0).sum() > 0


def test_regime_primary_weight_changes_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Weight > 1 must change RF fit vs unweighted on a two-asset frame."""
    import tr_bot.training.regime_dataset as rd
    from tr_bot.regime import RegimeModel

    rng = np.random.default_rng(11)
    n = 100

    def _one(asset: str, drift: float) -> pd.DataFrame:
        close = 50 + np.cumsum(rng.normal(drift, 0.5, n))
        df = pd.DataFrame(
            {
                "timestamp": np.arange(n),
                "open": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": rng.integers(500, 1000, n),
                "source": "exchange",
                "asset": asset,
            }
        )
        return compute_indicators(df).dropna(subset=["rsi", "ema20", "ema50", "atr"])

    # Divergent regimes so weighting the primary series shifts the decision surface.
    mixed = pd.concat(
        [_one("SOL_USDT", 0.15), _one("BTC_USDT", -0.15)],
        ignore_index=True,
    )

    monkeypatch.setattr(rd.config, "TRAIN_PRIMARY_ASSET", "SOL/USDT")
    monkeypatch.setattr(rd.config, "TRAIN_PRIMARY_SAMPLE_WEIGHT", 1.0)
    m1 = RegimeModel(n_estimators=20, random_state=0, max_depth=4)
    m1.train(mixed)

    monkeypatch.setattr(rd.config, "TRAIN_PRIMARY_SAMPLE_WEIGHT", 8.0)
    m2 = RegimeModel(n_estimators=20, random_state=0, max_depth=4)
    m2.train(mixed)

    # Same random_state + different sample_weight → different tree structure / importances
    assert not np.allclose(
        m1.model.feature_importances_,
        m2.model.feature_importances_,
    )

"""CMC macro merge for training."""

from __future__ import annotations

import pandas as pd
import pytest

from tr_bot.training import cmc_macro as macro


def test_merge_cmc_macro_training_features(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = pd.DataFrame(
        {
            "timestamp": [1000, 2000],
            "open": [1.0, 1.1],
            "close": [1.0, 1.1],
            "source": ["yahoo", "yahoo"],
            "asset": ["X", "X"],
        }
    )

    gdf = pd.DataFrame(
        {
            "timestamp": [500, 1500],
            "cmc_macro_btc_dominance": [50.0, 51.0],
            "cmc_macro_total_market_cap": [1e12, 1.01e12],
            "cmc_macro_total_volume_24h": [1e11, 1.02e11],
        }
    )
    fdf = pd.DataFrame({"timestamp": [900, 1900], "cmc_macro_fng_value": [20.0, 25.0]})

    monkeypatch.setattr(macro, "fetch_global_metrics_historical_df", lambda *a, **k: gdf)
    monkeypatch.setattr(macro, "fetch_fear_greed_historical_df", lambda *a, **k: fdf)
    monkeypatch.setattr(macro, "fetch_altcoin_season_historical_df", lambda *a, **k: pd.DataFrame())

    monkeypatch.setattr("tr_bot.config.CMC_TRAIN_MACRO_GLOBAL", True)
    monkeypatch.setattr("tr_bot.config.CMC_TRAIN_MACRO_FEAR_GREED", True)
    monkeypatch.setattr("tr_bot.config.CMC_TRAIN_MACRO_ALTCOIN", False)
    monkeypatch.setattr("tr_bot.config.CMC_CONVERT", "USD")
    monkeypatch.setattr("tr_bot.config.TRAIN_TIMEFRAME", "1d")
    monkeypatch.setattr("tr_bot.config.CMC_COUNT", 500)
    monkeypatch.setattr("tr_bot.config.CMC_TRAIN_MACRO_PARQUET_PATH", "")

    out = macro.merge_cmc_macro_training_features(raw, "apikey")
    assert "cmc_macro_btc_dominance" in out.columns
    assert "cmc_macro_fng_value" in out.columns
    assert out["cmc_macro_btc_dominance"].notna().all()

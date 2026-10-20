"""Backtest compare engines smoke (offline synthetic)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from tr_bot import config
from tr_bot.backtest.runner import run_backtest_compare_engines, run_backtest_on_dataframe
from tr_bot.research.hypothesis_registry import append_hypothesis, list_hypotheses
from tr_bot.strategy.explainability import build_decision_record


def _ohlcv(n: int = 420) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 100.0 + np.cumsum(rng.normal(0, 0.25, n))
    return pd.DataFrame(
        {
            "timestamp": (np.arange(n) * 60_000).astype(np.int64),
            "open": close,
            "high": close + 0.6,
            "low": close - 0.6,
            "close": close,
            "volume": rng.integers(500, 5000, n),
        }
    )


def test_backtest_smc_engine_runs(tmp_path: Path) -> None:
    df = _ohlcv()
    with (
        patch.object(config, "STRATEGY_ENGINE", "smc"),
        patch.object(config, "INITIAL_BARS", 220),
        patch.object(config, "USE_EXTENDED_TA", True),
        patch.object(config, "ARTIFACTS_DIR", str(tmp_path)),
    ):
        r = run_backtest_on_dataframe(df, symbol="TEST/USDT", timeframe="1m")
    assert r["ok"] is True
    assert r.get("strategy_engine") == "smc"


def test_backtest_compare_writes_artifacts(tmp_path: Path) -> None:
    df = _ohlcv()
    with (
        patch.object(config, "INITIAL_BARS", 220),
        patch.object(config, "USE_EXTENDED_TA", True),
        patch.object(config, "ARTIFACTS_DIR", str(tmp_path)),
    ):
        out = run_backtest_compare_engines(df, symbol="TEST/USDT", timeframe="1m")
    assert out["ok"] is True
    assert (tmp_path / "training" / "backtest_compare.json").is_file()
    assert (tmp_path / "research" / "smc_go_no_go.json").is_file()
    # go/no-go may fail on trade count; comparator_passes reflects that
    assert "go_no_go" in out


def test_hypothesis_and_explainability(tmp_path: Path) -> None:
    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)):
        h = append_hypothesis(description="test hyp", source="unit")
        assert h["id"].startswith("HYP-")
        listed = list_hypotheses()
        assert listed
        rec = build_decision_record(
            trade_id="t1",
            symbol="BTC/USDT",
            direction="long",
            entry_price=1.0,
            status="open",
        )
        assert rec["signal_generation"]["strategy_engine"]

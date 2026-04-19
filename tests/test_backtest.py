"""Backtest runner + last_run persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from tr_bot import config
from tr_bot.backtest.last_run import read_backtest_last_run, write_backtest_last_run
from tr_bot.backtest.runner import (
    _BacktestDailyOpenLimiter,
    run_backtest_ablation_matrix,
    run_backtest_on_dataframe,
)


def _synthetic_ohlcv(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(99)
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


def test_run_backtest_on_dataframe_metrics() -> None:
    df = _synthetic_ohlcv(420)
    with patch.object(config, "INITIAL_BARS", 220), patch.object(config, "USE_EXTENDED_TA", True):
        r = run_backtest_on_dataframe(
            df,
            symbol="TEST/USDT",
            timeframe="1m",
            include_buy_hold=True,
        )
    assert r["ok"] is True
    assert r["symbol"] == "TEST/USDT"
    assert r["timeframe"] == "1m"
    assert r["bars_total"] >= 80
    assert r["bars_simulated"] >= 1
    assert isinstance(r["trades"], int)
    assert r["trades"] >= 0
    assert "total_return_pct" in r
    assert "max_drawdown_pct" in r
    assert isinstance(r.get("equity_points"), list)
    assert "policy" in r
    assert r["policy"]["sentiment_mode"] in ("neutral", "bullish", "bearish")
    assert "benchmark_buy_hold" in r


def test_backtest_costs_reduce_or_match_equity() -> None:
    df = _synthetic_ohlcv(420)
    with patch.object(config, "INITIAL_BARS", 220), patch.object(config, "USE_EXTENDED_TA", True):
        base = run_backtest_on_dataframe(
            df,
            symbol="TEST/USDT",
            timeframe="1m",
            fee_bps=0.0,
            slippage_bps=0.0,
        )
        costly = run_backtest_on_dataframe(
            df,
            symbol="TEST/USDT",
            timeframe="1m",
            fee_bps=80.0,
            slippage_bps=40.0,
        )
    assert costly["final_equity"] <= base["final_equity"] + 1e-6


def test_backtest_fundamentals_never_pass_limits_trades() -> None:
    df = _synthetic_ohlcv(420)
    with patch.object(config, "INITIAL_BARS", 220), patch.object(config, "USE_EXTENDED_TA", True):
        always = run_backtest_on_dataframe(
            df,
            symbol="TEST/USDT",
            timeframe="1m",
            fundamentals_mode="always_pass",
        )
        blocked = run_backtest_on_dataframe(
            df,
            symbol="TEST/USDT",
            timeframe="1m",
            fundamentals_mode="never_pass",
        )
    assert blocked["trades"] <= always["trades"]


def test_backtest_audit_sample_when_stride() -> None:
    df = _synthetic_ohlcv(420)
    with patch.object(config, "INITIAL_BARS", 220), patch.object(config, "USE_EXTENDED_TA", True):
        r = run_backtest_on_dataframe(
            df,
            symbol="TEST/USDT",
            timeframe="1m",
            audit_sample_stride=5,
        )
    assert "audit_sample" in r
    assert len(r["audit_sample"]) >= 1
    row = r["audit_sample"][0]
    assert set(row.keys()) >= {"regime", "signal", "sentiment", "ta_ok", "fundamentals_ok", "decision"}


def test_backtest_ablation_matrix_shape() -> None:
    df = _synthetic_ohlcv(420)
    with patch.object(config, "INITIAL_BARS", 220), patch.object(config, "USE_EXTENDED_TA", True):
        m = run_backtest_ablation_matrix(df, symbol="TEST/USDT", timeframe="1m")
    assert m["ok"] is True
    assert len(m["ablations"]) == 6
    one = next(iter(m["ablations"].values()))
    assert "policy" in one


def test_write_and_read_backtest_last_run(tmp_path: Path) -> None:
    root = tmp_path / "art"
    root.mkdir()
    with patch.object(config, "ARTIFACTS_DIR", str(root)):
        write_backtest_last_run({"ok": True, "symbol": "X/Y", "trades": 0})
        path = root / "training" / "backtest_last_run.json"
        assert path.is_file()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["ok"] is True
        assert "finished_utc" in raw
        again = read_backtest_last_run()
        assert again is not None
        assert again.get("symbol") == "X/Y"


def test_backtest_daily_open_limiter_resets_per_utc_day() -> None:
    lim = _BacktestDailyOpenLimiter(2)
    t1 = int(datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t2 = int(datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    lim.on_bar(pd.Series({"timestamp": t1}))
    assert lim.can_open()
    lim.record_open()
    lim.record_open()
    assert not lim.can_open()
    lim.on_bar(pd.Series({"timestamp": t2}))
    assert lim.can_open()

"""Optional post-train walk-forward backtest using the freshly trained regime model."""

from __future__ import annotations

import time
from typing import Any

from tr_bot import config
from tr_bot.backtest.runner import run_backtest_on_dataframe
from tr_bot.startup.backtest_gate_thresholds import DEFAULT_MAX_DRAWDOWN_PCT, DEFAULT_MIN_RETURN_PCT
from tr_bot.logger import setup_logger
from tr_bot.market_data import fetch_ohlcv_deep
from tr_bot.regime import RegimeModel

logger = setup_logger("tr_bot.training.backtest_gate")


def run_train_backtest_gate(regime: RegimeModel) -> dict[str, Any]:
    symbol = config.TRAIN_BACKTEST_SYMBOL.strip() or config.SYMBOL
    bars = config.TRAIN_BACKTEST_BARS or config.BACKTEST_BARS
    tf = config.TRAIN_BACKTEST_TIMEFRAME.strip() or config.TIMEFRAME
    logger.info(
        "Train backtest gate: fetch %s %s limit=%s (frozen regime model)",
        symbol,
        tf,
        bars,
    )
    t0 = time.perf_counter()
    df = fetch_ohlcv_deep(symbol, tf, limit=bars)
    elapsed_s = time.perf_counter() - t0
    logger.info(
        "Train backtest gate: fetch finished in %.1fs (%s rows)",
        elapsed_s,
        len(df),
    )
    return run_backtest_on_dataframe(
        df,
        symbol=symbol,
        timeframe=tf,
        regime_model=regime,
        include_buy_hold=False,
        audit_sample_stride=0,
    )


# When TRAIN_BACKTEST_* are both unset, align with startup gate defaults (backtest_gate_thresholds).


def evaluate_train_backtest_gate(bt: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    min_ret = config.TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT
    max_dd = config.TRAIN_BACKTEST_MAX_DRAWDOWN_PCT
    if min_ret is None and max_dd is None:
        min_ret = DEFAULT_MIN_RETURN_PCT
        max_dd = DEFAULT_MAX_DRAWDOWN_PCT
        logger.info(
            "Train backtest gate: no TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT / TRAIN_BACKTEST_MAX_DRAWDOWN_PCT; "
            "using defaults from backtest_gate_thresholds (set either env var to override)"
        )
    tr = float(bt.get("total_return_pct") or 0.0)
    dd = float(bt.get("max_drawdown_pct") or 0.0)
    if min_ret is not None and tr < min_ret:
        reasons.append(
            f"total_return_pct={tr} < TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT={min_ret}"
        )
    if max_dd is not None and dd > max_dd:
        reasons.append(
            f"max_drawdown_pct={dd} > TRAIN_BACKTEST_MAX_DRAWDOWN_PCT={max_dd}"
        )
    return len(reasons) == 0, reasons

"""Side-by-side strategy engine comparison for promotion decisions."""

from __future__ import annotations

import logging
from typing import Any

from tr_bot.backtest.runner import run_backtest_compare_engines

logger = logging.getLogger("tr_bot.promotion.comparator")


def _mann_whitney(a: list[float], b: list[float]) -> dict[str, Any]:
    try:
        from scipy import stats  # type: ignore

        if len(a) < 3 or len(b) < 3:
            return {"ok": False, "reason": "insufficient_samples"}
        u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
        return {"ok": True, "u_statistic": float(u), "p_value": float(p)}
    except Exception as e:
        return {"ok": False, "reason": f"scipy_unavailable_or_failed: {e}"}


def compare_engines(
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    """
    Compare legacy vs smc on identical OHLCV via shared backtest harness.
    """
    out = run_backtest_compare_engines(
        symbol=symbol,
        timeframe=timeframe,
        write_artifacts=write_artifacts,
    )
    # Optional per-trade return test if equity points present — skip gracefully
    out["mann_whitney"] = {"ok": False, "reason": "deferred_no_per_trade_series"}
    return out


def comparator_passes(result: dict[str, Any]) -> bool:
    go = result.get("go_no_go") or {}
    return bool(result.get("ok")) and bool(go.get("passed"))

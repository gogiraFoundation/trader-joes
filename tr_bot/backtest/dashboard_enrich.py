"""Flatten backtest last_run for dashboard when ablation matrix is stored."""

from __future__ import annotations

from typing import Any

from tr_bot import config


def enrich_backtest_last_run_for_dashboard(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    When BACKTEST_RUN_ABLATION_MATRIX is true, metrics live under `ablations.{key}` only.
    Copy the run matching BACKTEST_SENTIMENT_MODE + BACKTEST_SKIP_TA_FILTER to top-level
    so the dashboard can show return / DD / trades without JSON shape changes in the UI.
    """
    if not raw or not isinstance(raw, dict):
        return raw
    if raw.get("total_return_pct") is not None:
        return raw
    ablations = raw.get("ablations")
    if not isinstance(ablations, dict) or not ablations:
        return raw

    sent = (config.BACKTEST_SENTIMENT_MODE or "neutral").strip().lower()
    ta_part = "skip" if config.BACKTEST_SKIP_TA_FILTER else "on"
    preferred_key = f"{sent}_ta_{ta_part}"

    def _has_metrics(v: Any) -> bool:
        if not isinstance(v, dict):
            return False
        if v.get("total_return_pct") is not None:
            return True
        m = v.get("metrics")
        return isinstance(m, dict) and m.get("total_return_pct") is not None

    def _pick_metric(v: dict[str, Any], field: str) -> Any:
        if v.get(field) is not None:
            return v.get(field)
        m = v.get("metrics")
        if isinstance(m, dict):
            return m.get(field)
        return None

    primary = ablations.get(preferred_key)
    chosen_key = preferred_key
    if not _has_metrics(primary):
        primary = None
        chosen_key = ""
        for k in sorted(ablations.keys()):
            v = ablations[k]
            if _has_metrics(v):
                primary = v
                chosen_key = k
                break
    if not isinstance(primary, dict):
        return raw

    out = dict(raw)
    for field in (
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "sharpe_ratio_annualized",
        "sortino_ratio_annualized",
        "risk_metric_meta",
        "execution_stats",
        "trades",
        "wins",
        "losses",
        "bars_total",
        "bars_simulated",
        "trained_on_rows",
        "final_equity",
    ):
        value = _pick_metric(primary, field)
        if value is not None:
            out[field] = value
    if out.get("trades") is None and out.get("wins") is not None and out.get("losses") is not None:
        out["trades"] = int(out["wins"]) + int(out["losses"])
    out["primary_ablation_key"] = chosen_key
    return out

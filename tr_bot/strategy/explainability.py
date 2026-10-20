"""Append HIATP-style decision records for opened/closed trades."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path

logger = logging.getLogger("tr_bot.strategy.explainability")

RECORDS_REL = "decision_records.jsonl"


def _path() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    return safe_artifact_path(root, RECORDS_REL)


def build_decision_record(
    *,
    trade_id: str,
    symbol: str,
    direction: str,
    entry_price: float | None,
    status: str,
    proposal: Any | None = None,
    context: dict[str, Any] | None = None,
    risk: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    score_components = getattr(proposal, "score_components", None) if proposal else None
    pipeline_steps = getattr(proposal, "pipeline_steps", None) if proposal else None
    rec: dict[str, Any] = {
        "trade_id": trade_id,
        "version": "hiatp-1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "status": status,
        "signal_generation": {
            "strategy_engine": str(getattr(config, "STRATEGY_ENGINE", "legacy")),
            "strategy_version": config.ACTIVE_STRATEGY_PROFILE_ID or None,
            "min_trade_score": float(getattr(config, "SMC_MIN_TRADE_SCORE", 65)),
            "trade_score": getattr(proposal, "score", None) if proposal else None,
            "score_components": score_components,
            "pipeline_steps": pipeline_steps,
            "smc_factors": getattr(proposal, "smc_factors", None) if proposal else None,
        },
        "risk_management": risk or {},
        "execution": execution or {},
        "context": context or {},
        "metadata": {
            "deployment_tier": config.DEPLOYMENT_TIER,
            "exit_mode": str(getattr(config, "EXIT_MODE", "atr")),
        },
    }
    if extra:
        rec.update(extra)
    return rec


def append_decision_record(record: dict[str, Any]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        logger.warning("decision record write failed: %s", e)

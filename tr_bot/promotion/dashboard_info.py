"""Dashboard payload for strategy promotion visibility."""

from __future__ import annotations

from typing import Any

from tr_bot import config
from tr_bot.promotion.service import strategy_evaluate, strategy_status


def build_strategy_promotion_dashboard_payload() -> dict[str, Any]:
    st = strategy_status()
    ev = strategy_evaluate()
    return {
        "pointers": st.get("pointers"),
        "deployment_tier": st.get("deployment_tier"),
        "active_strategy_profile_id": st.get("active_strategy_profile_id"),
        "promotions_frozen": st.get("promotions_frozen"),
        "last_promotion_events": st.get("promotion_log_tail"),
        "last_evaluate": {
            "passed": ev.get("passed"),
            "outcomes": ev.get("outcomes"),
            "input_hash": ev.get("input_hash"),
        },
        "thresholds": {
            "min_trades": config.PROMOTION_MIN_TRADES,
            "min_calendar_days": config.PROMOTION_MIN_CALENDAR_DAYS,
            "max_equity_drawdown_pct": config.PROMOTION_MAX_EQUITY_DRAWDOWN_PCT,
            "cooldown_hours": config.PROMOTION_COOLDOWN_HOURS,
        },
    }

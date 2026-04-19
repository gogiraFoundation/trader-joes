"""Optional PostgreSQL persistence for ops and analytics."""

from tr_bot.db.decision_log import maybe_log_no_trade_decision

__all__ = ["maybe_log_no_trade_decision"]

"""Position sizing and daily trade limits (Section 4.7)."""

from __future__ import annotations

from datetime import date, datetime, timezone


class RiskManager:
    def __init__(
        self,
        balance: float,
        risk_per_trade: float = 0.01,
        max_daily_trades: int = 5,
    ) -> None:
        self.balance = balance
        self.risk_per_trade = risk_per_trade
        self.daily_trades = 0
        self.max_daily_trades = max_daily_trades
        self._day: date | None = None

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self._day is None:
            self._day = today
            return
        if today != self._day:
            self.daily_trades = 0
            self._day = today

    def position_size(self, stop_distance: float, weight: float = 1.0) -> float:
        if stop_distance <= 0:
            return 0.0
        w = max(0.0, min(weight, 1.0))
        risk_amount = self.balance * self.risk_per_trade * w
        return risk_amount / stop_distance

    def can_trade(self) -> bool:
        """True if we have not exhausted today's opening budget (MAX_TRADES_PER_DAY). Not related to RISK_PER_TRADE."""
        self._roll_day()
        return self.daily_trades < self.max_daily_trades

    def record_trade(self) -> None:
        self._roll_day()
        self.daily_trades += 1

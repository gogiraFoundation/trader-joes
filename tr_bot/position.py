"""Open position and ATR / structural stop / take-profit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from tr_bot.exit_plan import ExitPlan, reconstruct_exit_plan_from_position


@dataclass
class OpenPosition:
    symbol: str
    side: str  # "long" or "short"
    entry_price: float
    atr: float
    size: float
    regime_at_entry: str = ""
    regime_confidence_at_entry: float | None = None
    signal_branch: str = ""  # mean_reversion | trend | smc | none
    profile_id: str = ""
    profile_hash: str = ""
    deployment_tier: str = ""
    filters_passed: dict[str, Any] | None = None
    entry_order_id: str = ""
    entry_order_utc: str = ""
    # Live TWAP: "market" | "twap"; clientAlgoId until terminal reconcile; coarse Binance algo lifecycle.
    entry_execution: str = "market"
    twap_client_algo_id: str = ""
    twap_algo_state: str = ""
    # Exit policy: atr | structural | hybrid
    exit_mode: str = "atr"
    exit_policy: str = "software_polled"
    structural_stop: float | None = None
    structural_target: float | None = None
    exit_plan_dict: dict[str, Any] | None = None
    intended_risk_usdt: float | None = None
    trade_score: float | None = None
    score_components: dict[str, Any] | None = None
    # Exchange-native protective stop
    protective_order_id: str = ""
    protective_client_order_id: str = ""
    protective_order_state: str = ""
    # Partial exit tracking
    cumulative_closed_size: float = 0.0
    # Durable software / coordinator exit state machine
    exit_state: str = "active"  # active | pending | unknown | partially_filled
    exit_reason: str = ""
    exit_client_order_id: str = ""
    exit_requested_utc: str = ""
    exit_requested_size: float = 0.0
    exit_filled_size: float = 0.0
    exit_outcome: str = ""  # win | loss | ""
    # Paper / simulated exit fill (stop/take); used on reconcile when order has no price
    exit_fill_price: float = 0.0
    # STRATEGY_SIGNAL | REBALANCE_TRIM | ...; REBALANCE_* skips circuit breaker
    execution_context: str = "STRATEGY_SIGNAL"

    def exit_plan(self) -> ExitPlan:
        return reconstruct_exit_plan_from_position(
            side=self.side,
            entry_price=float(self.entry_price),
            atr=float(self.atr),
            exit_mode=self.exit_mode,
            exit_policy=self.exit_policy,
            structural_stop=self.structural_stop,
            structural_target=self.structural_target,
            exit_plan_dict=self.exit_plan_dict,
        )

    def _stop_take(self) -> tuple[float, float]:
        plan = self.exit_plan()
        return float(plan.stop_price), float(plan.take_price)

    def check_bar(self, bar: pd.Series) -> tuple[bool, str | None]:
        """
        Returns (exited, outcome) where outcome is 'win' or 'loss' if exited.
        Uses exit_mode / structural levels when configured; else ATR defaults.
        """
        return self.exit_plan().check_bar(bar)

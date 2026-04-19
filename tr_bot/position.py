"""Open position and ATR-based stop / take-profit (Section 4.7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class OpenPosition:
    symbol: str
    side: str  # "long" or "short"
    entry_price: float
    atr: float
    size: float
    regime_at_entry: str = ""
    regime_confidence_at_entry: float | None = None
    signal_branch: str = ""  # mean_reversion | trend | none
    profile_id: str = ""
    deployment_tier: str = ""
    filters_passed: dict[str, Any] | None = None
    entry_order_id: str = ""
    entry_order_utc: str = ""
    # Live TWAP: "market" | "twap"; clientAlgoId until terminal reconcile; coarse Binance algo lifecycle.
    entry_execution: str = "market"
    twap_client_algo_id: str = ""
    twap_algo_state: str = ""

    def check_bar(self, bar: pd.Series) -> tuple[bool, str | None]:
        """
        Returns (exited, outcome) where outcome is 'win' or 'loss' if exited.
        Long: stop = entry - ATR, TP = entry + 1.5 * ATR
        Short: stop = entry + ATR, TP = entry - 1.5 * ATR
        """
        low = float(bar["low"])
        high = float(bar["high"])
        if self.side == "long":
            stop = self.entry_price - self.atr
            take = self.entry_price + 1.5 * self.atr
            if low <= stop:
                return True, "loss"
            if high >= take:
                return True, "win"
        else:
            stop = self.entry_price + self.atr
            take = self.entry_price - 1.5 * self.atr
            if high >= stop:
                return True, "loss"
            if low <= take:
                return True, "win"
        return False, None

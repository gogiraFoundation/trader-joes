"""Pure single-bar replay helpers shared by backtest parity tests."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tr_bot.exit_plan import ExitPlan, simulate_software_polled_exit_price


@dataclass(frozen=True)
class ExitEvent:
    exited: bool
    outcome: str | None
    exit_price: float | None
    trigger_level: float | None
    fill_source: str


def replay_bar_software_polled(plan: ExitPlan, bar: pd.Series) -> ExitEvent:
    """Evaluate one bar against a frozen exit plan (software-polled policy)."""
    exited, outcome = plan.check_bar(bar)
    if not exited or outcome is None:
        return ExitEvent(
            exited=False,
            outcome=None,
            exit_price=None,
            trigger_level=None,
            fill_source="none",
        )
    trigger = float(plan.stop_price) if outcome == "loss" else float(plan.take_price)
    exit_px = simulate_software_polled_exit_price(
        side=plan.side,
        stop_price=plan.stop_price,
        take_price=plan.take_price,
        bar=bar,
        outcome=outcome,
    )
    return ExitEvent(
        exited=True,
        outcome=outcome,
        exit_price=float(exit_px),
        trigger_level=trigger,
        fill_source="software_polled_ohlc",
    )


def replay_bar_exchange_native(plan: ExitPlan, bar: pd.Series) -> ExitEvent:
    """
    Exchange-native stop: conservative gap fill at stop when breached.
    Take-profit remains software-polled for this phase.
    """
    exited, outcome = plan.check_bar(bar)
    if not exited or outcome is None:
        return ExitEvent(
            exited=False,
            outcome=None,
            exit_price=None,
            trigger_level=None,
            fill_source="none",
        )
    low = float(bar["low"])
    high = float(bar["high"])
    open_px = float(bar["open"])
    stop = float(plan.stop_price)
    take = float(plan.take_price)

    if plan.side == "long":
        if outcome == "loss" and low <= stop:
            fill = min(open_px, stop)
            return ExitEvent(True, outcome, fill, stop, "exchange_native_stop")
        if outcome == "win" and high >= take:
            fill = min(float(bar["close"]), take)
            return ExitEvent(True, outcome, fill, take, "exchange_native_take")
    else:
        if outcome == "loss" and high >= stop:
            fill = max(open_px, stop)
            return ExitEvent(True, outcome, fill, stop, "exchange_native_stop")
        if outcome == "win" and low <= take:
            fill = max(float(bar["close"]), take)
            return ExitEvent(True, outcome, fill, take, "exchange_native_take")

    return ExitEvent(
        exited=True,
        outcome=outcome,
        exit_price=float(bar["close"]),
        trigger_level=None,
        fill_source="exchange_native_fallback_close",
    )

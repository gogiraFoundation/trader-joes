from __future__ import annotations

import pandas as pd
import pytest

from tr_bot.exit_plan import ExitPlan, build_exit_plan, reconstruct_exit_plan_from_position
from tr_bot.position import OpenPosition


def test_build_exit_plan_atr_long():
    plan = build_exit_plan(side="long", entry_price=100.0, atr=2.0)
    assert plan.stop_price == pytest.approx(98.0)
    assert plan.take_price == pytest.approx(103.0)
    assert plan.stop_distance_quote == pytest.approx(2.0)


def test_build_exit_plan_structural_wider_stop():
    plan = build_exit_plan(
        side="long",
        entry_price=100.0,
        atr=2.0,
        exit_mode="structural",
        structural_stop=90.0,
        structural_target=110.0,
    )
    assert plan.stop_distance_quote == pytest.approx(10.0)


def test_exit_plan_immutable():
    plan = build_exit_plan(side="long", entry_price=100.0, atr=2.0)
    with pytest.raises(Exception):
        plan.stop_price = 50.0  # type: ignore[misc]


def test_check_bar_long_stop():
    plan = build_exit_plan(side="long", entry_price=100.0, atr=2.0)
    bar = pd.Series({"open": 99.0, "high": 99.5, "low": 97.5, "close": 98.0})
    exited, outcome = plan.check_bar(bar)
    assert exited is True
    assert outcome == "loss"


def test_open_position_reconstructs_plan():
    pos = OpenPosition(
        symbol="BTC/USDT",
        side="long",
        entry_price=100.0,
        atr=2.0,
        size=1.0,
        exit_mode="hybrid",
        structural_stop=92.0,
    )
    plan = pos.exit_plan()
    assert plan.stop_price == pytest.approx(92.0)


def test_reconstruct_from_stored_dict():
    plan = build_exit_plan(side="long", entry_price=100.0, atr=2.0)
    loaded = reconstruct_exit_plan_from_position(
        side="long",
        entry_price=100.0,
        atr=2.0,
        exit_plan_dict=plan.to_dict(),
    )
    assert isinstance(loaded, ExitPlan)
    assert loaded.stop_price == plan.stop_price

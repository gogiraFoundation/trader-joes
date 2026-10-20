from __future__ import annotations

import pandas as pd

from tr_bot.exit_plan import build_exit_plan
from tr_bot.replay import replay_bar_exchange_native, replay_bar_software_polled


def test_replay_software_polled_stop_fill():
    plan = build_exit_plan(side="long", entry_price=100.0, atr=2.0)
    bar = pd.Series({"open": 99.0, "high": 99.5, "low": 97.0, "close": 97.5})
    ev = replay_bar_software_polled(plan, bar)
    assert ev.exited is True
    assert ev.outcome == "loss"
    assert ev.exit_price is not None
    assert ev.exit_price <= 98.0


def test_replay_exchange_native_gap_fill():
    plan = build_exit_plan(side="long", entry_price=100.0, atr=2.0)
    bar = pd.Series({"open": 95.0, "high": 96.0, "low": 94.0, "close": 94.5})
    ev = replay_bar_exchange_native(plan, bar)
    assert ev.exited is True
    assert ev.fill_source == "exchange_native_stop"

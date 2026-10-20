from __future__ import annotations

import pytest

from tr_bot.execution import resolve_fill_price


def test_resolve_fill_price_average():
    order = {"average": 64123.5, "filled": 0.1, "cost": 6412.35}
    px, src = resolve_fill_price(order)
    assert px == pytest.approx(64123.5)
    assert src == "order_fill"


def test_resolve_fill_price_fallback():
    px, src = resolve_fill_price({}, fallback=64000.0)
    assert px == 64000.0
    assert src == "fallback"

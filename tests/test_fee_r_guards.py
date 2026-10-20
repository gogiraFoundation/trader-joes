"""MIN_ATR_PCT and PAPER_MAX_NOTIONAL_USDT guards."""

from __future__ import annotations

import pytest

from tr_bot.strategy import tier


def test_apply_paper_notional_cap_shrinks_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier.config, "PAPER_TRADING", True)
    monkeypatch.setattr(tier.config, "PAPER_MAX_NOTIONAL_USDT", 8000.0)
    out = tier.apply_paper_notional_cap(200.0, close_px=100.0, quote_usdt=1.0)
    assert out == pytest.approx(80.0)


def test_apply_paper_notional_cap_noop_when_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier.config, "PAPER_TRADING", False)
    monkeypatch.setattr(tier.config, "PAPER_MAX_NOTIONAL_USDT", 8000.0)
    assert tier.apply_paper_notional_cap(200.0, 100.0, 1.0) == pytest.approx(200.0)


def test_apply_paper_notional_cap_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier.config, "PAPER_TRADING", True)
    monkeypatch.setattr(tier.config, "PAPER_MAX_NOTIONAL_USDT", 0.0)
    assert tier.apply_paper_notional_cap(200.0, 100.0, 1.0) == pytest.approx(200.0)


def test_apply_backtest_notional_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier.config, "PAPER_MAX_NOTIONAL_USDT", 5000.0)
    assert tier.apply_backtest_notional_cap(100.0, 100.0, 1.0) == pytest.approx(50.0)


def test_atr_meets_min_pct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier.config, "MIN_ATR_PCT", 0.002)
    assert tier.atr_meets_min_pct(0.0856, 77.52) is False
    assert tier.atr_meets_min_pct(0.20, 77.0) is True
    monkeypatch.setattr(tier.config, "MIN_ATR_PCT", 0.0)
    assert tier.atr_meets_min_pct(0.01, 100.0) is True


def test_cap_reduces_intended_risk() -> None:
    stop_distance_usdt = 0.10
    size_uncapped = 250.0
    size_capped = tier._cap_size_by_notional(size_uncapped, 77.0, 1.0, 8000.0)
    assert size_capped < size_uncapped
    assert stop_distance_usdt * size_capped < stop_distance_usdt * size_uncapped

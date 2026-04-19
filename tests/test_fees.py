"""Fee bps resolution (taker-only Tier 1) and round-trip fee formula."""

from __future__ import annotations

import pytest

from tr_bot.fees import (
    default_backtest_fee_bps,
    effective_taker_bps,
    live_fee_bps_for_symbol,
)
from tr_bot.pnl_costs import fee_usdt_round_trip


def test_live_fee_flat_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 7.5, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 1.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", True, raising=False)
    assert live_fee_bps_for_symbol("BTC/USDT") == 7.5
    assert live_fee_bps_for_symbol("DOGE/USDT") == 7.5


def test_flat_beats_explicit_taker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 9.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 8.0, raising=False)
    assert live_fee_bps_for_symbol("BTC/USDT") == 9.0
    assert effective_taker_bps("BTC/USDT") == 8.0


def test_explicit_taker_beats_algo_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 8.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", True, raising=False)
    monkeypatch.setattr("tr_bot.config.BINANCE_VIP5_PLUS", False, raising=False)
    assert effective_taker_bps("BTC/USDT") == 8.0
    assert live_fee_bps_for_symbol("BTC/USDT") == 8.0


def test_algo_tiers_beat_retail_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", True, raising=False)
    monkeypatch.setattr("tr_bot.config.BINANCE_VIP5_PLUS", False, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS", True, raising=False)
    monkeypatch.setattr("tr_bot.config.BINANCE_PAY_FEES_WITH_BNB", True, raising=False)
    assert effective_taker_bps("BTC/USDT") == 5.0


def test_retail_defaults_bnb_vs_standard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", False, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS", True, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BNB_TAKER_BPS", 7.5, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_STANDARD_TAKER_BPS", 10.0, raising=False)
    monkeypatch.setattr("tr_bot.config.BINANCE_PAY_FEES_WITH_BNB", True, raising=False)
    assert effective_taker_bps("BTC/USDT") == 7.5
    monkeypatch.setattr("tr_bot.config.BINANCE_PAY_FEES_WITH_BNB", False, raising=False)
    assert effective_taker_bps("BTC/USDT") == 10.0


def test_bnb_flag_alone_does_not_change_fee(monkeypatch: pytest.MonkeyPatch) -> None:
    """BINANCE_PAY_FEES_WITH_BNB without retail opt-in must not apply 7.5/10."""
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", False, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS", False, raising=False)
    monkeypatch.setattr("tr_bot.config.BINANCE_PAY_FEES_WITH_BNB", True, raising=False)
    assert effective_taker_bps("BTC/USDT") == 0.0


def test_algo_tiers_btc_eth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", True, raising=False)
    monkeypatch.setattr("tr_bot.config.BINANCE_VIP5_PLUS", False, raising=False)
    assert live_fee_bps_for_symbol("BTC/USDT") == 5.0
    assert live_fee_bps_for_symbol("ETH/USDC") == 5.0


def test_algo_tiers_stable_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", True, raising=False)
    monkeypatch.setattr("tr_bot.config.BINANCE_VIP5_PLUS", False, raising=False)
    assert live_fee_bps_for_symbol("USDC/USDT") == 0.0
    assert live_fee_bps_for_symbol("FDUSD/USDT") == 0.0


def test_algo_tiers_other(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", True, raising=False)
    monkeypatch.setattr("tr_bot.config.BINANCE_VIP5_PLUS", False, raising=False)
    assert live_fee_bps_for_symbol("DOGE/USDT") == 12.0


def test_vip5_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", True, raising=False)
    monkeypatch.setattr("tr_bot.config.BINANCE_VIP5_PLUS", True, raising=False)
    assert live_fee_bps_for_symbol("DOGE/USDT") == 0.0


def test_tiers_off_uses_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_ALGO_TIERS", False, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS", False, raising=False)
    assert live_fee_bps_for_symbol("BTC/USDT") == 0.0


def test_default_backtest_fee_bps_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_bot.config.BACKTEST_FEE_BPS", 11.0, raising=False)
    monkeypatch.setattr("tr_bot.config.BACKTEST_TAKER_BPS", 9.0, raising=False)
    assert default_backtest_fee_bps("BTC/USDT") == 11.0
    monkeypatch.setattr("tr_bot.config.BACKTEST_FEE_BPS", 0.0, raising=False)
    assert default_backtest_fee_bps("BTC/USDT") == 9.0
    monkeypatch.setattr("tr_bot.config.BACKTEST_TAKER_BPS", 0.0, raising=False)
    monkeypatch.setattr("tr_bot.config.LIVE_FEE_TAKER_BPS", 6.0, raising=False)
    assert default_backtest_fee_bps("BTC/USDT") == 6.0


def test_fee_usdt_round_trip_symmetric_px_two_legs() -> None:
    """Round trip ≈ 2 × one-side bps on notional when entry_px == exit_px."""
    entry = exit_px = 100.0
    size = 2.0
    bps = 10.0
    fee = fee_usdt_round_trip(entry, exit_px, size, bps)
    one_side = (bps / 10_000.0) * entry * size
    assert fee == pytest.approx(2.0 * one_side)
    assert fee == pytest.approx((bps / 10_000.0) * (entry * size + exit_px * size))


def test_fee_usdt_round_trip_asymmetric_px() -> None:
    entry_px, exit_px, size, bps = 100.0, 110.0, 1.0, 7.5
    expected = (bps / 10_000.0) * (entry_px * size + exit_px * size)
    assert fee_usdt_round_trip(entry_px, exit_px, size, bps) == pytest.approx(expected)

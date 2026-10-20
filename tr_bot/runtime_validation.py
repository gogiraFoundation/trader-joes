"""Fail-closed validation of effective runtime config for live/paper trading."""

from __future__ import annotations

from typing import Any

from tr_bot import config
from tr_bot.managers.portfolio import active_symbols
from tr_bot.managers.security import is_valid_ccxt_spot_symbol


def validate_runtime_config() -> list[str]:
    """
    Return blocking configuration errors for the current effective settings.

    Safe for paper mode (empty list when paper + software_polled). Live tiers
    require credentials, ccxt pairs, explicit notional caps, and safe canary
    settings. EXIT_POLICY=exchange_native is always rejected for the live/paper
    engine (BACKTEST_EXIT_POLICY is not checked here).
    """
    errors: list[str] = []

    exit_policy = str(getattr(config, "EXIT_POLICY", "software_polled") or "software_polled").strip().lower()
    if exit_policy == "exchange_native":
        errors.append(
            "exit_policy_exchange_native_disabled:"
            "places stop_loss_limit and skips software TP; "
            "protective_reconcile is not integrated. Use EXIT_POLICY=software_polled."
        )

    paper = bool(config.PAPER_TRADING)
    tier = str(config.DEPLOYMENT_TIER or "paper").strip().lower()

    if paper and tier == "paper":
        return errors

    # Live (or non-paper) path
    key = (config.BINANCE_API_KEY or "").strip()
    secret = (config.BINANCE_SECRET or "").strip()
    if not key:
        errors.append("live_missing_binance_api_key")
    if not secret:
        errors.append("live_missing_binance_secret")

    try:
        symbols = active_symbols()
    except Exception as e:
        errors.append(f"live_invalid_symbol_universe:{e}")
        symbols = []

    for sym in symbols:
        if "/" not in sym or not is_valid_ccxt_spot_symbol(sym):
            errors.append(f"live_requires_ccxt_pair:{sym}")

    live_cap = float(getattr(config, "LIVE_MAX_NOTIONAL_USDT", 0.0) or 0.0)
    if live_cap <= 0:
        errors.append("live_max_notional_usdt_required")

    if tier == "live_canary":
        raw_syms = (config.CANARY_SYMBOLS or "").strip()
        if not raw_syms:
            errors.append("live_canary_requires_canary_symbols")
        mult = float(config.CANARY_RISK_MULTIPLIER)
        if not (0.0 < mult <= 0.25):
            errors.append(
                f"live_canary_risk_multiplier_out_of_range:{mult} "
                "(require 0 < CANARY_RISK_MULTIPLIER <= 0.25)"
            )
        canary_cap = float(config.CANARY_MAX_NOTIONAL_USDT)
        if not (0.0 < canary_cap <= 50.0):
            errors.append(
                f"live_canary_max_notional_out_of_range:{canary_cap} "
                "(require 0 < CANARY_MAX_NOTIONAL_USDT <= 50)"
            )

    return errors


def assert_runtime_config_safe() -> None:
    """Raise RuntimeError if validate_runtime_config() returns any errors."""
    errs = validate_runtime_config()
    if errs:
        raise RuntimeError("runtime_config_unsafe: " + "; ".join(errs))


def runtime_validation_summary() -> dict[str, Any]:
    errs = validate_runtime_config()
    return {
        "ok": len(errs) == 0,
        "errors": errs,
        "deployment_tier": config.DEPLOYMENT_TIER,
        "paper_trading": bool(config.PAPER_TRADING),
        "exit_policy": str(getattr(config, "EXIT_POLICY", "software_polled")),
    }

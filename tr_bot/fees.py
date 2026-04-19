"""Live / backtest fee bps (taker-only in Tier 1; optional Binance Spot Algo tiers + retail defaults)."""

from __future__ import annotations

from tr_bot import config


def _algo_tier_taker_bps(symbol: str) -> float:
    """Binance Spot Algo-style tier table (taker-style bps); LIVE_FEE_USE_ALGO_TIERS must be true."""
    if config.BINANCE_VIP5_PLUS:
        return 0.0
    s = (symbol or "").strip().upper()
    if "/" not in s:
        return 12.0
    base, _, quote = s.partition("/")
    quote = quote.strip()
    if not base or not quote:
        return 12.0
    if (base == "FDUSD" and quote == "USDT") or (base == "USDC" and quote == "USDT"):
        return 0.0
    if base in ("BTC", "ETH") and quote in ("USDT", "USDC", "FDUSD"):
        return 5.0
    return 12.0


def effective_taker_bps(symbol: str) -> float:
    """
    Taker fee bps for ``symbol`` excluding the legacy LIVE_FEE_BPS flat override.

    Precedence:
        1. LIVE_FEE_TAKER_BPS > 0 → global explicit taker.
        2. LIVE_FEE_USE_ALGO_TIERS → symbol-specific algo tier table.
        3. LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS → LIVE_FEE_BNB_TAKER_BPS if
           BINANCE_PAY_FEES_WITH_BNB else LIVE_FEE_STANDARD_TAKER_BPS.
        4. Else 0.0.

    LIVE_FEE_MAKER_BPS is not used (Tier 2).
    """
    if float(config.LIVE_FEE_TAKER_BPS) > 0:
        return float(config.LIVE_FEE_TAKER_BPS)
    if config.LIVE_FEE_USE_ALGO_TIERS:
        return _algo_tier_taker_bps(symbol)
    if config.LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS:
        if config.BINANCE_PAY_FEES_WITH_BNB:
            return float(config.LIVE_FEE_BNB_TAKER_BPS)
        return float(config.LIVE_FEE_STANDARD_TAKER_BPS)
    return 0.0


def live_fee_bps_for_symbol(symbol: str) -> float:
    """
    Effective fee bps for closed-trade ledger estimates (taker-only).

    Precedence:
        1. LIVE_FEE_BPS > 0 → flat override for all symbols.
        2. Else → :func:`effective_taker_bps` (explicit taker, algo tiers, or retail defaults).
    """
    if float(config.LIVE_FEE_BPS) > 0:
        return float(config.LIVE_FEE_BPS)
    return effective_taker_bps(symbol)


def default_backtest_fee_bps(symbol: str) -> float:
    """
    Default fee bps for ``run_backtest_on_dataframe`` when ``fee_bps`` is None.

    Precedence:
        1. BACKTEST_FEE_BPS > 0 → explicit backtest override.
        2. BACKTEST_TAKER_BPS > 0 → explicit backtest taker.
        3. Else → :func:`effective_taker_bps` (aligned with live taker resolution).
    """
    if float(config.BACKTEST_FEE_BPS) > 0:
        return float(config.BACKTEST_FEE_BPS)
    if float(config.BACKTEST_TAKER_BPS) > 0:
        return float(config.BACKTEST_TAKER_BPS)
    return effective_taker_bps(symbol)


def live_fee_mode_summary() -> dict[str, float | bool | str]:
    """Snapshot for dashboard: how live fee bps are chosen."""
    flat = float(config.LIVE_FEE_BPS) > 0
    taker_explicit = float(config.LIVE_FEE_TAKER_BPS) > 0
    if flat:
        mode = "flat_override"
    elif taker_explicit:
        mode = "explicit_taker"
    elif config.LIVE_FEE_USE_ALGO_TIERS:
        mode = "algo_tiers"
    elif config.LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS:
        mode = "binance_retail_taker_defaults"
    else:
        mode = "config_default"

    sym = config.SYMBOL
    eff = effective_taker_bps(sym)
    live = live_fee_bps_for_symbol(sym)

    return {
        "mode": mode,
        "resolution": mode,
        "live_fee_bps_flat": float(config.LIVE_FEE_BPS),
        "live_fee_taker_bps": float(config.LIVE_FEE_TAKER_BPS),
        "live_fee_maker_bps": float(config.LIVE_FEE_MAKER_BPS),
        "live_fee_use_algo_tiers": bool(config.LIVE_FEE_USE_ALGO_TIERS),
        "binance_vip5_plus": bool(config.BINANCE_VIP5_PLUS),
        "binance_pay_fees_with_bnb": bool(config.BINANCE_PAY_FEES_WITH_BNB),
        "use_binance_retail_taker_defaults": bool(config.LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS),
        "bnb_taker_bps": float(config.LIVE_FEE_BNB_TAKER_BPS),
        "standard_taker_bps": float(config.LIVE_FEE_STANDARD_TAKER_BPS),
        "effective_taker_bps_example": float(eff),
        "live_fee_bps_example": float(live),
        "example_symbol_bps": float(live),
    }

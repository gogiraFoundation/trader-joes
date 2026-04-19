"""Load versioned strategy profiles and merge whitelisted params onto tr_bot.config."""

from __future__ import annotations

import logging
from pathlib import Path
from types import ModuleType
from typing import Any

from tr_bot.strategy import deployment_io as dep

logger = logging.getLogger("tr_bot.strategy.profile")

SCHEMA_VERSION = 1

# JSON profile: { "profile_id", "schema_version", "created_at", ... metadata, "params": { KEY: value } }
# Keys must match config module attribute names; only these are applied.

_OVERLAY_COERCERS: dict[str, tuple[str, Any]] = {
    # name -> (kind, default_unused)
    "RISK_PER_TRADE": ("float", None),
    "MAX_TRADES_PER_DAY": ("int", None),
    "SIGNAL_RSI_OVERSOLD": ("float", None),
    "SIGNAL_RSI_OVERBOUGHT": ("float", None),
    "REGIME_MIN_CONFIDENCE": ("float", None),
    "USE_SIGNAL_STRENGTH_FILTER": ("bool", None),
    "SIGNAL_MIN_RSI_DISTANCE": ("float", None),
    "SIGNAL_MIN_ADX_MARGIN": ("float", None),
    "LOSS_COOLDOWN_BARS": ("int", None),
    "CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES": ("int", None),
    "POLL_SECONDS": ("int", None),
    "USE_EXTENDED_TA": ("bool", None),
    "USE_TA_FILTER": ("bool", None),
    "ADX_MAX_FOR_MEAN_REVERSION": ("float", None),
    "TREND_STRATEGY_ENABLED": ("bool", None),
    "ADX_MIN_FOR_TREND": ("float", None),
    "USE_FUNDAMENTALS_FILTER": ("bool", None),
    "BACKTEST_BARS": ("int", None),
    "BACKTEST_MAX_EQUITY_POINTS": ("int", None),
    "BACKTEST_FEE_BPS": ("float", None),
    "BACKTEST_TAKER_BPS": ("float", None),
    "BACKTEST_SLIPPAGE_BPS": ("float", None),
    "BACKTEST_SENTIMENT_MODE": ("str_lower", None),
    "BACKTEST_FUNDAMENTALS_MODE": ("str_lower", None),
    "BACKTEST_SKIP_TA_FILTER": ("bool", None),
    "BACKTEST_AUDIT_SAMPLE_STRIDE": ("int", None),
    "BACKTEST_RUN_ABLATION_MATRIX": ("bool", None),
    "BACKTEST_INCLUDE_BUY_HOLD": ("bool", None),
    "BACKTEST_ENFORCE_MAX_TRADES_PER_DAY": ("bool", None),
    "REGIME_MODEL_PATH": ("str_strip", None),
    "LIVE_FEE_BPS": ("float", None),
    "LIVE_FEE_TAKER_BPS": ("float", None),
    "LIVE_FEE_MAKER_BPS": ("float", None),
    "LIVE_FEE_USE_ALGO_TIERS": ("bool", None),
    "BINANCE_VIP5_PLUS": ("bool", None),
    "BINANCE_PAY_FEES_WITH_BNB": ("bool", None),
    "LIVE_FEE_BNB_TAKER_BPS": ("float", None),
    "LIVE_FEE_STANDARD_TAKER_BPS": ("float", None),
    "LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS": ("bool", None),
    "LIVE_SLIPPAGE_BPS": ("float", None),
    "EXECUTION_STYLE": ("str_lower", None),
    "TWAP_DURATION_SECONDS": ("int", None),
}

OVERLAY_PARAM_KEYS = frozenset(_OVERLAY_COERCERS.keys())


def _coerce(key: str, raw: Any) -> Any:
    kind = _OVERLAY_COERCERS[key][0]
    if kind == "float":
        return float(raw)
    if kind == "int":
        return int(raw)
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if kind == "str_lower":
        return str(raw).strip().lower()
    if kind == "str_strip":
        return str(raw).strip()
    raise RuntimeError(f"unknown coercer {kind}")


def coerce_overlay_value(key: str, raw: Any) -> Any:
    """Coerce a single overlay param value (raises KeyError if key not allowed)."""
    if key not in _OVERLAY_COERCERS:
        raise KeyError(key)
    return _coerce(key, raw)


def _load_profile_doc(artifacts_root: Path, profile_id: str) -> dict[str, Any] | None:
    p = dep.strategy_profile_path(artifacts_root, profile_id)
    raw = dep.read_json_file(p)
    if raw is None or not isinstance(raw, dict):
        return None
    return raw


def apply_profile_overlay(config_module: ModuleType) -> str:
    """
    Read deployment pointer for current DEPLOYMENT_TIER, load profile, merge params.
    Mutates attributes on config_module. Returns active profile_id or "".
    """
    artifacts_dir = str(getattr(config_module, "ARTIFACTS_DIR", "artifacts"))
    root = Path(artifacts_dir).resolve()
    tier = str(getattr(config_module, "DEPLOYMENT_TIER", "paper"))

    ptr = dep.read_pointer(root, tier)
    if not ptr:
        return ""
    profile_id = ptr.get("profile_id")
    if not profile_id or not isinstance(profile_id, str):
        return ""

    doc = _load_profile_doc(root, profile_id.strip())
    if not doc:
        logger.warning("Strategy profile file missing or invalid: %s", profile_id)
        return ""

    if doc.get("profile_id") != profile_id:
        logger.warning("profile_id mismatch in file vs pointer: %s", profile_id)
    ver = doc.get("schema_version", 1)
    try:
        if int(ver) != SCHEMA_VERSION:
            logger.warning("Unsupported strategy profile schema_version: %s", ver)
    except (TypeError, ValueError):
        logger.warning("Invalid schema_version in profile %s", profile_id)

    params = doc.get("params")
    if not isinstance(params, dict):
        return profile_id

    for k, v in params.items():
        if k not in _OVERLAY_COERCERS:
            logger.debug("Ignoring unknown profile param: %s", k)
            continue
        try:
            setattr(config_module, k, _coerce(k, v))
        except (TypeError, ValueError) as e:
            logger.warning("Could not apply profile param %s=%r: %s", k, v, e)

    return profile_id

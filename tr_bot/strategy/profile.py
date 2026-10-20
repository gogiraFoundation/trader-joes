"""Load versioned strategy profiles and merge whitelisted params onto tr_bot.config."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
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
    "MIN_ATR_PCT": ("float", None),
    "MAX_OPEN_POSITIONS": ("int", None),
    "PAPER_MAX_NOTIONAL_USDT": ("float", None),
    "EXECUTION_STYLE": ("str_lower", None),
    "TWAP_DURATION_SECONDS": ("int", None),
    "STRATEGY_ENGINE": ("str_lower", None),
    "SMC_MIN_TRADE_SCORE": ("float", None),
    "SMC_MIN_SMC_FACTORS": ("int", None),
    "SMC_HTF_TIMEFRAME": ("str_strip", None),
    "SMC_PRIMARY_TIMEFRAME": ("str_strip", None),
    "SMC_ENTRY_TIMEFRAME": ("str_strip", None),
    "SMC_EXEC_TIMEFRAME": ("str_strip", None),
    "EXIT_MODE": ("str_lower", None),
    "EXIT_POLICY": ("str_lower", None),
    "BACKTEST_EXIT_POLICY": ("str_lower", None),
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


def compute_config_hash(params: dict[str, Any]) -> str:
    """SHA-256 over canonical whitelisted params (sorted keys)."""
    canonical: dict[str, Any] = {}
    for k in sorted(OVERLAY_PARAM_KEYS):
        if k in params:
            v = params[k]
            if isinstance(v, bool):
                canonical[k] = v
            elif isinstance(v, float):
                canonical[k] = round(v, 10)
            else:
                canonical[k] = v
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_candidate_profile(
    doc: dict[str, Any],
    *,
    expected_profile_id: str | None = None,
    require_config_hash: bool = False,
) -> dict[str, Any]:
    """
    Validate profile JSON before write/promotion.
    Returns {ok, errors, coerced_params, config_hash}.
    """
    errors: list[str] = []
    profile_id = doc.get("profile_id")
    if not profile_id or not isinstance(profile_id, str):
        errors.append("missing profile_id")
    elif expected_profile_id and profile_id != expected_profile_id:
        errors.append(f"profile_id mismatch: {profile_id!r} != {expected_profile_id!r}")

    ver = doc.get("schema_version", 1)
    try:
        if int(ver) != SCHEMA_VERSION:
            errors.append(f"unsupported schema_version: {ver}")
    except (TypeError, ValueError):
        errors.append("invalid schema_version")

    params = doc.get("params")
    if not isinstance(params, dict):
        errors.append("params must be a dict")
        return {"ok": False, "errors": errors, "coerced_params": {}, "config_hash": ""}

    coerced: dict[str, Any] = {}
    for k, v in params.items():
        if k not in _OVERLAY_COERCERS:
            errors.append(f"unknown param: {k}")
            continue
        try:
            coerced[k] = _coerce(k, v)
        except (TypeError, ValueError) as e:
            errors.append(f"invalid param {k}={v!r}: {e}")

    if coerced.get("EXIT_MODE") and coerced["EXIT_MODE"] not in ("atr", "structural", "hybrid"):
        errors.append(f"invalid EXIT_MODE: {coerced['EXIT_MODE']}")
    if coerced.get("EXIT_POLICY") and coerced["EXIT_POLICY"] not in (
        "software_polled",
        "exchange_native",
    ):
        errors.append(f"invalid EXIT_POLICY: {coerced['EXIT_POLICY']}")
    if coerced.get("BACKTEST_EXIT_POLICY") and coerced["BACKTEST_EXIT_POLICY"] not in (
        "software_polled",
        "exchange_native",
    ):
        errors.append(f"invalid BACKTEST_EXIT_POLICY: {coerced['BACKTEST_EXIT_POLICY']}")

    config_hash = compute_config_hash(coerced)
    stored_hash = doc.get("config_hash")
    if require_config_hash and stored_hash and stored_hash != config_hash:
        errors.append("config_hash mismatch (profile params tampered)")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "coerced_params": coerced,
        "config_hash": config_hash,
    }


def build_profile_document(
    profile_id: str,
    params: dict[str, Any],
    *,
    parent_profile_id: str | None = None,
    hypothesis: str | None = None,
    source: str = "candidate",
) -> dict[str, Any]:
    """Build a validated profile document with embedded config_hash."""
    subset = {k: params[k] for k in OVERLAY_PARAM_KEYS if k in params}
    config_hash = compute_config_hash(subset)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "profile_id": profile_id,
        "schema_version": SCHEMA_VERSION,
        "created_at": now,
        "parent_profile_id": parent_profile_id,
        "hypothesis": hypothesis or f"Profile {profile_id} ({source})",
        "params": subset,
        "config_hash": config_hash,
        "source": source,
    }


def active_config_hash(config_module: ModuleType) -> str:
    """Compute config hash from current effective overlay params on config module."""
    params = {k: getattr(config_module, k) for k in OVERLAY_PARAM_KEYS if hasattr(config_module, k)}
    return compute_config_hash(params)

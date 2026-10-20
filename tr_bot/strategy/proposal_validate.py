"""Validate LLM strategy proposal JSON: allowlist, bounds, coercion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.strategy.profile import OVERLAY_PARAM_KEYS, coerce_overlay_value

PROPOSAL_SCHEMA_VERSION = 1

# Hard-coded allowlist: numeric/filter thresholds only (no on/off safety switches).
_DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "REGIME_MIN_CONFIDENCE",
        "SIGNAL_MIN_ADX_MARGIN",
        "SIGNAL_MIN_RSI_DISTANCE",
        "SIGNAL_RSI_OVERSOLD",
        "SIGNAL_RSI_OVERBOUGHT",
        "ADX_MAX_FOR_MEAN_REVERSION",
        "ADX_MIN_FOR_TREND",
    }
)


def proposal_param_allowlist() -> frozenset[str]:
    """Return the fixed LLM proposal param allowlist (not env-widened)."""
    return _DEFAULT_ALLOWLIST


def _bounds_check(key: str, value: Any) -> tuple[bool, str | None]:
    try:
        if key == "REGIME_MIN_CONFIDENCE":
            lo = float(config.ADAPTATION_REGIME_CONFIDENCE_FLOOR)
            v = float(value)
            if not (lo <= v <= 1.0):
                return False, f"{key} must be in [{lo}, 1.0]"
        elif key == "SIGNAL_MIN_ADX_MARGIN":
            lo = float(config.ADAPTATION_ADX_MARGIN_FLOOR)
            v = float(value)
            if not (lo <= v <= 20.0):
                return False, f"{key} must be in [{lo}, 20]"
        elif key == "SIGNAL_MIN_RSI_DISTANCE":
            lo = float(config.ADAPTATION_RSI_DISTANCE_FLOOR)
            v = float(value)
            if not (lo <= v <= 30.0):
                return False, f"{key} must be in [{lo}, 30]"
        elif key == "SIGNAL_RSI_OVERSOLD":
            v = float(value)
            if not (5.0 <= v <= float(config.ADAPTATION_RSI_OVERSOLD_CEILING)):
                return False, f"{key} must be in [5, {config.ADAPTATION_RSI_OVERSOLD_CEILING}]"
        elif key == "SIGNAL_RSI_OVERBOUGHT":
            v = float(value)
            if not (float(config.ADAPTATION_RSI_OVERBOUGHT_FLOOR) <= v <= 95.0):
                return False, f"{key} must be in [{config.ADAPTATION_RSI_OVERBOUGHT_FLOOR}, 95]"
        elif key == "ADX_MAX_FOR_MEAN_REVERSION":
            v = float(value)
            if not (10.0 <= v <= 80.0):
                return False, f"{key} must be in [10, 80]"
        elif key == "ADX_MIN_FOR_TREND":
            v = float(value)
            if not (5.0 <= v <= 60.0):
                return False, f"{key} must be in [5, 60]"
        elif key == "SMC_MIN_TRADE_SCORE":
            v = float(value)
            if not (0.0 <= v <= 100.0):
                return False, f"{key} must be in [0, 100]"
        elif key == "SMC_MIN_SMC_FACTORS":
            v = int(value)
            if not (1 <= v <= 6):
                return False, f"{key} must be in [1, 6]"
        elif key == "STRATEGY_ENGINE":
            if str(value).strip().lower() not in ("legacy", "smc", "shadow"):
                return False, f"{key} must be legacy|smc|shadow"
        elif key == "EXIT_MODE":
            if str(value).strip().lower() not in ("atr", "structural", "hybrid"):
                return False, f"{key} must be atr|structural|hybrid"
        elif key in (
            "SMC_HTF_TIMEFRAME",
            "SMC_PRIMARY_TIMEFRAME",
            "SMC_ENTRY_TIMEFRAME",
            "SMC_EXEC_TIMEFRAME",
        ):
            if not str(value).strip():
                return False, f"{key} must be non-empty"
    except (TypeError, ValueError) as e:
        return False, f"{key}: invalid numeric value ({e})"
    return True, None


def validate_llm_proposal(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Returns { ok, params?, coerced_params?, rationale?, risk_notes?, errors: list[str] }.
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {"ok": False, "errors": ["root must be a JSON object"]}
    ver = raw.get("schema_version", PROPOSAL_SCHEMA_VERSION)
    try:
        if int(ver) != PROPOSAL_SCHEMA_VERSION:
            errors.append(f"schema_version must be {PROPOSAL_SCHEMA_VERSION}")
    except (TypeError, ValueError):
        errors.append("schema_version invalid")

    params_in = raw.get("params")
    if not isinstance(params_in, dict):
        errors.append("params must be an object")
        return {"ok": False, "errors": errors}

    allow = proposal_param_allowlist()
    coerced: dict[str, Any] = {}
    for k, v in params_in.items():
        if k not in allow:
            errors.append(f"key not allowed: {k}")
            continue
        if k not in OVERLAY_PARAM_KEYS:
            errors.append(f"key not in overlay schema: {k}")
            continue
        try:
            cv = coerce_overlay_value(k, v)
        except (KeyError, TypeError, ValueError) as e:
            errors.append(f"{k}: coerce failed ({e})")
            continue
        ok_b, msg = _bounds_check(k, cv)
        if not ok_b:
            errors.append(msg or f"{k}: bounds failed")
            continue
        coerced[k] = cv

    if errors:
        return {"ok": False, "errors": errors}

    if coerced:
        from tr_bot.strategy.effective_params import effective_params_from_config_and_profile

        eff = effective_params_from_config_and_profile()
        try:
            lo = float(coerced.get("SIGNAL_RSI_OVERSOLD", eff.get("SIGNAL_RSI_OVERSOLD", 30)))
            hi = float(coerced.get("SIGNAL_RSI_OVERBOUGHT", eff.get("SIGNAL_RSI_OVERBOUGHT", 70)))
            if lo >= hi:
                errors.append("SIGNAL_RSI_OVERSOLD must be < SIGNAL_RSI_OVERBOUGHT (effective after merge)")
        except (TypeError, ValueError) as e:
            errors.append(f"RSI band compare failed: {e}")

    if errors:
        return {"ok": False, "errors": errors}

    rationale = raw.get("rationale")
    risk_notes = raw.get("risk_notes")
    if rationale is not None and not isinstance(rationale, str):
        errors.append("rationale must be string or omitted")
    if risk_notes is not None and not isinstance(risk_notes, str):
        errors.append("risk_notes must be string or omitted")
    if errors:
        return {"ok": False, "errors": errors}

    return {
        "ok": True,
        "params": dict(params_in),
        "coerced_params": coerced,
        "rationale": rationale if isinstance(rationale, str) else None,
        "risk_notes": risk_notes if isinstance(risk_notes, str) else None,
    }


def load_and_validate_proposal_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        return {"ok": False, "errors": [f"read failed: {e}"]}
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [f"invalid json: {e}"]}
    if not isinstance(raw, dict):
        return {"ok": False, "errors": ["root must be object"]}
    return validate_llm_proposal(raw)

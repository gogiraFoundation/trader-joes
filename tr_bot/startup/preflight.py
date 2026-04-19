"""Deterministic startup checks for --all hard-block mode."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.health_checks import check_postgres, check_redis, overall_readiness_ok
from tr_bot.strategy import deployment_io as dep


def _parse_train_sources(s: str) -> set[str]:
    out: set[str] = set()
    for x in (x.strip().lower() for x in s.split(",") if x.strip()):
        out.add("exchange" if x == "binance" else x)
    return out


def _cmc_startup_warnings_and_summary() -> tuple[list[str], dict[str, Any]]:
    """CoinMarketCap / training consistency for startup (warnings only — non-blocking)."""
    warns: list[str] = []
    sources = _parse_train_sources(config.TRAIN_SOURCES)
    cmc_train = "coinmarketcap" in sources
    key_ok = bool((config.CMC_API_KEY or "").strip())
    ids = bool((config.CMC_TRAIN_IDS or "").strip())
    slugs = bool((config.CMC_TRAIN_SLUGS or "").strip())

    if cmc_train and not key_ok:
        warns.append("cmc:TRAIN_SOURCES includes coinmarketcap but CMC_API_KEY is empty (CMC rows skipped)")

    if ids and slugs:
        warns.append("cmc:CMC_TRAIN_IDS and CMC_TRAIN_SLUGS are both set; training uses ids only (slugs ignored)")

    if (
        (config.CMC_FEAR_GREED_ENABLED or config.CMC_ALTCOIN_SEASON_ENABLED)
        and not key_ok
    ):
        warns.append(
            "cmc:CMC_FEAR_GREED_ENABLED or CMC_ALTCOIN_SEASON_ENABLED but CMC_API_KEY is empty "
            "(optional CMC snapshots will not populate; set a key or turn flags off)"
        )

    if config.CMC_TRAIN_MACRO_MERGE_ENABLED and not key_ok:
        warns.append("cmc:CMC_TRAIN_MACRO_MERGE_ENABLED but CMC_API_KEY is empty (macro merge will no-op)")

    if config.TRAIN_REGIME_MACRO_FEATURES:
        if not config.CMC_TRAIN_MACRO_MERGE_ENABLED:
            warns.append(
                "cmc:TRAIN_REGIME_MACRO_FEATURES is on but CMC_TRAIN_MACRO_MERGE_ENABLED is false "
                "(no cmc_macro_* columns at fit time unless you merged them elsewhere)"
            )
        else:
            warns.append(
                "cmc:TRAIN_REGIME_MACRO_FEATURES: live bars must include the same cmc_macro_* columns as training "
                "or regime predict falls back (see docs/operations.md CoinMarketCap section)"
            )

    if int(getattr(config, "CMC_GLOBAL_METRICS_TTL_S", 300)) < 300:
        warns.append("cmc:CMC_GLOBAL_METRICS_TTL_S below 300s may burn credits vs typical global-metrics refresh")

    summary: dict[str, Any] = {
        "train_sources_includes_coinmarketcap": cmc_train,
        "api_key_configured": key_ok,
        "cmc_train_ids_set": ids,
        "cmc_train_slugs_set": slugs,
        "live_global_metrics": bool(config.CMC_GLOBAL_METRICS_ENABLED),
        "live_fear_greed": bool(config.CMC_FEAR_GREED_ENABLED),
        "live_altcoin_season": bool(config.CMC_ALTCOIN_SEASON_ENABLED),
        "train_macro_merge": bool(config.CMC_TRAIN_MACRO_MERGE_ENABLED),
        "train_regime_macro_features": bool(config.TRAIN_REGIME_MACRO_FEATURES),
    }
    return warns, summary


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def run_preflight_checks() -> dict[str, Any]:
    root = Path(config.ARTIFACTS_DIR).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".startup_write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as e:
        errors.append(f"artifacts_not_writable:{e}")

    if config.DEPLOYMENT_TIER not in dep.POINTER_NAMES:
        errors.append(f"invalid_deployment_tier:{config.DEPLOYMENT_TIER}")

    if (
        (not config.PAPER_TRADING)
        and (config.BINANCE_API_KEY or "").strip()
        and str(getattr(config, "EXECUTION_STYLE", "market")).strip().lower() == "twap"
    ):
        warnings.append(
            "binance_live_twap:SAPI TWAP (newOrderTwap) is high weight; avoid parallel bot processes and tight REST loops"
        )

    if float(config.RISK_PER_TRADE) <= 0:
        errors.append("risk_per_trade_must_be_positive")
    if int(config.MAX_TRADES_PER_DAY) <= 0:
        errors.append("max_trades_per_day_must_be_positive")

    ptr = dep.read_pointer(root, config.DEPLOYMENT_TIER)
    active_strategy_profile_id: str | None = None
    if ptr and isinstance(ptr, dict):
        pid = ptr.get("profile_id")
        if isinstance(pid, str) and pid.strip():
            active_strategy_profile_id = pid.strip()
            try:
                p = dep.strategy_profile_path(root, active_strategy_profile_id)
                if not p.is_file():
                    errors.append(f"active_profile_missing:{pid}")
            except ValueError as e:
                errors.append(f"active_profile_invalid:{e}")
    if active_strategy_profile_id is None:
        active_strategy_profile_id = config.ACTIVE_STRATEGY_PROFILE_ID or None

    redis_info = check_redis(config.REDIS_URL)
    pg_info = check_postgres(config.DATABASE_URL)
    ready = overall_readiness_ok(
        redis_info,
        pg_info,
        require_postgres=config.readiness_require_postgres(),
    )
    if not ready:
        errors.append("dependencies_not_ready")

    for e in config.coindesk_runtime_validation_errors():
        errors.append(e)

    if config.COINDESK_REFERENCE_CHECK_ENABLED and config.COINDESK_API_KEY.strip():
        try:
            from tr_bot.coindesk.reference import validate_configured_instruments

            v = validate_configured_instruments()
            if not v.get("ok"):
                msg = "coindesk_reference:" + ";".join(v.get("issues") or [])
                sample = v.get("mapped_instrument_keys_sample") or []
                if sample:
                    msg += ";mapped_keys_sample=" + ",".join(sample[:15])
                warnings.append(msg)
                if config.COINDESK_REFERENCE_STRICT:
                    errors.append("coindesk_instruments_not_active")
        except Exception as e:
            warnings.append(f"coindesk_reference_error:{e}")
            if config.COINDESK_REFERENCE_STRICT:
                errors.append("coindesk_reference_unavailable")

    cmc_warns, cmc_summary = _cmc_startup_warnings_and_summary()
    warnings.extend(cmc_warns)

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "cmc": cmc_summary,
        "artifacts_dir": str(root),
        "deployment_tier": config.DEPLOYMENT_TIER,
        "active_strategy_profile_id": active_strategy_profile_id,
        "dependencies": {
            "redis": redis_info,
            "postgres": pg_info,
            "postgres_required_for_ready": config.readiness_require_postgres(),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m tr_bot.startup.preflight")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()

    out = run_preflight_checks()
    if args.compact:
        print(
            "preflight ok=%s tier=%s profile=%s"
            % (
                out.get("ok"),
                out.get("deployment_tier"),
                out.get("active_strategy_profile_id"),
            )
        )
        if not out.get("ok"):
            print("errors:", ";".join(out.get("errors") or []))
        if out.get("warnings"):
            print("warnings:", ";".join(out.get("warnings") or []))
    else:
        print(json.dumps(out, indent=2, default=str))

    if not out.get("ok") and _bool_env("STARTUP_HARD_BLOCK", True):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

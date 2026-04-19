"""JSON API for the browser dashboard."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse

from tr_bot import config
from tr_bot.fees import default_backtest_fee_bps
from tr_bot.circuit_breaker import read_circuit_state_snapshot
from tr_bot.health_checks import check_postgres, check_redis, overall_readiness_ok
from tr_bot.ops_state import ops_view_for_api, read_equity_history, read_trading_snapshot
from tr_bot.backtest.dashboard_enrich import enrich_backtest_last_run_for_dashboard
from tr_bot.backtest.last_run import read_backtest_last_run
from tr_bot.dashboard_pnl import build_equity_history_display_payload, build_pnl_dashboard_payload
from tr_bot.promotion.dashboard_info import build_strategy_promotion_dashboard_payload
from tr_bot.training.dashboard_info import build_training_dashboard_payload
from tr_bot.startup.pipeline import read_startup_last_run

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_enrich_backtest_last_run_for_dashboard = enrich_backtest_last_run_for_dashboard


def _scheduler_payload() -> dict[str, Any]:
    return {
        "enabled": config.SCHEDULER_ENABLED,
        "training_interval_seconds": config.SCHEDULER_TRAINING_INTERVAL_SECONDS,
        "backtest_interval_seconds": config.SCHEDULER_BACKTEST_INTERVAL_SECONDS,
        "initial_delay_seconds": config.SCHEDULER_INITIAL_DELAY_SECONDS,
        "run_training_on_start": config.SCHEDULER_RUN_TRAINING_ON_START,
        "run_backtest_on_start": config.SCHEDULER_RUN_BACKTEST_ON_START,
        "tick_seconds": config.SCHEDULER_TICK_SECONDS,
        "promotion_interval_seconds": config.SCHEDULER_PROMOTION_INTERVAL_SECONDS,
        "run_promotion_on_start": config.SCHEDULER_RUN_PROMOTION_ON_START,
        "coindesk_asset_top_list_interval_seconds": config.SCHEDULER_COINDESK_ASSET_TOP_LIST_INTERVAL_SECONDS,
        "run_coindesk_asset_top_list_on_start": config.SCHEDULER_RUN_COINDESK_ASSET_TOP_LIST_ON_START,
    }


def _backtest_payload() -> dict[str, Any]:
    return {
        "last_run": enrich_backtest_last_run_for_dashboard(read_backtest_last_run()),
        "config": {
            "backtest_bars": config.BACKTEST_BARS,
            "max_equity_points": config.BACKTEST_MAX_EQUITY_POINTS,
            "fee_bps": default_backtest_fee_bps(config.SYMBOL),
            "fee_bps_backtest_override": float(config.BACKTEST_FEE_BPS),
            "fee_bps_backtest_taker": float(config.BACKTEST_TAKER_BPS),
            "slippage_bps": config.BACKTEST_SLIPPAGE_BPS,
            "sentiment_mode": config.BACKTEST_SENTIMENT_MODE,
            "fundamentals_mode": config.BACKTEST_FUNDAMENTALS_MODE,
            "skip_ta_filter": config.BACKTEST_SKIP_TA_FILTER,
            "audit_sample_stride": config.BACKTEST_AUDIT_SAMPLE_STRIDE,
            "run_ablation_matrix": config.BACKTEST_RUN_ABLATION_MATRIX,
            "include_buy_hold": config.BACKTEST_INCLUDE_BUY_HOLD,
            "execution_realism_enabled": config.BACKTEST_EXECUTION_REALISM_ENABLED,
            "execution_skip_pct": config.BACKTEST_EXECUTION_SKIP_PCT,
            "execution_reject_pct": config.BACKTEST_EXECUTION_REJECT_PCT,
            "execution_partial_fill_min": config.BACKTEST_EXECUTION_PARTIAL_FILL_MIN,
            "execution_partial_fill_max": config.BACKTEST_EXECUTION_PARTIAL_FILL_MAX,
            "execution_random_seed": config.BACKTEST_EXECUTION_RANDOM_SEED,
            "enforce_exchange_limits": config.BACKTEST_ENFORCE_EXCHANGE_LIMITS,
            "exit_realism_enabled": config.BACKTEST_EXIT_REALISM_ENABLED,
            "exit_delay_prob_pct": config.BACKTEST_EXIT_DELAY_PROB_PCT,
            "exit_partial_fill_min": config.BACKTEST_EXIT_PARTIAL_FILL_MIN,
            "exit_partial_fill_max": config.BACKTEST_EXIT_PARTIAL_FILL_MAX,
            "exit_extra_slippage_bps": config.BACKTEST_EXIT_EXTRA_SLIPPAGE_BPS,
            "exit_delay_fill_next_bar": config.BACKTEST_EXIT_DELAY_FILL_NEXT_BAR,
            "exit_delay_fill_price": config.BACKTEST_EXIT_DELAY_FILL_PRICE,
            "annual_gap_multiplier": config.BACKTEST_ANNUAL_GAP_MULTIPLIER,
            "min_risk_metric_obs": config.BACKTEST_MIN_RISK_METRIC_OBS,
            "decision_audit_jsonl_enabled": bool(config.DECISION_AUDIT_JSONL_PATH),
            "decision_audit_every_n_loops": config.DECISION_AUDIT_EVERY_N_LOOPS,
        },
    }


def _sanitized_config() -> dict[str, Any]:
    return {
        "symbol": config.SYMBOL,
        "timeframe": config.TIMEFRAME,
        "initial_bars": config.INITIAL_BARS,
        "loop_bars": config.LOOP_BARS,
        "portfolio_mode": config.PORTFOLIO_MODE,
        "paper_trading": config.PAPER_TRADING,
        "risk_per_trade": config.RISK_PER_TRADE,
        "max_trades_per_day": config.MAX_TRADES_PER_DAY,
        "poll_seconds": config.POLL_SECONDS,
        "circuit_breaker_max_consecutive_losses": config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES,
        "managers_enabled": config.MANAGERS_ENABLED,
        "use_llm": config.USE_LLM,
        "use_extended_ta": config.USE_EXTENDED_TA,
        "use_ta_filter": config.USE_TA_FILTER,
        "trend_strategy_enabled": config.TREND_STRATEGY_ENABLED,
        "adx_max_for_mean_reversion": config.ADX_MAX_FOR_MEAN_REVERSION,
        "adx_min_for_trend": config.ADX_MIN_FOR_TREND,
        "restore_reconcile_tolerance_pct": config.RESTORE_RECONCILE_TOLERANCE_PCT,
        "use_microstructure_guard": config.USE_MICROSTRUCTURE_GUARD,
        "micro_max_bar_range_pct": config.MICRO_MAX_BAR_RANGE_PCT,
        "micro_max_bar_jump_pct": config.MICRO_MAX_BAR_JUMP_PCT,
        "micro_min_bar_volume": config.MICRO_MIN_BAR_VOLUME,
        "micro_use_spread_depth_guard": config.MICRO_USE_SPREAD_DEPTH_GUARD,
        "micro_max_spread_pct": config.MICRO_MAX_SPREAD_PCT,
        "micro_min_top_depth_notional": config.MICRO_MIN_TOP_DEPTH_NOTIONAL,
        "restore_reconcile_use_order_history": config.RESTORE_RECONCILE_USE_ORDER_HISTORY,
        "restore_reconcile_trades_lookback": config.RESTORE_RECONCILE_TRADES_LOOKBACK,
        "restore_reconcile_require_order_id": config.RESTORE_RECONCILE_REQUIRE_ORDER_ID,
        "use_fundamentals_filter": config.USE_FUNDAMENTALS_FILTER,
        "max_drawdown_pct": config.MAX_DRAWDOWN_PCT,
        "deployment_tier": config.DEPLOYMENT_TIER,
        "effective_tier_overrides": config.effective_tier_overrides(),
        "active_strategy_profile_id": config.ACTIVE_STRATEGY_PROFILE_ID or None,
        "canary_risk_multiplier": config.CANARY_RISK_MULTIPLIER,
        "canary_max_notional_usdt": config.CANARY_MAX_NOTIONAL_USDT,
        "canary_symbols_configured": bool((config.CANARY_SYMBOLS or "").strip()),
        "paper_equity_usdt": config.PAPER_EQUITY_USDT,
        "fund_quote_asset": config.FUND_QUOTE_ASSET,
        "fund_refresh_seconds": config.FUND_REFRESH_SECONDS,
        "notify_enabled": config.NOTIFY_ENABLED,
        "notify_channels": config.NOTIFY_CHANNELS,
        "notify_environment": config.NOTIFY_ENVIRONMENT,
        "notify_webhook_configured": bool((config.NOTIFY_WEBHOOK_URL or "").strip()),
        "artifacts_dir": str(config.ARTIFACTS_DIR),
        "decision_log_db_enabled": config.DECISION_LOG_DB_ENABLED,
        "public_base_url": config.PUBLIC_BASE_URL or None,
        "readiness_mode": config.READINESS_MODE,
        "cmc_global_metrics_enabled": config.CMC_GLOBAL_METRICS_ENABLED,
        "cmc_global_metrics_ttl_s": config.CMC_GLOBAL_METRICS_TTL_S,
        "cmc_fear_greed_enabled": config.CMC_FEAR_GREED_ENABLED,
        "cmc_fear_greed_ttl_s": config.CMC_FEAR_GREED_TTL_S,
        "cmc_altcoin_season_enabled": config.CMC_ALTCOIN_SEASON_ENABLED,
        "cmc_altcoin_season_ttl_s": config.CMC_ALTCOIN_SEASON_TTL_S,
    }


def _portfolio_payload() -> dict[str, Any]:
    from tr_bot.managers.portfolio import parse_portfolio_weights

    try:
        weights = parse_portfolio_weights()
        return {"ok": True, "weights": weights}
    except Exception as e:
        return {"ok": False, "error": str(e), "weights": {}}


def _fund_payload() -> dict[str, Any]:
    if not config.MANAGERS_ENABLED:
        return {"enabled": False}
    try:
        from tr_bot.managers.fund import FundManager

        fund = FundManager()
        st = fund.get_state()
        return {
            "enabled": True,
            "halt_file": fund.manual_halt_file_active(),
            "state": asdict(st) if st else None,
        }
    except Exception as e:
        return {"enabled": True, "error": str(e)}


def _coindesk_asset_top_list_payload() -> dict[str, Any]:
    from tr_bot.coindesk.asset_catalog import read_asset_top_list_snapshot

    out = read_asset_top_list_snapshot()
    base: dict[str, Any] = {
        "enabled": config.COINDESK_ASSET_TOP_LIST_ENABLED,
        "sort_by": config.COINDESK_ASSET_TOP_LIST_SORT_BY,
    }
    if not out.get("available"):
        base["available"] = False
        if out.get("error"):
            base["error"] = out["error"]
        return base
    base["available"] = True
    base["snapshot"] = out.get("snapshot")
    base["path"] = out.get("path")
    return base


def _best_fit_payload() -> dict[str, Any]:
    """
    Read artifacts/sweep_best_fit/winner.json for dashboard display.
    Uses ARTIFACTS_DIR as base to match runtime process.
    """
    root = Path(config.ARTIFACTS_DIR).resolve()
    path = root / "sweep_best_fit" / "winner.json"
    if not path.is_file():
        return {"available": False, "note": "No winner.json yet"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        updates = raw.get("env_updates") if isinstance(raw, dict) else None
        return {
            "available": True,
            "mean_score": raw.get("mean_score"),
            "primary_score": raw.get("primary_score"),
            "stability_score": raw.get("stability_score"),
            "env_updates": updates if isinstance(updates, dict) else {},
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        return {"available": False, "note": str(e)}


def _best_fit_winner_path() -> Path:
    return Path(config.ARTIFACTS_DIR).resolve() / "sweep_best_fit" / "winner.json"


def require_dashboard_token(
    authorization: str | None = Header(None),
    x_dashboard_token: str | None = Header(None, alias="X-Dashboard-Token"),
) -> None:
    expected = (config.DASHBOARD_TOKEN or "").strip()
    if not expected:
        return
    got: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        got = authorization[7:].strip()
    elif x_dashboard_token:
        got = x_dashboard_token.strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing dashboard token")


@router.get("/summary")
def dashboard_summary(_: None = Depends(require_dashboard_token)) -> dict[str, Any]:
    redis_info = check_redis(config.REDIS_URL)
    pg_info = check_postgres(config.DATABASE_URL)
    req_pg = config.readiness_require_postgres()
    ready_ok = overall_readiness_ok(redis_info, pg_info, require_postgres=req_pg)

    snap = read_circuit_state_snapshot()
    m = snap.get("max_consecutive_losses") or config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES
    n = snap.get("consecutive_losses")
    tripped = snap.get("tripped")
    if tripped is None and n is not None:
        tripped = n >= m

    return {
        "status": "ready" if ready_ok else "degraded",
        "dependencies": {
            "redis": redis_info,
            "postgres": pg_info,
            "readiness": {
                "postgres_required_for_ready": req_pg,
                "mode": config.READINESS_MODE,
            },
        },
        "ops": ops_view_for_api(),
        "equity_history": read_equity_history(),
        "equity_history_display": build_equity_history_display_payload(),
        "trading": read_trading_snapshot(),
        "config": _sanitized_config(),
        "portfolio": _portfolio_payload(),
        "fund": _fund_payload(),
        "best_fit": _best_fit_payload(),
        "circuit": {
            **snap,
            "tripped": tripped,
        },
        "training": build_training_dashboard_payload(),
        "backtest": _backtest_payload(),
        "pnl": build_pnl_dashboard_payload(),
        "scheduler": _scheduler_payload(),
        "strategy_promotion": build_strategy_promotion_dashboard_payload(),
        "startup_pipeline": read_startup_last_run(),
        "coindesk_asset_top_list": _coindesk_asset_top_list_payload(),
    }


@router.get("/strategy/status")
def dashboard_strategy_status(_: None = Depends(require_dashboard_token)) -> dict[str, Any]:
    """Read-only strategy promotion snapshot (pointers, gates, thresholds)."""
    return build_strategy_promotion_dashboard_payload()


@router.get("/best-fit/winner.json")
def dashboard_best_fit_winner_json(
    _: None = Depends(require_dashboard_token),
) -> FileResponse:
    p = _best_fit_winner_path()
    if not p.is_file():
        raise HTTPException(status_code=404, detail="winner.json not found")
    return FileResponse(path=p, media_type="application/json", filename="winner.json")

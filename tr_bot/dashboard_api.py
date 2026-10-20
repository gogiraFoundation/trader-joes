"""JSON API for the browser dashboard."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response

from tr_bot import config
from tr_bot.fees import default_backtest_fee_bps
from tr_bot.circuit_breaker import read_circuit_state_snapshot
from tr_bot.dashboard_alerts import build_alerts
from tr_bot.health_checks import check_postgres, check_redis, overall_readiness_ok
from tr_bot.ops_state import ops_view_for_api, read_equity_history, read_trading_snapshot
from tr_bot.backtest.dashboard_enrich import enrich_backtest_last_run_for_dashboard
from tr_bot.backtest.last_run import read_backtest_last_run
from tr_bot.dashboard_pnl import (
    build_equity_history_display_payload,
    build_metrics_dashboard_payload,
    build_pnl_dashboard_payload,
)
from tr_bot.promotion.dashboard_info import build_strategy_promotion_dashboard_payload
from tr_bot.trade_ledger import filter_trades, trades_to_csv
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
        "max_open_positions": int(getattr(config, "MAX_OPEN_POSITIONS", 0) or 0),
        "min_atr_pct": float(getattr(config, "MIN_ATR_PCT", 0.0) or 0.0),
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
        "strategy_engine": str(getattr(config, "STRATEGY_ENGINE", "legacy") or "legacy"),
        "smc_min_trade_score": float(getattr(config, "SMC_MIN_TRADE_SCORE", 65)),
        "exit_mode": str(getattr(config, "EXIT_MODE", "atr") or "atr"),
        "effective_tier_overrides": config.effective_tier_overrides(),
        "active_strategy_profile_id": config.ACTIVE_STRATEGY_PROFILE_ID or None,
        "canary_risk_multiplier": config.CANARY_RISK_MULTIPLIER,
        "canary_max_notional_usdt": config.CANARY_MAX_NOTIONAL_USDT,
        "canary_symbols_configured": bool((config.CANARY_SYMBOLS or "").strip()),
        "live_max_notional_usdt": float(getattr(config, "LIVE_MAX_NOTIONAL_USDT", 0.0) or 0.0),
        "live_balance_reserve_bps": float(getattr(config, "LIVE_BALANCE_RESERVE_BPS", 0.0) or 0.0),
        "exit_policy": str(getattr(config, "EXIT_POLICY", "software_polled") or "software_polled"),
        "exit_watchdog_interval_s": float(getattr(config, "EXIT_WATCHDOG_INTERVAL_S", 5.0) or 5.0),
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
        "dashboard_writes_enabled": config.DASHBOARD_WRITES_ENABLED,
        "dashboard_ui": config.DASHBOARD_UI,
    }


def _portfolio_payload() -> dict[str, Any]:
    from tr_bot.managers.portfolio import parse_portfolio_weights

    try:
        weights = parse_portfolio_weights()
        return {"ok": True, "weights": weights}
    except Exception as e:
        return {"ok": False, "error": str(e), "weights": {}}


def _fund_state_with_drawdown(st: Any) -> dict[str, Any] | None:
    """Serialize FundState and attach authoritative positive drawdown_pct."""
    if st is None:
        return None
    out = asdict(st)
    try:
        peak = float(st.peak_nav)
        last = float(st.last_nav)
    except (TypeError, ValueError, AttributeError):
        out["drawdown_pct"] = None
        return out
    if peak <= 0:
        out["drawdown_pct"] = 0.0
    else:
        out["drawdown_pct"] = round(max(0.0, (peak - last) / peak) * 100.0, 4)
    return out


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
            "state": _fund_state_with_drawdown(st),
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


def _strategy_engine_status_payload() -> dict[str, Any]:
    """SMC/legacy engine status + optional compare / go-no-go artifacts."""
    root = Path(config.ARTIFACTS_DIR).resolve()
    compare_path = root / "training" / "backtest_compare.json"
    go_path = root / "research" / "smc_go_no_go.json"
    compare = None
    go = None
    if compare_path.is_file():
        try:
            compare = json.loads(compare_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            compare = {"error": "unreadable"}
    if go_path.is_file():
        try:
            go = json.loads(go_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            go = {"error": "unreadable"}
    # Shadow agreement from recent decision audit if present
    agreement = None
    audit_raw = (config.DECISION_AUDIT_JSONL_PATH or "").strip()
    if audit_raw:
        ap = Path(audit_raw)
        if not ap.is_absolute():
            ap = root / audit_raw
        if ap.is_file():
            try:
                lines = ap.read_text(encoding="utf-8").strip().splitlines()[-30:]
                agree = 0
                total = 0
                for line in lines:
                    rec = json.loads(line)
                    bys = rec.get("by_symbol") or {}
                    for _sym, view in bys.items():
                        sh = view.get("shadow") if isinstance(view, dict) else None
                        if not isinstance(sh, dict):
                            continue
                        total += 1
                        leg = (sh.get("legacy") or {}).get("decision")
                        smc = (sh.get("smc") or {}).get("decision")
                        if leg == smc:
                            agree += 1
                if total:
                    agreement = {"pairs": total, "agree": agree, "rate": round(agree / total, 4)}
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                agreement = None
    return {
        "strategy_engine": str(getattr(config, "STRATEGY_ENGINE", "legacy") or "legacy"),
        "smc_min_trade_score": float(getattr(config, "SMC_MIN_TRADE_SCORE", 65)),
        "exit_mode": str(getattr(config, "EXIT_MODE", "atr") or "atr"),
        "backtest_compare": compare,
        "smc_go_no_go": go,
        "shadow_agreement": agreement,
    }


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
def dashboard_summary(
    sections: str | None = Query(
        None,
        description=(
            "Optional comma-separated section keys. When omitted or empty, "
            "returns the full payload. Always includes status and dependencies."
        ),
    ),
    _: None = Depends(require_dashboard_token),
) -> dict[str, Any]:
    redis_info = check_redis(config.REDIS_URL)
    pg_info = check_postgres(config.DATABASE_URL)
    req_pg = config.readiness_require_postgres()
    ready_ok = overall_readiness_ok(redis_info, pg_info, require_postgres=req_pg)

    wanted = _parse_summary_sections(sections)

    def need(name: str) -> bool:
        return wanted is None or name in wanted

    # Lazy builders: only run expensive work when the section is requested.
    circuit_payload: dict[str, Any] | None = None
    ops_payload: dict[str, Any] | None = None
    fund_payload: dict[str, Any] | None = None
    pnl_payload: dict[str, Any] | None = None
    strategy_promotion_payload: dict[str, Any] | None = None

    def get_circuit() -> dict[str, Any]:
        nonlocal circuit_payload
        if circuit_payload is None:
            snap = read_circuit_state_snapshot()
            m = snap.get("max_consecutive_losses") or config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES
            n = snap.get("consecutive_losses")
            tripped = snap.get("tripped")
            if tripped is None and n is not None:
                tripped = n >= m
            circuit_payload = {**snap, "tripped": tripped}
        return circuit_payload

    def get_ops() -> dict[str, Any]:
        nonlocal ops_payload
        if ops_payload is None:
            ops_payload = ops_view_for_api()
        return ops_payload

    def get_fund() -> dict[str, Any]:
        nonlocal fund_payload
        if fund_payload is None:
            fund_payload = _fund_payload()
        return fund_payload

    def get_pnl() -> dict[str, Any]:
        nonlocal pnl_payload
        if pnl_payload is None:
            pnl_payload = build_pnl_dashboard_payload()
        return pnl_payload

    def get_strategy_promotion() -> dict[str, Any]:
        nonlocal strategy_promotion_payload
        if strategy_promotion_payload is None:
            strategy_promotion_payload = build_strategy_promotion_dashboard_payload()
        return strategy_promotion_payload

    out: dict[str, Any] = {
        "status": "ready" if ready_ok else "degraded",
        "dependencies": {
            "redis": redis_info,
            "postgres": pg_info,
            "readiness": {
                "postgres_required_for_ready": req_pg,
                "mode": config.READINESS_MODE,
            },
        },
    }

    agents_payload: dict[str, Any] | None = None

    def get_agents() -> dict[str, Any]:
        nonlocal agents_payload
        if agents_payload is None:
            from tr_bot.agents.runner import read_agents_dashboard_payload

            agents_payload = read_agents_dashboard_payload()
        return agents_payload

    if need("alerts"):
        # Alerts always consider last agent briefs (cheap file reads).
        out["alerts"] = build_alerts(
            circuit=get_circuit(),
            fund=get_fund(),
            ops=get_ops(),
            pnl=get_pnl(),
            strategy_promotion=get_strategy_promotion(),
            max_drawdown_pct=config.MAX_DRAWDOWN_PCT,
            agents=get_agents(),
        )
    if need("ops"):
        out["ops"] = get_ops()
    if need("equity_history"):
        out["equity_history"] = read_equity_history()
    if need("equity_history_display"):
        out["equity_history_display"] = build_equity_history_display_payload()
    if need("metrics"):
        out["metrics"] = build_metrics_dashboard_payload(fund=get_fund())
    if need("trading"):
        out["trading"] = read_trading_snapshot()
    if need("config"):
        out["config"] = _sanitized_config()
    if need("strategy_engine_status"):
        out["strategy_engine_status"] = _strategy_engine_status_payload()
    if need("portfolio"):
        out["portfolio"] = _portfolio_payload()
    if need("fund"):
        out["fund"] = get_fund()
    if need("best_fit"):
        out["best_fit"] = _best_fit_payload()
    if need("circuit"):
        out["circuit"] = get_circuit()
    if need("training"):
        out["training"] = build_training_dashboard_payload()
    if need("backtest"):
        out["backtest"] = _backtest_payload()
    if need("pnl"):
        out["pnl"] = get_pnl()
    if need("scheduler"):
        out["scheduler"] = _scheduler_payload()
    if need("strategy_promotion"):
        out["strategy_promotion"] = get_strategy_promotion()
    if need("startup_pipeline"):
        out["startup_pipeline"] = read_startup_last_run()
    if need("coindesk_asset_top_list"):
        out["coindesk_asset_top_list"] = _coindesk_asset_top_list_payload()
    if need("agents"):
        out["agents"] = get_agents()

    return out


_SUMMARY_SECTION_KEYS = frozenset(
    {
        "alerts",
        "ops",
        "equity_history",
        "equity_history_display",
        "metrics",
        "trading",
        "config",
        "strategy_engine_status",
        "portfolio",
        "fund",
        "best_fit",
        "circuit",
        "training",
        "backtest",
        "pnl",
        "scheduler",
        "strategy_promotion",
        "startup_pipeline",
        "coindesk_asset_top_list",
        "agents",
    }
)


def _parse_summary_sections(sections: str | None) -> frozenset[str] | None:
    """Return None for full payload, else the requested section names.

    Unknown names raise HTTP 400. Empty / whitespace-only means full payload.
    """
    if sections is None:
        return None
    raw = str(sections).strip()
    if not raw:
        return None
    names = [p.strip() for p in raw.split(",") if p.strip()]
    if not names:
        return None
    unknown = sorted({n for n in names if n not in _SUMMARY_SECTION_KEYS})
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown summary section(s): {', '.join(unknown)}",
        )
    return frozenset(names)


@router.get("/strategy/status")
def dashboard_strategy_status(_: None = Depends(require_dashboard_token)) -> dict[str, Any]:
    """Read-only strategy promotion snapshot (pointers, gates, thresholds)."""
    return build_strategy_promotion_dashboard_payload()


@router.get("/trades")
def dashboard_trades(
    symbol: str | None = None,
    side: str | None = None,
    outcome: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    format: str | None = None,
    _: None = Depends(require_dashboard_token),
) -> Any:
    """Filtered/paginated closed-trades ledger; add `format=csv` to export."""
    result = filter_trades(
        symbol=symbol,
        side=side,
        outcome=outcome,
        from_ts=from_,
        to_ts=to,
        limit=limit,
        offset=offset,
    )
    if (format or "").strip().lower() == "csv":
        csv_text = trades_to_csv(result["rows"])
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=trades.csv"},
        )
    return result


@router.get("/best-fit/winner.json")
def dashboard_best_fit_winner_json(
    _: None = Depends(require_dashboard_token),
) -> FileResponse:
    p = _best_fit_winner_path()
    if not p.is_file():
        raise HTTPException(status_code=404, detail="winner.json not found")
    return FileResponse(path=p, media_type="application/json", filename="winner.json")

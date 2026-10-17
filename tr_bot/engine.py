"""Trading loop: integrates fund, portfolio, advisory managers."""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import ccxt

from tr_bot import config
from tr_bot.binance_rest import call_ccxt_with_retries
from tr_bot.db.decision_log import maybe_log_no_trade_decision
from tr_bot.managers.security import collapse_artifact_dir_duplication, resolve_path_under_artifact_dir
from tr_bot.account import fetch_total_base_amount
from tr_bot.circuit_breaker import CircuitBreaker
from tr_bot.exchange import get_ccxt_exchange
from tr_bot.health_checks import check_postgres, check_redis
from tr_bot.analysis.fundamentals import allows_fundamentals, fetch_fundamentals_snapshot
from tr_bot.analysis.cmc_altcoin_season import get_altcoin_season_snapshot
from tr_bot.analysis.cmc_fear_greed import get_fear_greed_snapshot
from tr_bot.analysis.cmc_global import get_global_metrics_snapshot
from tr_bot.decision import decision_engine
from tr_bot.execution import execute_trade, place_protective_stop, resolve_fill_price, resolve_fill_qty
from tr_bot.exit_coordinator import ExitCoordinator
from tr_bot.exit_plan import build_exit_plan, simulate_software_polled_exit_price
from tr_bot.exit_watchdog import sleep_with_exit_watchdog
from tr_bot.twap_reconcile import reconcile_open_twap_positions
from tr_bot.indicators import compute_all_indicators, compute_indicators
from tr_bot.logger import setup_logger
from tr_bot.market_data import fetch_ohlcv
from tr_bot.managers import advise as advise_mod
from tr_bot.managers.fund import FundManager
from tr_bot.managers.portfolio import active_symbols, weight_for
from tr_bot.open_positions_state import load_open_positions, save_open_positions
from tr_bot.quote_usdt import QuoteUsdtRateError, usdt_per_quote_unit
from tr_bot.ops_state import (
    append_equity_point,
    write_runtime_error,
    write_runtime_success,
    write_trading_snapshot,
)
from tr_bot.position import OpenPosition
from tr_bot.regime import RegimeModel
from tr_bot.symbols import split_pair
from tr_bot.risk import RiskManager
from tr_bot.strategy.no_trade_adaptation import record_loop_without_open
from tr_bot.strategy.entry_gates import (
    apply_post_decision_gates,
    open_positions_at_limit,
)
from tr_bot.signals import mean_reversion_signal, trend_continuation_signal
from tr_bot.notifications import events as notify_events
from tr_bot.notifications.facade import notify
from tr_bot.ta_extended import mean_reversion_ta_allows, trend_regime_ta_allows
from tr_bot.strategy.decision_audit import DECISION_AUDIT_SCHEMA_VERSION
from tr_bot.strategy.rollup import maybe_write_daily_rollup
from tr_bot.strategy.tier import (
    apply_canary_notional_cap,
    apply_live_notional_cap,
    apply_paper_notional_cap,
    atr_meets_min_pct,
    canary_allows_symbol,
)

logger = setup_logger()


def _maybe_append_decision_audit_jsonl(loop_idx: int, symbol_views: dict[str, Any]) -> None:
    raw = (config.DECISION_AUDIT_JSONL_PATH or "").strip()
    if not raw:
        return
    n = max(1, int(config.DECISION_AUDIT_EVERY_N_LOOPS))
    if loop_idx % n != 0:
        return
    p = Path(raw)
    if not p.is_absolute():
        p = Path(config.ARTIFACTS_DIR).resolve() / raw
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "schema_version": DECISION_AUDIT_SCHEMA_VERSION,
            "loop": loop_idx,
            "utc": datetime.now(timezone.utc).isoformat(),
            "deployment_tier": config.DEPLOYMENT_TIER,
            "profile_id": config.ACTIVE_STRATEGY_PROFILE_ID or None,
            "strategy_engine": str(getattr(config, "STRATEGY_ENGINE", "legacy") or "legacy"),
            "by_symbol": symbol_views,
        }
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError as e:
        logger.warning("Decision audit JSONL write failed: %s", e)


def _is_ccxt_symbol(symbol: str) -> bool:
    return "/" in symbol


def _signal_branch(regime: str) -> str:
    if regime == "range":
        return "mean_reversion"
    if regime == "trend" and config.TREND_STRATEGY_ENABLED:
        return "trend"
    return "none"


def _evaluate_symbol_legacy(
    df: Any,
    last: Any,
    symbol: str,
    *,
    regime_model: Any,
) -> dict[str, Any]:
    """Legacy mean-reversion / trend decision path. Returns evaluation dict."""
    regime, regime_confidence = regime_model.predict_with_confidence(df)
    if regime == "range":
        signal = mean_reversion_signal(df)
        ta_ok = mean_reversion_ta_allows(last)
    elif regime == "trend" and config.TREND_STRATEGY_ENABLED:
        signal = trend_continuation_signal(df)
        ta_ok = trend_regime_ta_allows(last)
    else:
        signal = "hold"
        ta_ok = True

    if config.USE_LLM:
        sentiment = advise_mod.get_sentiment_for_symbol(symbol)
    else:
        sentiment = "neutral"

    snap = (
        fetch_fundamentals_snapshot(symbol)
        if config.USE_FUNDAMENTALS_FILTER
        else None
    )
    fundamentals_ok = allows_fundamentals(symbol, signal, snap)
    reason = None
    if not ta_ok:
        reason = "ta_block"
    elif not fundamentals_ok:
        reason = "fundamentals_block"

    decision = decision_engine(
        regime,
        signal,
        sentiment,
        ta_ok=ta_ok,
        fundamentals_ok=fundamentals_ok,
        trend_strategy_enabled=config.TREND_STRATEGY_ENABLED,
    )
    return {
        "regime": regime,
        "regime_confidence": float(regime_confidence),
        "signal": signal,
        "sentiment": sentiment,
        "ta_ok": ta_ok,
        "fundamentals_ok": fundamentals_ok,
        "decision": decision,
        "reason": reason,
        "signal_branch": _signal_branch(regime),
        "smc_proposal": None,
    }


def _evaluate_symbol_smc(
    df: Any,
    symbol: str,
    *,
    regime_model: Any,
) -> dict[str, Any]:
    from tr_bot.strategy.market_context import build_market_context
    from tr_bot.strategy.multi_tf import fetch_multi_tf
    from tr_bot.strategy.signal_pipeline import analyze_symbol, proposal_to_decision

    regime, regime_confidence = regime_model.predict_with_confidence(df)
    ctx = build_market_context(
        ml_regime=regime,
        regime_confidence=float(regime_confidence),
        df_exec=df,
        symbol=symbol,
        include_cmc=bool(getattr(config, "CMC_GLOBAL_METRICS_ENABLED", False)),
    )
    try:
        dfs = fetch_multi_tf(symbol, limit=max(80, int(config.LOOP_BARS)))
    except Exception:
        dfs = {}
    if not dfs:
        dfs = {str(config.TIMEFRAME): df}
    else:
        dfs[str(config.TIMEFRAME)] = df
    proposal = analyze_symbol(symbol, dfs_by_tf=dfs, context=ctx)
    decision = proposal_to_decision(proposal)
    sentiment = "neutral"
    if config.USE_LLM:
        sentiment = advise_mod.get_sentiment_for_symbol(symbol)
        if decision == "buy" and sentiment == "bearish":
            decision = "hold"
            proposal.blocked_gate = proposal.blocked_gate or "sentiment_veto"
        elif decision == "sell" and sentiment == "bullish":
            decision = "hold"
            proposal.blocked_gate = proposal.blocked_gate or "sentiment_veto"
    # Fundamentals veto after sentiment (fail-open rules live in allows_fundamentals).
    fundamentals_ok = True
    if decision in ("buy", "sell"):
        snap = fetch_fundamentals_snapshot(symbol)
        fundamentals_ok = allows_fundamentals(symbol, decision, snap)
        if not fundamentals_ok:
            decision = "hold"
            proposal.blocked_gate = proposal.blocked_gate or "fundamentals_block"
    return {
        "regime": regime,
        "regime_confidence": float(regime_confidence),
        "signal": proposal.direction if proposal else "hold",
        "sentiment": sentiment,
        "ta_ok": True,
        "fundamentals_ok": fundamentals_ok,
        "decision": decision,
        "reason": (
            (proposal.blocked_gate if proposal else None) if decision == "hold" else None
        ),
        "signal_branch": "smc",
        "smc_proposal": proposal,
    }


def _safe_float(x: Any) -> float | None:
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _execution_acknowledged(res: dict[str, Any]) -> tuple[bool, str | None]:
    status = str(res.get("status", "")).strip().lower()
    if status in (
        "skipped",
        "rejected",
        "canceled",
        "cancelled",
        "expired",
        "unknown_execution",
        "unknown",
    ):
        return False, status or "execution_not_acknowledged"
    return True, None


def _market_entry_fill_ok(res: dict[str, Any], *, requested: float) -> tuple[bool, str | None, float]:
    """
    For market (non-TWAP) entries: require an order id and positive filled qty.
    Returns (ok, reason, filled_qty).
    """
    entry_exec = str(res.get("entry_execution") or "market").strip().lower()
    if entry_exec == "twap":
        return True, None, float(requested)
    oid = str(res.get("id") or "").strip()
    if not oid:
        return False, "missing_order_id", 0.0
    filled, _src = resolve_fill_qty(res, requested=float(requested))
    if filled is None or filled <= 0:
        return False, "zero_or_missing_fill", 0.0
    return True, None, float(filled)


def _risk_snapshot(risk: RiskManager) -> dict[str, Any]:
    risk._roll_day()
    return {
        "balance": float(risk.balance),
        "daily_trades": risk.daily_trades,
        "max_daily_trades": risk.max_daily_trades,
        "can_trade": risk.can_trade(),
    }


def _fund_snapshot_line(fund: Optional[FundManager]) -> dict[str, Any] | None:
    if not fund or not config.MANAGERS_ENABLED:
        return None
    st = fund.get_state()
    if not st:
        return None
    peak = float(st.peak_nav)
    nav = float(st.last_nav)
    dd = (peak - nav) / peak if peak > 0 else 0.0
    return {
        "last_nav": nav,
        "peak_nav": peak,
        "drawdown_pct": dd,
        "line": f"Fund NAV={nav:.4f} peak={peak:.4f} dd={dd:.4f}",
    }


def _entry_pause_snapshot_note(reason: str | None) -> str | None:
    if reason == "manual_halt_file":
        return "new_entries_paused_manual_halt"
    if reason:
        return "new_entries_paused_fund"
    return None


def _eval_row(
    symbol: str,
    last: Any,
    regime: str,
    signal: str,
    sentiment: str,
    decision: str,
    ta_ok: bool,
    fundamentals_ok: bool,
    risk: RiskManager,
    **extra: Any,
) -> dict[str, Any]:
    risk._roll_day()
    row: dict[str, Any] = {
        "symbol": symbol,
        "regime": regime,
        "signal": signal,
        "sentiment": sentiment,
        "decision": decision,
        "ta_ok": ta_ok,
        "fundamentals_ok": fundamentals_ok,
        "close": _safe_float(last.get("close")),
        "rsi": _safe_float(last.get("rsi")),
        "atr": _safe_float(last.get("atr")),
        "daily_trades": risk.daily_trades,
        "max_daily_trades": risk.max_daily_trades,
        "can_trade": risk.can_trade(),
    }
    row.update(extra)
    return row


def _paper_unrealized_pnl_usdt(
    positions: dict[str, Optional[OpenPosition]],
    latest_close_by_symbol: dict[str, float],
) -> float:
    total = 0.0
    for symbol, pos in positions.items():
        if pos is None:
            continue
        px = latest_close_by_symbol.get(symbol)
        if px is None:
            continue
        if pos.side == "long":
            total += (px - float(pos.entry_price)) * float(pos.size)
        else:
            total += (float(pos.entry_price) - px) * float(pos.size)
    return float(total)


def _prefill_latest_closes_for_open_positions(
    positions: dict[str, Optional[OpenPosition]],
) -> dict[str, float]:
    """
    Spot last prices for symbols with open positions so periodic fund.refresh
    can include unrealized PnL before the main OHLCV loop runs.
    """
    out: dict[str, float] = {}
    for sym, pos in positions.items():
        if pos is None or not _is_ccxt_symbol(sym):
            continue
        try:
            ex = get_ccxt_exchange()
            t = call_ccxt_with_retries(
                ex,
                lambda s=sym: ex.fetch_ticker(s),
                context=f"fund_prefill_ticker {sym}",
            )
            px = float(t.get("last") or t.get("close") or 0)
            if px > 0:
                out[sym] = px
        except Exception as e:
            logger.debug("fund prefill ticker skipped %s: %s", sym, e)
    return out


def _recent_net_base_from_trades(symbol: str) -> float | None:
    if not config.RESTORE_RECONCILE_USE_ORDER_HISTORY:
        return None
    try:
        ex = get_ccxt_exchange()
        ex.load_markets()
        trades = ex.fetch_my_trades(symbol, limit=max(1, int(config.RESTORE_RECONCILE_TRADES_LOOKBACK)))
    except Exception:
        return None
    net = 0.0
    for t in trades or []:
        side = str(t.get("side") or "").lower()
        amt = _safe_float(t.get("amount"))
        if amt is None:
            continue
        if side == "buy":
            net += float(amt)
        elif side == "sell":
            net -= float(amt)
    return net


def _order_id_reconciles(symbol: str, pos: OpenPosition, min_required: float) -> tuple[bool, str | None]:
    oid = (pos.entry_order_id or "").strip()
    if not oid:
        if config.RESTORE_RECONCILE_REQUIRE_ORDER_ID:
            return False, "restore_reconcile_missing_entry_order_id"
        return True, None
    try:
        ex = get_ccxt_exchange()
        ex.load_markets()
        order = ex.fetch_order(oid, symbol)
    except Exception:
        return False, "restore_reconcile_order_lookup_failed"
    status = str(order.get("status") or "").lower()
    if status in ("canceled", "cancelled", "rejected", "expired"):
        return False, "restore_reconcile_entry_order_not_filled"
    filled = _safe_float(order.get("filled"))
    amount = _safe_float(order.get("amount"))
    qty = filled if filled is not None else amount
    if qty is not None and qty < min_required:
        return False, "restore_reconcile_entry_order_underfilled"
    return True, None


def _push_trading_snapshot(
    symbol_views: dict[str, Any],
    risk: RiskManager,
    fund: Optional[FundManager],
    restored_from_state: bool = False,
    note: str | None = None,
    cmc_global: dict[str, Any] | None = None,
    cmc_fear_greed: dict[str, Any] | None = None,
    cmc_altcoin_season: dict[str, Any] | None = None,
) -> None:
    write_trading_snapshot(
        {
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "by_symbol": symbol_views,
            "risk": _risk_snapshot(risk),
            "fund": _fund_snapshot_line(fund),
            "cmc_global": cmc_global,
            "cmc_fear_greed": cmc_fear_greed,
            "cmc_altcoin_season": cmc_altcoin_season,
            "restored_from_state": bool(restored_from_state),
            "note": note,
        }
    )


def _finish_cycle_success(risk: RiskManager, fund: Optional[FundManager]) -> None:
    if fund and config.MANAGERS_ENABLED:
        nav = float(fund.get_last_nav())
    else:
        nav = float(risk.balance)
    append_equity_point(nav)
    write_runtime_success()


def run_forever() -> None:
    logging.getLogger("tr_bot").setLevel(logging.INFO)

    from tr_bot.runtime_validation import assert_runtime_config_safe

    # Fail closed before rewriting durable position state.
    assert_runtime_config_safe()

    # Keep a FundManager instance even when MANAGERS_ENABLED is false so the
    # manual halt marker remains effective in lightweight deployments. NAV/
    # drawdown refresh still remains gated inside FundManager methods.
    fund: Optional[FundManager] = FundManager()

    symbols = active_symbols()

    positions: dict[str, Optional[OpenPosition]] = {s: None for s in symbols}
    restored = load_open_positions()
    restored_count = 0
    for s in symbols:
        p = restored.get(s)
        if p is not None:
            positions[s] = p
            restored_count += 1
    # Rewrite persisted state after symbol reconciliation so stale symbols are dropped.
    save_open_positions(positions)
    if restored_count > 0:
        logger.warning("Restored %s open position(s) from durable state", restored_count)
    restored_from_state = restored_count > 0
    reconcile_blocked_reason: dict[str, str] = {}
    if restored_from_state and not config.PAPER_TRADING:
        tol = max(0.0, min(float(config.RESTORE_RECONCILE_TOLERANCE_PCT), 1.0))
        for s in symbols:
            p = positions.get(s)
            if p is None:
                continue
            if not _is_ccxt_symbol(s):
                reconcile_blocked_reason[s] = "live_requires_ccxt_pair_for_restore"
                positions[s] = None
                continue
            if p.side != "long":
                reconcile_blocked_reason[s] = "unsupported_restored_side_in_live"
                positions[s] = None
                continue
            total_base = fetch_total_base_amount(s)
            min_required = max(0.0, float(p.size) * (1.0 - tol))
            ok_by_order, order_reason = _order_id_reconciles(s, p, min_required)
            if not ok_by_order:
                reconcile_blocked_reason[s] = order_reason or "restore_reconcile_order_mismatch"
                positions[s] = None
                continue
            if total_base < min_required:
                reconcile_blocked_reason[s] = "restore_reconcile_base_insufficient"
                positions[s] = None
                continue
            net_base = _recent_net_base_from_trades(s)
            if net_base is not None and net_base < min_required:
                reconcile_blocked_reason[s] = "restore_reconcile_trade_history_mismatch"
                positions[s] = None
        save_open_positions(positions)
        if reconcile_blocked_reason:
            logger.warning("Startup restore reconciliation blocked symbols: %s", reconcile_blocked_reason)

    risk = RiskManager(
        balance=float(config.PAPER_EQUITY_USDT),
        risk_per_trade=config.effective_risk_per_trade(),
        max_daily_trades=config.MAX_TRADES_PER_DAY,
    )
    circuit = CircuitBreaker()
    cooldown_bars_remaining: dict[str, int] = {s: 0 for s in symbols}
    exit_coordinator = ExitCoordinator(
        positions,
        circuit=circuit,
        cooldown_bars_remaining=cooldown_bars_remaining,
    )
    # Resume any in-flight exits before the first strategy cycle.
    try:
        exit_coordinator.reconcile_pending()
    except Exception as e:
        logger.warning("Startup exit reconcile failed: %s", e)

    model_path: Path | None = None
    if config.REGIME_MODEL_PATH.strip():
        rp = config.REGIME_MODEL_PATH.strip()
        root = Path(config.ARTIFACTS_DIR).resolve()
        model_path = (
            resolve_path_under_artifact_dir(root, rp)
            if not Path(rp).is_absolute()
            else collapse_artifact_dir_duplication(root, Path(rp))
        )
    if model_path and model_path.is_file():
        regime_model = RegimeModel.load(model_path)
        logger.info("Loaded regime model from %s", model_path.resolve())
    else:
        regime_model = RegimeModel()
        sym0 = active_symbols()[0]
        initial_df = fetch_ohlcv(sym0, config.TIMEFRAME, limit=config.INITIAL_BARS)
        initial_df = compute_indicators(initial_df)
        initial_df = initial_df.dropna(subset=["rsi", "ema20", "ema50", "atr"])
        regime_model.train(initial_df)
        logger.info("Regime model trained in-process on %s rows", len(initial_df))

    if config.MANAGERS_ENABLED and config.ADVISORY_WORKER_IN_PROCESS and config.USE_LLM:
        advise_mod.start_advisory_worker()
        logger.info("Started in-process advisory worker")

    loop_idx = 0
    while True:
        try:
            loop_idx += 1
            cmc_snap = get_global_metrics_snapshot()
            cmc_fg = get_fear_greed_snapshot()
            cmc_as = get_altcoin_season_snapshot()
            ri = check_redis(config.REDIS_URL)
            pi = check_postgres(config.DATABASE_URL)
            if not ri.get("ok") or not pi.get("ok"):
                notify(
                    notify_events.DEPENDENCIES_DEGRADED,
                    redis=ri,
                    postgres=pi,
                )

            symbol_views: dict[str, Any] = {}
            latest_close_by_symbol: dict[str, float] = {}
            entry_block_reason: str | None = None

            now = time.time()
            if fund:
                pre_u = 0.0
                if (
                    config.MANAGERS_ENABLED
                    and config.PAPER_TRADING
                    and fund.would_refresh(now)
                ):
                    latest_close_by_symbol.update(
                        _prefill_latest_closes_for_open_positions(positions)
                    )
                    pre_u = _paper_unrealized_pnl_usdt(positions, latest_close_by_symbol)
                fund.maybe_refresh(now, unrealized_pnl_usdt=pre_u)
                entry_block_reason = fund.entry_block_reason()
                if entry_block_reason:
                    logger.warning(
                        "New entries paused (%s); existing positions still managed",
                        entry_block_reason,
                    )
                if config.MANAGERS_ENABLED:
                    risk.balance = max(fund.get_last_nav(), 1.0)

            if not config.PAPER_TRADING:
                try:
                    reconcile_open_twap_positions(get_ccxt_exchange(), positions)
                    save_open_positions(positions)
                except Exception as e:
                    logger.warning("TWAP reconcile step failed: %s", e)

            try:
                exit_coordinator.reconcile_pending()
            except Exception as e:
                logger.warning("Exit pending reconcile failed: %s", e)

            try:
                from tr_bot.managers.rebalancer import (
                    drain_exit_requests_for_symbols,
                    load_exit_requests,
                )

                done: set[str] = set()
                for req in load_exit_requests():
                    sym = str(req.get("symbol") or "")
                    if not sym:
                        continue
                    pos = positions.get(sym)
                    if pos is None:
                        done.add(sym)
                        continue
                    st = str(getattr(pos, "exit_state", "active") or "active").lower()
                    if st in ("pending", "unknown"):
                        # Already in flight — leave request until finalize clears position.
                        continue
                    px = float(latest_close_by_symbol.get(sym) or pos.entry_price or 0.0)
                    if px <= 0:
                        px = float(pos.entry_price)
                    ctx = str(req.get("execution_context") or "REBALANCE_TRIM")
                    reason = str(req.get("reason") or "rebalance_trim")
                    # PnL outcome for ledger only; circuit skipped via execution_context.
                    outcome = "loss"
                    try:
                        if pos.side == "long" and px >= float(pos.entry_price):
                            outcome = "win"
                        elif pos.side == "short" and px <= float(pos.entry_price):
                            outcome = "win"
                    except (TypeError, ValueError):
                        outcome = "loss"
                    res = exit_coordinator.request_exit(
                        sym,
                        outcome=outcome,
                        reason=reason,
                        fallback_price=px,
                        execution_context=ctx,
                    )
                    status = str(res.get("status") or "")
                    if status == "closed" or positions.get(sym) is None:
                        done.add(sym)
                    elif status == "noop":
                        done.add(sym)
                if done:
                    drain_exit_requests_for_symbols(done)
            except Exception as e:
                logger.warning("Rebalancer exit-request drain failed: %s", e)

            twap_skip: dict[str, str] = {}
            for s, p in positions.items():
                if p and getattr(p, "entry_execution", "market") == "twap":
                    st = (getattr(p, "twap_algo_state", "") or "").lower()
                    if st in ("failed", "canceled"):
                        twap_skip[s] = f"twap_algo_{st}"

            for symbol in symbols:
                if not canary_allows_symbol(symbol):
                    symbol_views[symbol] = {"skipped": True, "reason": "canary_symbol_not_allowed"}
                    continue
                if symbol in reconcile_blocked_reason:
                    symbol_views[symbol] = {"skipped": True, "reason": reconcile_blocked_reason[symbol]}
                    continue
                if symbol in twap_skip:
                    symbol_views[symbol] = {"skipped": True, "reason": twap_skip[symbol]}
                    continue

                pos = positions[symbol]
                df = fetch_ohlcv(symbol, config.TIMEFRAME, limit=config.LOOP_BARS)
                df = compute_all_indicators(df)
                drop_cols = ["rsi", "ema20", "ema50", "atr"]
                if config.USE_EXTENDED_TA:
                    drop_cols.append("adx")
                df = df.dropna(subset=drop_cols)
                if len(df) < 20:
                    logger.warning("Insufficient data for %s; skip", symbol)
                    symbol_views[symbol] = {"skipped": True, "reason": "insufficient_data"}
                    continue

                last = df.iloc[-1]
                latest_close_by_symbol[symbol] = float(last["close"])

                if pos is not None:
                    exit_policy = str(getattr(pos, "exit_policy", "software_polled") or "software_polled")
                    if exit_policy == "exchange_native" and pos.protective_order_id:
                        # Native stops disabled at runtime; left for future rebuild.
                        pass
                    else:
                        st = str(getattr(pos, "exit_state", "active") or "active").lower()
                        if st in ("pending", "unknown"):
                            exit_coordinator.reconcile_pending()
                            pos = positions.get(symbol)
                        elif st == "partially_filled" or st == "active":
                            exited, outcome = pos.check_bar(last)
                            if exited and outcome:
                                plan = pos.exit_plan()
                                fill_px = simulate_software_polled_exit_price(
                                    side=plan.side,  # type: ignore[arg-type]
                                    stop_price=float(plan.stop_price),
                                    take_price=float(plan.take_price),
                                    bar=last,
                                    outcome=str(outcome),
                                )
                                exit_coordinator.request_exit(
                                    symbol,
                                    outcome=outcome,
                                    reason="bar_stop_or_take",
                                    fallback_price=float(fill_px),
                                )
                                pos = positions.get(symbol)

                if positions.get(symbol) is not None:
                    h = positions[symbol]
                    cur = float(last["close"])
                    unrealized = (
                        (cur - float(h.entry_price)) * float(h.size)
                        if h and h.side == "long"
                        else (float(h.entry_price) - cur) * float(h.size) if h else 0.0
                    )
                    symbol_views[symbol] = {
                        "holding": True,
                        "symbol": symbol,
                        "side": h.side if h else None,
                        "entry": float(h.entry_price) if h else None,
                        "size": float(h.size) if h else None,
                        "close": cur,
                        "unrealized_pnl_usdt": float(unrealized),
                    }
                    continue

                if entry_block_reason:
                    symbol_views[symbol] = {
                        "skipped": True,
                        "reason": (
                            "manual_halt_no_new_entries"
                            if entry_block_reason == "manual_halt_file"
                            else "fund_halt_no_new_entries"
                        ),
                    }
                    continue

                if circuit.tripped:
                    symbol_views[symbol] = {
                        "skipped": True,
                        "reason": "circuit_tripped_no_new_entries",
                    }
                    continue

                engine_mode = str(getattr(config, "STRATEGY_ENGINE", "legacy") or "legacy").lower()
                legacy_eval = _evaluate_symbol_legacy(
                    df, last, symbol, regime_model=regime_model
                )
                smc_eval = None
                if engine_mode in ("smc", "shadow"):
                    try:
                        smc_eval = _evaluate_symbol_smc(
                            df, symbol, regime_model=regime_model
                        )
                    except Exception as e:
                        logger.warning("SMC evaluate failed %s: %s", symbol, e)
                        smc_eval = {
                            "decision": "hold",
                            "reason": f"smc_error:{e}",
                            "smc_proposal": None,
                            "regime": legacy_eval["regime"],
                            "regime_confidence": legacy_eval["regime_confidence"],
                            "signal": "hold",
                            "sentiment": "neutral",
                            "ta_ok": True,
                            "fundamentals_ok": True,
                            "signal_branch": "smc",
                        }

                if engine_mode == "smc" and smc_eval is not None:
                    ev = smc_eval
                else:
                    ev = legacy_eval

                regime = ev["regime"]
                regime_confidence = ev["regime_confidence"]
                signal = ev["signal"]
                sentiment = ev["sentiment"]
                ta_ok = ev["ta_ok"]
                fundamentals_ok = ev["fundamentals_ok"]
                decision = ev["decision"]
                reason = ev.get("reason")
                signal_branch_for_open = ev.get("signal_branch") or _signal_branch(regime)
                smc_proposal = ev.get("smc_proposal")

                cooldown_active = cooldown_bars_remaining.get(symbol, 0) > 0
                if cooldown_active:
                    cooldown_bars_remaining[symbol] = max(0, cooldown_bars_remaining[symbol] - 1)

                prev = df.iloc[-2] if len(df) > 1 else None
                decision, reason = apply_post_decision_gates(
                    decision=decision,
                    reason=reason,
                    engine_mode=engine_mode,
                    regime=regime,
                    signal=signal,
                    regime_confidence=float(regime_confidence),
                    cooldown_active=cooldown_active,
                    last=last,
                    prev=prev,
                    df=df,
                    symbol=symbol,
                    paper_trading=bool(config.PAPER_TRADING),
                )

                # shadow audit fields attached later via symbol_views
                _shadow_payload = None
                if engine_mode == "shadow" and smc_eval is not None:
                    prop = smc_eval.get("smc_proposal")
                    _shadow_payload = {
                        "legacy": {
                            "decision": legacy_eval["decision"],
                            "regime": legacy_eval["regime"],
                            "signal": legacy_eval["signal"],
                        },
                        "smc": {
                            "decision": smc_eval.get("decision"),
                            "score": getattr(prop, "score", None) if prop else None,
                            "blocked_gate": getattr(prop, "blocked_gate", None) if prop else smc_eval.get("reason"),
                            "factors": getattr(prop, "smc_factors", None) if prop else None,
                        },
                    }

                if decision == "hold" or not risk.can_trade():
                    if reason is None:
                        # risk_limit_reached = MAX_TRADES_PER_DAY (openings) hit; see RiskManager.can_trade().
                        # RISK_PER_TRADE only affects position_size(), not this gate.
                        if not risk.can_trade() and decision in ("buy", "sell"):
                            reason = "risk_limit_reached"
                        else:
                            reason = "hold"
                    risk_blocks_new_open = not risk.can_trade() and decision in ("buy", "sell")
                    if risk_blocks_new_open:
                        logger.info(
                            "No trade %s (risk gate; cannot open new position): regime=%s signal=%s "
                            "sentiment=%s decision=%s ta_ok=%s fund_ok=%s reason=%s regime_confidence=%s",
                            symbol,
                            regime,
                            signal,
                            sentiment,
                            decision,
                            ta_ok,
                            fundamentals_ok,
                            reason,
                            float(regime_confidence),
                        )
                    elif decision == "hold":
                        logger.info(
                            "Hold %s: regime=%s signal=%s sentiment=%s decision=%s ta_ok=%s fund_ok=%s "
                            "reason=%s regime_confidence=%s",
                            symbol,
                            regime,
                            signal,
                            sentiment,
                            decision,
                            ta_ok,
                            fundamentals_ok,
                            reason,
                            float(regime_confidence),
                        )
                    else:
                        logger.info(
                            "No trade %s: regime=%s signal=%s sentiment=%s decision=%s ta_ok=%s fund_ok=%s "
                            "reason=%s regime_confidence=%s",
                            symbol,
                            regime,
                            signal,
                            sentiment,
                            decision,
                            ta_ok,
                            fundamentals_ok,
                            reason,
                            float(regime_confidence),
                        )
                    row = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="no_trade",
                        reason=reason,
                        regime_confidence=regime_confidence,
                        cooldown_bars_remaining=cooldown_bars_remaining.get(symbol, 0),
                    )
                    if _shadow_payload is not None:
                        row["shadow"] = _shadow_payload
                    if smc_proposal is not None:
                        row["smc"] = {
                            "score": smc_proposal.score,
                            "blocked_gate": smc_proposal.blocked_gate,
                            "factors": smc_proposal.smc_factors,
                        }
                    symbol_views[symbol] = row
                    maybe_log_no_trade_decision(
                        loop_idx=loop_idx,
                        symbol=symbol,
                        regime=regime,
                        signal=signal,
                        sentiment=sentiment,
                        decision=decision,
                        reason=reason,
                        regime_confidence=float(regime_confidence),
                        ta_ok=ta_ok,
                        fundamentals_ok=fundamentals_ok,
                        last=last,
                        cmc_snapshot=cmc_snap,
                        cmc_fear_greed_snapshot=cmc_fg,
                        cmc_altcoin_season_snapshot=cmc_as,
                    )
                    continue

                # Portfolio-wide concurrent open cap (0 = unlimited). Per-symbol
                # holding already continued above; this blocks a second symbol.
                if decision in ("buy", "sell") and open_positions_at_limit(positions):
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason="max_open_positions",
                        regime_confidence=regime_confidence,
                    )
                    maybe_log_no_trade_decision(
                        loop_idx=loop_idx,
                        symbol=symbol,
                        regime=regime,
                        signal=signal,
                        sentiment=sentiment,
                        decision=decision,
                        reason="max_open_positions",
                        regime_confidence=float(regime_confidence),
                        ta_ok=ta_ok,
                        fundamentals_ok=fundamentals_ok,
                        last=last,
                        cmc_snapshot=cmc_snap,
                        cmc_fear_greed_snapshot=cmc_fg,
                        cmc_altcoin_season_snapshot=cmc_as,
                    )
                    continue

                stop_distance = float(last["atr"])
                if stop_distance <= 0:
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason="invalid_atr",
                        regime_confidence=regime_confidence,
                    )
                    continue

                close_px = float(last["close"])
                if not atr_meets_min_pct(stop_distance, close_px):
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason="atr_too_small_for_costs",
                        regime_confidence=regime_confidence,
                    )
                    continue

                try:
                    _, quote = split_pair(symbol)
                    quote_usdt = usdt_per_quote_unit(quote)
                except (QuoteUsdtRateError, ValueError) as e:
                    logger.warning("Quote USDT rate for %s: %s", symbol, e)
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason="quote_usdt_rate_unavailable",
                        regime_confidence=regime_confidence,
                    )
                    continue

                side_for_plan = "long" if decision == "buy" else "short"
                exit_mode = str(getattr(config, "EXIT_MODE", "atr") or "atr")
                exit_policy = str(getattr(config, "EXIT_POLICY", "software_polled") or "software_polled")
                structural_stop = getattr(smc_proposal, "stop", None) if smc_proposal else None
                structural_target = getattr(smc_proposal, "target", None) if smc_proposal else None
                try:
                    plan = build_exit_plan(
                        side=side_for_plan,  # type: ignore[arg-type]
                        entry_price=float(last["close"]),
                        atr=stop_distance,
                        exit_mode=exit_mode,  # type: ignore[arg-type]
                        exit_policy=exit_policy,  # type: ignore[arg-type]
                        structural_stop=float(structural_stop) if structural_stop is not None else None,
                        structural_target=float(structural_target) if structural_target is not None else None,
                    )
                except ValueError as e:
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason=f"invalid_exit_plan:{e}",
                        regime_confidence=regime_confidence,
                    )
                    continue

                stop_distance_usdt = plan.stop_distance_usdt(quote_usdt)

                w = weight_for(symbol)
                if w <= 0:
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason="zero_weight",
                        regime_confidence=regime_confidence,
                    )
                    continue
                size = risk.position_size(stop_distance_usdt, weight=w)
                size = apply_canary_notional_cap(size, float(last["close"]), quote_usdt)
                size = apply_live_notional_cap(size, float(last["close"]), quote_usdt)
                size = apply_paper_notional_cap(size, float(last["close"]), quote_usdt)
                if size <= 0:
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason="zero_size",
                        regime_confidence=regime_confidence,
                    )
                    continue

                if not config.PAPER_TRADING and not _is_ccxt_symbol(symbol):
                    logger.warning("Live requires ccxt pair: %s", symbol)
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason="live_requires_ccxt_pair",
                        regime_confidence=regime_confidence,
                    )
                    continue

                if decision == "sell" and not _is_ccxt_symbol(symbol):
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason="sell_requires_ccxt",
                        regime_confidence=regime_confidence,
                    )
                    continue

                if decision == "sell" and not config.PAPER_TRADING:
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason="live_sell_disabled",
                        regime_confidence=regime_confidence,
                    )
                    continue

                entry_res = execute_trade(symbol, decision, size)
                acked, ack_reason = _execution_acknowledged(entry_res)
                if not acked:
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason=f"entry_execution_not_acknowledged:{ack_reason}",
                        regime_confidence=regime_confidence,
                    )
                    continue
                fill_ok, fill_reason, filled_size = _market_entry_fill_ok(entry_res, requested=size)
                if not fill_ok:
                    symbol_views[symbol] = _eval_row(
                        symbol,
                        last,
                        regime,
                        signal,
                        sentiment,
                        decision,
                        ta_ok,
                        fundamentals_ok,
                        risk,
                        action="blocked",
                        reason=f"entry_fill_invalid:{fill_reason}",
                        regime_confidence=regime_confidence,
                    )
                    continue
                risk.record_trade()

                entry_fallback = float(last["close"])
                entry_px, _entry_fill_src = resolve_fill_price(entry_res, fallback=entry_fallback)
                if entry_px is None:
                    entry_px = entry_fallback
                    _entry_fill_src = "bar_close"
                atr = float(last["atr"])
                side = "long" if decision == "buy" else "short"
                fp = {
                    "ta": ta_ok,
                    "fundamentals": fundamentals_ok,
                    "use_llm": config.USE_LLM,
                }
                intended_risk = float(stop_distance_usdt) * float(filled_size) / float(quote_usdt)
                positions[symbol] = OpenPosition(
                    symbol=symbol,
                    side=side,
                    entry_price=entry_px,
                    atr=atr,
                    size=float(filled_size),
                    regime_at_entry=regime,
                    regime_confidence_at_entry=float(regime_confidence),
                    signal_branch=signal_branch_for_open,
                    profile_id=config.ACTIVE_STRATEGY_PROFILE_ID or "",
                    profile_hash=str(getattr(config, "ACTIVE_STRATEGY_CONFIG_HASH", "") or ""),
                    deployment_tier=config.DEPLOYMENT_TIER,
                    filters_passed=fp,
                    entry_order_id=str(entry_res.get("id") or ""),
                    entry_order_utc=datetime.now(timezone.utc).isoformat(),
                    entry_execution=str(entry_res.get("entry_execution") or "market"),
                    twap_client_algo_id=str(entry_res.get("twap_client_algo_id") or ""),
                    twap_algo_state=str(entry_res.get("twap_algo_state") or ""),
                    exit_mode=exit_mode,
                    exit_policy=exit_policy,
                    structural_stop=plan.stop_price if exit_mode in ("structural", "hybrid") else None,
                    structural_target=plan.take_price if exit_mode in ("structural", "hybrid") else None,
                    exit_plan_dict=plan.to_dict(),
                    intended_risk_usdt=intended_risk,
                    trade_score=getattr(smc_proposal, "score", None) if smc_proposal else None,
                    score_components=getattr(smc_proposal, "score_components", None)
                    if smc_proposal
                    else None,
                )
                if exit_policy == "exchange_native":
                    prot_side = "sell" if side == "long" else "buy"
                    prot = place_protective_stop(
                        symbol,
                        side=prot_side,
                        size=float(filled_size),
                        stop_price=float(plan.stop_price),
                    )
                    if prot.get("success") or prot.get("status") in ("simulated", "open", "submitted"):
                        positions[symbol].protective_order_id = str(prot.get("id") or "")
                        positions[symbol].protective_client_order_id = str(
                            prot.get("clientOrderId") or prot.get("client_order_id") or ""
                        )
                        positions[symbol].protective_order_state = str(prot.get("status") or "open")
                    else:
                        logger.error(
                            "Protective stop failed for %s; flattening entry",
                            symbol,
                        )
                        flatten = execute_trade(symbol, prot_side, float(filled_size))
                        if _execution_acknowledged(flatten)[0]:
                            positions[symbol] = None
                        continue
                save_open_positions(positions)
                try:
                    from tr_bot.strategy.explainability import (
                        append_decision_record,
                        build_decision_record,
                    )

                    append_decision_record(
                        build_decision_record(
                            trade_id=str(entry_res.get("id") or f"{symbol}-{loop_idx}"),
                            symbol=symbol,
                            direction=side,
                            entry_price=entry_px,
                            status="open",
                            proposal=smc_proposal,
                            risk={"size": float(filled_size), "atr": atr},
                            execution={"style": entry_res.get("entry_execution"), "id": entry_res.get("id")},
                        )
                    )
                except Exception as e:
                    logger.debug("explainability write skipped: %s", e)
                logger.info("Opened %s %s size=%s", symbol, side, filled_size)
                notify(
                    notify_events.TRADE_OPENED,
                    symbol=symbol,
                    side=side,
                    size=float(filled_size),
                    entry=entry_px,
                    paper=config.PAPER_TRADING,
                )
                symbol_views[symbol] = _eval_row(
                    symbol,
                    last,
                    regime,
                    signal,
                    sentiment,
                    decision,
                    ta_ok,
                    fundamentals_ok,
                    risk,
                    action="opened",
                    side=side,
                    size=float(filled_size),
                    entry=entry_px,
                    regime_confidence=regime_confidence,
                    cooldown_bars_remaining=cooldown_bars_remaining.get(symbol, 0),
                )

            _push_trading_snapshot(
                symbol_views,
                risk,
                fund,
                restored_from_state=restored_from_state,
                note=_entry_pause_snapshot_note(entry_block_reason),
                cmc_global=cmc_snap,
                cmc_fear_greed=cmc_fg,
                cmc_altcoin_season=cmc_as,
            )
            if fund and config.MANAGERS_ENABLED and config.PAPER_TRADING:
                fund.refresh_nav_and_limits(
                    unrealized_pnl_usdt=_paper_unrealized_pnl_usdt(
                        positions,
                        latest_close_by_symbol,
                    )
                )
            _finish_cycle_success(risk, fund)
            maybe_write_daily_rollup()
            _maybe_append_decision_audit_jsonl(loop_idx, symbol_views)
            if config.ADAPTATION_ENABLED:
                record_loop_without_open(symbol_views, loop_idx)

        except ccxt.NetworkError as e:
            write_runtime_error(e)
            logger.warning("Network error (exchange): %s", e)
            notify(notify_events.BOT_ERROR, message=str(e)[:400])
        except Exception as e:
            write_runtime_error(e)
            logger.exception("Cycle error: %s", e)
            notify(notify_events.BOT_ERROR, message=str(e)[:400])

        sleep_with_exit_watchdog(
            float(config.POLL_SECONDS),
            coordinator=exit_coordinator,
            positions=positions,
            sleep_fn=time.sleep,
        )

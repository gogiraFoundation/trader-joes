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
from tr_bot.db.decision_log import maybe_log_no_trade_decision
from tr_bot.managers.security import collapse_artifact_dir_duplication, resolve_path_under_artifact_dir
from tr_bot.account import fetch_free_base_amount, fetch_total_base_amount
from tr_bot.circuit_breaker import CircuitBreaker
from tr_bot.exchange import get_ccxt_exchange
from tr_bot.health_checks import check_postgres, check_redis
from tr_bot.analysis.fundamentals import allows_fundamentals, fetch_fundamentals_snapshot
from tr_bot.analysis.cmc_altcoin_season import get_altcoin_season_snapshot
from tr_bot.analysis.cmc_fear_greed import get_fear_greed_snapshot
from tr_bot.analysis.cmc_global import get_global_metrics_snapshot
from tr_bot.decision import decision_engine
from tr_bot.execution import execute_trade
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
from tr_bot.fees import live_fee_bps_for_symbol
from tr_bot.pnl_costs import realized_pnl_breakdown
from tr_bot.position import OpenPosition
from tr_bot.regime import RegimeModel
from tr_bot.symbols import split_pair
from tr_bot.risk import RiskManager
from tr_bot.strategy.no_trade_adaptation import record_loop_without_open
from tr_bot.signals import mean_reversion_signal, trend_continuation_signal
from tr_bot.notifications import events as notify_events
from tr_bot.notifications.facade import notify
from tr_bot.ta_extended import mean_reversion_ta_allows, trend_regime_ta_allows
from tr_bot.strategy.decision_audit import DECISION_AUDIT_SCHEMA_VERSION
from tr_bot.strategy.rollup import maybe_write_daily_rollup
from tr_bot.strategy.tier import apply_canary_notional_cap, canary_allows_symbol
from tr_bot.trade_ledger import append_closed_trade

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


def _risk_snapshot(risk: RiskManager) -> dict[str, Any]:
    risk._roll_day()
    return {
        "balance": float(risk.balance),
        "daily_trades": risk.daily_trades,
        "max_daily_trades": risk.max_daily_trades,
        "can_trade": risk.can_trade(),
    }


def _signal_strength_allows(regime: str, signal: str, last: Any) -> tuple[bool, str | None]:
    if not config.USE_SIGNAL_STRENGTH_FILTER:
        return True, None
    if signal not in ("buy", "sell"):
        return True, None
    rsi = _safe_float(last.get("rsi"))
    if rsi is None:
        return False, "missing_rsi"
    if signal == "buy":
        dist = float(config.SIGNAL_RSI_OVERSOLD) - rsi
    else:
        dist = rsi - float(config.SIGNAL_RSI_OVERBOUGHT)
    if dist < float(config.SIGNAL_MIN_RSI_DISTANCE):
        return False, "rsi_distance_too_small"
    adx = _safe_float(last.get("adx"))
    if adx is None:
        return True, None
    if regime == "range":
        max_adx = float(config.ADX_MAX_FOR_MEAN_REVERSION) - float(config.SIGNAL_MIN_ADX_MARGIN)
        if adx > max_adx:
            return False, "adx_too_high_for_range"
    elif regime == "trend":
        min_adx = float(config.ADX_MIN_FOR_TREND) + float(config.SIGNAL_MIN_ADX_MARGIN)
        if adx < min_adx:
            return False, "adx_too_low_for_trend"
    return True, None


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


def _microstructure_allows(last: Any, prev: Any) -> tuple[bool, str | None]:
    if not config.USE_MICROSTRUCTURE_GUARD:
        return True, None
    close = _safe_float(last.get("close"))
    high = _safe_float(last.get("high"))
    low = _safe_float(last.get("low"))
    vol = _safe_float(last.get("volume"))
    prev_close = _safe_float(prev.get("close")) if prev is not None else None
    if close is None or close <= 0 or high is None or low is None:
        return False, "micro_missing_ohlc"
    range_pct = ((high - low) / close) * 100.0
    if range_pct > float(config.MICRO_MAX_BAR_RANGE_PCT):
        return False, "micro_range_too_wide"
    if prev_close is not None and prev_close > 0:
        jump_pct = abs((close / prev_close) - 1.0) * 100.0
        if jump_pct > float(config.MICRO_MAX_BAR_JUMP_PCT):
            return False, "micro_jump_too_large"
    if vol is None or vol < float(config.MICRO_MIN_BAR_VOLUME):
        return False, "micro_volume_too_low"
    return True, None


def _spread_depth_allows(symbol: str) -> tuple[bool, str | None]:
    if not config.MICRO_USE_SPREAD_DEPTH_GUARD:
        return True, None
    try:
        ex = get_ccxt_exchange()
        ob = ex.fetch_order_book(symbol, limit=5)
    except Exception:
        return False, "micro_orderbook_unavailable"
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    if not bids or not asks:
        return False, "micro_orderbook_empty"
    bid = float(bids[0][0])
    ask = float(asks[0][0])
    if bid <= 0 or ask <= 0 or ask < bid:
        return False, "micro_invalid_top_of_book"
    mid = (bid + ask) / 2.0
    spread_pct = ((ask - bid) / mid) * 100.0 if mid > 0 else 100.0
    if spread_pct > float(config.MICRO_MAX_SPREAD_PCT):
        return False, "micro_spread_too_wide"
    depth = 0.0
    for px, amt in bids[:5]:
        depth += float(px) * float(amt)
    for px, amt in asks[:5]:
        depth += float(px) * float(amt)
    if depth < float(config.MICRO_MIN_TOP_DEPTH_NOTIONAL):
        return False, "micro_depth_too_thin"
    return True, None


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

    fund: Optional[FundManager] = None
    if config.MANAGERS_ENABLED:
        fund = FundManager()

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

            now = time.time()
            if fund:
                fund.maybe_refresh(now)
                if not fund.is_trading_allowed():
                    logger.warning("Trading halted by fund manager; sleeping")
                    _push_trading_snapshot(
                        symbol_views,
                        risk,
                        fund,
                        restored_from_state=restored_from_state,
                        note="trading_halted_by_fund",
                        cmc_global=cmc_snap,
                        cmc_fear_greed=cmc_fg,
                        cmc_altcoin_season=cmc_as,
                    )
                    _finish_cycle_success(risk, fund)
                    _maybe_append_decision_audit_jsonl(loop_idx, symbol_views)
                    time.sleep(config.POLL_SECONDS)
                    continue
                risk.balance = max(fund.get_last_nav(), 1.0)

            if not config.PAPER_TRADING:
                try:
                    reconcile_open_twap_positions(get_ccxt_exchange(), positions)
                    save_open_positions(positions)
                except Exception as e:
                    logger.warning("TWAP reconcile step failed: %s", e)

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
                    exited, outcome = pos.check_bar(last)
                    if exited:
                        close_side = "sell" if pos.side == "long" else "buy"
                        size_close = float(pos.size)
                        if (
                            not config.PAPER_TRADING
                            and pos.side == "long"
                            and _is_ccxt_symbol(symbol)
                        ):
                            free = fetch_free_base_amount(symbol)
                            size_close = min(size_close, free)
                            if size_close <= 0:
                                logger.error("ATR exit: no base for %s", symbol)
                                continue
                        res = execute_trade(symbol, close_side, size_close)
                        close_acked, close_reason = _execution_acknowledged(res)
                        if not close_acked:
                            logger.error("ATR exit not acknowledged for %s: %s", symbol, close_reason)
                            continue
                        exit_px = float(last["close"])
                        br = realized_pnl_breakdown(
                            pos.side,
                            float(pos.entry_price),
                            exit_px,
                            float(pos.size),
                            live_fee_bps_for_symbol(symbol),
                            config.LIVE_SLIPPAGE_BPS,
                        )
                        row = {
                            "utc": datetime.now(timezone.utc).isoformat(),
                            "symbol": symbol,
                            "position_side": pos.side,
                            "outcome": outcome,
                            "entry_price": float(pos.entry_price),
                            "exit_price": exit_px,
                            "size": float(pos.size),
                            "gross_pnl_usdt": br["gross_pnl_usdt"],
                            "fee_usdt": br["fee_usdt"],
                            "net_pnl_usdt": br["net_pnl_usdt"],
                            "paper": config.PAPER_TRADING,
                            "exit_price_assumption": br.get("exit_price_assumption"),
                            "profile_id": pos.profile_id or None,
                            "deployment_tier": pos.deployment_tier or config.DEPLOYMENT_TIER,
                            "regime_at_entry": pos.regime_at_entry or None,
                            "regime_confidence_at_entry": pos.regime_confidence_at_entry,
                            "signal_branch": pos.signal_branch or None,
                            "filters_passed": pos.filters_passed,
                        }
                        append_closed_trade(row)
                        circuit.record_outcome(outcome == "win")
                        if outcome != "win":
                            cooldown_bars_remaining[symbol] = max(
                                int(config.LOSS_COOLDOWN_BARS), cooldown_bars_remaining.get(symbol, 0)
                            )
                        notify(
                            notify_events.TRADE_CLOSED,
                            symbol=symbol,
                            outcome=outcome,
                            position_side=pos.side,
                            paper=config.PAPER_TRADING,
                        )
                        positions[symbol] = None
                        save_open_positions(positions)
                        pos = None

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

                if circuit.tripped:
                    symbol_views[symbol] = {
                        "skipped": True,
                        "reason": "circuit_tripped_no_new_entries",
                    }
                    continue

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

                cooldown_active = cooldown_bars_remaining.get(symbol, 0) > 0
                if cooldown_active:
                    cooldown_bars_remaining[symbol] = max(0, cooldown_bars_remaining[symbol] - 1)

                decision = decision_engine(
                    regime,
                    signal,
                    sentiment,
                    ta_ok=ta_ok,
                    fundamentals_ok=fundamentals_ok,
                    trend_strategy_enabled=config.TREND_STRATEGY_ENABLED,
                )

                if regime_confidence < float(config.REGIME_MIN_CONFIDENCE):
                    decision = "hold"
                    reason = "low_regime_confidence"

                strength_ok, strength_reason = _signal_strength_allows(regime, signal, last)
                if decision != "hold" and not strength_ok:
                    decision = "hold"
                    reason = f"weak_signal:{strength_reason}" if strength_reason else "weak_signal"

                if decision != "hold" and cooldown_active:
                    decision = "hold"
                    reason = "cooldown_active"
                if decision != "hold":
                    prev = df.iloc[-2] if len(df) > 1 else None
                    micro_ok, micro_reason = _microstructure_allows(last, prev)
                    if not micro_ok:
                        decision = "hold"
                        reason = micro_reason or "microstructure_guard_blocked"
                    elif not config.PAPER_TRADING:
                        spread_ok, spread_reason = _spread_depth_allows(symbol)
                        if not spread_ok:
                            decision = "hold"
                            reason = spread_reason or "spread_depth_guard_blocked"

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
                        action="no_trade",
                        reason=reason,
                        regime_confidence=regime_confidence,
                        cooldown_bars_remaining=cooldown_bars_remaining.get(symbol, 0),
                    )
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

                stop_distance_usdt = stop_distance * quote_usdt

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
                risk.record_trade()

                entry = float(last["close"])
                atr = float(last["atr"])
                # TWAP: entry/size are provisional until twap_reconcile reflects Binance algo fills.
                side = "long" if decision == "buy" else "short"
                fp = {
                    "ta": ta_ok,
                    "fundamentals": fundamentals_ok,
                    "use_llm": config.USE_LLM,
                }
                positions[symbol] = OpenPosition(
                    symbol=symbol,
                    side=side,
                    entry_price=entry,
                    atr=atr,
                    size=size,
                    regime_at_entry=regime,
                    regime_confidence_at_entry=float(regime_confidence),
                    signal_branch=_signal_branch(regime),
                    profile_id=config.ACTIVE_STRATEGY_PROFILE_ID or "",
                    deployment_tier=config.DEPLOYMENT_TIER,
                    filters_passed=fp,
                    entry_order_id=str(entry_res.get("id") or ""),
                    entry_order_utc=datetime.now(timezone.utc).isoformat(),
                    entry_execution=str(entry_res.get("entry_execution") or "market"),
                    twap_client_algo_id=str(entry_res.get("twap_client_algo_id") or ""),
                    twap_algo_state=str(entry_res.get("twap_algo_state") or ""),
                )
                save_open_positions(positions)
                logger.info("Opened %s %s size=%s", symbol, side, size)
                notify(
                    notify_events.TRADE_OPENED,
                    symbol=symbol,
                    side=side,
                    size=size,
                    entry=entry,
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
                    size=size,
                    entry=entry,
                    regime_confidence=regime_confidence,
                    cooldown_bars_remaining=cooldown_bars_remaining.get(symbol, 0),
                )

            _push_trading_snapshot(
                symbol_views,
                risk,
                fund,
                restored_from_state=restored_from_state,
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

        time.sleep(config.POLL_SECONDS)

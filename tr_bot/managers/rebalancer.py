"""Portfolio holdings rebalancer: drift / structural-lag scan and trim-only execution."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tr_bot import config
from tr_bot.correlation import pearson_matrix, rolling_returns
from tr_bot.managers.fund import FundManager
from tr_bot.managers.portfolio import parse_portfolio_weights
from tr_bot.managers.security import safe_artifact_path
from tr_bot.notifications import events as notify_events
from tr_bot.notifications.facade import notify
from tr_bot.open_positions_state import load_open_positions

logger = logging.getLogger("tr_bot.rebalancer")

EXECUTION_CONTEXT_STRATEGY = "STRATEGY_SIGNAL"
EXECUTION_CONTEXT_REBALANCE_TRIM = "REBALANCE_TRIM"
EXIT_REQUESTS_FILENAME = "rebalancer/exit_requests.json"
LATEST_FILENAME = "rebalancer/latest.json"
STATE_FILENAME = "rebalancer/state.json"


def _root() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "rebalancer").mkdir(parents=True, exist_ok=True)
    return root


def _path(name: str) -> Path:
    return safe_artifact_path(_root(), name)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _max_drawdown(closes: pd.Series) -> float:
    if closes is None or len(closes) < 2:
        return 0.0
    c = closes.astype(float)
    peak = c.cummax()
    dd = (peak - c) / peak.replace(0, np.nan)
    v = float(dd.max()) if len(dd) else 0.0
    return v if v == v else 0.0


def build_snapshot(
    target_weights: dict[str, float] | None = None,
    *,
    fund: FundManager | None = None,
    holdings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map holdings vs target weights into a plan-ready snapshot."""
    tw = target_weights if target_weights is not None else parse_portfolio_weights()
    fm = fund or FundManager()
    raw = holdings if holdings is not None else fm.holdings_snapshot()
    total = float(raw.get("total_equity_usdt") or 0.0)
    legs_out: dict[str, dict[str, Any]] = {}
    for symbol, target_w in tw.items():
        leg = dict((raw.get("legs") or {}).get(symbol) or {})
        actual_w = float(leg.get("actual_weight") or 0.0)
        value = float(leg.get("value_usdt") or 0.0)
        if total > 0 and not leg.get("actual_weight") and value:
            actual_w = value / total
        drift = actual_w - float(target_w)
        legs_out[symbol] = {
            **leg,
            "symbol": symbol,
            "target_weight": float(target_w),
            "actual_weight": actual_w,
            "weight_drift": drift,
            "value_usdt": value,
            "base_total": float(leg.get("base_total") or 0.0),
            "base_free": float(leg.get("base_free") or 0.0),
            "last_price": float(leg.get("last_price") or 0.0),
        }
    quote_w = 0.0 if total <= 0 else float(raw.get("quote_total") or 0.0) / total
    return {
        "total_equity_usdt": total,
        "quote_asset": raw.get("quote_asset") or config.FUND_QUOTE_ASSET,
        "quote_free": float(raw.get("quote_free") or 0.0),
        "quote_total": float(raw.get("quote_total") or 0.0),
        "quote_weight": quote_w,
        "paper": bool(raw.get("paper")),
        "legs": legs_out,
        "target_weights": dict(tw),
    }


def drift_triggers(snapshot: dict[str, Any], *, band_pct: float | None = None) -> dict[str, bool]:
    band = float(config.REBALANCE_BAND_PCT if band_pct is None else band_pct)
    out: dict[str, bool] = {}
    for symbol, leg in (snapshot.get("legs") or {}).items():
        drift = abs(float(leg.get("weight_drift") or 0.0))
        out[symbol] = drift > band
    return out


def _load_daily_closes(symbols: list[str], *, lookback_days: int) -> pd.DataFrame:
    """Fetch daily closes; columns are symbols. Returns empty DataFrame on failure."""
    from tr_bot.market_data import fetch_ohlcv

    limit = max(int(lookback_days) + 5, 15)
    series: dict[str, pd.Series] = {}
    for symbol in symbols:
        try:
            df = fetch_ohlcv(symbol, "1d", limit=limit)
            if df is None or df.empty or "close" not in df.columns:
                continue
            closes = df["close"].astype(float)
            if "timestamp" in df.columns:
                closes.index = pd.to_datetime(df["timestamp"], utc=True)
            series[symbol] = closes
        except Exception as e:
            logger.warning("rebalancer OHLCV fetch failed for %s: %s", symbol, e)
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).dropna(how="all")


def structural_lag(
    symbols: list[str],
    *,
    price_history: pd.DataFrame | None = None,
    lookback_days: int | None = None,
    lag_threshold: float | None = None,
    corr_max: float | None = None,
    require_mdd_divergence: bool | None = None,
    benchmark: str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Relative underperformance vs equal-weight peers (excluding self) or a
    configured benchmark symbol, plus correlation breakdown / MDD divergence.
    """
    lb = int(config.LAG_LOOKBACK_DAYS if lookback_days is None else lookback_days)
    thr = float(
        config.LAG_RELATIVE_RETURN_THRESHOLD if lag_threshold is None else lag_threshold
    )
    cmax = float(config.LAG_CORR_BREAKDOWN_MAX if corr_max is None else corr_max)
    need_mdd = (
        config.LAG_REQUIRE_MDD_DIVERGENCE
        if require_mdd_divergence is None
        else bool(require_mdd_divergence)
    )
    bench_cfg = (
        (benchmark if benchmark is not None else config.REBALANCE_BENCHMARK) or ""
    ).strip()
    use_peer_basket = bench_cfg.upper() in ("", "EQUAL_WEIGHT_PEERS", "PEERS")

    fetch_syms = list(symbols)
    if not use_peer_basket and "/" in bench_cfg and bench_cfg not in fetch_syms:
        fetch_syms.append(bench_cfg)

    hist = (
        price_history
        if price_history is not None
        else _load_daily_closes(fetch_syms, lookback_days=lb)
    )
    out: dict[str, dict[str, Any]] = {}
    if hist is None or hist.empty or len(hist) < max(lb, 3):
        logger.warning("Insufficient price history for structural lag (need >= %s days)", lb)
        return out

    window = hist.tail(lb + 1)
    if len(window) < 2:
        return out

    rets = (window.iloc[-1] / window.iloc[0]) - 1.0
    ret_map = {
        s: rolling_returns(pd.DataFrame({"close": hist[s].dropna()}))
        for s in hist.columns
        if s in hist
    }
    matrix = pearson_matrix(ret_map) if len(ret_map) >= 2 else {}

    mdds = {s: _max_drawdown(hist[s].dropna().tail(lb + 1)) for s in hist.columns}

    for asset in symbols:
        if asset not in rets.index:
            continue
        peers = [s for s in symbols if s != asset and s in rets.index]
        if not peers and use_peer_basket:
            continue
        asset_ret = float(rets[asset])
        if use_peer_basket:
            if not peers:
                continue
            basket_ret = float(np.mean([float(rets[p]) for p in peers]))
            bench_label = "EQUAL_WEIGHT_PEERS"
        else:
            if bench_cfg not in rets.index:
                logger.warning("Benchmark %s missing from price history", bench_cfg)
                continue
            basket_ret = float(rets[bench_cfg])
            bench_label = bench_cfg
            # Still use portfolio peers for corr / MDD comparison when available.
            if not peers:
                peers = [bench_cfg] if bench_cfg != asset else []
        relative = asset_ret - basket_ret

        peer_corrs = []
        row = matrix.get(asset) or {}
        corr_peers = [p for p in peers if p != asset]
        for p in corr_peers:
            v = row.get(p)
            if v is not None and v == v:
                peer_corrs.append(float(v))
        latest_corr = float(np.mean(peer_corrs)) if peer_corrs else 1.0

        asset_mdd = float(mdds.get(asset) or 0.0)
        peer_mdd_med = (
            float(np.median([mdds[p] for p in corr_peers if p in mdds])) if corr_peers else 0.0
        )
        mdd_divergent = asset_mdd > peer_mdd_med + 0.02  # 2pp wider MDD than peers

        underperforms = relative < thr
        corr_break = latest_corr < cmax
        # Require underperformance vs peers/benchmark; plus MDD divergence and/or corr breakdown.
        if need_mdd:
            lag_trigger = underperforms and mdd_divergent
        else:
            lag_trigger = underperforms and corr_break

        out[asset] = {
            "cumulative_return": round(asset_ret, 6),
            "relative_vs_basket": round(relative, 6),
            "peer_basket_return": round(basket_ret, 6),
            "benchmark": bench_label,
            "latest_peer_correlation": round(latest_corr, 4),
            "asset_mdd": round(asset_mdd, 6),
            "peer_median_mdd": round(peer_mdd_med, 6),
            "mdd_divergent": bool(mdd_divergent),
            "corr_breakdown": bool(corr_break),
            "lag_trigger": bool(lag_trigger),
            "peers": corr_peers,
        }
    return out


def generate_plan(
    snapshot: dict[str, Any],
    lag_analysis: dict[str, dict[str, Any]] | None = None,
    *,
    band_pct: float | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """
    Combine drift and lag into trim-only actions.

    Lag never implies BUY. Overweight drift or structural lag → TRIM.
    """
    if not config.REBALANCER_ENABLED:
        return False, []

    band = float(config.REBALANCE_BAND_PCT if band_pct is None else band_pct)
    drifts = drift_triggers(snapshot, band_pct=band)
    lag = lag_analysis if lag_analysis is not None else {}
    total = float(snapshot.get("total_equity_usdt") or 0.0)
    plan: list[dict[str, Any]] = []

    for symbol, leg in (snapshot.get("legs") or {}).items():
        drift = float(leg.get("weight_drift") or 0.0)
        has_drift = bool(drifts.get(symbol))
        lag_info = lag.get(symbol) or {"lag_trigger": False}
        has_lag = bool(lag_info.get("lag_trigger"))

        overweight = drift > band
        # Two-stage execution intent: overweight band breach OR structural lag.
        should_trim = overweight or has_lag
        if not should_trim:
            continue

        # Trim delta: bring weight toward target (positive USDT notional to sell).
        trim_weight = max(drift, 0.0) if overweight else max(float(leg.get("actual_weight") or 0.0), 0.0)
        if has_lag and not overweight:
            # Flatten lagging sleeve toward zero contribution for defensive trim.
            trim_weight = float(leg.get("actual_weight") or 0.0)
        estimated_usdt = trim_weight * total

        plan.append(
            {
                "asset": symbol,
                "action": "TRIM",
                "current_weight": round(float(leg.get("actual_weight") or 0.0), 6),
                "target_weight": round(float(leg.get("target_weight") or 0.0), 6),
                "weight_delta": round(drift, 6),
                "estimated_usdt_impact": round(estimated_usdt, 4),
                "base_total": float(leg.get("base_total") or 0.0),
                "base_free": float(leg.get("base_free") or 0.0),
                "last_price": float(leg.get("last_price") or 0.0),
                "reasons": {
                    "band_drift_breached": bool(has_drift and overweight),
                    "structural_lag_detected": bool(has_lag),
                    "lag_metrics": lag_info,
                },
            }
        )

    return bool(plan), plan


def _load_state() -> dict[str, Any]:
    path = _path(STATE_FILENAME)
    if not path.is_file():
        return {"last_action_utc": None, "per_symbol": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"last_action_utc": None, "per_symbol": {}}
    except (OSError, json.JSONDecodeError, TypeError):
        return {"last_action_utc": None, "per_symbol": {}}


def _save_state(state: dict[str, Any]) -> None:
    path = _path(STATE_FILENAME)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _in_cooldown(symbol: str, state: dict[str, Any]) -> bool:
    hours = float(config.REBALANCE_COOLDOWN_HOURS)
    if hours <= 0:
        return False
    per = state.get("per_symbol") or {}
    last = (per.get(symbol) or {}).get("last_action_utc")
    if not last:
        return False
    try:
        ts = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        return age_h < hours
    except (TypeError, ValueError):
        return False


def load_exit_requests() -> list[dict[str, Any]]:
    path = _path(EXIT_REQUESTS_FILENAME)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            reqs = raw.get("requests") or []
        elif isinstance(raw, list):
            reqs = raw
        else:
            reqs = []
        return [r for r in reqs if isinstance(r, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def save_exit_requests(requests: list[dict[str, Any]]) -> None:
    path = _path(EXIT_REQUESTS_FILENAME)
    body = {"requests": requests, "updated_utc": _utc_now()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def enqueue_exit_request(symbol: str, *, reason: str = "rebalance_trim") -> dict[str, Any]:
    reqs = load_exit_requests()
    # Dedupe open requests for same symbol.
    reqs = [r for r in reqs if str(r.get("symbol")) != symbol]
    row = {
        "symbol": symbol,
        "reason": reason,
        "execution_context": EXECUTION_CONTEXT_REBALANCE_TRIM,
        "requested_utc": _utc_now(),
    }
    reqs.append(row)
    save_exit_requests(reqs)
    return row


def drain_exit_requests_for_symbols(done_symbols: set[str]) -> list[dict[str, Any]]:
    """Remove acknowledged symbols from the request file; return remaining."""
    reqs = load_exit_requests()
    remaining = [r for r in reqs if str(r.get("symbol")) not in done_symbols]
    save_exit_requests(remaining)
    return remaining


def scan_job(
    *,
    fund: FundManager | None = None,
    price_history: pd.DataFrame | None = None,
    notify_on_action: bool = True,
    force_execute_trims: bool | None = None,
) -> dict[str, Any]:
    """Phase A1: evaluate drift/lag, persist artifact, optionally notify.

    ``force_execute_trims=False`` records scan-only (agents path); never places trims.
    """
    fm = fund or FundManager()
    tw = parse_portfolio_weights()
    snapshot = build_snapshot(tw, fund=fm)
    symbols = list(tw.keys())
    lag = structural_lag(symbols, price_history=price_history)
    action_required, plan = generate_plan(snapshot, lag)

    if force_execute_trims is None:
        execute_trims = bool(config.REBALANCER_EXECUTE_TRIMS)
    else:
        execute_trims = bool(force_execute_trims)

    payload = {
        "utc": _utc_now(),
        "enabled": bool(config.REBALANCER_ENABLED),
        "execute_trims": execute_trims,
        "action_required": action_required,
        "band_pct": float(config.REBALANCE_BAND_PCT),
        "snapshot": {
            "total_equity_usdt": snapshot.get("total_equity_usdt"),
            "quote_free": snapshot.get("quote_free"),
            "quote_total": snapshot.get("quote_total"),
            "quote_weight": snapshot.get("quote_weight"),
            "paper": snapshot.get("paper"),
            "legs": snapshot.get("legs"),
        },
        "lag_analysis": lag,
        "plan": plan,
    }
    path = _path(LATEST_FILENAME)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)

    if action_required and notify_on_action and config.REBALANCER_ENABLED:
        notify(
            notify_events.REBALANCE_ALERT,
            action_required=True,
            n_actions=len(plan),
            symbols=[p["asset"] for p in plan],
        )
        logger.warning("Rebalancer plan: %s action(s) %s", len(plan), [p["asset"] for p in plan])
    else:
        logger.info("Rebalancer scan: action_required=%s plan_len=%s", action_required, len(plan))

    return payload


def _trim_size_base(item: dict[str, Any], *, total_equity: float) -> float:
    """Base units to sell for a TRIM plan item."""
    px = float(item.get("last_price") or 0.0)
    base_free = float(item.get("base_free") or 0.0)
    usdt = float(item.get("estimated_usdt_impact") or 0.0)
    cap = float(config.REBALANCE_MAX_TRIM_USDT or 0.0)
    if cap > 0:
        usdt = min(usdt, cap)
    if px <= 0:
        return 0.0
    size = usdt / px
    return max(0.0, min(size, base_free))


def trim_job(
    *,
    fund: FundManager | None = None,
    price_history: pd.DataFrame | None = None,
    execute: bool | None = None,
) -> dict[str, Any]:
    """
    Phase A2: scan then optionally execute trims.

    Execution requires REBALANCER_EXECUTE_TRIMS (and ``execute`` not False).
    Open positions → exit_requests file; idle free base → market/TWAP sell.
    """
    scan = scan_job(fund=fund, price_history=price_history, notify_on_action=True)
    do_exec = bool(config.REBALANCER_EXECUTE_TRIMS) if execute is None else bool(execute)
    result: dict[str, Any] = {
        "scan": scan,
        "executed": False,
        "halt_set": False,
        "halt_cleared": False,
        "actions": [],
        "skipped": [],
        "error": None,
    }

    if not config.REBALANCER_ENABLED:
        result["error"] = "rebalancer_disabled"
        return result
    if not scan.get("action_required"):
        result["error"] = "no_action_required"
        return result
    if not do_exec:
        result["error"] = "execute_trims_disabled"
        return result
    if not config.REBALANCER_EXECUTE_TRIMS:
        result["error"] = "REBALANCER_EXECUTE_TRIMS_false"
        return result

    fm = fund or FundManager()
    state = _load_state()
    open_pos = load_open_positions()
    halt_reason = config.REBALANCE_HALT_REASON

    # Do not overwrite a different operator halt.
    existing = fm.manual_halt_reason_text()
    if existing is not None and existing != halt_reason:
        result["error"] = f"halt_already_active:{existing}"
        return result

    fm.set_manual_halt(halt_reason)
    result["halt_set"] = True

    total = float((scan.get("snapshot") or {}).get("total_equity_usdt") or 0.0)
    try:
        for item in scan.get("plan") or []:
            symbol = str(item.get("asset") or "")
            if not symbol:
                continue
            if _in_cooldown(symbol, state):
                result["skipped"].append({"symbol": symbol, "reason": "cooldown"})
                continue

            if symbol in open_pos:
                req = enqueue_exit_request(symbol, reason="rebalance_trim")
                result["actions"].append(
                    {"symbol": symbol, "mode": "exit_request", "request": req}
                )
                per = state.setdefault("per_symbol", {})
                per[symbol] = {"last_action_utc": _utc_now(), "mode": "exit_request"}
                continue

            # Idle inventory trim (no open strategy position).
            size = _trim_size_base(item, total_equity=total)
            if size <= 0:
                result["skipped"].append({"symbol": symbol, "reason": "zero_size"})
                continue
            if config.PAPER_TRADING:
                # Paper has no idle exchange bags beyond open positions.
                result["skipped"].append({"symbol": symbol, "reason": "paper_no_idle_inventory"})
                continue

            from tr_bot.execution import execute_trade

            res = execute_trade(symbol, "sell", size)
            result["actions"].append(
                {
                    "symbol": symbol,
                    "mode": "idle_sell",
                    "size": size,
                    "result_status": res.get("status") if isinstance(res, dict) else None,
                }
            )
            per = state.setdefault("per_symbol", {})
            per[symbol] = {"last_action_utc": _utc_now(), "mode": "idle_sell", "size": size}

        state["last_action_utc"] = _utc_now()
        _save_state(state)
        result["executed"] = True
    except Exception as e:
        logger.exception("rebalancer trim_job failed: %s", e)
        result["error"] = str(e)
    finally:
        # Re-check bands; clear only our halt reason.
        try:
            post = build_snapshot(fund=fm)
            still = False
            band = float(config.REBALANCE_BAND_PCT)
            for leg in (post.get("legs") or {}).values():
                if float(leg.get("weight_drift") or 0.0) > band:
                    still = True
                    break
            # Always attempt selective clear after enqueue/sells so strategy can resume;
            # open-position exits continue via engine handoff while halt is cleared only
            # when no overweight drift remains OR we only enqueued requests (engine owns closes).
            only_requests = result["actions"] and all(
                a.get("mode") == "exit_request" for a in result["actions"]
            )
            if only_requests or not still:
                result["halt_cleared"] = fm.clear_manual_halt_if_reason(halt_reason)
            elif not result["actions"]:
                result["halt_cleared"] = fm.clear_manual_halt_if_reason(halt_reason)
        except Exception as e:
            logger.warning("rebalancer post-trim halt clear failed: %s", e)

    return result

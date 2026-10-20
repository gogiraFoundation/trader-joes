"""Read-only context bundles for supervisor / fund manager agents."""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.circuit_breaker import read_circuit_state_snapshot
from tr_bot.managers.fund import FundManager
from tr_bot.managers.portfolio import parse_portfolio_weights
from tr_bot.managers.security import safe_artifact_path
from tr_bot.ops_state import ops_view_for_api, read_trading_snapshot
from tr_bot.trade_ledger import read_recent_trades

logger = logging.getLogger("tr_bot.agents.context")


def _sanitized_trading_config() -> dict[str, Any]:
    return {
        "deployment_tier": config.DEPLOYMENT_TIER,
        "paper_trading": bool(config.PAPER_TRADING),
        "strategy_engine": config.STRATEGY_ENGINE,
        "exit_mode": getattr(config, "EXIT_MODE", None),
        "risk_per_trade": float(config.RISK_PER_TRADE),
        "max_trades_per_day": int(config.MAX_TRADES_PER_DAY),
        "max_drawdown_pct": float(config.MAX_DRAWDOWN_PCT),
        "circuit_max_consecutive_losses": int(config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES),
        "poll_seconds": int(config.POLL_SECONDS),
        "managers_enabled": bool(config.MANAGERS_ENABLED),
        "symbol": config.SYMBOL,
        "portfolio_mode": bool(config.PORTFOLIO_MODE),
    }


def _recent_trade_summary(limit: int = 20) -> dict[str, Any]:
    rows = read_recent_trades(limit=limit)
    wins = sum(1 for r in rows if str(r.get("outcome", "")).lower() == "win")
    losses = sum(1 for r in rows if str(r.get("outcome", "")).lower() == "loss")
    streak = 0
    for r in rows:
        if str(r.get("outcome", "")).lower() != "loss":
            break
        streak += 1
    slim = []
    for r in rows[:10]:
        slim.append(
            {
                "symbol": r.get("symbol"),
                "side": r.get("side"),
                "outcome": r.get("outcome"),
                "net_pnl_usdt": r.get("net_pnl_usdt"),
                "closed_utc": r.get("closed_utc") or r.get("exit_time_utc") or r.get("time"),
                "signal_branch": r.get("signal_branch"),
                "paper": r.get("paper"),
            }
        )
    return {
        "n": len(rows),
        "wins": wins,
        "losses": losses,
        "loss_streak": streak,
        "recent": slim,
    }


def _skip_reason_histogram(limit_lines: int = 200) -> dict[str, Any]:
    """Best-effort top skip/block reasons from decision audit JSONL."""
    path_s = (config.DECISION_AUDIT_JSONL_PATH or "").strip()
    if not path_s:
        # Common default used by the engine when env is empty — try artifacts path.
        root = Path(config.ARTIFACTS_DIR).resolve()
        candidate = root / "decision_audit.jsonl"
        path = candidate if candidate.is_file() else None
    else:
        path = Path(path_s)
        if not path.is_file():
            path = None
    if path is None:
        return {"available": False, "top": []}
    counts: Counter[str] = Counter()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit_lines:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            reason = row.get("reason") or row.get("blocked_gate") or row.get("action")
            if reason:
                counts[str(reason)] += 1
            # Per-symbol rows in trading snapshot style
            for key in ("symbols", "rows", "evaluations"):
                block = row.get(key)
                if isinstance(block, list):
                    for item in block:
                        if isinstance(item, dict):
                            r2 = item.get("reason") or item.get("blocked_gate") or item.get("action")
                            if r2:
                                counts[str(r2)] += 1
    except OSError as e:
        logger.warning("skip_reason_histogram read failed: %s", e)
        return {"available": False, "top": [], "error": str(e)}
    top = [{"reason": k, "count": v} for k, v in counts.most_common(12)]
    return {"available": True, "top": top, "lines_scanned": min(limit_lines, len(lines))}


def _fund_view() -> dict[str, Any]:
    fund = FundManager()
    st = fund.get_state()
    state_dict = None
    if st is not None:
        state_dict = {
            "last_nav": st.last_nav,
            "peak_nav": st.peak_nav,
            "last_update_utc": st.last_update_utc,
            "halted": st.halted,
            "halt_reason": st.halt_reason,
        }
        peak = float(st.peak_nav or 0)
        nav = float(st.last_nav or 0)
        dd = ((peak - nav) / peak) if peak > 0 else 0.0
    else:
        dd = 0.0
        try:
            nav = float(fund.estimate_equity_usdt())
        except Exception:
            nav = None
        state_dict = {"last_nav": nav, "peak_nav": None, "halted": False, "halt_reason": ""}
    return {
        "enabled": bool(config.MANAGERS_ENABLED),
        "halt_file": fund.manual_halt_file_active(),
        "halt_file_reason": fund.manual_halt_reason_text(),
        "entry_block_reason": fund.entry_block_reason(),
        "state": state_dict,
        "drawdown": dd,
        "max_drawdown_pct": float(config.MAX_DRAWDOWN_PCT),
    }


def _rebalancer_latest() -> dict[str, Any] | None:
    root = Path(config.ARTIFACTS_DIR).resolve()
    try:
        path = safe_artifact_path(root, "rebalancer/latest.json")
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            # Keep a compact view
            return {
                "ok": raw.get("ok"),
                "alert": raw.get("alert"),
                "actions": raw.get("actions"),
                "updated_utc": raw.get("updated_utc") or raw.get("ts"),
            }
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("rebalancer latest read failed: %s", e)
    return None


def build_ops_context() -> dict[str, Any]:
    circuit = read_circuit_state_snapshot()
    return {
        "role_context": "ops",
        "circuit": circuit,
        "fund": _fund_view(),
        "ops": ops_view_for_api(),
        "trading_snapshot": read_trading_snapshot(),
        "trades": _recent_trade_summary(),
        "skip_reasons": _skip_reason_histogram(),
        "config": _sanitized_trading_config(),
    }


def build_fund_context() -> dict[str, Any]:
    base = build_ops_context()
    base["role_context"] = "fund"
    try:
        weights = parse_portfolio_weights()
    except Exception as e:
        weights = {"error": str(e)}
    base["portfolio_weights"] = weights
    base["rebalancer"] = _rebalancer_latest()
    return base

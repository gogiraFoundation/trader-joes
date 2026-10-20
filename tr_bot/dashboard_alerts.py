"""Derive top-level alert entries from already-computed dashboard summary fields.

Pure functions over plain dicts (no filesystem/network access) so alert logic is
unit-testable in isolation from `dashboard_api.dashboard_summary()`.
"""

from __future__ import annotations

from typing import Any

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}

# Soft drawdown warning floor used when MAX_DRAWDOWN_PCT is not configured, or as an
# early warning before the hard threshold is breached.
_DEFAULT_DRAWDOWN_WARN_PCT = 0.05


def _detail_lines_text(lines: list[str]) -> str:
    return "\n".join([line for line in lines if line])


def _recent_loss_streak(pnl: dict[str, Any] | None) -> int:
    pnl = pnl or {}
    streak = 0
    for row in pnl.get("recent_trades") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("outcome", "")).lower() != "loss":
            break
        streak += 1
    return streak


def _circuit_alert(
    circuit: dict[str, Any] | None, pnl: dict[str, Any] | None
) -> dict[str, Any] | None:
    circuit = circuit or {}
    if circuit.get("tripped") is not True:
        return None
    n = circuit.get("consecutive_losses")
    m = circuit.get("max_consecutive_losses")
    updated = circuit.get("updated_utc") or circuit.get("last_update_utc")
    streak = _recent_loss_streak(pnl)
    return {
        "id": "circuit_tripped",
        "severity": SEVERITY_CRITICAL,
        "message": f"Circuit breaker tripped ({n}/{m} consecutive losses) \u2014 new entries paused.",
        "details": _detail_lines_text(
            [
                f"Consecutive losses: {n if n is not None else '—'} / {m if m is not None else '—'}",
                f"Recent contiguous loss streak from closed trades: {streak}" if streak else "",
                f"Updated: {updated}" if updated else "",
            ]
        ),
        "href": "/dashboard/risk.html",
    }


def _halt_alert(fund: dict[str, Any] | None) -> dict[str, Any] | None:
    fund = fund or {}
    if not fund.get("enabled"):
        return None
    state = fund.get("state")
    halted = isinstance(state, dict) and bool(state.get("halted"))
    halt_file = bool(fund.get("halt_file"))
    if not halted and not halt_file:
        return None
    reasons: list[str] = []
    if halted:
        reason = isinstance(state, dict) and state.get("halt_reason")
        reasons.append(str(reason) if reason else "fund halt active")
    if halt_file:
        reasons.append("manual halt file present")
    return {
        "id": "trading_halted",
        "severity": SEVERITY_CRITICAL,
        "message": "Trading halted: " + "; ".join(reasons) + ".",
        "details": _detail_lines_text(
            [
                "New entries are blocked while existing positions continue to be managed.",
                ("Fund halt reason: " + str(state.get("halt_reason"))) if halted and isinstance(state, dict) and state.get("halt_reason") else "",
                "Manual halt file is present." if halt_file else "",
            ]
        ),
        "href": "/dashboard/",
    }


def _nav_drawdown_alert(
    fund: dict[str, Any] | None, max_drawdown_pct: float | None
) -> dict[str, Any] | None:
    fund = fund or {}
    if not fund.get("enabled"):
        return None
    state = fund.get("state")
    if not isinstance(state, dict):
        return None
    try:
        peak = float(state.get("peak_nav") or 0)
        nav = float(state.get("last_nav") or 0)
    except (TypeError, ValueError):
        return None
    if peak <= 0:
        return None
    dd = (peak - nav) / peak
    if dd <= 0:
        return None

    threshold = float(max_drawdown_pct) if max_drawdown_pct is not None else None
    severe = threshold is not None and dd >= threshold
    warn = dd >= _DEFAULT_DRAWDOWN_WARN_PCT
    if not severe and not warn:
        return None

    pct_str = f"{dd * 100:.1f}%"
    limit_str = f" (limit {threshold * 100:.0f}%)" if threshold is not None else ""
    return {
        "id": "nav_drawdown",
        "severity": SEVERITY_CRITICAL if severe else SEVERITY_WARNING,
        "message": f"NAV drawdown {pct_str} vs peak{limit_str}.",
        "details": _detail_lines_text(
            [
                f"Current NAV: {nav:.4f}",
                f"Peak NAV: {peak:.4f}",
                f"Current drawdown: {pct_str}",
                f"Configured drawdown limit: {threshold * 100:.1f}%" if threshold is not None else "",
            ]
        ),
        "href": "/dashboard/",
    }


def _gate_alert(strategy_promotion: dict[str, Any] | None) -> dict[str, Any] | None:
    sp = strategy_promotion or {}
    ev = sp.get("last_evaluate") or {}
    if ev.get("passed") is not False:
        return None
    outcomes = ev.get("outcomes") or []
    failed = [
        o
        for o in outcomes
        if isinstance(o, dict) and o.get("passed") is False and o.get("name")
    ]
    detail = f" ({', '.join(str(o.get('name')) for o in failed)})" if failed else ""
    return {
        "id": "gates_failed",
        "severity": SEVERITY_WARNING,
        "message": f"Promotion gates failing{detail}.",
        "details": _detail_lines_text(
            [
                f"{o.get('name')}: {o.get('detail') or 'failed'}"
                for o in failed
            ]
        ),
        "href": "/dashboard/strategy.html",
    }


def _agents_critical_alert(agents: dict[str, Any] | None) -> dict[str, Any] | None:
    agents = agents or {}
    parts: list[str] = []
    for key in ("supervisor_last", "fund_manager_last"):
        block = agents.get(key)
        if not isinstance(block, dict):
            continue
        if str(block.get("severity", "")).lower() != SEVERITY_CRITICAL:
            continue
        role = block.get("role") or key
        summary = block.get("summary") or "critical agent brief"
        parts.append(f"{role}: {summary}")
    if not parts:
        return None
    return {
        "id": "agents_critical",
        "severity": SEVERITY_CRITICAL,
        "message": "Agent brief critical — " + "; ".join(parts)[:240],
        "details": _detail_lines_text(parts),
        "href": "/dashboard/",
    }


def _heartbeat_alert(ops: dict[str, Any] | None) -> dict[str, Any] | None:
    ops = ops or {}
    if ops.get("heartbeat_ok") is not False:
        return None
    if not ops.get("file_exists"):
        # Bot has simply never reported in yet; not a regression worth alerting on.
        return None
    age = ops.get("heartbeat_stale_seconds")
    age_str = f"{round(age)}s" if isinstance(age, (int, float)) else "unknown"
    threshold = ops.get("heartbeat_threshold_seconds")
    return {
        "id": "heartbeat_stale",
        "severity": SEVERITY_WARNING,
        "message": f"Bot heartbeat stale (age {age_str}).",
        "details": _detail_lines_text(
            [
                f"Heartbeat age: {age_str}",
                f"Threshold: {round(threshold)}s" if isinstance(threshold, (int, float)) else "",
                f"Last loop: {ops.get('last_loop_utc')}" if ops.get("last_loop_utc") else "",
            ]
        ),
        "href": "/dashboard/",
    }


def build_alerts(
    *,
    circuit: dict[str, Any] | None,
    fund: dict[str, Any] | None,
    ops: dict[str, Any] | None,
    pnl: dict[str, Any] | None,
    strategy_promotion: dict[str, Any] | None,
    max_drawdown_pct: float | None = None,
    agents: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Return an ordered (critical first) list of alert entries for the dashboard's
    top alert bar. Each entry has ``id``, ``severity``, ``message``, ``href``.
    """
    candidates = [
        _circuit_alert(circuit, pnl),
        _halt_alert(fund),
        _nav_drawdown_alert(fund, max_drawdown_pct),
        _agents_critical_alert(agents),
        _gate_alert(strategy_promotion),
        _heartbeat_alert(ops),
    ]
    alerts = [a for a in candidates if a is not None]
    alerts.sort(key=lambda a: _SEVERITY_ORDER.get(a.get("severity"), 3))
    return alerts

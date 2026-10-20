"""Configurable promotion gates for ledger + equity + optional backtest reference."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.backtest.last_run import read_backtest_last_run
from tr_bot.ops_state import read_equity_history
from tr_bot.strategy import deployment_io as dep
from tr_bot.promotion import log as promotion_log
from tr_bot.trade_ledger import read_recent_trades


@dataclass
class GateOutcome:
    name: str
    passed: bool
    detail: str


@dataclass
class GateReport:
    outcomes: list[GateOutcome] = field(default_factory=list)
    input_hash: str = ""
    benchmark_note: str | None = None

    @property
    def all_passed(self) -> bool:
        return bool(self.outcomes) and all(o.passed for o in self.outcomes)


def _ledger_rows_for_profile(
    rows: list[dict[str, Any]],
    profile_id: str | None,
    *,
    profile_hash: str | None = None,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """
    Filter closed-trade rows for promotion attribution.

    When ``profile_hash`` is supplied, rows must match both ``profile_id`` and an
    exact non-empty ``profile_hash``. Missing hashes never qualify in that mode.
    """
    if not profile_id:
        return rows
    want_hash = (profile_hash or "").strip() or None
    matched: list[dict[str, Any]] = []
    legacy = 0
    for r in rows:
        rp = r.get("profile_id")
        rh = r.get("profile_hash")
        if rp == profile_id:
            if want_hash is not None:
                if not isinstance(rh, str) or not rh.strip() or rh.strip() != want_hash:
                    continue
            matched.append(r)
        elif rp is None or rp == "":
            legacy += 1
    if matched:
        return matched
    if strict:
        return []
    # Legacy fallback only when no hash was requested (otherwise attribution is exact).
    if want_hash is None and legacy:
        return rows
    return []


def gate_strict_attribution(
    rows_all: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    profile_id: str | None,
    strict: bool,
) -> GateOutcome:
    if not strict or not profile_id:
        return GateOutcome(name="strict_attribution", passed=True, detail="disabled")
    if not rows:
        return GateOutcome(
            name="strict_attribution",
            passed=False,
            detail="no_attributed_trades_for_profile",
        )
    unattributed = sum(
        1
        for r in rows_all
        if r.get("profile_id") in (None, "") and r.get("outcome") in ("win", "loss")
    )
    return GateOutcome(
        name="strict_attribution",
        passed=True,
        detail=f"attributed={len(rows)} legacy_untagged={unattributed}",
    )


def _parse_utc_date(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def gate_min_trades(rows: list[dict[str, Any]], min_n: int) -> GateOutcome:
    n = len([r for r in rows if r.get("outcome") in ("win", "loss")])
    ok = n >= min_n
    return GateOutcome(
        name="min_trades",
        passed=ok,
        detail=f"closed={n} need>={min_n}",
    )


def gate_min_calendar_days(rows: list[dict[str, Any]], min_days: int) -> GateOutcome:
    dates: list[datetime] = []
    for r in rows:
        u = r.get("utc")
        if not u:
            continue
        d = _parse_utc_date(str(u))
        if d:
            dates.append(d)
    if not dates:
        return GateOutcome(
            name="min_calendar_days",
            passed=False,
            detail="no trade timestamps",
        )
    span = (max(dates) - min(dates)).days + 1
    ok = span >= min_days
    return GateOutcome(
        name="min_calendar_days",
        passed=ok,
        detail=f"span_days={span} need>={min_days}",
    )


def gate_equity_drawdown(max_dd_pct: float) -> GateOutcome:
    """Max drawdown from equity_history points (NAV series)."""
    eq = read_equity_history()

    points = eq.get("points") or []
    vals: list[float] = []
    for p in points:
        try:
            vals.append(float(p.get("nav") or p.get("equity") or 0))
        except (TypeError, ValueError):
            continue
    if not vals:
        return GateOutcome(
            name="equity_max_drawdown",
            passed=False,
            detail="no_equity_points_or_nav_values",
        )
    if len(vals) == 1:
        # One point (e.g. fund_state fallback): no path to measure drawdown; treat as 0.
        max_dd = 0.0
        return GateOutcome(
            name="equity_max_drawdown",
            passed=max_dd <= max_dd_pct,
            detail=f"single_point max_dd={max_dd:.4f} cap={max_dd_pct:.4f}",
        )
    peak = vals[0]
    max_dd = 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)
    ok = max_dd <= max_dd_pct
    return GateOutcome(
        name="equity_max_drawdown",
        passed=ok,
        detail=f"max_dd={max_dd:.4f} cap={max_dd_pct:.4f}",
    )


def gate_profit_factor(rows: list[dict[str, Any]], min_pf: float | None) -> GateOutcome:
    if min_pf is None:
        return GateOutcome(
            name="min_profit_factor",
            passed=True,
            detail="disabled",
        )
    gross_win = 0.0
    gross_loss = 0.0
    for r in rows:
        if r.get("outcome") != "win":
            continue
        try:
            gross_win += max(0.0, float(r.get("net_pnl_usdt", 0) or 0))
        except (TypeError, ValueError):
            pass
    for r in rows:
        if r.get("outcome") != "loss":
            continue
        try:
            gross_loss += max(0.0, -float(r.get("net_pnl_usdt", 0) or 0))
        except (TypeError, ValueError):
            pass
    if gross_loss <= 0:
        pf = float("inf") if gross_win > 0 else 0.0
    else:
        pf = gross_win / gross_loss
    ok = pf >= min_pf
    return GateOutcome(
        name="min_profit_factor",
        passed=ok,
        detail=f"pf={pf:.4f} need>={min_pf}",
    )


def gate_cooldown(artifacts_root: Path) -> GateOutcome:
    if config.PROMOTION_BREAK_COOLDOWN:
        return GateOutcome(name="cooldown", passed=True, detail="break_cooldown")
    rows = promotion_log.read_promotion_log_tail(artifacts_root, limit=500)
    cool_h = float(config.PROMOTION_COOLDOWN_HOURS)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cool_h)
    for row in reversed(rows):
        if row.get("action") != "promote" or row.get("passed") is not True:
            continue
        ts = row.get("utc") or row.get("timestamp")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if t > cutoff:
            return GateOutcome(
                name="cooldown",
                passed=False,
                detail=f"last_promote={ts} within {cool_h}h",
            )
    return GateOutcome(name="cooldown", passed=True, detail="ok")


def gate_backtest_reference() -> GateOutcome:
    """Soft gate: surface backtest summary when present."""
    bt = read_backtest_last_run()
    if not bt:
        return GateOutcome(
            name="backtest_last_run",
            passed=True,
            detail="missing (optional)",
        )
    tr = bt.get("total_return_pct")
    dd = bt.get("max_drawdown_pct")
    bh = bt.get("benchmark_buy_hold")
    note = f"bt_return={tr} bt_dd={dd}"
    if isinstance(bh, dict):
        note += f" bh_return={bh.get('total_return_pct')}"
    return GateOutcome(
        name="backtest_last_run",
        passed=True,
        detail=note,
    )


def promotions_frozen(artifacts_root: Path) -> bool:
    if config.PROMOTION_FROZEN:
        return True
    return dep.freeze_promotions_path(artifacts_root).is_file()


def build_input_hash(
    profile_id: str | None, tier: str, rows_sample: list[dict[str, Any]]
) -> str:
    payload = {
        "profile_id": profile_id,
        "tier": tier,
        "rows": rows_sample[-200:],
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def gate_strategy_engine_hint() -> GateOutcome:
    """Surface active STRATEGY_ENGINE (informational; always passes)."""
    eng = str(getattr(config, "STRATEGY_ENGINE", "legacy") or "legacy")
    return GateOutcome(
        name="strategy_engine",
        passed=True,
        detail=f"STRATEGY_ENGINE={eng}",
    )


def run_gates(
    artifacts_root: Path,
    *,
    profile_id: str | None,
    tier: str,
    profile_hash: str | None = None,
) -> GateReport:
    rows_all = read_recent_trades(limit=50_000)
    strict = bool(getattr(config, "PROMOTION_STRICT_LEDGER_FILTER", False))
    rows = _ledger_rows_for_profile(
        rows_all,
        profile_id,
        profile_hash=profile_hash,
        strict=strict,
    )
    report = GateReport()
    report.outcomes.append(gate_strict_attribution(rows_all, rows, profile_id=profile_id, strict=strict))
    report.outcomes.append(
        gate_min_trades(rows, int(config.PROMOTION_MIN_TRADES)),
    )
    report.outcomes.append(
        gate_min_calendar_days(rows, int(config.PROMOTION_MIN_CALENDAR_DAYS)),
    )
    report.outcomes.append(gate_equity_drawdown(float(config.PROMOTION_MAX_EQUITY_DRAWDOWN_PCT)))
    report.outcomes.append(gate_profit_factor(rows, config.PROMOTION_MIN_PROFIT_FACTOR))
    report.outcomes.append(gate_cooldown(artifacts_root))
    bt_out = gate_backtest_reference()
    report.outcomes.append(bt_out)
    report.outcomes.append(gate_strategy_engine_hint())

    floors_passed = all(
        o.passed
        for o in report.outcomes
        if o.name in ("min_trades", "min_calendar_days", "strict_attribution")
    )
    if bool(getattr(config, "PROMOTION_BOOTSTRAP_ENABLED", False)) and floors_passed:
        from tr_bot.promotion.stats import (
            gate_bootstrap_expectancy,
            gate_bootstrap_profit_factor,
        )

        report.outcomes.append(
            gate_bootstrap_expectancy(
                rows,
                min_lower_bound=getattr(config, "PROMOTION_MIN_EXPECTANCY_USDT", None),
                block_size=int(getattr(config, "PROMOTION_BOOTSTRAP_BLOCK_SIZE", 5)),
                n_resamples=int(getattr(config, "PROMOTION_BOOTSTRAP_RESAMPLES", 2000)),
                alpha=float(getattr(config, "PROMOTION_BOOTSTRAP_ALPHA", 0.05)),
            )
        )
        report.outcomes.append(
            gate_bootstrap_profit_factor(
                rows,
                min_pf_lower=getattr(config, "PROMOTION_BOOTSTRAP_MIN_PF_LOWER", None),
                block_size=int(getattr(config, "PROMOTION_BOOTSTRAP_BLOCK_SIZE", 5)),
                n_resamples=int(getattr(config, "PROMOTION_BOOTSTRAP_RESAMPLES", 2000)),
                alpha=float(getattr(config, "PROMOTION_BOOTSTRAP_ALPHA", 0.05)),
            )
        )
    elif bool(getattr(config, "PROMOTION_BOOTSTRAP_ENABLED", False)):
        report.outcomes.append(
            GateOutcome(
                name="bootstrap_expectancy",
                passed=False,
                detail="skipped_floors_not_met",
            )
        )

    report.benchmark_note = bt_out.detail
    report.input_hash = build_input_hash(profile_id, tier, rows)
    return report

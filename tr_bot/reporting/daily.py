"""UTC daily deterministic report: JSON + Markdown under artifacts/reports/."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.circuit_breaker import read_circuit_state_snapshot
from tr_bot.strategy.effective_params import effective_params_from_config_and_profile
from tr_bot.strategy.no_trade_adaptation import build_reason_histogram
from tr_bot.trade_ledger import LEDGER_FILENAME

DAILY_REPORT_SCHEMA_VERSION = 1

DAILY_REPORT_CONFIG_KEYS: tuple[str, ...] = (
    "SIGNAL_RSI_OVERSOLD",
    "SIGNAL_RSI_OVERBOUGHT",
    "REGIME_MIN_CONFIDENCE",
    "USE_SIGNAL_STRENGTH_FILTER",
    "SIGNAL_MIN_RSI_DISTANCE",
    "SIGNAL_MIN_ADX_MARGIN",
    "ADX_MAX_FOR_MEAN_REVERSION",
    "ADX_MIN_FOR_TREND",
    "TREND_STRATEGY_ENABLED",
    "STRATEGY_ENGINE",
    "SMC_MIN_TRADE_SCORE",
    "EXIT_MODE",
    "BACKTEST_FEE_BPS",
    "LIVE_FEE_BPS",
    "MAX_TRADES_PER_DAY",
    "LOSS_COOLDOWN_BARS",
    "CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES",
)


def _artifacts_root() -> Path:
    return Path(config.ARTIFACTS_DIR).resolve()


def _read_smc_go_no_go(root: Path) -> dict[str, Any] | None:
    p = root / "research" / "smc_go_no_go.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def resolve_decision_audit_jsonl(artifacts_root: Path | None = None) -> Path | None:
    root = artifacts_root or _artifacts_root()
    raw = (config.ADAPTATION_AUDIT_JSONL_PATH or "").strip()
    if not raw:
        raw = (config.DECISION_AUDIT_JSONL_PATH or "").strip()
    if not raw:
        cand = root / "decision_audit.jsonl"
        return cand if cand.is_file() else None
    p = Path(raw)
    if not p.is_absolute():
        p = root / raw
    return p if p.is_file() else None


def _parse_iso_utc(ts: str) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_day_window(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def load_audit_records_in_window(
    path: Path,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        u = rec.get("utc")
        if not u:
            continue
        dt = _parse_iso_utc(str(u))
        if dt is None or not (start <= dt < end):
            continue
        out.append(rec)
    return out


def load_ledger_rows_in_window(
    path: Path,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                u = row.get("utc")
                if not u:
                    continue
                dt = _parse_iso_utc(str(u))
                if dt is None or not (start <= dt < end):
                    continue
                out.append(row)
    except OSError:
        return out
    return out


def _flatten_audit_symbol_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in records:
        loop = rec.get("loop")
        utc = rec.get("utc")
        by_sym = rec.get("by_symbol")
        if not isinstance(by_sym, dict):
            continue
        for sym, payload in by_sym.items():
            if not isinstance(payload, dict):
                continue
            r = dict(payload)
            r["symbol"] = sym
            r["_audit_loop"] = loop
            r["_audit_utc"] = utc
            rows.append(r)
    return rows


def _counter_sorted(c: Counter[str]) -> dict[str, int]:
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def _read_backtest_last_run(root: Path) -> dict[str, Any] | None:
    p = root / "training" / "backtest_last_run.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def _read_runtime_metrics(root: Path) -> dict[str, Any] | None:
    """Optional structured counters (OHLCV retries, etc.); absent until instrumentation exists."""
    p = root / "runtime_metrics.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def build_daily_report_payload(
    day: date,
    *,
    artifacts_root: Path | None = None,
    include_preflight: bool = False,
) -> dict[str, Any]:
    root = artifacts_root or _artifacts_root()
    start, end = utc_day_window(day)
    day_s = day.isoformat()
    generated = datetime.now(timezone.utc).isoformat()

    audit_path = resolve_decision_audit_jsonl(root)
    audit_records: list[dict[str, Any]] = []
    if audit_path is not None:
        audit_records = load_audit_records_in_window(audit_path, start, end)

    ledger_path = root / LEDGER_FILENAME
    trades_all = load_ledger_rows_in_window(ledger_path, start, end) if ledger_path.is_file() else []
    max_rows = max(0, int(getattr(config, "DAILY_REPORT_MAX_TRADE_ROWS", 10_000)))
    trades_truncated = False
    sum_net = sum(float(t.get("net_pnl_usdt", 0) or 0) for t in trades_all)
    sum_fee = sum(float(t.get("fee_usdt", 0) or 0) for t in trades_all)
    trades = trades_all
    if max_rows and len(trades_all) > max_rows:
        trades_truncated = True
        trades = trades_all[:max_rows]

    flat = _flatten_audit_symbol_rows(audit_records)
    action_c: Counter[str] = Counter()
    decision_c: Counter[str] = Counter()
    regime_c: Counter[str] = Counter()
    confs: list[float] = []
    peak_daily = 0
    risk_reasons: Counter[str] = Counter()

    for row in flat:
        if row.get("skipped"):
            reason = str(row.get("reason", ""))
            if "circuit" in reason.lower():
                risk_reasons["circuit_skip"] += 1
            action_c["skipped"] += 1
            continue
        if row.get("holding"):
            action_c["holding"] += 1
            continue
        act = str(row.get("action", "unknown")).lower()
        action_c[act] += 1
        dec = str(row.get("decision", "unknown")).lower()
        decision_c[dec] += 1
        rg = str(row.get("regime", "unknown")).lower()
        regime_c[rg] += 1
        try:
            rc = float(row.get("regime_confidence", 0) or 0)
            confs.append(rc)
        except (TypeError, ValueError):
            pass
        try:
            dt = int(row.get("daily_trades", 0) or 0)
            peak_daily = max(peak_daily, dt)
        except (TypeError, ValueError):
            pass
        rsn = str(row.get("reason", ""))
        if rsn == "cooldown_active":
            risk_reasons["cooldown_active"] += 1
        elif rsn == "risk_limit_reached":
            risk_reasons["risk_limit_reached"] += 1

    hist = build_reason_histogram(audit_records)
    regime_eval_n = sum(regime_c.values())
    range_n = regime_c.get("range", 0)
    trend_n = regime_c.get("trend", 0)
    regime_mix_pct: dict[str, float | None] = {
        "range": (100.0 * range_n / regime_eval_n) if regime_eval_n else None,
        "trend": (100.0 * trend_n / regime_eval_n) if regime_eval_n else None,
    }
    avg_conf = float(statistics.mean(confs)) if confs else None
    med_conf = float(statistics.median(confs)) if confs else None

    eff = effective_params_from_config_and_profile(root)
    config_snapshot = {k: eff.get(k) for k in DAILY_REPORT_CONFIG_KEYS if k in eff or hasattr(config, k)}
    for k in DAILY_REPORT_CONFIG_KEYS:
        if k not in config_snapshot and hasattr(config, k):
            config_snapshot[k] = getattr(config, k)

    bt = _read_backtest_last_run(root)
    gate_block: dict[str, Any] | None = None
    if bt and bt.get("ok"):
        from tr_bot.strategy.no_trade_adaptation import evaluate_adaptation_backtest_gate

        gate_block = evaluate_adaptation_backtest_gate(bt)

    symbols_seen = sorted({str(t.get("symbol", "")) for t in trades if t.get("symbol")})
    if not symbols_seen:
        symbols_seen = sorted({str(r.get("symbol", "")) for r in flat if r.get("symbol")})
    symbols_out = [s for s in symbols_seen if s]

    infra: dict[str, Any] = {
        "runtime_metrics": _read_runtime_metrics(root),
        "ohlcv_retry_counters": None,
        "instrumentation_note": "ohlcv_retry_counters reserved for future runtime_metrics.json writers",
        "circuit": read_circuit_state_snapshot(),
    }
    if include_preflight:
        from tr_bot.startup.preflight import run_preflight_checks

        infra["preflight"] = run_preflight_checks()
    else:
        infra["preflight"] = None

    meta = {
        "day_utc": day_s,
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "symbols": symbols_out or ([config.SYMBOL] if getattr(config, "SYMBOL", None) else []),
        "timeframe": config.TIMEFRAME,
        "deployment_tier": config.DEPLOYMENT_TIER,
        "paper_trading": config.PAPER_TRADING,
        "profile_id": config.ACTIVE_STRATEGY_PROFILE_ID or None,
        "generated_utc": generated,
        "decision_audit_path": str(audit_path) if audit_path else None,
        "trade_ledger_path": str(ledger_path) if ledger_path.is_file() else None,
    }

    return {
        "schema_version": DAILY_REPORT_SCHEMA_VERSION,
        "meta": meta,
        "trades": {
            "count": len(trades_all),
            "rows": trades,
            "truncated": trades_truncated,
            "max_rows": max_rows,
            "sum_net_pnl_usdt": sum_net,
            "sum_fee_usdt": sum_fee,
        },
        "loops": {
            "audit_parent_records": len(audit_records),
            "flattened_symbol_rows": len(flat),
            "action_counts": _counter_sorted(action_c),
            "decision_counts": _counter_sorted(decision_c),
            "no_trade_reason_histogram": _counter_sorted(hist),
            "risk_signal_counts": _counter_sorted(risk_reasons),
        },
        "regime": {
            "regime_counts": _counter_sorted(regime_c),
            "mix_pct_range_trend": regime_mix_pct,
            "avg_regime_confidence": avg_conf,
            "median_regime_confidence": med_conf,
            "evaluation_rows": regime_eval_n,
        },
        "risk": {
            "max_trades_per_day_config": int(config.MAX_TRADES_PER_DAY),
            "loss_cooldown_bars_config": int(config.LOSS_COOLDOWN_BARS),
            "circuit_breaker_max_consecutive_losses": int(config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES),
            "peak_daily_trades_observed": peak_daily,
        },
        "infra": infra,
        "backtest_last_run": bt,
        "backtest_adaptation_gate": gate_block,
        "config_snapshot": config_snapshot,
        "strategy_engine": {
            "strategy_engine": str(getattr(config, "STRATEGY_ENGINE", "legacy") or "legacy"),
            "exit_mode": str(getattr(config, "EXIT_MODE", "atr") or "atr"),
            "smc_go_no_go": _read_smc_go_no_go(root),
        },
    }


def render_daily_report_markdown(payload: dict[str, Any]) -> str:
    meta = payload.get("meta") or {}
    lines = [
        "# Daily trading report",
        "",
        f"- **Day (UTC):** {meta.get('day_utc')}",
        f"- **Window:** {meta.get('window_start_utc')} → {meta.get('window_end_utc')}",
        f"- **Symbols:** {', '.join(meta.get('symbols') or []) or '—'}",
        f"- **Timeframe:** {meta.get('timeframe')}",
        f"- **Deployment:** {meta.get('deployment_tier')} (paper={meta.get('paper_trading')})",
        f"- **Profile:** {meta.get('profile_id') or '—'}",
        "",
        "## Trades",
    ]
    tr = payload.get("trades") or {}
    lines.append(f"- Closed round-trips: **{tr.get('count', 0)}**")
    if tr.get("truncated"):
        lines.append(f"- (rows list truncated to {tr.get('max_rows')} for file size; sums are full-day)")
    lines.append(f"- Sum net PnL (USDT): **{tr.get('sum_net_pnl_usdt', 0):.6f}**")
    lines.append(f"- Sum fees (USDT): **{tr.get('sum_fee_usdt', 0):.6f}**")
    lines.extend(["", "## Loop / decisions"])
    lp = payload.get("loops") or {}
    lines.append(f"- Audit loops in window: {lp.get('audit_parent_records', 0)}")
    top_reasons = lp.get("no_trade_reason_histogram") or {}
    top5 = list(top_reasons.items())[:5]
    lines.append("- Top no_trade reasons:")
    if not top5:
        lines.append("  - (none)")
    else:
        for k, v in top5:
            lines.append(f"  - {k}: {v}")
    lines.extend(["", "## Strategy engine / SMC"])
    eng = payload.get("strategy_engine") or {}
    lines.append(f"- STRATEGY_ENGINE: **{eng.get('strategy_engine') or meta.get('strategy_engine') or '—'}**")
    go = eng.get("smc_go_no_go") or {}
    if go:
        lines.append(f"- SMC go/no-go: **{go.get('passed')}** — {'; '.join(go.get('reasons') or []) or '—'}")
    else:
        lines.append("- SMC go/no-go: (no artifact)")
    lines.extend(["", "## Regime"])
    rg = payload.get("regime") or {}
    mix = rg.get("mix_pct_range_trend") or {}
    lines.append(
        f"- Range / trend mix (%): range={mix.get('range')}, trend={mix.get('trend')} "
        f"(n={rg.get('evaluation_rows', 0)})"
    )
    lines.append(f"- Avg regime confidence: **{rg.get('avg_regime_confidence')}**")
    lines.extend(["", "## Risk / limits"])
    rk = payload.get("risk") or {}
    lines.append(
        f"- MAX_TRADES_PER_DAY={rk.get('max_trades_per_day_config')} "
        f"peak daily_trades in audit={rk.get('peak_daily_trades_observed')}"
    )
    lines.extend(["", "## Backtest (last run artifact)"])
    gate = payload.get("backtest_adaptation_gate")
    if gate:
        lines.append(f"- Adaptation-style gate passed: **{gate.get('passed')}**")
        lines.append(f"- Observed: {gate.get('observed')}")
    else:
        lines.append("- No gate evaluation (missing or invalid backtest payload)")
    lines.extend(["", "## Config snapshot (subset)"])
    snap = payload.get("config_snapshot") or {}
    for k in sorted(snap.keys()):
        lines.append(f"- `{k}`: {snap[k]}")
    lines.append("")
    return "\n".join(lines)


def run_daily_report_job(
    day: date,
    *,
    artifacts_root: Path | None = None,
    include_preflight: bool = False,
) -> dict[str, Any]:
    root = artifacts_root or _artifacts_root()
    out_dir = root / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    day_s = day.isoformat()
    payload = build_daily_report_payload(day, artifacts_root=root, include_preflight=include_preflight)
    json_path = out_dir / f"{day_s}.json"
    md_path = out_dir / f"{day_s}.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_daily_report_markdown(payload), encoding="utf-8")
    return {"ok": True, "json_path": str(json_path), "md_path": str(md_path), "day_utc": day_s}

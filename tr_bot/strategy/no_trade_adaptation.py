"""
No-trade streak tracking + adaptive relaxation of strategy gates (meta-tuning).

Uses decision_audit JSONL for blocker histogram, optional walk-forward backtest gate
before writing a new strategy profile. See docs/operations.md.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.startup.backtest_gate_thresholds import (
    DEFAULT_MAX_DRAWDOWN_PCT,
    DEFAULT_MIN_RETURN_PCT,
    DEFAULT_MIN_TRADES,
)
from tr_bot.strategy import deployment_io as dep
from tr_bot.strategy.effective_params import effective_params_from_config_and_profile
from tr_bot.strategy.profile import OVERLAY_PARAM_KEYS
from tr_bot.promotion import log as promotion_log

logger = logging.getLogger("tr_bot.strategy.adaptation")

STATE_SCHEMA_VERSION = 1
STATE_REL = "adaptation/no_trade_state.json"
PROPOSALS_SUBDIR = "adaptation/proposals"


def _artifacts_root() -> Path:
    return Path(config.ARTIFACTS_DIR).resolve()


def state_path() -> Path:
    p = _artifacts_root() / STATE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_state() -> dict[str, Any]:
    p = state_path()
    if not p.is_file():
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "consecutive_loops_no_open": 0,
            "last_loop_idx": 0,
            "last_processed_loop_idx": 0,
            "last_adaptation_utc": None,
            "last_adaptation_profile_id": None,
        }
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "consecutive_loops_no_open": 0,
            "last_loop_idx": 0,
            "last_processed_loop_idx": 0,
            "last_adaptation_utc": None,
            "last_adaptation_profile_id": None,
        }
    raw.setdefault("schema_version", STATE_SCHEMA_VERSION)
    raw.setdefault("consecutive_loops_no_open", 0)
    raw.setdefault("last_loop_idx", 0)
    raw.setdefault("last_processed_loop_idx", 0)
    raw.setdefault("last_adaptation_utc", None)
    raw.setdefault("last_adaptation_profile_id", None)
    return raw


def _save_state(data: dict[str, Any]) -> None:
    dep.atomic_write_json(state_path(), data)


def record_loop_without_open(symbol_views: dict[str, Any], loop_idx: int) -> None:
    """Call from engine each loop when adaptation is enabled."""
    if not config.ADAPTATION_ENABLED:
        return
    opened = False
    for v in symbol_views.values():
        if not isinstance(v, dict):
            continue
        if v.get("skipped"):
            continue
        if str(v.get("action", "")).lower() == "opened":
            opened = True
            break
    st = _load_state()
    last_proc = int(st.get("last_processed_loop_idx") or 0)
    if loop_idx <= last_proc:
        return
    if opened:
        st["consecutive_loops_no_open"] = 0
    else:
        st["consecutive_loops_no_open"] = int(st.get("consecutive_loops_no_open") or 0) + 1
    st["last_loop_idx"] = int(loop_idx)
    st["last_processed_loop_idx"] = int(loop_idx)
    st["schema_version"] = STATE_SCHEMA_VERSION
    _save_state(st)


def _audit_path_resolved() -> Path | None:
    raw = (config.ADAPTATION_AUDIT_JSONL_PATH or "").strip()
    if not raw:
        raw = (config.DECISION_AUDIT_JSONL_PATH or "").strip()
    if not raw:
        cand = _artifacts_root() / "decision_audit.jsonl"
        if cand.is_file():
            return cand
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = _artifacts_root() / raw
    return p if p.is_file() else None


def _tail_jsonl_records(path: Path, max_lines: int) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = lines[-max(1, max_lines) :]
    out: list[dict[str, Any]] = []
    for ln in tail:
        try:
            obj = json.loads(ln)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def build_reason_histogram(records: list[dict[str, Any]]) -> Counter[str]:
    c: Counter[str] = Counter()
    for rec in records:
        by_sym = rec.get("by_symbol")
        if not isinstance(by_sym, dict):
            continue
        for _sym, row in by_sym.items():
            if not isinstance(row, dict):
                continue
            if row.get("skipped"):
                continue
            act = str(row.get("action", "")).lower()
            if act != "no_trade":
                continue
            r = row.get("reason")
            if r is None or r == "":
                c["(empty)"] += 1
            else:
                c[str(r)] += 1
    return c


def _dominant_reason(hist: Counter[str]) -> str | None:
    if not hist:
        return None
    # Prefer specific blockers over generic hold when hold is not overwhelming.
    total = sum(hist.values())
    items = hist.most_common()
    top_reason, top_n = items[0]
    if top_reason == "hold" and len(items) > 1:
        second_reason, second_n = items[1]
        if second_n >= max(3, int(0.15 * total)):
            return second_reason
    return top_reason


def propose_param_delta(dominant_reason: str | None) -> dict[str, Any]:
    """
    Map dominant no_trade reason to a single-step param change.
    Returns {} if no change.
    """
    if not dominant_reason:
        return {}
    dr = dominant_reason
    delta: dict[str, Any] = {}

    if dr == "low_regime_confidence":
        cur = float(getattr(config, "REGIME_MIN_CONFIDENCE", 0.55))
        step = float(config.ADAPTATION_REGIME_CONFIDENCE_STEP)
        floor = float(config.ADAPTATION_REGIME_CONFIDENCE_FLOOR)
        new_v = max(floor, cur - step)
        if new_v < cur - 1e-9:
            delta["REGIME_MIN_CONFIDENCE"] = round(new_v, 4)
        return delta

    if dr.startswith("weak_signal:"):
        sub = dr.split(":", 1)[-1]
        if sub == "rsi_distance_too_small":
            cur = float(getattr(config, "SIGNAL_MIN_RSI_DISTANCE", 3.0))
            step = float(config.ADAPTATION_RSI_DISTANCE_STEP)
            floor = float(config.ADAPTATION_RSI_DISTANCE_FLOOR)
            new_v = max(floor, cur - step)
            if new_v < cur - 1e-9:
                delta["SIGNAL_MIN_RSI_DISTANCE"] = round(new_v, 4)
            return delta
        if sub in (
            "adx_margin_failed",
            "adx_too_high_for_range",
            "adx_too_low_for_trend",
        ):
            cur = float(getattr(config, "SIGNAL_MIN_ADX_MARGIN", 2.0))
            step = float(config.ADAPTATION_ADX_MARGIN_STEP)
            floor = float(config.ADAPTATION_ADX_MARGIN_FLOOR)
            new_v = max(floor, cur - step)
            if new_v < cur - 1e-9:
                delta["SIGNAL_MIN_ADX_MARGIN"] = round(new_v, 4)
            return delta
        if config.ADAPTATION_RELAX_RSI_ON_WEAK_SIGNAL:
            lo = float(getattr(config, "SIGNAL_RSI_OVERSOLD", 30.0))
            hi = float(getattr(config, "SIGNAL_RSI_OVERBOUGHT", 70.0))
            step = float(config.ADAPTATION_RSI_BAND_STEP)
            cap_lo = float(config.ADAPTATION_RSI_OVERSOLD_CEILING)
            floor_hi = float(config.ADAPTATION_RSI_OVERBOUGHT_FLOOR)
            new_lo = min(cap_lo, lo + step)
            new_hi = max(floor_hi, hi - step)
            if new_lo > lo + 1e-9:
                delta["SIGNAL_RSI_OVERSOLD"] = round(new_lo, 2)
            if new_hi < hi - 1e-9:
                delta["SIGNAL_RSI_OVERBOUGHT"] = round(new_hi, 2)
            return delta
        return {}

    if (
        dr == "hold"
        and config.ADAPTATION_ENABLE_TREND_ON_HOLD
        and not bool(getattr(config, "TREND_STRATEGY_ENABLED", False))
    ):
        delta["TREND_STRATEGY_ENABLED"] = True
        return delta

    return {}


def _merge_params(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    out.update(delta)
    return out


def overlay_params_subset(params: dict[str, Any]) -> dict[str, Any]:
    """Full strategy profile params table for subprocess / JSON."""
    out: dict[str, Any] = {}
    for k in OVERLAY_PARAM_KEYS:
        if k in params:
            out[k] = params[k]
        elif hasattr(config, k):
            out[k] = getattr(config, k)
    return out


def _float_env_adapt(name: str, startup_fallback: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        raw = os.environ.get(startup_fallback)
    if raw is None or str(raw).strip() == "":
        return default
    return float(raw)


def _int_env_adapt(name: str, startup_fallback: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        raw = os.environ.get(startup_fallback)
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def evaluate_adaptation_backtest_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    """Same shape as startup evaluate_backtest_gate; uses ADAPT_* with STARTUP_* fallback."""
    min_ret = _float_env_adapt(
        "ADAPT_BACKTEST_MIN_RETURN_PCT", "STARTUP_BACKTEST_MIN_RETURN_PCT", DEFAULT_MIN_RETURN_PCT
    )
    max_dd = _float_env_adapt(
        "ADAPT_BACKTEST_MAX_DD_PCT", "STARTUP_BACKTEST_MAX_DD_PCT", DEFAULT_MAX_DRAWDOWN_PCT
    )
    min_trades = _int_env_adapt(
        "ADAPT_BACKTEST_MIN_TRADES", "STARTUP_BACKTEST_MIN_TRADES", DEFAULT_MIN_TRADES
    )

    ret = float(metrics.get("total_return_pct", 0) or 0)
    dd = float(metrics.get("max_drawdown_pct", 0) or 0)
    trades = int(metrics.get("trades", 0) or 0)

    checks = {
        "min_return": ret >= min_ret,
        "max_drawdown": dd <= max_dd,
        "min_trades": trades >= min_trades,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": {"min_return_pct": min_ret, "max_drawdown_pct": max_dd, "min_trades": min_trades},
        "observed": {"total_return_pct": ret, "max_drawdown_pct": dd, "trades": trades},
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _subprocess_env_layer(art_dir: str) -> dict[str, str]:
    return {
        "ARTIFACTS_DIR": art_dir,
        "BACKTEST_RUN_ABLATION_MATRIX": "false",
        "BACKTEST_AUDIT_SAMPLE_STRIDE": "0",
        "NOTIFY_ENABLED": "false",
        "MANAGERS_ENABLED": "false",
    }


def _serialize_param_env_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def run_isolated_backtest_for_packaged_profile(
    profile_id: str,
    profile_doc: dict[str, Any],
    timeout_s: int,
) -> tuple[dict[str, Any] | None, str | None]:
    td = tempfile.mkdtemp(prefix="tr_bot_adapt_")
    try:
        root = Path(td)
        dep.deployment_dir(root)
        dep.atomic_write_json(dep.pointer_path(root, "paper"), profile_doc["pointer"])
        dep.atomic_write_json(
            dep.strategy_profile_path(root, profile_id),
            profile_doc["profile_body"],
        )
        env = {k: v for k, v in os.environ.items() if v is not None}
        env = {**env, **_subprocess_env_layer(td)}
        env.setdefault("SYMBOL", config.SYMBOL)
        env.setdefault("TIMEFRAME", config.TIMEFRAME)
        env.setdefault("DEPLOYMENT_TIER", "paper")
        env.setdefault("PAPER_TRADING", "true")
        py = sys.executable
        cp = subprocess.run(
            [py, "-m", "tr_bot.managers", "backtest-run"],
            cwd=str(_repo_root()),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        out_path = root / "training" / "backtest_last_run.json"
        if not out_path.is_file():
            tail = ((cp.stderr or "") + "\n" + (cp.stdout or "")).strip()[-600:]
            return None, f"no backtest json rc={cp.returncode}: {tail}"
        try:
            return json.loads(out_path.read_text(encoding="utf-8")), None
        except (json.JSONDecodeError, OSError) as e:
            return None, str(e)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _write_profile_and_pointer(
    profile_id: str,
    params: dict[str, Any],
    *,
    source: str,
    proposal_only: bool,
    hypothesis: str | None = None,
    promotion_action: str = "no_trade_adaptation",
) -> dict[str, Any]:
    root = _artifacts_root()
    now = datetime.now(timezone.utc).isoformat()
    hyp = hypothesis if hypothesis is not None else f"No-trade adaptation ({source})"
    body = {
        "profile_id": profile_id,
        "schema_version": 1,
        "created_at": now,
        "parent_profile_id": config.ACTIVE_STRATEGY_PROFILE_ID or None,
        "hypothesis": hyp,
        "params": overlay_params_subset(params),
    }
    ptr = {"profile_id": profile_id, "activated_at": now, "source": source}
    prof_path = dep.strategy_profile_path(root, profile_id)
    if proposal_only:
        prop_dir = root / PROPOSALS_SUBDIR
        prop_dir.mkdir(parents=True, exist_ok=True)
        prop_file = prop_dir / f"{profile_id}.json"
        dep.atomic_write_json(prop_file, {"pointer": ptr, "profile": body})
        return {"ok": True, "proposal_path": str(prop_file), "profile_id": profile_id}
    dep.atomic_write_json(prof_path, body)
    dep.atomic_write_json(dep.pointer_path(root, "paper"), ptr)
    promotion_log.append_promotion_event(
        root,
        {
            "action": promotion_action,
            "utc": now,
            "profile_id": profile_id,
            "to_tier": "paper",
            "passed": True,
            "source": source,
        },
    )
    return {"ok": True, "profile_path": str(prof_path), "profile_id": profile_id}


def run_adaptation_job() -> dict[str, Any]:
    """
    Scheduler / CLI entry: may emit new profile after streak + audit + backtest gate.
    """
    if not config.ADAPTATION_ENABLED:
        return {"ok": True, "skipped": "ADAPTATION_ENABLED=false"}

    root = _artifacts_root()
    st = _load_state()
    streak = int(st.get("consecutive_loops_no_open") or 0)
    if streak < int(config.ADAPTATION_MIN_LOOPS_NO_OPEN):
        return {"ok": True, "skipped": "streak_below_min", "streak": streak}

    last_adapt = st.get("last_adaptation_utc")
    if last_adapt:
        try:
            prev = datetime.fromisoformat(str(last_adapt).replace("Z", "+00:00"))
            if prev.tzinfo is None:
                prev = prev.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - prev).total_seconds()
            if age < float(config.ADAPTATION_COOLDOWN_SECONDS):
                return {"ok": True, "skipped": "cooldown", "seconds_remaining": int(config.ADAPTATION_COOLDOWN_SECONDS - age)}
        except (TypeError, ValueError):
            pass

    ap = _audit_path_resolved()
    if ap is None:
        logger.warning("No-trade adaptation: no decision audit JSONL found; skip")
        return {"ok": False, "error": "no_audit_file"}

    records = _tail_jsonl_records(ap, int(config.ADAPTATION_AUDIT_TAIL_LINES))
    hist = build_reason_histogram(records)
    dominant = _dominant_reason(hist)
    delta = propose_param_delta(dominant)
    if not delta:
        return {
            "ok": True,
            "skipped": "no_param_delta",
            "dominant_reason": dominant,
            "histogram": dict(hist.most_common(12)),
        }

    base_params = effective_params_from_config_and_profile(_artifacts_root())
    candidate_params = overlay_params_subset(_merge_params(base_params, delta))
    profile_id = f"adapt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    now = datetime.now(timezone.utc).isoformat()

    out = apply_overlay_candidate_after_backtest(
        profile_id,
        candidate_params,
        source="no_trade_adaptation",
        extra_meta={"dominant_reason": dominant, "delta": delta, "histogram_tail": dict(hist.most_common(8))},
        hypothesis="No-trade adaptation candidate",
        promotion_action="no_trade_adaptation",
    )
    if not out.get("ok"):
        return out

    st["last_adaptation_utc"] = now
    st["last_adaptation_profile_id"] = profile_id
    st["consecutive_loops_no_open"] = 0
    _save_state(st)

    logger.info(
        "No-trade adaptation applied dominant=%s delta=%s proposal_only=%s profile_id=%s",
        dominant,
        delta,
        out.get("proposal_only"),
        profile_id,
    )
    return {
        "ok": True,
        "profile_id": profile_id,
        "dominant_reason": dominant,
        "delta": delta,
        "histogram_tail": dict(hist.most_common(8)),
        "gate": out.get("gate"),
        "proposal_only": out.get("proposal_only"),
        **{k: v for k, v in out.items() if k not in ("ok", "gate", "proposal_only", "extra_meta", "backtest")},
    }


def apply_overlay_candidate_after_backtest(
    profile_id: str,
    candidate_params: dict[str, Any],
    *,
    source: str,
    timeout_s: int | None = None,
    proposal_only: bool | None = None,
    extra_meta: dict[str, Any] | None = None,
    hypothesis: str | None = None,
    promotion_action: str | None = None,
) -> dict[str, Any]:
    """
    Package overlay params as a profile, run isolated walk-forward backtest, evaluate gate,
    then write proposal file and/or update paper pointer (same semantics as adaptation).
    """
    hyp = hypothesis if hypothesis is not None else f"Overlay candidate ({source})"
    profile_body = {
        "profile_id": profile_id,
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parent_profile_id": config.ACTIVE_STRATEGY_PROFILE_ID or None,
        "hypothesis": hyp,
        "params": dict(overlay_params_subset(candidate_params)),
    }
    now = datetime.now(timezone.utc).isoformat()
    ptr_body = {"profile_id": profile_id, "activated_at": now, "source": source}
    packaged = {"pointer": ptr_body, "profile_body": profile_body}
    tmo = int(timeout_s if timeout_s is not None else config.ADAPTATION_BACKTEST_TIMEOUT_SECONDS)

    bt_out, err = run_isolated_backtest_for_packaged_profile(profile_id, packaged, tmo)
    if bt_out is None:
        logger.error("%s: backtest failed: %s", source, err)
        return {"ok": False, "error": err or "backtest_failed", "extra_meta": extra_meta or {}}

    if not bt_out.get("ok"):
        return {"ok": False, "error": "backtest_ok_false", "backtest": bt_out, "extra_meta": extra_meta or {}}

    gate = evaluate_adaptation_backtest_gate(bt_out)
    if not gate["passed"]:
        logger.info("%s: rejected by backtest gate: %s", source, gate)
        return {
            "ok": False,
            "skipped": "backtest_gate_failed",
            "gate": gate,
            "extra_meta": extra_meta or {},
        }

    if proposal_only is None:
        tier = str(config.DEPLOYMENT_TIER)
        proposal_only = tier != "paper" or not config.ADAPTATION_AUTO_APPLY_PAPER

    pa = promotion_action or ("strategy_proposal" if source == "llm_proposal" else "no_trade_adaptation")
    result = _write_profile_and_pointer(
        profile_id,
        candidate_params,
        source=source,
        proposal_only=proposal_only,
        hypothesis=hyp,
        promotion_action=pa,
    )
    return {
        "ok": True,
        "profile_id": profile_id,
        "gate": gate,
        "proposal_only": proposal_only,
        "backtest": bt_out,
        "extra_meta": extra_meta or {},
        **result,
    }

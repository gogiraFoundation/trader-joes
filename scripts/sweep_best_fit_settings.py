#!/usr/bin/env python3
"""
Overnight / batch sweep of strategy env knobs via walk-forward backtest subprocesses.

Usage (from repo root):
  .venv/bin/python scripts/sweep_best_fit_settings.py [--limit N] [--seed S] [--apply] [--emit-profile]

Writes:
  artifacts/sweep_best_fit/results.csv
  artifacts/sweep_best_fit/winner.json
  artifacts/sweep_best_fit/stability.json  (top-K re-run on shorter history)

With --apply, patches only listed tuning keys in .env and .env.example (no secret keys).
With --emit-profile, also writes artifacts/strategy_profiles/sweep_<batch>.json for paper soak (no auto-promote).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from itertools import product
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "artifacts" / "sweep_best_fit"

# When executed as `python scripts/...`, sys.path[0] is `scripts/` instead of repo root.
# Ensure local package imports (`tr_bot.*`) work reliably for --emit-profile path.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Tunable grid (deep but bounded; use --limit for random subsample)
TIMEFRAMES = ["5m", "15m", "1h"]
TREND_ENABLED = ["false", "true"]  # TREND_STRATEGY_ENABLED
SKIP_TA = ["false", "true"]  # BACKTEST_SKIP_TA_FILTER (mirrors TA ablation)
SENTIMENTS = ["neutral", "bullish", "bearish"]
RSI_PAIRS = [("25", "70"), ("30", "70"), ("35", "65")]
ADX_MAX_MR = ["28", "35", "40"]
ADX_MIN_TR = ["20", "24"]
MAX_TRADES_DAY = ["2", "3", "5"]
MIN_TRADES_PER_SYMBOL = 1
DEFAULT_TARGET_TRADES = 8
DEFAULT_MAX_DD_PREFERRED = 30.0
DEFAULT_PENALTY_TRADES_WEIGHT = 0.25
DEFAULT_PENALTY_SYMBOL_TRADES_WEIGHT = 0.20
DEFAULT_PENALTY_DD_WEIGHT = 0.25
DEFAULT_PENALTY_NEGATIVE_RETURN_WEIGHT = 0.15
DEFAULT_PENALTY_NEGATIVE_EXPECTANCY_WEIGHT = 0.15
# Match tr_bot.backtest.runner.PROFIT_FACTOR_NO_LOSS_CAP for aggregated runs
PROFIT_FACTOR_NO_LOSS_CAP = 999.0

# Keys written on --apply (never secrets)
APPLY_KEYS = [
    "TIMEFRAME",
    "TREND_STRATEGY_ENABLED",
    "BACKTEST_SKIP_TA_FILTER",
    "USE_TA_FILTER",
    "BACKTEST_SENTIMENT_MODE",
    "SIGNAL_RSI_OVERSOLD",
    "SIGNAL_RSI_OVERBOUGHT",
    "ADX_MAX_FOR_MEAN_REVERSION",
    "ADX_MIN_FOR_TREND",
    "MAX_TRADES_PER_DAY",
    "BACKTEST_RUN_ABLATION_MATRIX",
    "BACKTEST_ENFORCE_MAX_TRADES_PER_DAY",
]


def _python_has_module(py_exe: str, module_name: str) -> bool:
    cp = subprocess.run(
        [py_exe, "-c", f"import {module_name}"],
        capture_output=True,
        text=True,
    )
    return cp.returncode == 0


def _select_subprocess_python() -> str:
    """
    Prefer current interpreter, but fallback to repo .venv python when
    required module(s) are missing in current interpreter.
    """
    cur = sys.executable
    if _python_has_module(cur, "defusedxml"):
        return cur
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_py.is_file() and _python_has_module(str(venv_py), "defusedxml"):
        return str(venv_py)
    return cur


SWEEP_PYTHON = _select_subprocess_python()


def _iter_combos() -> Iterator[dict[str, str]]:
    for tf, tr, sk, sent, (rso, rob), amr, amt, mtd in product(
        TIMEFRAMES,
        TREND_ENABLED,
        SKIP_TA,
        SENTIMENTS,
        RSI_PAIRS,
        ADX_MAX_MR,
        ADX_MIN_TR,
        MAX_TRADES_DAY,
    ):
        use_ta = "false" if sk.lower() == "true" else "true"
        yield {
            "TIMEFRAME": tf,
            "TREND_STRATEGY_ENABLED": tr,
            "BACKTEST_SKIP_TA_FILTER": sk,
            "USE_TA_FILTER": use_ta,
            "BACKTEST_SENTIMENT_MODE": sent,
            "SIGNAL_RSI_OVERSOLD": rso,
            "SIGNAL_RSI_OVERBOUGHT": rob,
            "ADX_MAX_FOR_MEAN_REVERSION": amr,
            "ADX_MIN_FOR_TREND": amt,
            "MAX_TRADES_PER_DAY": mtd,
        }


def _build_base_env() -> dict[str, str]:
    file_vals = dotenv_values(REPO_ROOT / ".env")
    base: dict[str, str] = {}
    for k, v in file_vals.items():
        if v is not None and str(v).strip() != "":
            base[k] = str(v)
    merged = {**os.environ, **base}
    merged["PYTHONPATH"] = str(REPO_ROOT)
    return merged


def _sweep_env_layer(art_dir: str) -> dict[str, str]:
    return {
        "ARTIFACTS_DIR": art_dir,
        "BACKTEST_RUN_ABLATION_MATRIX": "false",
        "BACKTEST_ENFORCE_MAX_TRADES_PER_DAY": "true",
        "BACKTEST_AUDIT_SAMPLE_STRIDE": "0",
        "BACKTEST_INCLUDE_BUY_HOLD": "false",
        "NOTIFY_ENABLED": "true",
        "SYMBOL": "BTC/USDT",
    }


def _safe_ratio(n: float, d: float) -> float:
    if d <= 0:
        return 0.0
    return max(0.0, n / d)


def _resolve_profit_factor(m: dict[str, Any]) -> float:
    """Numeric PF for scoring: same semantics as runner when gross_loss_abs == 0."""
    raw = m.get("profit_factor")
    if raw is not None:
        return float(raw)
    trades = int(m.get("trades") or 0)
    if trades <= 0:
        return 0.0
    gp = float(m.get("gross_profit") or 0.0)
    gl = float(m.get("gross_loss_abs") or 0.0)
    if gl > 0:
        return gp / gl
    return float(PROFIT_FACTOR_NO_LOSS_CAP) if gp > 0 else 0.0


def _score_details(
    m: dict[str, Any],
    *,
    min_trades_per_symbol: int = MIN_TRADES_PER_SYMBOL,
    target_trades: int = DEFAULT_TARGET_TRADES,
    max_dd_preferred: float = DEFAULT_MAX_DD_PREFERRED,
    penalty_trades_weight: float = DEFAULT_PENALTY_TRADES_WEIGHT,
    penalty_symbol_trades_weight: float = DEFAULT_PENALTY_SYMBOL_TRADES_WEIGHT,
    penalty_dd_weight: float = DEFAULT_PENALTY_DD_WEIGHT,
    penalty_negative_return_weight: float = DEFAULT_PENALTY_NEGATIVE_RETURN_WEIGHT,
    penalty_negative_expectancy_weight: float = DEFAULT_PENALTY_NEGATIVE_EXPECTANCY_WEIGHT,
    strict_gates: bool = False,
) -> dict[str, float | None]:
    empty = {
        "rank_score": None,
        "base_score": None,
        "trade_penalty": None,
        "symbol_trade_penalty": None,
        "dd_penalty": None,
        "stability_penalty": None,
        "negative_return_penalty": None,
        "negative_expectancy_penalty": None,
    }
    if not m.get("ok"):
        return empty
    trades = int(m.get("trades") or 0)
    min_trades_symbol = int(m.get("min_trades_per_symbol") or 0)
    wins = int(m.get("wins") or 0)
    wr = (wins / trades) if trades > 0 else 0.0
    trp = float(m.get("total_return_pct") or 0.0)
    dd = float(m.get("max_drawdown_pct") or 0.0)
    pf = _resolve_profit_factor(m)
    exp = float(m.get("expectancy_per_trade") or 0.0)
    stability_penalty = float(m.get("stability_variance_penalty") or 0.0)

    if strict_gates:
        if trades < int(target_trades):
            return empty
        if min_trades_symbol < int(min_trades_per_symbol):
            return empty
        if dd > float(max_dd_preferred):
            return empty

    base_score = (
        0.30 * (trp / 100.0)
        + 0.25 * wr
        - 0.20 * (dd / 100.0)
        + 0.15 * min(pf, 3.0) / 3.0
        + 0.10 * exp
    )
    trade_deficit = max(0.0, float(target_trades) - float(trades))
    trade_penalty = float(penalty_trades_weight) * _safe_ratio(trade_deficit, float(target_trades))

    symbol_trade_deficit = max(0.0, float(min_trades_per_symbol) - float(min_trades_symbol))
    symbol_trade_penalty = float(penalty_symbol_trades_weight) * _safe_ratio(
        symbol_trade_deficit, float(max(1, min_trades_per_symbol))
    )

    dd_excess = max(0.0, dd - float(max_dd_preferred))
    dd_penalty = float(penalty_dd_weight) * _safe_ratio(dd_excess, float(max(1.0, max_dd_preferred)))

    # Soft penalties when headline return or expectancy is negative (reduces "high WR, still losing" rankings)
    negative_return_penalty = float(penalty_negative_return_weight) * max(0.0, -trp / 100.0)
    negative_expectancy_penalty = float(penalty_negative_expectancy_weight) * max(0.0, -exp)

    rank_score = (
        base_score
        - trade_penalty
        - symbol_trade_penalty
        - dd_penalty
        - (0.10 * stability_penalty)
        - negative_return_penalty
        - negative_expectancy_penalty
    )
    return {
        "rank_score": rank_score,
        "base_score": base_score,
        "trade_penalty": trade_penalty,
        "symbol_trade_penalty": symbol_trade_penalty,
        "dd_penalty": dd_penalty,
        "stability_penalty": stability_penalty,
        "negative_return_penalty": negative_return_penalty,
        "negative_expectancy_penalty": negative_expectancy_penalty,
    }


def _run_backtest(env: dict[str, str], bars: str | None = None) -> dict[str, Any]:
    td = tempfile.mkdtemp(prefix="tr_bot_sweep_")
    try:
        run_env = {**env, **_sweep_env_layer(td)}
        if bars is not None:
            run_env["BACKTEST_BARS"] = bars
        cp = subprocess.run(
            [SWEEP_PYTHON, "-m", "tr_bot.managers", "backtest-run"],
            cwd=str(REPO_ROOT),
            env=run_env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        path = Path(td) / "training" / "backtest_last_run.json"
        if not path.is_file():
            err_tail = ((cp.stderr or "") + "\n" + (cp.stdout or "")).strip()[-500:]
            return {
                "ok": False,
                "error": f"no backtest json (rc={cp.returncode}, py={SWEEP_PYTHON}): {err_tail}",
            }
        return json.loads(path.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _sweep_symbols(env: dict[str, str]) -> list[str]:
    raw = (env.get("PORTFOLIO_WEIGHTS") or "").strip()
    out: list[str] = []
    if raw:
        for part in raw.split(","):
            tok = part.strip()
            if not tok or "=" not in tok:
                continue
            sym = tok.split("=", 1)[0].strip()
            if "/" in sym and sym not in out:
                out.append(sym)
    if not out:
        sym = (env.get("SYMBOL") or "BTC/USDT").strip()
        out = [sym]
    return out


def _aggregate_runs(symbol_runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    oks = [r for r in symbol_runs.values() if r.get("ok")]
    if not oks:
        first_sym = None
        first_err = "unknown"
        for sym, run in symbol_runs.items():
            if not run.get("ok"):
                first_sym = sym
                first_err = str(run.get("error", "unknown"))
                break
        return {
            "ok": False,
            "error": (
                f"all symbol runs failed ({len(symbol_runs)}/{len(symbol_runs)}). "
                f"first={first_sym}: {first_err[:300]}"
            ),
            "by_symbol": symbol_runs,
        }
    total_trades = sum(int(r.get("trades") or 0) for r in oks)
    total_wins = sum(int(r.get("wins") or 0) for r in oks)
    total_losses = sum(int(r.get("losses") or 0) for r in oks)
    avg_return = sum(float(r.get("total_return_pct") or 0.0) for r in oks) / len(oks)
    max_dd = max(float(r.get("max_drawdown_pct") or 0.0) for r in oks)
    total_gp = sum(float(r.get("gross_profit") or 0.0) for r in oks)
    total_gl = sum(float(r.get("gross_loss_abs") or 0.0) for r in oks)
    net = sum(float(r.get("net_pnl") or 0.0) for r in oks)
    if total_gl > 0:
        pf = total_gp / total_gl
    elif total_trades > 0:
        pf = float(PROFIT_FACTOR_NO_LOSS_CAP) if total_gp > 0 else 0.0
    else:
        pf = None
    expectancy = (net / total_trades) if total_trades > 0 else 0.0
    min_trades = min(int(r.get("trades") or 0) for r in oks)
    return {
        "ok": True,
        "trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "total_return_pct": round(avg_return, 6),
        "max_drawdown_pct": round(max_dd, 6),
        "gross_profit": round(total_gp, 6),
        "gross_loss_abs": round(total_gl, 6),
        "profit_factor": round(float(pf), 6) if pf is not None else None,
        "expectancy_per_trade": round(expectancy, 6),
        "min_trades_per_symbol": min_trades,
        "symbols_tested": len(oks),
        "by_symbol": symbol_runs,
    }


def _run_multi_symbol_backtest(env: dict[str, str], bars: str | None = None) -> dict[str, Any]:
    runs: dict[str, dict[str, Any]] = {}
    for sym in _sweep_symbols(env):
        sym_env = {**env, "SYMBOL": sym}
        runs[sym] = _run_backtest(sym_env, bars=bars)
    return _aggregate_runs(runs)


def emit_strategy_profile(repo_root: Path, env_updates: dict[str, str], batch_id: str) -> Path:
    """Write artifacts/strategy_profiles/sweep_<batch_id>.json for paper soak (no auto-promote)."""
    from datetime import datetime, timezone

    from tr_bot.strategy.deployment_io import atomic_write_json, strategy_profile_path
    from tr_bot.strategy.profile import OVERLAY_PARAM_KEYS, _coerce

    profile_id = f"sweep_{batch_id}"
    params: dict[str, Any] = {}
    for k, v in env_updates.items():
        if k not in OVERLAY_PARAM_KEYS:
            continue
        try:
            params[k] = _coerce(k, str(v))
        except (KeyError, TypeError, ValueError):
            continue
    doc = {
        "profile_id": profile_id,
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parent_profile_id": None,
        "hypothesis": "Emitted from scripts/sweep_best_fit_settings.py (bounded search)",
        "search_batch_id": batch_id,
        "params": params,
    }
    artifacts = repo_root / "artifacts"
    path = strategy_profile_path(artifacts, profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, doc)
    return path


def _apply_patch(env_path: Path, updates: dict[str, str]) -> None:
    if not env_path.is_file():
        raise FileNotFoundError(env_path)
    lines = env_path.read_text(encoding="utf-8").splitlines()
    keys_left = set(updates.keys())
    applied_once: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" not in line:
            new_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            if key in applied_once:
                continue
            new_lines.append(f"{key}={updates[key]}")
            keys_left.discard(key)
            applied_once.add(key)
        else:
            new_lines.append(line)
    for k in sorted(keys_left):
        new_lines.append(f"{k}={updates[k]}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep backtest env knobs for best-fit settings.")
    ap.add_argument("--limit", type=int, default=0, help="Random subsample size (0 = full grid)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stability-top", type=int, default=5)
    ap.add_argument("--stability-bars", type=str, default="1000")
    ap.add_argument("--min-trades-per-symbol", type=int, default=MIN_TRADES_PER_SYMBOL)
    ap.add_argument("--target-trades", type=int, default=DEFAULT_TARGET_TRADES)
    ap.add_argument("--max-dd-preferred", type=float, default=DEFAULT_MAX_DD_PREFERRED)
    ap.add_argument("--penalty-trades-weight", type=float, default=DEFAULT_PENALTY_TRADES_WEIGHT)
    ap.add_argument("--penalty-symbol-trades-weight", type=float, default=DEFAULT_PENALTY_SYMBOL_TRADES_WEIGHT)
    ap.add_argument("--penalty-dd-weight", type=float, default=DEFAULT_PENALTY_DD_WEIGHT)
    ap.add_argument(
        "--penalty-negative-return-weight",
        type=float,
        default=DEFAULT_PENALTY_NEGATIVE_RETURN_WEIGHT,
        help="Extra penalty scale for negative total_return_pct (soft).",
    )
    ap.add_argument(
        "--penalty-negative-expectancy-weight",
        type=float,
        default=DEFAULT_PENALTY_NEGATIVE_EXPECTANCY_WEIGHT,
        help="Extra penalty scale for negative expectancy_per_trade (soft).",
    )
    ap.add_argument(
        "--strict-gates",
        action="store_true",
        help="Use legacy hard-drop gates for target trades/min trades per symbol/max DD.",
    )
    ap.add_argument("--apply", action="store_true", help="Patch .env and .env.example with winner")
    ap.add_argument(
        "--apply-from",
        type=str,
        default="",
        help="Apply env_updates from an existing winner.json without re-running the sweep",
    )
    ap.add_argument(
        "--emit-profile",
        action="store_true",
        help="Also write artifacts/strategy_profiles/sweep_<batch>.json from winner env_updates",
    )
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.apply_from:
        data = json.loads(Path(args.apply_from).read_text(encoding="utf-8"))
        updates = data.get("env_updates") or {}
        patch = {k: updates[k] for k in APPLY_KEYS if k in updates}
        for p in (REPO_ROOT / ".env", REPO_ROOT / ".env.example"):
            _apply_patch(p, patch)
        print("Applied from", args.apply_from)
        if args.emit_profile:
            from datetime import datetime, timezone

            bid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            ep = emit_strategy_profile(REPO_ROOT, {k: str(v) for k, v in updates.items()}, bid)
            print("Emitted strategy profile:", ep)
        return 0

    base = _build_base_env()

    combos = list(_iter_combos())
    if args.limit and args.limit < len(combos):
        rnd = random.Random(args.seed)
        combos = rnd.sample(combos, args.limit)

    results_path = OUT_DIR / "results.csv"
    fieldnames = [
        "rank_score",
        "TIMEFRAME",
        "TREND_STRATEGY_ENABLED",
        "BACKTEST_SKIP_TA_FILTER",
        "USE_TA_FILTER",
        "BACKTEST_SENTIMENT_MODE",
        "SIGNAL_RSI_OVERSOLD",
        "SIGNAL_RSI_OVERBOUGHT",
        "ADX_MAX_FOR_MEAN_REVERSION",
        "ADX_MIN_FOR_TREND",
        "MAX_TRADES_PER_DAY",
        "total_return_pct",
        "max_drawdown_pct",
        "trades",
        "wins",
        "losses",
        "profit_factor",
        "expectancy_per_trade",
        "min_trades_per_symbol",
        "symbols_tested",
        "base_score",
        "trade_penalty",
        "symbol_trade_penalty",
        "dd_penalty",
        "stability_penalty",
        "negative_return_penalty",
        "negative_expectancy_penalty",
        "error",
    ]

    rows: list[dict[str, Any]] = []
    for i, c in enumerate(combos):
        run_env = {**base, **c}
        m = _run_multi_symbol_backtest(run_env, bars=None)
        score = _score_details(
            m,
            min_trades_per_symbol=args.min_trades_per_symbol,
            target_trades=args.target_trades,
            max_dd_preferred=args.max_dd_preferred,
            penalty_trades_weight=args.penalty_trades_weight,
            penalty_symbol_trades_weight=args.penalty_symbol_trades_weight,
            penalty_dd_weight=args.penalty_dd_weight,
            penalty_negative_return_weight=args.penalty_negative_return_weight,
            penalty_negative_expectancy_weight=args.penalty_negative_expectancy_weight,
            strict_gates=args.strict_gates,
        )
        sc = score.get("rank_score")
        row = {**c, "rank_score": sc, "error": ""}
        if m.get("ok"):
            row["total_return_pct"] = m.get("total_return_pct")
            row["max_drawdown_pct"] = m.get("max_drawdown_pct")
            row["trades"] = m.get("trades")
            row["wins"] = m.get("wins")
            row["losses"] = m.get("losses")
            row["profit_factor"] = m.get("profit_factor")
            row["expectancy_per_trade"] = m.get("expectancy_per_trade")
            row["min_trades_per_symbol"] = m.get("min_trades_per_symbol")
            row["symbols_tested"] = m.get("symbols_tested")
            row["base_score"] = score.get("base_score")
            row["trade_penalty"] = score.get("trade_penalty")
            row["symbol_trade_penalty"] = score.get("symbol_trade_penalty")
            row["dd_penalty"] = score.get("dd_penalty")
            row["stability_penalty"] = score.get("stability_penalty")
            row["negative_return_penalty"] = score.get("negative_return_penalty")
            row["negative_expectancy_penalty"] = score.get("negative_expectancy_penalty")
        else:
            row["total_return_pct"] = ""
            row["max_drawdown_pct"] = ""
            row["trades"] = ""
            row["wins"] = ""
            row["losses"] = ""
            row["profit_factor"] = ""
            row["expectancy_per_trade"] = ""
            row["min_trades_per_symbol"] = ""
            row["symbols_tested"] = ""
            row["base_score"] = ""
            row["trade_penalty"] = ""
            row["symbol_trade_penalty"] = ""
            row["dd_penalty"] = ""
            row["stability_penalty"] = ""
            row["negative_return_penalty"] = ""
            row["negative_expectancy_penalty"] = ""
            row["error"] = str(m.get("error", "failed"))[:500]
        rows.append(row)
        print(f"[{i+1}/{len(combos)}] score={sc} tf={c['TIMEFRAME']} trend={c['TREND_STRATEGY_ENABLED']} ...")

    scored = [r for r in rows if r.get("rank_score") is not None]
    scored.sort(key=lambda r: float(r["rank_score"]), reverse=True)

    with results_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in scored + [x for x in rows if x not in scored]:
            w.writerow({k: r.get(k, "") for k in fieldnames})

    if not scored:
        print("No valid scored runs (check network / Binance / min trades).", file=sys.stderr)
        return 1

    stability: list[dict[str, Any]] = []
    top = scored[: max(1, args.stability_top)]
    for r in top:
        combo = {
            "TIMEFRAME": r["TIMEFRAME"],
            "TREND_STRATEGY_ENABLED": r["TREND_STRATEGY_ENABLED"],
            "BACKTEST_SKIP_TA_FILTER": r["BACKTEST_SKIP_TA_FILTER"],
            "USE_TA_FILTER": r["USE_TA_FILTER"],
            "BACKTEST_SENTIMENT_MODE": r["BACKTEST_SENTIMENT_MODE"],
            "SIGNAL_RSI_OVERSOLD": r["SIGNAL_RSI_OVERSOLD"],
            "SIGNAL_RSI_OVERBOUGHT": r["SIGNAL_RSI_OVERBOUGHT"],
            "ADX_MAX_FOR_MEAN_REVERSION": r["ADX_MAX_FOR_MEAN_REVERSION"],
            "ADX_MIN_FOR_TREND": r["ADX_MIN_FOR_TREND"],
            "MAX_TRADES_PER_DAY": r["MAX_TRADES_PER_DAY"],
        }
        run_env = {**base, **combo}
        m2 = _run_multi_symbol_backtest(run_env, bars=args.stability_bars)
        # Penalize instability between primary and rerun return/max_dd.
        ret1 = float(r.get("total_return_pct") or 0.0)
        ret2 = float(m2.get("total_return_pct") or 0.0)
        dd1 = float(r.get("max_drawdown_pct") or 0.0)
        dd2 = float(m2.get("max_drawdown_pct") or 0.0)
        variance_penalty = abs(ret1 - ret2) / 100.0 + abs(dd1 - dd2) / 100.0
        m2["stability_variance_penalty"] = variance_penalty
        score2 = _score_details(
            m2,
            min_trades_per_symbol=args.min_trades_per_symbol,
            target_trades=args.target_trades,
            max_dd_preferred=args.max_dd_preferred,
            penalty_trades_weight=args.penalty_trades_weight,
            penalty_symbol_trades_weight=args.penalty_symbol_trades_weight,
            penalty_dd_weight=args.penalty_dd_weight,
            penalty_negative_return_weight=args.penalty_negative_return_weight,
            penalty_negative_expectancy_weight=args.penalty_negative_expectancy_weight,
            strict_gates=args.strict_gates,
        )
        s2 = score2.get("rank_score")
        primary = float(r["rank_score"])
        mean_score = (primary + s2) / 2.0 if s2 is not None else primary
        stability.append(
            {
                "combo": combo,
                "primary_score": primary,
                "stability_score": s2,
                "stability_variance_penalty": variance_penalty,
                "stability_score_breakdown": score2,
                "mean_score": mean_score,
                "stability_run": {"ok": m2.get("ok"), "trades": m2.get("trades"), "error": m2.get("error")},
            }
        )

    stability.sort(key=lambda x: float(x["mean_score"]), reverse=True)
    (OUT_DIR / "stability.json").write_text(
        json.dumps(stability, indent=2),
        encoding="utf-8",
    )

    best = stability[0]
    winner_updates = {
        **best["combo"],
        "BACKTEST_RUN_ABLATION_MATRIX": "false",
        "BACKTEST_ENFORCE_MAX_TRADES_PER_DAY": "true",
    }
    (OUT_DIR / "winner.json").write_text(
        json.dumps(
            {
                "mean_score": best["mean_score"],
                "primary_score": best["primary_score"],
                "stability_score": best["stability_score"],
                "env_updates": winner_updates,
                "alternatives": stability[1:6],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if args.apply:
        for p in (REPO_ROOT / ".env", REPO_ROOT / ".env.example"):
            patch = {k: winner_updates[k] for k in APPLY_KEYS if k in winner_updates}
            _apply_patch(p, patch)
        print("Applied winner to .env and .env.example")

    if args.emit_profile:
        from datetime import datetime, timezone

        bid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ep = emit_strategy_profile(REPO_ROOT, {k: str(v) for k, v in winner_updates.items()}, bid)
        print("Emitted strategy profile:", ep)

    print("Done. Results:", results_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

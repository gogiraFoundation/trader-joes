"""Hard-block startup pipeline for training/tuning/backtest gate before live bot launch."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.backtest.last_run import read_backtest_last_run
from tr_bot.notifications import events as notify_events
from tr_bot.notifications.facade import notify
from tr_bot.promotion import log as promotion_log
from tr_bot.strategy import deployment_io as dep
from tr_bot.strategy.no_trade_adaptation import run_isolated_backtest_for_packaged_profile
from tr_bot.strategy.profile import build_profile_document
from tr_bot.startup.backtest_gate_thresholds import (
    DEFAULT_MAX_DRAWDOWN_PCT,
    DEFAULT_MIN_RETURN_PCT,
    DEFAULT_MIN_TRADES,
)
from tr_bot.startup.preflight import run_preflight_checks
from tr_bot.training.last_run import read_training_last_run


_PROFILE_PATH_RE = re.compile(r"Emitted strategy profile:\s*(.+\.json)\s*$")


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return float(raw)


def _run_cmd(
    args: list[str],
    *,
    timeout_s: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or "")
        stderr = (stderr + f"\ncommand_timed_out_after_seconds:{timeout_s}").strip()
        return subprocess.CompletedProcess(args=args, returncode=124, stdout=stdout, stderr=stderr)


def _should_retry_training_with_fresh_fetch(cp: subprocess.CompletedProcess[str]) -> bool:
    stderr = cp.stderr or ""
    stdout = cp.stdout or ""
    if cp.returncode == 0:
        return False
    return (
        "Not enough rows after indicators" in stderr
        or "Using cached training dataset" in stdout
    )


def _training_env_for_startup() -> dict[str, str]:
    # Startup enforces validation and runs its own explicit post-training backtest gate.
    return {
        "TRAIN_VALIDATION_STRICT": "true",
        "TRAIN_BACKTEST_GATE": "false",
    }


def _run_training_stage(python_exe: str) -> dict[str, Any]:
    first = _run_cmd(
        [python_exe, "-m", "tr_bot.managers", "train-run", "--compact"],
        extra_env=_training_env_for_startup(),
    )
    out = {
        "ok": first.returncode == 0,
        "returncode": first.returncode,
        "stdout": first.stdout,
        "stderr": first.stderr,
        "retried_with_fresh_fetch": False,
    }
    if not _should_retry_training_with_fresh_fetch(first):
        return out

    env = _training_env_for_startup()
    env["TRAIN_DATASET_MODE"] = "fetch_only"
    second = _run_cmd(
        [python_exe, "-m", "tr_bot.managers", "train-run", "--compact"],
        extra_env=env,
    )
    out["retried_with_fresh_fetch"] = True
    out["retry"] = {
        "returncode": second.returncode,
        "stdout": second.stdout,
        "stderr": second.stderr,
        "dataset_mode": "fetch_only",
    }
    if second.returncode == 0:
        out["ok"] = True
        out["returncode"] = 0
        out["stdout"] = second.stdout
        out["stderr"] = second.stderr
    return out


def _load_profile_params(profile_id: str) -> dict[str, Any] | None:
    root = Path(config.ARTIFACTS_DIR).resolve()
    p = dep.strategy_profile_path(root, profile_id)
    raw = dep.read_json_file(p)
    if not isinstance(raw, dict):
        return None
    params = raw.get("params")
    return params if isinstance(params, dict) else None


def _build_candidate_package(profile_id: str) -> dict[str, Any] | None:
    """Build isolated-backtest package from an emitted sweep profile file."""
    root = Path(config.ARTIFACTS_DIR).resolve()
    raw = dep.read_json_file(dep.strategy_profile_path(root, profile_id))
    if not isinstance(raw, dict):
        return None
    params = raw.get("params")
    if not isinstance(params, dict):
        return None
    doc = build_profile_document(
        profile_id,
        params,
        parent_profile_id=str(raw.get("parent_profile_id") or config.ACTIVE_STRATEGY_PROFILE_ID or "") or None,
        hypothesis=str(raw.get("hypothesis") or f"Startup tuning candidate {profile_id}"),
        source="startup_tuning",
    )
    now = datetime.now(timezone.utc).isoformat()
    return {
        "pointer": {"profile_id": profile_id, "activated_at": now, "source": "startup_tuning"},
        "profile_body": doc,
    }


def _activate_candidate_after_gate(
    profile_id: str,
    *,
    backtest_metrics: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    """Commit profile + pointer only after backtest gate passes."""
    root = Path(config.ARTIFACTS_DIR).resolve()
    pkg = _build_candidate_package(profile_id)
    if not pkg:
        return {"ok": False, "reason": "candidate_profile_missing", "profile_id": profile_id}

    if not gate.get("passed"):
        now = datetime.now(timezone.utc).isoformat()
        promotion_log.append_promotion_event(
            root,
            {
                "action": "startup_candidate_rejected",
                "utc": now,
                "profile_id": profile_id,
                "to_tier": "paper",
                "passed": False,
                "reason": "backtest_gate_failed",
                "backtest": backtest_metrics,
                "gate": gate,
            },
        )
        return {
            "ok": False,
            "reason": "backtest_gate_failed",
            "profile_id": profile_id,
            "gate": gate,
        }

    result = dep.commit_candidate_profile(
        root,
        pkg["profile_body"],
        tier="paper",
        update_pointer=True,
        pointer_source="startup_tuning",
    )
    now = datetime.now(timezone.utc).isoformat()
    promotion_log.append_promotion_event(
        root,
        {
            "action": "startup_register_paper_candidate",
            "utc": now,
            "profile_id": profile_id,
            "to_tier": "paper",
            "passed": bool(result.get("ok")),
            "config_hash": result.get("config_hash"),
            "backtest": backtest_metrics,
            "gate": gate,
        },
    )
    return result


def _register_profile_to_paper(profile_id: str) -> dict[str, Any]:
    """Deprecated direct register — kept for tests; prefer _activate_candidate_after_gate."""
    root = Path(config.ARTIFACTS_DIR).resolve()
    pkg = _build_candidate_package(profile_id)
    if not pkg:
        raise ValueError(f"profile not found: {profile_id}")
    return dep.commit_candidate_profile(
        root,
        pkg["profile_body"],
        tier="paper",
        update_pointer=True,
        pointer_source="startup_tuning",
    )


def _extract_profile_id(stdout: str, start_ts: float) -> str | None:
    for ln in (stdout or "").splitlines():
        m = _PROFILE_PATH_RE.search(ln)
        if not m:
            continue
        p = Path(m.group(1).strip())
        if p.suffix == ".json":
            return p.stem

    root = Path(config.ARTIFACTS_DIR).resolve() / "strategy_profiles"
    if not root.is_dir():
        return None
    newest: Path | None = None
    newest_m = 0.0
    for p in root.glob("sweep_*.json"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime >= start_ts and mtime > newest_m:
            newest = p
            newest_m = mtime
    return newest.stem if newest else None


def evaluate_backtest_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    min_ret = _float_env("STARTUP_BACKTEST_MIN_RETURN_PCT", DEFAULT_MIN_RETURN_PCT)
    max_dd = _float_env("STARTUP_BACKTEST_MAX_DD_PCT", DEFAULT_MAX_DRAWDOWN_PCT)
    min_trades = _int_env("STARTUP_BACKTEST_MIN_TRADES", DEFAULT_MIN_TRADES)

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
        "thresholds": {
            "min_return_pct": min_ret,
            "max_drawdown_pct": max_dd,
            "min_trades": min_trades,
        },
        "observed": {
            "total_return_pct": ret,
            "max_drawdown_pct": dd,
            "trades": trades,
        },
    }


def evaluate_training_validation_gate(last_run: dict[str, Any]) -> dict[str, Any]:
    require = _bool_env("STARTUP_REQUIRE_VALIDATION_GATES", True)
    min_oof_f1 = _float_env("STARTUP_MIN_OOF_F1", float(config.TRAIN_MIN_OOF_F1))
    max_gap = _float_env("STARTUP_MAX_TRAIN_VAL_GAP", float(config.TRAIN_MAX_TRAIN_VAL_GAP))

    validation = last_run.get("validation") if isinstance(last_run, dict) else None
    oof_f1 = None
    gap = None
    if isinstance(validation, dict):
        try:
            oof_f1 = float(validation.get("oof_f1")) if validation.get("oof_f1") is not None else None
        except (TypeError, ValueError):
            oof_f1 = None
        try:
            mg = validation.get("mean_train_val_gap_accuracy")
            gap = float(mg) if mg is not None else None
        except (TypeError, ValueError):
            gap = None

    checks = {
        "present": validation is not None,
        "oof_f1": (oof_f1 is not None and oof_f1 >= min_oof_f1),
        "train_val_gap": (gap is not None and gap <= max_gap),
        "validation_gates_passed": bool(last_run.get("validation_gates_passed")),
    }
    passed = (all(checks.values()) if require else True)
    return {
        "passed": passed,
        "required": require,
        "checks": checks,
        "thresholds": {"min_oof_f1": min_oof_f1, "max_train_val_gap": max_gap},
        "observed": {"oof_f1": oof_f1, "mean_train_val_gap_accuracy": gap},
    }


def run_startup_pipeline(python_exe: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "stages": {},
    }

    pre = run_preflight_checks()
    out["stages"]["preflight"] = pre
    if not pre.get("ok"):
        out["reason"] = "preflight_failed"
        return out

    train_stage = _run_training_stage(python_exe)
    out["stages"]["train"] = train_stage
    if not train_stage.get("ok"):
        out["reason"] = "training_failed"
        return out
    tr_last = read_training_last_run() or {}
    tr_gate = evaluate_training_validation_gate(tr_last)
    out["stages"]["training_validation_gate"] = tr_gate
    if not tr_gate.get("passed"):
        out["reason"] = "training_validation_gate_failed"
        return out

    if _bool_env("STARTUP_RUN_TUNING", True):
        tuning_limit = _int_env("STARTUP_TUNING_LIMIT", 12)
        tuning_timeout = _int_env("STARTUP_TUNING_TIMEOUT_SECONDS", 900)
        script = Path(__file__).resolve().parents[2] / "scripts" / "sweep_best_fit_settings.py"
        tune_args = [
            python_exe,
            str(script),
            "--limit",
            str(tuning_limit),
            "--emit-profile",
        ]
        st_raw = os.environ.get("STARTUP_TUNING_STABILITY_TOP")
        if st_raw is not None and str(st_raw).strip() != "":
            tune_args.extend(["--stability-top", str(int(str(st_raw).strip()))])
        t0 = time.time()
        tune_cp = _run_cmd(
            tune_args,
            timeout_s=tuning_timeout,
        )
        emitted_pid = _extract_profile_id(tune_cp.stdout, t0)
        candidate_backtest: dict[str, Any] | None = None
        candidate_gate: dict[str, Any] | None = None
        register_result: dict[str, Any] | None = None
        if tune_cp.returncode == 0 and emitted_pid:
            pkg = _build_candidate_package(emitted_pid)
            if pkg is None:
                register_result = {"ok": False, "reason": "candidate_profile_missing"}
                tune_cp = subprocess.CompletedProcess(
                    args=tune_cp.args,
                    returncode=1,
                    stdout=tune_cp.stdout,
                    stderr=(tune_cp.stderr + "\ncandidate_profile_missing"),
                )
            else:
                timeout = _int_env("STARTUP_TUNING_BACKTEST_TIMEOUT_SECONDS", 600)
                metrics, bt_err = run_isolated_backtest_for_packaged_profile(
                    emitted_pid,
                    pkg,
                    timeout_s=timeout,
                )
                if metrics is None:
                    register_result = {"ok": False, "reason": f"isolated_backtest_failed:{bt_err}"}
                    candidate_backtest = {"error": bt_err}
                    promotion_log.append_promotion_event(
                        Path(config.ARTIFACTS_DIR).resolve(),
                        {
                            "action": "startup_candidate_rejected",
                            "utc": datetime.now(timezone.utc).isoformat(),
                            "profile_id": emitted_pid,
                            "passed": False,
                            "reason": f"isolated_backtest_failed:{bt_err}",
                        },
                    )
                else:
                    candidate_backtest = metrics
                    candidate_gate = evaluate_backtest_gate(metrics)
                    register_result = _activate_candidate_after_gate(
                        emitted_pid,
                        backtest_metrics=metrics,
                        gate=candidate_gate,
                    )
                    if not register_result.get("ok"):
                        tune_cp = subprocess.CompletedProcess(
                            args=tune_cp.args,
                            returncode=1,
                            stdout=tune_cp.stdout,
                            stderr=(
                                tune_cp.stderr
                                + f"\nstartup_candidate_activation_failed:{register_result.get('reason')}"
                            ),
                        )
        soft_timeout = (
            tune_cp.returncode == 124
            and _bool_env("STARTUP_TUNING_TIMEOUT_SOFT_FAIL", False)
        )
        out["stages"]["tune"] = {
            "ok": tune_cp.returncode == 0,
            "returncode": tune_cp.returncode,
            "stdout": tune_cp.stdout,
            "stderr": tune_cp.stderr,
            "timed_out": tune_cp.returncode == 124,
            "timeout_seconds": tuning_timeout,
            "soft_fail_skipped": soft_timeout,
            "emitted_profile_id": emitted_pid,
            "paper_pointer": register_result,
            "candidate_backtest": candidate_backtest,
            "candidate_backtest_gate": candidate_gate,
        }
        if isinstance(register_result, dict) and isinstance(register_result.get("profile_id"), str):
            out["stages"]["preflight"]["active_strategy_profile_id_after_tune"] = register_result[
                "profile_id"
            ]
        if tune_cp.returncode != 0:
            if soft_timeout:
                out["stages"]["tune"]["ok"] = True
                out["stages"]["tune"]["skipped_due_to_timeout"] = True
            else:
                out["reason"] = "tuning_failed"
                return out
    else:
        out["stages"]["tune"] = {"ok": True, "skipped": True}

    # Post-tuning backtest on active profile (validation of running config)
    back_cp = _run_cmd([python_exe, "-m", "tr_bot.managers", "backtest-run", "--compact"])
    out["stages"]["backtest"] = {
        "ok": back_cp.returncode == 0,
        "returncode": back_cp.returncode,
        "stdout": back_cp.stdout,
        "stderr": back_cp.stderr,
    }
    if back_cp.returncode != 0:
        out["reason"] = "backtest_failed"
        return out

    last = read_backtest_last_run() or {}
    gate = evaluate_backtest_gate(last)
    out["stages"]["backtest_gate"] = gate
    if not gate["passed"]:
        out["reason"] = "backtest_gate_failed"
        return out

    out["ok"] = True
    root = Path(config.ARTIFACTS_DIR).resolve()
    ptr = dep.read_pointer(root, config.DEPLOYMENT_TIER)
    if ptr and isinstance(ptr.get("profile_id"), str) and (ptr.get("profile_id") or "").strip():
        out["active_strategy_profile_id_effective"] = (ptr.get("profile_id") or "").strip()
    return out




def startup_last_run_path() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    return dep.deployment_dir(root) / "startup_last_run.json"


def write_startup_last_run(payload: dict[str, Any]) -> None:
    dep.atomic_write_json(startup_last_run_path(), payload)


def read_startup_last_run() -> dict[str, Any] | None:
    raw = dep.read_json_file(startup_last_run_path())
    return raw if isinstance(raw, dict) else None

def should_launch_scheduler() -> bool:
    return _bool_env("STARTUP_LAUNCH_SCHEDULER", True)


def run_cli() -> int:
    ap = argparse.ArgumentParser(prog="python -m tr_bot.startup.pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Run startup hard-block pipeline")
    sub.add_parser("should-launch-scheduler", help="Print 1 if scheduler should start")
    args = ap.parse_args()

    if args.cmd == "should-launch-scheduler":
        print("1" if should_launch_scheduler() else "0")
        return 0

    py = sys.executable
    out = run_startup_pipeline(py)
    pre_warnings = (out.get("stages") or {}).get("preflight", {}).get("warnings") or []
    cmc_warns = [w for w in pre_warnings if isinstance(w, str) and w.startswith("cmc:")]
    if cmc_warns:
        print("\nCoinMarketCap / training (preflight warnings):", file=sys.stderr)
        for w in cmc_warns:
            print(" ", w, file=sys.stderr)
        print(file=sys.stderr)
    print(json.dumps(out, indent=2, default=str))
    if out.get("ok"):
        rec = {"utc": datetime.now(timezone.utc).isoformat(), "passed": True, "result": out}
        write_startup_last_run(rec)
        notify(notify_events.STARTUP_SUCCESS, stage="startup_pipeline", reason="ok")
        notify(notify_events.BACKTEST_SUCCESS, stage="startup_pipeline")
        return 0

    notify(notify_events.BOT_ERROR, message=f"startup_pipeline_failed:{out.get('reason')}")
    root = Path(config.ARTIFACTS_DIR).resolve()
    rec = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "reason": out.get("reason"),
        "result": out,
    }
    write_startup_last_run(rec)
    promotion_log.append_promotion_event(
        root,
        {
            "action": "startup_pipeline",
            "utc": rec["utc"],
            "passed": False,
            "reason": out.get("reason"),
            "summary": {
                "preflight_ok": (out.get("stages", {}).get("preflight", {}).get("ok")),
                "train_ok": (out.get("stages", {}).get("train", {}).get("ok")),
                "tune_ok": (out.get("stages", {}).get("tune", {}).get("ok")),
                "backtest_ok": (out.get("stages", {}).get("backtest", {}).get("ok")),
                "gate_ok": (out.get("stages", {}).get("backtest_gate", {}).get("passed")),
            },
        },
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(run_cli())

from __future__ import annotations

from tr_bot.startup import pipeline

# Isolated startup pipeline tests must not depend on workspace artifacts/training/last_run.json.
_PASSING_TRAIN_LAST_RUN = {
    "validation": {"oof_f1": 0.99, "mean_train_val_gap_accuracy": 0.01},
    "validation_gates_passed": True,
}


def test_backtest_gate_thresholds_env(monkeypatch):
    monkeypatch.setenv("STARTUP_BACKTEST_MIN_RETURN_PCT", "5")
    monkeypatch.setenv("STARTUP_BACKTEST_MAX_DD_PCT", "12")
    monkeypatch.setenv("STARTUP_BACKTEST_MIN_TRADES", "10")

    out = pipeline.evaluate_backtest_gate(
        {
            "total_return_pct": 6.0,
            "max_drawdown_pct": 11.0,
            "trades": 12,
        }
    )
    assert out["passed"] is True



def test_backtest_gate_fails_when_threshold_not_met(monkeypatch):
    monkeypatch.setenv("STARTUP_BACKTEST_MIN_RETURN_PCT", "5")
    monkeypatch.setenv("STARTUP_BACKTEST_MAX_DD_PCT", "8")
    monkeypatch.setenv("STARTUP_BACKTEST_MIN_TRADES", "10")

    out = pipeline.evaluate_backtest_gate(
        {
            "total_return_pct": 4.0,
            "max_drawdown_pct": 9.0,
            "trades": 8,
        }
    )
    assert out["passed"] is False
    assert out["checks"]["min_return"] is False
    assert out["checks"]["max_drawdown"] is False
    assert out["checks"]["min_trades"] is False


def test_backtest_gate_builtin_defaults_allow_zero_trades(monkeypatch):
    """Default min_trades=0: no-fill walk-forward (e.g. strict profile) does not block startup."""
    for key in (
        "STARTUP_BACKTEST_MIN_RETURN_PCT",
        "STARTUP_BACKTEST_MAX_DD_PCT",
        "STARTUP_BACKTEST_MIN_TRADES",
    ):
        monkeypatch.delenv(key, raising=False)
    assert pipeline.evaluate_backtest_gate(
        {"total_return_pct": 0.0, "max_drawdown_pct": 0.0, "trades": 0}
    )["passed"] is True
    assert pipeline.evaluate_backtest_gate(
        {"total_return_pct": 3.0, "max_drawdown_pct": 5.0, "trades": 3}
    )["passed"] is True


def test_backtest_gate_min_trades_enforced_when_env_set(monkeypatch):
    monkeypatch.setenv("STARTUP_BACKTEST_MIN_TRADES", "3")
    monkeypatch.delenv("STARTUP_BACKTEST_MIN_RETURN_PCT", raising=False)
    monkeypatch.delenv("STARTUP_BACKTEST_MAX_DD_PCT", raising=False)
    g = pipeline.evaluate_backtest_gate(
        {"total_return_pct": 3.0, "max_drawdown_pct": 0.0, "trades": 2}
    )
    assert g["passed"] is False
    assert g["checks"]["min_trades"] is False


def test_backtest_gate_builtin_defaults_passes_small_negative_return(monkeypatch):
    """Default min return floor allows small walk-forward losses, not large ones."""
    for key in (
        "STARTUP_BACKTEST_MIN_RETURN_PCT",
        "STARTUP_BACKTEST_MAX_DD_PCT",
        "STARTUP_BACKTEST_MIN_TRADES",
    ):
        monkeypatch.delenv(key, raising=False)
    assert pipeline.evaluate_backtest_gate(
        {"total_return_pct": -0.0374, "max_drawdown_pct": 1.99, "trades": 5}
    )["passed"] is True
    assert pipeline.evaluate_backtest_gate(
        {"total_return_pct": -0.5199, "max_drawdown_pct": 1.99, "trades": 3}
    )["passed"] is True

    g = pipeline.evaluate_backtest_gate(
        {"total_return_pct": -2.0, "max_drawdown_pct": 1.0, "trades": 5}
    )
    assert g["passed"] is False
    assert g["checks"]["min_return"] is False


def test_extract_profile_id_from_stdout():
    stdout = "some line\nEmitted strategy profile: /tmp/artifacts/strategy_profiles/sweep_20260327T010203Z.json\n"
    pid = pipeline._extract_profile_id(stdout, 0.0)
    assert pid == "sweep_20260327T010203Z"



def test_should_launch_scheduler(monkeypatch):
    monkeypatch.setenv("STARTUP_LAUNCH_SCHEDULER", "false")
    assert pipeline.should_launch_scheduler() is False
    monkeypatch.setenv("STARTUP_LAUNCH_SCHEDULER", "true")
    assert pipeline.should_launch_scheduler() is True

def test_startup_last_run_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.config, "ARTIFACTS_DIR", str(tmp_path))
    payload = {"utc": "2026-01-01T00:00:00+00:00", "passed": True, "reason": "ok"}
    pipeline.write_startup_last_run(payload)
    got = pipeline.read_startup_last_run()
    assert got is not None
    assert got.get("passed") is True
    assert got.get("reason") == "ok"



def test_retry_training_decision():
    import subprocess

    cp = subprocess.CompletedProcess(
        args=["x"],
        returncode=1,
        stdout="Using cached training dataset (2 rows)\n",
        stderr="ValueError: Not enough rows after indicators: 0",
    )
    assert pipeline._should_retry_training_with_fresh_fetch(cp) is True

    cp2 = subprocess.CompletedProcess(args=["x"], returncode=1, stdout="", stderr="some other error")
    assert pipeline._should_retry_training_with_fresh_fetch(cp2) is False


def test_training_env_for_startup():
    env = pipeline._training_env_for_startup()
    assert env["TRAIN_VALIDATION_STRICT"] == "true"
    assert env["TRAIN_BACKTEST_GATE"] == "false"


def test_run_cmd_timeout_returns_completed_process():
    import sys
    cp = pipeline._run_cmd([sys.executable, "-c", "import time; time.sleep(2)"], timeout_s=1)
    assert cp.returncode == 124
    assert "command_timed_out_after_seconds" in (cp.stderr or "")


def test_tune_timeout_soft_fail_continues(monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("STARTUP_TUNING_TIMEOUT_SOFT_FAIL", "true")
    monkeypatch.setenv("STARTUP_RUN_TUNING", "true")
    monkeypatch.setattr(pipeline, "run_preflight_checks", lambda: {"ok": True})
    monkeypatch.setattr(pipeline, "_run_training_stage", lambda py: {"ok": True})
    monkeypatch.setattr(
        pipeline, "read_training_last_run", lambda: dict(_PASSING_TRAIN_LAST_RUN)
    )

    def fake_cmd(args, timeout_s=None, extra_env=None):
        joined = " ".join(str(a) for a in args)
        if "sweep_best_fit_settings.py" in joined:
            return subprocess.CompletedProcess(
                args=args, returncode=124, stdout="", stderr="timeout"
            )
        if "backtest-run" in joined:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pipeline, "_run_cmd", fake_cmd)
    monkeypatch.setattr(
        pipeline,
        "read_backtest_last_run",
        lambda: {"total_return_pct": 1.0, "max_drawdown_pct": 5.0, "trades": 3},
    )
    out = pipeline.run_startup_pipeline(sys.executable)
    assert out["ok"] is True
    assert out["stages"]["tune"]["soft_fail_skipped"] is True
    assert out["stages"]["tune"]["skipped_due_to_timeout"] is True


def test_tune_timeout_hard_fails_without_soft_fail(monkeypatch):
    import subprocess
    import sys

    monkeypatch.delenv("STARTUP_TUNING_TIMEOUT_SOFT_FAIL", raising=False)
    monkeypatch.setenv("STARTUP_RUN_TUNING", "true")
    monkeypatch.setattr(pipeline, "run_preflight_checks", lambda: {"ok": True})
    monkeypatch.setattr(pipeline, "_run_training_stage", lambda py: {"ok": True})
    monkeypatch.setattr(
        pipeline, "read_training_last_run", lambda: dict(_PASSING_TRAIN_LAST_RUN)
    )

    def fake_cmd(args, timeout_s=None, extra_env=None):
        joined = " ".join(str(a) for a in args)
        if "sweep_best_fit_settings.py" in joined:
            return subprocess.CompletedProcess(
                args=args, returncode=124, stdout="", stderr="timeout"
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pipeline, "_run_cmd", fake_cmd)
    out = pipeline.run_startup_pipeline(sys.executable)
    assert out["ok"] is False
    assert out["reason"] == "tuning_failed"


def test_tuning_stability_top_is_forwarded(monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("STARTUP_RUN_TUNING", "true")
    monkeypatch.setenv("STARTUP_TUNING_STABILITY_TOP", "2")
    monkeypatch.setattr(pipeline, "run_preflight_checks", lambda: {"ok": True})
    monkeypatch.setattr(pipeline, "_run_training_stage", lambda py: {"ok": True})
    monkeypatch.setattr(
        pipeline, "read_training_last_run", lambda: dict(_PASSING_TRAIN_LAST_RUN)
    )

    seen: dict[str, list[str]] = {}

    def fake_cmd(args, timeout_s=None, extra_env=None):
        joined = " ".join(str(a) for a in args)
        if "sweep_best_fit_settings.py" in joined:
            seen["args"] = [str(a) for a in args]
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if "backtest-run" in joined:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pipeline, "_run_cmd", fake_cmd)
    monkeypatch.setattr(
        pipeline,
        "read_backtest_last_run",
        lambda: {"total_return_pct": 1.0, "max_drawdown_pct": 5.0, "trades": 3},
    )
    out = pipeline.run_startup_pipeline(sys.executable)
    assert out["ok"] is True
    assert "--stability-top" in seen["args"]
    idx = seen["args"].index("--stability-top")
    assert seen["args"][idx + 1] == "2"


def test_active_strategy_profile_id_after_tune_when_register_succeeds(monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("STARTUP_RUN_TUNING", "true")
    monkeypatch.setattr(
        pipeline,
        "run_preflight_checks",
        lambda: {"ok": True, "active_strategy_profile_id": "before_tune"},
    )
    monkeypatch.setattr(pipeline, "_run_training_stage", lambda py: {"ok": True})
    monkeypatch.setattr(
        pipeline, "read_training_last_run", lambda: dict(_PASSING_TRAIN_LAST_RUN)
    )

    emitted = "sweep_after_tune"

    def fake_register(pid: str) -> dict:
        return {"profile_id": pid, "activated_at": "t", "source": "startup_tuning"}

    monkeypatch.setattr(pipeline, "_register_profile_to_paper", fake_register)

    def fake_cmd(args, timeout_s=None, extra_env=None):
        joined = " ".join(str(a) for a in args)
        if "sweep_best_fit_settings.py" in joined:
            line = f"Emitted strategy profile: /x/artifacts/strategy_profiles/{emitted}.json\n"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=line, stderr="")
        if "backtest-run" in joined:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pipeline, "_run_cmd", fake_cmd)
    monkeypatch.setattr(
        pipeline,
        "read_backtest_last_run",
        lambda: {"total_return_pct": 1.0, "max_drawdown_pct": 5.0, "trades": 3},
    )
    monkeypatch.setattr(
        pipeline.dep,
        "read_pointer",
        lambda root, tier: {"profile_id": emitted, "activated_at": "t", "source": "test"},
    )
    out = pipeline.run_startup_pipeline(sys.executable)
    assert out["ok"] is True
    assert out["stages"]["preflight"]["active_strategy_profile_id"] == "before_tune"
    assert (
        out["stages"]["preflight"]["active_strategy_profile_id_after_tune"] == emitted
    )
    assert out.get("active_strategy_profile_id_effective") == emitted

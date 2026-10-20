from __future__ import annotations


from tr_bot.startup import pipeline
from tr_bot.strategy import deployment_io as dep
from tr_bot.strategy.profile import build_profile_document


def test_activate_candidate_rejects_failed_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.config, "ARTIFACTS_DIR", str(tmp_path))
    dep.deployment_dir(tmp_path)
    doc = build_profile_document("sweep_test", {"RISK_PER_TRADE": 0.01})
    dep.atomic_write_json(dep.strategy_profile_path(tmp_path, "sweep_test"), doc)

    gate = pipeline.evaluate_backtest_gate({"total_return_pct": 0, "max_drawdown_pct": 0, "trades": 0})
    result = pipeline._activate_candidate_after_gate(
        "sweep_test",
        backtest_metrics={"trades": 0},
        gate=gate,
    )
    assert result["ok"] is False
    ptr = dep.read_pointer(tmp_path, "paper")
    assert ptr is None


def test_activate_candidate_commits_on_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.config, "ARTIFACTS_DIR", str(tmp_path))
    dep.deployment_dir(tmp_path)
    doc = build_profile_document("sweep_ok", {"RISK_PER_TRADE": 0.01})
    dep.atomic_write_json(dep.strategy_profile_path(tmp_path, "sweep_ok"), doc)

    metrics = {"total_return_pct": 1.0, "max_drawdown_pct": 2.0, "trades": 5}
    gate = pipeline.evaluate_backtest_gate(metrics)
    result = pipeline._activate_candidate_after_gate(
        "sweep_ok",
        backtest_metrics=metrics,
        gate=gate,
    )
    assert result["ok"] is True
    ptr = dep.read_pointer(tmp_path, "paper")
    assert ptr is not None
    assert ptr["profile_id"] == "sweep_ok"
    assert ptr.get("config_hash")

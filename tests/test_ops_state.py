"""ops_state equity history fallback."""

from __future__ import annotations

import json

import pytest

import tr_bot.config as cfg


def test_read_equity_history_falls_back_to_fund_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "ARTIFACTS_DIR", str(tmp_path))
    fund = {
        "last_nav": 10000.0,
        "peak_nav": 10000.0,
        "last_update_utc": "2026-01-01T00:00:00+00:00",
        "halted": False,
        "halt_reason": "",
    }
    (tmp_path / "fund_state.json").write_text(json.dumps(fund), encoding="utf-8")

    from tr_bot.ops_state import read_equity_history

    out = read_equity_history()
    assert len(out["points"]) == 1
    assert out["points"][0]["nav"] == 10000.0
    assert "2026" in out["points"][0]["t"]


def test_read_equity_history_prefers_file_over_fund_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "ARTIFACTS_DIR", str(tmp_path))
    (tmp_path / "fund_state.json").write_text(
        json.dumps({"last_nav": 1.0, "last_update_utc": "x"}),
        encoding="utf-8",
    )
    pts = {"points": [{"t": "2020-01-01T00:00:00+00:00", "nav": 99.0}]}
    (tmp_path / "equity_history.json").write_text(json.dumps(pts), encoding="utf-8")

    from tr_bot.ops_state import read_equity_history

    out = read_equity_history()
    assert len(out["points"]) == 1
    assert out["points"][0]["nav"] == 99.0

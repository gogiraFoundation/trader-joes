"""FundManager drawdown halt must not latch forever when NAV recovers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tr_bot.managers import fund as fund_mod


def test_drawdown_halt_clears_when_nav_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "MANAGERS_ENABLED", True)
    monkeypatch.setattr(fund_mod.config, "PAPER_TRADING", True)
    monkeypatch.setattr(fund_mod.config, "PAPER_EQUITY_USDT", 10_000.0)
    monkeypatch.setattr(fund_mod.config, "MAX_DRAWDOWN_PCT", 0.25)
    # Ledger not at baseline so corrupt-low clamp does not override fake estimate=100
    monkeypatch.setattr(fund_mod, "sum_closed_net_pnl_usdt", lambda paper=True: -2000.0)

    fm = fund_mod.FundManager()
    # Persisted peak high; first NAV read "low" => large drawdown => halt
    state_path = tmp_path / "fund_state.json"
    state_path.write_text(
        json.dumps(
            {
                "last_nav": 10_000.0,
                "peak_nav": 10_000.0,
                "last_update_utc": "2026-01-01T00:00:00+00:00",
                "halted": False,
                "halt_reason": "",
            }
        ),
        encoding="utf-8",
    )

    calls: list[float] = [100.0, 10_000.0]

    def fake_estimate(*, unrealized_pnl_usdt: float = 0.0) -> float:
        return float(calls.pop(0))

    monkeypatch.setattr(fm, "estimate_equity_usdt", fake_estimate)
    monkeypatch.setattr(fund_mod, "notify", lambda *a, **k: None)

    st1 = fm.refresh_nav_and_limits()
    assert st1.halted is True
    assert "max_drawdown" in st1.halt_reason

    st2 = fm.refresh_nav_and_limits()
    assert st2.halted is False
    assert st2.halt_reason == ""


def test_paper_nav_clamps_when_corrupt_low_matches_ledger_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """NAV read ~100 while baseline+ledger say ~10k must not create 99% DD halt."""
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "MANAGERS_ENABLED", True)
    monkeypatch.setattr(fund_mod.config, "PAPER_TRADING", True)
    monkeypatch.setattr(fund_mod.config, "PAPER_EQUITY_USDT", 10_000.0)
    monkeypatch.setattr(fund_mod.config, "MAX_DRAWDOWN_PCT", 0.25)
    monkeypatch.setattr(fund_mod, "sum_closed_net_pnl_usdt", lambda paper=True: 0.0)
    monkeypatch.setattr(fund_mod, "notify", lambda *a, **k: None)

    fm = fund_mod.FundManager()
    monkeypatch.setattr(fm, "estimate_equity_usdt", lambda **kw: 100.0)
    st = fm.refresh_nav_and_limits(unrealized_pnl_usdt=0.0)
    assert abs(st.last_nav - 10_000.0) < 1e-6
    assert st.halted is False
    assert abs(st.peak_nav - 10_000.0) < 1e-6


def test_paper_nav_not_clamped_when_ledger_shows_real_loss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "MANAGERS_ENABLED", True)
    monkeypatch.setattr(fund_mod.config, "PAPER_TRADING", True)
    monkeypatch.setattr(fund_mod.config, "PAPER_EQUITY_USDT", 10_000.0)
    monkeypatch.setattr(fund_mod.config, "MAX_DRAWDOWN_PCT", 0.99)
    monkeypatch.setattr(fund_mod, "sum_closed_net_pnl_usdt", lambda paper=True: -6000.0)
    monkeypatch.setattr(fund_mod, "notify", lambda *a, **k: None)

    fm = fund_mod.FundManager()
    monkeypatch.setattr(fm, "estimate_equity_usdt", lambda **kw: 4000.0)
    st = fm.refresh_nav_and_limits()
    assert abs(st.last_nav - 4000.0) < 1e-6


def test_paper_equity_baseline_change_resets_peak(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "MANAGERS_ENABLED", True)
    monkeypatch.setattr(fund_mod.config, "PAPER_TRADING", True)
    monkeypatch.setattr(fund_mod.config, "PAPER_EQUITY_USDT", 10_000.0)
    monkeypatch.setattr(fund_mod.config, "MAX_DRAWDOWN_PCT", 0.25)
    monkeypatch.setattr(fund_mod, "sum_closed_net_pnl_usdt", lambda paper=True: 0.0)
    monkeypatch.setattr(fund_mod, "notify", lambda *a, **k: None)

    state_path = tmp_path / "fund_state.json"
    state_path.write_text(
        json.dumps(
            {
                "last_nav": 10_000.0,
                "peak_nav": 10_000.0,
                "last_update_utc": "2026-01-01T00:00:00+00:00",
                "halted": True,
                "halt_reason": "max_drawdown_exceeded (0.9900>=0.25)",
                "paper_equity_baseline_usdt": 100.0,
            }
        ),
        encoding="utf-8",
    )

    fm = fund_mod.FundManager()
    st = fm.refresh_nav_and_limits()
    assert st.halted is False
    assert st.peak_nav == 10_000.0
    assert st.paper_equity_baseline_usdt == 10_000.0


def test_manual_halt_file_still_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "MANAGERS_ENABLED", True)
    monkeypatch.setattr(fund_mod.config, "TRADING_HALT_FILENAME", "TRADING_HALT")
    monkeypatch.setattr(fund_mod.config, "PAPER_TRADING", True)
    monkeypatch.setattr(fund_mod.config, "PAPER_EQUITY_USDT", 100.0)
    monkeypatch.setattr(fund_mod.config, "MAX_DRAWDOWN_PCT", 0.99)
    monkeypatch.setattr(fund_mod, "sum_closed_net_pnl_usdt", lambda paper=True: 0.0)

    halt_file = tmp_path / "TRADING_HALT"
    halt_file.write_text("stop", encoding="utf-8")

    monkeypatch.setattr(fund_mod, "notify", lambda *a, **k: None)
    fm = fund_mod.FundManager()
    st = fm.refresh_nav_and_limits()
    assert st.halted is True
    assert st.halt_reason == "manual_halt_file"


def test_set_manual_halt_creates_file_with_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "TRADING_HALT_FILENAME", "TRADING_HALT")

    fm = fund_mod.FundManager()
    assert fm.manual_halt_file_active() is False
    fm.set_manual_halt("paused from dashboard")
    assert fm.manual_halt_file_active() is True
    halt_file = tmp_path / "TRADING_HALT"
    assert "paused from dashboard" in halt_file.read_text(encoding="utf-8")
    assert fm.manual_halt_owner() == "operator"
    assert fm.manual_halt_reason_text() == "paused from dashboard"


def test_agent_halt_json_owner_and_no_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "TRADING_HALT_FILENAME", "TRADING_HALT")

    fm = fund_mod.FundManager()
    assert fm.set_manual_halt_if_inactive("agents_supervisor_pause", owner="agent") is True
    assert fm.manual_halt_owner() == "agent"
    assert fm.manual_halt_reason_text() == "agents_supervisor_pause"
    body = (tmp_path / "TRADING_HALT").read_text(encoding="utf-8")
    assert '"owner":"agent"' in body.replace(" ", "")

    assert fm.set_manual_halt_if_inactive("other", owner="agent") is False
    assert fm.manual_halt_reason_text() == "agents_supervisor_pause"
    assert fm.clear_manual_halt_if_agent("wrong") is False
    assert fm.clear_manual_halt_if_agent("agents_supervisor_pause") is True
    assert fm.manual_halt_file_active() is False

    fm.set_manual_halt("manual pause via dashboard")
    assert fm.clear_manual_halt_if_agent("manual pause via dashboard") is False
    assert fm.manual_halt_file_active() is True


def test_set_manual_halt_if_inactive_is_exclusive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "TRADING_HALT_FILENAME", "TRADING_HALT")

    fm = fund_mod.FundManager()
    assert fm.set_manual_halt_if_inactive("operator", owner="operator") is True
    # Simulate concurrent create: O_EXCL must refuse overwrite of operator pause.
    assert fm.set_manual_halt_if_inactive("agents_supervisor_pause", owner="agent") is False
    assert fm.manual_halt_owner() == "operator"
    assert fm.manual_halt_reason_text() == "operator"


def test_set_manual_halt_default_reason_when_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "TRADING_HALT_FILENAME", "TRADING_HALT")

    fm = fund_mod.FundManager()
    fm.set_manual_halt("")
    halt_file = tmp_path / "TRADING_HALT"
    assert halt_file.is_file()
    assert halt_file.read_text(encoding="utf-8").strip() == "manual halt"


def test_set_manual_halt_then_clear(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "TRADING_HALT_FILENAME", "TRADING_HALT")

    fm = fund_mod.FundManager()
    fm.set_manual_halt("stop")
    assert fm.manual_halt_file_active() is True
    fm.clear_manual_halt()
    assert fm.manual_halt_file_active() is False


def test_manual_halt_blocks_entries_even_when_managers_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fund_mod.config, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(fund_mod.config, "TRADING_HALT_FILENAME", "TRADING_HALT")
    monkeypatch.setattr(fund_mod.config, "MANAGERS_ENABLED", False)

    fm = fund_mod.FundManager()
    assert fm.is_trading_allowed() is True
    fm.set_manual_halt("pause")
    assert fm.entry_block_reason() == "manual_halt_file"
    assert fm.is_trading_allowed() is False


def test_fund_state_with_drawdown_pct_matches_peak_last() -> None:
    from tr_bot.dashboard_api import _fund_state_with_drawdown
    from tr_bot.managers.fund import FundState

    st = FundState(
        last_nav=7989.42194,
        peak_nav=10207.747626742077,
        last_update_utc="2026-07-16T10:40:52+00:00",
        halted=False,
        halt_reason="",
    )
    out = _fund_state_with_drawdown(st)
    assert out is not None
    expected = round((st.peak_nav - st.last_nav) / st.peak_nav * 100.0, 4)
    assert abs(float(out["drawdown_pct"]) - expected) < 1e-6
    assert abs(float(out["drawdown_pct"]) - 21.7308) < 0.01

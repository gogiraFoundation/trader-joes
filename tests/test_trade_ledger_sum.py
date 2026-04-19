"""Ledger net P&L aggregation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tr_bot import config
from tr_bot.managers.fund import FundManager
from tr_bot.trade_ledger import LEDGER_FILENAME, sum_closed_net_pnl_usdt


def _write_ledger(root: Path, rows: list[dict]) -> None:
    p = root / LEDGER_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_sum_closed_net_pnl_paper_filters_live(tmp_path: Path) -> None:
    _write_ledger(
        tmp_path,
        [
            {"net_pnl_usdt": 1.0, "paper": True},
            {"net_pnl_usdt": -0.5, "paper": False},
            {"net_pnl_usdt": 0.25, "paper": True},
        ],
    )
    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)):
        assert sum_closed_net_pnl_usdt(paper=True) == pytest.approx(1.25)
        assert sum_closed_net_pnl_usdt(paper=False) == pytest.approx(-0.5)


def test_sum_legacy_rows_count_as_paper(tmp_path: Path) -> None:
    _write_ledger(
        tmp_path,
        [
            {"net_pnl_usdt": -2.0},
        ],
    )
    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)):
        assert sum_closed_net_pnl_usdt(paper=True) == pytest.approx(-2.0)
        assert sum_closed_net_pnl_usdt(paper=False) == pytest.approx(0.0)


def test_fund_paper_nav_matches_baseline_plus_ledger(tmp_path: Path) -> None:
    _write_ledger(
        tmp_path,
        [
            {"net_pnl_usdt": -2.15, "paper": True},
        ],
    )
    with patch.object(config, "ARTIFACTS_DIR", str(tmp_path)):
        with patch.object(config, "PAPER_TRADING", True):
            with patch.object(config, "PAPER_EQUITY_USDT", 100.0):
                f = FundManager()
                assert f.estimate_equity_usdt() == pytest.approx(97.85)

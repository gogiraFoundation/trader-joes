"""Filter/pagination/CSV helpers for the closed-trades ledger (dashboard Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tr_bot import trade_ledger as tl


@pytest.fixture(autouse=True)
def _artifacts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tl.config, "ARTIFACTS_DIR", str(tmp_path))
    return tmp_path


def _seed(rows: list[dict]) -> None:
    for row in rows:
        tl.append_closed_trade(row)


ROWS = [
    {
        "utc": "2026-07-01T00:00:00+00:00",
        "symbol": "BTC/USDT",
        "position_side": "long",
        "outcome": "win",
        "net_pnl_usdt": 12.5,
        "paper": True,
    },
    {
        "utc": "2026-07-02T00:00:00+00:00",
        "symbol": "ETH/USDT",
        "position_side": "short",
        "outcome": "loss",
        "net_pnl_usdt": -4.0,
        "paper": True,
    },
    {
        "utc": "2026-07-03T00:00:00+00:00",
        "symbol": "BTC/USDT",
        "position_side": "long",
        "outcome": "loss",
        "net_pnl_usdt": -1.0,
        "paper": True,
    },
]


def test_filter_trades_no_ledger_returns_empty() -> None:
    out = tl.filter_trades()
    assert out["rows"] == []
    assert out["total_matched"] == 0
    assert out["has_more"] is False


def test_filter_trades_newest_first_no_filters() -> None:
    _seed(ROWS)
    out = tl.filter_trades(limit=10)
    assert out["total_matched"] == 3
    assert [r["utc"] for r in out["rows"]] == [
        "2026-07-03T00:00:00+00:00",
        "2026-07-02T00:00:00+00:00",
        "2026-07-01T00:00:00+00:00",
    ]


def test_filter_trades_by_symbol_case_insensitive() -> None:
    _seed(ROWS)
    out = tl.filter_trades(symbol="btc/usdt", limit=10)
    assert out["total_matched"] == 2
    assert all(r["symbol"] == "BTC/USDT" for r in out["rows"])


def test_filter_trades_by_side_and_outcome() -> None:
    _seed(ROWS)
    out_side = tl.filter_trades(side="short", limit=10)
    assert out_side["total_matched"] == 1
    assert out_side["rows"][0]["symbol"] == "ETH/USDT"

    out_outcome = tl.filter_trades(outcome="loss", limit=10)
    assert out_outcome["total_matched"] == 2


def test_filter_trades_by_date_range() -> None:
    _seed(ROWS)
    out = tl.filter_trades(from_ts="2026-07-02T00:00:00+00:00", limit=10)
    assert out["total_matched"] == 2
    out2 = tl.filter_trades(to_ts="2026-07-01T23:59:59+00:00", limit=10)
    assert out2["total_matched"] == 1


def test_filter_trades_pagination() -> None:
    _seed(ROWS)
    page1 = tl.filter_trades(limit=1, offset=0)
    assert page1["has_more"] is True
    assert len(page1["rows"]) == 1
    page2 = tl.filter_trades(limit=1, offset=1)
    assert page2["has_more"] is True
    page3 = tl.filter_trades(limit=1, offset=2)
    assert page3["has_more"] is False
    assert len({page1["rows"][0]["utc"], page2["rows"][0]["utc"], page3["rows"][0]["utc"]}) == 3


def test_filter_trades_limit_is_clamped() -> None:
    _seed(ROWS)
    out = tl.filter_trades(limit=100_000)
    assert out["limit"] <= 1000


def test_filter_trades_no_match() -> None:
    _seed(ROWS)
    out = tl.filter_trades(symbol="DOGE/USDT")
    assert out["total_matched"] == 0
    assert out["rows"] == []


def test_trades_to_csv_header_and_rows() -> None:
    _seed(ROWS)
    out = tl.filter_trades(limit=10)
    csv_text = tl.trades_to_csv(out["rows"])
    lines = csv_text.strip().splitlines()
    assert lines[0] == ",".join(tl.TRADES_CSV_FIELDS)
    assert len(lines) == 1 + len(ROWS)
    assert "BTC/USDT" in csv_text


def test_append_closed_trade_stamps_profile_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tl.config, "ACTIVE_STRATEGY_CONFIG_HASH", "abc123deadbeef")
    monkeypatch.setattr(tl.config, "ACTIVE_STRATEGY_PROFILE_ID", "prof_test")
    tl.append_closed_trade(
        {
            "utc": "2026-07-16T12:00:00+00:00",
            "symbol": "BTC/USDT",
            "position_side": "long",
            "outcome": "win",
            "net_pnl_usdt": 1.0,
            "paper": True,
        }
    )
    out = tl.filter_trades(limit=1)
    row = out["rows"][0]
    assert row["profile_hash"] == "abc123deadbeef"
    assert row["profile_id"] == "prof_test"
    csv_text = tl.trades_to_csv(out["rows"])
    assert "profile_hash" in csv_text.splitlines()[0]
    assert "abc123deadbeef" in csv_text


def test_trades_to_csv_empty_rows_still_has_header() -> None:
    csv_text = tl.trades_to_csv([])
    lines = csv_text.strip().splitlines()
    assert len(lines) == 1


def test_closed_trade_stats_full_ledger_paper_and_live() -> None:
    _seed(
        [
            {
                "utc": "2026-07-01T00:00:00+00:00",
                "symbol": "BTC/USDT",
                "position_side": "long",
                "outcome": "win",
                "net_pnl_usdt": 10.0,
                "fee_usdt": 0.1,
                "paper": True,
            },
            {
                "utc": "2026-07-02T00:00:00+00:00",
                "symbol": "ETH/USDT",
                "position_side": "short",
                "outcome": "loss",
                "net_pnl_usdt": -4.0,
                "fee_usdt": 0.2,
                "paper": True,
            },
            {
                "utc": "2026-07-03T00:00:00+00:00",
                "symbol": "BTC/USDT",
                "position_side": "long",
                "outcome": "win",
                "net_pnl_usdt": 7.0,
                "fee_usdt": 0.3,
                "paper": False,
            },
        ]
    )
    paper = tl.closed_trade_stats(paper=True)
    assert paper["scope"] == "full_ledger"
    assert paper["paper_mode"] == "paper"
    assert paper["closed_trades"] == 2
    assert paper["sum_net_pnl_usdt"] == 6.0
    assert paper["avg_win_usdt"] == 10.0
    assert paper["avg_loss_usdt"] == -4.0
    assert paper["best_trade_usdt"] == 10.0
    assert paper["worst_trade_usdt"] == -4.0
    assert paper["best_trade"]["symbol"] == "BTC/USDT"
    assert paper["worst_trade"]["symbol"] == "ETH/USDT"

    live = tl.closed_trade_stats(paper=False)
    assert live["paper_mode"] == "live"
    assert live["closed_trades"] == 1
    assert live["sum_net_pnl_usdt"] == 7.0
    assert live["avg_win_usdt"] == 7.0
    assert live["avg_loss_usdt"] is None


def test_sum_closed_net_pnl_uses_closed_trade_stats_cache() -> None:
    _seed(ROWS)
    assert tl.sum_closed_net_pnl_usdt(paper=True) == 7.5
    assert tl.sum_closed_net_pnl_usdt(paper=False) == 0.0

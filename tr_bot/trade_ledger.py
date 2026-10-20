"""Append-only JSONL ledger of closed trades under ARTIFACTS_DIR."""

from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path

logger = logging.getLogger("tr_bot.trade_ledger")

LEDGER_FILENAME = "trade_ledger.jsonl"

# Bound how many trailing ledger lines a filtered query scans, so a large ledger
# can't turn a dashboard request into an unbounded full-file scan.
MAX_TRADES_SCAN_LINES = 50_000

TRADES_CSV_FIELDS = [
    "utc",
    "symbol",
    "position_side",
    "outcome",
    "entry_price",
    "exit_price",
    "size",
    "gross_pnl_usdt",
    "fee_usdt",
    "net_pnl_usdt",
    "paper",
    "profile_id",
    "profile_hash",
    "deployment_tier",
]

_STATS_CACHE_KEY: tuple[str, int, int] | None = None
_STATS_CACHE_VALUE: dict[str, dict[str, Any]] | None = None


def _ledger_path() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    return safe_artifact_path(root, LEDGER_FILENAME)


def enrich_closed_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure attribution fields are present for new hardening (profile hash, tier)."""
    out = dict(row)
    if not out.get("profile_hash"):
        out["profile_hash"] = getattr(config, "ACTIVE_STRATEGY_CONFIG_HASH", None) or None
    if not out.get("profile_id"):
        out["profile_id"] = getattr(config, "ACTIVE_STRATEGY_PROFILE_ID", None) or None
    if not out.get("deployment_tier"):
        out["deployment_tier"] = getattr(config, "DEPLOYMENT_TIER", None) or None
    return out


def append_closed_trade(row: dict[str, Any]) -> None:
    """Append one JSON object per line."""
    row = enrich_closed_trade_row(row)
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, default=str) + "\n"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("Could not append trade ledger: %s", e)


def _empty_closed_trade_stats(*, paper: bool) -> dict[str, Any]:
    return {
        "scope": "full_ledger",
        "paper_mode": "paper" if paper else "live",
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate_pct": None,
        "tp_stop_win_rate_pct": None,
        "net_positive_pct": None,
        "net_pnl_win_rate_pct": None,
        "sum_net_pnl_usdt": 0.0,
        "sum_fee_usdt": 0.0,
        "avg_win_usdt": None,
        "avg_loss_usdt": None,
        "best_trade_usdt": None,
        "worst_trade_usdt": None,
        "best_trade": None,
        "worst_trade": None,
    }


def summarize_trades(trades: list[dict[str, Any]], *, scope: str = "subset") -> dict[str, Any]:
    out = {
        "scope": scope,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate_pct": None,
        "tp_stop_win_rate_pct": None,
        "net_positive_pct": None,
        "net_pnl_win_rate_pct": None,
        "sum_net_pnl_usdt": 0.0,
        "sum_fee_usdt": 0.0,
        "avg_win_usdt": None,
        "avg_loss_usdt": None,
        "best_trade_usdt": None,
        "worst_trade_usdt": None,
        "best_trade": None,
        "worst_trade": None,
    }
    if not trades:
        return out

    wins = losses = 0
    net_sum = fee_sum = 0.0
    net_positive = 0
    win_values: list[float] = []
    loss_values: list[float] = []
    best_trade: dict[str, Any] | None = None
    worst_trade: dict[str, Any] | None = None
    best_net: float | None = None
    worst_net: float | None = None

    for t in trades:
        ctx = str(t.get("execution_context") or "STRATEGY_SIGNAL")
        # Rebalance / capital-ops closes stay in PnL sums but do not affect win/loss rates.
        strategy_outcome = not ctx.startswith("REBALANCE_")
        oc = str(t.get("outcome", "")).lower()
        if strategy_outcome:
            if oc == "win":
                wins += 1
            elif oc == "loss":
                losses += 1
        try:
            net = float(t.get("net_pnl_usdt", 0) or 0)
        except (TypeError, ValueError):
            net = 0.0
        net_sum += net
        if net > 0:
            net_positive += 1
            win_values.append(net)
        elif net < 0:
            loss_values.append(net)
        try:
            fee_sum += float(t.get("fee_usdt", 0) or 0)
        except (TypeError, ValueError):
            pass
        if best_net is None or net > best_net:
            best_net = net
            best_trade = t
        if worst_net is None or net < worst_net:
            worst_net = net
            worst_trade = t

    n = wins + losses
    nclosed = len(trades)
    wr = (wins / n * 100.0) if n else None
    np_pct = (net_positive / nclosed * 100.0) if nclosed else None
    out.update(
        {
            "closed_trades": nclosed,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wr, 2) if wr is not None else None,
            "tp_stop_win_rate_pct": round(wr, 2) if wr is not None else None,
            "net_positive_pct": round(np_pct, 2) if np_pct is not None else None,
            "net_pnl_win_rate_pct": round(np_pct, 2) if np_pct is not None else None,
            "sum_net_pnl_usdt": round(net_sum, 6),
            "sum_fee_usdt": round(fee_sum, 6),
            "avg_win_usdt": round(sum(win_values) / len(win_values), 6) if win_values else None,
            "avg_loss_usdt": round(sum(loss_values) / len(loss_values), 6) if loss_values else None,
            "best_trade_usdt": round(best_net, 6) if best_net is not None else None,
            "worst_trade_usdt": round(worst_net, 6) if worst_net is not None else None,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
        }
    )
    return out


def _load_cached_stats() -> dict[str, dict[str, Any]] | None:
    global _STATS_CACHE_KEY, _STATS_CACHE_VALUE
    path = _ledger_path()
    if not path.is_file():
        _STATS_CACHE_KEY = None
        _STATS_CACHE_VALUE = None
        return None
    try:
        st = path.stat()
    except OSError as e:
        logger.warning("Could not stat trade ledger: %s", e)
        return None
    key = (str(path), int(st.st_mtime_ns), int(st.st_size))
    if _STATS_CACHE_KEY == key and _STATS_CACHE_VALUE is not None:
        return _STATS_CACHE_VALUE
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Could not read trade ledger: %s", e)
        return None
    paper_rows: list[dict[str, Any]] = []
    live_rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("paper") is False:
            live_rows.append(row)
        else:
            paper_rows.append(row)
    _STATS_CACHE_KEY = key
    _STATS_CACHE_VALUE = {
        "paper": summarize_trades(paper_rows, scope="full_ledger"),
        "live": summarize_trades(live_rows, scope="full_ledger"),
    }
    _STATS_CACHE_VALUE["paper"]["paper_mode"] = "paper"
    _STATS_CACHE_VALUE["live"]["paper_mode"] = "live"
    return _STATS_CACHE_VALUE


def closed_trade_stats(*, paper: bool) -> dict[str, Any]:
    cached = _load_cached_stats()
    if cached is None:
        return _empty_closed_trade_stats(paper=paper)
    key = "paper" if paper else "live"
    return dict(cached.get(key) or _empty_closed_trade_stats(paper=paper))


def sum_closed_net_pnl_usdt(*, paper: bool) -> float:
    """
    Sum net_pnl_usdt over the full ledger.

    When ``paper`` is True, count rows where ``paper`` is not false (includes legacy
    rows with no ``paper`` key). When False, only rows with ``paper`` false (live).
    """
    stats = closed_trade_stats(paper=paper)
    try:
        return float(stats.get("sum_net_pnl_usdt", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def read_recent_trades(limit: int = 200) -> list[dict[str, Any]]:
    """Return up to `limit` most recent trades, newest first."""
    path = _ledger_path()
    if not path.is_file() or limit <= 0:
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Could not read trade ledger: %s", e)
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    tail = lines[-limit:]
    out: list[dict[str, Any]] = []
    for ln in reversed(tail):
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _trade_matches(
    row: dict[str, Any],
    *,
    symbol: str | None,
    side: str | None,
    outcome: str | None,
    from_ts: str | None,
    to_ts: str | None,
) -> bool:
    if symbol and str(row.get("symbol", "")).upper() != symbol.strip().upper():
        return False
    if side and str(row.get("position_side", "")).lower() != side.strip().lower():
        return False
    if outcome and str(row.get("outcome", "")).lower() != outcome.strip().lower():
        return False
    if from_ts or to_ts:
        utc = row.get("utc")
        if not isinstance(utc, str) or not utc:
            return False
        if from_ts and utc < from_ts:
            return False
        if to_ts and utc > to_ts:
            return False
    return True


def filter_trades(
    *,
    symbol: str | None = None,
    side: str | None = None,
    outcome: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Return a page of closed trades (newest first) matching filters, plus
    pagination metadata. Scans at most `MAX_TRADES_SCAN_LINES` trailing ledger
    lines so a very large ledger can't make this an unbounded scan; ISO-8601
    UTC timestamps sort/compare correctly as plain strings.
    """
    path = _ledger_path()
    empty: dict[str, Any] = {
        "rows": [],
        "total_matched": 0,
        "scanned": 0,
        "truncated": False,
        "has_more": False,
        "offset": max(0, offset),
        "limit": max(1, limit),
    }
    if not path.is_file():
        return empty
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Could not read trade ledger: %s", e)
        return {**empty, "error": str(e)}

    lines = [ln for ln in raw.splitlines() if ln.strip()]
    truncated = len(lines) > MAX_TRADES_SCAN_LINES
    scan_lines = lines[-MAX_TRADES_SCAN_LINES:] if truncated else lines

    matched: list[dict[str, Any]] = []
    for ln in reversed(scan_lines):
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if _trade_matches(row, symbol=symbol, side=side, outcome=outcome, from_ts=from_ts, to_ts=to_ts):
            matched.append(row)

    offset = max(0, offset)
    limit = max(1, min(limit, 1000))
    page = matched[offset : offset + limit]
    return {
        "rows": page,
        "total_matched": len(matched),
        "scanned": len(scan_lines),
        "truncated": truncated,
        "has_more": offset + len(page) < len(matched),
        "offset": offset,
        "limit": limit,
    }


def trades_to_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize a list of trade-ledger rows to CSV text (fixed column order)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=TRADES_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in TRADES_CSV_FIELDS})
    return buf.getvalue()

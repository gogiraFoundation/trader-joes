"""Append-only JSONL ledger of closed trades under ARTIFACTS_DIR."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path

logger = logging.getLogger("tr_bot.trade_ledger")

LEDGER_FILENAME = "trade_ledger.jsonl"


def _ledger_path() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    return safe_artifact_path(root, LEDGER_FILENAME)


def append_closed_trade(row: dict[str, Any]) -> None:
    """Append one JSON object per line."""
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, default=str) + "\n"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("Could not append trade ledger: %s", e)


def sum_closed_net_pnl_usdt(*, paper: bool) -> float:
    """
    Sum net_pnl_usdt over the full ledger.

    When ``paper`` is True, count rows where ``paper`` is not false (includes legacy
    rows with no ``paper`` key). When False, only rows with ``paper`` false (live).
    """
    path = _ledger_path()
    if not path.is_file():
        return 0.0
    total = 0.0
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rp = row.get("paper")
                if paper:
                    if rp is False:
                        continue
                else:
                    if rp is not False:
                        continue
                try:
                    total += float(row.get("net_pnl_usdt", 0) or 0)
                except (TypeError, ValueError):
                    continue
    except OSError as e:
        logger.warning("Could not sum trade ledger: %s", e)
        return 0.0
    return total


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

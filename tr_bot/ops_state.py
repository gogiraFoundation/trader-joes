"""Cross-process ops state and equity history for the dashboard (artifact files)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path

logger = logging.getLogger("tr_bot.ops")

RUNTIME_FILENAME = "bot_runtime_state.json"
EQUITY_HISTORY_FILENAME = "equity_history.json"
TRADING_SNAPSHOT_FILENAME = "trading_snapshot.json"


def _artifacts_root() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _runtime_path() -> Path:
    return safe_artifact_path(_artifacts_root(), RUNTIME_FILENAME)


def _equity_path() -> Path:
    return safe_artifact_path(_artifacts_root(), EQUITY_HISTORY_FILENAME)


def _trading_snapshot_path() -> Path:
    return safe_artifact_path(_artifacts_root(), TRADING_SNAPSHOT_FILENAME)


def write_trading_snapshot(payload: dict[str, Any]) -> None:
    """Persist last-cycle trading view for the dashboard (written by the bot process)."""
    path = _trading_snapshot_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not write trading snapshot: %s", e)


def read_trading_snapshot() -> dict[str, Any]:
    path = _trading_snapshot_path()
    if not path.is_file():
        return {
            "file_exists": False,
            "updated_utc": None,
            "by_symbol": {},
            "risk": None,
            "fund": None,
            "cmc_global": None,
            "cmc_fear_greed": None,
            "cmc_altcoin_season": None,
            "restored_from_state": False,
            "note": None,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            "file_exists": True,
            "updated_utc": raw.get("updated_utc"),
            "by_symbol": raw.get("by_symbol") or {},
            "risk": raw.get("risk"),
            "fund": raw.get("fund"),
            "cmc_global": raw.get("cmc_global"),
            "cmc_fear_greed": raw.get("cmc_fear_greed"),
            "cmc_altcoin_season": raw.get("cmc_altcoin_season"),
            "restored_from_state": bool(raw.get("restored_from_state", False)),
            "note": raw.get("note"),
        }
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read trading snapshot: %s", e)
        return {
            "file_exists": True,
            "updated_utc": None,
            "by_symbol": {},
            "risk": None,
            "fund": None,
            "cmc_global": None,
            "cmc_fear_greed": None,
            "cmc_altcoin_season": None,
            "restored_from_state": False,
            "note": str(e),
        }


def write_runtime_success() -> None:
    """Record successful loop completion; clears last_error."""
    now = datetime.now(timezone.utc).isoformat()
    path = _runtime_path()
    payload = {
        "last_loop_utc": now,
        "last_error": None,
        "last_error_utc": None,
        "pid": os.getpid(),
        "version": "tr_bot",
    }
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not write runtime state: %s", e)


def write_runtime_error(exc: BaseException) -> None:
    """Append error info; leaves last_loop_utc unchanged (last successful loop)."""
    now = datetime.now(timezone.utc).isoformat()
    msg = str(exc)[:800]
    path = _runtime_path()
    prev: dict[str, Any] = {}
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    payload = {
        **prev,
        "last_error": msg,
        "last_error_utc": now,
        "pid": os.getpid(),
        "version": "tr_bot",
    }
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not write runtime error state: %s", e)


def read_runtime_snapshot() -> dict[str, Any]:
    path = _runtime_path()
    if not path.is_file():
        return {
            "file_exists": False,
            "last_loop_utc": None,
            "last_error": None,
            "last_error_utc": None,
            "pid": None,
            "version": None,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            "file_exists": True,
            "last_loop_utc": raw.get("last_loop_utc"),
            "last_error": raw.get("last_error"),
            "last_error_utc": raw.get("last_error_utc"),
            "pid": raw.get("pid"),
            "version": raw.get("version"),
        }
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read runtime state: %s", e)
        return {
            "file_exists": True,
            "last_loop_utc": None,
            "last_error": str(e),
            "last_error_utc": None,
            "pid": None,
            "version": None,
        }


def ops_view_for_api() -> dict[str, Any]:
    """Dashboard `ops` object with heartbeat_ok and stale seconds."""
    snap = read_runtime_snapshot()
    last = snap.get("last_loop_utc")
    heartbeat_ok = False
    heartbeat_stale_seconds: float | None = None
    threshold = float(config.POLL_SECONDS * max(1, config.HEARTBEAT_STALE_POLL_MULTIPLIER))

    if isinstance(last, str) and last:
        try:
            iso = last.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            age = (now - dt.astimezone(timezone.utc)).total_seconds()
            heartbeat_stale_seconds = max(0.0, age)
            heartbeat_ok = age <= threshold
        except (TypeError, ValueError):
            heartbeat_stale_seconds = None
            heartbeat_ok = False

    return {
        **snap,
        "heartbeat_ok": heartbeat_ok,
        "heartbeat_stale_seconds": heartbeat_stale_seconds,
        "heartbeat_threshold_seconds": threshold,
    }


def append_equity_point(nav: float) -> None:
    """Append one NAV/equity sample; cap length."""
    path = _equity_path()
    points: list[dict[str, Any]] = []
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            points = list(raw.get("points") or [])
        except (OSError, json.JSONDecodeError, TypeError):
            points = []
    now = datetime.now(timezone.utc).isoformat()
    points.append({"t": now, "nav": float(nav)})
    max_pts = max(10, config.MAX_EQUITY_HISTORY_POINTS)
    points = points[-max_pts:]
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps({"points": points}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not write equity history: %s", e)


def _fallback_points_from_fund_state() -> list[dict[str, Any]]:
    """
    When equity_history.json is missing or empty, show at least one point from
    fund_state.json (same ARTIFACTS_DIR) so the dashboard chart works if the bot
    updated NAV but the API-only process never received append_equity_point writes.
    """
    try:
        p = safe_artifact_path(_artifacts_root(), "fund_state.json")
        if not p.is_file():
            return []
        raw = json.loads(p.read_text(encoding="utf-8"))
        nav = raw.get("last_nav")
        if nav is None:
            return []
        t = raw.get("last_update_utc")
        return [{"t": str(t or ""), "nav": float(nav)}]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        logger.debug("fund_state fallback for equity chart: %s", e)
        return []


def read_equity_history() -> dict[str, Any]:
    path = _equity_path()
    points: list[dict[str, Any]] = []
    err: str | None = None
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            points = list(raw.get("points") or [])
        except (OSError, json.JSONDecodeError, TypeError) as e:
            logger.warning("Could not read equity history: %s", e)
            err = str(e)
    if not points:
        fb = _fallback_points_from_fund_state()
        if fb:
            points = fb
    out: dict[str, Any] = {"points": points}
    if err:
        out["error"] = err
    return out

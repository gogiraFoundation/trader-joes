"""Circuit breaker: stop after N consecutive losses (Section 8)."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path
from tr_bot.notifications import notify
from tr_bot.notifications import events as notify_events

logger = logging.getLogger("tr_bot.circuit")

CIRCUIT_STATE_FILENAME = "circuit_state.json"


def _artifacts_root() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_path() -> Path:
    return safe_artifact_path(_artifacts_root(), CIRCUIT_STATE_FILENAME)


def read_circuit_state_snapshot() -> dict[str, Any]:
    """Read persisted circuit state for dashboard (API may run in a separate process)."""
    path = _state_path()
    if not path.is_file():
        return {
            "file_exists": False,
            "consecutive_losses": None,
            "max_consecutive_losses": config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES,
            "tripped": None,
            "updated_utc": None,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        n = int(raw["consecutive_losses"])
        m = int(raw.get("max_consecutive_losses", config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES))
        return {
            "file_exists": True,
            "consecutive_losses": n,
            "max_consecutive_losses": m,
            "tripped": n >= m,
            "updated_utc": str(raw.get("updated_utc", "")),
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.warning("Could not read circuit state: %s", e)
        return {
            "file_exists": True,
            "consecutive_losses": None,
            "max_consecutive_losses": config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES,
            "tripped": None,
            "updated_utc": None,
            "error": str(e),
        }


class CircuitBreaker:
    def __init__(self, max_consecutive_losses: int | None = None) -> None:
        self.max_consecutive_losses = (
            max_consecutive_losses or config.CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES
        )
        self.consecutive_losses = 0
        self._reset_token: str | None = None
        self._load_persisted()

    def _load_persisted(self) -> None:
        path = _state_path()
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            n = int(raw["consecutive_losses"])
            self.consecutive_losses = max(0, n)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning("Could not load circuit state: %s", e)

    def _persist(self) -> None:
        path = _state_path()
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "consecutive_losses": self.consecutive_losses,
            "max_consecutive_losses": self.max_consecutive_losses,
            "updated_utc": now,
        }
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            logger.warning("Could not persist circuit state: %s", e)

    @property
    def tripped(self) -> bool:
        self._check_reset_file()
        return self.consecutive_losses >= self.max_consecutive_losses

    def record_outcome(self, won: bool) -> None:
        if won:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            if self.consecutive_losses == self.max_consecutive_losses:
                notify(
                    notify_events.CIRCUIT_TRIPPED,
                    consecutive_losses=self.consecutive_losses,
                    max_consecutive_losses=self.max_consecutive_losses,
                )
        self._persist()

    def _check_reset_file(self) -> None:
        """Consume a nonce written to `.circuit_reset` (see ``reset_for_api``)."""
        path = _reset_file_path()
        if not path.exists():
            return
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning("Could not read circuit reset marker: %s", e)
            return
        if not token or token == self._reset_token:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        self._reset_token = token
        self.consecutive_losses = 0
        self._persist()
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not consume circuit reset marker: %s", e)

    def reset(self) -> None:
        self.consecutive_losses = 0
        self._persist()


def _reset_file_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".circuit_reset"


def reset_for_api() -> None:
    """
    Request a circuit-breaker reset from the dashboard (or CLI) process.

    Writes a unique nonce into `.circuit_reset`. The running engine's
    `CircuitBreaker` polls that marker via `_check_reset_file`, applies the
    reset, persists state, then deletes the marker so a leftover file cannot
    auto-clear the breaker on the next process start.
    """
    path = _reset_file_path()
    nonce = f"{time.time_ns()}-{uuid.uuid4().hex}"
    path.write_text(nonce + "\n", encoding="utf-8")
    logger.info("Circuit breaker reset requested (wrote nonce to %s)", path)

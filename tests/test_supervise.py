"""Supervise.sh gate helpers (no Docker daemon, no trading loop)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUPERVISE = REPO_ROOT / "scripts" / "supervise.sh"


def _bash(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(REPO_ROOT),
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


def test_require_startup_gate_accepts_passed_true(tmp_path: Path) -> None:
    gate = tmp_path / "startup_last_run.json"
    gate.write_text(json.dumps({"passed": True, "utc": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    r = _bash(
        f'source "{SUPERVISE}" && require_startup_gate "{gate}"',
    )
    assert r.returncode == 0, r.stderr


def test_require_startup_gate_rejects_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    r = _bash(
        f'source "{SUPERVISE}" && require_startup_gate "{missing}"',
    )
    assert r.returncode == 1
    assert "missing" in r.stderr.lower() or "startup gate" in r.stderr.lower()


def test_require_startup_gate_rejects_passed_false(tmp_path: Path) -> None:
    gate = tmp_path / "startup_last_run.json"
    gate.write_text(json.dumps({"passed": False, "reason": "preflight_failed"}), encoding="utf-8")
    r = _bash(
        f'source "{SUPERVISE}" && require_startup_gate "{gate}"',
    )
    assert r.returncode == 1
    assert "not passed" in r.stderr.lower()


def test_unknown_role_exits_nonzero() -> None:
    r = _bash(
        f'source "{SUPERVISE}" && main not-a-role',
    )
    assert r.returncode == 1
    assert "unknown role" in r.stderr.lower()


def test_sourcing_does_not_exec_role() -> None:
    """Sourcing must define helpers without starting api/bot."""
    r = _bash(
        f'source "{SUPERVISE}" && declare -F require_startup_gate && declare -F main',
    )
    assert r.returncode == 0, r.stderr
    assert "require_startup_gate" in r.stdout
    assert "main" in r.stdout

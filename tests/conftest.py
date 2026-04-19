"""Default env for tests (no live Redis/Postgres required for most tests)."""

from __future__ import annotations

import os

# Force isolation for the test process (overrides .env for deterministic CI)
os.environ["MANAGERS_ENABLED"] = "false"
os.environ["NOTIFY_ENABLED"] = "false"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/99"
os.environ["DATABASE_URL"] = "postgresql://invalid:invalid@127.0.0.1:65432/none"

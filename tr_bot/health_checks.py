"""Shared Redis / Postgres checks for health and dashboard APIs."""

from __future__ import annotations

import time
from typing import Any


def check_redis(redis_url: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        import redis

        r = redis.from_url(redis_url, socket_connect_timeout=1.0)
        ok = r.ping()
        ms = (time.perf_counter() - t0) * 1000.0
        return {"ok": bool(ok), "latency_ms": round(ms, 2)}
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000.0
        return {"ok": False, "error": str(e), "latency_ms": round(ms, 2)}


def overall_readiness_ok(
    redis_info: dict[str, Any],
    pg_info: dict[str, Any],
    *,
    require_postgres: bool,
) -> bool:
    """True if Redis is up and (if required) Postgres is up."""
    if not bool(redis_info.get("ok")):
        return False
    if require_postgres and not bool(pg_info.get("ok")):
        return False
    return True


def check_postgres(database_url: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        import psycopg2

        conn = psycopg2.connect(database_url, connect_timeout=1)
        conn.close()
        ms = (time.perf_counter() - t0) * 1000.0
        return {"ok": True, "latency_ms": round(ms, 2)}
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000.0
        return {"ok": False, "error": str(e), "latency_ms": round(ms, 2)}

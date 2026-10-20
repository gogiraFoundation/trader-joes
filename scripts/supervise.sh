#!/usr/bin/env bash
# Homelab process supervisor: one container role per invocation.
# Roles: startup | api | bot | scheduler
#
# Sourcing this file must not exec a process (tests call gate helpers).
set -euo pipefail

ARTIFACTS_DIR="${ARTIFACTS_DIR:-/app/artifacts}"
STARTUP_LAST_RUN="${ARTIFACTS_DIR}/deployment/startup_last_run.json"

# Docker image provides `python`; host/dev often only has `python3`.
if command -v python >/dev/null 2>&1; then
  PYTHON=python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  echo "supervise: neither python nor python3 found on PATH" >&2
  # Still allow sourcing for unknown-role tests that never call Python.
  PYTHON=""
fi

_bool_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "$v" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

_require_python() {
  if [[ -z "${PYTHON}" ]]; then
    echo "supervise: neither python nor python3 found on PATH" >&2
    return 127
  fi
}

wait_for_deps() {
  local redis_url db_url
  _require_python || return $?
  redis_url="${REDIS_URL:-redis://redis:6379/0}"
  db_url="${DATABASE_URL:-}"
  if [[ -z "$db_url" ]]; then
    echo "supervise: DATABASE_URL is required" >&2
    return 1
  fi

  echo "supervise: waiting for Redis…"
  "$PYTHON" - "$redis_url" <<'PY'
import sys, time
import redis
url = sys.argv[1]
deadline = time.time() + 120
last = None
while time.time() < deadline:
    try:
        r = redis.Redis.from_url(url, socket_connect_timeout=2)
        r.ping()
        print("supervise: Redis ready", flush=True)
        raise SystemExit(0)
    except Exception as e:
        last = e
        time.sleep(2)
print(f"supervise: Redis not ready: {last}", file=sys.stderr)
raise SystemExit(1)
PY

  echo "supervise: waiting for Postgres…"
  "$PYTHON" - "$db_url" <<'PY'
import sys, time
import psycopg2
url = sys.argv[1]
deadline = time.time() + 120
last = None
while time.time() < deadline:
    try:
        conn = psycopg2.connect(url, connect_timeout=3)
        conn.close()
        print("supervise: Postgres ready", flush=True)
        raise SystemExit(0)
    except Exception as e:
        last = e
        time.sleep(2)
print(f"supervise: Postgres not ready: {last}", file=sys.stderr)
raise SystemExit(1)
PY
}

# Exit 0 if startup gate file says passed=true; else 1.
# Exported for tests that source this script.
require_startup_gate() {
  local path="${1:-$STARTUP_LAST_RUN}"
  _require_python || return $?
  if [[ ! -f "$path" ]]; then
    echo "supervise: startup gate missing: $path" >&2
    return 1
  fi
  "$PYTHON" - "$path" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
except (OSError, json.JSONDecodeError) as e:
    print(f"supervise: cannot read startup gate: {e}", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(raw, dict) or raw.get("passed") is not True:
    print(f"supervise: startup gate not passed: {path}", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0)
PY
}

role_startup() {
  _require_python || return $?
  wait_for_deps
  echo "supervise: running startup pipeline"
  exec "$PYTHON" -m tr_bot.startup.pipeline run
}

role_api() {
  local host port
  _require_python || return $?
  host="${API_HOST:-0.0.0.0}"
  port="${API_PORT:-8080}"
  echo "supervise: starting API on ${host}:${port}"
  exec "$PYTHON" -m uvicorn tr_bot.health:app --host "$host" --port "$port"
}

role_bot() {
  _require_python || return $?
  if _bool_true "${STARTUP_AUTOMATION_ENABLED:-true}"; then
    require_startup_gate "$STARTUP_LAST_RUN" || return 1
  else
    echo "supervise: STARTUP_AUTOMATION_ENABLED off; skipping startup gate"
  fi
  echo "supervise: starting trading loop"
  exec "$PYTHON" -m tr_bot
}

role_scheduler() {
  local should
  _require_python || return $?
  should="$("$PYTHON" -m tr_bot.startup.pipeline should-launch-scheduler)"
  if [[ "$should" != "1" ]]; then
    echo "supervise: scheduler disabled (should-launch-scheduler=$should); sleeping"
    # Stay up so Compose does not restart-loop when STARTUP_LAUNCH_SCHEDULER=false.
    exec sleep infinity
  fi
  echo "supervise: starting scheduler"
  exec "$PYTHON" -m tr_bot.scheduler
}

main() {
  local role="${1:-}"
  case "$role" in
    startup) role_startup ;;
    api) role_api ;;
    bot) role_bot ;;
    scheduler) role_scheduler ;;
    *)
      echo "supervise: unknown role '${role}' (expected: startup|api|bot|scheduler)" >&2
      return 1
      ;;
  esac
}

# Only run main when executed as a script (not when sourced).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi

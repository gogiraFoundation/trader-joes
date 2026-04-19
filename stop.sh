#!/usr/bin/env bash
# Gracefully stop tr_bot processes started from this repo.
# - Stops bot loop (python -m tr_bot)
# - Stops dashboard/API (uvicorn tr_bot.health:app) if listening on API_PORT
# - Stops scheduler (python -m tr_bot.scheduler) if running
# - Optionally resets circuit breaker state (delete ARTIFACTS_DIR/circuit_state.json)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

API_PORT="${API_PORT:-8080}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-15}"

RESET_CIRCUIT="prompt" # prompt | yes | no

usage() {
  cat <<'EOF'
Usage: ./stop.sh [--reset-circuit | --no-reset] [--port N] [--timeout S]

Stops tr_bot bot + API (uvicorn) started from this repo.

Options:
  --reset-circuit   Delete ARTIFACTS_DIR/circuit_state.json after stopping
  --no-reset        Do not reset circuit breaker (default: prompt)
  --port N          API port to target (default: API_PORT env or 8080)
  --timeout S       Seconds to wait before SIGKILL (default: 15)
  --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reset-circuit)
      RESET_CIRCUIT="yes"
      shift
      ;;
    --no-reset)
      RESET_CIRCUIT="no"
      shift
      ;;
    --port)
      API_PORT="${2:-}"
      shift 2
      ;;
    --timeout)
      TIMEOUT_SECONDS="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${API_PORT}" ]] || ! [[ "${API_PORT}" =~ ^[0-9]+$ ]]; then
  echo "error: API_PORT must be numeric, got ${API_PORT@Q}" >&2
  exit 2
fi
if [[ -z "${TIMEOUT_SECONDS}" ]] || ! [[ "${TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "error: TIMEOUT_SECONDS must be integer seconds, got ${TIMEOUT_SECONDS@Q}" >&2
  exit 2
fi

cmdline_for_pid() {
  local pid="$1"
  ps -o command= -p "$pid" 2>/dev/null || true
}

pid_owned_by_me() {
  local pid="$1"
  local u
  u="$(ps -o user= -p "$pid" 2>/dev/null | awk '{$1=$1;print}' || true)"
  [[ -n "$u" ]] && [[ "$u" == "$(id -un)" ]]
}

pid_cwd_under_repo() {
  local pid="$1"
  local cwd
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | awk '/^n/{sub(/^n/,""); print; exit}' || true)"
  [[ -n "$cwd" ]] && [[ "$cwd" == "$SCRIPT_DIR"* ]]
}

is_our_bot_pid() {
  local pid="$1"
  local cmd
  cmd="$(cmdline_for_pid "$pid")"
  [[ -n "$cmd" ]] || return 1
  pid_cwd_under_repo "$pid" || return 1
  [[ "$cmd" == *".venv/bin/python"*"-m tr_bot"* ]] || return 1
  [[ "$cmd" != *"uvicorn"* ]] || return 1
  [[ "$cmd" != *"-m tr_bot.scheduler"* ]] || return 1
  return 0
}

is_our_scheduler_pid() {
  local pid="$1"
  local cmd
  cmd="$(cmdline_for_pid "$pid")"
  [[ -n "$cmd" ]] || return 1
  pid_cwd_under_repo "$pid" || return 1
  [[ "$cmd" == *".venv/bin/python"*"-m tr_bot.scheduler"* ]] || return 1
  return 0
}

is_our_api_pid() {
  local pid="$1"
  local cmd
  cmd="$(cmdline_for_pid "$pid")"
  [[ -n "$cmd" ]] || return 1
  pid_cwd_under_repo "$pid" || return 1
  [[ "$cmd" == *"uvicorn"* ]] || return 1
  [[ "$cmd" == *"tr_bot.health:app"* ]] || return 1
  return 0
}

discover_api_pid() {
  # Find the LISTEN pid on API_PORT, then validate it is our uvicorn.
  local pid
  pid="$(lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true)"
  if [[ -n "$pid" ]] && pid_owned_by_me "$pid" && is_our_api_pid "$pid"; then
    echo "$pid"
  fi
}

discover_pids_by_pgrep() {
  # Use pgrep with a narrow match, then validate each candidate.
  local pattern="$1"
  pgrep -f "$pattern" 2>/dev/null || true
}

discover_bot_pids() {
  local pids=()
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    if pid_owned_by_me "$pid" && is_our_bot_pid "$pid"; then
      pids+=("$pid")
    fi
  done < <(discover_pids_by_pgrep "$SCRIPT_DIR/.venv/bin/python -m tr_bot")
  printf '%s\n' "${pids[@]:-}"
}

discover_scheduler_pids() {
  local pids=()
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    if pid_owned_by_me "$pid" && is_our_scheduler_pid "$pid"; then
      pids+=("$pid")
    fi
  done < <(discover_pids_by_pgrep "$SCRIPT_DIR/.venv/bin/python -m tr_bot.scheduler")
  printf '%s\n' "${pids[@]:-}"
}

wait_for_exit_or_kill() {
  local pid="$1"
  local timeout="$2"
  local name="$3"

  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi

  echo "Stopping $name (pid=$pid)…"
  kill -TERM "$pid" 2>/dev/null || true

  local t=0
  while kill -0 "$pid" 2>/dev/null; do
    if (( t >= timeout )); then
      echo "Escalating: SIGKILL $name (pid=$pid) after ${timeout}s"
      kill -KILL "$pid" 2>/dev/null || true
      break
    fi
    sleep 1
    ((t+=1))
  done
}

reset_circuit_breaker() {
  local artifacts_dir=""

  # Prefer the repo venv python to resolve ARTIFACTS_DIR the same way as the bot.
  if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    artifacts_dir="$("$SCRIPT_DIR/.venv/bin/python" -c "from tr_bot import config; import pathlib; print(pathlib.Path(config.ARTIFACTS_DIR).resolve())" 2>/dev/null || true)"
  fi
  if [[ -z "$artifacts_dir" ]]; then
    artifacts_dir="$("$SCRIPT_DIR/.venv/bin/python3" -c "from tr_bot import config; import pathlib; print(pathlib.Path(config.ARTIFACTS_DIR).resolve())" 2>/dev/null || true)"
  fi
  if [[ -z "$artifacts_dir" ]]; then
    artifacts_dir="$SCRIPT_DIR/artifacts"
  fi

  local p="$artifacts_dir/circuit_state.json"
  if [[ -f "$p" ]]; then
    rm -f "$p"
    echo "Circuit breaker reset: removed $p"
  else
    echo "Circuit breaker reset: no file at $p"
  fi
}

API_PID="$(discover_api_pid || true)"
BOT_PIDS="$(discover_bot_pids || true)"
SCHED_PIDS="$(discover_scheduler_pids || true)"

if [[ -z "${API_PID}" ]] && [[ -z "${BOT_PIDS}" ]] && [[ -z "${SCHED_PIDS}" ]]; then
  echo "No tr_bot processes found for this repo (port=$API_PORT)."
else
  # Stop bot(s) first, then API, then scheduler.
  for pid in $BOT_PIDS; do
    wait_for_exit_or_kill "$pid" "$TIMEOUT_SECONDS" "bot"
  done

  if [[ -n "${API_PID}" ]]; then
    wait_for_exit_or_kill "$API_PID" "$TIMEOUT_SECONDS" "api"
  fi

  for pid in $SCHED_PIDS; do
    wait_for_exit_or_kill "$pid" "$TIMEOUT_SECONDS" "scheduler"
  done
fi

if [[ "$RESET_CIRCUIT" == "prompt" ]]; then
  read -r -p "Reset circuit breaker (delete circuit_state.json)? [y/N] " ans || true
  # Portable lowercase conversion (avoid bash-specific ${var,,} in mixed shells)
  ans_lc="$(printf '%s' "${ans:-}" | tr '[:upper:]' '[:lower:]')"
  if [[ "$ans_lc" == "y" ]] || [[ "$ans_lc" == "yes" ]]; then
    RESET_CIRCUIT="yes"
  else
    RESET_CIRCUIT="no"
  fi
fi

if [[ "$RESET_CIRCUIT" == "yes" ]]; then
  reset_circuit_breaker
fi

echo "Done."


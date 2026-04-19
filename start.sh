#!/usr/bin/env bash
# Setup venv + deps, then start one of:
#   ./start.sh | ./start.sh --bot     trading loop (default)
#   ./start.sh --api | --dashboard    FastAPI (health + dashboard UI + /api/dashboard)
#   ./start.sh --all | --both         API in background + trading loop in foreground
#   ./start.sh --scheduler            Periodic training + backtest (see SCHEDULER_* in .env)
#
# Optional env: API_HOST (default 0.0.0.0), API_PORT (default 8080)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="bot"
while [[ $# -gt 0 ]]; do
  case $1 in
    --api|--dashboard)
      MODE="api"
      ;;
    --bot)
      MODE="bot"
      ;;
    --all|--both)
      MODE="all"
      ;;
    --scheduler)
      MODE="scheduler"
      ;;
    --help|-h)
      echo "Usage: $0 [--bot | --api | --dashboard | --all | --both | --scheduler]"
      echo "  --bot          Run trading loop (default)"
      echo "  --api          Run FastAPI (uvicorn): /dashboard, /api/dashboard/summary, /health, /docs"
      echo "  --all / --both Run startup pipeline, then API+scheduler in background, trading loop in foreground"
      echo "  --scheduler    Run python -m tr_bot.scheduler (training/backtest/promotion intervals from .env)"
      exit 0
      ;;
    *)
      echo "error: unknown option: $1 (try --help)" >&2
      exit 1
      ;;
  esac
  shift
done

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    echo "Created .env from .env.example — edit it for secrets and settings before production use."
  else
    echo "error: .env is missing and .env.example was not found" >&2
    exit 1
  fi
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

# Quiet installs; suppress “pip upgrade available” spam
export PIP_DISABLE_PIP_VERSION_CHECK=1
.venv/bin/pip install -q -r requirements.txt

# API bind (export before running, or add API_HOST/API_PORT to your environment)
API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8080}"
# Human-friendly URL (avoid 0.0.0.0 in the browser)
OPEN_URL="http://127.0.0.1:${API_PORT}"
UVICORN=(.venv/bin/uvicorn tr_bot.health:app --host "$API_HOST" --port "$API_PORT")

SCHED_PID_FILE="${SCRIPT_DIR}/artifacts/deployment/scheduler.pid"

run_startup_pipeline() {
  local t0 t1 dt
  t0="$(date +%s)"
  echo "Startup pipeline: preflight (incl. CMC/train consistency) -> training -> tuning -> backtest gate"
  echo "  CMC knobs: .env + docs/operations.md (CoinMarketCap); preflight JSON includes \"cmc\" + warnings."
  .venv/bin/python -m tr_bot.startup.pipeline run
  t1="$(date +%s)"
  dt="$((t1 - t0))"
  echo "Startup pipeline passed in ${dt}s"
}

maybe_start_scheduler_bg() {
  local should
  should="$(.venv/bin/python -m tr_bot.startup.pipeline should-launch-scheduler)"
  if [[ "$should" != "1" ]]; then
    return 0
  fi

  mkdir -p "$(dirname "$SCHED_PID_FILE")"
  if [[ -f "$SCHED_PID_FILE" ]]; then
    local existing
    existing="$(cat "$SCHED_PID_FILE" 2>/dev/null || true)"
    if [[ -n "${existing:-}" ]] && kill -0 "$existing" 2>/dev/null; then
      echo "Scheduler already running (pid=$existing); reusing existing process"
      return 0
    fi
    rm -f "$SCHED_PID_FILE"
  fi

  echo "Scheduler: starting in background"
  .venv/bin/python -m tr_bot.scheduler &
  SCHED_PID=$!
  echo "$SCHED_PID" > "$SCHED_PID_FILE"
}

case "$MODE" in
  scheduler)
    echo "Scheduler: training/backtest/promotion per SCHEDULER_* env (see README)"
    exec .venv/bin/python -m tr_bot.scheduler
    ;;
  api)
    echo "Dashboard: ${OPEN_URL}/  ·  API docs: ${OPEN_URL}/docs"
    echo "Listening on ${API_HOST}:${API_PORT}"
    exec "${UVICORN[@]}"
    ;;
  all)
    if [[ "${STARTUP_AUTOMATION_ENABLED:-true}" =~ ^(1|true|yes|on)$ ]]; then
      run_startup_pipeline
    else
      echo "Startup pipeline disabled by STARTUP_AUTOMATION_ENABLED"
    fi

    echo "API: listening ${API_HOST}:${API_PORT} — open ${OPEN_URL}/ (dashboard) · ${OPEN_URL}/docs"
    "${UVICORN[@]}" &
    API_PID=$!

    SCHED_PID=""
    maybe_start_scheduler_bg

    cleanup() {
      if [[ -n "${SCHED_PID:-}" ]]; then
        if kill -0 "$SCHED_PID" 2>/dev/null; then
          kill "$SCHED_PID" 2>/dev/null || true
        fi
      fi
      rm -f "$SCHED_PID_FILE"

      if kill -0 "$API_PID" 2>/dev/null; then
        kill "$API_PID" 2>/dev/null || true
      fi
    }
    trap cleanup EXIT INT TERM

    echo "Trading loop starting (Ctrl+C stops bot, API and scheduler)…"
    .venv/bin/python -m tr_bot
    ;;
  bot|*)
    exec .venv/bin/python -m tr_bot
    ;;
esac

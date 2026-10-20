---
name: automation
description: >-
  Run and change this repo's automation: start.sh --all, the hard-block
  startup pipeline, the scheduler, no-trade adaptation, the in-loop exit
  watchdog, and dashboard background jobs. Use when the user asks about
  startup, scheduling, cron, unattended train or backtest, auto-apply
  profiles, or what runs without a manual command. Do not put order entry
  on the scheduler.
---

# Automation

Three processes stay separate. The trading loop (`python -m tr_bot`) decides and orders. The scheduler (`python -m tr_bot.scheduler`) trains, backtests, evaluates promotion, adapts, and writes reports. The API (`tr_bot.health:app`) serves the dashboard. None of them is a substitute for the others.

`./start.sh --bot` is the loop only. `--api` is the API only. `--scheduler` is the scheduler only. `--all` / `--both` runs the startup pipeline, then the API and maybe the scheduler in the background, then the loop in the foreground. Ctrl+C stops all three. `set -euo pipefail` in `start.sh` means a failed pipeline command never reaches the loop.

The CoinDesk poller is its own process: `python -m tr_bot.coindesk`. The scheduler only refreshes the asset top-list snapshot. Do not fold the poller into the scheduler tick.

If you cite an outside orchestrator (cron, systemd, a workflow engine), follow `.cursor/skills/research-web/SKILL.md` and say how it maps onto the processes below. Do not add a second scheduler.

## Startup pipeline

Owner: `tr_bot/startup/pipeline.py`. Shell entry: `run_startup_pipeline` in `start.sh`, which runs `python -m tr_bot.startup.pipeline run`.

`STARTUP_AUTOMATION_ENABLED` defaults to on in the shell (`:-true`). Set it false to skip the pipeline and still launch API and loop. That is the only skip. A stage failure returns exit code 1, `start.sh` aborts, and the bot does not start.

Stages, in order. The first failure sets `reason` and returns. Later stages do not run.

| Stage | What runs | Failure reason |
| --- | --- | --- |
| preflight | `run_preflight_checks` | `preflight_failed` |
| train | `train-run --compact` with `TRAIN_VALIDATION_STRICT=true` and `TRAIN_BACKTEST_GATE=false` | `training_failed` |
| training validation | OOF F1 and train/val gap vs `STARTUP_MIN_OOF_F1`, `STARTUP_MAX_TRAIN_VAL_GAP` | `training_validation_gate_failed` |
| tune | `scripts/sweep_best_fit_settings.py --limit STARTUP_TUNING_LIMIT --emit-profile`, then an isolated backtest | `tuning_failed` |
| backtest | `backtest-run --compact` on the active profile | `backtest_failed` |
| backtest gate | return, drawdown, trade count | `backtest_gate_failed` |

Preflight `ok` is false only when `errors` is non-empty. `cmc:*` lines are warnings. They print on stderr and do not fail the stage. Writable `ARTIFACTS_DIR`, a known deployment tier, `RISK_PER_TRADE > 0`, and `MAX_TRADES_PER_DAY > 0` are errors.

Training retry: if the first train fails with "Not enough rows after indicators" or stdout says "Using cached training dataset", one retry runs with `TRAIN_DATASET_MODE=fetch_only`. No third attempt.

Tuning is on unless `STARTUP_RUN_TUNING=false` (then the stage is `{ok: true, skipped: true}`). Default limit 12, timeout 900s, isolated backtest timeout 600s. Exit code 124 is a timeout. It still fails the pipeline unless `STARTUP_TUNING_TIMEOUT_SOFT_FAIL=true`, which marks the stage skipped and continues. A tuned profile is committed to the **paper** pointer only after `evaluate_backtest_gate` passes (`startup_register_paper_candidate`). A failed gate appends `startup_candidate_rejected` and does not move the pointer. Startup never writes `live_canary` or `live_full`.

Backtest gate defaults (`tr_bot/startup/backtest_gate_thresholds.py`): min return **−1%**, max drawdown **12%**, min trades **3**. Env: `STARTUP_BACKTEST_MIN_RETURN_PCT`, `STARTUP_BACKTEST_MAX_DD_PCT`, `STARTUP_BACKTEST_MIN_TRADES`. Zero-trade profiles fail. This gate is separate from `PROMOTION_MIN_TRADES`.

The train stage forces `TRAIN_BACKTEST_GATE=false` because the pipeline runs its own backtest afterward. Standalone `train-run` and the scheduler still honor `TRAIN_BACKTEST_*`.

Success and failure both write `artifacts/deployment/startup_last_run.json`. Failure also appends `action=startup_pipeline` to the promotion log and notifies `BOT_ERROR`. Notify failures must not change the exit code.

`python -m tr_bot.startup.pipeline should-launch-scheduler` prints `1` when `STARTUP_LAUNCH_SCHEDULER` is true (default true). `--all` starts the scheduler only then, and reuses `artifacts/deployment/scheduler.pid` if that pid is alive.

## Scheduler

Owner: `tr_bot/scheduler.py`. One thread lock. If a job holds it, the next due job logs `Skip <name>: another scheduler job is running` and is not queued. The interval clock still advances, so a skipped job waits another full interval.

A job is armed only when its interval is **greater than 0**. `SCHEDULER_ENABLED=true` with every interval at 0 keeps the process alive and runs nothing. Interval `0` is off, not "every tick".

| Job | Interval env | On-start flag | Function | What it must not do |
| --- | --- | --- | --- | --- |
| training | `SCHEDULER_TRAINING_INTERVAL_SECONDS` | `SCHEDULER_RUN_TRAINING_ON_START` | `run_training_job()` | Promote the model unless `TRAINING_PROMOTE_ON_SUCCESS=true` (default false) |
| backtest | `SCHEDULER_BACKTEST_INTERVAL_SECONDS` | `SCHEDULER_RUN_BACKTEST_ON_START` | `run_backtest_job` | Place orders |
| promotion evaluate | `SCHEDULER_PROMOTION_INTERVAL_SECONDS` | `SCHEDULER_RUN_PROMOTION_ON_START` | `strategy_evaluate` | Promote or move a pointer. Evaluate only |
| adaptation | `ADAPTATION_INTERVAL_SECONDS` | `SCHEDULER_RUN_ADAPTATION_ON_START` | `run_adaptation_job` | See adaptation rules below |
| agents | `SCHEDULER_AGENTS_INTERVAL_SECONDS` | `SCHEDULER_RUN_AGENTS_ON_START` | `run_agents_job()` | Place orders, `strategy-promote`, or rebalance trim. Requires `AGENTS_ENABLED`. Default `AGENTS_DRY_RUN=true` (briefs only). |
| CoinDesk top list | `SCHEDULER_COINDESK_ASSET_TOP_LIST_INTERVAL_SECONDS` | `SCHEDULER_RUN_COINDESK_ASSET_TOP_LIST_ON_START` | `refresh_asset_top_list_snapshot` | Trade |
| daily report | `SCHEDULER_DAILY_REPORT_INTERVAL_SECONDS` | `SCHEDULER_RUN_DAILY_REPORT_ON_START` | `run_daily_report_job` for **yesterday UTC**, preflight off | Trade |

Defaults: every interval `0`, every on-start flag false, `SCHEDULER_ENABLED` false, tick 30s (minimum 5), initial delay 60s. The delay happens before on-start jobs.

On-start runs only when that job's interval is also `> 0`. After on-start, `last_*` is set to now. Jobs that did not run on start also get `last_*=now` before the loop, so the first tick does not fire them immediately. That avoids a duplicate of `--all`.

A job exception is logged and the lock is released. The scheduler keeps looping.

New periodic work: add an interval, an on-start flag, a `_run_locked` call, and the same `last_* = now` seeding. Do not call the trading loop, `execute_trade`, or `strategy-promote` from the scheduler.

## No-trade adaptation

Owner: `tr_bot/strategy/no_trade_adaptation.py`. The loop records consecutive loops with no open via `record_loop_without_open`. The scheduler (or `adaptation-run`) consumes that state.

Skips, all `ok: true` unless noted:

- `ADAPTATION_ENABLED=false` (default)
- `STRATEGY_ENGINE=smc` (`strategy_engine_smc`). Shadow and legacy may adapt
- streak below `ADAPTATION_MIN_LOOPS_NO_OPEN` (default 48)
- inside `ADAPTATION_COOLDOWN_SECONDS` (default 86400)
- no decision-audit JSONL (`ok: false`, `no_audit_file`)
- histogram has no mappable dominant reason (`no_param_delta`)

A candidate profile id is `adapt_<UTC timestamp>`. It is backtest-gated before any pointer write. `ADAPTATION_AUTO_APPLY_PAPER=true` (default) may commit the **paper** pointer. Any non-paper tier, or paper with auto-apply off, writes `artifacts/.../proposals/` only. Adaptation does not promote to `live_canary` or `live_full`.

`proposal-apply` is the manual path for a proposal file. It is not a scheduler job.

## Inside the trading loop

These are not scheduler jobs. Do not move them out of the loop.

- Poll: `run_forever` sleeps `POLL_SECONDS` via `sleep_with_exit_watchdog`.
- Exit watchdog (`tr_bot/exit_watchdog.py`) checks stops about every `EXIT_WATCHDOG_INTERVAL_S` (default 5s, minimum 1s) between polls. It closes through `ExitCoordinator`. It does not open trades.
- Decision audit, equity points, and optional Postgres no-trade rows are best-effort. A write failure does not stop the loop.

## Dashboard jobs

Owner: `tr_bot/dashboard_jobs.py`, started from `tr_bot/dashboard_actions.py`. One job at a time. A second start raises `JobAlreadyRunningError`. Status moves `queued` → `running` → `done` or `error`. Records are persisted under the artifacts jobs directory. Threads are daemon threads: process exit drops a running job.

Kinds today: `backtest`, `training` (body may set `promote`), `backtest_compare`.

Writes stay off until `DASHBOARD_WRITES_ENABLED=true` and a write token or `DASHBOARD_TOKEN` is set. Missing opt-in is 403. A bad token is 401. A new button calls the same function as the CLI (`run_training_job`, `run_backtest_job`), not a second implementation. It does not bypass promotion freeze or the paper-only startup pointer rule.

## Changes

- New stage: add it in `run_startup_pipeline` with a `reason` string, a stage dict, and an early return. Cover pass and fail in `tests/test_startup_pipeline.py` or `tests/test_startup_transactional.py`. A failed stage must not launch the bot when hard-block is in effect.
- New scheduled job: interval default `0`, on-start default false, single-flight via `_run_locked`, no orders. Test due/skip behavior in `tests/test_scheduler.py`.
- Adaptation behavior: `tests/test_no_trade_adaptation.py`. Dashboard jobs: `tests/test_dashboard_jobs.py`. Preflight errors vs warnings: `tests/test_startup_preflight.py`.
- Do not start the loop from a partial `ok` flag. `out["ok"]` is set only after the backtest gate passes.

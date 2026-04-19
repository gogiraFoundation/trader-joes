# Manager CLI

Entry point:

```bash
python -m tr_bot.managers --help
```

## Commands

## `train-run`

Run offline regime training and emit artifacts under `ARTIFACTS_DIR/training`.

```bash
python -m tr_bot.managers train-run
python -m tr_bot.managers train-run --compact
```

- Default output: JSON payload.
- `--compact`: concise summary with rows, sources, validation status, and key artifact paths.

## `backtest-run`

Run walk-forward simulation on configured symbol/timeframe.

```bash
python -m tr_bot.managers backtest-run
python -m tr_bot.managers backtest-run --compact
```

- Default output: JSON payload.
- `--compact`: concise summary with return, max drawdown, trade counts, bars, and benchmark when available.

## `fund-snapshot`

Capture or print fund state snapshot used by dashboard and ops tooling.

```bash
python -m tr_bot.managers fund-snapshot
```

## `advise-refresh`

Runs advisory flow (if configured) without changing exchange/account state.

```bash
python -m tr_bot.managers advise-refresh
```

## `strategy-status`

Deployment tier pointers (`artifacts/deployment/*_active.json`), promotion freeze state, and tail of `artifacts/promotion/log.jsonl`.

```bash
python -m tr_bot.managers strategy-status
python -m tr_bot.managers strategy-status --compact
```

## `strategy-evaluate`

Run promotion gates against the current ledger and equity history (default profile: active profile id or `ACTIVE_STRATEGY_PROFILE_ID`).

```bash
python -m tr_bot.managers strategy-evaluate
python -m tr_bot.managers strategy-evaluate --profile-id my_profile --tier paper --compact
```

- Exit code `0` if all gates pass, `1` otherwise.

## `strategy-promote`

Validate gates, then atomically write a deployment pointer for the selected tier.

```bash
python -m tr_bot.managers strategy-promote --to-tier paper --profile-id default_v1
python -m tr_bot.managers strategy-promote --to-tier live_canary --profile-id default_v1 --dry-run
```

## `strategy-revert`

Restore the last successful `profile_id` from the promotion log to a tier pointer (profile file must still exist).

```bash
python -m tr_bot.managers strategy-revert --to-tier live_canary
```

## Startup hard-block commands

Used by `./start.sh --all`:

```bash
python -m tr_bot.startup.preflight --compact
python -m tr_bot.startup.pipeline run
python -m tr_bot.startup.pipeline should-launch-scheduler
```


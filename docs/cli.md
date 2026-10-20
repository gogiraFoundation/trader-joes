# Manager CLI

Entry point:

```bash
python -m tr_bot.managers --help
```

Most commands print JSON unless `--compact` is supported and passed. Exit codes: `0` success; non-zero on failure (rebalance-trim returns `2` when trims are planned but `REBALANCER_EXECUTE_TRIMS=false`).

## Fund and rebalance

### `fund-snapshot`

Compute NAV / drawdown and persist `artifacts/fund_state.json`.

```bash
python -m tr_bot.managers fund-snapshot
```

### `rebalance-scan`

Phase A1: holdings drift / lag scan → `artifacts/rebalancer/latest.json` (no orders).

```bash
python -m tr_bot.managers rebalance-scan
```

### `rebalance-trim`

Phase A2: scan + trim when `REBALANCER_EXECUTE_TRIMS=true`; otherwise prints a plan and exits `2`.

```bash
python -m tr_bot.managers rebalance-trim
```

See [`operations.md`](operations.md#portfolio-rebalancer-drift--lag-trim).

## Training and backtest

### `train-run`

Offline regime training under `ARTIFACTS_DIR/training`.

```bash
python -m tr_bot.managers train-run
python -m tr_bot.managers train-run --compact
python -m tr_bot.managers train-run --promote   # copy to REGIME_MODEL_PATH when gates pass
python -m tr_bot.managers train-run --output /path/to/model.joblib
```

### `backtest-run`

Walk-forward simulation on configured symbol / timeframe.

```bash
python -m tr_bot.managers backtest-run
python -m tr_bot.managers backtest-run --compact
```

### `backtest-compare`

Run legacy vs SMC backtests on the same bars; write compare + go/no-go artifacts (`artifacts/training/backtest_compare.json`, `artifacts/research/smc_go_no_go.json`).

```bash
python -m tr_bot.managers backtest-compare
python -m tr_bot.managers backtest-compare --compact
```

### `adaptation-run`

No-trade meta-tuning: audit histogram → bounded param delta → isolated backtest gate → profile or proposal.

```bash
python -m tr_bot.managers adaptation-run
python -m tr_bot.managers adaptation-run --compact
```

## Strategy promotion

### `strategy-status`

Deployment pointers, freeze state, promotion log tail.

```bash
python -m tr_bot.managers strategy-status
python -m tr_bot.managers strategy-status --compact
```

### `strategy-evaluate`

Run promotion gates (exit `0` if all pass, `1` otherwise).

```bash
python -m tr_bot.managers strategy-evaluate
python -m tr_bot.managers strategy-evaluate --profile-id my_profile --tier paper --compact
```

### `strategy-promote`

Validate gates, then atomically write a deployment pointer.

```bash
python -m tr_bot.managers strategy-promote --to-tier paper --profile-id default_v1
python -m tr_bot.managers strategy-promote --to-tier live_canary --profile-id default_v1 --dry-run
```

### `strategy-revert`

Restore the last successful `profile_id` from the promotion log (profile file must still exist).

```bash
python -m tr_bot.managers strategy-revert --to-tier live_canary
```

### `strategy-compare`

Comparator wrapper (legacy vs SMC) for promotion evidence. When `PROMOTION_REQUIRE_COMPARATOR=true`, promote paths expect this evidence.

```bash
python -m tr_bot.managers strategy-compare
python -m tr_bot.managers strategy-compare --compact
```

Full promotion runbook: [`strategy_promotion.md`](strategy_promotion.md).

## Agents and proposals

### `advise-refresh`

Refresh Redis advisory for all active symbols (`USE_LLM` must be true).

```bash
python -m tr_bot.managers advise-refresh
```

### `agents-run`

Supervisor + Fund Manager (advisory). `--dry-run` forces advise-only. Mutating runs still require `AGENTS_ENABLED=true` and the relevant `AGENTS_AUTO_*` flags.

```bash
python -m tr_bot.managers agents-run --dry-run
python -m tr_bot.managers agents-run --role supervisor --compact
python -m tr_bot.managers agents-run --role fund_manager
```

### `proposal-apply`

Validate LLM proposal JSON, run adaptation-style backtest gate, write proposal or apply on paper.

```bash
python -m tr_bot.managers proposal-apply --file path/to/proposal.json
python -m tr_bot.managers proposal-apply --file path/to/proposal.json --compact
```

## Research and reports

### `research-query`

Fixed ledger/query templates: `losing_smc` | `high_score_wins` | `recent`.

```bash
python -m tr_bot.managers research-query recent --limit 50
python -m tr_bot.managers research-query high_score_wins --min-score 80
```

### `research-agent`

Offline research agent: `performance` | `structure` | `smc`.

```bash
python -m tr_bot.managers research-agent performance
python -m tr_bot.managers research-agent smc --note "post-soak check"
```

### `daily-report`

Deterministic JSON + Markdown under `artifacts/reports/`.

```bash
python -m tr_bot.managers daily-report
python -m tr_bot.managers daily-report --date 2026-10-13 --preflight --compact
```

## Startup hard-block (used by `./start.sh --all`)

```bash
python -m tr_bot.startup.preflight --compact
python -m tr_bot.startup.pipeline run
python -m tr_bot.startup.pipeline should-launch-scheduler
```

Narrative: [`operations.md`](operations.md#hard-block-startup-pipeline---all).

## Related scripts (not manager subcommands)

### Fee/R universe bake-off

```bash
.venv/bin/python scripts/compare_fee_r_universes.py
.venv/bin/python scripts/compare_fee_r_universes.py --dry-run
```

Forces soak cost knobs in-process and ranks BTC/ETH/SOL @ 1h by profit factor after costs → `artifacts/research/fee_r_universe_compare.json`. See [`operations.md`](operations.md#feer-soak-1h--costs--single-open).

### CoinDesk poller

```bash
.venv/bin/python -m tr_bot.coindesk
```

Separate long-running process; not started by `./start.sh --all`.

# Strategy promotion (operator runbook)

This project supports **versioned strategy profiles** under `ARTIFACTS_DIR/strategy_profiles/<profile_id>.json`, **deployment tier pointers** under `artifacts/deployment/`, and an **append-only promotion log** at `artifacts/promotion/log.jsonl`.

## Tiers

| Tier         | Execution                         | Pointer file                 |
| ------------ | --------------------------------- | ---------------------------- |
| `paper`      | Simulated orders (`PAPER_TRADING`) | `paper_active.json`        |
| `live_canary` | Live, with canary caps            | `live_canary_active.json`    |
| `live_full`  | Live, profile + env caps          | `live_full_active.json`      |

Set `DEPLOYMENT_TIER` in the environment; if unset, it is inferred from `PAPER_TRADING`.

**Multi-process topology:** Run separate bot processes for paper soak vs live (e.g. compose services) sharing `ARTIFACTS_DIR` but different `DEPLOYMENT_TIER` / `PAPER_TRADING`. Do not assume one process holds both paper and live state.

## Freeze promotions

- **Env:** `PROMOTION_FROZEN=true`
- **File:** `artifacts/deployment/freeze_promotions` (empty file is enough)

While frozen, `strategy-promote` refuses and records a rejection.

## Rollback

1. **Manual halt:** `TRADING_HALT_FILENAME` (default `TRADING_HALTED`) and fund halt rules still apply.
2. **Promotion revert:** `python -m tr_bot.managers strategy-revert --to-tier live_canary` restores the last `profile_id` from a successful `promote` line in `promotion/log.jsonl` (if the profile file still exists).

## CLI

`python -m tr_bot.managers strategy-status` — pointers, freeze state, log tail.

`python -m tr_bot.managers strategy-evaluate` — run gate catalog (exit 1 if any gate fails).

`python -m tr_bot.managers strategy-promote --to-tier paper --profile-id <id>` — gates + atomic pointer write.

`python -m tr_bot.managers strategy-revert --to-tier <tier>` — restore last good profile from log.

`python -m tr_bot.managers strategy-compare` — legacy vs SMC comparator evidence for promotion.

`python -m tr_bot.managers backtest-compare` — full walk-forward compare; writes `artifacts/training/backtest_compare.json` and `artifacts/research/smc_go_no_go.json`.

## Gates (env)

Configure thresholds via `.env`. Hardened [`.env.example`](../.env.example) soak floors:

| Knob | Example value | Notes |
|------|---------------|-------|
| `PROMOTION_MIN_TRADES` | `50` | Closed trades attributed to the profile |
| `PROMOTION_MIN_CALENDAR_DAYS` | `14` | Soak calendar span |
| `PROMOTION_MAX_EQUITY_DRAWDOWN_PCT` | `0.25` | vs `equity_history.json` NAV series |
| `PROMOTION_MIN_PROFIT_FACTOR` | `1.15` | Optional; unset disables in code |
| `PROMOTION_STRICT_LEDGER_FILTER` | `true` | Tighten ledger filtering used by soak stats |
| `PROMOTION_COOLDOWN_HOURS` | `168` | Minimum spacing after a successful promotion |
| `PROMOTION_REQUIRE_COMPARATOR` | `true` | Require comparator / go-no-go evidence before promote succeeds |
| `PROMOTION_FROZEN` | `false` | Or touch `artifacts/deployment/freeze_promotions` |

Also: `PROMOTION_BREAK_COOLDOWN=true` bypasses cooldown (use carefully). Optional bootstrap / expectancy knobs live in `tr_bot/config.py` (`PROMOTION_BOOTSTRAP_*`, `PROMOTION_MIN_EXPECTANCY_USDT`).

Prefer paper soak evidence before `live_canary`, then canary caps before `live_full`. Startup backtest gates (`STARTUP_BACKTEST_*`) are a separate, usually looser smoke check — do not confuse them with promotion soak floors.

Dashboard write path: `POST /api/dashboard/actions/strategy-promote` (writes enabled + token; live promote needs `confirm: true`). See [`dashboard.md`](dashboard.md).

## Strategy engine and promotion evidence

When evaluating SMC for promotion:

1. Soak with `STRATEGY_ENGINE=shadow` (or paper `smc`) and collect ledger + decision-audit dual views.
2. Run `backtest-compare` / `strategy-compare` and inspect `smc_go_no_go.json`.
3. Promote only when soak gates and (if enabled) comparator requirements pass.
4. Kill switch: set `STRATEGY_ENGINE=legacy` plus halt / freeze as needed.

## Audit trail

Each promotion attempt should append to `artifacts/promotion/log.jsonl` with `action`, `passed`, `profile_id`, and `to_tier`. Closed trades in `trade_ledger.jsonl` may include `profile_id`, `deployment_tier`, `regime_at_entry`, and `signal_branch` for explainability.

## References

- Decision audit JSONL schema: `tr_bot/strategy/decision_audit.py`
- Comparator: `tr_bot/promotion/comparator.py`, stats in `tr_bot/promotion/stats.py`
- Canary caps: `CANARY_*` in `.env.example`
- Operator narrative: [`operations.md`](operations.md#strategy-engine-hiatp--smc-path)

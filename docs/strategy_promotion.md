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

## Gates (env)

Configure thresholds via `.env`:

- `PROMOTION_MIN_TRADES`
- `PROMOTION_MIN_CALENDAR_DAYS`
- `PROMOTION_MAX_EQUITY_DRAWDOWN_PCT` (vs `equity_history.json` NAV series)
- `PROMOTION_MIN_PROFIT_FACTOR` (optional; unset disables)
- `PROMOTION_COOLDOWN_HOURS` — minimum spacing after a successful promotion
- `PROMOTION_BREAK_COOLDOWN=true` — bypass cooldown (use carefully)

## Audit trail

Each promotion attempt should append to `artifacts/promotion/log.jsonl` with `action`, `passed`, `profile_id`, and `to_tier`. Closed trades in `trade_ledger.jsonl` may include `profile_id`, `deployment_tier`, `regime_at_entry`, and `signal_branch` for explainability.

## References

- Decision audit JSONL schema: `tr_bot/strategy/decision_audit.py`
- Canary caps: `CANARY_*` in `.env.example`

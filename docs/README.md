# Documentation Index

Project docs for runtime operations, dashboard features, manager CLI, and strategy promotion.

## Start here

| Doc | Use when |
|-----|----------|
| [`README.md`](../README.md) | Install, run, configure, Docker, high-level architecture |
| [`skills.md`](../skills.md) | Changing trading / risk / exits / training / promotion / dashboard code — module ownership and invariants |
| [`AGENTS.md`](../AGENTS.md) | Learned operator preferences and homelab facts |
| [`.env.example`](../.env.example) | Authoritative soak-oriented env knobs (stricter than some code defaults) |

## Guides

- [`dashboard.md`](dashboard.md) — Summary API, alerts, write actions, jobs, P&L, `DASHBOARD_UI` v1/v2 (Overview / Metrics / Risk / Strategy / Jobs).
- [`cli.md`](cli.md) — `python -m tr_bot.managers` commands (`train-run`, `backtest-compare`, `agents-run`, rebalance, research, …).
- [`operations.md`](operations.md) — Day-2 runbook: start/stop, Compose + Pi LAN, paper vs live, fee/R soak, agents, rebalancer, CMC/CoinDesk, troubleshooting.
- [`strategy_promotion.md`](strategy_promotion.md) — Strategy profiles, deployment tiers, soak gates, comparator, promote/revert.

## Data references

- [`coindesk_reference.md`](coindesk_reference.md) — CoinDesk / market-data reference notes.
- [`coindesk_asset_catalog.md`](coindesk_asset_catalog.md) — Asset catalog used with CoinDesk-related tooling.

## Related paths

- `deploy/pi/nginx-trbot.lan.conf` — Example Pi nginx reverse proxy for `trbot.lan`.
- `scripts/supervise.sh` — Docker entrypoint (`startup` \| `api` \| `bot` \| `scheduler`).
- `scripts/compare_fee_r_universes.py` — Fee/R universe bake-off (BTC/ETH/SOL @ 1h).
- `.cursor/skills/` — Cursor agent skills aligned with `skills.md`.

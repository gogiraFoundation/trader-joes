## Learned User Preferences

- Prefers a refine-plan-then-implement workflow; when a plan is attached for implementation, do not edit the plan file itself.
- Keep LLM agent mutates (pause/resume/reset/proposals/rebalance) gated off by default; agents must not overwrite or clear a human/dashboard pause.
- Prefer always-on homelab deploy with LAN-only dashboard access (Pi-hole/nginx hostname); do not expose the bot directly to the public internet.
- Treat Binance spot fees realistically (~10 bps taker unless a BNB discount applies); judge edges with fee/R and move-vs-cost (ATR), not liquidity narratives alone.
- Prefer paper soak and promotion-gate evidence before live; ask for controlled knob changes aimed at positive PnL rather than casually resetting risk halts.
- Uses `skills.md` and `.cursor/skills/` as architecture/process guidance for backend, dashboard, research, and automation work.
- Often pastes live dashboard snapshots for diagnosis—answer against current paper/risk/promotion state, not abstract advice.

## Learned Workspace Facts

- `tr_bot` is a Python trading stack with paper/live tiers, Redis + Postgres, and a dashboard API typically on port 8080.
- Homelab deploy: app on `gogir-server` under `/mnt/data/apps/tr_bot`; shared compose/nginx under `/mnt/data/compose`; Pi (`gogir-labs`) provides Pi-hole DNS for `trbot.lan`.
- Agents (Grok/xAI supervisor + fund manager) live under `tr_bot/agents/` and stay inactive unless `AGENTS_*` env flags are set.
- `scripts/supervise.sh` waits for Redis/Postgres, then runs the startup pipeline before bringing services up.
- Regime training should use exchange OHLCV (e.g. 5m) when aligning the classifier with backtest microstructure; Yahoo daily alone is a common mismatch.
- Promotion soak gates commonly include `min_trades`, `min_calendar_days`, and `min_profit_factor` before leaving paper.
- Dashboard write actions require `DASHBOARD_WRITES_ENABLED` and, when configured, a write token.
- Project domain skills live in `.cursor/skills/` (fundamentals, technicals, quant, research-*, decision-generator, automation).

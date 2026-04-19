# Dashboard Reference

## API endpoint

- **Summary payload:** `GET /api/dashboard/summary`
- **Implementation:** `tr_bot/dashboard_api.py`
- **Auth:** If `DASHBOARD_TOKEN` is set in the environment, requests must send either:
  - `Authorization: Bearer <token>`, or
  - `X-Dashboard-Token: <token>`

## Core sections

- Ops heartbeat + dependency checks (`redis`, `postgres`).
- Equity/NAV sparkline from `artifacts/equity_history.json`.
- Trading snapshot from `artifacts/trading_snapshot.json` (via `read_trading_snapshot()`).
- Training and backtest status from:
  - `artifacts/training/last_run.json`
  - `artifacts/training/backtest_last_run.json`
- **Sanitized config** under `config` in the summary JSON (symbol, risk, microstructure, deployment tier, etc.).

### Effective tier overrides (ops)

The summary includes **`config.effective_tier_overrides`** (same structure as `tr_bot.config.effective_tier_overrides()`):

| Field | Meaning |
|-------|---------|
| `deployment_tier` | `paper` \| `live_canary` \| `live_full` |
| `env_before_tier_apply` | Values after strategy profile overlay, **before** tier floor enforcement |
| `effective_after_tier` | Values **after** `live_canary` / `live_full` minimums (e.g. `TRAIN_MIN_OOF_F1`, `TRAIN_MAX_TRAIN_VAL_GAP`, `BACKTEST_MIN_RISK_METRIC_OBS`, microstructure / spread-depth / `RESTORE_RECONCILE_REQUIRE_ORDER_ID`) |

Use this to see **true floors**, not only raw `.env` strings.

### Backtest config snapshot

Under `backtest.config`, the API exposes selected **`BACKTEST_*`** knobs (bars, fees, execution/exit realism, `BACKTEST_MIN_RISK_METRIC_OBS`, `BACKTEST_ANNUAL_GAP_MULTIPLIER`, `BACKTEST_EXIT_DELAY_FILL_NEXT_BAR`, etc.). See `tr_bot/dashboard_api.py` → `_backtest_payload()` for the full list.

## P&L & cost analysis section

Rendered in `tr_bot/static/dashboard/index.html` via `renderPnl(data.pnl)`.
Payload is built by `tr_bot/dashboard_pnl.py` → `build_pnl_dashboard_payload()`.

### Data inputs

- Equity window rollups: `read_equity_history()`
- Baseline comparison: fund/equity + `PAPER_EQUITY_USDT`
- Backtest row: enriched last backtest (`enrich_backtest_last_run_for_dashboard`)
- Closed trades: `artifacts/trade_ledger.jsonl`
- **`effective_tier_overrides`** — duplicated here for convenience (same as `config.effective_tier_overrides` in the summary).

### Trade summary (`pnl.trade_summary`)

Aggregates recent ledger rows. Two win-rate concepts:

| Field | Meaning |
|-------|---------|
| `tp_stop_win_rate_pct` | Outcome-based: `outcome == win` vs `win`+`loss` (TP/stop labeling from `OpenPosition.check_bar`) |
| `win_rate_pct` | Alias of `tp_stop_win_rate_pct` (backward compatible) |
| `net_pnl_win_rate_pct` | Share of closed trades with `net_pnl_usdt > 0` after fees |
| `net_positive_pct` | Alias of `net_pnl_win_rate_pct` |

Also: `closed_trades`, `wins`, `losses`, `sum_net_pnl_usdt`, `sum_fee_usdt`.

### Backtest row enrichment

When the last backtest JSON is present, the PnL section can show return, drawdown, Sharpe/Sortino, and **`risk_metric_meta`**:

- **`published`** — Sharpe/Sortino shown only when enough return observations (`BACKTEST_MIN_RISK_METRIC_OBS` gate).
- **`annualization`** — `source`, `steps_per_year_assumption`, gap detection fields (`gap_interval_count`, `regular_interval_count`, …), optional **`fallback_note`** for irregular bar spacing.
- **`comparison_guardrails`** — Human-readable strings (e.g. insufficient observations, annualization caveats). Built in `dashboard_pnl.py` from `risk_metric_meta`.

### Closed trades table

Rows are written on every successful position close in `tr_bot/engine.py`.
Expected columns:

- `utc`, `symbol`, `position_side`, `outcome`, `paper`
- `entry_price`, `exit_price`, `size`
- `gross_pnl_usdt`, `fee_usdt`, `net_pnl_usdt`

### Cost assumptions shown in UI

- `LIVE_FEE_BPS`
- `LIVE_SLIPPAGE_BPS`
- `BACKTEST_FEE_BPS`
- `BACKTEST_SLIPPAGE_BPS`

## Notes

- In paper mode, values are simulation estimates (not exchange-confirmed fills).
- If the closed-trades table is empty, no position has closed yet or the ledger path differs from the running process (`ARTIFACTS_DIR`).
- Live exit **economics** may differ from walk-forward backtest (see README §4.7): intrabar TP/stop detection vs PnL assumptions. Compare Sharpe/profit factor across runs with the same scaling and policy flags.

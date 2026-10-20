# Dashboard Reference

Operator UI and summary API for artifacts under `ARTIFACTS_DIR`. Install and process model: [`README.md`](../README.md). Day-2 ops: [`operations.md`](operations.md).

## API endpoint

- **Summary payload:** `GET /api/dashboard/summary`
- **Implementation:** `tr_bot/dashboard_api.py`
- **Auth:** If `DASHBOARD_TOKEN` is set in the environment, requests must send either:
  - `Authorization: Bearer <token>`, or
  - `X-Dashboard-Token: <token>`

## Core sections

- Ops heartbeat + dependency checks (`redis`, `postgres`).
- **Alerts** (`alerts`, see below) — critical/warning banner entries for the top of Main.
- Equity/NAV sparkline from `artifacts/equity_history.json` (Main renders the **filtered** `equity_history_display` series — see below).
- Trading snapshot from `artifacts/trading_snapshot.json` (via `read_trading_snapshot()`).
- Training and backtest status from:
  - `artifacts/training/last_run.json`
  - `artifacts/training/backtest_last_run.json`
- **Sanitized config** under `config` in the summary JSON (symbol, risk, microstructure, deployment tier, etc.).

### Alerts (`alerts`)

`GET /api/dashboard/summary` includes an `alerts` array, derived by `tr_bot/dashboard_alerts.py` (`build_alerts()`, pure function — see `tests/test_dashboard_alerts.py`). Each entry has:

| Field | Meaning |
|-------|---------|
| `id` | Stable identifier: `circuit_tripped`, `trading_halted`, `nav_drawdown`, `agents_critical`, `gates_failed`, `heartbeat_stale` |
| `severity` | `critical` \| `warning` \| `info` (list is sorted critical-first) |
| `message` | Human-readable summary |
| `href` | Deep link to the relevant page (e.g. `/dashboard/risk.html`) |
| `details` | Optional multiline detail text for the v2 expandable alert surface |

Rendered on Main v1 as a sticky bar above the page content, and on Main v2 as an expandable alert strip with a details panel and deep links. Empty list ⇒ bar stays hidden. This enriches the existing summary payload rather than adding a new endpoint — no separate `/api/dashboard/alerts` route exists or is planned.

### Equity series: raw vs display

- `equity_history` — raw persisted samples from `artifacts/equity_history.json` (may include stale/corrupt outlier NAVs).
- `equity_history_display` — same series with `_filter_equity_points_for_rollups()` outlier filtering applied (`outliers_dropped` count included). **Main and Metrics both chart this series** so the sparkline never contradicts the P&L/performance cards (which already used the filtered series via `pnl.equity_window`).
- `metrics.drawdown_history` — fund-peak-anchored drawdown % series for Metrics v2 (running peak seeded from `fund.state.peak_nav` so chart DD matches live fund drawdown even when equity history never recorded the peak).

### Equity/NAV chart (Chart.js)

Main's "Equity / NAV" card renders `equity_history_display.points` on a
Chart.js line chart (`#chart-equity`) instead of the old hand-rolled SVG
sparkline, matching the charting library and hover-tooltip UX already used on
Metrics (`chart.js@4.4.1` from the same CDN). Hovering a point shows the exact
UTC timestamp and NAV value; the "no samples yet" empty state still applies
when `equity_history_display.points` is empty.

### Strategy engine card + Compare-to-SMC modal

Main shows a compact "Strategy engine" card (`renderStrategyEngine()`) reading
`strategy_engine_status` from the summary payload:

- Engine mode (`legacy` \| `shadow` \| `smc`), `smc_min_trade_score`, `exit_mode`.
- **Shadow agreement pill** — only shown when `strategy_engine == "shadow"` and
  `shadow_agreement` is present (`{pairs, agree, rate}` computed from the last
  ~30 decision-audit lines); green at ≥80% agreement, amber below.
- **"View compare" button** opens a modal (`#compare-modal`) showing the cached
  `backtest_compare` (legacy vs SMC returns) and `smc_go_no_go` verdict from
  `artifacts/training/backtest_compare.json` / `artifacts/research/smc_go_no_go.json`,
  plus the raw JSON for inspection. This is read-only against already-cached
  artifacts — no live run.

## Write actions (Phase 3)

Everything below is **disabled by default**. Enable deliberately:

```bash
DASHBOARD_WRITES_ENABLED=true
# Optional: separate token for write actions (falls back to DASHBOARD_TOKEN if unset).
# At least one write-capable token is required whenever writes are enabled.
DASHBOARD_WRITE_TOKEN=some-other-secret
```

- **Gate:** `tr_bot.dashboard_actions.require_dashboard_writes` — `403` if
  `DASHBOARD_WRITES_ENABLED` is false; also `403` if writes are enabled but
  neither `DASHBOARD_WRITE_TOKEN` nor `DASHBOARD_TOKEN` is configured;
  otherwise `401` on a token mismatch
  (checks `DASHBOARD_WRITE_TOKEN`, falling back to `DASHBOARD_TOKEN`, same
  `Authorization: Bearer <token>` / `X-Dashboard-Token: <token>` header shapes
  as the read-only gate).
- **Router:** `tr_bot/dashboard_actions.py`, mounted in `tr_bot/health.py`.
- Main only shows the "Quick actions" bar and the "Reset circuit breaker"
  button on Risk when `config.dashboard_writes_enabled` is true in the summary
  payload — the backend gate is authoritative either way.

### Immediate actions (no job queue)

| Route | Effect |
|-------|--------|
| `POST /api/dashboard/actions/circuit-reset` | Writes a nonce into `.circuit_reset` at the repo root — the same cross-process marker `CircuitBreaker._check_reset_file()` polls on every `.tripped` check (see `tr_bot/circuit_breaker.py::reset_for_api()`). The engine resets+persists then deletes the marker so a leftover file cannot auto-clear on restart. The dashboard reflects the reset on its next poll, not instantly. |
| `POST /api/dashboard/actions/trading-pause` `{"reason": "..."}` | `FundManager.set_manual_halt(reason)` — writes the manual halt file (`TRADING_HALT_FILENAME`) the engine already checks each loop. |
| `POST /api/dashboard/actions/trading-resume` | `FundManager.clear_manual_halt()` — removes the halt file. |
| `POST /api/dashboard/actions/strategy-promote` `{"to_tier", "profile_id", "dry_run", "confirm"}` | Calls `tr_bot.promotion.service.strategy_promote()` directly (fast; no job needed). `dry_run` defaults to `true`. A **live** promotion (`dry_run: false`) additionally requires `confirm: true`, else `400`. |

### Background jobs (queue depth 1)

`POST /api/dashboard/actions/backtest-run`, `.../training-run`
(`{"promote": true|false|null}`), and `.../backtest-compare` each start a
background job via `tr_bot/dashboard_jobs.py` and return `{id, kind, status, ...}`
immediately (status typically already `"running"` by the time the HTTP
response is built, since the job thread starts synchronously).

- **Queue depth 1:** only one dashboard-triggered job may be queued/running at
  a time; a second call while one is in flight gets `409` with the running
  job's id/kind in the error detail.
- **Poll:** `GET /api/dashboard/jobs/{id}` → `{id, kind, status, created_utc,
  started_utc, finished_utc, result, error}` (`status`: `queued` \| `running`
  \| `done` \| `error`). `GET /api/dashboard/jobs/current` returns the most
  recently started job (any status), or `null` if none has run yet — useful
  for resuming a poll after a page reload.
- **Persistence:** job records are also written to
  `ARTIFACTS_DIR/dashboard_jobs/<id>.json` as a simple audit trail. Scheduling
  state itself is in-memory (a dashboard API process restart naturally ends any
  in-flight background thread too — there's nothing to resume).
- Main's Quick Actions bar disables all `.qa-btn` buttons while a job is in
  flight and shows live status via `TRDashboard.postAction()` +
  `TRDashboard.pollJob()` (`tr_bot/static/dashboard/js/common.js`).
- Main v2 and Risk wrap write actions in a shared confirmation modal helper
  (`TRDashboard.confirmAction(...)`) before calling the write endpoints.

### Dashboard UI rollback flag (`DASHBOARD_UI`)

- `DASHBOARD_UI=v1` (default) — serves `tr_bot/static/dashboard/index.html` at `/` and `metrics.html` at `/dashboard/metrics.html`.
- `DASHBOARD_UI=v2` — serves `index.v2.html` / `metrics.v2.html` when present.
- `DASHBOARD_UI=v3` — serves `index.v3.html` / `metrics.v3.html` when present (Operator Command Console).
- **Fallback chain:** requested version → older → v1 (e.g. v3 missing → v2 → v1). Resolution: `tr_bot.health._dashboard_index_path()` / `_dashboard_metrics_path()`.
- Summary `config.dashboard_ui` echoes the effective flag value.
- Strategy/Risk/Scheduler static pages are unaffected by content versioning; they share rail chrome via `TRDashboard.mountHeader`.

### Main v2 Overview layout (`index.v2.html`)

When `DASHBOARD_UI=v2`, Overview is ordered for operator triage:

1. Sticky alerts, then a 12-column hero: NAV chart (≥280px, 8 cols) + last loop / circuit / halt / open positions (4 cols).
2. Compact strategy-engine strip with compare modal.
3. Performance Live / Backtest tabs (`js/overview-core.js` for headline math). Fee assumptions live on the Backtest tab.
4. Closed trades table (`GET /api/dashboard/trades`).
5. Trading snapshot + compact portfolio / risk-loop cards.
6. Deep links to Strategy, Jobs (`scheduler.html`), Diagnostics (Metrics), and Risk — training, promotion, best-fit, and startup cards are no longer duplicated on Overview.

### Main v3 Overview layout (`index.v3.html`)

Same triage order as v2, with Operator Command Console chrome:

1. Left rail nav + top status bar (all pages via `mountHeader`).
2. Sticky alerts, then a **KPI strip** (NAV, drawdown, circuit, halt, open positions, last loop) and a **full-width** NAV chart.
3. Strategy strip → Performance tabs → Closed trades → Snapshot (unchanged contracts / element ids from v2).

Shared chrome: `dashboard.css` tokens + rail layout, vendored Chart.js / Tailwind under `static/dashboard/vendor/`. Pause and circuit-reset use `.btn-danger`.

Frontend pure helpers: `node --test tests/frontend/metrics-core.test.js tests/frontend/overview-core.test.js`.

### Metrics page (`/dashboard/metrics.html`)

**v1** (`metrics.html`) — early Chart.js prototype (NAV/DD/deps/risk/backtest rolling samples).

**v2** (`metrics.v2.html` + `js/metrics-core.js` + `js/metrics.js`) — diagnostic cockpit:

| Widget | Data |
|--------|------|
| NAV chart (7d) | `equity_history_display.points`, baseline from `config.paper_equity_usdt` |
| Drawdown chart | `metrics.drawdown_history` (fund-peak-anchored); current value from `fund.state.drawdown_pct` |
| Trade activity | `GET /api/dashboard/trades?from=&to=&limit=1000` — daily counts + cumulative net P&L |
| Rolling performance | `pnl.recent_trades` windows 25/50/100; NAV daily Sharpe when ≥5 returns |
| Dependencies | `dependencies.*.latency_ms` with &lt;10 / 10–50 / &gt;50 ms severity + 24h sparkline |
| Circuit & limits | `circuit`, `trading.risk`, `fund.halt_file` / `fund.state.halted` |
| Backtest card | `backtest.last_run` + `/api/dashboard/jobs/current`; **View compare** uses cached `strategy_engine_status.backtest_compare` via shared `TRDashboard.wireCompareModal` |

`MAX_EQUITY_HISTORY_POINTS` (default **2016**) caps persisted NAV samples (~7 days at `POLL_SECONDS=300`). The Metrics UI still filters by a 7-day timestamp window.

`GET /api/dashboard/summary` includes:

- `fund.state.drawdown_pct` — authoritative positive % from peak/last NAV
- `metrics.drawdown_history` — chart series with `source: "fund_peak_anchored"`

Frontend pure helpers are covered by `node --test tests/frontend/metrics-core.test.js`.

### Shared frontend module (`static/dashboard/js/common.js`)

All 5 dashboard pages share the same auth-gate markup (`#auth-gate`, `#token-input`, `#token-save`) and `#error-banner`. `common.js` centralizes:

- `TRDashboard.fetchDashboardJson(url)` — attaches the bearer token from `sessionStorage`, shows the auth gate on 401, throws on other non-OK responses.
- `TRDashboard.postAction(url, body)` — POSTs JSON with the same auth headers; shows the auth gate on 401; throws an `Error` (with `.status`/`.payload`) whose message is the API's `detail` field for any other non-OK response (403 disabled, 401 bad token, 409 job conflict, 400 validation, etc).
- `TRDashboard.downloadDashboardFile(url, filename)` — authenticated blob download used by the filtered trades CSV export.
- `TRDashboard.confirmAction(opts)` — shared confirmation modal helper used by Main v2 and Risk before destructive/sensitive write actions.
- `TRDashboard.setTabPanels(tabs, panels, activeKey)` — small accessible tab helper used by Main v2's Live/Backtest switcher.
- `TRDashboard.pollJob(jobId, {onUpdate, intervalMs, timeoutMs})` — polls `GET /api/dashboard/jobs/{id}` until `status` is `done`/`error` (default 1.5s interval, 10 min timeout), calling `onUpdate(job)` after every poll; resolves with the final job record.
- `TRDashboard.wireTokenGate(onSaved)` / `TRDashboard.startPolling(loadFn, intervalMs)` — token save + 20s poll loop.
- `TRDashboard.fmtUtc(iso)`, `TRDashboard.escapeHtml(value)`, `TRDashboard.pillStatus(elId, data)`.
- `TRDashboard.markActiveNav()` / `TRDashboard.mountHeader(opts)` — shared header chrome (Overview / Risk / Strategy / Jobs / Diagnostics) and active-nav highlighting via `data-nav`.
- `TRDashboard.setDialogOpen` — dialog open/close with focus return for confirm and compare modals.
- `TRDashboard.renderCompareModal` / `openCompareModal` / `closeCompareModal` / `wireCompareModal` — shared Legacy vs SMC compare modal used by Main and Metrics v2.

Include it before a page's own `<script>` block: `<script src="/dashboard/js/common.js"></script>`. `metrics.html` (v1) keeps its own `fmtUtc(tsMs)` (epoch-ms, used for Chart.js tick labels) since it has a different signature from the ISO-string `fmtUtc` used elsewhere.

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

### Optional summary sections

`GET /api/dashboard/summary?sections=circuit,config,fund` returns only the named
keys (plus always `status` and `dependencies`). Omit `sections` (or pass empty)
for the full payload. Unknown names return `400`.

Typical page subsets:

| Page | `sections` |
|------|------------|
| Overview v2 | `alerts,ops,equity_history_display,fund,pnl,trading,config,circuit,strategy_engine_status,portfolio,backtest,agents` (`backtest` feeds the Performance Backtest tab; `alerts` still builds promotion gates internally without returning `strategy_promotion`; `agents` is last Supervisor/Fund Manager briefs) |
| Metrics v2 | `metrics,equity_history_display,fund,pnl,backtest,circuit,config,strategy_engine_status,trading` (`trading` feeds the circuit/limits card) |
| Strategy | `strategy_promotion,strategy_engine_status,config` |
| Risk | `circuit,config,fund` |
| Jobs | `scheduler,training,backtest` |
| Main / Metrics v1 | (none — full payload) |

### Background jobs / stale after API restart

`GET /api/dashboard/jobs/current` prefers the in-memory job. If memory is empty
but the newest `ARTIFACTS_DIR/dashboard_jobs/*.json` is still `queued` or
`running`, the response includes `stale: true` (the thread was lost on restart;
the UI should warn, not resume).

## P&L & cost analysis section

Rendered on the main dashboard (`index.html` or `index.v2.html` via `DASHBOARD_UI`) through `renderPnl(data.pnl)`.
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

### Full-ledger closed-trade stats (`pnl.closed_trade_stats`)

`pnl.closed_trade_stats` is the authoritative aggregate for the active paper/live
mode, computed from the full ledger with an mtime/size cache in
`tr_bot.trade_ledger.closed_trade_stats()`:

- `scope` (`full_ledger`) and `paper_mode` (`paper` or `live`)
- `closed_trades`, `wins`, `losses`
- `sum_net_pnl_usdt`, `sum_fee_usdt`
- `win_rate_pct` / `net_positive_pct`
- `avg_win_usdt`, `avg_loss_usdt`
- `best_trade_usdt`, `worst_trade_usdt`
- `best_trade`, `worst_trade` (the corresponding ledger rows)

Main v2 uses this for the Live tab so the headline realized performance is not
quietly derived from a truncated recent-trades subset.

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

Main's "Closed trades" widget no longer reads the unfiltered `pnl.recent_trades`
snapshot for its table body — it calls **`GET /api/dashboard/trades`** (below)
with the current filter/pagination state, so the table, page info, and CSV
export link all stay in sync with user-applied filters.

#### Filtered/paginated trades endpoint (`GET /api/dashboard/trades`)

- **Query params:** `symbol`, `side` (`long`/`short`), `outcome` (`win`/`loss`),
  `from`/`to` (ISO-8601 UTC strings, inclusive), `limit` (default 50, max 1000),
  `offset` (default 0), `format=csv` for a CSV download instead of JSON.
- **Implementation:** `tr_bot.trade_ledger.filter_trades()` — scans at most the
  `MAX_TRADES_SCAN_LINES` (50k) most recent ledger lines newest-first, so a very
  large ledger can't turn a dashboard request into an unbounded scan. Response
  includes `total_matched`, `scanned`, `truncated`, `has_more`, `offset`, `limit`.
- **Auth:** same `DASHBOARD_TOKEN` gate as `/api/dashboard/summary` (read-only,
  not gated by `DASHBOARD_WRITES_ENABLED`).
- **CSV export:** `GET /api/dashboard/trades?format=csv&limit=1000` returns
  `text/csv` with a fixed column order (`tr_bot.trade_ledger.TRADES_CSV_FIELDS`)
  and an `attachment; filename=trades.csv` header. The "Export CSV" button on
  Main mirrors the current filters and downloads through an authenticated fetch,
  so it also works when `DASHBOARD_TOKEN` is configured.

### Cost assumptions shown in UI

- `LIVE_FEE_BPS`
- `LIVE_SLIPPAGE_BPS`
- `BACKTEST_FEE_BPS`
- `BACKTEST_SLIPPAGE_BPS`

## Notes

- In paper mode, values are simulation estimates (not exchange-confirmed fills).
- If the closed-trades table is empty, no position has closed yet or the ledger path differs from the running process (`ARTIFACTS_DIR`).
- Live exit **economics** may differ from walk-forward backtest (see README §4.7): intrabar TP/stop detection vs PnL assumptions. Compare Sharpe/profit factor across runs with the same scaling and policy flags.
- Homelab LAN access (`PUBLIC_BASE_URL=http://trbot.lan`, Pi nginx): [`operations.md`](operations.md#lan-access-via-pi-hole--nginx-not-the-internet). Keep the dashboard off the public internet; set a long `DASHBOARD_TOKEN` before LAN exposure.
- Agents briefs appear under summary section `agents` when `AGENTS_ENABLED` runs have produced `artifacts/agents/*_last.json`. Mutating agent actions stay off unless `AGENTS_AUTO_*` flags are set (see [`operations.md`](operations.md#grok--llm-agents-supervisor--fund-manager)).

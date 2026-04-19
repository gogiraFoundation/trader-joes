# Operations Runbook

## Start services

```bash
./start.sh
./start.sh --api
./start.sh --scheduler
```

- Bot loop and API can run independently.
- Scheduler is optional and does not replace the trading loop.


## Hard-block startup pipeline (`--all`)

`./start.sh --all` runs a mandatory startup pipeline before trading starts:

1. preflight checks (`python -m tr_bot.startup.preflight`) — includes **CoinMarketCap / training consistency** (`cmc` in the JSON plus `cmc:*` warnings). `./start.sh --all` runs `python -m tr_bot.startup.pipeline run`, which prints `cmc:*` warnings to **stderr** before the full pipeline JSON.
2. offline training (`python -m tr_bot.managers train-run --compact`)
3. bounded tuning (`scripts/sweep_best_fit_settings.py --emit-profile`)
4. walk-forward backtest + startup gate
5. launch API + trading loop, and optionally scheduler in background

If any stage fails and `STARTUP_HARD_BLOCK=true`, trading does not start.

Key knobs are in `.env.example` under `STARTUP_*`.

## Stop services

```bash
./stop.sh
```

`stop.sh` prompt handling is shell-portable and safe across bash/zsh/sh environments.

## Paper vs live

- `PAPER_TRADING=true` (default): simulated execution; no live orders.
- `PAPER_TRADING=false`: live mode; requires valid exchange credentials and symbol format.

Important: in paper mode, the exchange client does not attach API keys to avoid private signed endpoint calls.

Taker fee / backtest cost precedence (`LIVE_FEE_*`, `BACKTEST_*`, BNB retail defaults) is documented in `.env.example` and implemented in `tr_bot.fees`.

## Training anti-overfitting controls

Common knobs in `.env`:

- `TRAIN_RUN_VALIDATION`
- `TRAIN_TS_SPLITS`
- `TRAIN_MIN_ROWS_PER_FOLD`
- `TRAIN_MIN_OOF_F1`
- `TRAIN_MAX_TRAIN_VAL_GAP`
- `TRAIN_VALIDATION_STRICT`
- `TRAIN_VALIDATION_PER_GROUP`
- `TRAIN_BACKTEST_GATE`
- `TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT`
- `TRAIN_BACKTEST_MAX_DRAWDOWN_PCT`

When a Yahoo Finance ticker fails during offline training, the pipeline can skip that symbol only; scheduler logs include **`[yahoo_symbol_skipped] ticker=...`** (unless `YAHOO_TRAIN_FAIL_ON_SKIP=true`).

When `TRAIN_BACKTEST_GATE=true`, set `TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT` and/or `TRAIN_BACKTEST_MAX_DRAWDOWN_PCT` to override. If both are unset, the train gate uses **−1% min return** and **12% max drawdown** (same module as startup: `tr_bot/startup/backtest_gate_thresholds.py`). The train gate does not check trade count.

**Startup / adaptation backtest gate** (`STARTUP_BACKTEST_*`, optional `ADAPT_*` overrides): defaults are **−1% min return**, **12% max drawdown**, **0 min trades** (no-fill windows still pass). Tighten with e.g. `STARTUP_BACKTEST_MIN_RETURN_PCT=-0.5` or `0`; require activity with `STARTUP_BACKTEST_MIN_TRADES=3`.

If strict mode is enabled and gates fail, training exits with a runtime error by design.

## CoinMarketCap (training and live)

- **Training source**: Add `coinmarketcap` to `TRAIN_SOURCES` when CMC aggregate OHLCV or a specific listed asset adds value beyond Yahoo or your exchange feed. Prefer exchange or Yahoo for bars that should track execution venues closely.
- **Stable identity**: Prefer [`CMC_TRAIN_IDS`](../tr_bot/config.py) (comma-separated CMC numeric ids) or `CMC_TRAIN_SLUGS` over raw symbols. Symbols are ambiguous; with `CMC_TRAIN_SYMBOLS` only, keep `CMC_MAP_RESOLVE_SYMBOLS=true` (default) so [`tr_bot/training/cmc.py`](../tr_bot/training/cmc.py) resolves once per run via `/v1/cryptocurrency/map`.
- **Credits**: Use a single `CMC_CONVERT` (extra quote currencies cost more credits). Tune `CMC_COUNT` to the history you need, and use cached training datasets (`TRAIN_DATASET_MODE`, `TRAIN_DATASET_MAX_AGE_SECONDS`) so full OHLCV pulls are not repeated on every retrain.
- **Live macro**: [`tr_bot/analysis/cmc_global.py`](../tr_bot/analysis/cmc_global.py) calls `/v1/global-metrics/quotes/latest` with TTL `CMC_GLOBAL_METRICS_TTL_S` (default 300s). Optional `/v3/fear-and-greed/latest` and `/v1/altcoin-season-index/latest` are gated by `CMC_FEAR_GREED_ENABLED` / `CMC_ALTCOIN_SEASON_ENABLED` and use TTL ≥ 900s. Do not put `/v1/global-metrics/quotes/historical` on the live trading hot path.
- **Training macro merge**: When `CMC_TRAIN_MACRO_MERGE_ENABLED=true`, [`tr_bot/training/cmc_macro.py`](../tr_bot/training/cmc_macro.py) fetches batch macro history and merges onto all training rows using **backward** `merge_asof` on `timestamp` (each bar only sees the last published point at or before that time). Optional `CMC_TRAIN_MACRO_PARQUET_PATH` writes the merged frame under `ARTIFACTS_DIR`. If `TRAIN_REGIME_MACRO_FEATURES=true`, the regime classifier uses the added `cmc_macro_*` columns; the live loop must supply those same columns on the decision dataframe or prediction degrades to a neutral fallback when values are missing.

## Regime model: `TIMEFRAME` vs `TRAIN_TIMEFRAME`

Offline training ([`tr_bot/training/pipeline.py`](../tr_bot/training/pipeline.py)) uses **`TRAIN_TIMEFRAME`** (often `1d` when mixing Yahoo, CoinDesk index OHLCV, and exchange history). The live loop ([`tr_bot/engine.py`](../tr_bot/engine.py)) loads the saved [`RegimeModel`](../tr_bot/regime.py) and predicts on bars from **`TIMEFRAME`** (default `5m` in `.env.example`).

The model inputs are RSI, EMA20, EMA50, and ATR on whatever bar length you fetch. Training on **daily** bars but predicting on **5m** bars changes the distribution of those features versus what the forest saw at fit time.

**Recommended alignment**

- Prefer **`TIMEFRAME` = `TRAIN_TIMEFRAME`** for production if the promoted regime model should match the training distribution (e.g. both `1d` for swing-style gates, or both `5m` for intraday).
- If you intentionally mix daily training with intraday execution, treat regime confidence as approximate and monitor `regime_confidence` / decision logs; document that choice for your desk.

`TRAIN_BACKTEST_TIMEFRAME` / walk-forward helpers may default to `TIMEFRAME`; keep those consistent with how you evaluate the model.

## Circuit breaker vs open positions

The circuit breaker stops **new entries** when tripped; **ATR / stop exits and holding snapshots** still run each poll. Clear a trip via `.circuit_reset` in the project root (see `tr_bot/circuit_breaker.py`) or adjust persisted `circuit_state.json` under `ARTIFACTS_DIR` per your runbook.

## Decision log (PostgreSQL)

When `DECISION_LOG_DB_ENABLED=true` and `DATABASE_URL` is set, the trading loop inserts one row per **no-trade** evaluation into **`decision_events`** (schema: `scripts/sql/001_decision_events.sql` or auto-created on first insert). Columns include `reason`, `regime_confidence`, and `features_json` (e.g. RSI, ATR, profile/tier) for analytics and offline joins. Failures are logged and never block trading.

Example export:

```sql
COPY (SELECT ts_utc, symbol, reason, regime_confidence, features_json FROM decision_events ORDER BY ts_utc DESC LIMIT 10000)
TO STDOUT WITH CSV HEADER;
```

Offline regime training still uses parquet OHLCV; join or export `decision_events` separately if you want labels from past gate outcomes.

## No-trade adaptation (meta-tuning)

Operational layer separate from regime **model** training: it tracks consecutive trading loops with no new `opened` action, reads recent `decision_audit.jsonl` lines, picks a dominant `no_trade` **reason**, applies a **single bounded step** on profile overlay keys (`REGIME_MIN_CONFIDENCE`, `SIGNAL_*`, optional RSI bands / `TREND_STRATEGY_ENABLED`), runs a **walk-forward backtest in a temp artifacts tree**, and only then writes a new strategy profile (and updates the paper pointer) if the backtest gate passes.

- **Enable**: `ADAPTATION_ENABLED=true` and set `ADAPTATION_INTERVAL_SECONDS` (or run `python -m tr_bot.managers adaptation-run` manually). The scheduler picks up a positive interval even when `SCHEDULER_ENABLED=false`, same as other interval-only jobs.
- **Paper vs live**: On `DEPLOYMENT_TIER=paper` with `ADAPTATION_AUTO_APPLY_PAPER=true`, a passing gate updates `artifacts/strategy_profiles/` and `paper_active.json`. Other tiers (or `ADAPTATION_AUTO_APPLY_PAPER=false`) write **`artifacts/adaptation/proposals/<profile_id>.json`** only—no automatic pointer change.
- **State**: `artifacts/adaptation/no_trade_state.json` stores the streak and last adaptation time (survives restarts).
- **Gate env**: `ADAPT_BACKTEST_MIN_RETURN_PCT`, `ADAPT_BACKTEST_MAX_DD_PCT`, `ADAPT_BACKTEST_MIN_TRADES`—if unset, **`STARTUP_BACKTEST_*`** values are used so startup and adaptation thresholds stay aligned unless you override.
- **Not a profitability guarantee**: This only relaxes gates that dominated recent `no_trade` telemetry; it does not replace walk-forward validation or risk limits.

See `.env.example` for step sizes, floors, and optional `ADAPTATION_AUDIT_JSONL_PATH`.

CoinDesk index reference checks (markets + mapped instruments, optional strict preflight): see [coindesk_reference.md](coindesk_reference.md). Asset Top List / on-chain helpers: [coindesk_asset_catalog.md](coindesk_asset_catalog.md).

### CoinDesk / `tr_bot` package mismatch (AttributeError on reference helpers)

If logs show `no attribute 'coindesk_effective_index_markets_path'` but this repo’s [`tr_bot/config.py`](../tr_bot/config.py) defines that function, the running process is almost certainly importing a **different** `tr_bot` tree (stale install, `PYTHONPATH`, or a worker that was never restarted).

1. Stop `./start.sh --all` (or the API / scheduler processes you care about).
2. From the project root, use the **same** interpreter as `start.sh` (`.venv`):

```bash
.venv/bin/python -c "import tr_bot.config as c; print(c.__file__); print(hasattr(c, 'coindesk_effective_index_markets_path'))"
```

You should see `True` and a `config.py` path under this checkout. If not, fix `PYTHONPATH`, remove conflicting installs, or clear stale bytecode under this repo (`find . -path './.venv' -prune -o -name '*.pyc' -print` then delete as needed), then start again.

### CoinDesk OHLCV store: `connection already closed` right after `SELECT 1`

Training used to open Postgres, run `SELECT 1`, then convert the OHLCV DataFrame with `iterrows()` (which can take noticeable time on large frames) before the first insert. Poolers or short `idle_session_timeout` settings can drop the session in that gap. The pipeline now **builds row dicts before connecting**, then connects, pings, and upserts in one tight sequence ([`tr_bot/training/pipeline.py`](../tr_bot/training/pipeline.py), [`tr_bot/db/coindesk_repo.py`](../tr_bot/db/coindesk_repo.py)).

CoinDesk DB sessions also use **TCP keepalives** on connect, the tick **poller** pings before writes and retries once on recoverable connection errors, and **CCSEQ gap backfill** always refreshes the main session after a backfill attempt so the connection is not reused after sitting idle through HTTP ([`tr_bot/coindesk/poller.py`](../tr_bot/coindesk/poller.py), [`tr_bot/coindesk/ccseq.py`](../tr_bot/coindesk/ccseq.py)).

### CoinDesk reference preflight (ccix / index): instrument naming

Reference validation normalizes `-` / `_` / `/` and matches rows by `MAPPED_INSTRUMENT` (and common aliases) so `COINDESK_INSTRUMENTS=BTC-USD` aligns with API keys like `BTC_USD` or lowercase mapped symbols ([`tr_bot/coindesk/reference.py`](../tr_bot/coindesk/reference.py)).

### Trading: ADX margin holds

When signal strength filtering is on, mean-reversion buys are blocked if ADX is above the range cap minus `SIGNAL_MIN_ADX_MARGIN`; trend entries need ADX above the trend floor plus that margin. Audit reasons `weak_signal:adx_too_high_for_range` and `weak_signal:adx_too_low_for_trend` describe that filter explicitly (not an exchange or data failure). Tuning `SIGNAL_MIN_ADX_MARGIN`, `ADX_*`, or the active strategy profile is a product choice ([`tr_bot/engine.py`](../tr_bot/engine.py)).

## Common issues

- `address already in use` -> old API/bot process still running.
- `Ready · Postgres down` -> database not reachable or auth mismatch.
- repeated sentiment `429` -> LLM rate limit; engine falls back to neutral sentiment.
- Binance signed endpoint errors in paper mode -> verify `PAPER_TRADING=true` and restart.

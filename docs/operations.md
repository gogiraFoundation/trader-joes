# Operations Runbook

## Start services

```bash
./start.sh                 # trading loop (default)
./start.sh --api           # dashboard + health
./start.sh --scheduler     # periodic offline jobs
./start.sh --all           # hard-block pipeline, then API + loop
```

- Bot loop and API can run independently.
- Scheduler is optional and does not replace the trading loop.
- Top-level install / architecture: [`README.md`](../README.md). Module ownership when changing code: [`skills.md`](../skills.md).

## Homelab (Docker Compose)

Always-on stack on a homelab host (e.g. `/mnt/data/apps/tr_bot`). Requires a project-root `.env` (see `.env.example`).

### Sync from laptop

```bash
# on the Mac, from the repo root
rsync -avz --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  --exclude 'artifacts' --exclude '.env' \
  ./ gogirdev@gogir-server:/mnt/data/apps/tr_bot/
scp .env gogirdev@gogir-server:/mnt/data/apps/tr_bot/.env
```

### Start

```bash
cd /mnt/data/apps/tr_bot   # or your deploy path
docker compose up -d --build
```

| Service | Role |
|---------|------|
| `redis` / `db` | Shared dependencies (no host ports; Postgres persisted in `tr_bot_pg`) |
| `startup` | One-shot hard-block pipeline (`python -m tr_bot.startup.pipeline run`). Restarts on failure only. |
| `api` | Dashboard + health on `${API_PUBLISH:-0.0.0.0}:${API_PORT:-8080}` |
| `bot` | Trading loop after `startup` exits 0 |
| `scheduler` | Periodic train / backtest / adaptation / daily report (`python -m tr_bot.scheduler`) |

Compose overrides `REDIS_URL`, `DATABASE_URL`, `ARTIFACTS_DIR`, and container `API_PORT=8080` for the in-network hosts. Trading knobs (`SYMBOL`, `PAPER_TRADING`, etc.) come from `.env`.

### Startup wait (bot stays Created)

`bot` and `scheduler` stay **Created** while Compose shows `startup` as **Waiting**. That is normal: they use `depends_on: service_completed_successfully`. The first pipeline (preflight → train → tune → backtest) often takes many minutes.

```bash
docker compose logs -f startup
docker compose ps
```

- Still logging train/backtest → wait.
- Non-zero exit and restart (`restart: on-failure`) → fix `.env` / gate; check `deployment/startup_last_run.json` in the artifacts volume.
- `api` can be healthy while startup runs; it does not wait on the gate.

If the startup gate fails, `bot` and `scheduler` never start. Fix the gate, then `docker compose up -d` again. A later `bot` crash restarts the loop only; it does not re-run training.

Entry point: [`scripts/supervise.sh`](../scripts/supervise.sh) (`startup` \| `api` \| `bot` \| `scheduler`).

### LAN access via Pi-hole + nginx (not the internet)

Path: LAN client → Pi-hole DNS (`trbot.lan` → **Pi IP**) → Pi nginx → `http://<gogir-server-lan-ip>:8080`.

1. On gogir-server, publish the API (default after sync): `API_PUBLISH=0.0.0.0`, then `docker compose up -d`. Set `PUBLIC_BASE_URL=http://trbot.lan` and a long `DASHBOARD_TOKEN` in `.env`.
2. Optional firewall on the server: allow only the Pi to hit 8080, e.g. `ufw allow from <pi-lan-ip> to any port 8080`.
3. Copy [`deploy/pi/nginx-trbot.lan.conf`](../deploy/pi/nginx-trbot.lan.conf) to the Pi, replace `GOGIR_SERVER_LAN_IP` with the server’s LAN address, enable the site, then `sudo nginx -t && sudo systemctl reload nginx`.
4. Pi-hole → Local DNS Records: **A** `trbot.lan` → **the Pi’s LAN IP** (nginx host), not the bot container IP.

Verify:

```bash
# on gogir-server
curl -sS http://127.0.0.1:8080/health
# from Pi or another LAN host
curl -sS -H "Host: trbot.lan" http://<pi-ip>/health
```

Open `http://trbot.lan/` on the LAN and enter `DASHBOARD_TOKEN`.

**Non-goals for this setup:** no router port-forward for 80/443/8080 aimed at the bot; no Cloudflare Tunnel in this slice.

## Hard-block startup pipeline (`--all`)

`./start.sh --all` runs a mandatory startup pipeline before trading starts when `STARTUP_AUTOMATION_ENABLED` is true (default). Set it `false` to skip the pipeline and still launch API + loop.

Stages (first failure stops later stages):

1. preflight checks (`python -m tr_bot.startup.preflight`) — includes **CoinMarketCap / training consistency** (`cmc` in the JSON plus `cmc:*` warnings). `./start.sh --all` runs `python -m tr_bot.startup.pipeline run`, which prints `cmc:*` warnings to **stderr** before the full pipeline JSON.
2. offline training (`python -m tr_bot.managers train-run --compact`) with `TRAIN_BACKTEST_GATE=false` (pipeline runs its own backtest afterward)
3. bounded tuning (`scripts/sweep_best_fit_settings.py --emit-profile`) — **`.env.example` sets `STARTUP_TUNING_LIMIT=0`** so startup does not emit new `sweep_*` profiles until you re-enable
4. walk-forward backtest + startup gate
5. launch API + trading loop, and optionally scheduler in background (`STARTUP_LAUNCH_SCHEDULER`, default true)

If any stage fails and `STARTUP_HARD_BLOCK=true`, trading does not start. Success/failure both write `artifacts/deployment/startup_last_run.json`.

Key knobs: `STARTUP_*` in `.env` / `.env.example`. Code defaults for the backtest gate (`tr_bot/startup/backtest_gate_thresholds.py`) are **−1%** min return, **12%** max drawdown, **3** min trades. The hardened example raises `STARTUP_BACKTEST_MIN_TRADES=20`.

## Portfolio rebalancer (drift / lag trim)

Read-only scan (Phase A1) — no orders:

```bash
python -m tr_bot.managers rebalance-scan
```

Writes `artifacts/rebalancer/latest.json` and emits `rebalance_alert` when triggers fire (if `REBALANCER_ENABLED=true`).

Trim execution (Phase A2) — requires both flags:

1. Soak alerts with `REBALANCER_ENABLED=true` and `REBALANCER_EXECUTE_TRIMS=false`.
2. Flip `REBALANCER_EXECUTE_TRIMS=true`, then:

```bash
python -m tr_bot.managers rebalance-trim
```

Open strategy positions are closed via `artifacts/rebalancer/exit_requests.json` (engine → `ExitCoordinator` with `execution_context=REBALANCE_TRIM`, circuit skipped). Idle exchange inventory is sold directly. Halt file uses `REBALANCE_HALT_REASON` and is only cleared when that reason matches.

Capital withdrawal / off-exchange transfer is **not** part of this path.

## Stop services

```bash
./stop.sh
```

`stop.sh` prompt handling is shell-portable and safe across bash/zsh/sh environments.

## Strategy engine (HIATP / SMC path)

**HIATP** in this codebase is not a third `STRATEGY_ENGINE` value. It names the SMC composite score (`tr_bot/strategy/scoring.py`), explainability records (`explainability.py`, version `hiatp-1`), and session / volatility / correlation size adjusters used by `RiskManager`.

- `STRATEGY_ENGINE=legacy` (default): existing mean-reversion + ML regime path.
- `STRATEGY_ENGINE=smc`: entries from SMC signal pipeline + HIATP-style scoring (`tr_bot/strategy/signal_pipeline.py`). Defaults: `SMC_MIN_TRADE_SCORE=65`, `SMC_MIN_SMC_FACTORS=2`; multi-TF via `SMC_*_TIMEFRAME`.
- `STRATEGY_ENGINE=shadow`: trade legacy; evaluate SMC in parallel and log dual views in decision audit (schema v2).
- Compare engines offline: `python -m tr_bot.managers backtest-compare` → `artifacts/training/backtest_compare.json` and `artifacts/research/smc_go_no_go.json`.
- Promotion evidence: `python -m tr_bot.managers strategy-compare`; set `PROMOTION_REQUIRE_COMPARATOR=true` to gate promotes.
- Kill switch: set `STRATEGY_ENGINE=legacy` (and existing halt / freeze controls).

## Paper vs live

- `PAPER_TRADING=true` (default): simulated execution; no live orders.
- `PAPER_TRADING=false` or `DEPLOYMENT_TIER=live_canary|live_full`: live Binance **spot** mode.

Live starts fail closed via `tr_bot.runtime_validation` (preflight and `engine.run_forever`):

- Both `BINANCE_API_KEY` and `BINANCE_SECRET` required.
- Symbols must be ccxt pairs (e.g. `BTC/USDT`).
- `LIVE_MAX_NOTIONAL_USDT` must be **> 0** (hard USDT notional cap per entry).
- `EXIT_POLICY=exchange_native` is **rejected** until protective-order reconciliation is integrated; use `software_polled`.
- `live_canary` additionally requires non-empty `CANARY_SYMBOLS`, `0 < CANARY_RISK_MULTIPLIER <= 0.25`, and `0 < CANARY_MAX_NOTIONAL_USDT <= 50`.

Between strategy polls (`POLL_SECONDS`), a single-threaded REST exit watchdog checks active stop levels about every `EXIT_WATCHDOG_INTERVAL_S` (default 5s). Paper fills still use simulated prices and **do not** measure Binance slippage.

Important: in paper mode, the exchange client does not attach API keys to avoid private signed endpoint calls.

Taker fee / backtest cost precedence (`LIVE_FEE_*`, `BACKTEST_*`, BNB retail defaults) is documented in `.env.example` and implemented in `tr_bot.fees`.

## Fee/R soak (1h + costs + single open)

Fee drag on short bars is usually **ATR vs round-trip cost**, not which USDT pair is most liquid. Flat `LIVE_FEE_BPS>0` overrides every symbol and **blocks** the BNB retail path in [`tr_bot/fees.py`](../tr_bot/fees.py).

**Recommended paper soak (apply to your live `.env`; `.env.example` mirrors this):**

1. `TIMEFRAME=1h` and `TRAIN_TIMEFRAME=1h` (retrain regime after the change).
2. Fees: `LIVE_FEE_BPS=0`, `BACKTEST_FEE_BPS=0`, `LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS=true`, `BINANCE_PAY_FEES_WITH_BNB=true` → modeled **7.5** bps taker. Binance account must also pay fees with BNB.
3. `MIN_ATR_PCT=0.0045` (~3× round-trip at 7.5 bps). Without BNB (10 bps) use ~`0.006`.
4. `MAX_OPEN_POSITIONS=1` — live engine blocks a second symbol with reason `max_open_positions`. Walk-forward backtest is already single-position.
5. Drop BNB from live weights. Run `python scripts/compare_fee_r_universes.py` (BTC/ETH/SOL @ 1h). The script **forces** soak cost knobs in-process (`LIVE_FEE_BPS=0`, BNB retail 7.5, `MIN_ATR_PCT=0.0045`) so a leftover flat-10 / `MIN_ATR_PCT=0.002` in `.env` cannot skew ranks. Use `--dry-run` to write the JSON schema without network. Set `PORTFOLIO_WEIGHTS` from the top 1–2 by **profit factor after costs**. Artifact: `artifacts/research/fee_r_universe_compare.json`.
6. Reset the circuit only after TF + fee + universe deploy. Copy soak knobs into your live `.env` (example only ships in `.env.example`).

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

**Startup / adaptation backtest gate** (`STARTUP_BACKTEST_*`, optional `ADAPT_*` overrides): code defaults are **−1% min return**, **12% max drawdown**, **3 min trades** (zero-trade profiles are rejected at startup). [`.env.example`](../.env.example) hardens the trade floor to **`STARTUP_BACKTEST_MIN_TRADES=20`**. Tighten further with e.g. `STARTUP_BACKTEST_MIN_RETURN_PCT=-0.5` or `0`. This gate is separate from `PROMOTION_MIN_TRADES`.

If strict mode is enabled and gates fail, training exits with a runtime error by design.

## CoinMarketCap (training and live)

- **Training source**: Add `coinmarketcap` to `TRAIN_SOURCES` when CMC aggregate OHLCV or a specific listed asset adds value beyond Yahoo or your exchange feed. Prefer exchange or Yahoo for bars that should track execution venues closely.
- **Stable identity**: Prefer [`CMC_TRAIN_IDS`](../tr_bot/config.py) (comma-separated CMC numeric ids) or `CMC_TRAIN_SLUGS` over raw symbols. Symbols are ambiguous; with `CMC_TRAIN_SYMBOLS` only, keep `CMC_MAP_RESOLVE_SYMBOLS=true` (default) so [`tr_bot/training/cmc.py`](../tr_bot/training/cmc.py) resolves once per run via `/v1/cryptocurrency/map`.
- **Credits**: Use a single `CMC_CONVERT` (extra quote currencies cost more credits). Tune `CMC_COUNT` to the history you need, and use cached training datasets (`TRAIN_DATASET_MODE`, `TRAIN_DATASET_MAX_AGE_SECONDS`) so full OHLCV pulls are not repeated on every retrain.
- **Live macro**: [`tr_bot/analysis/cmc_global.py`](../tr_bot/analysis/cmc_global.py) calls `/v1/global-metrics/quotes/latest` with TTL `CMC_GLOBAL_METRICS_TTL_S` (default 300s). Optional `/v3/fear-and-greed/latest` and `/v1/altcoin-season-index/latest` are gated by `CMC_FEAR_GREED_ENABLED` / `CMC_ALTCOIN_SEASON_ENABLED` and use TTL ≥ 900s. Do not put `/v1/global-metrics/quotes/historical` on the live trading hot path.
- **Training macro merge**: When `CMC_TRAIN_MACRO_MERGE_ENABLED=true`, [`tr_bot/training/cmc_macro.py`](../tr_bot/training/cmc_macro.py) fetches batch macro history and merges onto all training rows using **backward** `merge_asof` on `timestamp` (each bar only sees the last published point at or before that time). Optional `CMC_TRAIN_MACRO_PARQUET_PATH` writes the merged frame under `ARTIFACTS_DIR`. If `TRAIN_REGIME_MACRO_FEATURES=true`, the regime classifier uses the added `cmc_macro_*` columns; the live loop must supply those same columns on the decision dataframe or prediction degrades to a neutral fallback when values are missing.

## Regime model: `TIMEFRAME` vs `TRAIN_TIMEFRAME`

Offline training ([`tr_bot/training/pipeline.py`](../tr_bot/training/pipeline.py)) uses **`TRAIN_TIMEFRAME`**. The live loop ([`tr_bot/engine.py`](../tr_bot/engine.py)) loads the saved [`RegimeModel`](../tr_bot/regime.py) and predicts on bars from **`TIMEFRAME`**.

The model inputs are RSI, EMA20, EMA50, and ATR on whatever bar length you fetch. Training on **daily** bars but predicting on **5m** bars changes the distribution of those features versus what the forest saw at fit time.

**Recommended alignment (intraday / Option A)**

- Set **`TIMEFRAME` = `TRAIN_TIMEFRAME`** (fee/R soak: both `1h`; previously often `5m`).
- Prefer **`TRAIN_SOURCES=exchange`** with `TRAIN_EXCHANGE_SYMBOL` as a **comma-separated** list of CCXT pairs (e.g. `SOL/USDT,BTC/USDT,ETH/USDT`); each pair is fetched with the same lookback/range and tagged as `source=exchange`. Empty falls back to `SYMBOL`. The **primary** series for sample weighting is `TRAIN_PRIMARY_ASSET` when set, otherwise the **first** CSV entry (then `SYMBOL`). Set `TRAIN_PRIMARY_SAMPLE_WEIGHT` (e.g. `3.0`) so the live pair dominates the regime fit while aux pairs still contribute. Keep **`TRAIN_BACKTEST_SYMBOL`** equal to the live **`SYMBOL`**.
- Do **not** also list the same bases on Yahoo (e.g. omit `SOL-USD` when `SOL/USDT` is already in `TRAIN_EXCHANGE_SYMBOL`) to avoid double-counting.
- With more than one exchange symbol, set **`TRAIN_VALIDATION_PER_GROUP=true`** so walk-forward gates are not dominated by a single series. Note that **`TRAIN_MAX_ROWS`** is a global timestamp-ordered tail across all assets — raise it or leave at `0` when training many pairs.
- Optional **`TRAIN_EXCHANGE_FAIL_ON_SKIP=true`** fails the run if any listed pair cannot be fetched (default skips failed pairs and continues).
- For history beyond one ccxt page (~1000 candles), set **`TRAIN_EXCHANGE_LOOKBACK_DAYS > 0`** (or both `TRAIN_EXCHANGE_START_MS` / `END_MS`). **`TRAIN_EXCHANGE_LIMIT` alone does not paginate** — without lookback/range the pipeline calls a single `fetch_ohlcv`.
- Walk-forward / startup backtests that request `BACKTEST_BARS` above the chunk size use [`fetch_ohlcv_deep`](../tr_bot/market_data.py) (paginated range fetch for exchange pairs). `TRAIN_BACKTEST_SYMBOL` remains a **single** pair (typically the live symbol).

**Startup note:** `./start.sh --all` forces `TRAIN_BACKTEST_GATE=false` during `train-run` and evaluates the strategy with `python -m tr_bot.managers backtest-run` plus `STARTUP_BACKTEST_*` thresholds. Keep `TRAIN_BACKTEST_*` aligned for standalone / scheduler training.

If you intentionally mix daily training with intraday execution, treat regime confidence as approximate and document that choice.

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

## Grok / LLM agents (Supervisor + Fund Manager)

Advisory agents that read circuit/fund/ledger context and write briefs under `artifacts/agents/`. They **never** open trades. Sentiment (`USE_LLM`) remains a veto only.

**Enable (paper soak):**

1. Point LLM at xAI (or OpenAI): `USE_LLM=true`, `OPENAI_BASE_URL=https://api.x.ai/v1`, `OPENAI_API_KEY=…`, `LLM_MODEL=grok-2-latest` (optional `AGENTS_MODEL` override).
2. `AGENTS_ENABLED=true` and keep `AGENTS_DRY_RUN=true`.
3. One-shot dry briefs: `python -m tr_bot.managers agents-run --dry-run` (bypasses `AGENTS_ENABLED` for advise-only). Mutating runs require `AGENTS_ENABLED=true` and never bypass that switch.
4. Scheduler: set `SCHEDULER_AGENTS_INTERVAL_SECONDS` (e.g. `3600`) and optionally `SCHEDULER_RUN_AGENTS_ON_START=true`. Interval `0` keeps the job off.
5. Artifacts: `artifacts/agents/supervisor_last.json`, `fund_manager_last.json`, `actions.jsonl`. Dashboard summary section `agents`; alert id `agents_critical` when a brief is critical.

**Mutating actions (all default off):** set `AGENTS_DRY_RUN=false` only after dry-run soak, then enable one flag at a time: `AGENTS_AUTO_PAUSE`, `AGENTS_AUTO_RESUME` (clears only an agent-owned pause with `AGENTS_HALT_REASON`; never while drawdown halt or circuit is tripped), `AGENTS_AUTO_RESET_CIRCUIT`, `AGENTS_AUTO_EMIT_PROPOSAL` (write proposal file), `AGENTS_AUTO_APPLY_PROPOSAL` (paper tier + proposal allowlist/backtest gate), `AGENTS_AUTO_REBALANCE_SCAN` (scan only, never trim). At most one mutating action per run. Cooldown: `AGENTS_COOLDOWN_SECONDS` (default 3600).

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

The liveness probe ends the implicit transaction with **`rollback()`** (not a silent `commit()` failure), training **chunks** large `coindesk_ohlcv` upserts (`COINDESK_OHLCV_UPSERT_CHUNK`), and the training pipeline **retries** a few times with backoff on recoverable DB errors.

CoinDesk DB sessions also use **TCP keepalives** and **`connect_timeout`** on connect, the tick **poller** pings before writes and retries once on recoverable connection errors, and **CCSEQ gap backfill** always refreshes the main session after a backfill attempt so the connection is not reused after sitting idle through HTTP ([`tr_bot/coindesk/poller.py`](../tr_bot/coindesk/poller.py), [`tr_bot/coindesk/ccseq.py`](../tr_bot/coindesk/ccseq.py)).

### CoinDesk reference preflight (ccix / index): instrument naming

Reference validation normalizes `-` / `_` / `/` and matches rows by `MAPPED_INSTRUMENT` (and common aliases) so `COINDESK_INSTRUMENTS=BTC-USD` aligns with API keys like `BTC_USD` or lowercase mapped symbols ([`tr_bot/coindesk/reference.py`](../tr_bot/coindesk/reference.py)).

### Trading: ADX margin holds

When signal strength filtering is on, mean-reversion buys are blocked if ADX is above the range cap minus `SIGNAL_MIN_ADX_MARGIN`; trend entries need ADX above the trend floor plus that margin. Audit reasons `weak_signal:adx_too_high_for_range` and `weak_signal:adx_too_low_for_trend` describe that filter explicitly (not an exchange or data failure). Tuning `SIGNAL_MIN_ADX_MARGIN`, `ADX_*`, or the active strategy profile is a product choice ([`tr_bot/engine.py`](../tr_bot/engine.py)).

## Optional CoinDesk poller

Tick / OHLCV ingest is a **separate** process (not started by `./start.sh --all` or Compose `bot`/`scheduler`):

```bash
python -m tr_bot.coindesk
```

Enable with `COINDESK_ENABLED` / store flags in `.env`. Scheduler may refresh the asset top-list snapshot on its own interval; it does not replace the poller. See [`coindesk_reference.md`](coindesk_reference.md).

## Common issues

- `address already in use` -> old API/bot process still running.
- `Ready · Postgres down` -> database not reachable or auth mismatch.
- repeated sentiment `429` -> LLM rate limit; engine falls back to neutral sentiment.
- Binance signed endpoint errors in paper mode -> verify `PAPER_TRADING=true` and restart.
- `bot` stays Created under Compose -> `startup` still running or failed; see [Startup wait](#startup-wait-bot-stays-created).

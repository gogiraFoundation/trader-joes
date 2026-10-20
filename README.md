# tr_bot

Python trading stack for **Binance spot** (via **ccxt**) with **paper trading by default**. The loop combines **ML regime detection**, **legacy mean-reversion / optional trend signals** or an optional **SMC (smart-money concepts) pipeline** with HIATP-style scoring, **risk controls** (including session / volatility / correlation size adjusters), and **optional LLM sentiment as a veto only** (never a primary signal). Advisory **Supervisor / Fund Manager** agents may pause, notify, or propose profile changes — they never call `execute_trade`.

A **FastAPI** app serves health checks, a dashboard (v1/v2), and JSON summaries over artifacts under `ARTIFACTS_DIR` (default `artifacts/`). Homelab deploy is Compose + LAN-only dashboard (`trbot.lan`); do not expose the bot to the public internet.

This repository is for **research and operations**: validate in paper mode (fee/R soak first) and read the [disclaimer](#disclaimer) before live use. For module ownership and invariants when changing code, see [`skills.md`](skills.md). Operator preferences learned across sessions: [`AGENTS.md`](AGENTS.md).

## Table of contents

- [Overview](#overview)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running services](#running-services)
- [Configuration](#configuration)
- [Docker](#docker)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [Design and strategy reference](#design-and-strategy-reference)
- [Disclaimer](#disclaimer)

## Overview

- **Loop:** `python -m tr_bot` runs `tr_bot.engine.run_forever` on `POLL_SECONDS`.
- **Strategy engines** (`STRATEGY_ENGINE`):
  - `legacy` (default) — regime + RSI/EMA mean reversion; trend only if `TREND_STRATEGY_ENABLED=true`.
  - `smc` — multi-TF SMC signal pipeline + HIATP-style composite scoring (`tr_bot/strategy/signal_pipeline.py`, `scoring.py`).
  - `shadow` — trade legacy; evaluate SMC in parallel and log dual views in the decision audit.
- **Sizing adjusters:** `RiskManager.effective_risk_multiplier` combines correlation penalty with `market_session` and `volatility_regime` factors (clamped).
- **Exits:** `ExitCoordinator` is the sole closer; software-polled stops by default (`EXIT_POLICY=software_polled`). Inter-poll checks via `EXIT_WATCHDOG_INTERVAL_S`. `exchange_native` remains rejected until `protective_reconcile` is fully wired.
- **Processes (keep separate):** trading loop · API (`tr_bot.health:app`) · scheduler · optional CoinDesk poller (`python -m tr_bot.coindesk`). None substitutes for another.
- **Bootstrap:** [`start.sh`](start.sh) creates `.venv`, installs [`requirements.txt`](requirements.txt), copies [`.env.example`](.env.example) → `.env` when missing, and starts bot / API / full pipeline.
- **Offline tooling:** `python -m tr_bot.managers` for training, backtests, promotion, rebalance, agents, research ([`docs/cli.md`](docs/cli.md)). Fee/R universe bake-off: `scripts/compare_fee_r_universes.py`.

## Requirements

- **Python 3.12+** (matches [`Dockerfile`](Dockerfile); local development may use 3.13).
- **bash** for `start.sh` / `stop.sh`.
- Network access for market data (exchange, Yahoo, optional CoinMarketCap / CoinDesk APIs).
- **Redis** and **PostgreSQL** are optional unless you set `REDIS_URL`, `DATABASE_URL`, or features that need them (see [`.env.example`](.env.example)).

## Installation

1. Clone the repository and change into the project root.
2. Run `./start.sh --help`, or start directly — on first run, `start.sh` creates `.venv`, installs deps, and copies `.env` from `.env.example` if missing.
3. Edit **`.env`** for secrets (`BINANCE_*`, `OPENAI_API_KEY`, `CMC_API_KEY`, etc.) and trading settings before any live use.

**Manual setup (optional):**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

## Running services

| Mode | Command | Purpose |
|------|---------|---------|
| Trading loop (default) | `./start.sh` or `./start.sh --bot` | `python -m tr_bot` (foreground). |
| API / dashboard | `./start.sh --api` | **uvicorn** on `tr_bot.health:app` — UI, `/health`, `/docs`, `/api/dashboard/...`. Bind via `API_HOST` / `API_PORT` (defaults `0.0.0.0` / `8080`). |
| Full stack | `./start.sh --all` | When `STARTUP_AUTOMATION_ENABLED` is true (default): hard-block pipeline (preflight → train → tune → backtest gate), then API + optional scheduler in the background and the **trading loop in the foreground**. |
| Scheduler only | `./start.sh --scheduler` | Periodic train / backtest / adaptation / agents / reports per `SCHEDULER_*`; does **not** replace the trading loop. |
| CoinDesk poller | `python -m tr_bot.coindesk` | Optional tick/OHLCV ingest; not started by `--all`. |

Stop locally with [`./stop.sh`](stop.sh).

**Paper soak first:** align `TIMEFRAME`/`TRAIN_TIMEFRAME` (fee/R: `1h`), retail BNB fee path, `MIN_ATR_PCT`, `MAX_OPEN_POSITIONS=1`, then promote only after soak gates. Details: [`docs/operations.md`](docs/operations.md#feer-soak-1h--costs--single-open).

**Ops runbook:** paper vs live, hard-block stages, agents, rebalancer, CoinDesk/CMC, and troubleshooting → [`docs/operations.md`](docs/operations.md). Homelab Compose + Pi nginx LAN access are documented there as well.

## Configuration

All knobs live in **`.env`**; [`.env.example`](.env.example) is authoritative. High-level groups:

| Group | Examples |
|-------|----------|
| Trading | `SYMBOL`, `TIMEFRAME`, `POLL_SECONDS`, `PAPER_TRADING`, `DEPLOYMENT_TIER`, Binance keys |
| Strategy | `STRATEGY_ENGINE`, `EXIT_MODE`, `SMC_*`, RSI/ADX, `TREND_STRATEGY_ENABLED` |
| Risk | `RISK_PER_TRADE`, `MAX_TRADES_PER_DAY`, `MAX_OPEN_POSITIONS`, `MAX_DRAWDOWN_PCT`, circuit, canary caps, `MIN_ATR_PCT`, `PAPER_MAX_NOTIONAL_USDT` |
| Exits | `EXIT_POLICY` (`software_polled`; `exchange_native` rejected until reconcile is ready), `EXIT_WATCHDOG_INTERVAL_S` |
| Fees | `LIVE_FEE_*`, `BACKTEST_*`, BNB retail taker defaults (`LIVE_FEE_BPS=0` to use them) |
| LLM / agents | `USE_LLM`, model URLs, `AGENTS_*` (dry-run and mutating flags default off) |
| Training / backtest | `TRAIN_*` (prefer `TRAIN_SOURCES=exchange` + `TRAIN_TIMEFRAME` = live TF), `BACKTEST_*` |
| Startup / scheduler | `STARTUP_*` (example sets `STARTUP_TUNING_LIMIT=0`, `STARTUP_BACKTEST_MIN_TRADES=20`), `SCHEDULER_*`, `ADAPTATION_*` |
| Promotion | `PROMOTION_*` (example: `min_trades=50`, `min_calendar_days=14`, `min_profit_factor=1.15`, comparator required) |
| Rebalancer | `REBALANCER_*`, `LAG_*`, `REBALANCE_*` |
| Infra | `REDIS_URL`, `DATABASE_URL`, `ARTIFACTS_DIR`, `DASHBOARD_*` (writes off by default), notifications, optional CMC / CoinDesk |

Code defaults can be looser than [`.env.example`](.env.example) (e.g. startup min trades **3** in code vs **20** in the example). Prefer the example for paper soak.

## Docker

[`docker-compose.yml`](docker-compose.yml) defines:

| Service | Role |
|---------|------|
| `redis` / `db` | Shared deps (Timescale/Postgres; persisted volume) |
| `startup` | One-shot hard-block pipeline; bot waits for success |
| `api` | Dashboard + health (`API_PUBLISH` / `API_PORT`, default host `0.0.0.0:8080`) |
| `bot` | Trading loop after startup exits 0 |
| `scheduler` | Periodic offline jobs |

Entry point is [`scripts/supervise.sh`](scripts/supervise.sh) (`startup` \| `api` \| `bot` \| `scheduler`). App services share the `tr_bot_artifacts` volume for `ARTIFACTS_DIR`. The [`Dockerfile`](Dockerfile) uses **Python 3.12-slim**.

```bash
docker compose up -d --build
docker compose logs -f startup   # first pipeline can take many minutes
docker compose ps
```

Override secrets via `.env`; never commit API keys. Homelab sync and Pi-hole + nginx LAN path: [`docs/operations.md`](docs/operations.md).

## Testing

```bash
.venv/bin/pytest tests/
# Frontend pure helpers (Node):
node --test tests/frontend/metrics-core.test.js tests/frontend/overview-core.test.js
```

The suite covers engine gates, exits, SMC/scoring, agents, promotion, dashboard, training, startup, and related modules. Type-check config: [`pyrightconfig.json`](pyrightconfig.json).

## Project layout

| Path | Role |
|------|------|
| `tr_bot/` | Application package |
| `tests/` | `pytest` (+ frontend Node tests under `tests/frontend/`) |
| `scripts/` | `supervise.sh`, fee/R bake-off, sweep/tuning, SQL, ops helpers |
| `docs/` | Runbooks (operations, CLI, dashboard, promotion, CoinDesk) |
| `skills.md` | Agent handbook: module ownership, invariants, tests |
| `AGENTS.md` | Learned operator preferences and workspace facts |
| `.cursor/skills/` | Cursor skills that mirror domain standards |
| `artifacts/` | Runtime outputs (often gitignored); do not commit secrets or live state |
| `deploy/pi/` | Example nginx config for LAN hostname (`trbot.lan`) |

### Code map (selected)

| Area | Module(s) |
|------|-----------|
| Main loop | `tr_bot/engine.py` (`run_forever`) |
| Config / tiers / runtime gates | `tr_bot/config.py`, `tr_bot/runtime_validation.py` |
| Indicators / regime / signals | `tr_bot/indicators.py`, `regime.py`, `signals.py`, `decision.py` |
| SMC + HIATP scoring | `tr_bot/smc/`, `strategy/signal_pipeline.py`, `scoring.py`, `explainability.py` |
| Entry gates | `tr_bot/strategy/entry_gates.py`, `tier.py` |
| Session / vol / correlation | `market_session.py`, `volatility_regime.py`, `correlation.py` |
| Exits | `exit_plan.py`, `exit_coordinator.py`, `exit_watchdog.py`, `position.py`, `protective_reconcile.py` |
| Orders / fills | `tr_bot/execution.py`, `trade_ledger.py` |
| Risk / circuit / fund | `risk.py`, `circuit_breaker.py`, `managers/fund.py` |
| Rebalancer | `managers/rebalancer.py` |
| Agents (advisory) | `tr_bot/agents/` |
| Research queries | `tr_bot/research/` |
| Training / backtest / replay | `training/`, `backtest/runner.py`, `replay.py` |
| Promotion | `tr_bot/promotion/` |
| Startup / scheduler | `startup/pipeline.py`, `scheduler.py` |
| CoinDesk | `tr_bot/coindesk/`, `db/coindesk_repo.py` |
| Dashboard | `health.py`, `dashboard_api.py`, `dashboard_actions.py`, `dashboard_alerts.py`, `dashboard_jobs.py`, `static/dashboard/` |

## Documentation

- [`skills.md`](skills.md) — Agent handbook (invariants, module ownership, tests).
- [`AGENTS.md`](AGENTS.md) — Learned preferences and deploy facts.
- [`docs/README.md`](docs/README.md) — Documentation index.
- [`docs/operations.md`](docs/operations.md) — Day-2 runbook, Compose, paper/live, fee/R soak, agents, rebalancer, CMC/CoinDesk.
- [`docs/dashboard.md`](docs/dashboard.md) — Summary API, alerts, writes, v1/v2 UI, P&L.
- [`docs/cli.md`](docs/cli.md) — `python -m tr_bot.managers` commands.
- [`docs/strategy_promotion.md`](docs/strategy_promotion.md) — Profiles, tiers, gates, promote/revert, comparator.
- [`docs/coindesk_reference.md`](docs/coindesk_reference.md), [`docs/coindesk_asset_catalog.md`](docs/coindesk_asset_catalog.md) — CoinDesk notes.

---

## Design and strategy reference

The sections below are a **technical specification**. **Inline Python examples illustrate concepts**; they are not guaranteed to match imports or function names—use the `tr_bot/` package and [`skills.md`](skills.md) as the source of truth. Illustrative win-rate / profit-factor ranges are **research targets**, not guarantees or acceptance criteria.

### 1. Decision hierarchy

```
Market Data
    ↓
Regime Detection (ML)           →  When mean reversion / trend is allowed (legacy)
    ↓
Signal path
  · legacy: statistical MR / optional trend
  · smc: multi-TF structure, liquidity, OB/FVG, score
  · shadow: trade legacy + log SMC view
    ↓
Entry gates (confidence, TA, ATR floor, caps, circuit, halt, …)
    ↓
LLM sentiment (optional)        →  Veto only (bullish / bearish / neutral)
    ↓
Sizing + execution
    ↓
ExitCoordinator (+ watchdog between polls)
```

**Key insight:** Mean reversion works primarily in **range** regimes. LLMs and advisory agents filter or operate risk controls; they do not generate the primary entry side.

### 2. Strategy engines

| `STRATEGY_ENGINE` | Behavior |
|-------------------|----------|
| `legacy` | Range → `mean_reversion_signal`; trend → `trend_continuation_signal` only if `TREND_STRATEGY_ENABLED=true`. Sentiment vetoes apply. |
| `smc` | Entries from `signal_pipeline` + HIATP-style `scoring` (min score / factor count via `SMC_*`). |
| `shadow` | Submits legacy decisions only; SMC evaluation is audit/compare evidence. |

**HIATP (in this repo)** means the SMC scoring weights, explainability records (`hiatp-1`), and risk size adjusters — not a separate engine flag. Kill switch back to the production path: set `STRATEGY_ENGINE=legacy` (plus halt / promotion freeze as needed). Offline compare: `python -m tr_bot.managers backtest-compare` → `artifacts/training/backtest_compare.json` and `artifacts/research/smc_go_no_go.json`.

### 3. System architecture overview

```
+------------------+     +-----------------------+     +------------------+
| Market Data      | --> | Indicators / multi-TF | --> | Regime + SMC     |
| (ccxt / Yahoo /  |     |                       |     | detectors        |
|  optional CMC)   |     +-----------------------+     +------------------+
+------------------+                                              |
                                                                  v
+------------------+     +-----------------------+     +------------------+
| Risk / circuit / | <-- | Decision + entry      | <-- | Signal path      |
| fund halt        |     | gates                 |     | (legacy or SMC)  |
+------------------+     +-----------------------+     +------------------+
        |                          ^
        v                          |
+------------------+     +-----------------------+
| Execution        | --> | Sentiment veto +      |
| + ExitCoordinator|     | optional agents       |
+------------------+     +-----------------------+
```

#### 3.1 Role of LLM and agents

- **Sentiment (`USE_LLM`):** news → `bullish|bearish|neutral`; opposing sentiment blocks the trade. Timeouts / parse failures → `neutral`.
- **Agents (`AGENTS_*`):** Supervisor / Fund Manager write briefs under `artifacts/agents/`. Mutating actions (pause, circuit reset, proposal emit/apply, rebalance **scan**) are opt-in and dry-run by default. See [`docs/operations.md`](docs/operations.md#grok--llm-agents-supervisor--fund-manager).

### 4. Component details

#### 4.1 Market data

OHLCV via `tr_bot/market_data.py` (`fetch_ohlcv`, paginated `fetch_ohlcv_deep`). Live symbols must be ccxt pairs (e.g. `BTC/USDT`).

#### 4.2 Feature engineering

`tr_bot/indicators.py` — RSI, EMA20/50, ATR, and extended TA when enabled. Conceptual sketch:

```python
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    # RSI / ATR as implemented in tr_bot/indicators.py
    return df
```

#### 4.3 ML regime detection

`tr_bot/regime.py` — classifier labels used by the engine are **`range`** and **`trend`**. Train on **`TRAIN_TIMEFRAME`**; predict on live **`TIMEFRAME`**. Prefer keeping them equal (fee/R soak: both `1h`). See [`docs/operations.md`](docs/operations.md#regime-model-timeframe-vs-train_timeframe).

#### 4.4 Legacy signal generator

```python
# Conceptual — see tr_bot/signals.py
def mean_reversion_signal(df) -> str:
    last = df.iloc[-1]
    if last["rsi"] < 30 and last["close"] < last["ema20"]:
        return "buy"
    if last["rsi"] > 70 and last["close"] > last["ema20"]:
        return "sell"
    return "hold"
```

#### 4.5 Decision engine (legacy)

`tr_bot/decision.py` aggregates regime, signal, TA/fundamentals OK flags, and sentiment:

- **Range:** MR buys blocked if sentiment bearish; sells if bullish.
- **Trend:** optional trend continuation when `TREND_STRATEGY_ENABLED=true`; ADX floors/caps via `ADX_*` and `SIGNAL_MIN_ADX_MARGIN`.

#### 4.6 Risk management

Production behavior (`tr_bot/risk.py`, `engine.py`):

- Live size uses `effective_risk_per_trade()` (`RISK_PER_TRADE` × `CANARY_RISK_MULTIPLIER` on `live_canary`).
- Per-trade multiplier: `effective_risk_multiplier(vol_regime, correlation_penalty, session)` from `volatility_regime`, `correlation`, and `market_session` (clamped ~0.25–1.5).
- Portfolio weights, `apply_canary_notional_cap` / live notional caps, `MAX_TRADES_PER_DAY`, optional `MAX_OPEN_POSITIONS`.
- Fee/R floor: skip entries when ATR/close < `MIN_ATR_PCT` (reason `atr_too_small_for_costs`).
- **FundManager** can halt on drawdown (`MAX_DRAWDOWN_PCT`).
- **Circuit breaker** blocks **new entries** only; exits and holding snapshots continue. `REBALANCE_*` execution contexts skip the circuit.

**Stops (ATR mode):** conceptual geometry is stop ≈ entry ± 1× ATR, take-profit ≈ entry ± 1.5× ATR — confirm multipliers in `position.py` / the active profile. `EXIT_MODE` may be `atr`, `structural`, or `hybrid` on the SMC path.

**Live vs backtest exit economics:** `check_bar` uses bar high/low for TP/stop *hits*. The live loop records PnL using bar **close** after an exit signal; walk-forward backtest often uses theoretical TP/stop **prices**. Align assumptions before comparing Sharpe / profit factor.

#### 4.7 Execution and exits

- Entries: `tr_bot/execution.py`.
- Closes: **`ExitCoordinator` only** — persist `exit_state=pending` with a stable client order id before submit.
- Live spot: a **new** raw `sell` entry is rejected (`live_sell_disabled`); closing an existing long still sells.
- Live starts fail closed via `runtime_validation` (keys, ccxt pairs, notional caps, canary floors, `EXIT_POLICY`).

### 5. Trading loop (conceptual)

Illustrative shape (not copy-paste runnable):

```python
while True:
    df = fetch_ohlcv(SYMBOL, TIMEFRAME, limit=LOOP_BARS)
    df = compute_indicators(df)
    # legacy and/or SMC evaluation → entry gates → size → execute
    # exits via ExitCoordinator; sleep_with_exit_watchdog(POLL_SECONDS)
```

### 6. Infrastructure and deployment

| Layer | Technology |
|-------|------------|
| Data | `ccxt`, `yfinance`, optional CMC / CoinDesk |
| Indicators / ML | `pandas`, scikit-learn |
| LLM (optional) | OpenAI-compatible API (incl. xAI) |
| Execution | Binance spot via ccxt (paper default) |
| API | FastAPI + optional Redis / Postgres |
| Artifacts | Shared volume / `ARTIFACTS_DIR` |

**Paper vs live**

- **Paper (default):** `PAPER_TRADING=true` — simulated orders; API keys are **not** attached to the exchange client.
- **Live:** `PAPER_TRADING=false` or `DEPLOYMENT_TIER=live_canary|live_full`, with keys and spot enabled. Prefer canary caps before `live_full`.

**24/7:** Docker Compose (above) or bare metal under systemd/tmux with `./start.sh`. Optional scheduler does not replace the bot.

### 7. Expected performance (research targets only)

When tuned on liquid assets with regime filtering, **historical research targets** have included ~65–72% win rate and profit factor ~1.5–2.0 with drawdown under ~10%. These are **not** product SLAs. Past or backtested results do not guarantee future performance.

### Scheduled training, backtest, and CLI

```bash
.venv/bin/python -m tr_bot.managers train-run
.venv/bin/python -m tr_bot.managers backtest-run
.venv/bin/python -m tr_bot.managers backtest-compare
.venv/bin/python -m tr_bot.managers strategy-status
```

Full command list: [`docs/cli.md`](docs/cli.md). Scheduler and `--all` hard-block stages: [`docs/operations.md`](docs/operations.md).

**Selected backtest knobs:** `BACKTEST_ENFORCE_*`, `BACKTEST_USE_CIRCUIT_BREAKER`, `BACKTEST_EXECUTION_REALISM_*`, `BACKTEST_EXIT_*`, `BACKTEST_MIN_RISK_METRIC_OBS`, `BACKTEST_ANNUAL_GAP_MULTIPLIER`. Metrics metadata lands in `training/backtest_last_run.json` (`risk_metric_meta`).

### Dashboard

`GET /api/dashboard/summary` (optional `DASHBOARD_TOKEN`). UI: Overview / Risk / Strategy / Jobs / Diagnostics; `DASHBOARD_UI=v1|v2|v3` (v3 = Operator Command Console; fallback v3→v2→v1). Writes off until `DASHBOARD_WRITES_ENABLED` + token. Details: [`docs/dashboard.md`](docs/dashboard.md).

### Safety invariants (summary)

- LLM / agents never open trades.
- Circuit blocks entries only; exits still run.
- One closer: `ExitCoordinator`.
- Live fails closed on missing keys, bad symbols, zero max notional, or unsupported `EXIT_POLICY`.
- Decision-log and notification failures must not stop the loop.
- Artifact paths stay under `ARTIFACTS_DIR`; redact secrets in logs/notifications.

Full list: [`skills.md`](skills.md#safety-invariants).

### 8. Conclusion

The runnable system emphasizes **regime-aware entries**, optional **SMC / HIATP-style scoring**, **session- and vol-aware sizing**, **explicit exit authority** (`ExitCoordinator`), and **paper-first** ops with promotion tiers. Prefer `tr_bot/`, [`skills.md`](skills.md), and `docs/` over schematic snippets when implementing changes.

## Disclaimer

This software is provided for **educational and operational experimentation** only. It is **not financial advice**. Cryptocurrency trading involves substantial risk of loss. Use **paper mode** until you fully understand behavior, fees, and failure modes. The authors and contributors are **not responsible** for losses, bugs, exchange outages, or regulatory issues. You are solely responsible for compliance, API key security, and any capital you deploy.

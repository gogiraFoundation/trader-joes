# tr_bot

Python trading stack that combines **market regime detection** (scikit-learn), **mean reversion and optional trend signals**, **risk controls**, and **optional LLM sentiment** as a veto-only filter—not as a primary signal. Execution targets **Binance spot** via **ccxt** with **paper trading** by default. A **FastAPI** app serves health checks, a dashboard, and JSON summaries over artifacts under `ARTIFACTS_DIR` (default `artifacts/`).

This repository is for **research and operations**: validate behavior in paper mode and read the [disclaimer](#disclaimer) before live use.

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

- **Loop:** `python -m tr_bot` runs `tr_bot.engine.run_forever` on a poll interval (`POLL_SECONDS`).
- **Bootstrap:** [`start.sh`](start.sh) creates `.venv`, installs [`requirements.txt`](requirements.txt), copies [`.env.example`](.env.example) to `.env` when missing, and starts the bot, API, or full startup pipeline.
- **Offline tooling:** `python -m tr_bot.managers` for training, backtests, fund snapshots, strategy promotion, and more ([`docs/cli.md`](docs/cli.md)).

## Requirements

- **Python 3.12+** (matches [`Dockerfile`](Dockerfile); local development may use 3.13 if your venv does).
- **bash** for `start.sh` / `stop.sh`.
- Network access for market data (exchange, Yahoo, optional CoinMarketCap / CoinDesk APIs).
- **Redis** and **PostgreSQL** are optional unless you configure `REDIS_URL`, `DATABASE_URL`, or features that require them (defaults are in [`.env.example`](.env.example)).

## Installation

1. Clone the repository and change into the project root.
2. Run `./start.sh --help` to confirm script options, or start directly:
   - On first run, `start.sh` creates `.venv`, runs `pip install -r requirements.txt`, and copies `.env` from `.env.example` if `.env` is missing.
3. Edit **`.env`** for secrets (`BINANCE_*`, `OPENAI_API_KEY`, `CMC_API_KEY`, etc.) and trading settings before production use.

**Manual setup (optional):**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

## Running services

| Mode | Command | Purpose |
|------|---------|---------|
| Trading loop (default) | `./start.sh` or `./start.sh --bot` | Runs `python -m tr_bot` (foreground). |
| API / dashboard | `./start.sh --api` | **uvicorn** on `tr_bot.health:app` — dashboard UI, `/health`, `/docs`, `/api/dashboard/...`. Bind via `API_HOST` / `API_PORT` (defaults `0.0.0.0` / `8080`). |
| Full stack | `./start.sh --all` | When `STARTUP_AUTOMATION_ENABLED` is true: runs the startup pipeline (preflight → training → tuning → backtest gate), then API (and optionally scheduler) in the background and the **trading loop in the foreground**. |
| Scheduler only | `./start.sh --scheduler` | Periodic training/backtest/promotion per `SCHEDULER_*` in `.env`; does **not** replace the trading loop. |

Stop locally with [`./stop.sh`](stop.sh).

**Operations:** paper vs live, hard-block startup stages (`STARTUP_*`), CoinMarketCap and training gates, adaptation, Postgres decision log, and troubleshooting are covered in [`docs/operations.md`](docs/operations.md).

## Configuration

All knobs live in **`.env`**; [`.env.example`](.env.example) is the authoritative list. Grouped at a high level:

- **Trading:** `SYMBOL`, `TIMEFRAME`, `POLL_SECONDS`, `PAPER_TRADING`, Binance keys, fees/slippage, execution style.
- **Risk and limits:** `RISK_PER_TRADE`, `MAX_TRADES_PER_DAY`, `MAX_DRAWDOWN_PCT`, circuit breaker, portfolio/canary tiers.
- **LLM:** `USE_LLM`, provider URLs, model, timeouts, confidence floor.
- **Training / backtest:** `TRAIN_*`, `BACKTEST_*`, `TRAIN_SOURCES`, Yahoo and exchange training symbols, optional CMC and CoinDesk settings.
- **Startup / scheduler:** `STARTUP_*`, `SCHEDULER_*`, `STARTUP_HARD_BLOCK`, automation toggles.
- **Infra:** `REDIS_URL`, `DATABASE_URL`, `ARTIFACTS_DIR`, dashboard token, notifications.

## Docker

[`docker-compose.yml`](docker-compose.yml) defines **redis** (6379), **db** (Timescale/Postgres image; host port **5433** → container 5432), **api** (uvicorn on 8080), and **bot** (`python -m tr_bot`). Both app services mount a shared volume for `ARTIFACTS_DIR` so snapshots and ledger data survive restarts.

Examples:

```bash
docker compose up -d redis db
docker compose up -d bot
docker compose up -d api bot
```

Override secrets and symbols with Compose `environment` or an env file; never commit API keys. The [`Dockerfile`](Dockerfile) uses **Python 3.12-slim** and copies the `tr_bot` package plus `requirements.txt`.

## Testing

From the repository root (with dev dependencies installed from `requirements.txt`):

```bash
.venv/bin/pytest tests/
```

The suite covers training pipelines, startup preflight, dashboard helpers, signals, backtests, and related modules.

## Project layout

| Path | Role |
|------|------|
| `tr_bot/` | Application package (engine, config, execution, training, backtest, dashboard API, CoinDesk/CMC clients, startup pipeline, scheduler). |
| `tests/` | `pytest` regression tests. |
| `scripts/` | Operational scripts (e.g. sweep / tuning helpers). |
| `docs/` | Runbooks and feature guides (operations, CLI, dashboard, CoinDesk). |
| `artifacts/` | Runtime outputs (models, JSON state, ledgers, deployment pointers); contents depend on `.env` and are often gitignored. |

### Code map (selected modules)

| Area | Module(s) |
|------|-----------|
| Main loop | `tr_bot/engine.py` (`run_forever`) |
| Config / tier floors | `tr_bot/config.py` (`effective_tier_overrides()`, `DEPLOYMENT_TIER`, `_apply_tier_minimums`) |
| Indicators | `tr_bot/indicators.py` |
| Regime ML | `tr_bot/regime.py` |
| Signals & TA filters | `tr_bot/signals.py`, `tr_bot/ta_extended.py` |
| Decision aggregation | `tr_bot/decision.py` |
| Risk & sizing | `tr_bot/risk.py` |
| Positions, ATR exits | `tr_bot/position.py` |
| Orders | `tr_bot/execution.py` |
| Circuit breaker | `tr_bot/circuit_breaker.py` |
| Fund / NAV halt | `tr_bot/managers/fund.py` |
| Walk-forward backtest | `tr_bot/backtest/runner.py` |
| Startup pipeline | `tr_bot/startup/pipeline.py`, `tr_bot/startup/preflight.py` |
| Offline training | `tr_bot/training/pipeline.py` |
| Dashboard API | `tr_bot/dashboard_api.py`, `tr_bot/dashboard_pnl.py` |
| Promotion gates | `tr_bot/promotion/gates.py` |

## Documentation

- [`docs/dashboard.md`](docs/dashboard.md) — Dashboard sections, P&L payload, data sources, ledger.
- [`docs/cli.md`](docs/cli.md) — `python -m tr_bot.managers` (`train-run`, `backtest-run`, `--compact`, strategy commands).
- [`docs/operations.md`](docs/operations.md) — Start/stop, `--all` pipeline, paper vs live, training gates, CMC, adaptation, troubleshooting.
- [`docs/README.md`](docs/README.md) — Documentation index.
- [`docs/coindesk_reference.md`](docs/coindesk_reference.md), [`docs/coindesk_asset_catalog.md`](docs/coindesk_asset_catalog.md) — CoinDesk integration notes.

---

## Design and strategy reference

The sections below are a **technical specification** for the strategy and architecture. **Inline Python examples illustrate concepts**; they are not guaranteed to match imports or function names in the runnable tree—use the `tr_bot/` package as the source of truth.

### 1. Core principle for high win rate

Most retail AI bots fail because they attempt to predict market direction directly. Profitable strategies instead follow a **hierarchy of decisions**:

```
Market Data
    ↓
Regime Detection (ML)      →  Determines when strategy works
    ↓
Signal Generation (statistical) →  High-probability setups
    ↓
Risk Filter                 →  Protects capital
    ↓
LLM Context (optional)     →  Confirms sentiment
    ↓
Trade Execution
```

**Key insight:** Mean reversion strategies achieve 60–75% win rates **only in range-bound markets**. The bot emphasizes trading when the regime model detects a **range** regime. LLMs filter trades that conflict with recent news sentiment; they do not generate the primary signal.

### 2. Strategy design targeting 65–72% win rate

A high win rate is achieved by focusing on **mean reversion setups** rather than trend prediction. Common high-probability setups:

| Strategy              | Win rate range |
|-----------------------|----------------|
| RSI mean reversion    | 60–72%         |
| VWAP reversion        | 65–75%         |
| Liquidity sweep reversal | 62–70%     |

**Example setup – RSI mean reversion**

- **Buy conditions:** RSI < 30, price near support (below 20‑period EMA), volume spike, ATR not extreme.
- **Sell conditions:** RSI > 70, price near resistance (above 20‑period EMA), volume spike.

These conditions are backtested on liquid assets (BTC, ETH, SPY, AAPL) in research configurations.

### 3. System architecture overview

```
+------------------+     +-----------------------+     +------------------+
| Market Data      | --> | Feature Engineering   | --> | ML Regime        |
| Service          |     | (Indicators)          |     | Detection        |
+------------------+     +-----------------------+     +------------------+
                                                               |
                                                               v
+------------------+     +-----------------------+     +------------------+
| Risk Manager     | <-- | Decision Engine       | <-- | Signal Generator |
|                  |     | (Aggregation)         |     | (Mean Reversion) |
+------------------+     +-----------------------+     +------------------+
        |                                 ^
        |                                 |
        v                                 |
+------------------+     +-----------------------+     |
| Execution Layer  | --> | Optional LLM          | ----+
| (Broker API)     |     | Sentiment Agent       |
+------------------+     +-----------------------+
```

#### 3.1 Role of LLM

- **Not** used to generate trade signals.
- Used **only** to analyze news sentiment and provide a `bullish/bearish/neutral` flag.
- This flag acts as a **final filter**: if sentiment strongly opposes the signal, the trade is skipped.

### 4. Component details

#### 4.1 Market data service

Collects OHLCV from exchanges using `ccxt` or `yfinance`.

```python
import ccxt
import pandas as pd

exchange = ccxt.binance()

def fetch_ohlcv(symbol="BTC/USDT", timeframe="5m", limit=200):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    return pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
```

#### 4.2 Feature engineering

Computes technical indicators used by both the regime model and signals.

```python
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df['ema20'] = df['close'].ewm(span=20).mean()
    df['ema50'] = df['close'].ewm(span=50).mean()

    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    df['atr'] = (df['high'] - df['low']).rolling(14).mean()
    return df
```

#### 4.3 ML regime detection

A Random Forest classifier trained to identify **range** vs. **trend** conditions. Mean reversion signals are only considered in range markets in the conceptual flow below.

```python
from sklearn.ensemble import RandomForestClassifier

class RegimeModel:
    def __init__(self):
        self.model = RandomForestClassifier()

    def train(self, df: pd.DataFrame):
        X = df[['rsi', 'ema20', 'ema50', 'atr']]
        y = (df['close'] > df['ema20']).rolling(3).mean().fillna(0) > 0.5
        self.model.fit(X, y.astype(int))

    def predict(self, df: pd.DataFrame) -> str:
        X = df[['rsi', 'ema20', 'ema50', 'atr']].tail(1)
        regime = self.model.predict(X)[0]
        return "trend" if regime == 1 else "range"
```

#### 4.4 Signal generator – mean reversion

```python
def mean_reversion_signal(df: pd.DataFrame) -> str:
    last = df.iloc[-1]
    if last['rsi'] < 30 and last['close'] < last['ema20']:
        return "buy"
    if last['rsi'] > 70 and last['close'] > last['ema20']:
        return "sell"
    return "hold"
```

#### 4.5 Optional LLM sentiment agent

```python
import requests

def get_sentiment(news_text: str) -> str:
    prompt = f"""
    Analyze the sentiment of the following news headlines.
    Return JSON: {{"sentiment": "bullish|bearish|neutral"}}
    News: {news_text}
    """
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
    )
    result = response.json()["choices"][0]["message"]["content"]
    return eval(result)["sentiment"]
```

#### 4.6 Decision engine

Aggregates regime, signal, and sentiment.

- **Range regime:** mean reversion signals ([`mean_reversion_signal`](tr_bot/signals.py)); trades when sentiment does not veto (buy blocked if bearish, sell if bullish).
- **Trend regime:** optional **trend continuation** ([`trend_continuation_signal`](tr_bot/signals.py)) when **`TREND_STRATEGY_ENABLED=true`** — EMA stack (`close > ema20 > ema50` → buy, mirrored for sell). Same sentiment vetoes as range. When disabled, trend behaves like before (no entries from this engine).

Trend entries use **`trend_regime_ta_allows`** (ADX at least **`ADX_MIN_FOR_TREND`**) when extended TA is on; range entries use **`mean_reversion_ta_allows`** (ADX below **`ADX_MAX_FOR_MEAN_REVERSION`**).

#### 4.7 Risk management

```python
class RiskManager:
    def __init__(self, balance: float, risk_per_trade: float = 0.01):
        self.balance = balance
        self.risk_per_trade = risk_per_trade
        self.daily_trades = 0
        self.max_daily_trades = 5

    def position_size(self, stop_distance: float) -> float:
        risk_amount = self.balance * self.risk_per_trade
        return risk_amount / stop_distance

    def can_trade(self) -> bool:
        return self.daily_trades < self.max_daily_trades
```

**Production behavior (see `tr_bot/risk.py`, `tr_bot/engine.py`):**

- Live uses **`effective_risk_per_trade()`** (`RISK_PER_TRADE` × **`CANARY_RISK_MULTIPLIER`** in `live_canary`).
- Portfolio mode applies **`weight_for(symbol)`** to sizing; **`apply_canary_notional_cap`** caps notional on canary.
- **`MAX_TRADES_PER_DAY`** maps to the risk manager’s daily cap (backtest can enforce per UTC day with `BACKTEST_ENFORCE_MAX_TRADES_PER_DAY`).
- With **managers** enabled, **`FundManager`** can halt trading when drawdown exceeds **`MAX_DRAWDOWN_PCT`** (see `tr_bot/managers/fund.py`).

**Stop‑loss and take‑profit** (see `tr_bot/position.py`): stop = entry ± 1× ATR; take profit = entry ± 1.5× ATR.

**Live vs backtest exit economics:** `check_bar` uses bar high/low to decide TP/stop *hits*. The live loop records PnL using the bar **close** after an exit signal; the walk-forward backtest uses theoretical TP/stop **prices** for PnL. Treat reported Sharpe/profit factor as comparable only when you align this assumption.

#### 4.8 Execution layer

```python
def execute_trade(symbol: str, side: str, size: float):
    order = exchange.create_market_order(symbol, side, size)
    logger.info(f"Trade executed: {order}")
    return order
```

### 5. Trading loop – integrated example

Illustrative end-to-end loop (imports and helpers are schematic):

```python
import time
from logger import setup_logger
from config import SYMBOL, TIMEFRAME, RISK_PER_TRADE, MAX_TRADES_PER_DAY, USE_LLM

logger = setup_logger()
regime_model = RegimeModel()
risk = RiskManager(balance=10000, risk_per_trade=RISK_PER_TRADE)

initial_df = fetch_ohlcv(SYMBOL, TIMEFRAME, limit=500)
initial_df = compute_indicators(initial_df)
regime_model.train(initial_df)

while True:
    df = fetch_ohlcv(SYMBOL, TIMEFRAME, limit=200)
    df = compute_indicators(df)
    regime = regime_model.predict(df)
    signal = mean_reversion_signal(df)
    sentiment = "neutral"
    if USE_LLM:
        news = fetch_latest_news()
        sentiment = get_sentiment(news)
    decision = decision_engine(regime, signal, sentiment)
    if decision != "hold" and risk.can_trade():
        stop_distance = df['atr'].iloc[-1]
        size = risk.position_size(stop_distance)
        execute_trade(SYMBOL, decision, size)
        risk.daily_trades += 1
        logger.info(f"Trade {decision} executed. Size: {size}")
    else:
        logger.info("No trade this cycle")
    time.sleep(300)
```

### 6. Infrastructure and deployment

| Layer          | Technology        | Estimated monthly cost |
|----------------|-------------------|------------------------|
| Data           | `ccxt`, `yfinance`| $0                     |
| Indicators     | `pandas`, TA‑Lib  | $0                     |
| ML model       | `scikit-learn`    | $0                     |
| LLM (optional) | OpenAI API        | $5–15 (based on usage) |
| Execution      | Broker API        | $0 (paper trading)     |
| Backend        | FastAPI + Redis   | $10–20 (VPS)           |
| Database       | PostgreSQL        | included in VPS        |
| Monitoring     | Grafana (optional)| $0                     |
| **Total**      |                   | **~$30/month**         |

Use a **VPS** for 24/7 operation. Container layout is summarized in [Docker](#docker) above.

#### Automated trading (hands-off operation)

The trading process is **automatic**: `python -m tr_bot` (or `./start.sh`) runs `tr_bot.engine.run_forever` with no per-trade confirmation. Pause between cycles is **`POLL_SECONDS`**.

**Paper vs live**

- **Paper (default):** `PAPER_TRADING=true` — simulated orders; exchange keys are not attached to the client (avoids signed private calls).
- **Live (Binance spot):** set `PAPER_TRADING=false`, set **`BINANCE_API_KEY`** and **`BINANCE_SECRET`**, spot trading enabled on the key. Symbols must be **ccxt-style pairs** (e.g. `BTC/USDT`).

**Live spot caveat:** Opening a **new** position from a raw **`sell`** signal is **disabled in live** (`live_sell_disabled` in `engine.py`) for spot; **closing a long** via the position-exit path still issues a sell.

**Running 24/7**

- **Docker:** shared `ARTIFACTS_DIR` volume between `bot` and `api` (see [Docker](#docker)).
- **VPS / bare metal:** run `./start.sh` or `.venv/bin/python -m tr_bot` under **systemd**, **tmux**, or similar, with restart on failure.

**Optional scheduler:** `./start.sh --scheduler` runs `tr_bot.scheduler` for periodic training/backtest; it does **not** replace the trading loop. Interval and gate variables are `SCHEDULER_*`, `STARTUP_*`, and training/backtest keys in [`.env.example`](.env.example); narrative detail is in [`docs/operations.md`](docs/operations.md).

**Safety and monitoring:** `RISK_PER_TRADE`, `MAX_TRADES_PER_DAY`, `MAX_DRAWDOWN_PCT`, circuit breaker, manual halt / fund halt, notifications ([`.env.example`](.env.example)). FastAPI dashboard (`./start.sh --api`) reads the same `ARTIFACTS_DIR` as the bot.

### 7. Expected performance

When tuned on liquid assets with RSI mean reversion and regime filtering, research targets have included:

| Metric          | Target range |
|-----------------|--------------|
| Win rate        | 65–72%       |
| Profit factor   | 1.5–2.0      |
| Max drawdown    | <10%         |
| Avg trades/day  | 2–5          |

Past or backtested performance does not guarantee future results.

### Scheduled training, backtest, and CLI

Offline **regime training** and **walk-forward backtest** on configured `SYMBOL` / `TIMEFRAME`:

```bash
.venv/bin/python -m tr_bot.managers train-run
.venv/bin/python -m tr_bot.managers backtest-run
```

Artifacts land under `ARTIFACTS_DIR` (e.g. `training/last_run.json`, `training/backtest_last_run.json`) and surface in the dashboard API.

**Scheduler:** run `./start.sh --scheduler` or `python -m tr_bot.scheduler`. Configure `SCHEDULER_TRAINING_INTERVAL_SECONDS`, `SCHEDULER_BACKTEST_INTERVAL_SECONDS`, `SCHEDULER_TICK_SECONDS`, and related flags in `.env` (see [`.env.example`](.env.example)).

**`./start.sh --all` hard-block path:** preflight → `train-run` → bounded tuning → `backtest-run` → startup backtest gate; on failure with `STARTUP_HARD_BLOCK=true`, trading does not start. Full stage list: [`docs/operations.md`](docs/operations.md).

**Cron / systemd (without long-lived scheduler):**

```cron
0 2 * * * cd /path/to/tr_bot && .venv/bin/python -m tr_bot.managers train-run >> /var/log/tr_bot_train.log 2>&1
15 * * * * cd /path/to/tr_bot && .venv/bin/python -m tr_bot.managers backtest-run >> /var/log/tr_bot_backtest.log 2>&1
```

**Compact CLI output:**

```bash
python -m tr_bot.managers train-run --compact
python -m tr_bot.managers backtest-run --compact
```

**Backtest parity and metrics (selected `.env` knobs):**

| Variable | Role |
|----------|------|
| `BACKTEST_ENFORCE_REGIME_CONFIDENCE`, `BACKTEST_ENFORCE_SIGNAL_STRENGTH`, `BACKTEST_ENFORCE_LOSS_COOLDOWN` | Mirror live gating when `true` |
| `BACKTEST_USE_CIRCUIT_BREAKER`, `BACKTEST_CIRCUIT_MAX_CONSECUTIVE_LOSSES` | Circuit behavior in sim |
| `BACKTEST_EXECUTION_REALISM_*` | Probabilistic entry skip/reject/partial fills |
| `BACKTEST_EXIT_REALISM_*`, `BACKTEST_EXIT_DELAY_FILL_NEXT_BAR`, `BACKTEST_EXIT_DELAY_FILL_PRICE` | Exit delay / partial / slippage |
| `BACKTEST_MIN_RISK_METRIC_OBS` | Min return steps before Sharpe/Sortino are **published** |
| `BACKTEST_ANNUAL_GAP_MULTIPLIER` | Gap handling when inferring steps/year |

Walk-forward code: `tr_bot/backtest/runner.py`. Metrics metadata: `risk_metric_meta` in `training/backtest_last_run.json`.

### Dashboard: P&L and cost analysis

The dashboard includes **P&L and cost analysis** backed by artifacts and `trade_ledger.jsonl`. HTTP: `GET /api/dashboard/summary` (optional `DASHBOARD_TOKEN`). Details: [`docs/dashboard.md`](docs/dashboard.md).

**Data sources**

- `artifacts/equity_history.json` — equity rollups
- `artifacts/fund_state.json` — NAV context
- `artifacts/training/backtest_last_run.json` — last simulation summary
- `artifacts/trade_ledger.jsonl` — closed trades

**Config snapshot:** `config.effective_tier_overrides` exposes `env_before_tier_apply` vs `effective_after_tier` for tier-gated keys.

**Trade summary:** distinguishes TP/stop outcome rate from net-PnL win rate after fees.

**Cost assumptions:** ledger display uses `LIVE_FEE_BPS` / `LIVE_SLIPPAGE_BPS`; backtest rows use `BACKTEST_FEE_BPS` / `BACKTEST_SLIPPAGE_BPS`.

### Operations notes

- **`stop.sh`** — portable prompt handling across bash/zsh/sh.
- **Paper mode** — no API keys on the exchange client to avoid private signed endpoint errors.
- **Diagnostics:** `address already in use` → stale process on `8080`; `Ready · Postgres down` → DB URL/auth; repeated sentiment `429` → LLM rate limit (engine falls back to neutral).

### 8. Safety and hallucination protection

- **LLM not authoritative:** sentiment only vetoes; it never initiates a trade.
- **Structured outputs:** JSON schema validation where configured.
- **Confidence threshold:** ignore weak LLM output when below configured confidence (e.g. `LLM_MIN_CONFIDENCE`).
- **Latency guards:** timeout and neutral fallback.
- **Circuit breaker:** stop new entries after consecutive **outcome** losses (`CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES`); state under `artifacts/circuit_state.json`; reset via `.circuit_reset` / dashboard (see `tr_bot/circuit_breaker.py`).

### 9. Conclusion

The design targets disciplined execution by combining **regime detection** and **mean reversion** (with optional trend and LLM veto), modular services, and explicit risk controls. The runnable implementation lives in `tr_bot/` with FastAPI, managers, walk-forward backtest, and optional Postgres/Redis integrations as described above and in `docs/`.

## Disclaimer

This software is provided for **educational and operational experimentation** only. It is **not financial advice**. Cryptocurrency and derivatives trading involve substantial risk of loss. Use **paper mode** until you fully understand behavior, fees, and failure modes. The authors and contributors are **not responsible** for losses, bugs, exchange outages, or regulatory issues. You are solely responsible for compliance, API key security, and any capital you deploy.

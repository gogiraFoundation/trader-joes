# tr_bot skills

Agent handbook for this repository. The runnable package under `tr_bot/` is the source of truth. `README.md` sections that show schematic Python are conceptual; they are not guaranteed to match imports or function names.

Use this file before changing trading, risk, execution, training, promotion, or dashboard behavior. Each skill names the modules that own the behavior, the invariants that must stay true, and the tests that lock them.

## How to work in this repo

1. Identify the skill below that owns the change. If two skills touch the same path (for example engine and exits), satisfy both.
2. Read the owning modules and the listed tests before editing. Match the surrounding style: typed functions, explicit config reads, fail-closed errors.
3. Configuration lives in `.env`. `.env.example` is the soak-oriented authoritative list of knobs (often stricter than code defaults, e.g. `STARTUP_BACKTEST_MIN_TRADES`). Full names live in `tr_bot/config.py`. Do not invent a parallel settings object.
4. Runtime outputs go under `ARTIFACTS_DIR` (default `artifacts/`). Do not commit secrets, ledgers, models, or live state.
5. Paper trading is the default. Live Binance spot is opt-in and fail-closed.
6. Lock behavior with `pytest` under `tests/`. Run the tests that cover the skill you changed. From the repo root: `.venv/bin/pytest tests/`.
7. Python target is 3.12+ (Dockerfile). Local venvs may be 3.13. Type-check config is `pyrightconfig.json` (`venvPath` `.`, `venv` `.venv`).

Illustrative performance ranges in the README (win rate, profit factor, drawdown) are research targets, not guarantees and not acceptance criteria.

## Safety invariants

These apply across skills. A change that violates one of them is incorrect even if tests are green.

- **LLM is a veto, never a signal.** Sentiment is `bullish`, `bearish`, or `neutral`. It can block a statistical or SMC decision. It must not open a trade by itself. Timeouts, rate limits, and parse failures fall back to `neutral`. See `tr_bot/sentiment.py` and `tr_bot/decision.py`. Supervisor / Fund Manager agents (`tr_bot/agents/`) may notify, pause/resume via halt file, request circuit reset, emit allowlisted proposals, or run rebalance scan — never `execute_trade`.
- **Circuit breaker blocks new entries only.** ATR / stop exits, holding snapshots, and `ExitCoordinator` closes still run. `REBALANCE_*` execution contexts skip the circuit. See `tr_bot/circuit_breaker.py` and `tr_bot/exit_coordinator.py`.
- **One closer.** `ExitCoordinator` is the sole authority for submitting close orders. Persist `exit_state=pending` with a stable `clientOrderId` before submission.
- **Live spot does not open shorts from a raw sell.** A new live sell entry is rejected (`live_sell_disabled` in `tr_bot/engine.py`). Closing an existing long still sells.
- **`EXIT_POLICY=exchange_native` is rejected** for the live and paper engine until protective-order reconciliation is integrated. Use `software_polled`. `BACKTEST_EXIT_POLICY` is separate and is not checked by `validate_runtime_config()`.
- **Live starts fail closed.** `tr_bot/runtime_validation.py` requires Binance key and secret, ccxt pairs (`BTC/USDT`), `LIVE_MAX_NOTIONAL_USDT > 0`, and canary caps when `DEPLOYMENT_TIER=live_canary`.
- **Paper mode does not attach API keys** to the exchange client. Do not “fix” paper by signing private endpoints.
- **Decision-log and notification failures must not block the trading loop.**
- **Artifact paths stay inside `ARTIFACTS_DIR`.** Use `tr_bot/managers/security.py` (`resolve_path_under_artifact_dir`, `safe_artifact_path`, `redact_secrets`). Do not log API keys, dashboard tokens, or secrets.
- **Dashboard writes are off by default.** `DASHBOARD_WRITES_ENABLED` plus a write token (or `DASHBOARD_TOKEN`) is required. Missing opt-in is `403`, bad token is `401`.
- **Do not place historical CMC global-metrics calls on the live hot path.**

## Skill index

| Skill | Owns |
| --- | --- |
| [Config and tiers](#config-and-tiers) | `.env`, `config.py`, deployment tier floors |
| [Trading loop](#trading-loop) | `engine.py`, poll cycle, audit, snapshots |
| [Legacy decisions](#legacy-decisions) | Regime, mean reversion, trend, TA vetoes |
| [SMC and scoring](#smc-and-scoring) | `STRATEGY_ENGINE=smc\|shadow`, signal pipeline |
| [Exits and fills](#exits-and-fills) | Exit plan, coordinator, watchdog, ledger |
| [Risk and capital](#risk-and-capital) | Sizing, circuit, fund halt, rebalancer |
| [Fees and PnL](#fees-and-pnl) | Fee precedence, realized PnL, dashboard costs |
| [Training and regime](#training-and-regime) | Offline fit, validation gates, macro features |
| [Backtest and replay](#backtest-and-replay) | Walk-forward, parity, engine compare |
| [Promotion](#promotion) | Profiles, gates, pointers, freeze, revert |
| [Startup and scheduler](#startup-and-scheduler) | `--all` pipeline, periodic jobs |
| [Market data](#market-data) | ccxt, Yahoo, CoinDesk, CMC |
| [Dashboard](#dashboard) | FastAPI summary, static UI, write actions |
| [Persistence and security](#persistence-and-security) | Artifacts, Postgres, path safety |
| [Notifications](#notifications) | Event facade, sanitization, throttle |
| [Fundamentals](#fundamentals) | CoinGecko and earnings vetoes |
| [Technicals](#technicals) | RSI, EMA, ATR, ADX filters, extended TA |
| [Quant](#quant) | Sizing, reward-to-risk, Sharpe, bootstrap gates |
| [Decision generator](#decision-generator) | Legacy vs SMC path, then entry gates |
| [Automation](#automation) | Startup pipeline, scheduler, adaptation, dashboard jobs |
| [Research](#research) | Local ledger queries, plus web research skills |
| [Testing](#testing) | Isolation, what each suite locks |

---

## Config and tiers

**When:** any new knob, default, tier floor, or live/paper branch.

**Source of truth**

- `tr_bot/config.py` reads the environment. Add the knob there and in `.env.example` in the matching group.
- `DEPLOYMENT_TIER` is `paper`, `live_canary`, or `live_full`. If unset, it is inferred from `PAPER_TRADING` (`true` → `paper`, otherwise `live_full`). An unrecognized explicit value falls back to `paper`.
- Tier floors are applied in config (`effective_tier_overrides()`, `_apply_tier_minimums`). The dashboard config snapshot distinguishes `env_before_tier_apply` from `effective_after_tier`.
- Active strategy profile overlay is applied from `tr_bot/strategy/profile.py` after env load (`apply_profile_overlay`, `active_config_hash`).

**Invariants**

- Defaults must be safe for paper: `PAPER_TRADING=true`, `STRATEGY_ENGINE=legacy`, `EXIT_POLICY=software_polled`, dashboard writes off.
- Unknown enum values fall back to the safe member (`legacy`, `software_polled`, `atr`, `v1`, `paper`). Do not raise on a typo in a way that takes down an already-running paper loop unless the value is a live safety control.
- Live safety values are re-checked by `validate_runtime_config()` / `assert_runtime_config_safe()`, not only at import time.
- Canary, when tier is `live_canary`: non-empty `CANARY_SYMBOLS`, `0 < CANARY_RISK_MULTIPLIER <= 0.25`, `0 < CANARY_MAX_NOTIONAL_USDT <= 50`.
- `LIVE_MAX_NOTIONAL_USDT > 0` is required for non-paper. `PAPER_MAX_NOTIONAL_USDT=0` means the paper cap is disabled.
- `MIN_ATR_PCT=0` disables the ATR floor. Fee/R soak uses ~`0.0045` (~3× RT at 7.5 bps). `EXIT_WATCHDOG_INTERVAL_S` minimum is 1 second.
- `MAX_OPEN_POSITIONS` (default 0 = unlimited) caps concurrent strategy opens across the portfolio. Live reason `max_open_positions`. Soak value: `1`.

**Strategy engine values**

| `STRATEGY_ENGINE` | Behavior |
| --- | --- |
| `legacy` | Mean reversion, plus trend only when `TREND_STRATEGY_ENABLED=true`. |
| `smc` | Entries from `tr_bot/strategy/signal_pipeline.py` and scoring. |
| `shadow` | Trade the legacy path. Evaluate SMC in parallel and log both in the decision audit. |

**Tests:** `tests/test_strategy_profile_config.py`, `tests/test_strategy_tier.py`, `tests/test_runtime_validation.py`, `tests/test_profile_validation.py`.

---

## Trading loop

**When:** poll cycle, symbol evaluation, entry gating, snapshots, pause, or decision audit.

**Source of truth:** `tr_bot/engine.py` (`run_forever`). Entry point is `python -m tr_bot`. Interval is `POLL_SECONDS`.

**Cycle shape**

1. Load open positions, fund state, and the saved `RegimeModel`.
2. For each active symbol, fetch OHLCV on `TIMEFRAME` and compute indicators.
3. Evaluate legacy and, when configured, SMC. Shadow mode trades legacy and records the SMC view.
4. Apply gates: regime confidence, signal strength, TA, fundamentals, ATR floor, cooldown, daily trade cap, circuit, halt, notional caps, live-sell rule.
5. Size and submit entries through `tr_bot/execution.py`.
6. Exits go through `ExitCoordinator`. Between polls, `sleep_with_exit_watchdog` checks stops about every `EXIT_WATCHDOG_INTERVAL_S`.
7. Persist trading snapshot, equity point, and optional decision-audit JSONL. No-trade rows may go to Postgres (`decision_events`). Failures are logged and do not stop the loop.

**Invariants**

- The loop is automatic. Do not add a per-trade confirmation prompt.
- One process holds one tier. Paper soak and live are separate processes that may share `ARTIFACTS_DIR` but must not share `DEPLOYMENT_TIER` / `PAPER_TRADING`. See `docs/strategy_promotion.md`.
- Manual halt file (`TRADING_HALT_FILENAME`, default `TRADING_HALTED`) and fund halt still block new risk.
- Decision audit schema version is `DECISION_AUDIT_SCHEMA_VERSION` in `tr_bot/strategy/decision_audit.py`. Bump it when the JSONL shape changes, and keep shadow dual-views on schema v2.
- Audit writes are throttled by `DECISION_AUDIT_EVERY_N_LOOPS` and best-effort on `OSError`.
- Portfolio weights come from `tr_bot/managers/portfolio.py` (`active_symbols`, `weight_for`) when managers are enabled.

**Entry caps (apply in this spirit, in engine / `tr_bot/strategy/tier.py`)**

- `apply_live_notional_cap` — hard USDT cap per live entry.
- `apply_canary_notional_cap` and `canary_allows_symbol` — canary tier only.
- `apply_paper_notional_cap` — only when `PAPER_MAX_NOTIONAL_USDT > 0`.
- `atr_meets_min_pct` — skip entries when ATR/close is below `MIN_ATR_PCT` (reason `atr_too_small_for_costs`).
- `open_positions_at_limit` / `MAX_OPEN_POSITIONS` — block a new symbol when the book already has enough opens (`max_open_positions`).

**Tests:** `tests/test_engine_entry.py`, `tests/test_engine_pause.py`, `tests/test_execution_live_limits.py`.

---

## Legacy decisions

**When:** RSI / EMA / ATR signals, regime labels, ADX filters, or the sentiment veto on the legacy path.

**Source of truth**

- Indicators: `tr_bot/indicators.py` (`compute_indicators`, `compute_all_indicators`). Extended filters: `tr_bot/ta_extended.py`.
- Regime: `tr_bot/regime.py` (`RegimeModel.predict_with_confidence`). Labels used by the engine are `range` and `trend`.
- Signals: `tr_bot/signals.py` — `mean_reversion_signal` in range; `trend_continuation_signal` only when trend strategy is enabled (EMA stack: `close > ema20 > ema50` for buy, mirrored for sell).
- Aggregation: `tr_bot/decision.py` `decision_engine`.

**Decision rules**

- `ta_ok` or `fundamentals_ok` false → `hold`.
- Range: buy if signal is buy and sentiment is not bearish; sell if signal is sell and sentiment is not bullish.
- Trend: same vetoes, and only when `trend_strategy_enabled` is true. Otherwise trend is `hold`.
- Extended TA: trend entries use `trend_regime_ta_allows` (ADX at least `ADX_MIN_FOR_TREND`); range entries use `mean_reversion_ta_allows` (ADX below `ADX_MAX_FOR_MEAN_REVERSION`).
- Signal branch names recorded by the engine: `mean_reversion`, `trend`, or `none`.

**Invariants**

- Do not let the LLM choose the side. Wire new context as another veto or score input, not as a replacement for `mean_reversion_signal` / the SMC pipeline.
- Live regime prediction uses bars from `TIMEFRAME`. The saved model was fit on `TRAIN_TIMEFRAME`. Mixing daily training with 5m execution changes the feature distribution. Prefer `TIMEFRAME == TRAIN_TIMEFRAME`.
- Feature columns the forest expects include RSI, EMA20, EMA50, and ATR. If training adds `cmc_macro_*` columns (`TRAIN_REGIME_MACRO_FEATURES`), the live frame must supply the same columns or prediction falls back neutral.

**Tests:** `tests/test_decision.py`, `tests/test_signals.py`, `tests/test_trend_signal.py`, `tests/test_indicators.py`, `tests/test_regime.py`.

---

## SMC and scoring

**When:** smart-money concepts, multi-timeframe bias, trade score, or `STRATEGY_ENGINE` other than legacy.

**Source of truth**

- Detectors: `tr_bot/smc/` (`structure`, `liquidity`, `order_blocks`, `fvg`, `bos_mss`, `zones`, `types`).
- Pipeline: `tr_bot/strategy/signal_pipeline.py`. It is shared by the live engine and the backtest. Do not fork a second SMC decision path.
- Score: `tr_bot/strategy/scoring.py`. Proposal object: `tr_bot/strategy/trade_proposal.py`.
- Context and timeframes: `tr_bot/strategy/market_context.py`, `tr_bot/strategy/multi_tf.py`.
- Explainability: `tr_bot/strategy/explainability.py`.

**Pipeline gates (early exit sets `blocked_gate`)**

1. HTF bias
2. Market regime suitability
3. Market structure
4. Liquidity analysis
5. SMC confluence (order block, FVG, OTE, BOS, MSS)
6. Price-action confirmation
7. Technical confirmation
8. Quantitative validation
9. Trade scoring
10. Risk, minimum score, and reward-to-risk

**Defaults**

- `SMC_MIN_TRADE_SCORE=65`, `SMC_MIN_SMC_FACTORS=2`.
- Timeframes: HTF `4h`, primary `1h`, entry `15m`, exec `5m` (`SMC_HTF_TIMEFRAME`, `SMC_PRIMARY_TIMEFRAME`, `SMC_ENTRY_TIMEFRAME`, `SMC_EXEC_TIMEFRAME`).
- `EXIT_MODE` is `atr` (default), `structural`, or `hybrid`.

**Invariants**

- Shadow mode must not submit SMC orders. It logs the SMC view beside the legacy decision.
- Kill switch back to the production path is `STRATEGY_ENGINE=legacy`, plus the existing halt and freeze controls.
- Compare engines offline with `python -m tr_bot.managers backtest-compare`. Artifacts: `artifacts/training/backtest_compare.json`, `artifacts/research/smc_go_no_go.json`.
- Promotion evidence is `strategy-compare`. It blocks promotion only when `PROMOTION_REQUIRE_COMPARATOR=true`.
- Detectors stay pure functions of OHLCV. Side effects (orders, artifact writes, notifications) belong in the engine or coordinator.

**Tests:** `tests/test_smc_detectors.py`, `tests/test_scoring.py`, `tests/test_hiatp_core.py`.

---

## Exits and fills

**When:** stops, take-profit, partial fills, paper fill prices, protective orders, or close-side PnL.

**Source of truth**

- Plan construction: `tr_bot/exit_plan.py` (`build_exit_plan`, `simulate_software_polled_exit_price`).
- Position model and bar hit-test: `tr_bot/position.py` (`OpenPosition`). Conceptual ATR geometry is stop at entry ± 1× ATR and take-profit at entry ± 1.5× ATR; confirm multipliers in `position.py` / the active profile before changing them.
- Close authority: `tr_bot/exit_coordinator.py`.
- Inter-poll monitor: `tr_bot/exit_watchdog.py`.
- Order submit and fill parsing: `tr_bot/execution.py` (`execute_trade`, `resolve_fill_price`, `resolve_fill_qty`, `place_protective_stop`).
- Open-position persistence: `tr_bot/open_positions_state.py`.
- Closed-trade append: `tr_bot/trade_ledger.py`.
- Protective-order reconciliation stub: `tr_bot/protective_reconcile.py`. Do not enable `exchange_native` by bypassing `runtime_validation`.
- Single-bar replay used by parity tests: `tr_bot/replay.py`.

**Coordinator contract**

- `request_exit(symbol, outcome, reason, fallback_price, execution_context)`.
- `outcome` is `win` or `loss` for circuit and trigger level.
- `execution_context` is `STRATEGY_SIGNAL` or `REBALANCE_TRIM` (and other `REBALANCE_*`). Rebalance exits skip the circuit.
- Result dict includes `status` and, when relevant, `closed`, `remaining`, `reason`.
- Per-symbol locks plus a global lock. Client order ids are `tx` plus 32 hex chars.
- Terminal acknowledgements: `skipped`, `rejected`, `canceled`, `cancelled`, `expired`, `unknown_execution`, `unknown`.
- Persist pending state before the exchange call. Finalize, mark unknown, or keep `partially_filled` from the response. Do not drop a position from memory because a request timed out.

**Price semantics**

- `check_bar` uses bar high/low to decide whether TP or stop was touched.
- The live loop records PnL from the bar close after an exit signal. Walk-forward backtest uses theoretical TP/stop prices. Sharpe and profit factor are comparable only when that assumption is aligned. Paper fills use simulated prices and do not measure Binance slippage.
- Backtest realism knobs (`BACKTEST_EXIT_REALISM_*`, `BACKTEST_EXIT_DELAY_FILL_NEXT_BAR`, `BACKTEST_EXECUTION_REALISM_*`) belong in the backtest runner, not in the live coordinator.

**Tests:** `tests/test_exit_plan.py`, `tests/test_exit_coordinator.py`, `tests/test_paper_exit_fill.py`, `tests/test_fill_prices.py`, `tests/test_replay_parity.py`, `tests/test_trade_ledger.py`.

---

## Risk and capital

**When:** position size, daily caps, drawdown halt, consecutive-loss breaker, portfolio weights, or inventory trim.

**Source of truth**

- `tr_bot/risk.py` — `RiskManager`. Live size uses `effective_risk_per_trade()` (`RISK_PER_TRADE` × `CANARY_RISK_MULTIPLIER` on `live_canary`). `MAX_TRADES_PER_DAY` is the daily cap. Backtest can enforce it per UTC day with `BACKTEST_ENFORCE_MAX_TRADES_PER_DAY`.
- `tr_bot/strategy/entry_gates.py` — `open_positions_at_limit` / `MAX_OPEN_POSITIONS` (live concurrent opens; reason `max_open_positions`).
- `tr_bot/circuit_breaker.py` — consecutive **outcome** losses (`CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES`). State: `artifacts/circuit_state.json`. Reset: `.circuit_reset` in the project root, or the dashboard write action when writes are enabled.
- `tr_bot/managers/fund.py` — NAV and `MAX_DRAWDOWN_PCT` halt when managers are enabled. Snapshot command: `python -m tr_bot.managers fund-snapshot` → `fund_state.json`.
- `tr_bot/managers/portfolio.py` — symbol universe and weights.
- `tr_bot/managers/rebalancer.py` — drift / lag trim.
- `tr_bot/quote_usdt.py` — quote to USDT conversion. `QuoteUsdtRateError` must fail the sizing path closed, not invent a rate.
- `tr_bot/correlation.py` — cross-asset exposure helper. Do not silently ignore it if a change adds a new concurrent-entry path.

**Rebalancer**

- Phase A1 is read-only: `python -m tr_bot.managers rebalance-scan`. Writes `artifacts/rebalancer/latest.json`. Emits `rebalance_alert` when `REBALANCER_ENABLED=true`.
- Phase A2: soak with `REBALANCER_EXECUTE_TRIMS=false`, then set it true and run `rebalance-trim`. If trims are not enabled, the command plans only and exits 2.
- Open strategy positions close via `artifacts/rebalancer/exit_requests.json` and `ExitCoordinator` with `execution_context=REBALANCE_TRIM`.
- Idle exchange inventory is sold directly.
- Halt file uses `REBALANCE_HALT_REASON` and is cleared only when that reason matches.
- This path does not withdraw capital or transfer off-exchange.

**Invariants**

- A tripped circuit, fund halt, or manual halt stops new entries. It does not freeze protective exits.
- Canary risk multiplier in config may default to `1.0` for paper imports. Live canary validation rejects anything outside `(0, 0.25]`. Do not weaken that check to match the import default.
- Sizing is risk budget divided by stop distance, then weight and notional caps. Never size from account balance alone without the stop distance.

**Tests:** `tests/test_circuit_breaker.py`, `tests/test_fund_manager.py`, `tests/test_rebalancer.py`, `tests/test_portfolio.py`, `tests/test_quote_usdt.py`, `tests/test_fee_r_guards.py`, `tests/test_max_open_positions.py`.

---

## Fees and PnL

**When:** cost assumptions, realized PnL, ledger sums, or dashboard P&L cards.

**Source of truth:** `tr_bot/fees.py`, `tr_bot/pnl_costs.py`, `tr_bot/dashboard_pnl.py`, `tr_bot/trade_ledger.py`.

**Taker bps precedence** (`effective_taker_bps`, excluding the legacy flat `LIVE_FEE_BPS` override path documented on the function):

1. `LIVE_FEE_TAKER_BPS > 0` → global explicit taker.
2. `LIVE_FEE_USE_ALGO_TIERS` → symbol table (VIP5+ and some stable pairs at 0; BTC/ETH vs USDT/USDC/FDUSD at 5 bps; else 12).
3. `LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS` → `LIVE_FEE_BNB_TAKER_BPS` when paying fees with BNB, else `LIVE_FEE_STANDARD_TAKER_BPS`.
4. Else `0.0`.

`LIVE_FEE_MAKER_BPS` is not used (taker-only tier). Ledger display uses `LIVE_FEE_BPS` / `LIVE_SLIPPAGE_BPS`. Backtest rows use `BACKTEST_FEE_BPS` / `BACKTEST_SLIPPAGE_BPS`. Do not mix those pairs in one reported number.

Dashboard trade summary distinguishes TP/stop outcome rate from net-PnL win rate after fees.

**Tests:** `tests/test_fees.py`, `tests/test_pnl_costs.py`, `tests/test_dashboard_pnl.py`, `tests/test_trade_ledger_sum.py`.

---

## Training and regime

**When:** offline fit, dataset sources, walk-forward validation, or macro feature columns.

**Source of truth**

- Pipeline: `tr_bot/training/pipeline.py`.
- Dataset: `tr_bot/training/dataset.py`, `tr_bot/training/regime_dataset.py`, `tr_bot/training/exchange_history.py`, `tr_bot/training/yahoo.py`, `tr_bot/training/cmc.py`, `tr_bot/training/cmc_macro.py`.
- Validation: `tr_bot/training/regime_validation.py`.
- Train-time backtest gate: `tr_bot/training/backtest_gate.py` and shared thresholds in `tr_bot/startup/backtest_gate_thresholds.py`.
- CLI: `python -m tr_bot.managers train-run [--compact] [--promote]`. `--promote` copies to `REGIME_MODEL_PATH`.
- Artifacts: `ARTIFACTS_DIR/training/last_run.json`.

**Anti-overfit knobs**

- `TRAIN_RUN_VALIDATION`, `TRAIN_TS_SPLITS`, `TRAIN_MIN_ROWS_PER_FOLD`, `TRAIN_MIN_OOF_F1`, `TRAIN_MAX_TRAIN_VAL_GAP`.
- `TRAIN_VALIDATION_STRICT` fails the process when gates fail.
- `TRAIN_VALIDATION_PER_GROUP=true` when more than one exchange symbol is listed, so one series cannot dominate.
- `TRAIN_BACKTEST_GATE` plus `TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT` / `TRAIN_BACKTEST_MAX_DRAWDOWN_PCT`. If both thresholds are unset, the train gate uses **−1% min return** and **12% max drawdown**. The train gate does not check trade count.

**Data alignment**

- Training timeframe is `TRAIN_TIMEFRAME`. Live prediction timeframe is `TIMEFRAME`. Keep them equal for intraday.
- Prefer `TRAIN_SOURCES=exchange` with comma-separated `TRAIN_EXCHANGE_SYMBOL` ccxt pairs. Primary series is `TRAIN_PRIMARY_ASSET` or the first CSV entry. `TRAIN_PRIMARY_SAMPLE_WEIGHT` (for example `3.0`) lets the live pair dominate.
- Do not also list the same base on Yahoo.
- `TRAIN_MAX_ROWS` is a global timestamp-ordered tail. Raise it or leave `0` when training many pairs.
- `TRAIN_EXCHANGE_LIMIT` alone does not paginate. Set `TRAIN_EXCHANGE_LOOKBACK_DAYS > 0` or both start and end milliseconds. Deep fetches use `fetch_ohlcv_deep` in `tr_bot/market_data.py`.
- `TRAIN_BACKTEST_SYMBOL` is a single pair, normally the live `SYMBOL`.
- Yahoo skip: a failed ticker is skipped unless `YAHOO_TRAIN_FAIL_ON_SKIP=true`. Log marker: `[yahoo_symbol_skipped]`. Exchange skip is analogous unless `TRAIN_EXCHANGE_FAIL_ON_SKIP=true`.
- CMC identity: prefer `CMC_TRAIN_IDS` or `CMC_TRAIN_SLUGS`. With symbols only, keep `CMC_MAP_RESOLVE_SYMBOLS=true`.
- Macro merge (`CMC_TRAIN_MACRO_MERGE_ENABLED`) uses backward `merge_asof` on `timestamp` so a bar only sees points published at or before that time.

`./start.sh --all` forces `TRAIN_BACKTEST_GATE=false` during `train-run` and evaluates the strategy afterward with `backtest-run` and `STARTUP_BACKTEST_*`. Keep `TRAIN_BACKTEST_*` aligned for standalone and scheduler runs.

**Tests:** `tests/test_regime_dataset.py`, `tests/test_regime_validation.py`, `tests/test_training_exchange_dataset.py`, `tests/test_training_pipeline_yahoo_skip.py`, `tests/test_yahoo_training_quality.py`, `tests/test_yahoo_training_ticker.py`, `tests/test_train_backtest_gate.py`, `tests/test_cmc_training.py`, `tests/test_cmc_macro.py`.

---

## Backtest and replay

**When:** walk-forward simulation, metrics, cost realism, or live/backtest parity.

**Source of truth:** `tr_bot/backtest/runner.py`. CLI: `python -m tr_bot.managers backtest-run [--compact]`. Artifact: `artifacts/training/backtest_last_run.json` (`risk_metric_meta` for Sharpe/Sortino publication).

**Parity knobs**

| Variable | Role |
| --- | --- |
| `BACKTEST_ENFORCE_REGIME_CONFIDENCE`, `BACKTEST_ENFORCE_SIGNAL_STRENGTH`, `BACKTEST_ENFORCE_LOSS_COOLDOWN` | Mirror live gates when true |
| `BACKTEST_USE_CIRCUIT_BREAKER`, `BACKTEST_CIRCUIT_MAX_CONSECUTIVE_LOSSES` | Circuit in sim |
| `BACKTEST_EXECUTION_REALISM_*` | Entry skip, reject, partial fills |
| `BACKTEST_EXIT_REALISM_*`, `BACKTEST_EXIT_DELAY_FILL_NEXT_BAR`, `BACKTEST_EXIT_DELAY_FILL_PRICE` | Exit delay and slippage |
| `BACKTEST_MIN_RISK_METRIC_OBS` | Minimum return steps before Sharpe/Sortino are published |
| `BACKTEST_ANNUAL_GAP_MULTIPLIER` | Gap handling for steps/year |
| `BACKTEST_SENTIMENT_MODE` | Fixed `neutral`, `bullish`, or `bearish` |

LLM sentiment is not replayed historically. The runner records that the sentiment label is fixed.

**Startup gate** (`STARTUP_BACKTEST_*`, optional `ADAPT_*` overrides): default **−1% min return**, **12% max drawdown**, **3 min trades**. Zero-trade profiles are rejected at startup. This is stricter than the train gate, which has no trade-count check.

**Engine compare:** `backtest-compare` runs legacy vs SMC on the same bars and writes the go/no-go artifact. `strategy-compare` is the promotion wrapper.

**Invariants**

- Shared decision code (legacy `decision_engine`, SMC `signal_pipeline`, `replay_bar_software_polled`) must stay deterministic. Do not read the clock, the network, or `ARTIFACTS_DIR` inside a per-bar function.
- Changing exit price semantics requires a replay-parity test update in the same change.

**Tests:** `tests/test_backtest.py`, `tests/test_replay_parity.py`, `tests/test_train_backtest_gate.py`.

---

## Promotion

**When:** profile versions, soak gates, pointer files, freeze, or revert.

**Source of truth**

- Gates: `tr_bot/promotion/gates.py`. Stats: `tr_bot/promotion/stats.py`. Service: `tr_bot/promotion/service.py`.
- Comparator: `tr_bot/promotion/comparator.py`. Log: `tr_bot/promotion/log.py`.
- Profiles on disk: `ARTIFACTS_DIR/strategy_profiles/<profile_id>.json`.
- Pointers: `artifacts/deployment/paper_active.json`, `live_canary_active.json`, `live_full_active.json`.
- Append-only log: `artifacts/promotion/log.jsonl`.
- Operator narrative: `docs/strategy_promotion.md`.

**CLI**

- `strategy-status` — pointers, freeze, log tail.
- `strategy-evaluate` — exit 1 if any gate fails.
- `strategy-promote --to-tier <paper\|live_canary\|live_full> --profile-id <id> [--dry-run]` — gates, then atomic pointer write.
- `strategy-revert --to-tier <tier>` — last successful `profile_id` from the log. The profile file must still exist.

**Gates (env):** `PROMOTION_MIN_TRADES`, `PROMOTION_MIN_CALENDAR_DAYS`, `PROMOTION_MAX_EQUITY_DRAWDOWN_PCT` (vs `equity_history.json`), optional `PROMOTION_MIN_PROFIT_FACTOR`, `PROMOTION_COOLDOWN_HOURS`. `PROMOTION_BREAK_COOLDOWN=true` bypasses cooldown. SMC-specific optional floors: `SMC_PROMOTION_MIN_PF`, `SMC_PROMOTION_MAX_DD_PCT`.

**Freeze:** `PROMOTION_FROZEN=true` or an empty file `artifacts/deployment/freeze_promotions`. While frozen, promote refuses and records a rejection.

**Invariants**

- Every attempt appends a log line with `action`, `passed`, `profile_id`, and `to_tier`.
- Pointer writes are atomic. A failed gate must not leave a half-written active pointer.
- Closed ledger rows may include `profile_id`, `deployment_tier`, `regime_at_entry`, and `signal_branch`. Preserve those fields when extending the ledger schema.
- Adaptation (`adaptation-run`, `proposal-apply`) may write a profile or proposal only after its isolated backtest gate. Validation lives in `tr_bot/strategy/proposal_validate.py` and `tr_bot/strategy/proposal_apply.py`.

**Tests:** `tests/test_promotion_gates.py`, `tests/test_promotion_stats.py`, `tests/test_proposal_validate.py`, `tests/test_profile_validation.py`, `tests/test_no_trade_adaptation.py`.

---

## Startup and scheduler

**When:** `./start.sh --all`, preflight, or periodic train/backtest/promotion.

**Source of truth:** `tr_bot/startup/pipeline.py`, `tr_bot/startup/preflight.py`, `tr_bot/scheduler.py`. Narrative: `docs/operations.md`.

**`--all` order**

1. Preflight (`python -m tr_bot.startup.preflight`), including CMC / training consistency warnings (`cmc:*` on stderr).
2. Offline training (`train-run --compact`) with the train backtest gate forced off.
3. Bounded tuning (`scripts/sweep_best_fit_settings.py --emit-profile`).
4. Walk-forward backtest plus the startup gate.
5. API in the background, trading loop in the foreground, scheduler only if the pipeline says to launch it.

If a stage fails and `STARTUP_HARD_BLOCK=true`, trading does not start.

**Process model**

| Command | Role |
| --- | --- |
| `./start.sh` or `--bot` | `python -m tr_bot` foreground |
| `./start.sh --api` | uvicorn `tr_bot.health:app` (`API_HOST` / `API_PORT`, default `0.0.0.0:8080`) |
| `./start.sh --scheduler` | Periodic jobs. Does not replace the trading loop |
| `./start.sh --all` | Pipeline, then API and loop |
| `./stop.sh` | Portable stop across bash, zsh, and sh |

Docker Compose services: `redis`, `db` (Timescale/Postgres volume), `startup` (one-shot hard-block pipeline), `api`, `bot`, `scheduler`. Entry point: `scripts/supervise.sh`. App services share the `tr_bot_artifacts` volume for `ARTIFACTS_DIR`. Homelab / Pi LAN notes: `docs/operations.md`.

**Invariants**

- Preflight warnings are visible. Do not swallow `cmc:*` lines.
- Scheduler intervals are `SCHEDULER_*`. They must not mutate live positions except through the existing manager commands.
- Startup transaction semantics (a failed stage does not launch the loop when hard-block is on) are locked by tests. Do not launch the bot from a partial success flag.

**Tests:** `tests/test_startup_preflight.py`, `tests/test_startup_pipeline.py`, `tests/test_startup_transactional.py`, `tests/test_scheduler.py`.

---

## Market data

**When:** OHLCV fetch, symbol format, CoinDesk storage, or CMC snapshots.

**Source of truth**

- Live and backtest bars: `tr_bot/market_data.py` (`fetch_ohlcv`, `fetch_ohlcv_deep`), `tr_bot/ohlcv.py`, `tr_bot/binance_rest.py` (`call_ccxt_with_retries`), `tr_bot/exchange.py`.
- Symbols: `tr_bot/symbols.py` (`split_pair`). Live symbols must be ccxt spot pairs. `is_valid_ccxt_spot_symbol` in `tr_bot/managers/security.py`.
- CoinDesk: `tr_bot/coindesk/` (client, auth, endpoints, normalize, rate limit, poller, asset catalog) and `tr_bot/db/coindesk_schema.py`, `tr_bot/db/coindesk_repo.py`. Docs: `docs/coindesk_reference.md`, `docs/coindesk_asset_catalog.md`.
- CMC live snapshots (TTL-cached, not historical): `tr_bot/analysis/cmc_global.py` (`CMC_GLOBAL_METRICS_TTL_S` default 300s), `cmc_fear_greed.py`, `cmc_altcoin_season.py` (TTL at least 900s, flags `CMC_FEAR_GREED_ENABLED` / `CMC_ALTCOIN_SEASON_ENABLED`). Training history is a different module (`tr_bot/training/cmc.py`).
- Session clock: `tr_bot/market_session.py`. Fundamentals veto input: `tr_bot/analysis/fundamentals.py`.

**Invariants**

- Paginate with `fetch_ohlcv_deep` when `BACKTEST_BARS` or training lookback exceeds one ccxt page (~1000 candles). A single `fetch_ohlcv` call is not a deep history.
- CoinDesk writes go through the repo and schema modules. Do not invent a second table from the client.
- Respect `tr_bot/coindesk/rate_limit.py`. Retries belong next to existing ccxt retry helpers, not as unbounded loops in the engine.
- One `CMC_CONVERT`. Extra quote currencies spend credits. Cache training datasets with `TRAIN_DATASET_MODE` and `TRAIN_DATASET_MAX_AGE_SECONDS`.

**Tests:** `tests/test_fetch_ohlcv_deep.py`, `tests/test_binance_rest.py`, `tests/test_market_session.py`, `tests/test_coindesk_*.py`, `tests/test_cmc_client.py`, `tests/test_cmc_macro.py`.

---

## Dashboard

**When:** HTTP summary, static pages, alerts, jobs, or operator write actions.

**Source of truth**

- App: `tr_bot/health.py` (uvicorn target `tr_bot.health:app`). Routes: `tr_bot/dashboard_api.py`.
- P&L: `tr_bot/dashboard_pnl.py`. Alerts: `tr_bot/dashboard_alerts.py` (`build_alerts`, pure). Jobs: `tr_bot/dashboard_jobs.py`. Writes: `tr_bot/dashboard_actions.py`.
- Static UI: `tr_bot/static/dashboard/`. `DASHBOARD_UI=v1` serves `index.html`. `v2` serves `index.v2.html` when present, otherwise falls back to v1. Related pages: `metrics.html`, `metrics.v2.html`, `risk.html`, `scheduler.html`, `strategy.html`. Shared JS: `js/common.js` (`mountHeader`), `js/metrics-core.js`, `js/metrics.js`, `js/overview-core.js`. Shared CSS / vendor: `dashboard.css`, `vendor/`.
- Operator reference: `docs/dashboard.md`.

**Read API**

- `GET /api/dashboard/summary` is the main payload. Optional `sections=` comma list returns only those keys (plus always `status` and `dependencies`); omit for the full body. Unknown names are `400`.
- Optional auth when `DASHBOARD_TOKEN` is set: `Authorization: Bearer <token>` or `X-Dashboard-Token`.
- Also: `GET /api/dashboard/strategy/status`, `GET /api/dashboard/trades`, `GET /api/dashboard/best-fit/winner.json`.
- Alerts are a field on the summary (`id`, `severity`, `message`, `href`, `details`), sorted critical-first. There is no separate `/api/dashboard/alerts` route. Known ids include `circuit_tripped`, `trading_halted`, `nav_drawdown`, `agents_critical`, `gates_failed`, `heartbeat_stale`.
- Chart the filtered series `equity_history_display`, not the raw `equity_history` points. Metrics drawdown is anchored at `fund.state.peak_nav`.
- `GET /api/dashboard/jobs/current` may return `stale: true` when memory is empty but a disk audit is still queued/running after an API restart.

**Write API**

- Disabled unless `DASHBOARD_WRITES_ENABLED=true` and `DASHBOARD_WRITE_TOKEN` or `DASHBOARD_TOKEN` is set.
- Gate: `require_dashboard_writes`. Actions include circuit reset, pause, force train/backtest, and promote. They must call the same manager functions as the CLI, not a second implementation.

**Invariants**

- Summary reads artifacts. It must tolerate missing files (empty states), not 500 because `last_run.json` is absent.
- Do not put order placement on a GET handler.
- v1 and v2 pages that share summary fields must stay consistent. If a derived series changes, update both surfaces and `docs/dashboard.md`.
- Frontend metric math that can drift belongs in `js/metrics-core.js` with `tests/frontend/metrics-core.test.js`.

**Tests:** `tests/test_dashboard.py`, `tests/test_dashboard_pnl.py`, `tests/test_dashboard_alerts.py`, `tests/test_dashboard_actions.py`, `tests/test_dashboard_jobs.py`, `tests/test_health_endpoints.py`, `tests/test_training_dashboard.py`.

---

## Persistence and security

**When:** artifact paths, JSON state, JSONL logs, or Postgres.

**Source of truth**

- Path safety and redaction: `tr_bot/managers/security.py`.
- Ops snapshots: `tr_bot/ops_state.py` (`append_equity_point`, `write_trading_snapshot`, `write_runtime_error`, `write_runtime_success`).
- Ledger: `tr_bot/trade_ledger.py` (`trade_ledger.jsonl`).
- Equity samples: `artifacts/equity_history.json`, capped by `MAX_EQUITY_HISTORY_POINTS` (default 2016).
- Decision log: `tr_bot/db/decision_log.py`. Enabled with `DECISION_LOG_DB_ENABLED=true` and `DATABASE_URL`. Schema: `scripts/sql/001_decision_events.sql` or auto-create on first insert. One row per no-trade evaluation (`reason`, `regime_confidence`, `features_json`). Insert failure is non-fatal.
- Heartbeat freshness: `HEARTBEAT_STALE_POLL_MULTIPLIER` × `POLL_SECONDS` (default 3).

**Invariants**

- Resolve artifact paths with `resolve_path_under_artifact_dir` / `collapse_artifact_dir_duplication`. Reject `..` escapes.
- JSONL files are append-only (promotion log, trade ledger, decision audit). Rewriting history requires an explicit migration, not a silent truncate.
- `redact_secrets` before notification or log lines that might echo env or exception text.
- Tests force invalid Redis and Postgres URLs in `tests/conftest.py` (`MANAGERS_ENABLED=false`, `NOTIFY_ENABLED=false`). Do not point tests at a developer `.env` database.

**Tests:** `tests/test_security.py`, `tests/test_ops_state.py`, `tests/test_decision_log.py`, `tests/test_trade_ledger.py`.

---

## Notifications

**When:** alerts, webhooks, or operator messages.

**Source of truth:** `tr_bot/notifications/` (`facade.py`, `events.py`, `manager.py`, `sanitize.py`, backends `console.py` and `webhook.py`).

**Invariants**

- Call `notify` / `notify_events`. Do not open a webhook from the engine directly.
- Sanitize payloads. No keys, secrets, or full account responses.
- Throttle: `NOTIFY_MIN_INTERVAL_SECONDS` generally, and `NOTIFY_DEPS_INTERVAL_SECONDS` (default 300) for dependency-degraded alerts.
- A notify failure is logged and swallowed at the loop boundary. Trading continues.
- `NOTIFY_ENABLED=false` in tests. New events need a unit test that does not hit the network (`tests/test_notifications.py`).

---

## Fundamentals

**When:** CoinGecko rank, earnings windows, yfinance snapshots, or CMC macro context next to a trade decision.

**Skill:** `.cursor/skills/fundamentals/SKILL.md`.

**Source of truth:** `tr_bot/analysis/fundamentals.py` (`fetch_fundamentals_snapshot`, `allows_fundamentals`). `decision_engine` holds when `fundamentals_ok` is false.

**Invariants**

- The snapshot is a veto. It does not choose buy or sell.
- Fail-open on disabled filter, missing snap, and fetch errors. Earnings veto is buy-only. Crypto rank above `MAX_CRYPTO_MARKET_CAP_RANK` (default 500) vetoes both sides.
- Sentiment and CMC fear-and-greed / altcoin-season / global-metrics snapshots are separate inputs. Do not merge them into `allows_fundamentals` unless asked.
- Backtest default `BACKTEST_FUNDAMENTALS_MODE=always_pass`. Historical fundamentals are not replayed.
- Vendor field names come from a page fetched in the same turn (`research-web`), then mapped onto this module.

## Technicals

**When:** RSI, EMA, ATR, MACD, Bollinger, Stochastic, ADX, volume z-score, mean reversion, or trend continuation.

**Skill:** `.cursor/skills/technicals/SKILL.md`.

**Source of truth:** `tr_bot/indicators.py`, `tr_bot/ta_extended.py`, `tr_bot/signals.py`. Extended columns load only when `USE_EXTENDED_TA` is true.

**Invariants**

- `ta_ok` false forces `hold`. ADX filters fail open when ADX is missing and when `USE_TA_FILTER` is false.
- Mean reversion blocks when ADX is above `ADX_MAX_FOR_MEAN_REVERSION` (default 35). Trend entries block when ADX is below `ADX_MIN_FOR_TREND` (default 22).
- RSI in this repo is a 14-period rolling mean, not Wilder smoothing. ATR is Wilder-style. Say so when comparing to a fetched definition.
- `ta_confirmation_bias` scores SMC confluence. It is not an entry signal.
- Multi-asset frames compute indicators per `source` / `asset` group.
- Formula changes require a fetched primary definition and a fixed-series test in `tests/test_indicators.py` or `tests/test_signals.py`.

---

## Quant

**When:** position size, expectancy, profit factor, Sharpe, Sortino, correlation or volatility scaling, reward-to-risk, or promotion bootstrap.

**Skill:** `.cursor/skills/quant/SKILL.md`.

**Source of truth:** `tr_bot/risk.py`, `tr_bot/correlation.py`, `tr_bot/volatility_regime.py`, `tr_bot/strategy/scoring.py`, `tr_bot/strategy/signal_pipeline.py`, `tr_bot/backtest/runner.py` (`_risk_adjusted_metrics`), `tr_bot/promotion/stats.py`.

**Invariants**

- Size is `balance * risk_per_trade * weight * multiplier / stop_distance`. Stop distance `<= 0` yields size 0. This is not Kelly sizing.
- `effective_risk_multiplier` clamps to `[0.25, 1.5]` after correlation, ATR-percentile volatility, and session adjusters.
- SMC hard stops: regime confidence `< 0.35`, reward/risk `< 1.2`, score below `SMC_MIN_TRADE_SCORE` (default 65). The quantitative component weight defaults to `0.02`.
- `sharpe_ratio` is `(mean / sample_std) * sqrt(n)` on equity-curve simple returns, with no risk-free rate. Annualized fields multiply that by `sqrt(steps_per_year)` again. Do not report either as a textbook annualized Sharpe.
- Sharpe and Sortino stay unpublished below `BACKTEST_MIN_RISK_METRIC_OBS` (default 30). Profit factor with no losses is capped at 999.
- Block bootstrap is off unless `PROMOTION_BOOTSTRAP_ENABLED=true`. Seed 42 (expectancy) and 43 (profit factor). A null lower-bound env var disables that metric.
- Textbook definitions come from a page fetched in the same turn (`research-web`). State the difference from this code before changing a formula.

---

## Decision generator

**When:** how a `buy`, `sell`, or `hold` is chosen, where a new veto belongs, or how `STRATEGY_ENGINE` `legacy`, `smc`, and `shadow` differ.

**Skill:** `.cursor/skills/decision-generator/SKILL.md`.

**Source of truth:** `tr_bot/decision.py` (`decision_engine`), `tr_bot/engine.py` (`_evaluate_symbol_legacy`, `_evaluate_symbol_smc`), `tr_bot/strategy/signal_pipeline.py` (`analyze_symbol`, `proposal_to_decision`), `tr_bot/strategy/trade_proposal.py`. The backtest runner calls the same two generators.

**Architecture**

- `legacy` trades the legacy decision. `smc` trades the SMC decision. `shadow` trades legacy and logs SMC. An SMC exception becomes `hold` (`smc_error:…`) and does not replace a legacy fill in shadow mode.
- Legacy order: regime → mean-reversion or trend signal → ADX veto → sentiment veto → fundamentals veto → `decision_engine`.
- SMC order: data, regime suitability, directional bias, structure, SMC factor count, TA conflict, regime confidence `< 0.35`, reward/risk `< 1.2`, minimum score. Sentiment veto is applied after `proposal_to_decision`, then `allows_fundamentals` (same fail-open rules as legacy; veto sets `fundamentals_block`).
- Shared post-gates live in `tr_bot.strategy.entry_gates.apply_post_decision_gates` (cooldown, microstructure, live spread, extreme volatility, plus legacy-only regime confidence / signal strength). They can only turn an entry into a no-trade. They cannot turn `hold` into an entry. Daily trade cap / `live_sell_disabled` / `sell_requires_ccxt` stay in the engine loop.
- `REGIME_MIN_CONFIDENCE` (default 0.55) and signal-strength apply when mode is not `smc`.
- LLM output and new models stay vetoes. Exits stay in `ExitCoordinator`.

**Invariants**

- Do not add a third side-selection function in the poll loop or a forked copy in the backtest.
- New SMC blocks use a stable `blocked_gate`. New engine blocks use a stable `reason`. Tests assert that string.
- Decision-audit schema bumps when the JSONL shape changes. Shadow dual-views stay on schema v2.

---

## Automation

**When:** `./start.sh --all`, the startup pipeline, the scheduler, no-trade adaptation, dashboard background jobs, or anything that should run unattended.

**Skill:** `.cursor/skills/automation/SKILL.md`.

**Source of truth:** `start.sh`, `tr_bot/startup/pipeline.py`, `tr_bot/startup/preflight.py`, `tr_bot/scheduler.py`, `tr_bot/strategy/no_trade_adaptation.py`, `tr_bot/agents/`, `tr_bot/dashboard_jobs.py`, `tr_bot/exit_watchdog.py`.

**Processes**

- The trading loop, the scheduler, and the API are separate. `--bot`, `--api`, and `--scheduler` start one each. `--all` runs the pipeline, then API and maybe scheduler in the background, then the loop in the foreground.
- A failed pipeline exits 1. `set -e` in `start.sh` then does not start the bot. `STARTUP_AUTOMATION_ENABLED=false` is the only skip.
- The CoinDesk poller is `python -m tr_bot.coindesk`, not a scheduler job. The exit watchdog runs inside the loop sleep, not on the scheduler.

**Pipeline**

- Order: preflight → `train-run` (`TRAIN_VALIDATION_STRICT=true`, `TRAIN_BACKTEST_GATE=false`) → validation gate → optional tune → `backtest-run` → backtest gate.
- `cmc:*` preflight lines are warnings. Errors (unwritable artifacts, bad tier, non-positive risk or daily cap) fail the stage.
- One training retry with `TRAIN_DATASET_MODE=fetch_only` when the failure is a short cached dataset. Tuning timeout is exit 124 and fails the run unless `STARTUP_TUNING_TIMEOUT_SOFT_FAIL=true`.
- A tuned profile is written to the **paper** pointer only after the backtest gate passes. Live pointers are not moved. Gate defaults: −1% return, 12% drawdown, 3 trades.
- `out["ok"]` is set only after the backtest gate. Partial stage flags must not launch the loop.

**Scheduler**

- One job at a time. A skipped job is not queued, and its interval clock still advances.
- An interval of `0` means that job is off. `SCHEDULER_ENABLED` alone runs nothing.
- Promotion on the scheduler is `strategy_evaluate` only. It does not promote.
- Training promotes the model only when `TRAINING_PROMOTE_ON_SUCCESS=true` (default false).
- First tick does not repeat on-start work. `last_*` is seeded to now before the loop.
- Adaptation is off unless `ADAPTATION_ENABLED=true`, skips `STRATEGY_ENGINE=smc`, and auto-applies the paper pointer only when `ADAPTATION_AUTO_APPLY_PAPER=true`. Live tiers get a proposal file.
- Agents job (`SCHEDULER_AGENTS_INTERVAL_SECONDS`, `AGENTS_ENABLED`) writes briefs under `artifacts/agents/`. Default `AGENTS_DRY_RUN=true`. Mutating flags (`AGENTS_AUTO_*`) default false. CLI dry briefs: `python -m tr_bot.managers agents-run --dry-run` (may bypass enabled for advise-only); mutating runs require `AGENTS_ENABLED`. At most one mutating action per run; `AGENTS_AUTO_EMIT_PROPOSAL` gates proposal file writes.

**Dashboard jobs**

- One job (`backtest`, `training`, `backtest_compare`). Writes require `DASHBOARD_WRITES_ENABLED` and a token. Jobs call the same functions as the CLI.

---

## Research

**When:** questions about past trades, SMC quality, hypothesis notes, or any fact that lives outside this repository (exchange rules, fees, API docs, papers, library behavior).

**Two layers**

| Layer | Where | What it can answer |
| --- | --- | --- |
| Local ledger | `tr_bot/research/query.py`, `tr_bot/research/hypothesis_registry.py`, `tr_bot/research/agents/` | Closed trades, hypotheses, training parquet samples |
| Web | `.cursor/skills/research-web/SKILL.md`, `research-market`, `research-library` | Current pages. Required before stating an external fact |

Read the matching skill under `.cursor/skills/` and follow it. Local commands do not search the web.

**Web skills (required page opens)**

- `research-web` — `WebSearch`, then `WebFetch` on the chosen URLs (at least two pages when search returns two). Snippets are not citations. Cite title, URL, and the fact the fetched page established.
- `research-market` — Binance spot rules and fees, ccxt, CoinDesk, CoinMarketCap, SMC concepts. Search the official docs, open them, then compare with `tr_bot/fees.py`, `tr_bot/execution.py`, `tr_bot/coindesk/`, and `tr_bot/smc/`.
- `research-library` — package or API choice. Open the official docs page and, when version matters, the changelog. Check `requirements.txt` before recommending an add.

**CLI (local only)**

- `research-query [losing_smc|high_score_wins|recent]` with `--limit` and `--min-score` (default template `recent`, limit 50, min score 80).
- `research-agent [performance|structure|smc]`.

**Invariants**

- Read artifacts and ledgers. Do not submit orders, edit `.env`, or rewrite deployment pointers from a research pass.
- New query templates are fixed names, not arbitrary SQL from the CLI argument.
- Do not answer exchange, fee, vendor, or library questions from memory. Fetch the page in the same turn or report the gap.
- A fetched doc does not override a fail-closed check in `tr_bot/runtime_validation.py`. Report the mismatch and leave the check in place until the user asks for a code change.

---

## Testing

**When:** every behavior change.

**Harness**

- `tests/conftest.py` isolates the process from real Redis, Postgres, managers, and notifications before imports that read config. Set env vars that config captures at import time before importing `tr_bot.config`.
- Prefer pure functions and small fakes over live exchange calls. No test should require `BINANCE_API_KEY`.
- Name files `tests/test_<area>.py`. Mirror the module under test.

**What to add**

| Change | Lock it with |
| --- | --- |
| New live rejection reason | `test_runtime_validation.py` or `test_engine_entry.py` asserting the exact reason string |
| Exit price or partial fill | `test_exit_coordinator.py`, `test_fill_prices.py`, `test_replay_parity.py` |
| Promotion threshold | `test_promotion_gates.py` for pass, fail, freeze, and cooldown |
| Dashboard field | API test plus, if the page derives numbers, `tests/frontend/metrics-core.test.js` |
| Training skip / gate | the matching `test_training_*` or `test_train_backtest_gate.py` |
| Fee branch | `test_fees.py` for each precedence step, including the 0 bps fallback |

**Invariants**

- Tests fail closed on the dangerous case (live without a cap, exchange-native policy, promote while frozen), not only the happy path.
- Do not weaken an assertion to match a bug. If production code and a test disagree, the safety invariant section above wins.
- Frontend changes to dashboard pages need a browser pass of the affected flow when a server is available. If it is not, say so and cover the contract with the API tests.

## Change checklist

Before finishing a change in any skill:

- The owning module, not a parallel helper, holds the new behavior.
- Paper still starts with the default `.env.example` values.
- Live still fails closed when keys, symbol form, or notional caps are missing.
- Exits still run when the circuit is tripped.
- LLM output still cannot open a trade.
- Artifact writes stay under `ARTIFACTS_DIR` and do not log secrets.
- A test covers the branch that can lose money or promote a profile if it is wrong.

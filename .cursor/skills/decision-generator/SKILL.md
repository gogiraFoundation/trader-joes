---
name: decision-generator
description: >-
  Follow this repo's decision architecture: legacy decision_engine versus the
  SMC TradeProposal pipeline, then the shared entry gates. Use when the user
  asks how a buy, sell, or hold is chosen, where to add a veto, or how
  STRATEGY_ENGINE legacy, smc, and shadow differ. Do not add a third entry path.
---

# Decision generator

A decision is one of `buy`, `sell`, or `hold`. Generators propose that word. The engine and backtest then apply entry gates. Nothing in this layer submits an order.

`ExitCoordinator` closes positions. Do not put exits inside the generator.

Read the sibling skill for the layer you change: `.cursor/skills/technicals/SKILL.md`, `fundamentals`, or `quant`. If you cite an outside architecture (a paper, a blog flow, another bot), follow `.cursor/skills/research-web/SKILL.md` and say how it differs from the path below. Do not replace this path with that diagram.

## Mode switch

`STRATEGY_ENGINE` in `tr_bot/engine.py` (the backtest runner branches the same way):

| Mode | What is evaluated | What can open a trade |
| --- | --- | --- |
| `legacy` (default) | `_evaluate_symbol_legacy` only | That decision |
| `smc` | Legacy and SMC. An SMC exception becomes `hold` with reason `smc_error:…` | The SMC decision only |
| `shadow` | Both | The legacy decision. SMC is logged under `shadow` and must not be submitted |

Unknown values fall back to `legacy` in config. Kill switch back to production entries is `STRATEGY_ENGINE=legacy`.

## Two generators

Keep side selection inside these two functions. The loop calls them. It does not reimplement them.

**Legacy** — `tr_bot/decision.py` `decision_engine`, built by `_evaluate_symbol_legacy`:

1. `RegimeModel.predict_with_confidence` → `range` or `trend`.
2. Range uses `mean_reversion_signal` and `mean_reversion_ta_allows`. Trend uses `trend_continuation_signal` and `trend_regime_ta_allows` only when `TREND_STRATEGY_ENABLED`. Otherwise the signal is `hold`.
3. Sentiment is `neutral` unless `USE_LLM`. Bearish blocks buy, bullish blocks sell.
4. `allows_fundamentals` can veto. Fail-open rules live in the fundamentals skill.
5. `ta_ok` or `fundamentals_ok` false → `hold`, reason `ta_block` or `fundamentals_block`.
6. `signal_branch` is `mean_reversion`, `trend`, or `none`.

**SMC** — `tr_bot/strategy/signal_pipeline.py` `analyze_symbol` → `TradeProposal` → `proposal_to_decision`. Actionable means direction is `buy` or `sell` and `blocked_gate` is null.

Gates, in order, each returning `hold` with `blocked_gate` set:

1. `insufficient_data` (exec frame shorter than 20 bars)
2. `regime_unsuitable` (extreme vol and regime confidence `< 0.4`)
3. `no_directional_bias`
4. `structure_conflict`
5. `smc_confluence` (fewer than `SMC_MIN_SMC_FACTORS`, default 2)
6. `ta_conflict` (`ta_confirmation_bias` opposes; neutral does not block)
7. `quantitative` (regime confidence `< 0.35`)
8. `risk_reward` (ratio present and `< 1.2`)
9. `min_score` (composite below `SMC_MIN_TRADE_SCORE`, default 65)

Then `_evaluate_symbol_smc` applies the same sentiment veto and sets `blocked_gate` to `sentiment_veto` if it was empty. After sentiment, it applies `allows_fundamentals` the same way as legacy: on veto, `decision` becomes `hold`, `fundamentals_ok` is false, and `blocked_gate` becomes `fundamentals_block` when empty. Filter off / missing snap / fetch errors stay fail-open inside `allows_fundamentals`. `ta_ok` stays true on the SMC eval dict; pipeline gates remain the primary record. `signal_branch` is `smc`.

`MarketContext.soft_score_multiplier` scales the composite after the weighted sum. It does not choose the side. Clamp is `[0.5, 1.2]` (`tr_bot/strategy/market_context.py`).

## After the generator

These run in the engine on the chosen decision. They turn `buy` or `sell` into a no-trade or blocked row. They do not flip `hold` into an entry.

Applied in both modes (via `tr_bot.strategy.entry_gates.apply_post_decision_gates`):

- Cooldown (`cooldown_active`)
- Microstructure (`USE_MICROSTRUCTURE_GUARD`): wide range, large jump, low volume, missing OHLC
- Spread and depth, live only (`MICRO_USE_SPREAD_DEPTH_GUARD`). A failed order book fails closed
- `vol_extreme_suppress` when `classify_volatility` is `extreme`
- Daily open cap (`risk.can_trade` false → reason `risk_limit_reached`). This is `MAX_TRADES_PER_DAY`, not `RISK_PER_TRADE` — still applied in the engine loop after the shared gates
- Portfolio concurrent opens (`MAX_OPEN_POSITIONS` > 0 and at limit → reason `max_open_positions`) in `engine.py` via `open_positions_at_limit`
- Live spot: `live_sell_disabled` rejects a sell that would open a short. Closing a long is a different path
- Non-ccxt symbols cannot sell (`sell_requires_ccxt`)

Skipped when `STRATEGY_ENGINE=smc` (the pipeline already gated confidence and score):

- `REGIME_MIN_CONFIDENCE` (default 0.55) → `low_regime_confidence`
- `signal_strength_allows` → `weak_signal:…`

Shadow still uses the legacy decision, so those two gates still apply in shadow mode.

Sizing, notional caps, and `execute_trade` happen only after the decision survives this list. Circuit breaker, manual halt, and fund halt skip evaluation for new entries and do not block exits.

## Where a change goes

| Change | Put it |
| --- | --- |
| New legacy side rule | `tr_bot/signals.py`, then `decision_engine` only if it is a veto |
| New SMC gate | Next early return in `analyze_symbol`, with a stable `blocked_gate` string |
| Soft ranking | `tr_bot/strategy/scoring.py` weights. Say which component gives up weight |
| LLM, news, or a new model | Veto only. It must not set `buy` or `sell` by itself |
| Entry blocked by risk or microstructure | Engine gate with a `reason` string. Do not hide it inside `decision_engine` |
| Close a position | `ExitCoordinator`, not this layer |

The backtest must call the same `decision_engine` or `analyze_symbol`. Do not copy the branch into `tr_bot/backtest/runner.py`.

## Tests and audit

- Legacy table: `tests/test_decision.py`. Engine blocks: `tests/test_engine_entry.py`. SMC gates: `tests/test_hiatp_core.py` and `tests/test_scoring.py`.
- A new `blocked_gate` or `reason` needs an assertion on that exact string.
- Decision-audit JSONL uses `DECISION_AUDIT_SCHEMA_VERSION`. Shadow dual-views stay on schema v2 (`tr_bot/strategy/decision_audit.py`). Bump the version when the record shape changes.
- No-trade rows may go to Postgres. A log failure must not change the decision.

---
name: quant
description: >-
  Apply this repo's position sizing, reward-to-risk gate, composite score,
  Sharpe and Sortino publication, and block-bootstrap promotion checks. Use
  when the user asks about quant, expectancy, profit factor, Sharpe, Sortino,
  Kelly, position size, correlation penalty, volatility sizing, or statistical
  gates. Check textbook definitions on the web before treating a metric as
  the textbook version.
---

# Quant

Sizing and statistics constrain trades. They do not invent a side. A high score, Sharpe, or profit factor does not bypass the circuit breaker, live notional caps, or `runtime_validation`.

Follow `.cursor/skills/research-web/SKILL.md` before quoting a textbook Sharpe, Sortino, Kelly fraction, or bootstrap confidence interval. Search, `WebFetch` a primary page (a methods note or the original paper), and state how this repo differs. Do not relabel this repo's fields as the textbook statistic.

## Position size

Owner: `tr_bot/risk.py` `RiskManager.position_size`.

```text
size = balance * risk_per_trade * weight * risk_multiplier / stop_distance
```

- `stop_distance <= 0` → size `0`.
- `weight` is clamped to `[0, 1]`. `risk_multiplier` passed into `position_size` is clamped to `[0, 2]`.
- This is fixed-fractional sizing from the stop distance. It is not a Kelly fraction. Do not replace it with Kelly unless the user asks, and then fetch a Kelly reference and add a test.

`effective_risk_multiplier` multiplies correlation, volatility, and session adjusters, then clamps to `[0.25, 1.5]`:

| Input | Owner | Scale |
| --- | --- | --- |
| Pearson peer correlation | `tr_bot/correlation.py` | `>0.80` → 0.50; `≥0.70` → 0.70; `≥0.50` → 0.85; `≥0.30` → 0.95; else 1 |
| ATR percentile regime | `tr_bot/volatility_regime.py` | low 1.1, normal 1.0, high 0.8, extreme 0.5 |
| Session | `tr_bot/market_session.py` `session_size_adjuster` | read the function before changing it |

Volatility labels use the last ATR versus the trailing 100 ATR values: percentile `≥95` extreme, `≥80` high, `≤20` low, otherwise normal. Short frames return `normal`.

Live and canary notional caps still apply after this size (`tr_bot/strategy/tier.py`).

## SMC score and reward-to-risk

Owner: `tr_bot/strategy/scoring.py` and gate 10 in `tr_bot/strategy/signal_pipeline.py`.

- Reward/risk is `abs(target-entry) / abs(entry-stop)`. Risk `<= 0` yields no ratio.
- `score_rr`: `≥3` → 100, `≥2` → 70, `≥1.5` → 50, else 30. Missing ratio scores 40.
- `score_quantitative` maps regime confidence in `[0, 1]` to 100 (`≥0.7`), 60 (`≥0.5`), or 20. Its default weight is `0.02`.
- Composite score renormalizes over components that are present (`composite_score`). Default weights sum to 1. Do not add a weight without saying which component gives up mass, or the score scale drifts.
- Pipeline hard stops: regime confidence `< 0.35` (`blocked_gate=quantitative`); reward/risk `< 1.2` (`risk_reward`); score below `SMC_MIN_TRADE_SCORE` (default 65).
- `soft_score_multiplier` on the market context scales the score after the weighted sum.

`ta_confirmation` inside the composite is the technicals skill's confirmation bias. Do not duplicate that formula here.

## Backtest statistics

Owner: `tr_bot/backtest/runner.py` `_risk_adjusted_metrics`.

Published only when equity has at least 3 points and return observations are at least `BACKTEST_MIN_RISK_METRIC_OBS` (default 30; tier floors may raise this). Otherwise `sharpe_ratio` and `sortino_ratio` are null and `published` is false.

On simple returns `r` of the equity curve (no risk-free rate):

- Sample standard deviation uses `n - 1`.
- `sharpe_ratio` is `(mean(r) / std) * sqrt(n)`, where `n` is the number of returns in the test. That is a sample scaling, not "mean/std" and not annualized Sharpe.
- Downside deviation is `sqrt(mean of min(0, r)^2)` over all returns (gains contribute 0), divided by `n` rather than `n - 1`.
- `sortino_ratio` uses that downside deviation with the same `sqrt(n)` scale.
- `sharpe_ratio_annualized` and `sortino_ratio_annualized` multiply those figures again by `sqrt(steps_per_year)`. Reporting either number as a textbook annualized Sharpe overstates it by about `sqrt(n)`.

Profit factor is gross win PnL / gross loss PnL. No losses and some profit → cap `999` (`PROFIT_FACTOR_NO_LOSS_CAP`). No trades → null. Expectancy is net PnL / trade count, `0` when there are no trades.

Live exit PnL uses the bar close. The walk-forward backtest uses theoretical stop and target prices. Do not compare Sharpe or profit factor across those two fill assumptions without saying so.

## Promotion bootstrap

Owner: `tr_bot/promotion/stats.py`. Off unless `PROMOTION_BOOTSTRAP_ENABLED=true`, and only after min-trades, calendar-days, and strict-attribution gates pass.

Moving-block bootstrap (`block_bootstrap_metric`): block size default 5, 2000 resamples, seed 42 for expectancy and 43 for profit factor. The reported lower value is the order statistic at `max(0, int(alpha * n_samples) - 1)` with `alpha` default `0.05`. Fewer than two net PnLs fails the gate. A null `PROMOTION_MIN_EXPECTANCY_USDT` or `PROMOTION_BOOTSTRAP_MIN_PF_LOWER` disables that metric (pass, detail `disabled`).

## Changes

- Metric change: write the formula now in code, the formula on the fetched page, and the difference. Lock it with a fixed series in `tests/test_scoring.py`, `tests/test_promotion_stats.py`, or the backtest test that covers the metric.
- Do not publish a Sharpe from fewer observations than `BACKTEST_MIN_RISK_METRIC_OBS`.
- Do not size from account balance alone. Stop distance stays in the denominator.

---
name: technicals
description: >-
  Apply this repo's technical indicators, signal rules, and ADX vetoes, and
  check indicator definitions on the web before changing a formula. Use when
  the user asks about RSI, EMA, ATR, MACD, Bollinger Bands, ADX, Stochastic,
  volume z-score, mean reversion, trend continuation, or TA confirmation.
---

# Technicals

Technical indicators and TA filters propose or veto. They do not override risk, the circuit breaker, or live notional caps. `decision_engine` returns `hold` when `ta_ok` is false (`tr_bot/decision.py`).

Follow `.cursor/skills/research-web/SKILL.md` before changing a formula or citing a textbook definition (Wilder RSI, ADX, MACD, Bollinger). `WebSearch` the indicator name plus `original definition` or the library docs, then `WebFetch` at least one primary page. State whether this repo matches that page.

## Columns this bot computes

Base, always, in `tr_bot/indicators.py` `compute_indicators`:

| Column | Construction in this repo |
| --- | --- |
| `ema20`, `ema50` | `ewm(span=20)` and `span=50` on `close` (pandas default `adjust=True`) |
| `rsi` | 14-period rolling mean of gains and losses, not Wilder smoothing |
| `atr` | True range, then Wilder-style `ewm(alpha=1/14, adjust=False)` |

If `source` or `asset` is present, indicators are computed per group so concatenated training frames do not bleed across series.

Extended columns come from `tr_bot/ta_extended.py` `extend_ta`, and only when `USE_EXTENDED_TA` is true (`compute_all_indicators`):

| Column | Construction in this repo |
| --- | --- |
| `macd`, `macd_signal`, `macd_hist` | EMA 12 minus EMA 26 (`adjust=False`); signal EMA 9 |
| `bb_mid`, `bb_upper`, `bb_lower` | 20-period mean ± 2 standard deviations |
| `stoch_k`, `stoch_d` | 14-period close location in high-low range; `%D` is a 3-period mean of `%K` |
| `adx` | 14-period smoothed +DI/−DI and DX (`adjust=False`) |
| `volume_z` | 20-period volume z-score |

## How a bar becomes a side

Legacy entries (`STRATEGY_ENGINE=legacy`), from `tr_bot/signals.py`:

- Range: `mean_reversion_signal`. Buy when RSI is below `SIGNAL_RSI_OVERSOLD` (default 30) and close is below `ema20`. Sell when RSI is above `SIGNAL_RSI_OVERBOUGHT` (default 70) and close is above `ema20`. Missing RSI or EMA20 → `hold`.
- Trend, only if `TREND_STRATEGY_ENABLED`: `trend_continuation_signal`. Buy on `close > ema20 > ema50`, sell on the mirror. Missing any of the three → `hold`.

ADX vetoes when `USE_TA_FILTER` is true (default true), in `tr_bot/ta_extended.py`:

- Mean reversion: block when `adx` is above `ADX_MAX_FOR_MEAN_REVERSION` (default 35).
- Trend: block when `adx` is below `ADX_MIN_FOR_TREND` (default 22).
- Missing or NaN ADX fails open (allows the entry). Do not flip that to fail-closed unless the user asks.

`ta_confirmation_bias` (RSI bands at 45/55 plus the EMA stack, two agreeing votes) is confirmation for SMC scoring only. It must not become a standalone entry.

Strength floors used elsewhere: `SIGNAL_MIN_RSI_DISTANCE` (default 3) and `SIGNAL_MIN_ADX_MARGIN` (default 2). Read the call site before changing them. Adaptation steps live under `ADAPTATION_RSI_*` and `ADAPTATION_ADX_*`.

## Changes

- Formula change: name the current construction, the fetched definition, and the difference (for example rolling-mean RSI versus Wilder). Update `tests/test_indicators.py` or `tests/test_signals.py` with a fixed series and expected value.
- New filter: keep it a veto on the existing signal. Do not add a second entry function that ignores regime.
- SMC path (`tr_bot/strategy/signal_pipeline.py`) may read these columns. Do not fork a private indicator implementation inside a detector.
- Paper and live must call the same `compute_indicators` / `extend_ta` functions the backtest uses.

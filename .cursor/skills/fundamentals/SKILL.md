---
name: fundamentals
description: >-
  Apply this repo's fundamental vetoes and check vendor field definitions on
  the web. Use when the user asks about fundamentals, CoinGecko market cap,
  earnings windows, yfinance info, crypto rank filters, or CMC macro context
  (fear and greed, altcoin season, global metrics) relative to trade decisions.
---

# Fundamentals

Fundamentals veto a decision. They do not open a trade. `decision_engine` returns `hold` when `fundamentals_ok` is false (`tr_bot/decision.py`).

Follow `.cursor/skills/research-web/SKILL.md` before stating a CoinGecko, Yahoo Finance, or CoinMarketCap field, endpoint, or unit. Search, then `WebFetch` the vendor page. Compare that page with the code below. Do not treat a search snippet as the field definition.

## What this bot uses

Owner: `tr_bot/analysis/fundamentals.py`.

| Input | Source | Veto |
| --- | --- | --- |
| Crypto (`symbol` contains `/`) | CoinGecko `GET /api/v3/coins/{id}` with `market_data=true` | `market_cap_rank` greater than `MAX_CRYPTO_MARKET_CAP_RANK` (default 500) |
| Equity (no `/`) | `yfinance` `Ticker.info` | Buy only, when `earningsTimestamp` is within `EARNINGS_BLOCK_SECONDS` (default 86400) of now |

Coin id map: `COINGECKO_IDS` as `BTC=bitcoin,ETH=ethereum`. If unset, BTC and ETH default to `bitcoin` and `ethereum`; any other base also falls through to `bitcoin`. Do not leave that fallback in place for a new base.

`USE_FUNDAMENTALS_FILTER` defaults to true. Redis cache key prefix `tr_bot:fundamentals:v1:`, TTL `FUNDAMENTALS_CACHE_TTL_SECONDS` (default 86400), timeout `FUNDAMENTALS_HTTP_TIMEOUT` (default 10s). Cache read/write failures are logged and ignored.

## Fail-open

`allows_fundamentals` returns true when the filter is off, `snap` is missing, `disabled` is set, or `error` is set. A fetch failure must not block trading. Do not invert this into fail-closed without an explicit user request and a test.

Earnings veto is buy-only. Rank veto applies to both sides.

## Not the same filter

These are separate inputs. Do not fold them into `allows_fundamentals` unless the user asks for that code change.

- LLM sentiment (`tr_bot/sentiment.py`) is a side veto in `decision_engine`, not a fundamental.
- CMC live snapshots (`tr_bot/analysis/cmc_global.py`, `cmc_fear_greed.py`, `cmc_altcoin_season.py`) are macro context. Historical CMC global-metrics stay off the live poll path.
- Training macro columns (`tr_bot/training/cmc_macro.py`) use backward `merge_asof`. They are not the live fundamentals snapshot.

## Backtest

`BACKTEST_FUNDAMENTALS_MODE` defaults to `always_pass`. Per-bar historical fundamentals are not replayed. `match_engine` calls `allows_fundamentals` with the snapshot the runner supplies. Do not claim a backtest applied earnings or rank vetoes unless that mode and snapshot are in the run artifact.

## Changes

- New veto: extend `allows_fundamentals`, document the knob in `.env.example`, and test pass, veto, and fail-open (error snap still allows).
- New vendor field: fetch the current API page in the same turn, then map only fields the page documents.
- A fundamentals pass does not place orders or edit live tier settings.

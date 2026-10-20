---
name: research-market
description: >-
  Research market structure, exchange rules, fees, SMC concepts, regime
  methods, and vendor data APIs by searching and opening webpages. Use when
  the user asks about Binance spot rules, fees, filters, CoinDesk, CoinMarketCap,
  ccxt behavior, smart money concepts, order blocks, fair value gaps, or
  whether a trading idea matches current market documentation.
---

# Market research

Follow `.cursor/skills/research-web/SKILL.md` first. Do not describe exchange rules, fee tiers, or data-vendor endpoints from memory.

Local context, when the question is about this bot:

- Decision path: `tr_bot/decision.py`, `tr_bot/signals.py`, `tr_bot/strategy/signal_pipeline.py`
- Fees: `tr_bot/fees.py` and `LIVE_FEE_*` / `BACKTEST_FEE_*` in `.env.example`
- Live safety: `tr_bot/runtime_validation.py`
- Vendors: `tr_bot/coindesk/`, `tr_bot/analysis/cmc_*.py`, `tr_bot/training/cmc.py`
- Offline trades only: `python -m tr_bot.managers research-query` and `research-agent`

## Searches to run

Pick the rows that match the question. Run `WebSearch` for each, then `WebFetch` the official page from the results.

| Topic | Search for | Then compare with |
| --- | --- | --- |
| Spot order rules, filters, lot size | `Binance spot exchange filters official docs` | `tr_bot/managers/security.py`, `tr_bot/execution.py` |
| Fees and VIP / BNB pay | `Binance spot trading fee schedule official` | `tr_bot/fees.py` precedence |
| User data stream, orders, stops | `Binance spot API stop loss official docs` | `EXIT_POLICY` in `tr_bot/config.py` (`exchange_native` stays rejected) |
| ccxt symbol and OHLCV | `ccxt binance fetch_ohlcv manual` | `tr_bot/market_data.py` |
| CoinMarketCap credits and endpoints | `CoinMarketCap API documentation global metrics fear greed` | `tr_bot/analysis/cmc_global.py`, `tr_bot/training/cmc.py` |
| CoinDesk market data | `CoinDesk data API documentation official` | `tr_bot/coindesk/endpoints.py`, `docs/coindesk_reference.md` |
| SMC (order block, FVG, BOS, liquidity) | the concept name plus `smart money concept` | `tr_bot/smc/` detector that would change |

Fetch at least one primary page per topic you answer. If the official page is missing from the first search, search again with `site:` on the vendor's doc host before using a blog.

## Rules for this repo

- Historical CoinMarketCap global-metrics calls stay off the live poll path. Confirm the endpoint on the fetched CMC page, then keep live code on the TTL snapshot modules.
- A webpage does not override a fail-closed check in `runtime_validation.py`. If the docs and the code disagree, report both and leave the safety check in place until the user asks for a code change.
- SMC notes from the web are hypotheses. Record them with `append_hypothesis` only when the user asks to save a hypothesis. Do not change detectors in the same turn as a literature search unless they asked for an implementation.
- Fee numbers in a fetched schedule are not the bot's costs until `tr_bot/fees.py` uses them. State both figures.

## Answer shape

```markdown
## Market finding
[What the opened pages say, with URLs.]

## What this bot does
[Module and config. Quote the precedence or gate, not a paraphrase of the README diagrams.]

## Mismatch
[Doc vs code, or "none".]

## Sources
- [Title](URL) — fact
```

# CoinDesk index reference data

This project calls CoinDesk Data API index endpoints to validate configuration before training or live loops, and exposes a small snapshot on `GET /ready`.

## Endpoints used

Reference checks use **index** paths for CoinDesk index families (`cadli`, `ccix` by default) and **spot** paths when `COINDESK_DEFAULT_MARKET` is an exchange slug (e.g. `coinbase`). Override with `COINDESK_REFERENCE_API_MODE=index|spot` when `auto` is wrong for your deployment.

| Purpose | Path (default) | Role |
|--------|------------------|------|
| Index family metadata | `GET /index/cc/v2/markets` (index mode) or `GET /spot/v1/markets` (spot mode) | Confirm the configured market exists and surface family-level status. |
| Mapped instruments | `GET /index/cc/v1/markets/instruments` (index) or `GET /spot/v1/markets/instruments` (spot) | Confirm each symbol in `COINDESK_INSTRUMENTS` is present and `ACTIVE` for `COINDESK_DEFAULT_MARKET`. Requests are split so each call has at most `COINDESK_MAPPED_INSTRUMENTS_CHUNK` instruments (default 25). |

Responses are cached in-process for `COINDESK_REFERENCE_CACHE_TTL_S` seconds (default 60).

## Environment variables

| Variable | Meaning |
|----------|---------|
| `COINDESK_REFERENCE_CHECK_ENABLED` | If unset, defaults to the value of `COINDESK_ENABLED`. When true and `COINDESK_API_KEY` is set, reference checks run at startup preflight and inform `/ready`. |
| `COINDESK_REFERENCE_STRICT` | When true, startup preflight fails (hard-block) if instruments are missing or not `ACTIVE`, or if the check raises. When false (default), issues are warnings only. |
| `COINDESK_REFERENCE_API_MODE` | `auto` (default), `index`, or `spot`. With `auto`, `cadli` / `ccix` use index URLs; exchange slugs use spot URLs. Index slugs are listed in `COINDESK_INDEX_STYLE_MARKETS` (default `cadli,ccix`). |
| `COINDESK_INDEX_STYLE_MARKETS` | Comma-separated slugs that always resolve to index reference endpoints (not `/spot/*`). |
| `COINDESK_INDEX_MARKETS_PATH` | If set in the environment, overrides the effective path for the markets list GET (otherwise index vs spot defaults apply). |
| `COINDESK_MAPPED_INSTRUMENTS_PATH` | If set in the environment, overrides the effective mapped-instruments GET path. |
| `COINDESK_SPOT_MARKETS_PATH` | Default `/spot/v1/markets` when reference mode resolves to spot. |
| `COINDESK_SPOT_INSTRUMENTS_PATH` | Default `/spot/v1/markets/instruments` when reference mode resolves to spot. |
| `COINDESK_CCIX_STRICT_VALIDATION` | When true (default) and `COINDESK_DEFAULT_MARKET=ccix`, startup rejects `/spot/...` tick or OHLCV paths and `COINDESK_REFERENCE_API_MODE=spot`. |
| `COINDESK_EOD_DAYS_PATH` | Path for optional EOD day bars helper (default `/index/cc/v1/historical/days/eod`). |
| `COINDESK_REFERENCE_CACHE_TTL_S` | In-process cache TTL for reference GETs (default `60`). |
| `COINDESK_MAPPED_INSTRUMENTS_CHUNK` | Max instruments per mapped-instruments request (default `25`, capped 1–100). |

## Optional EOD day bars

`tr_bot.coindesk.reference.fetch_eod_day_bars` is a thin wrapper for EOD index families (documentation uses different `market` and `instrument` pairs than cadli spot-style symbols). It is not wired into training by default.

## Daily-ish products: do not mix without labels

These are different products; use distinct column names in parquet and models.

| Product | Typical path / idea | Notes |
|---------|---------------------|--------|
| DA fixings | `.../historical/days/ccda` | Fixing window via timezone and `close_time`; CCIX-oriented semantics. |
| EOD index days | `.../historical/days/eod` | EOD-specific index families (e.g. institutional products); not the same as cadli generic daily OHLC. |
| Standard index historical days | `/index/cc/v1/historical/days` with `market=cadli` and instruments like `BTC-USD` | Primary cadli daily OHLC used by `fetch_training_ohlcv` when timeframe is daily. |

Mixing series without explicit labels will confuse regime features and P&L attribution.

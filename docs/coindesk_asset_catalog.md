# CoinDesk asset catalog (Top List, on-chain)

This complements **indices / cadli** (price path for OHLCV) with **cross-asset rankings and on-chain metadata**. Do not mix Top List aggregates with index OHLC in one unnamed feature: label source and timeframe.

## Asset Top List — `GET /asset/v1/top/list`

- **Use**: Breadth / regime context, universe construction, or fallback when CMC is unavailable; refresh on a **schedule** (tens of seconds cache in docs), not inside the tight trading loop.
- **Implementation**: [`tr_bot/coindesk/asset_catalog.py`](../tr_bot/coindesk/asset_catalog.py) — `fetch_asset_top_list`, in-process cache + optional Redis (`COINDESK_ASSET_TOP_LIST_USE_REDIS`), TTL `COINDESK_ASSET_TOP_LIST_CACHE_TTL_S` (default 30s).
- **Snapshot file**: `ARTIFACTS_DIR/coindesk/asset_top_list_snapshot.json` — written by `refresh_asset_top_list_snapshot()` (scheduler or manual).
- **Dashboard**: `/api/dashboard/summary` includes `coindesk_asset_top_list` when a snapshot exists.

### Env (subset)

| Variable | Role |
|----------|------|
| `COINDESK_ASSET_TOP_LIST_ENABLED` | Must be true to refresh snapshots (default false). |
| `COINDESK_ASSET_TOP_LIST_PAGE_SIZE` | 10–100 (default 50). |
| `COINDESK_ASSET_TOP_LIST_SORT_BY` | e.g. `CIRCULATING_MKT_CAP_USD`. |
| `COINDESK_ASSET_TOP_LIST_GROUPS` | Optional field packs (narrow to save payload). |
| `COINDESK_ASSET_TOP_LIST_WATCH_SYMBOLS` | Comma list; if empty, derived from `COINDESK_INSTRUMENTS` (e.g. `BTC-USD` → `BTC`). |
| `SCHEDULER_COINDESK_ASSET_TOP_LIST_INTERVAL_SECONDS` | Periodic refresh in `python -m tr_bot.scheduler` (0 = off). |
| `SCHEDULER_RUN_COINDESK_ASSET_TOP_LIST_ON_START` | Run once at scheduler startup when interval is positive. |

## On-chain by address — `GET /onchain/v2/data/by/address`

- **Use**: Due diligence on a contract, mapping to CoinDesk symbol/ID before listing an alt. Low value for plain BTC spot unless you track wrapped assets.
- **Helper**: `fetch_onchain_by_address` (optional `chain`, `groups`).

## Chain summary — `GET /onchain/v3/summary/by/chain`

- **Use**: Discovery (“what does CoinDesk support on this chain?”). Docs suggest long cache; call rarely.
- **Helper**: `fetch_chain_summary_by_chain`.

## Layering vs indices

| Layer | Role |
|-------|------|
| Indices (cadli, etc.) | Execution-agnostic reference prices and training OHLCV. |
| Asset / on-chain | Rankings, metadata, universe — not a substitute for Binance execution or index bars. |

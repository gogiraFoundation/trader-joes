"""Configuration from environment (see .env.example)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return float(v)


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def _optional_float(name: str) -> float | None:
    v = os.environ.get(name)
    if v is None or str(v).strip() == "":
        return None
    return float(v)


def _optional_str(name: str) -> str | None:
    v = os.environ.get(name)
    if v is None or str(v).strip() == "":
        return None
    return str(v).strip()


def _optional_int(name: str) -> int | None:
    v = os.environ.get(name)
    if v is None or str(v).strip() == "":
        return None
    return int(v)


SYMBOL = os.environ.get("SYMBOL", "BTC/USDT")
TIMEFRAME = os.environ.get("TIMEFRAME", "5m")
INITIAL_BARS = _int("INITIAL_BARS", 500)
LOOP_BARS = _int("LOOP_BARS", 200)

RISK_PER_TRADE = _float("RISK_PER_TRADE", 0.01)
MAX_TRADES_PER_DAY = _int("MAX_TRADES_PER_DAY", 5)

USE_LLM = _bool("USE_LLM", False)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_MIN_CONFIDENCE = _float("LLM_MIN_CONFIDENCE", 0.6)
LLM_TIMEOUT_SECONDS = _float("LLM_TIMEOUT_SECONDS", 3.0)
# OpenAI 429: sentiment uses cooldown + skip (no per-request retry storm). Legacy vars kept for .env compatibility.
LLM_MAX_RETRIES_ON_429 = _int("LLM_MAX_RETRIES_ON_429", 3)
LLM_BACKOFF_CAP_SECONDS = _float("LLM_BACKOFF_CAP_SECONDS", 16.0)
# After a 429, skip LLM calls until this elapses (floor; Retry-After header can extend).
LLM_COOLDOWN_AFTER_429_SECONDS = _float("LLM_COOLDOWN_AFTER_429_SECONDS", 120.0)

PAPER_TRADING = _bool("PAPER_TRADING", True)
# Ledger + dashboard cost estimates for closed trades (same bps semantics as BACKTEST_*)
LIVE_FEE_BPS = _float("LIVE_FEE_BPS", 0.0)
# Explicit taker bps (all symbols) when LIVE_FEE_BPS==0; see tr_bot.fees effective_taker_bps precedence.
LIVE_FEE_TAKER_BPS = _float("LIVE_FEE_TAKER_BPS", 0.0)
# Reserved for Tier 2 (maker); parsed only, not used in Tier 1 fee math.
LIVE_FEE_MAKER_BPS = _float("LIVE_FEE_MAKER_BPS", 0.0)
# When false (default), use LIVE_FEE_BPS unless it is > 0 (override). When true and LIVE_FEE_BPS==0, use Binance Spot Algo tiers.
LIVE_FEE_USE_ALGO_TIERS = _bool("LIVE_FEE_USE_ALGO_TIERS", False)
BINANCE_VIP5_PLUS = _bool("BINANCE_VIP5_PLUS", False)
# With LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS=true: pick discounted vs standard published taker (see LIVE_FEE_*_TAKER_BPS below).
BINANCE_PAY_FEES_WITH_BNB = _bool("BINANCE_PAY_FEES_WITH_BNB", False)
LIVE_FEE_BNB_TAKER_BPS = _float("LIVE_FEE_BNB_TAKER_BPS", 7.5)
LIVE_FEE_STANDARD_TAKER_BPS = _float("LIVE_FEE_STANDARD_TAKER_BPS", 10.0)
LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS = _bool("LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS", False)
LIVE_SLIPPAGE_BPS = _float("LIVE_SLIPPAGE_BPS", 0.0)
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_SECRET = os.environ.get("BINANCE_SECRET", "")

# Live execution: market (ccxt market order) or twap (Binance SAPI algo/spot/newOrderTwap)
_EXECUTION_STYLE_RAW = os.environ.get("EXECUTION_STYLE", "market").strip().lower()
EXECUTION_STYLE = _EXECUTION_STYLE_RAW if _EXECUTION_STYLE_RAW in ("market", "twap") else "market"
TWAP_DURATION_SECONDS = max(300, min(86400, _int("TWAP_DURATION_SECONDS", 3600)))
TWAP_LIMIT_PRICE = _optional_float("TWAP_LIMIT_PRICE")
TWAP_MIN_NOTIONAL_USDT = _optional_float("TWAP_MIN_NOTIONAL_USDT")

# Transient HTTP/TLS failures (e.g. connection reset by Binance)
CCXT_FETCH_MAX_RETRIES = _int("CCXT_FETCH_MAX_RETRIES", 5)
CCXT_FETCH_RETRY_DELAY_S = _float("CCXT_FETCH_RETRY_DELAY_S", 1.0)

# Binance signed REST: recvWindow ms (1000–60000). Leave unset to use ccxt default (often 10000).
_rw = _optional_int("BINANCE_RECV_WINDOW_MS")
BINANCE_RECV_WINDOW_MS: int | None = None
if _rw is not None:
    BINANCE_RECV_WINDOW_MS = max(1000, min(60_000, int(_rw)))

# Optional REST host overrides (same paths as ccxt defaults, different host only).
BINANCE_API_BASE_URL = _optional_str("BINANCE_API_BASE_URL")  # origin e.g. https://api.binance.com
BINANCE_SAPI_BASE_URL = _optional_str("BINANCE_SAPI_BASE_URL")  # full sapi/v1 prefix e.g. https://api.binance.com/sapi/v1

# Retries for throttles / 5xx / timeouts (orders + shared REST helper).
BINANCE_HTTP_MAX_RETRIES = max(1, _int("BINANCE_HTTP_MAX_RETRIES", CCXT_FETCH_MAX_RETRIES))
BINANCE_HTTP_BASE_DELAY_S = max(0.05, _float("BINANCE_HTTP_BASE_DELAY_S", CCXT_FETCH_RETRY_DELAY_S))
BINANCE_HTTP_MAX_DELAY_S = max(0.5, _float("BINANCE_HTTP_MAX_DELAY_S", 120.0))

# Minimum seconds between TWAP reconcile SAPI bursts across all symbols (0 = every poll).
TWAP_RECONCILE_MIN_INTERVAL_S = max(0.0, _float("TWAP_RECONCILE_MIN_INTERVAL_S", 0.0))

POLL_SECONDS = _int("POLL_SECONDS", 300)

# Mean reversion signal thresholds ([tr_bot/signals.py])
SIGNAL_RSI_OVERSOLD = _float("SIGNAL_RSI_OVERSOLD", 30.0)
SIGNAL_RSI_OVERBOUGHT = _float("SIGNAL_RSI_OVERBOUGHT", 70.0)
# Decision quality gates
REGIME_MIN_CONFIDENCE = _float("REGIME_MIN_CONFIDENCE", 0.55)
USE_SIGNAL_STRENGTH_FILTER = _bool("USE_SIGNAL_STRENGTH_FILTER", True)
# Minimum RSI distance from threshold to consider signal actionable.
# For buys: SIGNAL_RSI_OVERSOLD - rsi; for sells: rsi - SIGNAL_RSI_OVERBOUGHT.
SIGNAL_MIN_RSI_DISTANCE = _float("SIGNAL_MIN_RSI_DISTANCE", 3.0)
# If ADX is present, require at least this distance from ADX cutoff for signal quality.
SIGNAL_MIN_ADX_MARGIN = _float("SIGNAL_MIN_ADX_MARGIN", 2.0)
# Cooldown bars after a losing close per symbol before new entries.
LOSS_COOLDOWN_BARS = _int("LOSS_COOLDOWN_BARS", 4)
# Startup restore reconciliation tolerance (live): required base >= size * (1 - tolerance).
RESTORE_RECONCILE_TOLERANCE_PCT = _float("RESTORE_RECONCILE_TOLERANCE_PCT", 0.05)
RESTORE_RECONCILE_USE_ORDER_HISTORY = _bool("RESTORE_RECONCILE_USE_ORDER_HISTORY", True)
RESTORE_RECONCILE_TRADES_LOOKBACK = _int("RESTORE_RECONCILE_TRADES_LOOKBACK", 200)
RESTORE_RECONCILE_REQUIRE_ORDER_ID = _bool("RESTORE_RECONCILE_REQUIRE_ORDER_ID", False)
# Bar-level microstructure guards for live entry quality.
USE_MICROSTRUCTURE_GUARD = _bool("USE_MICROSTRUCTURE_GUARD", False)
MICRO_MAX_BAR_RANGE_PCT = _float("MICRO_MAX_BAR_RANGE_PCT", 4.0)
MICRO_MAX_BAR_JUMP_PCT = _float("MICRO_MAX_BAR_JUMP_PCT", 2.0)
MICRO_MIN_BAR_VOLUME = _float("MICRO_MIN_BAR_VOLUME", 0.0)
MICRO_USE_SPREAD_DEPTH_GUARD = _bool("MICRO_USE_SPREAD_DEPTH_GUARD", False)
MICRO_MAX_SPREAD_PCT = _float("MICRO_MAX_SPREAD_PCT", 0.5)
MICRO_MIN_TOP_DEPTH_NOTIONAL = _float("MICRO_MIN_TOP_DEPTH_NOTIONAL", 0.0)

CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES = _int("CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES", 3)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://tr_bot:tr_bot@localhost:5432/tr_bot",
)
# Persist no-trade decisions to Postgres decision_events (creates table on first insert).
DECISION_LOG_DB_ENABLED = _bool("DECISION_LOG_DB_ENABLED", False)
# Backward-compatible explicit toggle.
# If false, /ready and dashboard "status" ignore Postgres (Redis still required).
READINESS_REQUIRE_POSTGRES = _bool("READINESS_REQUIRE_POSTGRES", False)
# required | optional | auto
# - required: Postgres must be up for readiness
# - optional: Postgres health is reported but not required
# - auto: if READINESS_REQUIRE_POSTGRES is explicitly set, respect it; otherwise
#         require Postgres for non-paper setups and keep paper/dev non-blocking.
READINESS_MODE = os.environ.get("READINESS_MODE", "auto").strip().lower()


def readiness_require_postgres() -> bool:
    mode = READINESS_MODE
    if mode in ("required", "require", "on", "true", "1"):
        return True
    if mode in ("optional", "off", "false", "0", "disabled"):
        return False
    # auto
    if os.environ.get("READINESS_REQUIRE_POSTGRES") is not None:
        return READINESS_REQUIRE_POSTGRES
    return not PAPER_TRADING

# Regime model: load from disk if path set and file exists (else train in-memory from INITIAL_BARS)
REGIME_MODEL_PATH = os.environ.get("REGIME_MODEL_PATH", "").strip()

# Offline training (`python -m tr_bot.training`)
TRAIN_SOURCES = os.environ.get("TRAIN_SOURCES", "yahoo")
TRAIN_OUTPUT_PATH = os.environ.get("TRAIN_OUTPUT_PATH", "regime_model.joblib")
TRAIN_TIMEFRAME = os.environ.get("TRAIN_TIMEFRAME", "1d")
YAHOO_TRAIN_TICKERS = os.environ.get("YAHOO_TRAIN_TICKERS", "SPY,QQQ,BTC-USD,ETH-USD")
YAHOO_TRAIN_PERIOD = os.environ.get("YAHOO_TRAIN_PERIOD", "2y")
YAHOO_TRAIN_FAIL_ON_SKIP = _bool("YAHOO_TRAIN_FAIL_ON_SKIP", False)
YAHOO_TRAIN_MIN_DAILY_RETURN_STDDEV = _float("YAHOO_TRAIN_MIN_DAILY_RETURN_STDDEV", 0.0)
YAHOO_TRAIN_MIN_CLOSE_RANGE_PCT = _float("YAHOO_TRAIN_MIN_CLOSE_RANGE_PCT", 0.0)
CMC_API_KEY = os.environ.get("CMC_API_KEY", "")
# Prefer stable CMC ids (comma-separated integers) or slugs over ambiguous symbols.
CMC_TRAIN_IDS = os.environ.get("CMC_TRAIN_IDS", "").strip()
CMC_TRAIN_SLUGS = os.environ.get("CMC_TRAIN_SLUGS", "").strip()
CMC_TRAIN_SYMBOLS = os.environ.get("CMC_TRAIN_SYMBOLS", "BTC,ETH")
# When training with symbols only, resolve once via /v1/cryptocurrency/map (recommended).
CMC_MAP_RESOLVE_SYMBOLS = _bool("CMC_MAP_RESOLVE_SYMBOLS", True)
CMC_INTERVAL = os.environ.get("CMC_INTERVAL", "1d")
CMC_COUNT = _int("CMC_COUNT", 500)
CMC_CONVERT = os.environ.get("CMC_CONVERT", "USD")

# CoinDesk Digital Asset Data API (https://data-api.coindesk.com)
COINDESK_API_KEY = os.environ.get("COINDESK_API_KEY", "").strip()
COINDESK_BASE_URL = os.environ.get("COINDESK_BASE_URL", "https://data-api.coindesk.com").rstrip("/")
# bearer | x_api_key | both | apikey_header (Authorization: Apikey <key>)
_COINDESK_AUTH = os.environ.get("COINDESK_AUTH_MODE", "bearer").strip().lower()
COINDESK_AUTH_MODE = _COINDESK_AUTH if _COINDESK_AUTH in ("bearer", "x_api_key", "both", "apikey_header") else "bearer"
COINDESK_ENABLED = _bool("COINDESK_ENABLED", False)
COINDESK_STORE_ENABLED = _bool("COINDESK_STORE_ENABLED", False)
# Defaults match CoinDesk cadli + index paths. For spot ticks: COINDESK_LATEST_TICK_PATH=/spot/v1/latest/tick
# and COINDESK_DEFAULT_MARKET to the exchange slug (e.g. coinbase).
COINDESK_DEFAULT_MARKET = os.environ.get("COINDESK_DEFAULT_MARKET", "cadli").strip()
COINDESK_INSTRUMENTS = os.environ.get("COINDESK_INSTRUMENTS", "BTC-USD,ETH-USD").strip()
COINDESK_LATEST_TICK_PATH = os.environ.get("COINDESK_LATEST_TICK_PATH", "/index/cc/v1/latest/tick").strip()
# Comma-separated groups (empty = all allowed). See API "groups" parameter.
COINDESK_TICK_GROUPS = os.environ.get("COINDESK_TICK_GROUPS", "").strip()
COINDESK_APPLY_MAPPING = _bool("COINDESK_APPLY_MAPPING", True)
# Historical OHLCV (override full path if needed; default builder uses /index/cc/v1/historical/{days|hours|minutes})
COINDESK_OHLCV_PATH = os.environ.get("COINDESK_OHLCV_PATH", "").strip()
# Index OHLCV groups filter (see CoinDesk Index Historical OHLCV+ docs)
COINDESK_OHLCV_GROUPS = os.environ.get("COINDESK_OHLCV_GROUPS", "OHLC,VOLUME").strip()
COINDESK_HISTORICAL_FILL = _bool("COINDESK_HISTORICAL_FILL", True)
COINDESK_HISTORICAL_RESPONSE_FORMAT = os.environ.get("COINDESK_HISTORICAL_RESPONSE_FORMAT", "JSON").strip() or "JSON"
COINDESK_ORDERBOOK_PATH = os.environ.get("COINDESK_ORDERBOOK_PATH", "").strip()
COINDESK_INSTRUMENTS_METADATA_PATH = os.environ.get("COINDESK_INSTRUMENTS_METADATA_PATH", "").strip()
COINDESK_HTTP_TIMEOUT_S = _float("COINDESK_HTTP_TIMEOUT_S", 120.0)
COINDESK_MAX_RETRIES = _int("COINDESK_MAX_RETRIES", 5)
COINDESK_RETRY_BACKOFF_CAP_S = _float("COINDESK_RETRY_BACKOFF_CAP_S", 120.0)
COINDESK_RATE_LIMIT_REMAINING_THRESHOLD = _int("COINDESK_RATE_LIMIT_REMAINING_THRESHOLD", 5)
COINDESK_RATE_LIMIT_SLOW_DELAY_S = _float("COINDESK_RATE_LIMIT_SLOW_DELAY_S", 0.75)
COINDESK_POLL_SECONDS = _int("COINDESK_POLL_SECONDS", 10)
COINDESK_REDIS_CHANNEL = os.environ.get("COINDESK_REDIS_CHANNEL", "coindesk:ticks").strip()
COINDESK_REDIS_PUBLISH = _bool("COINDESK_REDIS_PUBLISH", False)
# Training fetch limits (when TRAIN_SOURCES includes coindesk)
COINDESK_TRAIN_LIMIT = _int("COINDESK_TRAIN_LIMIT", 2000)
COINDESK_TRAIN_TO_TS = os.environ.get("COINDESK_TRAIN_TO_TS", "").strip()
# Live engine: paper-only OHLCV from DB hypertable (see tr_bot.market_data)
USE_COINDESK_OHLCV_FROM_DB = _bool("USE_COINDESK_OHLCV_FROM_DB", False)
COINDESK_OHLCV_DB_TIMEFRAME = os.environ.get("COINDESK_OHLCV_DB_TIMEFRAME", "").strip()
COINDESK_METRICS_ENABLED = _bool("COINDESK_METRICS_ENABLED", False)
COINDESK_EXPORT_PARQUET_PATH = os.environ.get("COINDESK_EXPORT_PARQUET_PATH", "").strip()
# REST pollers only see the latest tick per instrument; CCSEQ jumps between polls (many trades) — not a gap.
COINDESK_CCSEQ_POLL_MODE = _bool("COINDESK_CCSEQ_POLL_MODE", True)
# If false and COINDESK_OHLCV_PATH is set: attempt OHLCV backfill when CCSEQ != previous+1 (streaming / strict).
COINDESK_CCSEQ_BACKFILL_ON_GAP = _bool("COINDESK_CCSEQ_BACKFILL_ON_GAP", False)
# Index reference data (v2 markets + v1 mapped instruments); see docs/coindesk_reference.md
_ref_check_raw = os.environ.get("COINDESK_REFERENCE_CHECK_ENABLED")
if _ref_check_raw is None or str(_ref_check_raw).strip() == "":
    COINDESK_REFERENCE_CHECK_ENABLED = COINDESK_ENABLED
else:
    COINDESK_REFERENCE_CHECK_ENABLED = str(_ref_check_raw).strip().lower() in ("1", "true", "yes", "on")
COINDESK_REFERENCE_STRICT = _bool("COINDESK_REFERENCE_STRICT", False)
# auto: cadli/ccix (see COINDESK_INDEX_STYLE_MARKETS) → index reference API; exchange slugs → spot API.
_RAW_REF_MODE = os.environ.get("COINDESK_REFERENCE_API_MODE", "auto").strip().lower()
COINDESK_REFERENCE_API_MODE = _RAW_REF_MODE if _RAW_REF_MODE in ("auto", "index", "spot") else "auto"
_INDEX_STYLE_MARKETS_RAW = os.environ.get("COINDESK_INDEX_STYLE_MARKETS", "cadli,ccix")
COINDESK_INDEX_STYLE_MARKETS = frozenset(
    x.strip().lower() for x in _INDEX_STYLE_MARKETS_RAW.split(",") if x.strip()
)
COINDESK_INDEX_MARKETS_PATH_EXPLICIT = "COINDESK_INDEX_MARKETS_PATH" in os.environ
COINDESK_INDEX_MARKETS_PATH = os.environ.get("COINDESK_INDEX_MARKETS_PATH", "/index/cc/v2/markets").strip()
COINDESK_MAPPED_INSTRUMENTS_PATH_EXPLICIT = "COINDESK_MAPPED_INSTRUMENTS_PATH" in os.environ
COINDESK_MAPPED_INSTRUMENTS_PATH = os.environ.get(
    "COINDESK_MAPPED_INSTRUMENTS_PATH", "/index/cc/v1/markets/instruments"
).strip()
COINDESK_SPOT_MARKETS_PATH = os.environ.get("COINDESK_SPOT_MARKETS_PATH", "/spot/v1/markets").strip()
COINDESK_SPOT_INSTRUMENTS_PATH = os.environ.get(
    "COINDESK_SPOT_INSTRUMENTS_PATH", "/spot/v1/markets/instruments"
).strip()
# When COINDESK_DEFAULT_MARKET=ccix: reject spot API paths for tick/OHLCV and COINDESK_REFERENCE_API_MODE=spot.
COINDESK_CCIX_STRICT_VALIDATION = _bool("COINDESK_CCIX_STRICT_VALIDATION", True)
COINDESK_EOD_DAYS_PATH = os.environ.get("COINDESK_EOD_DAYS_PATH", "/index/cc/v1/historical/days/eod").strip()
COINDESK_REFERENCE_CACHE_TTL_S = _float("COINDESK_REFERENCE_CACHE_TTL_S", 60.0)
COINDESK_MAPPED_INSTRUMENTS_CHUNK = max(1, min(100, _int("COINDESK_MAPPED_INSTRUMENTS_CHUNK", 25)))


def _coindesk_path_norm(p: str) -> str:
    p = p.strip()
    return p if p.startswith("/") else f"/{p}"


def coindesk_reference_api_mode_resolved() -> str:
    """Whether reference preflight uses index /index/cc/* or spot /spot/* paths (see docs)."""
    dm = COINDESK_DEFAULT_MARKET.lower()
    if dm in COINDESK_INDEX_STYLE_MARKETS:
        return "index"
    m = COINDESK_REFERENCE_API_MODE
    if m == "auto":
        return "spot"
    if m == "index":
        return "index"
    return "spot"


def coindesk_effective_index_markets_path() -> str:
    if COINDESK_INDEX_MARKETS_PATH_EXPLICIT:
        return _coindesk_path_norm(COINDESK_INDEX_MARKETS_PATH)
    if coindesk_reference_api_mode_resolved() == "spot":
        return _coindesk_path_norm(COINDESK_SPOT_MARKETS_PATH or "/spot/v1/markets")
    return _coindesk_path_norm(COINDESK_INDEX_MARKETS_PATH or "/index/cc/v2/markets")


def coindesk_effective_mapped_instruments_path() -> str:
    if COINDESK_MAPPED_INSTRUMENTS_PATH_EXPLICIT:
        return _coindesk_path_norm(COINDESK_MAPPED_INSTRUMENTS_PATH)
    if coindesk_reference_api_mode_resolved() == "spot":
        return _coindesk_path_norm(COINDESK_SPOT_INSTRUMENTS_PATH or "/spot/v1/markets/instruments")
    return _coindesk_path_norm(COINDESK_MAPPED_INSTRUMENTS_PATH or "/index/cc/v1/markets/instruments")


def coindesk_runtime_validation_errors() -> list[str]:
    """Blocking inconsistencies between COINDESK_DEFAULT_MARKET, reference mode, and tick/OHLCV paths."""
    errs: list[str] = []
    dm = COINDESK_DEFAULT_MARKET.lower()
    if COINDESK_REFERENCE_API_MODE == "spot" and dm in COINDESK_INDEX_STYLE_MARKETS:
        errs.append("coindesk_reference_mode_spot_incompatible_with_index_market_cadli_or_ccix")
    if not COINDESK_CCIX_STRICT_VALIDATION:
        return errs
    if dm != "ccix":
        return errs
    tick = COINDESK_LATEST_TICK_PATH.strip().lower()
    if tick.startswith("/spot/"):
        errs.append("coindesk_ccix_requires_index_tick_path_not_spot")
    ohlcv = COINDESK_OHLCV_PATH.strip()
    if ohlcv and ohlcv.lower().startswith("/spot/"):
        errs.append("coindesk_ccix_requires_index_ohlcv_path_not_spot")
    return errs
# Asset / on-chain (parallel to CMC; not index OHLC — label features by source)
COINDESK_ASSET_TOP_LIST_ENABLED = _bool("COINDESK_ASSET_TOP_LIST_ENABLED", False)
COINDESK_ASSET_TOP_LIST_PATH = os.environ.get("COINDESK_ASSET_TOP_LIST_PATH", "/asset/v1/top/list").strip()
COINDESK_ASSET_TOP_LIST_PAGE = max(1, _int("COINDESK_ASSET_TOP_LIST_PAGE", 1))
COINDESK_ASSET_TOP_LIST_PAGE_SIZE = max(10, min(100, _int("COINDESK_ASSET_TOP_LIST_PAGE_SIZE", 50)))
COINDESK_ASSET_TOP_LIST_SORT_BY = os.environ.get(
    "COINDESK_ASSET_TOP_LIST_SORT_BY", "CIRCULATING_MKT_CAP_USD"
).strip()
COINDESK_ASSET_TOP_LIST_GROUPS = os.environ.get("COINDESK_ASSET_TOP_LIST_GROUPS", "").strip()
COINDESK_ASSET_TOP_LIST_CACHE_TTL_S = _float("COINDESK_ASSET_TOP_LIST_CACHE_TTL_S", 30.0)
COINDESK_ASSET_TOP_LIST_USE_REDIS = _bool("COINDESK_ASSET_TOP_LIST_USE_REDIS", False)
COINDESK_ASSET_TOP_LIST_WATCH_SYMBOLS = os.environ.get("COINDESK_ASSET_TOP_LIST_WATCH_SYMBOLS", "").strip()
COINDESK_ONCHAIN_BY_ADDRESS_PATH = os.environ.get(
    "COINDESK_ONCHAIN_BY_ADDRESS_PATH", "/onchain/v2/data/by/address"
).strip()
COINDESK_ONCHAIN_CHAIN_SUMMARY_PATH = os.environ.get(
    "COINDESK_ONCHAIN_CHAIN_SUMMARY_PATH", "/onchain/v3/summary/by/chain"
).strip()
# Append vwap_cum + OHLCV microstructure proxies after indicators in training pipeline
TRAIN_MICROSTRUCTURE_FEATURES = _bool("TRAIN_MICROSTRUCTURE_FEATURES", False)

# Live snapshot: GET /v1/global-metrics/quotes/latest (same API key as training)
CMC_GLOBAL_METRICS_ENABLED = _bool("CMC_GLOBAL_METRICS_ENABLED", True)
CMC_GLOBAL_METRICS_TTL_S = _int("CMC_GLOBAL_METRICS_TTL_S", 300)
# Optional coarse regime flags (poll >= 15m per CMC docs; default TTL 900s)
CMC_FEAR_GREED_ENABLED = _bool("CMC_FEAR_GREED_ENABLED", False)
CMC_FEAR_GREED_TTL_S = _int("CMC_FEAR_GREED_TTL_S", 900)
CMC_ALTCOIN_SEASON_ENABLED = _bool("CMC_ALTCOIN_SEASON_ENABLED", False)
CMC_ALTCOIN_SEASON_TTL_S = _int("CMC_ALTCOIN_SEASON_TTL_S", 900)
# Batch macro history merged onto training OHLCV (merge_asof backward; not for live hot path)
CMC_TRAIN_MACRO_MERGE_ENABLED = _bool("CMC_TRAIN_MACRO_MERGE_ENABLED", False)
CMC_TRAIN_MACRO_GLOBAL = _bool("CMC_TRAIN_MACRO_GLOBAL", True)
CMC_TRAIN_MACRO_FEAR_GREED = _bool("CMC_TRAIN_MACRO_FEAR_GREED", True)
CMC_TRAIN_MACRO_ALTCOIN = _bool("CMC_TRAIN_MACRO_ALTCOIN", False)
CMC_TRAIN_MACRO_PARQUET_PATH = os.environ.get("CMC_TRAIN_MACRO_PARQUET_PATH", "").strip()
# Extend regime RF with merged cmc_macro_* columns when present (requires macro merge + columns)
TRAIN_REGIME_MACRO_FEATURES = _bool("TRAIN_REGIME_MACRO_FEATURES", False)
TRAIN_EXCHANGE_SYMBOL = os.environ.get("TRAIN_EXCHANGE_SYMBOL", "")
TRAIN_EXCHANGE_LIMIT = _int("TRAIN_EXCHANGE_LIMIT", 1000)
TRAIN_MAX_ROWS = _int("TRAIN_MAX_ROWS", 0)
# Binance-scale history via ccxt pagination (when TRAIN_SOURCES includes exchange)
TRAIN_EXCHANGE_LOOKBACK_DAYS = _int("TRAIN_EXCHANGE_LOOKBACK_DAYS", 0)
# Epoch ms (optional). If both set, used instead of lookback when lookback is 0.
TRAIN_EXCHANGE_START_MS = os.environ.get("TRAIN_EXCHANGE_START_MS", "").strip()
TRAIN_EXCHANGE_END_MS = os.environ.get("TRAIN_EXCHANGE_END_MS", "").strip()
TRAIN_EXCHANGE_FETCH_SLEEP_S = _float("TRAIN_EXCHANGE_FETCH_SLEEP_S", 0.2)
TRAIN_EXCHANGE_CHUNK_LIMIT = _int("TRAIN_EXCHANGE_CHUNK_LIMIT", 1000)

# Cached training concat (parquet under ARTIFACTS_DIR)
TRAIN_DATASET_PATH = os.environ.get("TRAIN_DATASET_PATH", "training/datasets/latest.parquet")
# fetch_only | load_or_fetch | load_only
TRAIN_DATASET_MODE = os.environ.get("TRAIN_DATASET_MODE", "fetch_only").strip().lower()
# 0 = cache never expires by age (load_or_fetch always uses file if present)
TRAIN_DATASET_MAX_AGE_SECONDS = _int("TRAIN_DATASET_MAX_AGE_SECONDS", 0)
TRAIN_DATASET_VERSION_SNAPSHOT = _bool("TRAIN_DATASET_VERSION_SNAPSHOT", True)

# Artifacts root (models, fund state, halt file)
ARTIFACTS_DIR = os.environ.get("ARTIFACTS_DIR", "artifacts")

# Managers (fund / portfolio / advisory / training automation)
MANAGERS_ENABLED = _bool("MANAGERS_ENABLED", True)
PORTFOLIO_MODE = _bool("PORTFOLIO_MODE", True)
PORTFOLIO_WEIGHTS = os.environ.get("PORTFOLIO_WEIGHTS", "")
MAX_DRAWDOWN_PCT = _float("MAX_DRAWDOWN_PCT", 0.25)
PAPER_EQUITY_USDT = _float("PAPER_EQUITY_USDT", 10_000.0)
FUND_QUOTE_ASSET = os.environ.get("FUND_QUOTE_ASSET", "USDT")
# TTL for quote→USDT rate cache (position sizing on non-USDT pairs)
QUOTE_USDT_CACHE_SECONDS = _int("QUOTE_USDT_CACHE_SECONDS", 60)
FUND_REFRESH_SECONDS = _int("FUND_REFRESH_SECONDS", 300)
TRADING_HALT_FILENAME = os.environ.get("TRADING_HALT_FILENAME", "TRADING_HALTED")

ADVISORY_USE_REDIS = _bool("ADVISORY_USE_REDIS", True)
ADVISORY_TTL_SECONDS = _int("ADVISORY_TTL_SECONDS", 3600)
ADVISORY_WORKER_IN_PROCESS = _bool("ADVISORY_WORKER_IN_PROCESS", True)
ADVISORY_WORKER_INTERVAL_SECONDS = _int("ADVISORY_WORKER_INTERVAL_SECONDS", 900)

MIN_TRAIN_ROWS_VALIDATE = _int("MIN_TRAIN_ROWS_VALIDATE", 500)
TRAINING_PROMOTE_ON_SUCCESS = _bool("TRAINING_PROMOTE_ON_SUCCESS", False)

# Regime RandomForest (offline training + validation; defaults match prior hardcoded model)
TRAIN_RF_N_ESTIMATORS = _int("TRAIN_RF_N_ESTIMATORS", 100)
TRAIN_RF_RANDOM_STATE = _int("TRAIN_RF_RANDOM_STATE", 42)
TRAIN_RF_MAX_DEPTH = _int("TRAIN_RF_MAX_DEPTH", 8)
TRAIN_RF_MIN_SAMPLES_LEAF = _int("TRAIN_RF_MIN_SAMPLES_LEAF", 1)


def _train_rf_max_features() -> str | float | None:
    v = os.environ.get("TRAIN_RF_MAX_FEATURES", "").strip()
    if not v:
        return None
    vl = v.lower()
    if vl in ("sqrt", "log2", "auto"):
        return vl
    try:
        return float(v)
    except ValueError:
        return None


TRAIN_RF_MAX_FEATURES = _train_rf_max_features()

# Time-series CV for regime training (overfitting diagnostics)
TRAIN_RUN_VALIDATION = _bool("TRAIN_RUN_VALIDATION", True)
TRAIN_TS_SPLITS = _int("TRAIN_TS_SPLITS", 5)
TRAIN_MIN_ROWS_PER_FOLD = _int("TRAIN_MIN_ROWS_PER_FOLD", 80)
TRAIN_MIN_OOF_F1 = _float("TRAIN_MIN_OOF_F1", 0.0)
TRAIN_MAX_TRAIN_VAL_GAP = _float("TRAIN_MAX_TRAIN_VAL_GAP", 1.0)
TRAIN_VALIDATION_STRICT = _bool("TRAIN_VALIDATION_STRICT", False)
TRAIN_SKIP_VALIDATION_GATES = _bool("TRAIN_SKIP_VALIDATION_GATES", False)
TRAIN_VALIDATION_PER_GROUP = _bool("TRAIN_VALIDATION_PER_GROUP", False)

# Backtest (walk-forward on SYMBOL / TIMEFRAME)
BACKTEST_BARS = _int("BACKTEST_BARS", 1500)
BACKTEST_MAX_EQUITY_POINTS = _int("BACKTEST_MAX_EQUITY_POINTS", 200)
# Costs (bps = 0.01%%); applied per leg on notional when closing / marking
BACKTEST_FEE_BPS = _float("BACKTEST_FEE_BPS", 0.0)
# When >0 and BACKTEST_FEE_BPS==0, used as backtest taker bps; else default_backtest_fee_bps(symbol) uses effective_taker_bps.
BACKTEST_TAKER_BPS = _float("BACKTEST_TAKER_BPS", 0.0)
BACKTEST_SLIPPAGE_BPS = _float("BACKTEST_SLIPPAGE_BPS", 0.0)
# Sentiment label for decision_engine: neutral | bullish | bearish (live LLM replay not available historically)
BACKTEST_SENTIMENT_MODE = os.environ.get("BACKTEST_SENTIMENT_MODE", "neutral").strip().lower()
# always_pass | match_engine_disabled (same as live when USE_FUNDAMENTALS_FILTER is false); true_replay not supported
BACKTEST_FUNDAMENTALS_MODE = os.environ.get("BACKTEST_FUNDAMENTALS_MODE", "always_pass").strip().lower()
# If true, ta_ok is always True (ablation: ignore mean_reversion_ta_allows)
BACKTEST_SKIP_TA_FILTER = _bool("BACKTEST_SKIP_TA_FILTER", False)
# 0 = off; every Nth simulated bar append one audit row to result
BACKTEST_AUDIT_SAMPLE_STRIDE = _int("BACKTEST_AUDIT_SAMPLE_STRIDE", 0)
# If true, run_backtest_job runs a small ablation matrix (sentiment x TA) and stores under ablations
BACKTEST_RUN_ABLATION_MATRIX = _bool("BACKTEST_RUN_ABLATION_MATRIX", False)
# Include buy-and-hold benchmark on same window (from train_end close)
BACKTEST_INCLUDE_BUY_HOLD = _bool("BACKTEST_INCLUDE_BUY_HOLD", True)
# If true, walk-forward backtest skips new entries when MAX_TRADES_PER_DAY opens already hit (UTC day from bar ts)
BACKTEST_ENFORCE_MAX_TRADES_PER_DAY = _bool("BACKTEST_ENFORCE_MAX_TRADES_PER_DAY", False)
# Live-parity controls for backtest policy.
BACKTEST_ENFORCE_REGIME_CONFIDENCE = _bool("BACKTEST_ENFORCE_REGIME_CONFIDENCE", True)
BACKTEST_ENFORCE_SIGNAL_STRENGTH = _bool("BACKTEST_ENFORCE_SIGNAL_STRENGTH", True)
BACKTEST_ENFORCE_LOSS_COOLDOWN = _bool("BACKTEST_ENFORCE_LOSS_COOLDOWN", True)
BACKTEST_USE_CIRCUIT_BREAKER = _bool("BACKTEST_USE_CIRCUIT_BREAKER", True)
BACKTEST_CIRCUIT_MAX_CONSECUTIVE_LOSSES = _int(
    "BACKTEST_CIRCUIT_MAX_CONSECUTIVE_LOSSES",
    CIRCUIT_BREAKER_MAX_CONSECUTIVE_LOSSES,
)
# Backtest execution realism and risk-metric publication controls.
BACKTEST_EXECUTION_REALISM_ENABLED = _bool("BACKTEST_EXECUTION_REALISM_ENABLED", False)
BACKTEST_EXECUTION_SKIP_PCT = _float("BACKTEST_EXECUTION_SKIP_PCT", 0.0)
BACKTEST_EXECUTION_REJECT_PCT = _float("BACKTEST_EXECUTION_REJECT_PCT", 0.0)
BACKTEST_EXECUTION_PARTIAL_FILL_MIN = _float("BACKTEST_EXECUTION_PARTIAL_FILL_MIN", 1.0)
BACKTEST_EXECUTION_PARTIAL_FILL_MAX = _float("BACKTEST_EXECUTION_PARTIAL_FILL_MAX", 1.0)
BACKTEST_EXECUTION_RANDOM_SEED = _int("BACKTEST_EXECUTION_RANDOM_SEED", 42)
BACKTEST_ENFORCE_EXCHANGE_LIMITS = _bool("BACKTEST_ENFORCE_EXCHANGE_LIMITS", False)
BACKTEST_MIN_RISK_METRIC_OBS = _int("BACKTEST_MIN_RISK_METRIC_OBS", 30)
BACKTEST_EXIT_REALISM_ENABLED = _bool("BACKTEST_EXIT_REALISM_ENABLED", False)
BACKTEST_EXIT_DELAY_PROB_PCT = _float("BACKTEST_EXIT_DELAY_PROB_PCT", 0.0)
BACKTEST_EXIT_PARTIAL_FILL_MIN = _float("BACKTEST_EXIT_PARTIAL_FILL_MIN", 1.0)
BACKTEST_EXIT_PARTIAL_FILL_MAX = _float("BACKTEST_EXIT_PARTIAL_FILL_MAX", 1.0)
BACKTEST_EXIT_EXTRA_SLIPPAGE_BPS = _float("BACKTEST_EXIT_EXTRA_SLIPPAGE_BPS", 0.0)
# When exit delay fires, optionally fill at the next bar (open/close) with slippage/partial model (no second delay).
BACKTEST_EXIT_DELAY_FILL_NEXT_BAR = _bool("BACKTEST_EXIT_DELAY_FILL_NEXT_BAR", False)
_RAW_EXIT_DELAY_FILL_PRICE = os.environ.get("BACKTEST_EXIT_DELAY_FILL_PRICE", "open").strip().lower()
BACKTEST_EXIT_DELAY_FILL_PRICE = (
    _RAW_EXIT_DELAY_FILL_PRICE if _RAW_EXIT_DELAY_FILL_PRICE in ("open", "close") else "open"
)
# Intervals larger than nominal_bar * multiplier are treated as gaps for annualization step estimation.
BACKTEST_ANNUAL_GAP_MULTIPLIER = _float("BACKTEST_ANNUAL_GAP_MULTIPLIER", 3.0)

# After offline training: optional walk-forward gate using the trained regime (fetches TRAIN_BACKTEST_SYMBOL)
TRAIN_BACKTEST_GATE = _bool("TRAIN_BACKTEST_GATE", False)
TRAIN_BACKTEST_SYMBOL = os.environ.get("TRAIN_BACKTEST_SYMBOL", "").strip()
TRAIN_BACKTEST_BARS = _int("TRAIN_BACKTEST_BARS", 0)
TRAIN_BACKTEST_TIMEFRAME = os.environ.get("TRAIN_BACKTEST_TIMEFRAME", "").strip()
TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT = _optional_float("TRAIN_BACKTEST_MIN_TOTAL_RETURN_PCT")
TRAIN_BACKTEST_MAX_DRAWDOWN_PCT = _optional_float("TRAIN_BACKTEST_MAX_DRAWDOWN_PCT")

# Optional: append one JSON line per trading loop to this path (empty = off)
DECISION_AUDIT_JSONL_PATH = os.environ.get("DECISION_AUDIT_JSONL_PATH", "").strip()
DECISION_AUDIT_EVERY_N_LOOPS = _int("DECISION_AUDIT_EVERY_N_LOOPS", 1)

# Scheduler process (`python -m tr_bot.scheduler`)
SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", False)
SCHEDULER_TRAINING_INTERVAL_SECONDS = _int("SCHEDULER_TRAINING_INTERVAL_SECONDS", 0)
SCHEDULER_BACKTEST_INTERVAL_SECONDS = _int("SCHEDULER_BACKTEST_INTERVAL_SECONDS", 0)
SCHEDULER_INITIAL_DELAY_SECONDS = _float("SCHEDULER_INITIAL_DELAY_SECONDS", 60.0)
SCHEDULER_RUN_TRAINING_ON_START = _bool("SCHEDULER_RUN_TRAINING_ON_START", False)
SCHEDULER_RUN_BACKTEST_ON_START = _bool("SCHEDULER_RUN_BACKTEST_ON_START", False)
SCHEDULER_TICK_SECONDS = _int("SCHEDULER_TICK_SECONDS", 30)
SCHEDULER_PROMOTION_INTERVAL_SECONDS = _int("SCHEDULER_PROMOTION_INTERVAL_SECONDS", 0)
SCHEDULER_RUN_PROMOTION_ON_START = _bool("SCHEDULER_RUN_PROMOTION_ON_START", False)
SCHEDULER_COINDESK_ASSET_TOP_LIST_INTERVAL_SECONDS = _int("SCHEDULER_COINDESK_ASSET_TOP_LIST_INTERVAL_SECONDS", 0)
SCHEDULER_RUN_COINDESK_ASSET_TOP_LIST_ON_START = _bool("SCHEDULER_RUN_COINDESK_ASSET_TOP_LIST_ON_START", False)
SCHEDULER_DAILY_REPORT_INTERVAL_SECONDS = _int("SCHEDULER_DAILY_REPORT_INTERVAL_SECONDS", 0)
SCHEDULER_RUN_DAILY_REPORT_ON_START = _bool("SCHEDULER_RUN_DAILY_REPORT_ON_START", False)

# No-trade meta-tuning: streak → audit histogram → optional profile relax + isolated backtest gate
ADAPTATION_ENABLED = _bool("ADAPTATION_ENABLED", False)
ADAPTATION_MIN_LOOPS_NO_OPEN = _int("ADAPTATION_MIN_LOOPS_NO_OPEN", 48)
ADAPTATION_COOLDOWN_SECONDS = _float("ADAPTATION_COOLDOWN_SECONDS", 86400.0)
ADAPTATION_INTERVAL_SECONDS = _int("ADAPTATION_INTERVAL_SECONDS", 0)
ADAPTATION_AUDIT_TAIL_LINES = _int("ADAPTATION_AUDIT_TAIL_LINES", 400)
ADAPTATION_AUDIT_JSONL_PATH = os.environ.get("ADAPTATION_AUDIT_JSONL_PATH", "").strip()
ADAPTATION_REGIME_CONFIDENCE_STEP = _float("ADAPTATION_REGIME_CONFIDENCE_STEP", 0.05)
ADAPTATION_REGIME_CONFIDENCE_FLOOR = _float("ADAPTATION_REGIME_CONFIDENCE_FLOOR", 0.35)
ADAPTATION_RSI_DISTANCE_STEP = _float("ADAPTATION_RSI_DISTANCE_STEP", 0.5)
ADAPTATION_RSI_DISTANCE_FLOOR = _float("ADAPTATION_RSI_DISTANCE_FLOOR", 0.0)
ADAPTATION_ADX_MARGIN_STEP = _float("ADAPTATION_ADX_MARGIN_STEP", 0.5)
ADAPTATION_ADX_MARGIN_FLOOR = _float("ADAPTATION_ADX_MARGIN_FLOOR", 0.0)
ADAPTATION_RSI_BAND_STEP = _float("ADAPTATION_RSI_BAND_STEP", 1.0)
ADAPTATION_RSI_OVERSOLD_CEILING = _float("ADAPTATION_RSI_OVERSOLD_CEILING", 45.0)
ADAPTATION_RSI_OVERBOUGHT_FLOOR = _float("ADAPTATION_RSI_OVERBOUGHT_FLOOR", 55.0)
ADAPTATION_RELAX_RSI_ON_WEAK_SIGNAL = _bool("ADAPTATION_RELAX_RSI_ON_WEAK_SIGNAL", False)
ADAPTATION_ENABLE_TREND_ON_HOLD = _bool("ADAPTATION_ENABLE_TREND_ON_HOLD", False)
ADAPTATION_AUTO_APPLY_PAPER = _bool("ADAPTATION_AUTO_APPLY_PAPER", True)
ADAPTATION_BACKTEST_TIMEOUT_SECONDS = _int("ADAPTATION_BACKTEST_TIMEOUT_SECONDS", 300)
SCHEDULER_RUN_ADAPTATION_ON_START = _bool("SCHEDULER_RUN_ADAPTATION_ON_START", False)

# Comma-separated overlay keys an LLM proposal may touch (empty = built-in default subset in proposal_validate)
LLM_PROPOSAL_PARAM_KEYS = os.environ.get("LLM_PROPOSAL_PARAM_KEYS", "").strip()
DAILY_REPORT_MAX_TRADE_ROWS = _int("DAILY_REPORT_MAX_TRADE_ROWS", 10_000)

# Extended TA + fundamentals (filters / vetoes; regime model still uses base indicators only)
USE_EXTENDED_TA = _bool("USE_EXTENDED_TA", True)
USE_TA_FILTER = _bool("USE_TA_FILTER", True)
ADX_MAX_FOR_MEAN_REVERSION = _float("ADX_MAX_FOR_MEAN_REVERSION", 35.0)
# Trend continuation strategy ([tr_bot/signals.py] trend_continuation_signal); min ADX when USE_EXTENDED_TA
TREND_STRATEGY_ENABLED = _bool("TREND_STRATEGY_ENABLED", False)
ADX_MIN_FOR_TREND = _float("ADX_MIN_FOR_TREND", 22.0)

USE_FUNDAMENTALS_FILTER = _bool("USE_FUNDAMENTALS_FILTER", True)
FUNDAMENTALS_USE_REDIS = _bool("FUNDAMENTALS_USE_REDIS", True)
FUNDAMENTALS_CACHE_TTL_SECONDS = _int("FUNDAMENTALS_CACHE_TTL_SECONDS", 86_400)
FUNDAMENTALS_HTTP_TIMEOUT = _float("FUNDAMENTALS_HTTP_TIMEOUT", 10.0)
COINGECKO_IDS = os.environ.get("COINGECKO_IDS", "BTC=bitcoin,ETH=ethereum")
EARNINGS_BLOCK_SECONDS = _int("EARNINGS_BLOCK_SECONDS", 86_400)
MAX_CRYPTO_MARKET_CAP_RANK = _int("MAX_CRYPTO_MARKET_CAP_RANK", 500)

# Notifications (console + optional webhook)
NOTIFY_ENABLED = _bool("NOTIFY_ENABLED", True)
NOTIFY_CHANNELS = os.environ.get("NOTIFY_CHANNELS", "console")
NOTIFY_WEBHOOK_URL = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()
NOTIFY_WEBHOOK_FORMAT = os.environ.get("NOTIFY_WEBHOOK_FORMAT", "slack")
NOTIFY_WEBHOOK_TIMEOUT = _float("NOTIFY_WEBHOOK_TIMEOUT", 10.0)
NOTIFY_MIN_INTERVAL_SECONDS = _float("NOTIFY_MIN_INTERVAL_SECONDS", 30.0)
NOTIFY_ENVIRONMENT = os.environ.get("NOTIFY_ENVIRONMENT", "development")

# Dashboard (FastAPI); if set, /api/dashboard/* requires Bearer or X-Dashboard-Token
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()
# Shown on dashboard banner for remote debugging (e.g. https://bot.example.com)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

# Ops: heartbeat stale if last_loop older than this multiple of POLL_SECONDS
HEARTBEAT_STALE_POLL_MULTIPLIER = _int("HEARTBEAT_STALE_POLL_MULTIPLIER", 3)
# Rolling equity/NAV samples for dashboard sparkline
MAX_EQUITY_HISTORY_POINTS = _int("MAX_EQUITY_HISTORY_POINTS", 288)

# Throttle DEPENDENCIES_DEGRADED alerts (separate from NOTIFY_MIN_INTERVAL_SECONDS)
NOTIFY_DEPS_INTERVAL_SECONDS = _float("NOTIFY_DEPS_INTERVAL_SECONDS", 300.0)

# Deployment tier selects which pointer file is read: artifacts/deployment/<tier>_active.json
_DEPLOYMENT_TIER_RAW = os.environ.get("DEPLOYMENT_TIER", "").strip().lower()
if _DEPLOYMENT_TIER_RAW in ("paper", "live_canary", "live_full"):
    DEPLOYMENT_TIER = _DEPLOYMENT_TIER_RAW
elif _DEPLOYMENT_TIER_RAW:
    DEPLOYMENT_TIER = "paper"
else:
    DEPLOYMENT_TIER = "paper" if PAPER_TRADING else "live_full"

# Live canary: extra risk / notional caps (live_canary tier only; see docs/strategy_promotion.md)
CANARY_RISK_MULTIPLIER = _float("CANARY_RISK_MULTIPLIER", 1.0)
CANARY_MAX_NOTIONAL_USDT = _float("CANARY_MAX_NOTIONAL_USDT", 0.0)
CANARY_SYMBOLS = os.environ.get("CANARY_SYMBOLS", "").strip()

from tr_bot.strategy.profile import apply_profile_overlay

# Optional overlay from artifacts/strategy_profiles/<profile_id>.json
ACTIVE_STRATEGY_PROFILE_ID = apply_profile_overlay(sys.modules[__name__])

_es_post = str(EXECUTION_STYLE).strip().lower()
EXECUTION_STYLE = _es_post if _es_post in ("market", "twap") else "market"
TWAP_DURATION_SECONDS = max(300, min(86400, int(TWAP_DURATION_SECONDS)))

# Align simulated vs live execution with deployment tier
if DEPLOYMENT_TIER == "paper":
    PAPER_TRADING = True
elif DEPLOYMENT_TIER in ("live_canary", "live_full"):
    PAPER_TRADING = False


def effective_risk_per_trade() -> float:
    """Risk fraction after canary multiplier (live_canary only)."""
    if DEPLOYMENT_TIER == "live_canary":
        return float(RISK_PER_TRADE) * float(CANARY_RISK_MULTIPLIER)
    return float(RISK_PER_TRADE)


def _apply_tier_minimums() -> None:
    global TRAIN_MIN_OOF_F1
    global TRAIN_MAX_TRAIN_VAL_GAP
    global BACKTEST_MIN_RISK_METRIC_OBS
    global USE_MICROSTRUCTURE_GUARD
    global MICRO_USE_SPREAD_DEPTH_GUARD
    global RESTORE_RECONCILE_REQUIRE_ORDER_ID

    if DEPLOYMENT_TIER == "paper":
        return
    if DEPLOYMENT_TIER == "live_canary":
        TRAIN_MIN_OOF_F1 = max(float(TRAIN_MIN_OOF_F1), 0.35)
        TRAIN_MAX_TRAIN_VAL_GAP = min(float(TRAIN_MAX_TRAIN_VAL_GAP), 0.35)
        BACKTEST_MIN_RISK_METRIC_OBS = max(int(BACKTEST_MIN_RISK_METRIC_OBS), 40)
        USE_MICROSTRUCTURE_GUARD = True
        return
    if DEPLOYMENT_TIER == "live_full":
        TRAIN_MIN_OOF_F1 = max(float(TRAIN_MIN_OOF_F1), 0.45)
        TRAIN_MAX_TRAIN_VAL_GAP = min(float(TRAIN_MAX_TRAIN_VAL_GAP), 0.25)
        BACKTEST_MIN_RISK_METRIC_OBS = max(int(BACKTEST_MIN_RISK_METRIC_OBS), 60)
        USE_MICROSTRUCTURE_GUARD = True
        MICRO_USE_SPREAD_DEPTH_GUARD = True
        RESTORE_RECONCILE_REQUIRE_ORDER_ID = True


# Snapshot of tier-gated knobs before live_canary / live_full floors (for ops dashboards).
_TIER_SNAPSHOT_BEFORE: dict[str, Any] = {
    "TRAIN_MIN_OOF_F1": float(TRAIN_MIN_OOF_F1),
    "TRAIN_MAX_TRAIN_VAL_GAP": float(TRAIN_MAX_TRAIN_VAL_GAP),
    "BACKTEST_MIN_RISK_METRIC_OBS": int(BACKTEST_MIN_RISK_METRIC_OBS),
    "USE_MICROSTRUCTURE_GUARD": bool(USE_MICROSTRUCTURE_GUARD),
    "MICRO_USE_SPREAD_DEPTH_GUARD": bool(MICRO_USE_SPREAD_DEPTH_GUARD),
    "RESTORE_RECONCILE_REQUIRE_ORDER_ID": bool(RESTORE_RECONCILE_REQUIRE_ORDER_ID),
}

_apply_tier_minimums()


def effective_tier_overrides() -> dict[str, Any]:
    """Tier floor keys: `env_before_tier_apply` is after strategy profile overlay; `effective_after_tier` is post-`DEPLOYMENT_TIER` floors."""
    return {
        "deployment_tier": DEPLOYMENT_TIER,
        "env_before_tier_apply": _TIER_SNAPSHOT_BEFORE,
        "effective_after_tier": {
            "TRAIN_MIN_OOF_F1": float(TRAIN_MIN_OOF_F1),
            "TRAIN_MAX_TRAIN_VAL_GAP": float(TRAIN_MAX_TRAIN_VAL_GAP),
            "BACKTEST_MIN_RISK_METRIC_OBS": int(BACKTEST_MIN_RISK_METRIC_OBS),
            "USE_MICROSTRUCTURE_GUARD": bool(USE_MICROSTRUCTURE_GUARD),
            "MICRO_USE_SPREAD_DEPTH_GUARD": bool(MICRO_USE_SPREAD_DEPTH_GUARD),
            "RESTORE_RECONCILE_REQUIRE_ORDER_ID": bool(RESTORE_RECONCILE_REQUIRE_ORDER_ID),
        },
    }


STRATEGY_DAILY_ROLLUP_ENABLED = _bool("STRATEGY_DAILY_ROLLUP_ENABLED", False)

# Strategy promotion gates (python -m tr_bot.managers strategy-evaluate / strategy-promote)
PROMOTION_MIN_TRADES = _int("PROMOTION_MIN_TRADES", 20)
PROMOTION_MIN_CALENDAR_DAYS = _int("PROMOTION_MIN_CALENDAR_DAYS", 5)
PROMOTION_MAX_EQUITY_DRAWDOWN_PCT = _float("PROMOTION_MAX_EQUITY_DRAWDOWN_PCT", 0.25)
PROMOTION_MIN_PROFIT_FACTOR = _optional_float("PROMOTION_MIN_PROFIT_FACTOR")
PROMOTION_COOLDOWN_HOURS = _float("PROMOTION_COOLDOWN_HOURS", 168.0)
PROMOTION_FROZEN = _bool("PROMOTION_FROZEN", False)
PROMOTION_BREAK_COOLDOWN = _bool("PROMOTION_BREAK_COOLDOWN", False)

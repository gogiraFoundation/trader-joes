"""
Batch CoinMarketCap macro series for training (not for live hot paths).

Merges global metrics, Fear & Greed, and optional altcoin-season history onto
training rows using ``merge_asof(..., direction="backward")`` so each bar only
sees the last published point at or before that bar's time (no lookahead).
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tr_bot import config
from tr_bot.analysis.cmc_client import CMC_PRO_BASE, cmc_pro_get

logger = logging.getLogger("tr_bot.training.cmc_macro")

GLOBAL_HIST_URL = f"{CMC_PRO_BASE}/v1/global-metrics/quotes/historical"
FNG_HIST_URL = f"{CMC_PRO_BASE}/v3/fear-and-greed/historical"
ALTS_HIST_URL = f"{CMC_PRO_BASE}/v1/altcoin-season-index/historical"

MACRO_NUMERIC_COLS = (
    "cmc_macro_total_market_cap",
    "cmc_macro_total_volume_24h",
    "cmc_macro_btc_dominance",
    "cmc_macro_fng_value",
    "cmc_macro_altcoin_season",
)


def _interval_for_global(train_tf: str) -> str:
    """Global-metrics historical supports coarse intervals; use hourly only for 1h bars."""
    t = (train_tf or "1d").strip().lower()
    if t == "1h":
        return "hourly"
    return "daily"


def _parse_global_hist_quotes(payload: dict[str, Any], convert: str) -> pd.DataFrame:
    data = payload.get("data")
    if not isinstance(data, dict):
        return pd.DataFrame()
    quotes = data.get("quotes")
    if not isinstance(quotes, list):
        return pd.DataFrame()
    cu = convert.upper().strip() or "USD"
    rows: list[dict[str, Any]] = []
    for q in quotes:
        if not isinstance(q, dict):
            continue
        ts_raw = q.get("timestamp")
        if ts_raw is None:
            continue
        t = pd.Timestamp(ts_raw)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        else:
            t = t.tz_convert("UTC")
        sec = int(t.timestamp())
        btc_dom = q.get("btc_dominance")
        quote = (q.get("quote") or {}).get(cu) if isinstance(q.get("quote"), dict) else {}
        if not isinstance(quote, dict):
            quote = {}
        tmc = quote.get("total_market_cap", q.get("total_market_cap"))
        tv = quote.get("total_volume_24h", q.get("total_volume_24h"))
        rows.append(
            {
                "timestamp": sec,
                "cmc_macro_btc_dominance": float(btc_dom) if btc_dom is not None else float("nan"),
                "cmc_macro_total_market_cap": float(tmc) if tmc is not None else float("nan"),
                "cmc_macro_total_volume_24h": float(tv) if tv is not None else float("nan"),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("timestamp").drop_duplicates(subset=["timestamp"])


def fetch_global_metrics_historical_df(
    api_key: str,
    *,
    convert: str,
    interval: str,
    count: int,
) -> pd.DataFrame:
    params: dict[str, Any] = {
        "convert": (convert or "USD").strip().upper() or "USD",
        "interval": interval,
        "count": max(1, min(int(count), 10_000)),
    }
    r = cmc_pro_get(GLOBAL_HIST_URL, api_key=api_key, params=params, timeout=90.0)
    if r.status_code != 200:
        logger.warning("CMC global historical HTTP %s: %s", r.status_code, r.text[:400])
        return pd.DataFrame()
    try:
        payload = r.json()
    except ValueError:
        return pd.DataFrame()
    st = payload.get("status") or {}
    if st.get("error_code") not in (None, 0):
        logger.warning("CMC global historical API error: %s", st)
        return pd.DataFrame()
    return _parse_global_hist_quotes(payload, params["convert"])


def _parse_fng_hist(payload: dict[str, Any]) -> pd.DataFrame:
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    if not isinstance(data, list):
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        ts_raw = row.get("timestamp")
        if ts_raw is None:
            continue
        t = pd.Timestamp(ts_raw)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        else:
            t = t.tz_convert("UTC")
        sec = int(t.timestamp())
        v = row.get("value")
        rows.append(
            {
                "timestamp": sec,
                "cmc_macro_fng_value": float(v) if v is not None else float("nan"),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("timestamp").drop_duplicates(subset=["timestamp"])


def fetch_fear_greed_historical_df(api_key: str, *, limit: int) -> pd.DataFrame:
    lim = max(1, min(int(limit), 500))
    r = cmc_pro_get(FNG_HIST_URL, api_key=api_key, params={"limit": lim}, timeout=90.0)
    if r.status_code != 200:
        logger.warning("CMC fear-greed historical HTTP %s: %s", r.status_code, r.text[:400])
        return pd.DataFrame()
    try:
        payload = r.json()
    except ValueError:
        return pd.DataFrame()
    st = payload.get("status") or {}
    if st.get("error_code") not in (None, 0):
        logger.warning("CMC fear-greed historical API error: %s", st)
        return pd.DataFrame()
    return _parse_fng_hist(payload)


def _parse_alt_hist(payload: dict[str, Any]) -> pd.DataFrame:
    data = payload.get("data")
    if isinstance(data, dict) and "points" in data:
        pts = data.get("points")
    elif isinstance(data, list):
        pts = data
    else:
        pts = None
    if not isinstance(pts, list):
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for row in pts:
        if not isinstance(row, dict):
            continue
        ts_raw = row.get("timestamp") or row.get("time_open")
        if ts_raw is None:
            continue
        t = pd.Timestamp(ts_raw)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        else:
            t = t.tz_convert("UTC")
        sec = int(t.timestamp())
        v = row.get("value") if row.get("value") is not None else row.get("index")
        rows.append(
            {
                "timestamp": sec,
                "cmc_macro_altcoin_season": float(v) if v is not None else float("nan"),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("timestamp").drop_duplicates(subset=["timestamp"])


def fetch_altcoin_season_historical_df(api_key: str, *, limit: int) -> pd.DataFrame:
    lim = max(1, min(int(limit), 500))
    r = cmc_pro_get(ALTS_HIST_URL, api_key=api_key, params={"limit": lim}, timeout=90.0)
    if r.status_code != 200:
        logger.warning("CMC altcoin season historical HTTP %s: %s", r.status_code, r.text[:400])
        return pd.DataFrame()
    try:
        payload = r.json()
    except ValueError:
        return pd.DataFrame()
    st = payload.get("status") or {}
    if st.get("error_code") not in (None, 0):
        logger.warning("CMC altcoin season historical API error: %s", st)
        return pd.DataFrame()
    return _parse_alt_hist(payload)


def merge_cmc_macro_training_features(raw: pd.DataFrame, api_key: str) -> pd.DataFrame:
    """
    Left-merge macro columns onto ``raw`` by ``timestamp`` (UTC epoch seconds).
    """
    if raw.empty or "timestamp" not in raw.columns:
        return raw
    key = (api_key or "").strip()
    if not key:
        logger.warning("CMC macro merge skipped: empty CMC_API_KEY")
        return raw

    out = raw.copy()
    u_ts = pd.DataFrame({"timestamp": sorted(out["timestamp"].astype(int).unique())})

    convert = (config.CMC_CONVERT or "USD").strip() or "USD"
    g_interval = _interval_for_global(config.TRAIN_TIMEFRAME)
    n_ts = len(u_ts)
    macro_count = max(n_ts, int(config.CMC_COUNT), 100)

    merged = u_ts.copy()

    if config.CMC_TRAIN_MACRO_GLOBAL:
        gdf = fetch_global_metrics_historical_df(
            key,
            convert=convert,
            interval=g_interval,
            count=min(macro_count, 10_000),
        )
        if not gdf.empty:
            merged = pd.merge_asof(
                merged.sort_values("timestamp"),
                gdf.sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )

    if config.CMC_TRAIN_MACRO_FEAR_GREED:
        fdf = fetch_fear_greed_historical_df(key, limit=min(500, max(macro_count, 50)))
        if not fdf.empty:
            merged = pd.merge_asof(
                merged.sort_values("timestamp"),
                fdf.sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )

    if config.CMC_TRAIN_MACRO_ALTCOIN:
        adf = fetch_altcoin_season_historical_df(key, limit=min(500, max(90, n_ts)))
        if not adf.empty:
            merged = pd.merge_asof(
                merged.sort_values("timestamp"),
                adf.sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )

    for c in MACRO_NUMERIC_COLS:
        if c not in merged.columns:
            merged[c] = float("nan")

    macro_cols = [c for c in MACRO_NUMERIC_COLS if c in merged.columns]
    lookup = merged.set_index("timestamp")[macro_cols]
    for c in macro_cols:
        out[c] = out["timestamp"].astype(int).map(lookup[c])

    out[macro_cols] = out[macro_cols].ffill().bfill().fillna(0.0)

    pq = (config.CMC_TRAIN_MACRO_PARQUET_PATH or "").strip()
    if pq:
        try:
            from tr_bot.managers.security import safe_artifact_path
            from pathlib import Path

            root = Path(config.ARTIFACTS_DIR).resolve()
            path = safe_artifact_path(root, pq)
            path.parent.mkdir(parents=True, exist_ok=True)
            out.to_parquet(path, index=False)
            logger.info("Wrote CMC macro merge to %s", path.resolve())
        except OSError as e:
            logger.warning("CMC macro parquet write failed: %s", e)

    return out

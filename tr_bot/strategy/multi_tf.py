"""Multi-timeframe OHLCV fetch/cache helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from tr_bot import config
from tr_bot.managers.security import safe_artifact_path

logger = logging.getLogger("tr_bot.strategy.multi_tf")

DEFAULT_TFS = ("4h", "1h", "15m", "5m")


def configured_timeframes() -> tuple[str, str, str, str]:
    return (
        str(getattr(config, "SMC_HTF_TIMEFRAME", "4h") or "4h"),
        str(getattr(config, "SMC_PRIMARY_TIMEFRAME", "1h") or "1h"),
        str(getattr(config, "SMC_ENTRY_TIMEFRAME", "15m") or "15m"),
        str(getattr(config, "SMC_EXEC_TIMEFRAME", "5m") or "5m"),
    )


def _cache_dir() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    safe_artifact_path(root, "cache/mtf")  # validate under artifacts root
    p = root / "cache" / "mtf"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cache_key(symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")[:80]
    return _cache_dir() / f"{safe}_{timeframe}.json"


def save_mtf_cache(symbol: str, timeframe: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    path = _cache_key(symbol, timeframe)
    try:
        payload = {
            "symbol": symbol,
            "timeframe": timeframe,
            "rows": df.tail(500).to_dict(orient="list"),
        }
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    except OSError as e:
        logger.debug("mtf cache write failed: %s", e)


def load_mtf_cache(symbol: str, timeframe: str) -> pd.DataFrame | None:
    path = _cache_key(symbol, timeframe)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw.get("rows") or {}
        df = pd.DataFrame(rows)
        if df.empty:
            return None
        return df
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def fetch_multi_tf(
    symbol: str,
    *,
    timeframes: tuple[str, ...] | None = None,
    limit: int = 200,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for each timeframe via market_data.fetch_ohlcv.
    Falls back to cache on failure when use_cache=True.
    """
    from tr_bot.market_data import fetch_ohlcv

    tfs = timeframes or configured_timeframes()
    out: dict[str, pd.DataFrame] = {}
    for tf in tfs:
        try:
            df = fetch_ohlcv(symbol, tf, limit)
            if df is not None and not df.empty:
                out[tf] = df.reset_index(drop=True)
                if use_cache:
                    save_mtf_cache(symbol, tf, out[tf])
                continue
        except Exception as e:
            logger.warning("multi_tf fetch failed %s %s: %s", symbol, tf, e)
        if use_cache:
            cached = load_mtf_cache(symbol, tf)
            if cached is not None and not cached.empty:
                out[tf] = cached
                logger.info("multi_tf used cache for %s %s", symbol, tf)
    return out


def slice_window_as_of(
    df: pd.DataFrame,
    *,
    as_of_ts: int | None,
    max_bars: int = 200,
) -> pd.DataFrame:
    """Return bars with timestamp <= as_of_ts (inclusive), last max_bars."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df
    if as_of_ts is not None and "timestamp" in df.columns:
        out = df[df["timestamp"].astype("int64") <= int(as_of_ts)]
    return out.tail(max_bars).reset_index(drop=True)


def align_htf_bias_frame(
    dfs_by_tf: dict[str, pd.DataFrame],
    *,
    htf: str,
    as_of_ts: int | None = None,
) -> pd.DataFrame:
    df = dfs_by_tf.get(htf)
    if df is None:
        return pd.DataFrame()
    return slice_window_as_of(df, as_of_ts=as_of_ts)


def pick_exec_frame(
    dfs_by_tf: dict[str, pd.DataFrame],
    *,
    preferred: tuple[str, ...] | None = None,
) -> tuple[str, pd.DataFrame]:
    # Prefer exec/entry first
    prefs = list(configured_timeframes())
    ordered = [prefs[3], prefs[2], prefs[1], prefs[0]]
    if preferred:
        ordered = list(preferred) + [t for t in ordered if t not in preferred]
    for tf in ordered:
        df = dfs_by_tf.get(tf)
        if df is not None and len(df) >= 20:
            return tf, df
    for tf, df in dfs_by_tf.items():
        if df is not None and len(df) >= 20:
            return tf, df
    # empty fallback
    for tf, df in dfs_by_tf.items():
        return tf, df if df is not None else pd.DataFrame()
    return "5m", pd.DataFrame()

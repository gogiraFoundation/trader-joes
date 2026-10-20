"""Microstructure-style features from OHLCV and optional order book snapshots."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_vwap_columns(
    df: pd.DataFrame,
    *,
    group_cols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Session-ordered VWAP: cumulative sum(typical * volume) / cumsum(volume) per group.
    """
    out = df.copy()
    if group_cols is None:
        group_cols = [c for c in ("source", "asset") if c in out.columns]

    def _vwap_series(g: pd.DataFrame) -> pd.Series:
        tp = (g["high"] + g["low"] + g["close"]) / 3.0
        v = g["volume"].astype(float)
        return (tp * v).cumsum() / v.cumsum().replace(0, np.nan)

    if not group_cols:
        out["vwap_cum"] = _vwap_series(out)
        return out

    sort_keys = ["timestamp"] + [c for c in group_cols if c in out.columns]
    out = out.sort_values(sort_keys, kind="mergesort")
    out["vwap_cum"] = out.groupby(group_cols, sort=False, group_keys=False).apply(_vwap_series)
    return out


def add_ohlcv_microstructure_proxies(
    df: pd.DataFrame,
    *,
    group_cols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Without L2 data: bar range position, volume z-score momentum proxy, return velocity.

    Return / rolling series are computed per ``source``/``asset`` when present so
    multi-asset frames do not bleed across groups.
    """
    out = df.copy()
    if group_cols is None:
        group_cols = [c for c in ("source", "asset") if c in out.columns]

    hl = (out["high"] - out["low"]).replace(0, np.nan)
    out["bar_position"] = ((out["close"] - out["low"]) / hl).clip(0, 1)
    out["bar_range_pct"] = (hl / out["close"].replace(0, np.nan)) * 100.0

    if not group_cols:
        out["ret_1"] = out["close"].pct_change()
        out["volatility_proxy"] = out["ret_1"].rolling(20, min_periods=5).std()
        out["volume_velocity"] = out["volume"].pct_change()
        return out

    sort_keys = list(group_cols)
    if "timestamp" in out.columns:
        sort_keys.append("timestamp")
    out = out.sort_values(sort_keys, kind="mergesort")
    g = out.groupby(group_cols, sort=False)
    out["ret_1"] = g["close"].pct_change()
    out["volatility_proxy"] = g["ret_1"].transform(lambda s: s.rolling(20, min_periods=5).std())
    out["volume_velocity"] = g["volume"].pct_change()
    return out


def orderbook_features_row(bids: list[list[float]], asks: list[list[float]]) -> dict[str, float]:
    """Single-row spread, imbalance, top depth from L2 lists [[price, size], ...]."""
    best_bid = bids[0][0] if bids else float("nan")
    best_ask = asks[0][0] if asks else float("nan")
    spread = best_ask - best_bid if bids and asks else float("nan")
    mid = (best_bid + best_ask) / 2 if bids and asks else float("nan")
    spread_pct = (spread / mid * 100.0) if mid and mid == mid and mid != 0 else float("nan")
    bv = sum(x[1] for x in bids[:5]) if bids else 0.0
    av = sum(x[1] for x in asks[:5]) if asks else 0.0
    tot = bv + av
    imbalance = (bv - av) / tot if tot > 0 else 0.0
    return {
        "ob_spread": spread,
        "ob_spread_pct": spread_pct,
        "ob_imbalance_5": imbalance,
        "ob_bid_depth_5": bv,
        "ob_ask_depth_5": av,
    }


def orderbook_frame_features(df: pd.DataFrame) -> pd.DataFrame:
    """Expect columns bids_json, asks_json as list columns or JSON-parseable."""
    out = df.copy()
    rows: list[dict[str, float]] = []
    for _, r in out.iterrows():
        b = r.get("bids") or []
        a = r.get("asks") or []
        if hasattr(b, "tolist"):
            b = b.tolist()
        if hasattr(a, "tolist"):
            a = a.tolist()
        rows.append(orderbook_features_row(list(b), list(a)))
    feat = pd.DataFrame(rows, index=out.index)
    return pd.concat([out, feat], axis=1)

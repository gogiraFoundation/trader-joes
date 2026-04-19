"""Extended technical analysis (MACD, Bollinger, ADX, Stochastic, volume z-score)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def extend_ta(df: pd.DataFrame) -> pd.DataFrame:
    """Append TA columns to OHLCV DataFrame (expects `close`, `high`, `low`, `volume`)."""
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    vol = out["volume"].astype(float)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    out["bb_mid"] = mid
    out["bb_upper"] = mid + 2.0 * std
    out["bb_lower"] = mid - 2.0 * std

    low14 = low.rolling(14).min()
    high14 = high.rolling(14).max()
    rng = (high14 - low14).replace(0, np.nan)
    out["stoch_k"] = 100.0 * (close - low14) / rng
    out["stoch_d"] = out["stoch_k"].rolling(3).mean()

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=out.index, dtype=float)
    minus_dm = pd.Series(minus_dm, index=out.index, dtype=float)
    period = 14
    atr_w = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_w.replace(0, np.nan))
    minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_w.replace(0, np.nan))
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    out["adx"] = dx.ewm(alpha=1.0 / period, adjust=False).mean()

    vm = vol.rolling(20).mean()
    vs = vol.rolling(20).std().replace(0, np.nan)
    out["volume_z"] = (vol - vm) / vs

    return out


def mean_reversion_ta_allows(row: pd.Series) -> bool:
    """
    If ADX is very high, treat market as strongly trending; skip mean-reversion entries.
    Fail-open on missing columns.
    """
    from tr_bot import config

    if not config.USE_TA_FILTER:
        return True
    adx = row.get("adx")
    if adx is None or (isinstance(adx, float) and np.isnan(adx)):
        return True
    if float(adx) > float(config.ADX_MAX_FOR_MEAN_REVERSION):
        return False
    return True


def trend_regime_ta_allows(row: pd.Series) -> bool:
    """
    For trend continuation entries: require sufficient ADX (trend strength).
    Fail-open on missing ADX (same spirit as mean_reversion_ta_allows).
    """
    from tr_bot import config

    if not config.USE_TA_FILTER:
        return True
    adx = row.get("adx")
    if adx is None or (isinstance(adx, float) and np.isnan(adx)):
        return True
    if float(adx) < float(config.ADX_MIN_FOR_TREND):
        return False
    return True

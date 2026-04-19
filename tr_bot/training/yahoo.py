"""Yahoo Finance OHLCV for offline training (longer history than live loop)."""

from __future__ import annotations

import logging

import pandas as pd

from tr_bot import config
from tr_bot.ohlcv import timeframe_to_yfinance_interval, yfinance_history_to_ohlcv

logger = logging.getLogger("tr_bot.training.yahoo")


# Known USD-pegged crypto symbols on Yahoo; regime/volatility training adds little from near-flat series.
STABLECOIN_USD_TICKERS: frozenset[str] = frozenset(
    {
        "USDT-USD",
        "USDC-USD",
        "DAI-USD",
        "BUSD-USD",
        "TUSD-USD",
        "USDP-USD",
        "FDUSD-USD",
        "PYUSD-USD",
        "EURC-USD",
        "GUSD-USD",
        "USDD-USD",
        "FRAX-USD",
    }
)


def normalize_yahoo_training_ticker(ticker: str) -> str:
    """
    Map common Binance-style symbols to Yahoo tickers.

    Yahoo does not list *-USDT pairs; spot USD quotes use *-USD (e.g. ETH-USD not ETH-USDT).
    """
    t = (ticker or "").strip().upper()
    if t.endswith("-USDT"):
        return t[: -len("-USDT")] + "-USD"
    return t.strip()


def warn_yahoo_training_quality(
    df: pd.DataFrame,
    ysym: str,
    *,
    min_daily_return_stddev: float,
    min_close_range_pct: float,
) -> None:
    """Log warnings for stablecoin-like symbols or suspiciously flat OHLCV."""
    u = ysym.strip().upper()
    if u in STABLECOIN_USD_TICKERS:
        logger.warning(
            "Yahoo training: %s matches a known USD stablecoin pair; prices are ~flat and may dilute regime features.",
            ysym,
        )
    if len(df) < 2:
        return
    close = df["close"].astype(float)
    if min_daily_return_stddev > 0:
        r = close.pct_change().dropna()
        if len(r) > 0:
            std = float(r.std())
            if std < min_daily_return_stddev:
                logger.warning(
                    "Yahoo training: %s daily close return std=%.6g is below YAHOO_TRAIN_MIN_DAILY_RETURN_STDDEV=%s (stablecoin or illiquid?)",
                    ysym,
                    std,
                    min_daily_return_stddev,
                )
    if min_close_range_pct > 0:
        lo, hi = float(close.min()), float(close.max())
        mid = (lo + hi) / 2.0
        if mid > 0:
            span = (hi - lo) / mid
            if span < min_close_range_pct:
                logger.warning(
                    "Yahoo training: %s close range vs mid (%.6g) is below YAHOO_TRAIN_MIN_CLOSE_RANGE_PCT=%s",
                    ysym,
                    span,
                    min_close_range_pct,
                )


def fetch_yahoo_training(
    ticker: str,
    timeframe: str,
    period: str,
) -> pd.DataFrame:
    import yfinance as yf

    ysym = normalize_yahoo_training_ticker(ticker)
    if ysym != ticker.strip():
        logger.info("Yahoo training: using %s (requested %s)", ysym, ticker.strip())

    interval = timeframe_to_yfinance_interval(timeframe)
    hist = yf.Ticker(ysym).history(
        period=period,
        interval=interval,
        auto_adjust=False,
    )
    if hist.empty:
        raise RuntimeError(f"yfinance: no data for {ysym!r} period={period} interval={interval}")
    df = yfinance_history_to_ohlcv(hist)
    df["source"] = "yahoo"
    df["asset"] = ysym
    warn_yahoo_training_quality(
        df,
        ysym,
        min_daily_return_stddev=config.YAHOO_TRAIN_MIN_DAILY_RETURN_STDDEV,
        min_close_range_pct=config.YAHOO_TRAIN_MIN_CLOSE_RANGE_PCT,
    )
    return df

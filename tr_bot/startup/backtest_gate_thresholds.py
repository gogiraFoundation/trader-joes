"""Defaults for startup and adaptation backtest gates when STARTUP_BACKTEST_* env vars are unset.

Tune via .env: STARTUP_BACKTEST_MIN_RETURN_PCT, STARTUP_BACKTEST_MAX_DD_PCT, STARTUP_BACKTEST_MIN_TRADES.
"""

from __future__ import annotations

# Allow modest negative totals on short walk-forward windows (-0.52% can sit just below -0.5).
# Stricter: set STARTUP_BACKTEST_MIN_RETURN_PCT=-0.5 or 0 in .env.
DEFAULT_MIN_RETURN_PCT = -1.0
# Max allowed drawdown (% points); typical paper range 10–20.
DEFAULT_MAX_DRAWDOWN_PCT = 12.0
# Smoke-test floor: reject zero-trade profiles at startup. Override via STARTUP_BACKTEST_MIN_TRADES.
# Promotion uses separate PROMOTION_MIN_TRADES (typically higher).
DEFAULT_MIN_TRADES = 3

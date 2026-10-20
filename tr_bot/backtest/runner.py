"""Walk-forward backtest: regime + mean-reversion + ATR position sim (paper)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import math
import random
import pandas as pd

from tr_bot import config
from tr_bot.fees import default_backtest_fee_bps
from tr_bot.analysis.fundamentals import allows_fundamentals
from tr_bot.decision import decision_engine
from tr_bot.indicators import compute_all_indicators
from tr_bot.logger import setup_logger
from tr_bot.market_data import fetch_ohlcv_deep
from tr_bot.notifications import events as notify_events
from tr_bot.notifications.facade import notify
from tr_bot.exit_plan import build_exit_plan, simulate_software_polled_exit_price
from tr_bot.position import OpenPosition
from tr_bot.replay import replay_bar_exchange_native, replay_bar_software_polled
from tr_bot.regime import RegimeModel
from tr_bot.quote_usdt import QuoteUsdtRateError, usdt_per_quote_unit
from tr_bot.risk import RiskManager
from tr_bot.strategy.tier import apply_backtest_notional_cap, atr_meets_min_pct
from tr_bot.symbols import split_pair
from tr_bot.signals import mean_reversion_signal, trend_continuation_signal
from tr_bot.ta_extended import mean_reversion_ta_allows, trend_regime_ta_allows

from tr_bot.backtest.last_run import write_backtest_last_run
from tr_bot.exchange import get_ccxt_exchange

logger = setup_logger("tr_bot.backtest")

# When every closed trade is a win (gross_loss_abs == 0), ratio is undefined; emit a cap for JSON/reporting.
PROFIT_FACTOR_NO_LOSS_CAP = 999.0

# Backtest uses high daily limit so one historical run is not capped by calendar day.
_BACKTEST_MAX_DAILY = 100_000

_VALID_SENTIMENTS = frozenset({"neutral", "bullish", "bearish"})
_EXCHANGE_LIMITS_CACHE: dict[str, dict[str, Any] | None] = {}


def _tf_minutes(timeframe: str) -> int:
    tf = (timeframe or "").strip().lower()
    if tf.endswith("m"):
        return max(1, int(tf[:-1] or "1"))
    if tf.endswith("h"):
        return max(1, int(tf[:-1] or "1") * 60)
    if tf.endswith("d"):
        return max(1, int(tf[:-1] or "1") * 60 * 24)
    return 1


def _steps_per_year_from_timestamps(
    timestamps: list[int], timeframe: str
) -> tuple[float, str, dict[str, Any]]:
    """Estimate steps/year from bar timestamps; down-weight long gaps vs nominal bar length."""
    nominal_sec = float(_tf_minutes(timeframe)) * 60.0
    gap_mult = max(1.0, float(config.BACKTEST_ANNUAL_GAP_MULTIPLIER))
    gap_sec = nominal_sec * gap_mult
    fallback_steps = max(1.0, 365.25 * 24.0 * 3600.0 / max(nominal_sec, 1e-9))

    ts = [int(t) for t in timestamps if isinstance(t, (int, float)) and int(t) > 0]
    diffs: list[float] = []
    for i in range(1, len(ts)):
        d = float(ts[i] - ts[i - 1])
        if d <= 0:
            continue
        if d > 10**12:
            d = d / 1000.0
        diffs.append(d)

    meta: dict[str, Any] = {
        "nominal_bar_seconds": round(nominal_sec, 6),
        "gap_threshold_seconds": round(gap_sec, 6),
        "gap_multiplier": gap_mult,
    }

    if not diffs:
        meta["fallback_note"] = "no positive inter-bar intervals; using timeframe nominal"
        return (fallback_steps, "timeframe_fallback", meta)

    regular = [d for d in diffs if d <= gap_sec]
    gaps = [d for d in diffs if d > gap_sec]
    meta["intervals_total"] = len(diffs)
    meta["gap_interval_count"] = len(gaps)
    meta["regular_interval_count"] = len(regular)

    if not regular:
        meta["fallback_note"] = (
            "all inter-bar intervals exceed gap threshold vs nominal bar; "
            "using timeframe nominal for steps/year"
        )
        return (fallback_steps, "timeframe_fallback", meta)

    med = sorted(regular)[len(regular) // 2]
    if med <= 0:
        meta["fallback_note"] = "non-positive median interval; using timeframe nominal"
        return (fallback_steps, "timeframe_fallback", meta)

    if gaps:
        meta["fallback_note"] = (
            f"excluded {len(gaps)} gap interval(s) from median (threshold {gap_sec:.0f}s)"
        )
    meta["median_interval_seconds"] = round(med, 6)
    src = "timestamp_median_regular" if gaps else "timestamp_median"
    return (365.25 * 24.0 * 3600.0 / med, src, meta)


def _bar_utc_date(bar: pd.Series) -> date | None:
    """UTC calendar day from OHLCV bar timestamp (ms or s)."""
    ts = bar.get("timestamp")
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return None
    try:
        t = int(ts)
    except (TypeError, ValueError):
        return None
    if t > 10**12:
        t //= 1000
    return datetime.fromtimestamp(t, tz=timezone.utc).date()


class _BacktestDailyOpenLimiter:
    """Limit new positions per UTC day using bar timestamps (matches live daily cap intent)."""

    def __init__(self, max_per_day: int) -> None:
        self.max_per_day = max(1, int(max_per_day))
        self._day: date | None = None
        self._opens = 0

    def on_bar(self, bar: pd.Series) -> None:
        d = _bar_utc_date(bar)
        if d is None:
            return
        if d != self._day:
            self._day = d
            self._opens = 0

    def can_open(self) -> bool:
        return self._opens < self.max_per_day

    def record_open(self) -> None:
        self._opens += 1


def _backtest_exit_fill_price(pos: OpenPosition, bar: pd.Series, outcome: str) -> float:
    """Policy-aware exit fill for backtest (shared with replay parity)."""
    plan = pos.exit_plan()
    policy = str(getattr(config, "BACKTEST_EXIT_POLICY", getattr(config, "EXIT_POLICY", "software_polled")))
    if policy == "exchange_native":
        ev = replay_bar_exchange_native(plan, bar)
    else:
        ev = replay_bar_software_polled(plan, bar)
    if ev.exit_price is not None:
        return float(ev.exit_price)
    return simulate_software_polled_exit_price(
        side=plan.side,
        stop_price=plan.stop_price,
        take_price=plan.take_price,
        bar=bar,
        outcome=outcome,
    )


def _exit_price(side: str, entry: float, atr: float, outcome: str) -> float:
    """Legacy ATR-only exit (kept for tests); prefer _backtest_exit_fill_price."""
    if side == "long":
        if outcome == "loss":
            return entry - atr
        return entry + 1.5 * atr
    if outcome == "loss":
        return entry + atr
    return entry - 1.5 * atr


def _long_pnl_with_costs(
    entry_px: float,
    exit_px: float,
    size: float,
    fee_bps: float,
    slip_bps: float,
) -> float:
    """Long: adverse slippage on buy and sell; fees on both notionals."""
    e = entry_px * (1.0 + slip_bps / 10_000.0)
    x = exit_px * (1.0 - slip_bps / 10_000.0)
    gross = (x - e) * size
    fees = (fee_bps / 10_000.0) * (entry_px * size + exit_px * size)
    return gross - fees


def _short_pnl_with_costs(
    entry_px: float,
    exit_px: float,
    size: float,
    fee_bps: float,
    slip_bps: float,
) -> float:
    """Short: slippage on entry (sell) and exit (cover)."""
    e = entry_px * (1.0 - slip_bps / 10_000.0)
    x = exit_px * (1.0 + slip_bps / 10_000.0)
    gross = (e - x) * size
    fees = (fee_bps / 10_000.0) * (entry_px * size + exit_px * size)
    return gross - fees


def _sentiment_for_backtest(mode: str | None) -> str:
    m = (mode or config.BACKTEST_SENTIMENT_MODE or "neutral").strip().lower()
    if m not in _VALID_SENTIMENTS:
        logger.warning("Invalid BACKTEST_SENTIMENT_MODE %r; using neutral", m)
        return "neutral"
    return m


def _safe_float(x: Any) -> float | None:
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _signal_branch_legacy(regime: str) -> str:
    if regime == "range":
        return "mean_reversion"
    if regime == "trend" and config.TREND_STRATEGY_ENABLED:
        return "trend"
    return "none"


def _signal_strength_allows(regime: str, signal: str, last: Any) -> tuple[bool, str | None]:
    if signal not in ("buy", "sell"):
        return True, None
    rsi = _safe_float(last.get("rsi"))
    if rsi is None:
        return False, "missing_rsi"
    if signal == "buy":
        dist = float(config.SIGNAL_RSI_OVERSOLD) - rsi
    else:
        dist = rsi - float(config.SIGNAL_RSI_OVERBOUGHT)
    if dist < float(config.SIGNAL_MIN_RSI_DISTANCE):
        return False, "rsi_distance_too_small"
    adx = _safe_float(last.get("adx"))
    if adx is None:
        return True, None
    if regime == "range":
        max_adx = float(config.ADX_MAX_FOR_MEAN_REVERSION) - float(config.SIGNAL_MIN_ADX_MARGIN)
        if adx > max_adx:
            return False, "adx_too_high_for_range"
    elif regime == "trend":
        min_adx = float(config.ADX_MIN_FOR_TREND) + float(config.SIGNAL_MIN_ADX_MARGIN)
        if adx < min_adx:
            return False, "adx_too_low_for_trend"
    return True, None


def _risk_adjusted_metrics(equity_curve: list[float]) -> dict[str, Any]:
    if len(equity_curve) < 3:
        return {
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "returns_observations": 0,
            "published": False,
        }
    returns: list[float] = []
    for i in range(1, len(equity_curve)):
        prev = float(equity_curve[i - 1])
        cur = float(equity_curve[i])
        if prev <= 0:
            continue
        returns.append((cur / prev) - 1.0)
    obs = len(returns)
    min_obs = max(2, int(config.BACKTEST_MIN_RISK_METRIC_OBS))
    if obs < min_obs:
        return {
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "returns_observations": obs,
            "published": False,
        }
    n = float(len(returns))
    mean_r = sum(returns) / n
    var = sum((r - mean_r) ** 2 for r in returns) / max(1.0, n - 1.0)
    std = math.sqrt(var)
    downside = [min(0.0, r) for r in returns]
    dvar = sum((r**2) for r in downside) / n
    dstd = math.sqrt(dvar)
    scale = math.sqrt(n)
    sharpe = (mean_r / std) * scale if std > 0 else None
    sortino = (mean_r / dstd) * scale if dstd > 0 else None
    return {
        "sharpe_ratio": round(float(sharpe), 6) if sharpe is not None else None,
        "sortino_ratio": round(float(sortino), 6) if sortino is not None else None,
        "returns_observations": obs,
        "published": True,
    }


def _fundamentals_ok_for_backtest(symbol: str, signal: str, mode: str | None) -> bool:
    m = (mode or config.BACKTEST_FUNDAMENTALS_MODE or "always_pass").strip().lower()
    if m in ("always_pass", "always_true", ""):
        return True
    if m in ("never_pass", "blocked"):
        return False
    if m in ("match_engine", "match_engine_disabled"):
        snap = None
        if not config.USE_FUNDAMENTALS_FILTER:
            return allows_fundamentals(symbol, signal, snap)
        return allows_fundamentals(symbol, signal, snap)
    logger.warning("Unknown BACKTEST_FUNDAMENTALS_MODE %r; using always_pass", m)
    return True


def _buy_and_hold_benchmark(
    dfp: pd.DataFrame,
    train_end: int,
    start_equity: float,
) -> dict[str, Any]:
    """Long spot: deploy all equity at train_end close, exit at last close."""
    if train_end >= len(dfp) or start_equity <= 0:
        return {"total_return_pct": 0.0, "note": "invalid window"}
    c0 = float(dfp.iloc[train_end]["close"])
    c1 = float(dfp.iloc[-1]["close"])
    if c0 <= 0:
        return {"total_return_pct": 0.0, "note": "invalid close"}
    ret_pct = ((c1 / c0) - 1.0) * 100.0
    return {
        "total_return_pct": round(ret_pct, 4),
        "entry_close": c0,
        "exit_close": c1,
        "from_bar_index": train_end,
    }


def _exchange_limits_allows(symbol: str, size: float, entry: float) -> tuple[bool, str | None]:
    if not config.BACKTEST_ENFORCE_EXCHANGE_LIMITS:
        return True, None
    if "/" not in symbol:
        return True, None
    market = _EXCHANGE_LIMITS_CACHE.get(symbol)
    if symbol not in _EXCHANGE_LIMITS_CACHE:
        try:
            ex = get_ccxt_exchange()
            ex.load_markets()
            market = ex.market(symbol)
        except Exception:
            market = None
        _EXCHANGE_LIMITS_CACHE[symbol] = market
    if not market:
        return True, None
    precision = (market.get("precision") or {}) if isinstance(market, dict) else {}
    limits = (market.get("limits") or {}) if isinstance(market, dict) else {}
    amount_p = precision.get("amount")
    if amount_p is not None:
        step = 10 ** (-int(amount_p))
        quant = math.floor(size / step) * step
        if quant <= 0:
            return False, "exchange_precision_zero"
    amt_limits = limits.get("amount") or {}
    min_amt = _safe_float(amt_limits.get("min"))
    if min_amt is not None and size < min_amt:
        return False, "exchange_min_amount"
    cost_limits = limits.get("cost") or {}
    min_cost = _safe_float(cost_limits.get("min"))
    if min_cost is not None and (size * entry) < min_cost:
        return False, "exchange_min_notional"
    return True, None


def _exit_realism_model(
    *,
    rng: random.Random,
    side: str,
    exit_px: float,
    size: float,
    allow_delay: bool = True,
) -> tuple[bool, float, float]:
    if not config.BACKTEST_EXIT_REALISM_ENABLED:
        return True, exit_px, size
    delay_cut = max(0.0, float(config.BACKTEST_EXIT_DELAY_PROB_PCT))
    if allow_delay and (rng.random() * 100.0) < delay_cut:
        return False, exit_px, size
    lo = max(0.0, min(1.0, float(config.BACKTEST_EXIT_PARTIAL_FILL_MIN)))
    hi = max(0.0, min(1.0, float(config.BACKTEST_EXIT_PARTIAL_FILL_MAX)))
    if hi < lo:
        lo, hi = hi, lo
    frac = lo if hi == lo else rng.uniform(lo, hi)
    close_size = max(0.0, size * frac)
    slip = max(0.0, float(config.BACKTEST_EXIT_EXTRA_SLIPPAGE_BPS)) / 10_000.0
    px = float(exit_px)
    if side == "long":
        px = px * (1.0 - slip)
    else:
        px = px * (1.0 + slip)
    return True, px, close_size


def run_backtest_on_dataframe(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    sentiment_mode: str | None = None,
    skip_ta_filter: bool | None = None,
    fundamentals_mode: str | None = None,
    fee_bps: float | None = None,
    slippage_bps: float | None = None,
    audit_sample_stride: int | None = None,
    include_buy_hold: bool | None = None,
    regime_model: RegimeModel | None = None,
) -> dict[str, Any]:
    """
    Run simulation on OHLCV columns timestamp, open, high, low, close, volume.
    Policy knobs mirror live where possible; LLM sentiment is not replayed historically.

    - sentiment_mode: neutral | bullish | bearish (default from BACKTEST_SENTIMENT_MODE)
    - skip_ta_filter: if True, ta_ok is always True (ablation)
    - fundamentals_mode: always_pass | never_pass | match_engine (see BACKTEST_FUNDAMENTALS_MODE)
    - Historical fundamentals vetoes are not replayed when USE_FUNDAMENTALS_FILTER=true; see policy_notes.
    - regime_model: if set, use this frozen model for regime predictions (skip in-backtest retrain on prefix).
    """
    if fee_bps is None:
        fee_bps = default_backtest_fee_bps(symbol)
    slip_bps = config.BACKTEST_SLIPPAGE_BPS if slippage_bps is None else slippage_bps
    audit_stride = (
        config.BACKTEST_AUDIT_SAMPLE_STRIDE if audit_sample_stride is None else audit_sample_stride
    )
    do_bh = (
        config.BACKTEST_INCLUDE_BUY_HOLD if include_buy_hold is None else include_buy_hold
    )

    sentiment = _sentiment_for_backtest(sentiment_mode)
    fund_mode = fundamentals_mode if fundamentals_mode is not None else config.BACKTEST_FUNDAMENTALS_MODE
    skip_ta = config.BACKTEST_SKIP_TA_FILTER if skip_ta_filter is None else skip_ta_filter

    policy_notes: list[str] = [
        "sentiment is fixed label for backtest (no historical LLM replay).",
    ]
    if config.BACKTEST_ENFORCE_REGIME_CONFIDENCE:
        policy_notes.append(
            f"BACKTEST_ENFORCE_REGIME_CONFIDENCE=true: hold when regime_confidence < {config.REGIME_MIN_CONFIDENCE}."
        )
    if config.BACKTEST_ENFORCE_SIGNAL_STRENGTH:
        policy_notes.append("BACKTEST_ENFORCE_SIGNAL_STRENGTH=true: engine RSI/ADX quality filter applied.")
    if config.BACKTEST_ENFORCE_LOSS_COOLDOWN:
        policy_notes.append(f"BACKTEST_ENFORCE_LOSS_COOLDOWN=true: LOSS_COOLDOWN_BARS={config.LOSS_COOLDOWN_BARS}.")
    if config.BACKTEST_USE_CIRCUIT_BREAKER:
        policy_notes.append(
            f"BACKTEST_USE_CIRCUIT_BREAKER=true: stop after {config.BACKTEST_CIRCUIT_MAX_CONSECUTIVE_LOSSES} consecutive losses."
        )
    if config.BACKTEST_EXECUTION_REALISM_ENABLED:
        policy_notes.append(
            "BACKTEST_EXECUTION_REALISM_ENABLED=true: probabilistic skip/reject + partial-fill model on entries."
        )
    if config.BACKTEST_ENFORCE_EXCHANGE_LIMITS:
        policy_notes.append(
            "BACKTEST_ENFORCE_EXCHANGE_LIMITS=true: entry checks apply exchange precision/min amount/min notional."
        )
    if config.USE_FUNDAMENTALS_FILTER:
        policy_notes.append(
            "USE_FUNDAMENTALS_FILTER=true: per-bar fundamentals vetoes are not replayed; "
            "use fundamentals_mode=match_engine with allows_fundamentals(..., None) or always_pass."
        )
    if config.TREND_STRATEGY_ENABLED:
        policy_notes.append(
            "TREND_STRATEGY_ENABLED=true: trend regime uses trend_continuation_signal + trend_regime_ta_allows."
        )
    if config.BACKTEST_ENFORCE_MAX_TRADES_PER_DAY:
        policy_notes.append(
            f"BACKTEST_ENFORCE_MAX_TRADES_PER_DAY=true: new entries capped at "
            f"MAX_TRADES_PER_DAY={config.MAX_TRADES_PER_DAY} per UTC day (bar timestamp)."
        )
    if config.BACKTEST_EXIT_DELAY_FILL_NEXT_BAR:
        policy_notes.append(
            "BACKTEST_EXIT_DELAY_FILL_NEXT_BAR=true: delayed exits fill at next bar "
            f"({config.BACKTEST_EXIT_DELAY_FILL_PRICE}) with slippage/partial (no second delay)."
        )

    df = compute_all_indicators(df.copy())
    drop_cols = ["rsi", "ema20", "ema50", "atr"]
    if config.USE_EXTENDED_TA:
        drop_cols.append("adx")
    dfp = df.dropna(subset=drop_cols).reset_index(drop=True)
    n = len(dfp)
    if n < 80:
        raise ValueError(f"Backtest needs >= 80 valid bars after indicators, got {n}")

    train_end = min(config.INITIAL_BARS, n - 15)
    train_end = max(train_end, 55)
    if train_end >= n - 5:
        train_end = n - 10

    if regime_model is not None:
        model = regime_model
        policy_notes.append(
            "regime_model: frozen pretrained RegimeModel (no prefix retrain on dfp[:train_end])."
        )
    else:
        model = RegimeModel()
        model.train(dfp.iloc[:train_end])

    equity = float(config.PAPER_EQUITY_USDT)
    peak = equity
    max_dd_pct = 0.0
    wins = losses = trades = 0
    gross_profit = 0.0
    gross_loss_abs = 0.0
    net_pnl = 0.0
    pos: OpenPosition | None = None
    cooldown_bars_remaining = 0
    consecutive_losses = 0
    circuit_tripped = False
    risk = RiskManager(
        balance=equity,
        risk_per_trade=config.RISK_PER_TRADE,
        max_daily_trades=_BACKTEST_MAX_DAILY,
    )
    daily_open_limiter: _BacktestDailyOpenLimiter | None = None
    if config.BACKTEST_ENFORCE_MAX_TRADES_PER_DAY:
        daily_open_limiter = _BacktestDailyOpenLimiter(config.MAX_TRADES_PER_DAY)

    try:
        _, quote = split_pair(symbol)
        quote_usdt_rate = usdt_per_quote_unit(quote)
    except (QuoteUsdtRateError, ValueError) as e:
        raise RuntimeError(f"backtest: cannot resolve USDT rate for {symbol!r}: {e}") from e

    max_pts = max(20, config.BACKTEST_MAX_EQUITY_POINTS)
    equity_points: list[dict[str, Any]] = []
    stride = max(1, (n - train_end) // max_pts) if n > train_end else 1
    audit_rows: list[dict[str, Any]] = []
    equity_curve: list[float] = [equity]
    exec_stats = {
        "opened": 0,
        "skipped": 0,
        "rejected": 0,
        "partial_fills": 0,
        "limits_blocked": 0,
        "exit_delayed": 0,
        "exit_fill_next_bar": 0,
        "exit_partial_fills": 0,
    }
    rng = random.Random(int(config.BACKTEST_EXECUTION_RANDOM_SEED))
    sim_timestamps: list[int] = []
    deferred_exit: dict[str, Any] | None = None

    def _apply_exit_fill(
        pos: OpenPosition,
        outcome: str,
        xp: float,
        close_size: float,
    ) -> OpenPosition | None:
        nonlocal equity, net_pnl, trades, wins, losses, gross_profit, gross_loss_abs
        nonlocal peak, max_dd_pct, cooldown_bars_remaining, consecutive_losses, circuit_tripped
        if pos.side == "long":
            pnl = _long_pnl_with_costs(
                pos.entry_price, xp, close_size, fee_bps, slip_bps
            )
        else:
            pnl = _short_pnl_with_costs(
                pos.entry_price, xp, close_size, fee_bps, slip_bps
            )
        equity += pnl
        net_pnl += pnl
        risk.balance = max(equity, 1.0)
        trades += 1
        if outcome == "win":
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss_abs += abs(pnl)
            if config.BACKTEST_ENFORCE_LOSS_COOLDOWN:
                cooldown_bars_remaining = max(
                    int(config.LOSS_COOLDOWN_BARS), cooldown_bars_remaining
                )
            if config.BACKTEST_USE_CIRCUIT_BREAKER:
                consecutive_losses += 1
                if consecutive_losses >= int(config.BACKTEST_CIRCUIT_MAX_CONSECUTIVE_LOSSES):
                    circuit_tripped = True
        if outcome == "win":
            consecutive_losses = 0
        if close_size >= float(pos.size):
            return None
        pos.size = max(0.0, float(pos.size) - close_size)
        return pos

    for i in range(train_end, n):
        if circuit_tripped:
            break
        window = dfp.iloc[: i + 1]
        last = window.iloc[-1]
        bar = dfp.iloc[i]
        sim_timestamps.append(int(bar.get("timestamp", 0) or 0))
        if daily_open_limiter is not None:
            daily_open_limiter.on_bar(bar)

        if deferred_exit is not None:
            pos_d = deferred_exit["pos"]
            outcome_d = str(deferred_exit["outcome"])
            fill_bar = bar
            xp_raw = (
                float(fill_bar["close"])
                if config.BACKTEST_EXIT_DELAY_FILL_PRICE == "close"
                else float(fill_bar["open"])
            )
            close_now, xp, close_size = _exit_realism_model(
                rng=rng,
                side=pos_d.side,
                exit_px=xp_raw,
                size=float(pos_d.size),
                allow_delay=False,
            )
            deferred_exit = None
            exec_stats["exit_fill_next_bar"] += 1
            if not close_now or close_size <= 0:
                pos = pos_d
                if (i - train_end) % stride == 0 and len(equity_points) < max_pts:
                    equity_points.append(
                        {
                            "i": i,
                            "equity": round(equity, 4),
                            "ts": int(bar.get("timestamp", 0) or 0),
                        }
                    )
                equity_curve.append(equity)
                continue
            if close_size < float(pos_d.size):
                exec_stats["exit_partial_fills"] += 1
            pos = _apply_exit_fill(pos_d, outcome_d, xp, close_size)
            peak = max(peak, equity)
            if peak > 0:
                max_dd_pct = max(max_dd_pct, (peak - equity) / peak)

        if pos is not None:
            exited, outcome = pos.check_bar(bar)
            if exited and outcome:
                xp = _backtest_exit_fill_price(pos, bar, str(outcome))
                close_now, xp, close_size = _exit_realism_model(
                    rng=rng,
                    side=pos.side,
                    exit_px=xp,
                    size=float(pos.size),
                )
                if not close_now:
                    exec_stats["exit_delayed"] += 1
                    if (
                        config.BACKTEST_EXIT_DELAY_FILL_NEXT_BAR
                        and config.BACKTEST_EXIT_REALISM_ENABLED
                    ):
                        deferred_exit = {"outcome": outcome, "pos": pos}
                    continue
                if close_size <= 0:
                    exec_stats["exit_delayed"] += 1
                    continue
                if close_size < float(pos.size):
                    exec_stats["exit_partial_fills"] += 1
                pos = _apply_exit_fill(pos, str(outcome), xp, close_size)
                peak = max(peak, equity)
                if peak > 0:
                    max_dd_pct = max(max_dd_pct, (peak - equity) / peak)
            else:
                if (i - train_end) % stride == 0 and len(equity_points) < max_pts:
                    equity_points.append(
                        {
                            "i": i,
                            "equity": round(equity, 4),
                            "ts": int(bar.get("timestamp", 0) or 0),
                        }
                    )
                continue

        regime, regime_confidence = model.predict_with_confidence(window)
        smc_audit: dict[str, Any] = {}
        engine = str(getattr(config, "STRATEGY_ENGINE", "legacy") or "legacy").lower()
        use_smc = engine == "smc"
        if use_smc:
            from tr_bot.strategy.market_context import empty_context
            from tr_bot.strategy.signal_pipeline import analyze_symbol, proposal_to_decision

            ctx = empty_context(
                ml_regime=regime,
                regime_confidence=float(regime_confidence),
            )
            # Single-TF backtest: map window to all configured TFs for SMC pipeline.
            dfs = {
                str(getattr(config, "SMC_HTF_TIMEFRAME", "4h")): window,
                str(getattr(config, "SMC_PRIMARY_TIMEFRAME", "1h")): window,
                str(getattr(config, "SMC_ENTRY_TIMEFRAME", "15m")): window,
                str(getattr(config, "SMC_EXEC_TIMEFRAME", timeframe)): window,
                str(timeframe): window,
            }
            proposal = analyze_symbol(symbol, dfs_by_tf=dfs, context=ctx)
            decision = proposal_to_decision(proposal)
            signal = proposal.direction if proposal else "hold"
            ta_ok = True
            fundamentals_ok = True
            if decision in ("buy", "sell"):
                fundamentals_ok = _fundamentals_ok_for_backtest(symbol, decision, fund_mode)
                if not fundamentals_ok:
                    decision = "hold"
                    if proposal is not None and not proposal.blocked_gate:
                        proposal.blocked_gate = "fundamentals_block"
            smc_audit = {
                "score": proposal.score if proposal else None,
                "score_components": proposal.score_components if proposal else None,
                "pipeline_steps": proposal.pipeline_steps if proposal else None,
                "blocked_gate": proposal.blocked_gate if proposal else None,
                "smc_factors": proposal.smc_factors if proposal else None,
            }
            pending_smc_levels = (
                {
                    "stop": proposal.stop,
                    "target": proposal.target,
                    "score": proposal.score,
                    "score_components": proposal.score_components,
                    "exit_mode": str(getattr(config, "EXIT_MODE", "atr")),
                }
                if proposal and proposal.is_actionable
                else None
            )
        else:
            pending_smc_levels = None
            if regime == "range":
                signal = mean_reversion_signal(window)
                ta_ok = True if skip_ta else mean_reversion_ta_allows(last)
            elif regime == "trend" and config.TREND_STRATEGY_ENABLED:
                signal = trend_continuation_signal(window)
                ta_ok = True if skip_ta else trend_regime_ta_allows(last)
            else:
                signal = "hold"
                ta_ok = True
            fundamentals_ok = _fundamentals_ok_for_backtest(symbol, signal, fund_mode)
            decision = decision_engine(
                regime,
                signal,
                sentiment,
                ta_ok=ta_ok,
                fundamentals_ok=fundamentals_ok,
                trend_strategy_enabled=config.TREND_STRATEGY_ENABLED,
            )
        if config.BACKTEST_ENFORCE_REGIME_CONFIDENCE and regime_confidence < float(
            config.REGIME_MIN_CONFIDENCE
        ):
            decision = "hold"
        if (not use_smc) and config.BACKTEST_ENFORCE_SIGNAL_STRENGTH and decision != "hold":
            strength_ok, _ = _signal_strength_allows(regime, signal, last)
            if not strength_ok:
                decision = "hold"
        if config.BACKTEST_ENFORCE_LOSS_COOLDOWN:
            if cooldown_bars_remaining > 0:
                cooldown_bars_remaining = max(0, cooldown_bars_remaining - 1)
                if decision != "hold":
                    decision = "hold"

        if audit_stride > 0 and (i - train_end) % audit_stride == 0:
            row_aud: dict[str, Any] = {
                "i": i,
                "ts": int(bar.get("timestamp", 0) or 0),
                "regime": regime,
                "signal": signal,
                "sentiment": sentiment,
                "ta_ok": ta_ok,
                "fundamentals_ok": fundamentals_ok,
                "decision": decision,
                "regime_confidence": regime_confidence,
                "cooldown_bars_remaining": cooldown_bars_remaining,
                "circuit_tripped": circuit_tripped,
                "strategy_engine": engine,
            }
            if smc_audit:
                row_aud["smc"] = smc_audit
            audit_rows.append(row_aud)

        if decision in ("buy", "sell"):
            if daily_open_limiter is not None and not daily_open_limiter.can_open():
                pass
            else:
                atr = float(last["atr"])
                if atr > 0:
                    close_bt = float(last["close"])
                    if not atr_meets_min_pct(atr, close_bt):
                        continue
                    side_bt = "long" if decision == "buy" else "short"
                    exit_mode_bt = str(getattr(config, "EXIT_MODE", "atr") or "atr")
                    exit_policy_bt = str(
                        getattr(config, "BACKTEST_EXIT_POLICY", getattr(config, "EXIT_POLICY", "software_polled"))
                    )
                    struct_stop = pending_smc_levels.get("stop") if pending_smc_levels else None
                    struct_target = pending_smc_levels.get("target") if pending_smc_levels else None
                    try:
                        plan_bt = build_exit_plan(
                            side=side_bt,  # type: ignore[arg-type]
                            entry_price=float(last["close"]),
                            atr=atr,
                            exit_mode=exit_mode_bt,  # type: ignore[arg-type]
                            exit_policy=exit_policy_bt,  # type: ignore[arg-type]
                            structural_stop=float(struct_stop) if struct_stop is not None else None,
                            structural_target=float(struct_target) if struct_target is not None else None,
                        )
                    except ValueError:
                        continue
                    stop_distance_usdt = plan_bt.stop_distance_usdt(quote_usdt_rate)
                    size = risk.position_size(stop_distance_usdt, weight=1.0)
                    size = apply_backtest_notional_cap(size, close_bt, quote_usdt_rate)
                    if size > 0:
                        fill_frac = 1.0
                        if config.BACKTEST_EXECUTION_REALISM_ENABLED:
                            r = rng.random() * 100.0
                            skip_cut = max(0.0, float(config.BACKTEST_EXECUTION_SKIP_PCT))
                            rej_cut = skip_cut + max(0.0, float(config.BACKTEST_EXECUTION_REJECT_PCT))
                            if r < skip_cut:
                                exec_stats["skipped"] += 1
                                continue
                            if r < rej_cut:
                                exec_stats["rejected"] += 1
                                continue
                            lo = max(0.0, min(1.0, float(config.BACKTEST_EXECUTION_PARTIAL_FILL_MIN)))
                            hi = max(0.0, min(1.0, float(config.BACKTEST_EXECUTION_PARTIAL_FILL_MAX)))
                            if hi < lo:
                                lo, hi = hi, lo
                            fill_frac = lo if hi == lo else rng.uniform(lo, hi)
                            if fill_frac <= 0.0:
                                exec_stats["skipped"] += 1
                                continue
                            if fill_frac < 0.999999:
                                exec_stats["partial_fills"] += 1
                        size = size * fill_frac
                        if size <= 0:
                            exec_stats["skipped"] += 1
                            continue
                        side = "long" if decision == "buy" else "short"
                        entry = float(last["close"])
                        limits_ok, _limits_reason = _exchange_limits_allows(symbol, size, entry)
                        if not limits_ok:
                            exec_stats["limits_blocked"] += 1
                            continue
                        pos_kw: dict[str, Any] = {
                            "symbol": symbol,
                            "side": side,
                            "entry_price": entry,
                            "atr": atr,
                            "size": size,
                            "signal_branch": "smc" if use_smc else _signal_branch_legacy(regime),
                            "regime_at_entry": regime,
                            "regime_confidence_at_entry": float(regime_confidence),
                        }
                        if pending_smc_levels:
                            pos_kw["exit_mode"] = pending_smc_levels.get("exit_mode") or "atr"
                            pos_kw["structural_stop"] = pending_smc_levels.get("stop")
                            pos_kw["structural_target"] = pending_smc_levels.get("target")
                            pos_kw["trade_score"] = pending_smc_levels.get("score")
                            pos_kw["score_components"] = pending_smc_levels.get("score_components")
                        pos_kw["exit_policy"] = exit_policy_bt
                        pos_kw["exit_plan_dict"] = plan_bt.to_dict()
                        pos_kw["intended_risk_usdt"] = float(stop_distance_usdt) * float(size) / float(
                            quote_usdt_rate
                        )
                        pos = OpenPosition(**pos_kw)
                        exec_stats["opened"] += 1
                        if daily_open_limiter is not None:
                            daily_open_limiter.record_open()

        if (i - train_end) % stride == 0 and len(equity_points) < max_pts:
            equity_points.append(
                {
                    "i": i,
                    "equity": round(equity, 4),
                    "ts": int(bar.get("timestamp", 0) or 0),
                }
            )

        equity_curve.append(equity)

    if deferred_exit is not None:
        pos_d = deferred_exit["pos"]
        outcome_d = str(deferred_exit["outcome"])
        fill_bar = dfp.iloc[-1]
        xp_raw = float(fill_bar["close"])
        close_now, xp, close_size = _exit_realism_model(
            rng=rng,
            side=pos_d.side,
            exit_px=xp_raw,
            size=float(pos_d.size),
            allow_delay=False,
        )
        deferred_exit = None
        exec_stats["exit_fill_next_bar"] += 1
        if close_now and close_size > 0:
            if close_size < float(pos_d.size):
                exec_stats["exit_partial_fills"] += 1
            pos = _apply_exit_fill(pos_d, outcome_d, xp, close_size)
            peak = max(peak, equity)
            if peak > 0:
                max_dd_pct = max(max_dd_pct, (peak - equity) / peak)
        else:
            pos = pos_d

    flattened = False
    if pos is not None:
        last_bar = dfp.iloc[-1]
        exit_px = float(last_bar["close"])
        if pos.side == "long":
            pnl = _long_pnl_with_costs(
                pos.entry_price, exit_px, pos.size, fee_bps, slip_bps
            )
        else:
            pnl = _short_pnl_with_costs(
                pos.entry_price, exit_px, pos.size, fee_bps, slip_bps
            )
        equity += pnl
        net_pnl += pnl
        trades += 1
        if pnl >= 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss_abs += abs(pnl)
        flattened = True
        peak = max(peak, equity)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, (peak - equity) / peak)
        equity_curve.append(equity)

    start_eq = float(config.PAPER_EQUITY_USDT)
    total_return_pct = ((equity - start_eq) / start_eq) * 100.0 if start_eq > 0 else 0.0
    if gross_loss_abs > 0:
        profit_factor = gross_profit / gross_loss_abs
    elif trades > 0:
        profit_factor = float(PROFIT_FACTOR_NO_LOSS_CAP) if gross_profit > 0 else 0.0
    else:
        profit_factor = None
    expectancy_per_trade = (net_pnl / trades) if trades > 0 else 0.0
    risk_adj = _risk_adjusted_metrics(equity_curve)
    steps_per_year, annual_src, annual_extra = _steps_per_year_from_timestamps(
        sim_timestamps, timeframe
    )
    annual_factor = math.sqrt(max(1.0, steps_per_year))
    ann_sharpe = (
        round(float(risk_adj["sharpe_ratio"]) * annual_factor, 6)
        if risk_adj["sharpe_ratio"] is not None and risk_adj["published"]
        else None
    )
    ann_sortino = (
        round(float(risk_adj["sortino_ratio"]) * annual_factor, 6)
        if risk_adj["sortino_ratio"] is not None and risk_adj["published"]
        else None
    )

    out: dict[str, Any] = {
        "ok": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_engine": str(getattr(config, "STRATEGY_ENGINE", "legacy") or "legacy"),
        "bars_total": n,
        "bars_simulated": n - train_end,
        "trained_on_rows": train_end,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "final_equity": round(equity, 4),
        "net_pnl": round(net_pnl, 6),
        "gross_profit": round(gross_profit, 6),
        "gross_loss_abs": round(gross_loss_abs, 6),
        "profit_factor": round(float(profit_factor), 6) if profit_factor is not None else None,
        "expectancy_per_trade": round(expectancy_per_trade, 6),
        "sharpe_ratio": risk_adj["sharpe_ratio"],
        "sortino_ratio": risk_adj["sortino_ratio"],
        "sharpe_ratio_annualized": ann_sharpe,
        "sortino_ratio_annualized": ann_sortino,
        "risk_metric_meta": {
            "returns_observations": risk_adj["returns_observations"],
            "min_observations_required": max(2, int(config.BACKTEST_MIN_RISK_METRIC_OBS)),
            "published": bool(risk_adj["published"]),
            "scaling": "sqrt(N) over simulated per-step equity returns",
            "annualization": {
                "enabled": bool(risk_adj["published"]),
                "steps_per_year_assumption": round(float(steps_per_year), 6),
                "source": annual_src,
                **annual_extra,
            },
        },
        "total_return_pct": round(total_return_pct, 4),
        "max_drawdown_pct": round(max_dd_pct * 100.0, 4),
        "equity_points": equity_points[-max_pts:],
        "flattened_at_end": flattened,
        "circuit_tripped": circuit_tripped,
        "policy": {
            "sentiment_mode": sentiment,
            "fundamentals_mode": fund_mode,
            "skip_ta_filter": skip_ta,
            "fee_bps": fee_bps,
            "slippage_bps": slip_bps,
            "notes": policy_notes,
            "execution_realism_enabled": bool(config.BACKTEST_EXECUTION_REALISM_ENABLED),
            "execution_random_seed": int(config.BACKTEST_EXECUTION_RANDOM_SEED),
        },
        "execution_stats": exec_stats,
    }
    if audit_rows:
        out["audit_sample"] = audit_rows
    if do_bh:
        out["benchmark_buy_hold"] = _buy_and_hold_benchmark(dfp, train_end, start_eq)
    return out


def run_backtest_ablation_matrix(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
) -> dict[str, Any]:
    """Run a small grid: sentiment x TA filter (same df)."""
    grid: dict[str, Any] = {}
    for sent in ("neutral", "bullish", "bearish"):
        for skip_ta in (False, True):
            key = f"{sent}_ta_{'skip' if skip_ta else 'on'}"
            grid[key] = run_backtest_on_dataframe(
                df,
                symbol=symbol,
                timeframe=timeframe,
                sentiment_mode=sent,
                skip_ta_filter=skip_ta,
                include_buy_hold=False,
                audit_sample_stride=0,
            )
    return {"ok": True, "symbol": symbol, "timeframe": timeframe, "ablations": grid}


def run_backtest_job() -> dict[str, Any]:
    """Fetch live SYMBOL/TIMEFRAME history and run simulation."""
    sym = config.SYMBOL
    tf = config.TIMEFRAME
    limit = max(200, config.BACKTEST_BARS)
    try:
        raw = fetch_ohlcv_deep(sym, tf, limit=limit)
        if raw.empty or len(raw) < 50:
            raise RuntimeError("Not enough OHLCV for backtest")
        if config.BACKTEST_RUN_ABLATION_MATRIX:
            result = run_backtest_ablation_matrix(raw, symbol=sym, timeframe=tf)
            notify(
                notify_events.BACKTEST_SUCCESS,
                symbol=sym,
                timeframe=tf,
                ablations=len(result.get("ablations") or {}),
            )
        else:
            result = run_backtest_on_dataframe(raw, symbol=sym, timeframe=tf)
            notify(
                notify_events.BACKTEST_SUCCESS,
                **{k: v for k, v in result.items() if k not in ("equity_points", "audit_sample")},
            )
        write_backtest_last_run(result)
        logger.info(
            "Backtest done: ok=%s",
            result.get("ok"),
        )
        return result
    except Exception as e:
        err = str(e)[:800]
        notify(notify_events.BACKTEST_FAILED, error=err)
        write_backtest_last_run({"ok": False, "error": err, "symbol": sym, "timeframe": tf})
        logger.exception("Backtest failed: %s", e)
        raise


_COMPARE_METRIC_KEYS = (
    "trades",
    "wins",
    "losses",
    "total_return_pct",
    "max_drawdown_pct",
    "profit_factor",
    "sharpe_ratio",
    "sortino_ratio",
    "final_equity",
    "net_pnl",
)


def _summary_metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {k: result.get(k) for k in _COMPARE_METRIC_KEYS}


def run_backtest_compare_engines(
    df: pd.DataFrame | None = None,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    """
    Run legacy and smc engines on identical OHLCV; write compare + go/no-go artifacts.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path
    from unittest.mock import patch

    sym = symbol or config.SYMBOL
    tf = timeframe or config.TIMEFRAME
    if df is None:
        limit = max(200, config.BACKTEST_BARS)
        df = fetch_ohlcv_deep(sym, tf, limit=limit)
    if df is None or df.empty or len(df) < 50:
        return {"ok": False, "error": "Not enough OHLCV for compare"}

    with patch.object(config, "STRATEGY_ENGINE", "legacy"):
        legacy = run_backtest_on_dataframe(df, symbol=sym, timeframe=tf)
    with patch.object(config, "STRATEGY_ENGINE", "smc"):
        smc = run_backtest_on_dataframe(df, symbol=sym, timeframe=tf)

    go = _evaluate_smc_go_no_go(legacy, smc)
    out: dict[str, Any] = {
        "ok": True,
        "symbol": sym,
        "timeframe": tf,
        "utc": datetime.now(timezone.utc).isoformat(),
        "legacy": _summary_metrics(legacy),
        "smc": _summary_metrics(smc),
        "go_no_go": go,
        "legacy_ok": bool(legacy.get("ok")),
        "smc_ok": bool(smc.get("ok")),
    }
    if write_artifacts:
        root = Path(config.ARTIFACTS_DIR).resolve()
        (root / "training").mkdir(parents=True, exist_ok=True)
        (root / "research").mkdir(parents=True, exist_ok=True)
        compare_path = root / "training" / "backtest_compare.json"
        go_path = root / "research" / "smc_go_no_go.json"
        compare_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        go_path.write_text(json.dumps(go, indent=2, default=str), encoding="utf-8")
        out["compare_path"] = str(compare_path)
        out["go_no_go_path"] = str(go_path)
    return out


def _evaluate_smc_go_no_go(
    legacy: dict[str, Any],
    smc: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    passed = True
    if not smc.get("ok"):
        return {"passed": False, "reasons": ["smc_backtest_failed"], "override_allowed": True}

    min_pf = getattr(config, "SMC_PROMOTION_MIN_PF", None)
    max_dd = getattr(config, "SMC_PROMOTION_MAX_DD_PCT", None)

    smc_pf = smc.get("profit_factor")
    leg_pf = legacy.get("profit_factor")
    smc_dd = smc.get("max_drawdown_pct")
    leg_ret = legacy.get("total_return_pct")
    smc_ret = smc.get("total_return_pct")
    smc_trades = int(smc.get("trades") or 0)

    if smc_trades < 3:
        passed = False
        reasons.append(f"smc_trades={smc_trades} need>=3")

    if min_pf is not None:
        try:
            if smc_pf is None or float(smc_pf) < float(min_pf):
                passed = False
                reasons.append(f"smc_pf={smc_pf} need>={min_pf}")
        except (TypeError, ValueError):
            passed = False
            reasons.append("smc_pf_invalid")

    if max_dd is not None:
        try:
            if smc_dd is None or float(smc_dd) > float(max_dd):
                passed = False
                reasons.append(f"smc_dd={smc_dd} need<={max_dd}")
        except (TypeError, ValueError):
            passed = False
            reasons.append("smc_dd_invalid")

    # Soft compare: prefer SMC PF >= legacy when both present
    try:
        if smc_pf is not None and leg_pf is not None and float(smc_pf) + 1e-9 < float(leg_pf):
            reasons.append(f"smc_pf_below_legacy smc={smc_pf} legacy={leg_pf}")
            # Not hard-fail unless explicit min_pf was also missed
    except (TypeError, ValueError):
        pass

    if not reasons and passed:
        reasons.append("passed_default_gates")

    return {
        "passed": passed,
        "reasons": reasons,
        "override_allowed": True,
        "legacy_return_pct": leg_ret,
        "smc_return_pct": smc_ret,
        "legacy_pf": leg_pf,
        "smc_pf": smc_pf,
        "smc_trades": smc_trades,
        "smc_max_drawdown_pct": smc_dd,
    }

"""P&L rollups, trade ledger summary, and cost assumptions for the dashboard API."""

from __future__ import annotations

import statistics
from typing import Any

from tr_bot import config
from tr_bot.fees import default_backtest_fee_bps, live_fee_mode_summary
from tr_bot.backtest.dashboard_enrich import enrich_backtest_last_run_for_dashboard
from tr_bot.backtest.last_run import read_backtest_last_run
from tr_bot.ops_state import read_equity_history
from tr_bot.trade_ledger import read_recent_trades


def _max_drawdown_pct_from_navs(navs: list[float]) -> float | None:
    if len(navs) < 2:
        return None
    peak = navs[0]
    max_dd = 0.0
    for n in navs:
        peak = max(peak, n)
        if peak > 0:
            max_dd = max(max_dd, (peak - n) / peak)
    return round(max_dd * 100.0, 4)


def _positive_nav_pairs(points: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    pairs: list[tuple[dict[str, Any], float]] = []
    for p in points:
        try:
            n = float(p.get("nav", 0))
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        pairs.append((p, n))
    return pairs


def _filter_equity_points_for_rollups(
    points: list[dict[str, Any]],
    *,
    band: float = 25.0,
) -> tuple[list[dict[str, Any]], int]:
    """
    Drop NAV samples that sit far outside the median band (or a two-point corrupt low).

    Stale fund/equity writes (e.g. transient NAV ~100 vs true ~10k) otherwise make the
    equity window show absurd return and max drawdown while fund_state is flat.
    """
    pairs = _positive_nav_pairs(points)
    if not pairs:
        return points, 0

    # Exactly two samples: huge ratio with a tiny low vs paper baseline → keep the sane high.
    if len(pairs) == 2 and config.PAPER_TRADING:
        (pa, na), (pb, nb) = pairs[0], pairs[1]
        lo_n, hi_n = min(na, nb), max(na, nb)
        b = float(config.PAPER_EQUITY_USDT)
        if (
            b > 0
            and hi_n >= lo_n * 25
            and lo_n < b * 0.2
            and hi_n >= b * 0.85
            and hi_n <= b * 1.5
        ):
            hi_pt = pa if na == hi_n else pb
            return ([hi_pt], 1)

    if len(pairs) < 3:
        return points, 0
    navs = [n for _, n in pairs]
    med = float(statistics.median(navs))
    if med <= 0:
        return points, 0
    lo, hi = med / band, med * band
    kept = [p for p, n in pairs if lo <= n <= hi]
    dropped = len(pairs) - len(kept)
    if dropped <= 0:
        return points, 0
    if len(kept) < 2:
        return points, 0
    return kept, dropped


def _equity_window(points: list[dict[str, Any]]) -> dict[str, Any]:
    filtered, dropped = _filter_equity_points_for_rollups(points)
    note: str | None = None
    if dropped > 0:
        note = (
            f"Excluded {dropped} NAV sample(s) outside median ÷25…×25 band "
            "(likely stale equity history; delete artifacts/equity_history.json to reset)."
        )

    navs: list[float] = []
    for p in filtered:
        try:
            navs.append(float(p.get("nav", 0)))
        except (TypeError, ValueError):
            continue
    if len(navs) < 1:
        return {
            "label": "Equity history window",
            "ok": False,
            "note": "No NAV points",
        }
    first_p = filtered[0]
    last_p = filtered[-1]
    first = navs[0]
    last = navs[-1]
    delta = last - first
    pct = ((last / first) - 1.0) * 100.0 if first != 0 else None
    out: dict[str, Any] = {
        "label": "Equity history window",
        "ok": True,
        "first_nav": first,
        "last_nav": last,
        "first_t": first_p.get("t"),
        "last_t": last_p.get("t"),
        "delta_usdt": round(delta, 6),
        "return_pct": round(pct, 4) if pct is not None else None,
        "points": len(navs),
        "max_drawdown_pct": _max_drawdown_pct_from_navs(navs),
    }
    if dropped > 0:
        out["outliers_dropped"] = dropped
        out["filter_note"] = note
    return out


def _vs_initial() -> dict[str, Any]:
    last_nav: float | None = None
    mode = "paper_config"
    if config.MANAGERS_ENABLED:
        try:
            from tr_bot.managers.fund import FundManager

            fund = FundManager()
            st = fund.get_state()
            if st:
                last_nav = float(st.last_nav)
                mode = "fund_state"
        except Exception:
            pass
    if last_nav is None:
        eq = read_equity_history().get("points") or []
        if eq:
            try:
                last_nav = float(eq[-1].get("nav", 0))
                mode = "equity_history_last"
            except (TypeError, ValueError, IndexError):
                pass
    initial = float(config.PAPER_EQUITY_USDT)
    if last_nav is None:
        return {
            "label": "vs initial equity",
            "ok": False,
            "mode": mode,
            "initial_usdt": initial,
            "note": "No NAV available (run bot with shared ARTIFACTS_DIR)",
        }
    delta = last_nav - initial
    pct = ((last_nav / initial) - 1.0) * 100.0 if initial != 0 else None
    return {
        "label": "vs initial (paper baseline)",
        "ok": True,
        "mode": mode,
        "initial_usdt": initial,
        "last_nav_usdt": round(last_nav, 6),
        "delta_usdt": round(delta, 6),
        "return_pct": round(pct, 4) if pct is not None else None,
    }


def _trade_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "net_positive_pct": None,
            "tp_stop_win_rate_pct": None,
            "net_pnl_win_rate_pct": None,
            "sum_net_pnl_usdt": 0.0,
            "sum_fee_usdt": 0.0,
        }
    wins = losses = 0
    net_sum = fee_sum = 0.0
    net_positive = 0
    for t in trades:
        oc = str(t.get("outcome", "")).lower()
        if oc == "win":
            wins += 1
        elif oc == "loss":
            losses += 1
        try:
            net = float(t.get("net_pnl_usdt", 0) or 0)
            net_sum += net
            if net > 0:
                net_positive += 1
        except (TypeError, ValueError):
            pass
        try:
            fee_sum += float(t.get("fee_usdt", 0) or 0)
        except (TypeError, ValueError):
            pass
    n = wins + losses
    wr = (wins / n * 100.0) if n else None
    nclosed = len(trades)
    np_pct = (net_positive / nclosed * 100.0) if nclosed else None
    return {
        "closed_trades": nclosed,
        "wins": wins,
        "losses": losses,
        # TP/stop outcome (OpenPosition.check_bar), not net P&L after fees
        "win_rate_pct": round(wr, 2) if wr is not None else None,
        "tp_stop_win_rate_pct": round(wr, 2) if wr is not None else None,
        "net_positive_pct": round(np_pct, 2) if np_pct is not None else None,
        "net_pnl_win_rate_pct": round(np_pct, 2) if np_pct is not None else None,
        "sum_net_pnl_usdt": round(net_sum, 6),
        "sum_fee_usdt": round(fee_sum, 6),
    }


def build_equity_history_display_payload() -> dict[str, Any]:
    """
    NAV samples for metrics charts: same outlier filter as P&L equity window rollups.

    Raw persisted points stay in ``read_equity_history()`` / ``equity_history.json``;
    this payload is what the metrics dashboard should chart so corrupt lows do not
    dominate last NAV and drawdown %.
    """
    raw_points = list(read_equity_history().get("points") or [])
    filtered, dropped = _filter_equity_points_for_rollups(raw_points)
    display = filtered if filtered else raw_points
    return {
        "points": display,
        "raw_point_count": len(raw_points),
        "display_point_count": len(display),
        "outliers_dropped": dropped,
    }


def build_pnl_dashboard_payload() -> dict[str, Any]:
    eq = read_equity_history()
    points = list(eq.get("points") or [])
    equity_window = _equity_window(points)
    vs_initial = _vs_initial()

    raw_bt = read_backtest_last_run()
    enriched = enrich_backtest_last_run_for_dashboard(raw_bt) if raw_bt else None
    backtest_row: dict[str, Any] = {
        "label": "Last backtest (historical sim)",
        "ok": enriched is not None and enriched.get("ok") is not False,
    }
    if enriched:
        rm = enriched.get("risk_metric_meta") if isinstance(enriched, dict) else None
        compare_guardrails: list[str] = []
        if isinstance(rm, dict):
            if not bool(rm.get("published")):
                compare_guardrails.append(
                    "Sharpe/Sortino hidden: insufficient observations for reliable comparison."
                )
            scaling = rm.get("scaling")
            if scaling:
                compare_guardrails.append(
                    f"Compare Sharpe/Sortino only across runs with same scaling ({scaling})."
                )
            ann = rm.get("annualization") if isinstance(rm.get("annualization"), dict) else None
            if isinstance(ann, dict):
                src = str(ann.get("source") or "")
                if src == "timeframe_fallback":
                    note = str(ann.get("fallback_note") or "").strip()
                    compare_guardrails.append(
                        "Annualization used timeframe fallback; compare carefully with irregular data windows."
                        + (f" ({note})" if note else "")
                    )
                elif src == "timestamp_median_regular":
                    note = str(ann.get("fallback_note") or "").strip()
                    if note:
                        compare_guardrails.append(f"Annualization: {note}")
        backtest_row.update(
            {
                "total_return_pct": enriched.get("total_return_pct"),
                "max_drawdown_pct": enriched.get("max_drawdown_pct"),
                "sharpe_ratio": enriched.get("sharpe_ratio"),
                "sortino_ratio": enriched.get("sortino_ratio"),
                "sharpe_ratio_annualized": enriched.get("sharpe_ratio_annualized"),
                "sortino_ratio_annualized": enriched.get("sortino_ratio_annualized"),
                "risk_metric_meta": rm,
                "comparison_guardrails": compare_guardrails,
                "trades": enriched.get("trades"),
                "wins": enriched.get("wins"),
                "losses": enriched.get("losses"),
                "symbol": enriched.get("symbol"),
                "timeframe": enriched.get("timeframe"),
            }
        )

    recent = read_recent_trades(200)
    summary = _trade_summary(recent)

    disclaimer = (
        "Illustrative metrics: ledger uses bar-close fill estimates and configured fee/slippage bps; "
        "backtest uses BACKTEST_* costs. Not financial advice."
    )

    return {
        "equity_window": equity_window,
        "vs_initial": vs_initial,
        "effective_tier_overrides": config.effective_tier_overrides(),
        "backtest": backtest_row,
        "recent_trades": recent,
        "trade_summary": summary,
        "cost_assumptions": {
            "live_fee_bps": config.LIVE_FEE_BPS,
            "live_slippage_bps": config.LIVE_SLIPPAGE_BPS,
            "backtest_fee_bps": config.BACKTEST_FEE_BPS,
            "backtest_taker_bps": float(config.BACKTEST_TAKER_BPS),
            "backtest_fee_effective_bps": default_backtest_fee_bps(config.SYMBOL),
            "backtest_slippage_bps": config.BACKTEST_SLIPPAGE_BPS,
            "paper_trading": config.PAPER_TRADING,
            "live_fee": live_fee_mode_summary(),
            "execution_style": config.EXECUTION_STYLE,
            "twap_duration_seconds": config.TWAP_DURATION_SECONDS,
        },
        "disclaimer": disclaimer,
    }

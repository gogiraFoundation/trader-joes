"""P&L rollups, trade ledger summary, and cost assumptions for the dashboard API."""

from __future__ import annotations

import statistics
from typing import Any

from tr_bot import config
from tr_bot.fees import default_backtest_fee_bps, live_fee_mode_summary
from tr_bot.backtest.dashboard_enrich import enrich_backtest_last_run_for_dashboard
from tr_bot.backtest.last_run import read_backtest_last_run
from tr_bot.ops_state import read_equity_history
from tr_bot.trade_ledger import closed_trade_stats, read_recent_trades, summarize_trades


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
    return summarize_trades(trades, scope="recent_subset")


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


def _parse_iso_utc(value: Any) -> float | None:
    """Return epoch seconds for an ISO timestamp, or None if unparseable."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        # Accept both Z and +00:00; treat naive as UTC.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def build_drawdown_history_payload(
    *,
    points: list[dict[str, Any]] | None = None,
    peak_anchor: float | None = None,
    last_nav: float | None = None,
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    """
    Fund-peak-anchored drawdown series for Metrics charts.

    Uses existing equity timestamps (no synthetic peak NAV point). Running peak
    starts at ``max(peak_anchor, first_nav)`` so chart drawdown matches fund state
    even when equity_history never recorded the historical peak. Appends the latest
    fund NAV only when its timestamp is newer than the last equity sample.
    """
    raw_points = list(points if points is not None else (read_equity_history().get("points") or []))
    filtered, dropped = _filter_equity_points_for_rollups(raw_points)
    series = filtered if filtered else raw_points

    nav_pairs: list[tuple[str, float, float | None]] = []
    for p in series:
        try:
            nav = float(p.get("nav", 0))
        except (TypeError, ValueError):
            continue
        if nav <= 0:
            continue
        t = str(p.get("t") or "")
        nav_pairs.append((t, nav, _parse_iso_utc(t)))

    # Append fund last_nav if newer than last equity sample (or series empty).
    if last_nav is not None and float(last_nav) > 0 and as_of_utc:
        fund_ts = _parse_iso_utc(as_of_utc)
        last_eq_ts = nav_pairs[-1][2] if nav_pairs else None
        if fund_ts is not None and (last_eq_ts is None or fund_ts > last_eq_ts + 1e-6):
            nav_pairs.append((str(as_of_utc), float(last_nav), fund_ts))

    out_points: list[dict[str, Any]] = []
    peak: float | None = None
    if peak_anchor is not None:
        try:
            pa = float(peak_anchor)
            if pa > 0:
                peak = pa
        except (TypeError, ValueError):
            peak = None

    for t, nav, _ts in nav_pairs:
        if peak is None:
            peak = nav
        else:
            peak = max(peak, nav)
        dd = 0.0 if peak <= 0 else ((peak - nav) / peak) * 100.0
        out_points.append({"t": t, "drawdown_pct": round(dd, 4), "nav": nav, "peak": peak})

    current_dd: float | None = None
    if out_points:
        current_dd = float(out_points[-1]["drawdown_pct"])
    elif peak_anchor is not None and last_nav is not None:
        try:
            pk = float(peak_anchor)
            ln = float(last_nav)
            if pk > 0:
                current_dd = round(max(0.0, (pk - ln) / pk) * 100.0, 4)
        except (TypeError, ValueError):
            current_dd = None

    return {
        "source": "fund_peak_anchored",
        "peak_anchor": float(peak_anchor) if peak_anchor is not None else None,
        "current_drawdown_pct": current_dd,
        "as_of_utc": as_of_utc,
        "points": out_points,
        "outliers_dropped": dropped,
    }


def build_metrics_dashboard_payload(*, fund: dict[str, Any] | None = None) -> dict[str, Any]:
    """Metrics-page extras nested under ``summary.metrics`` (backward-compatible)."""
    fund = fund or {}
    state = fund.get("state") if isinstance(fund.get("state"), dict) else {}
    peak = state.get("peak_nav")
    last = state.get("last_nav")
    as_of = state.get("last_update_utc")
    try:
        peak_f = float(peak) if peak is not None else None
    except (TypeError, ValueError):
        peak_f = None
    try:
        last_f = float(last) if last is not None else None
    except (TypeError, ValueError):
        last_f = None
    dd_hist = build_drawdown_history_payload(
        peak_anchor=peak_f,
        last_nav=last_f,
        as_of_utc=str(as_of) if as_of else None,
    )
    # Prefer fund.state.drawdown_pct when present (authoritative current value).
    if state.get("drawdown_pct") is not None:
        try:
            dd_hist["current_drawdown_pct"] = round(float(state["drawdown_pct"]), 4)
        except (TypeError, ValueError):
            pass
    return {"drawdown_history": dd_hist}


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
    closed_stats = closed_trade_stats(paper=bool(config.PAPER_TRADING))

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
        "closed_trade_stats": closed_stats,
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

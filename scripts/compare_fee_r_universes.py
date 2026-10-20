#!/usr/bin/env python3
"""
Single-symbol 1h walk-forward bake-off for fee/R soak (BTC / ETH / SOL).

Holds cost knobs constant, ranks by profit_factor after fees. Does not edit .env.

Usage (repo root):
  .venv/bin/python scripts/compare_fee_r_universes.py
  .venv/bin/python scripts/compare_fee_r_universes.py --dry-run   # schema only, no network

Writes:
  artifacts/research/fee_r_universe_compare.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
SOAK_MIN_ATR_PCT = 0.0045


def _apply_soak_cost_defaults() -> dict[str, Any]:
    """
    Align config for a fair fee/R compare (process-local; does not write .env).

    Always forces 1h + retail BNB taker path + MIN_ATR_PCT=0.0045 so a leftover
    LIVE_FEE_BPS=10 or MIN_ATR_PCT=0.002 in .env cannot skew the bake-off.
    """
    from tr_bot import config

    applied: dict[str, Any] = {}

    def _set(name: str, value: Any) -> None:
        setattr(config, name, value)
        applied[name] = value

    _set("TIMEFRAME", "1h")
    # Clear flat / explicit overrides that would hide the BNB retail path.
    _set("LIVE_FEE_BPS", 0.0)
    _set("BACKTEST_FEE_BPS", 0.0)
    _set("LIVE_FEE_TAKER_BPS", 0.0)
    _set("BACKTEST_TAKER_BPS", 0.0)
    _set("LIVE_FEE_USE_ALGO_TIERS", False)
    _set("LIVE_FEE_USE_BINANCE_RETAIL_TAKER_DEFAULTS", True)
    _set("BINANCE_PAY_FEES_WITH_BNB", True)
    _set("LIVE_FEE_BNB_TAKER_BPS", 7.5)
    _set("LIVE_FEE_STANDARD_TAKER_BPS", 10.0)
    _set("MIN_ATR_PCT", float(SOAK_MIN_ATR_PCT))
    return applied


def _metric_slice(result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ok",
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
        "fee_bps",
        "slippage_bps",
    )
    return {k: result.get(k) for k in keys}


def run_symbol_backtest(symbol: str, *, timeframe: str = "1h") -> dict[str, Any]:
    from tr_bot import config
    from tr_bot.backtest.runner import run_backtest_on_dataframe
    from tr_bot.fees import default_backtest_fee_bps
    from tr_bot.market_data import fetch_ohlcv_deep

    limit = max(200, int(config.BACKTEST_BARS))
    try:
        raw = fetch_ohlcv_deep(symbol, timeframe, limit=limit)
        if raw is None or getattr(raw, "empty", True) or len(raw) < 50:
            return {
                "ok": False,
                "symbol": symbol,
                "timeframe": timeframe,
                "error": "Not enough OHLCV for backtest",
            }
        result = run_backtest_on_dataframe(raw, symbol=symbol, timeframe=timeframe)
        out = _metric_slice(result if isinstance(result, dict) else {})
        out["ok"] = bool(result.get("ok", True)) if isinstance(result, dict) else True
        out["symbol"] = symbol
        out["timeframe"] = timeframe
        out["fee_bps_resolved"] = default_backtest_fee_bps(symbol)
        out["min_atr_pct"] = float(config.MIN_ATR_PCT)
        return out
    except Exception as e:
        return {
            "ok": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "error": str(e)[:500],
        }


def rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(r: dict[str, Any]) -> tuple[int, float, float]:
        ok = 1 if r.get("ok") else 0
        try:
            pf = float(r.get("profit_factor") or 0.0)
        except (TypeError, ValueError):
            pf = 0.0
        if pf != pf:  # NaN
            pf = 0.0
        try:
            ret = float(r.get("total_return_pct") or 0.0)
        except (TypeError, ValueError):
            ret = 0.0
        return (ok, pf, ret)

    return sorted(rows, key=_key, reverse=True)


def build_compare_payload(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframe: str = "1h",
    applied: dict[str, Any] | None = None,
    run_fn: Any = None,
) -> dict[str, Any]:
    runner = run_fn or run_symbol_backtest
    rows = [runner(sym, timeframe=timeframe) for sym in symbols]
    ranked = rank_rows(rows)
    top = [r for r in ranked if r.get("ok")]
    recommendation: dict[str, Any]
    if not top:
        recommendation = {
            "portfolio_weights": None,
            "note": "No successful backtests; fix data/fees and retry.",
        }
    elif len(top) == 1:
        s = top[0]["symbol"]
        recommendation = {
            "portfolio_weights": {s: 1.0},
            "note": "Single winner; set PORTFOLIO_MODE=true with one symbol or leave equal self-weight.",
        }
    else:
        a, b = top[0]["symbol"], top[1]["symbol"]
        recommendation = {
            "portfolio_weights": {a: 0.5, b: 0.5},
            "note": "Top two by profit_factor (then return). Drop BNB. Apply to PORTFOLIO_WEIGHTS.",
        }
    return {
        "schema_version": 1,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "timeframe": timeframe,
        "symbols": list(symbols),
        "config_applied": applied or {},
        "results": rows,
        "ranked": ranked,
        "recommendation": recommendation,
    }


def write_compare(payload: dict[str, Any], *, artifacts_dir: Path | None = None) -> Path:
    from tr_bot import config
    from tr_bot.managers.security import safe_artifact_path

    root = Path(artifacts_dir or config.ARTIFACTS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    research = safe_artifact_path(root, "research")
    research.mkdir(parents=True, exist_ok=True)
    path = safe_artifact_path(research, "fee_r_universe_compare.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _print_table(ranked: list[dict[str, Any]]) -> None:
    print(
        f"{'rank':<5}{'symbol':<12}{'ok':<6}{'pf':>8}{'ret%':>10}{'dd%':>10}{'trades':>8}  error"
    )
    for i, r in enumerate(ranked, start=1):
        pf = r.get("profit_factor")
        ret = r.get("total_return_pct")
        dd = r.get("max_drawdown_pct")
        trades = r.get("trades")
        err = (r.get("error") or "")[:40]
        print(
            f"{i:<5}{str(r.get('symbol') or ''):<12}{str(bool(r.get('ok'))):<6}"
            f"{(pf if pf is not None else '—')!s:>8}"
            f"{(ret if ret is not None else '—')!s:>10}"
            f"{(dd if dd is not None else '—')!s:>10}"
            f"{(trades if trades is not None else '—')!s:>8}  {err}"
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="1h fee/R universe bake-off (BTC/ETH/SOL). Writes artifacts/research/fee_r_universe_compare.json"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Apply soak config defaults and write a schema stub without fetching OHLCV",
    )
    p.add_argument(
        "--symbols",
        type=str,
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated CCXT pairs (default BTC/USDT,ETH/USDT,SOL/USDT)",
    )
    args = p.parse_args(argv)

    symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
    if not symbols:
        print("error: empty --symbols", file=sys.stderr)
        return 2

    applied = _apply_soak_cost_defaults()
    if args.dry_run:

        def stub(symbol: str, *, timeframe: str = "1h") -> dict[str, Any]:
            return {
                "ok": False,
                "symbol": symbol,
                "timeframe": timeframe,
                "error": "dry_run",
                "trades": None,
                "profit_factor": None,
                "total_return_pct": None,
                "max_drawdown_pct": None,
            }

        payload = build_compare_payload(
            symbols=symbols,
            applied={**applied, "dry_run": True},
            run_fn=stub,
        )
    else:
        payload = build_compare_payload(symbols=symbols, applied=applied)

    path = write_compare(payload)
    _print_table(payload.get("ranked") or [])
    rec = payload.get("recommendation") or {}
    print()
    print("recommendation:", json.dumps(rec, indent=2))
    print("config_applied:", json.dumps(applied, indent=2, default=str))
    print("wrote:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI: fund snapshot, training job, one-shot advisory refresh."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tr_bot import config
from tr_bot.logger import setup_logger
from tr_bot.managers.advise import refresh_advisory
from tr_bot.managers.fund import FundManager
from tr_bot.managers.portfolio import active_symbols
from tr_bot.backtest.runner import run_backtest_compare_engines, run_backtest_job
from tr_bot.managers.training_manager import run_training_job
from tr_bot.promotion.comparator import compare_engines
from tr_bot.promotion.service import (
    strategy_evaluate,
    strategy_promote,
    strategy_revert_to_last_good,
    strategy_status,
)
from tr_bot.reporting.daily import run_daily_report_job
from tr_bot.research.agents import run_agent
from tr_bot.research.query import query_template
from tr_bot.strategy.no_trade_adaptation import run_adaptation_job
from tr_bot.strategy.proposal_apply import apply_llm_proposal_from_path

logger = setup_logger("tr_bot.managers")


def _cmd_fund_snapshot() -> int:
    fund = FundManager()
    out = fund.snapshot_job()
    print(json.dumps(out, indent=2))
    return 0


def _cmd_rebalance_scan() -> int:
    from tr_bot.managers.rebalancer import scan_job

    out = scan_job()
    print(json.dumps(out, indent=2, default=str))
    return 0


def _cmd_rebalance_trim() -> int:
    from tr_bot import config as cfg
    from tr_bot.managers.rebalancer import trim_job

    out = trim_job()
    print(json.dumps(out, indent=2, default=str))
    if out.get("error") == "execute_trims_disabled" or not cfg.REBALANCER_EXECUTE_TRIMS:
        if out.get("error") in ("execute_trims_disabled", "REBALANCER_EXECUTE_TRIMS_false"):
            logger.warning(
                "rebalance-trim: plan printed but REBALANCER_EXECUTE_TRIMS is false; no orders sent"
            )
            return 2
    if out.get("error") and out.get("error") not in (None, "no_action_required"):
        return 1
    return 0


def _cmd_train(args: argparse.Namespace) -> int:
    logging.getLogger("tr_bot.training_manager").setLevel(logging.INFO)
    out = run_training_job(
        output_path=Path(args.output) if args.output else None,
        promote=args.promote,
    )
    if getattr(args, "compact", False):
        rows = out.get("rows", "—")
        sources = out.get("sources") or []
        sources_s = ",".join(str(s) for s in sources) if sources else "—"
        vg = out.get("validation_gates_passed")
        vg_s = "true" if vg is True else "false" if vg is False else "—"
        print(
            "Training OK: "
            f"rows={rows} "
            f"sources={sources_s} "
            f"validation_gates={vg_s} "
            f"artifact={out.get('artifact', '—')}"
        )
        if out.get("version"):
            print(f"Versioned: {out.get('version')}")
        if out.get("promoted_to"):
            print(f"Promoted: {out.get('promoted_to')}")
        reasons = out.get("validation_gate_reasons") or []
        if reasons:
            print("Validation gate reasons: " + "; ".join(str(r) for r in reasons))
    else:
        print(json.dumps(out, indent=2))
    return 0


def _fmt_pct(v: object) -> str:
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _cmd_backtest(args: argparse.Namespace) -> int:
    logging.getLogger("tr_bot.backtest").setLevel(logging.INFO)
    try:
        out = run_backtest_job()
    except Exception:
        return 1
    if getattr(args, "compact", False):
        if out.get("ablations"):
            ab = out.get("ablations") or {}
            print(
                "Backtest OK (ablation matrix): "
                f"symbol={out.get('symbol', config.SYMBOL)} timeframe={out.get('timeframe', config.TIMEFRAME)} "
                f"ablations={len(ab)}"
            )
            for key in sorted(ab.keys()):
                row = ab.get(key) or {}
                print(
                    f"- {key}: return={_fmt_pct(row.get('total_return_pct'))} "
                    f"max_dd={_fmt_pct(row.get('max_drawdown_pct'))} "
                    f"trades={row.get('trades', '—')}"
                )
        else:
            print(
                "Backtest OK: "
                f"symbol={out.get('symbol', config.SYMBOL)} "
                f"timeframe={out.get('timeframe', config.TIMEFRAME)} "
                f"return={_fmt_pct(out.get('total_return_pct'))} "
                f"max_dd={_fmt_pct(out.get('max_drawdown_pct'))} "
                f"trades={out.get('trades', '—')} "
                f"wins={out.get('wins', '—')} "
                f"losses={out.get('losses', '—')} "
                f"bars={out.get('bars_total', '—')}"
            )
            bh = out.get("benchmark_buy_hold")
            if isinstance(bh, dict):
                print(f"BuyHold: return={_fmt_pct(bh.get('total_return_pct'))}")
    else:
        print(json.dumps(out, indent=2, default=str))
    return 0


def _cmd_advise_refresh() -> int:
    if not config.USE_LLM:
        logger.error("USE_LLM must be true")
        return 1
    for sym in active_symbols():
        refresh_advisory(sym)
        logger.info("Refreshed advisory for %s", sym)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m tr_bot.managers")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("fund-snapshot", help="Compute NAV, drawdown, persist fund_state.json")
    sub.add_parser(
        "rebalance-scan",
        help="Phase A1: holdings drift/lag scan → artifacts/rebalancer/latest.json (no orders)",
    )
    sub.add_parser(
        "rebalance-trim",
        help="Phase A2: scan + trim if REBALANCER_EXECUTE_TRIMS=true (else plan only, exit 2)",
    )

    tp = sub.add_parser("train-run", help="Run validated training + optional promote")
    tp.add_argument("--output", type=str, default=None)
    tp.add_argument("--promote", action="store_true", help="Copy to REGIME_MODEL_PATH")
    tp.add_argument(
        "--compact",
        action="store_true",
        help="Print concise summary instead of full JSON output",
    )

    bp = sub.add_parser("backtest-run", help="Walk-forward backtest on SYMBOL/TIMEFRAME")
    bp.add_argument(
        "--compact",
        action="store_true",
        help="Print concise summary instead of full JSON output",
    )

    ar = sub.add_parser(
        "adaptation-run",
        help="No-trade meta-tuning: audit histogram → param delta → isolated backtest gate → profile/proposal",
    )
    ar.add_argument(
        "--compact",
        action="store_true",
        help="Print concise one-line summary instead of full JSON",
    )

    sub.add_parser("advise-refresh", help="Refresh Redis advisory for all active symbols")

    ss = sub.add_parser("strategy-status", help="Deployment pointers, promotion log tail, freeze state")
    ss.add_argument(
        "--compact",
        action="store_true",
        help="One-line summary + pointers JSON",
    )

    se = sub.add_parser("strategy-evaluate", help="Run promotion gates for a profile (default: active)")
    se.add_argument("--profile-id", type=str, default=None)
    se.add_argument("--tier", type=str, default=None)
    se.add_argument("--compact", action="store_true")

    sp = sub.add_parser("strategy-promote", help="Validate gates and set deployment pointer atomically")
    sp.add_argument("--to-tier", type=str, required=True, choices=["paper", "live_canary", "live_full"])
    sp.add_argument("--profile-id", type=str, required=True)
    sp.add_argument("--dry-run", action="store_true")

    sr = sub.add_parser("strategy-revert", help="Restore last successful profile_id from log to a tier pointer")
    sr.add_argument("--to-tier", type=str, required=True, choices=["paper", "live_canary", "live_full"])

    dr = sub.add_parser(
        "daily-report",
        help="Write deterministic JSON + Markdown daily report under artifacts/reports/",
    )
    dr.add_argument(
        "--date",
        type=str,
        default=None,
        help="UTC date YYYY-MM-DD (default: yesterday UTC)",
    )
    dr.add_argument(
        "--preflight",
        action="store_true",
        help="Include startup preflight warnings/errors in infra section",
    )
    dr.add_argument(
        "--compact",
        action="store_true",
        help="Print one-line paths instead of full JSON",
    )

    pa = sub.add_parser(
        "proposal-apply",
        help="Validate LLM proposal JSON, run adaptation-style backtest gate, write proposal or apply paper",
    )
    pa.add_argument("--file", type=str, required=True, help="Path to proposal JSON file")
    pa.add_argument(
        "--compact",
        action="store_true",
        help="Print concise summary instead of full JSON",
    )

    p_bc = sub.add_parser(
        "backtest-compare",
        help="Run legacy vs smc backtests on same bars; write go/no-go artifact",
    )
    p_bc.add_argument("--compact", action="store_true")

    p_sc = sub.add_parser(
        "strategy-compare",
        help="Comparator wrapper (legacy vs smc) for promotion evidence",
    )
    p_sc.add_argument("--compact", action="store_true")

    p_rq = sub.add_parser(
        "research-query",
        help="Run a fixed research query template (losing_smc|high_score_wins|recent)",
    )
    p_rq.add_argument("template", type=str, nargs="?", default="recent")
    p_rq.add_argument("--limit", type=int, default=50)
    p_rq.add_argument("--min-score", type=float, default=80.0)

    p_ra = sub.add_parser(
        "research-agent",
        help="Run offline research agent (performance|structure|smc)",
    )
    p_ra.add_argument("agent", type=str, nargs="?", default="performance")
    p_ra.add_argument("--note", type=str, default="")

    p_ag = sub.add_parser(
        "agents-run",
        help="Run Grok/OpenAI Supervisor + Fund Manager agents (advisory; dry-run by default)",
    )
    p_ag.add_argument(
        "--role",
        type=str,
        default="both",
        choices=["both", "supervisor", "fund_manager"],
        help="Which agent role(s) to run",
    )
    p_ag.add_argument(
        "--dry-run",
        action="store_true",
        help="Force advise-only (no mutating actions)",
    )
    p_ag.add_argument(
        "--compact",
        action="store_true",
        help="Print concise summary instead of full JSON",
    )

    args = p.parse_args()
    if args.cmd == "fund-snapshot":
        sys.exit(_cmd_fund_snapshot())
    if args.cmd == "rebalance-scan":
        sys.exit(_cmd_rebalance_scan())
    if args.cmd == "rebalance-trim":
        sys.exit(_cmd_rebalance_trim())
    if args.cmd == "train-run":
        sys.exit(_cmd_train(args))
    if args.cmd == "backtest-run":
        sys.exit(_cmd_backtest(args))
    if args.cmd == "backtest-compare":
        out = run_backtest_compare_engines()
        if getattr(args, "compact", False):
            go = out.get("go_no_go") or {}
            print(
                "backtest-compare: ok=%s passed=%s path=%s"
                % (out.get("ok"), go.get("passed"), out.get("compare_path"))
            )
        else:
            print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "strategy-compare":
        out = compare_engines()
        if getattr(args, "compact", False):
            go = out.get("go_no_go") or {}
            print("strategy-compare: ok=%s passed=%s" % (out.get("ok"), go.get("passed")))
        else:
            print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "research-query":
        out = query_template(
            args.template,
            limit=args.limit,
            min_score=args.min_score,
        )
        print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "research-agent":
        out = run_agent(args.agent, note=args.note)
        print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "agents-run":
        from tr_bot.agents.runner import run_agents_job

        # --dry-run may force briefs when AGENTS_ENABLED is false; mutating runs never bypass.
        dry = True if args.dry_run else None
        out = run_agents_job(role=args.role, dry_run=dry, force=bool(args.dry_run))
        if getattr(args, "compact", False):
            print(
                "agents-run: ok=%s dry_run=%s skipped=%s roles=%s"
                % (
                    out.get("ok"),
                    out.get("dry_run"),
                    out.get("skipped"),
                    ",".join(r.get("role", "") for r in (out.get("roles") or [])),
                )
            )
        else:
            print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "adaptation-run":
        logging.getLogger("tr_bot.strategy.adaptation").setLevel(logging.INFO)
        out = run_adaptation_job()
        if getattr(args, "compact", False):
            ok = out.get("ok")
            sk = out.get("skipped")
            print(
                "adaptation-run: ok=%s skipped=%s profile_id=%s"
                % (ok, sk, out.get("profile_id"))
            )
        else:
            print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "advise-refresh":
        sys.exit(_cmd_advise_refresh())
    if args.cmd == "strategy-status":
        out = strategy_status()
        if getattr(args, "compact", False):
            p = out.get("pointers") or {}
            print(
                "tier=%s profile=%s frozen=%s"
                % (
                    out.get("deployment_tier"),
                    out.get("active_strategy_profile_id"),
                    out.get("promotions_frozen"),
                )
            )
            print("pointers:", json.dumps(p, indent=2))
        else:
            print(json.dumps(out, indent=2, default=str))
        sys.exit(0)
    if args.cmd == "strategy-evaluate":
        out = strategy_evaluate(profile_id=args.profile_id, tier=args.tier)
        if getattr(args, "compact", False):
            ok = out.get("passed")
            print("evaluate: passed=%s profile=%s tier=%s" % (ok, out.get("profile_id"), out.get("tier")))
            for o in out.get("outcomes") or []:
                print(" - %s: %s (%s)" % (o.get("name"), o.get("passed"), o.get("detail")))
        else:
            print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("passed") else 1)
    if args.cmd == "strategy-promote":
        out = strategy_promote(args.to_tier, args.profile_id, dry_run=args.dry_run)
        print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "strategy-revert":
        out = strategy_revert_to_last_good(args.to_tier)
        print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "daily-report":
        if args.date:
            try:
                day = date.fromisoformat(args.date)
            except ValueError:
                print("daily-report: invalid --date (expected YYYY-MM-DD)", file=sys.stderr)
                sys.exit(2)
        else:
            day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        out = run_daily_report_job(day, include_preflight=args.preflight)
        if getattr(args, "compact", False):
            print("daily-report: %s %s" % (out.get("json_path"), out.get("md_path")))
        else:
            print(json.dumps(out, indent=2, default=str))
        sys.exit(0 if out.get("ok") else 1)
    if args.cmd == "proposal-apply":
        out = apply_llm_proposal_from_path(Path(args.file))
        if getattr(args, "compact", False):
            print(
                "proposal-apply: ok=%s profile_id=%s proposal_only=%s"
                % (out.get("ok"), out.get("profile_id"), out.get("proposal_only"))
            )
        else:
            print(json.dumps(out, indent=2, default=str))
        ok = bool(out.get("ok"))
        if out.get("errors"):
            ok = False
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

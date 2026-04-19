#!/usr/bin/env python3
"""
Summarize decision audit JSONL into actionable bottleneck buckets.

Input defaults to artifacts/decision_audit.jsonl (or --input).
Writes JSON and Markdown reports under artifacts/decision_quality/.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        by_symbol = rec.get("by_symbol")
        if not isinstance(by_symbol, dict):
            continue
        for symbol, payload in by_symbol.items():
            if isinstance(payload, dict):
                rows.append({"symbol": symbol, **payload})
    return rows


def _counter_dict(c: Counter[str]) -> dict[str, int]:
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    action = Counter()
    reason = Counter()
    regime = Counter()
    by_symbol_reason: dict[str, Counter[str]] = defaultdict(Counter)
    by_regime_reason: dict[str, Counter[str]] = defaultdict(Counter)
    no_trade_buckets = Counter()

    for r in rows:
        sym = str(r.get("symbol", "unknown"))
        a = str(r.get("action", "unknown"))
        rg = str(r.get("regime", "unknown"))
        rsn = str(r.get("reason", "unspecified"))
        action[a] += 1
        reason[rsn] += 1
        regime[rg] += 1
        by_symbol_reason[sym][rsn] += 1
        by_regime_reason[rg][rsn] += 1
        if a in ("no_trade", "blocked") or rsn not in ("hold", "unspecified"):
            no_trade_buckets[rsn] += 1

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows),
        "action_counts": _counter_dict(action),
        "reason_counts": _counter_dict(reason),
        "regime_counts": _counter_dict(regime),
        "no_trade_or_block_reasons": _counter_dict(no_trade_buckets),
        "by_symbol_reason": {k: _counter_dict(v) for k, v in sorted(by_symbol_reason.items())},
        "by_regime_reason": {k: _counter_dict(v) for k, v in sorted(by_regime_reason.items())},
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Decision Quality Report",
        "",
        f"- Generated UTC: {report.get('generated_utc')}",
        f"- Rows analyzed: {report.get('rows', 0)}",
        "",
        "## Top no-trade/block reasons",
    ]
    top = report.get("no_trade_or_block_reasons") or {}
    if not top:
        lines.append("- none")
    else:
        for k, v in list(top.items())[:15]:
            lines.append(f"- {k}: {v}")
    lines.extend(["", "## By regime (reason counts)"])
    by_regime = report.get("by_regime_reason") or {}
    for rg, counts in by_regime.items():
        lines.append(f"- {rg}:")
        for k, v in list(counts.items())[:10]:
            lines.append(f"  - {k}: {v}")
    lines.extend(["", "## By symbol (reason counts)"])
    by_sym = report.get("by_symbol_reason") or {}
    for sym, counts in by_sym.items():
        lines.append(f"- {sym}:")
        for k, v in list(counts.items())[:10]:
            lines.append(f"  - {k}: {v}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize decision audit JSONL.")
    ap.add_argument("--input", type=str, default="artifacts/decision_audit.jsonl")
    ap.add_argument("--out-dir", type=str, default="artifacts/decision_quality")
    args = ap.parse_args()

    inp = Path(args.input)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_rows(inp)
    report = build_report(rows)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(_render_markdown(report), encoding="utf-8")
    print(f"Wrote {out_dir / 'report.json'}")
    print(f"Wrote {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

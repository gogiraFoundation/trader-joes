"""Offline research agent wrappers (advisory only)."""

from __future__ import annotations

from typing import Any

from tr_bot.research.hypothesis_registry import append_hypothesis
from tr_bot.trade_ledger import read_recent_trades


def performance_review(*, limit: int = 100) -> dict[str, Any]:
    rows = read_recent_trades(limit=limit)
    wins = sum(1 for r in rows if r.get("outcome") == "win")
    losses = sum(1 for r in rows if r.get("outcome") == "loss")
    smc = [r for r in rows if r.get("signal_branch") == "smc"]
    summary = {
        "n": len(rows),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / (wins + losses)) if (wins + losses) else None,
        "smc_trades": len(smc),
        "narrative": (
            f"Reviewed {len(rows)} closed trades: {wins} wins / {losses} losses; "
            f"{len(smc)} tagged signal_branch=smc."
        ),
    }
    append_hypothesis(
        description=summary["narrative"],
        category="performance",
        source="performance_review_agent",
        meta={"wins": wins, "losses": losses},
    )
    return {"ok": True, "agent": "performance_review", "summary": summary}


def structure_review(*, note: str = "") -> dict[str, Any]:
    text = note.strip() or "Structure review placeholder — attach HTF bias notes offline."
    hyp = append_hypothesis(description=text, category="structure", source="structure_review_agent")
    return {"ok": True, "agent": "structure_review", "hypothesis": hyp}


def smc_review(*, note: str = "") -> dict[str, Any]:
    text = note.strip() or "SMC review placeholder — inspect FVGs/OBs that failed mitigation."
    hyp = append_hypothesis(description=text, category="smc", source="smc_review_agent")
    return {"ok": True, "agent": "smc_review", "hypothesis": hyp}


def run_agent(name: str, **kwargs: Any) -> dict[str, Any]:
    n = (name or "").strip().lower()
    if n in ("performance", "performance_review"):
        return performance_review(limit=int(kwargs.get("limit", 100)))
    if n in ("structure", "structure_review"):
        return structure_review(note=str(kwargs.get("note", "")))
    if n in ("smc", "smc_review"):
        return smc_review(note=str(kwargs.get("note", "")))
    return {"ok": False, "error": f"unknown agent: {name}"}

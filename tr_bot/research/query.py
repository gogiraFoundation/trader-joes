"""Template query helpers over trade ledger / parquet (no RAG)."""

from __future__ import annotations

from typing import Any

from tr_bot.trade_ledger import read_recent_trades


def filter_trades(
    *,
    outcome: str | None = None,
    min_score: float | None = None,
    signal_branch: str | None = None,
    session: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    rows = read_recent_trades(limit=max(limit * 5, 500))
    out: list[dict[str, Any]] = []
    for r in rows:
        if outcome and str(r.get("outcome")) != outcome:
            continue
        if signal_branch and str(r.get("signal_branch") or "") != signal_branch:
            continue
        if min_score is not None:
            try:
                sc = r.get("trade_score")
                if sc is None or float(sc) < float(min_score):
                    continue
            except (TypeError, ValueError):
                continue
        if session and str((r.get("session") or "")) != session:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return {"ok": True, "count": len(out), "trades": out}


def query_template(name: str, **kwargs: Any) -> dict[str, Any]:
    name = (name or "").strip().lower()
    if name in ("losing_smc", "smc_losses"):
        return filter_trades(outcome="loss", signal_branch="smc", limit=int(kwargs.get("limit", 50)))
    if name in ("high_score_wins",):
        return filter_trades(
            outcome="win",
            min_score=float(kwargs.get("min_score", 80)),
            limit=int(kwargs.get("limit", 50)),
        )
    if name in ("recent", "all"):
        return filter_trades(limit=int(kwargs.get("limit", 50)))
    return {"ok": False, "error": f"unknown template: {name}", "known": ["losing_smc", "high_score_wins", "recent"]}


def load_training_parquet_sample(limit: int = 5) -> dict[str, Any]:
    try:
        import pandas as pd
        from tr_bot.training.dataset import dataset_file_path

        path = dataset_file_path()
        if not path.is_file():
            return {"ok": False, "error": "parquet_missing", "path": str(path)}
        df = pd.read_parquet(path)
        return {
            "ok": True,
            "path": str(path),
            "rows": int(len(df)),
            "sample": df.tail(limit).to_dict(orient="records"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

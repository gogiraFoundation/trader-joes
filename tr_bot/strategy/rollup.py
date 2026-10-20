"""Optional daily JSON rollup under artifacts/strategy_rollups/ for gate shortcuts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tr_bot import config
from tr_bot.strategy import deployment_io as dep
from tr_bot.trade_ledger import read_recent_trades


def _utc_date_prefix(iso_utc: str) -> str | None:
    try:
        return str(iso_utc)[:10]
    except (TypeError, ValueError):
        return None


def build_daily_rollup_payload(artifacts_root: Path | None = None) -> dict:
    """Aggregate recent closed trades for the current UTC day (for dashboards / gates)."""
    today = datetime.now(timezone.utc).date().isoformat()
    trades = read_recent_trades(limit=50_000)
    day_rows = []
    for t in trades:
        u = t.get("utc") or ""
        if _utc_date_prefix(u) == today:
            day_rows.append(t)
    wins = sum(1 for t in day_rows if t.get("outcome") == "win")
    losses = sum(1 for t in day_rows if t.get("outcome") == "loss")
    net = 0.0
    for t in day_rows:
        try:
            net += float(t.get("net_pnl_usdt", 0) or 0)
        except (TypeError, ValueError):
            pass
    return {
        "date_utc": today,
        "profile_id": config.ACTIVE_STRATEGY_PROFILE_ID or None,
        "deployment_tier": config.DEPLOYMENT_TIER,
        "closed_trades": len(day_rows),
        "wins": wins,
        "losses": losses,
        "net_pnl_usdt_sum": net,
    }


def maybe_write_daily_rollup() -> None:
    """Idempotent once per UTC day when STRATEGY_DAILY_ROLLUP_ENABLED is true."""
    if not config.STRATEGY_DAILY_ROLLUP_ENABLED:
        return
    root = Path(config.ARTIFACTS_DIR).resolve()
    today = datetime.now(timezone.utc).date().isoformat()
    out_dir = root / "strategy_rollups"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{today}.json"
    if path.is_file():
        return
    dep.atomic_write_json(path, build_daily_rollup_payload(root))

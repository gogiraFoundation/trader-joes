"""Fund-level NAV, drawdown, and trading halt (production-oriented)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.exchange import get_ccxt_exchange
from tr_bot.notifications import notify
from tr_bot.notifications import events as notify_events
from tr_bot.managers.portfolio import active_symbols
from tr_bot.managers.security import safe_artifact_path
from tr_bot.symbols import split_pair
from tr_bot.trade_ledger import sum_closed_net_pnl_usdt

logger = logging.getLogger("tr_bot.fund")


@dataclass
class FundState:
    last_nav: float
    peak_nav: float
    last_update_utc: str
    halted: bool
    halt_reason: str
    # When set, mismatch vs PAPER_EQUITY_USDT means prior peak is invalid; reset peak.
    paper_equity_baseline_usdt: float | None = None


class FundManager:
    """
    Tracks equity (paper or live), drawdown vs peak, and halt conditions.
    State is persisted under ARTIFACTS_DIR for crash-safe restarts.
    """

    def __init__(self) -> None:
        self._root = Path(config.ARTIFACTS_DIR).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._state_path = safe_artifact_path(self._root, "fund_state.json")
        self._halt_file = safe_artifact_path(self._root, config.TRADING_HALT_FILENAME)
        self._last_refresh = 0.0

    def _load_state(self) -> FundState | None:
        if not self._state_path.is_file():
            return None
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            pb = raw.get("paper_equity_baseline_usdt")
            paper_base = float(pb) if pb is not None and str(pb).strip() != "" else None
            return FundState(
                last_nav=float(raw["last_nav"]),
                peak_nav=float(raw["peak_nav"]),
                last_update_utc=str(raw["last_update_utc"]),
                halted=bool(raw.get("halted", False)),
                halt_reason=str(raw.get("halt_reason", "")),
                paper_equity_baseline_usdt=paper_base,
            )
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Could not load fund state: %s", e)
            return None

    def _save_state(self, st: FundState) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(st), indent=2), encoding="utf-8")
        os.replace(tmp, self._state_path)

    def manual_halt_file_active(self) -> bool:
        """Operator creates this file to stop trading without code deploy."""
        return self._halt_file.is_file()

    def clear_manual_halt(self) -> None:
        if self._halt_file.is_file():
            self._halt_file.unlink()

    def estimate_equity_usdt(self, *, unrealized_pnl_usdt: float = 0.0) -> float:
        """Total equity in USDT: live = exchange balances; paper = baseline + ledger net P&L."""
        if config.PAPER_TRADING:
            base = float(config.PAPER_EQUITY_USDT)
            sim = sum_closed_net_pnl_usdt(paper=True)
            return max(base + sim + float(unrealized_pnl_usdt), 0.0)

        ex = get_ccxt_exchange()
        ex.load_markets()
        bal = ex.fetch_balance()
        quote = config.FUND_QUOTE_ASSET
        total = float((bal.get(quote) or {}).get("total", 0) or 0)

        for symbol in active_symbols():
            if "/" not in symbol:
                continue
            try:
                base, q = split_pair(symbol)
            except ValueError:
                continue
            if q != quote:
                continue
            amt = float((bal.get(base) or {}).get("total", 0) or 0)
            if amt <= 0:
                continue
            t = ex.fetch_ticker(symbol)
            total += amt * float(t.get("last") or t.get("close") or 0)
        return max(total, 0.0)

    def refresh_nav_and_limits(self, *, unrealized_pnl_usdt: float = 0.0) -> FundState:
        prev = self._load_state()
        cfg_paper = float(config.PAPER_EQUITY_USDT) if config.PAPER_TRADING else None
        baseline_mismatch = False
        if cfg_paper is not None and prev is not None and prev.paper_equity_baseline_usdt is not None:
            if abs(prev.paper_equity_baseline_usdt - cfg_paper) > 1e-6:
                baseline_mismatch = True
                logger.info(
                    "Fund: PAPER_EQUITY_USDT changed (%.4f -> %.4f); resetting peak to current NAV",
                    prev.paper_equity_baseline_usdt,
                    cfg_paper,
                )

        nav = self.estimate_equity_usdt(unrealized_pnl_usdt=unrealized_pnl_usdt)
        u = float(unrealized_pnl_usdt)
        # Transient corrupt NAV (e.g. wrong intermediate read) while baseline+ledger
        # still show full paper equity — would pair with stale peak and false 99% DD halt.
        if (
            cfg_paper is not None
            and cfg_paper >= 500.0
            and abs(u) <= max(1.0, cfg_paper * 0.001)
        ):
            sim_closed = sum_closed_net_pnl_usdt(paper=True)
            ledger_floor = max(0.0, float(cfg_paper) + float(sim_closed))
            if (
                nav < float(cfg_paper) * 0.05
                and ledger_floor >= float(cfg_paper) * 0.90
                and nav < ledger_floor * 0.25
            ):
                logger.warning(
                    "Fund: paper NAV %.4f inconsistent with baseline+ledger %.4f; using ledger equity",
                    nav,
                    ledger_floor,
                )
                nav = ledger_floor

        now = datetime.now(timezone.utc).isoformat()
        if baseline_mismatch:
            peak = nav
        else:
            peak = max(nav, prev.peak_nav if prev else nav)
        dd = 0.0 if peak <= 0 else (peak - nav) / peak
        # Drawdown halt is evaluated each refresh from current dd only. Do not latch
        # prev.halted: a stale halt_reason (e.g. dd=0.99) would stick forever while
        # current dd returns to 0 after NAV/peak reconcile or PAPER_EQUITY change.
        halted = False
        reason = ""

        if self.manual_halt_file_active():
            halted = True
            reason = "manual_halt_file"
        elif dd >= config.MAX_DRAWDOWN_PCT:
            halted = True
            reason = f"max_drawdown_exceeded ({dd:.4f}>={config.MAX_DRAWDOWN_PCT})"

        st = FundState(
            last_nav=nav,
            peak_nav=peak,
            last_update_utc=now,
            halted=halted,
            halt_reason=reason,
            paper_equity_baseline_usdt=cfg_paper,
        )
        was_halted = bool(prev and prev.halted)
        self._save_state(st)
        if halted and not was_halted:
            notify(
                notify_events.FUND_HALT,
                reason=reason,
                nav=nav,
                drawdown=dd,
                peak_nav=peak,
            )
        if halted:
            logger.warning("Fund halt active: %s nav=%.4f dd=%.4f", reason, nav, dd)
        else:
            logger.info("Fund NAV=%.4f peak=%.4f dd=%.4f", nav, peak, dd)
        return st

    def is_trading_allowed(self) -> bool:
        if not config.MANAGERS_ENABLED:
            return True
        st = self._load_state()
        if st and st.halted:
            return False
        if self.manual_halt_file_active():
            return False
        return True

    def get_state(self) -> FundState | None:
        return self._load_state()

    def get_last_nav(self) -> float:
        st = self._load_state()
        if st:
            return float(st.last_nav)
        return self.estimate_equity_usdt()

    def maybe_refresh(self, now: float) -> None:
        if not config.MANAGERS_ENABLED:
            return
        st = self._load_state()
        urgent = st is not None and st.halted
        if (
            not urgent
            and now - getattr(self, "_last_refresh", 0.0) < float(config.FUND_REFRESH_SECONDS)
        ):
            return
        self._last_refresh = now
        self.refresh_nav_and_limits()

    def snapshot_job(self) -> dict[str, Any]:
        """Callable from cron / manager worker."""
        st = self.refresh_nav_and_limits()
        return asdict(st)

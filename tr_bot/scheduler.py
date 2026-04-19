"""Periodic training + backtest jobs (separate process; do not block the trading loop)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from tr_bot import config
from tr_bot.backtest.runner import run_backtest_job
from tr_bot.logger import setup_logger
from tr_bot.managers.training_manager import run_training_job
from tr_bot.promotion.service import strategy_evaluate
from tr_bot.reporting.daily import run_daily_report_job
from tr_bot.strategy.no_trade_adaptation import run_adaptation_job

logger = setup_logger("tr_bot.scheduler")

_lock = threading.Lock()


def _run_locked(name: str, fn: Callable[[], None]) -> None:
    if not _lock.acquire(blocking=False):
        logger.info("Skip %s: another scheduler job is running", name)
        return
    try:
        logger.info("Scheduler: starting %s", name)
        fn()
        logger.info("Scheduler: finished %s", name)
    except Exception:
        logger.exception("Scheduler: %s failed", name)
    finally:
        _lock.release()


def due_for_interval(last: float | None, interval: int, now: float) -> bool:
    if interval <= 0:
        return False
    if last is None:
        return True
    return (now - last) >= interval


def run_forever() -> None:
    logging.getLogger("tr_bot.scheduler").setLevel(logging.INFO)

    train_iv = int(config.SCHEDULER_TRAINING_INTERVAL_SECONDS)
    back_iv = int(config.SCHEDULER_BACKTEST_INTERVAL_SECONDS)
    promo_iv = int(config.SCHEDULER_PROMOTION_INTERVAL_SECONDS)
    adapt_iv = int(config.ADAPTATION_INTERVAL_SECONDS)
    asset_iv = int(config.SCHEDULER_COINDESK_ASSET_TOP_LIST_INTERVAL_SECONDS)
    daily_report_iv = int(config.SCHEDULER_DAILY_REPORT_INTERVAL_SECONDS)
    tick = max(5, int(config.SCHEDULER_TICK_SECONDS))
    initial = float(config.SCHEDULER_INITIAL_DELAY_SECONDS)

    active = (
        config.SCHEDULER_ENABLED
        or train_iv > 0
        or back_iv > 0
        or promo_iv > 0
        or adapt_iv > 0
        or asset_iv > 0
        or daily_report_iv > 0
    )
    if not active:
        logger.error(
            "Nothing to schedule: set SCHEDULER_ENABLED=1 or set a positive "
            "SCHEDULER_TRAINING_INTERVAL_SECONDS / SCHEDULER_BACKTEST_INTERVAL_SECONDS / "
            "SCHEDULER_PROMOTION_INTERVAL_SECONDS / ADAPTATION_INTERVAL_SECONDS / "
            "SCHEDULER_COINDESK_ASSET_TOP_LIST_INTERVAL_SECONDS / "
            "SCHEDULER_DAILY_REPORT_INTERVAL_SECONDS"
        )
        return

    logger.info(
        "Scheduler tick=%ss training_interval=%ss backtest_interval=%ss promotion_interval=%ss adaptation_interval=%ss asset_top_list_interval=%ss daily_report_interval=%ss",
        tick,
        train_iv,
        back_iv,
        promo_iv,
        adapt_iv,
        asset_iv,
        daily_report_iv,
    )

    if initial > 0:
        time.sleep(initial)

    last_train: float | None = None
    last_back: float | None = None
    last_promo: float | None = None
    last_adapt: float | None = None
    last_asset: float | None = None
    last_daily_report: float | None = None

    def _run_promotion_eval() -> None:
        out = strategy_evaluate()
        ok = bool(out.get("passed"))
        logger.info(
            "Scheduler promotion evaluate: passed=%s hash=%s",
            ok,
            out.get("input_hash"),
        )

    def _run_coindesk_asset_top_list() -> None:
        from tr_bot.coindesk.asset_catalog import refresh_asset_top_list_snapshot

        out = refresh_asset_top_list_snapshot()
        if not out.get("ok"):
            logger.warning("CoinDesk asset top list snapshot: %s", out.get("error"))
        else:
            logger.info("CoinDesk asset top list snapshot written: %s", out.get("path"))

    def _run_daily_report() -> None:
        from datetime import datetime, timedelta, timezone

        day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        out = run_daily_report_job(day, include_preflight=False)
        logger.info("Daily report: %s", out.get("json_path"))

    if config.SCHEDULER_RUN_TRAINING_ON_START and train_iv > 0:
        _run_locked("training", lambda: run_training_job())
        last_train = time.time()
    if config.SCHEDULER_RUN_BACKTEST_ON_START and back_iv > 0:
        _run_locked("backtest", run_backtest_job)
        last_back = time.time()
    if config.SCHEDULER_RUN_PROMOTION_ON_START and promo_iv > 0:
        _run_locked("promotion_evaluate", _run_promotion_eval)
        last_promo = time.time()
    if config.SCHEDULER_RUN_ADAPTATION_ON_START and adapt_iv > 0:
        _run_locked("no_trade_adaptation", run_adaptation_job)
        last_adapt = time.time()
    if config.SCHEDULER_RUN_COINDESK_ASSET_TOP_LIST_ON_START and asset_iv > 0:
        _run_locked("coindesk_asset_top_list", _run_coindesk_asset_top_list)
        last_asset = time.time()
    if config.SCHEDULER_RUN_DAILY_REPORT_ON_START and daily_report_iv > 0:
        _run_locked("daily_report", _run_daily_report)
        last_daily_report = time.time()

    # due_for_interval(None, interval, now) is True — so without this, the first loop
    # iteration would run training/backtest immediately after startup (duplicate of
    # ./start.sh --all or redundant with *_ON_START=false).
    if train_iv > 0 and last_train is None:
        last_train = time.time()
    if back_iv > 0 and last_back is None:
        last_back = time.time()
    if promo_iv > 0 and last_promo is None:
        last_promo = time.time()
    if adapt_iv > 0 and last_adapt is None:
        last_adapt = time.time()
    if asset_iv > 0 and last_asset is None:
        last_asset = time.time()
    if daily_report_iv > 0 and last_daily_report is None:
        last_daily_report = time.time()

    while True:
        now = time.time()
        if train_iv > 0 and due_for_interval(last_train, train_iv, now):
            _run_locked("training", lambda: run_training_job())
            last_train = time.time()
        if back_iv > 0 and due_for_interval(last_back, back_iv, now):
            _run_locked("backtest", run_backtest_job)
            last_back = time.time()
        if promo_iv > 0 and due_for_interval(last_promo, promo_iv, now):
            _run_locked("promotion_evaluate", _run_promotion_eval)
            last_promo = time.time()
        if adapt_iv > 0 and due_for_interval(last_adapt, adapt_iv, now):
            _run_locked("no_trade_adaptation", run_adaptation_job)
            last_adapt = time.time()
        if asset_iv > 0 and due_for_interval(last_asset, asset_iv, now):
            _run_locked("coindesk_asset_top_list", _run_coindesk_asset_top_list)
            last_asset = time.time()
        if daily_report_iv > 0 and due_for_interval(last_daily_report, daily_report_iv, now):
            _run_locked("daily_report", _run_daily_report)
            last_daily_report = time.time()
        time.sleep(tick)


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()

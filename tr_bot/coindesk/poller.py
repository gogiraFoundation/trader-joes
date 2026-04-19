"""Async polling loop: latest ticks → Postgres (+ optional Redis pub)."""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp

from tr_bot import config
from tr_bot.coindesk.async_fetch import fetch_latest_ticks_aiohttp
from tr_bot.coindesk.ccseq import process_ticks_with_ccseq
from tr_bot.coindesk.endpoints import fetch_instruments_metadata
from tr_bot.db import coindesk_repo

logger = logging.getLogger("tr_bot.coindesk.poller")


def _store_and_publish(rows: list) -> None:
    if not rows:
        logger.info("CoinDesk poll: no tick rows")
        return
    logger.info("CoinDesk poll: %s tick rows", len(rows))

    if config.COINDESK_STORE_ENABLED:

        def _store_ticks_once() -> None:
            conn_holder = [coindesk_repo.connect()]
            try:
                coindesk_repo.ping(conn_holder[0])
                process_ticks_with_ccseq(conn_holder, rows)
            finally:
                coindesk_repo.safe_close(conn_holder[0])

        try:
            _store_ticks_once()
        except Exception as e:
            if coindesk_repo.is_recoverable_connection_error(e):
                logger.warning("CoinDesk DB store transient error, retry once: %s", e)
                _store_ticks_once()
            else:
                raise

    if config.COINDESK_REDIS_PUBLISH:
        try:
            import redis

            r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
            payload = json.dumps(rows[:50], default=str)
            r.publish(config.COINDESK_REDIS_CHANNEL, payload)
        except Exception as e:
            logger.warning("CoinDesk Redis publish failed: %s", e)


async def _one_poll_cycle_aiohttp(session: aiohttp.ClientSession) -> None:
    if not config.COINDESK_API_KEY:
        logger.warning("COINDESK_API_KEY empty; poller skipping")
        return
    try:
        rows = await fetch_latest_ticks_aiohttp(session)
    except Exception as e:
        logger.error("CoinDesk aiohttp fetch failed: %s", e)
        return
    _store_and_publish(rows)


def _metadata_cycle() -> None:
    path = config.COINDESK_INSTRUMENTS_METADATA_PATH.strip()
    if not path or not config.COINDESK_STORE_ENABLED:
        return
    try:
        meta = fetch_instruments_metadata()
        if not meta:
            return
        conn = coindesk_repo.connect()
        try:
            coindesk_repo.ping(conn)
            coindesk_repo.upsert_instruments(conn, meta)
        finally:
            coindesk_repo.safe_close(conn)
    except Exception as e:
        logger.warning("CoinDesk instruments metadata cycle failed: %s", e)


async def run_forever() -> None:
    loop = asyncio.get_running_loop()
    meta_every = max(1, int(3600 / max(1, config.COINDESK_POLL_SECONDS)))
    n = 0
    timeout = aiohttp.ClientTimeout(total=max(5.0, config.COINDESK_HTTP_TIMEOUT_S))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                await _one_poll_cycle_aiohttp(session)
                n += 1
                if n % meta_every == 0:
                    await loop.run_in_executor(None, _metadata_cycle)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("CoinDesk poll cycle error: %s", e)
            await asyncio.sleep(max(1, config.COINDESK_POLL_SECONDS))


def main() -> None:
    from tr_bot.logger import setup_logger

    setup_logger("tr_bot.coindesk.poller")
    asyncio.run(run_forever())

"""HTTP client for CoinDesk Data API with auth, retries, and rate limits."""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping
from urllib.parse import urljoin

import requests

from tr_bot import config
from tr_bot.coindesk.auth import build_auth_headers
from tr_bot.coindesk.metrics import LatencyTimer, record_failure
from tr_bot.coindesk.rate_limit import RateLimitController, coindesk_payload_messages

logger = logging.getLogger("tr_bot.coindesk.client")


class CoinDeskClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else config.COINDESK_API_KEY) or ""
        self.base_url = (base_url or config.COINDESK_BASE_URL).rstrip("/")
        self.timeout_s = float(timeout_s if timeout_s is not None else config.COINDESK_HTTP_TIMEOUT_S)
        self.rate = RateLimitController(
            remaining_threshold=max(0, config.COINDESK_RATE_LIMIT_REMAINING_THRESHOLD),
            slow_delay_s=max(0.0, config.COINDESK_RATE_LIMIT_SLOW_DELAY_S),
        )

    def get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        endpoint_metric: str = "get",
    ) -> dict[str, Any]:
        """GET JSON; raises on unrecoverable HTTP errors after retries."""
        url = path if path.startswith("http") else urljoin(self.base_url + "/", path.lstrip("/"))
        headers = build_auth_headers(self.api_key)
        max_retries = max(1, config.COINDESK_MAX_RETRIES)
        backoff_cap = max(1.0, config.COINDESK_RETRY_BACKOFF_CAP_S)
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            self.rate.maybe_slow_down()
            timer = LatencyTimer(endpoint_metric)
            try:
                r = requests.get(url, params=dict(params or {}), headers=headers, timeout=self.timeout_s)
            except requests.RequestException as e:
                timer.done(None)
                record_failure("network")
                last_exc = e
                logger.error("CoinDesk request failed (network): %s", e)
                if attempt == max_retries - 1:
                    raise
                time.sleep(min(backoff_cap, 2**attempt))
                continue

            self.rate.observe_headers(dict(r.headers))
            timer.done(r.status_code)

            if r.status_code == 429:
                record_failure("429")
                self.rate.sleep_for_429(dict(r.headers), attempt=attempt, backoff_cap_s=backoff_cap)
                continue

            if r.status_code >= 500:
                record_failure(f"http_{r.status_code}")
                logger.warning(
                    "CoinDesk HTTP %s (attempt %s/%s): %s",
                    r.status_code,
                    attempt + 1,
                    max_retries,
                    r.text[:400],
                )
                time.sleep(min(backoff_cap, 2**attempt + 0.1 * attempt))
                continue

            if r.status_code >= 400:
                record_failure(f"http_{r.status_code}")
                logger.error("CoinDesk HTTP %s: %s", r.status_code, r.text[:500])
                r.raise_for_status()

            try:
                payload: Any = r.json()
            except ValueError as e:
                record_failure("json_decode")
                logger.error("CoinDesk invalid JSON: %s", e)
                raise RuntimeError("CoinDesk response is not valid JSON") from e

            if isinstance(payload, dict):
                errs, warns = coindesk_payload_messages(payload)
                for w in warns:
                    logger.warning("CoinDesk API warn: %s", w)
                if errs:
                    logger.error("CoinDesk API err: %s", "; ".join(errs))

            if not isinstance(payload, dict):
                raise RuntimeError(f"CoinDesk expected JSON object, got {type(payload)}")
            return payload

        if last_exc:
            raise last_exc
        raise RuntimeError("CoinDesk GET exhausted retries")

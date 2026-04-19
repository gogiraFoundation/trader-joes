"""Parse CoinDesk rate-limit headers and compute adaptive delays."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger("tr_bot.coindesk.rate_limit")


def _header_ci(headers: Mapping[str, str], name: str) -> str | None:
    if not headers:
        return None
    lower = name.lower()
    for k, v in headers.items():
        if k.lower() == lower:
            return v.strip() if isinstance(v, str) else str(v)
    return None


def _parse_int(v: str | None) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


@dataclass
class RateLimitSnapshot:
    limit: int | None = None
    remaining: int | None = None
    reset_epoch: int | None = None  # Unix seconds when window resets

    def update_from_headers(self, headers: Mapping[str, str] | None) -> None:
        if not headers:
            return
        self.limit = _parse_int(_header_ci(headers, "X-Ratelimit-Limit")) or self.limit
        self.remaining = _parse_int(_header_ci(headers, "X-Ratelimit-Remaining")) or self.remaining
        self.reset_epoch = _parse_int(_header_ci(headers, "X-Ratelimit-Reset")) or self.reset_epoch


@dataclass
class RateLimitController:
    """Adaptive delay when remaining is low; sleep until reset on 429."""

    remaining_threshold: int
    slow_delay_s: float
    last_snapshot: RateLimitSnapshot = field(default_factory=RateLimitSnapshot)

    def observe_headers(self, headers: Mapping[str, str] | None) -> None:
        self.last_snapshot.update_from_headers(headers)
        rem = self.last_snapshot.remaining
        lim = self.last_snapshot.limit
        if rem is not None and lim is not None:
            logger.debug("CoinDesk rate limit: %s/%s remaining", rem, lim)

    def maybe_slow_down(self) -> None:
        rem = self.last_snapshot.remaining
        if rem is not None and rem < self.remaining_threshold:
            delay = max(0.0, float(self.slow_delay_s))
            if delay > 0:
                logger.info(
                    "CoinDesk rate limit low (%s remaining < %s); sleeping %.2fs",
                    rem,
                    self.remaining_threshold,
                    delay,
                )
                time.sleep(delay)

    def sleep_for_429(
        self,
        response_headers: Mapping[str, str] | None,
        *,
        attempt: int,
        backoff_cap_s: float,
    ) -> None:
        retry_after = _parse_int(_header_ci(response_headers or {}, "Retry-After"))
        reset = self.last_snapshot.reset_epoch
        now = int(time.time())
        until_reset = max(0, (reset or 0) - now) if reset else 0
        base = 2**max(0, attempt)
        exp = min(float(backoff_cap_s), base + 0.25 * attempt)
        if retry_after is not None and retry_after > 0:
            wait = float(retry_after)
        elif until_reset > 0:
            wait = float(min(until_reset, backoff_cap_s))
        else:
            wait = exp
        wait = min(wait, backoff_cap_s)
        logger.warning("CoinDesk HTTP 429; sleeping %.1fs (attempt %s)", wait, attempt + 1)
        time.sleep(wait)


def coindesk_payload_messages(payload: Any) -> tuple[list[str], list[str]]:
    """Extract Err / Warn style messages from a parsed JSON body (best-effort)."""
    errs: list[str] = []
    warns: list[str] = []
    if not isinstance(payload, dict):
        return errs, warns
    for key, bucket in (("Err", errs), ("Warning", warns), ("Warnings", warns), ("Warn", warns)):
        v = payload.get(key)
        if v is None:
            continue
        if isinstance(v, str):
            bucket.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    bucket.append(item)
                elif isinstance(item, dict):
                    msg = item.get("message") or item.get("Message") or str(item)
                    bucket.append(str(msg))
    st = payload.get("status") or payload.get("Status")
    if isinstance(st, dict):
        m = st.get("error_message") or st.get("message")
        if isinstance(m, str):
            errs.append(m)
    return errs, warns

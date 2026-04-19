"""Optional Prometheus metrics for CoinDesk ingestion."""

from __future__ import annotations

import logging
import time
from typing import Any

from tr_bot import config

logger = logging.getLogger("tr_bot.coindesk.metrics")

_metrics: Any = None


def _ensure_metrics() -> Any:
    global _metrics
    if _metrics is not None:
        return _metrics
    if not config.COINDESK_METRICS_ENABLED:
        _metrics = False
        return _metrics
    try:
        from prometheus_client import Counter, Histogram

        m = {
            "requests": Counter(
                "coindesk_api_requests_total",
                "CoinDesk API requests",
                ["endpoint", "status"],
            ),
            "failures": Counter(
                "coindesk_api_failures_total",
                "CoinDesk API failures (network/HTTP error)",
                ["reason"],
            ),
            "ccseq_gaps": Counter(
                "coindesk_ccseq_gap_total",
                "Detected CCSEQ sequence gaps",
                ["market", "instrument"],
            ),
            "rows_written": Counter(
                "coindesk_db_rows_written_total",
                "Rows written to CoinDesk tables",
                ["table"],
            ),
            "latency": Histogram(
                "coindesk_request_latency_seconds",
                "CoinDesk HTTP request latency",
                buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
            ),
        }
        _metrics = m
    except ImportError:
        logger.warning("prometheus_client not installed; CoinDesk metrics disabled")
        _metrics = False
    return _metrics


def record_request(endpoint: str, status_code: int | None, latency_s: float | None = None) -> None:
    m = _ensure_metrics()
    if not m:
        return
    label = str(status_code or "error")
    m["requests"].labels(endpoint=endpoint, status=label).inc()
    if latency_s is not None:
        m["latency"].observe(latency_s)


def record_failure(reason: str) -> None:
    m = _ensure_metrics()
    if not m:
        return
    m["failures"].labels(reason=reason).inc()


def record_ccseq_gap(market: str, instrument: str) -> None:
    m = _ensure_metrics()
    if not m:
        return
    m["ccseq_gaps"].labels(market=market, instrument=instrument).inc()


def record_rows_written(table: str, n: int) -> None:
    m = _ensure_metrics()
    if not m:
        return
    m["rows_written"].labels(table=table).inc(max(0, int(n)))


class LatencyTimer:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self._t0 = time.perf_counter()

    def done(self, status_code: int | None) -> None:
        dt = time.perf_counter() - self._t0
        record_request(self.endpoint, status_code, dt)

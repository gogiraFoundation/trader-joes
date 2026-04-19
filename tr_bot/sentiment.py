"""LLM sentiment: advisory only; JSON + timeout + fallback (Sections 4.5, 8)."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import requests

from tr_bot import config

logger = logging.getLogger("tr_bot")

_cooldown_lock = threading.Lock()
# Monotonic time until which we skip LLM calls (global across symbols / loops).
_cooldown_until: float = 0.0


def _retry_after_seconds(response: requests.Response) -> float | None:
    h = response.headers.get("Retry-After")
    if not h:
        return None
    try:
        return float(h.strip())
    except ValueError:
        return None


def _extend_cooldown_seconds(seconds: float) -> None:
    global _cooldown_until
    with _cooldown_lock:
        until = time.monotonic() + max(0.0, seconds)
        if until > _cooldown_until:
            _cooldown_until = until


def get_sentiment(news_text: str) -> str:
    if not config.USE_LLM or not config.OPENAI_API_KEY:
        return "neutral"

    with _cooldown_lock:
        if time.monotonic() < _cooldown_until:
            return "neutral"

    url = f"{config.OPENAI_BASE_URL}/chat/completions"
    prompt = (
        "Analyze the sentiment of the following news headlines for short-term "
        "trading context. Return a JSON object with keys: "
        '"sentiment" (one of: bullish, bearish, neutral), '
        '"confidence" (number 0 to 1). '
        f"News:\n{news_text[:8000]}"
    )
    payload: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json=payload,
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
        if r.status_code == 429:
            ra = _retry_after_seconds(r)
            floor = float(config.LLM_COOLDOWN_AFTER_429_SECONDS)
            secs = max(floor, ra if ra is not None else floor)
            _extend_cooldown_seconds(secs)
            logger.warning(
                "get_sentiment rate limited (429); using neutral (~%.0fs cooldown, other symbols skip LLM)",
                secs,
            )
            return "neutral"
        r.raise_for_status()
        data = r.json()
        raw = data["choices"][0]["message"]["content"]
        obj = json.loads(raw)
        sentiment = str(obj.get("sentiment", "neutral")).lower()
        if sentiment not in ("bullish", "bearish", "neutral"):
            return "neutral"
        conf = obj.get("confidence")
        if isinstance(conf, (int, float)) and conf < config.LLM_MIN_CONFIDENCE:
            return "neutral"
        return sentiment
    except Exception as e:
        logger.warning("get_sentiment failed (%s); using neutral", e)
        return "neutral"

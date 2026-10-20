"""Shared OpenAI-compatible chat JSON client (sentiment + agents)."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

from tr_bot import config

logger = logging.getLogger("tr_bot.agents.llm")

_cooldown_lock = threading.Lock()
_cooldown_until: float = 0.0

# Whole-string markdown fence only (no prose wrapper).
_WHOLE_FENCE_RE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


class LLMClientError(Exception):
    """Non-retryable or exhausted LLM failure (caller should fail open)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _retry_after_seconds(response: requests.Response) -> float | None:
    h = response.headers.get("Retry-After")
    if not h:
        return None
    try:
        return float(h.strip())
    except ValueError:
        return None


def extend_cooldown_seconds(seconds: float) -> None:
    global _cooldown_until
    with _cooldown_lock:
        until = time.monotonic() + max(0.0, seconds)
        if until > _cooldown_until:
            _cooldown_until = until


def cooldown_active() -> bool:
    with _cooldown_lock:
        return time.monotonic() < _cooldown_until


def _require_https_base_url(base: str) -> None:
    raw = (base or "").strip()
    if not raw:
        raise LLMClientError("OPENAI_BASE_URL unset")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme != "https":
        raise LLMClientError("OPENAI_BASE_URL must use https (refusing to send API key over plain http)")


def parse_json_content(raw: str) -> dict[str, Any]:
    """Parse model content as a JSON object; whole-string JSON or a single fence only."""
    text = (raw or "").strip()
    if not text:
        raise LLMClientError("empty model content")
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
        raise LLMClientError("JSON root must be an object")
    except json.JSONDecodeError:
        pass
    m = _WHOLE_FENCE_RE.match(text)
    if m:
        inner = m.group(1).strip()
        try:
            obj = json.loads(inner)
        except json.JSONDecodeError as e:
            raise LLMClientError(f"fenced JSON invalid: {e}") from e
        if isinstance(obj, dict):
            return obj
        raise LLMClientError("fenced JSON root must be an object")
    raise LLMClientError("could not parse JSON object from model content")


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    timeout: float | None = None,
    use_response_format: bool = True,
) -> dict[str, Any]:
    """
    POST /chat/completions and return a parsed JSON object.

    On 429, extends the shared cooldown and raises LLMClientError.
    """
    if not config.OPENAI_API_KEY:
        raise LLMClientError("OPENAI_API_KEY unset")
    _require_https_base_url(config.OPENAI_BASE_URL)
    if cooldown_active():
        raise LLMClientError("LLM cooldown active")

    url = f"{config.OPENAI_BASE_URL}/chat/completions"
    mdl = (model or config.LLM_MODEL or "").strip() or config.LLM_MODEL
    to = float(timeout if timeout is not None else config.LLM_TIMEOUT_SECONDS)
    payload: dict[str, Any] = {
        "model": mdl,
        "messages": messages,
    }
    if use_response_format:
        payload["response_format"] = {"type": "json_object"}

    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json=payload,
            timeout=to,
        )
    except requests.RequestException as e:
        raise LLMClientError(f"request failed: {e}") from e

    if r.status_code == 429:
        ra = _retry_after_seconds(r)
        floor = float(config.LLM_COOLDOWN_AFTER_429_SECONDS)
        secs = max(floor, ra if ra is not None else floor)
        extend_cooldown_seconds(secs)
        raise LLMClientError(f"rate limited (429); cooldown ~{secs:.0f}s", status_code=429)

    if r.status_code >= 400 and use_response_format:
        # Some providers reject response_format; retry once without it.
        body_l = (r.text or "").lower()
        if "response_format" in body_l or r.status_code in (400, 422):
            logger.info("chat_json: retrying without response_format (status=%s)", r.status_code)
            return chat_json(
                messages,
                model=mdl,
                timeout=to,
                use_response_format=False,
            )

    try:
        r.raise_for_status()
        data = r.json()
        raw = data["choices"][0]["message"]["content"]
        return parse_json_content(str(raw))
    except LLMClientError:
        raise
    except Exception as e:
        raise LLMClientError(f"parse/response failed: {e}") from e

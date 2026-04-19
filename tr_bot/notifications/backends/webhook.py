"""HTTPS webhook (Slack-compatible `text` field, or generic JSON body)."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

import requests

from tr_bot import config

logger = logging.getLogger("tr_bot.notify.webhook")


def send(title: str, event: str, payload: dict[str, Any]) -> None:
    url = (config.NOTIFY_WEBHOOK_URL or "").strip()
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        logger.warning(
            "NOTIFY_WEBHOOK_URL must be an http(s) URL (got %r); skipping webhook",
            url[:80] + ("…" if len(url) > 80 else ""),
        )
        return
    env = config.NOTIFY_ENVIRONMENT or "default"
    text = f"[{env}] *{title}*\n`{event}`\n```{json.dumps(payload, indent=2, default=str)}```"
    fmt = (config.NOTIFY_WEBHOOK_FORMAT or "slack").lower().strip()
    if fmt == "slack":
        body: dict[str, Any] = {"text": text, "username": "tr_bot"}
    elif fmt == "ntfy":
        # https://docs.ntfy.sh/publish/#publish-as-json — mobile push via ntfy app
        short = json.dumps(payload, default=str)
        if len(short) > 3500:
            short = short[:3500] + "…"
        body = {
            "title": f"[{env}] {title}",
            "message": f"{event}\n{short}",
        }
    else:
        body = {"title": title, "event": event, "payload": payload, "environment": env}
    try:
        r = requests.post(
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=config.NOTIFY_WEBHOOK_TIMEOUT,
        )
        if r.status_code >= 400:
            logger.warning("Webhook HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("Webhook failed: %s", e)

"""LLM sentiment: advisory only; JSON + timeout + fallback (Sections 4.5, 8)."""

from __future__ import annotations

import logging

from tr_bot import config
from tr_bot.agents.llm_client import LLMClientError, chat_json, cooldown_active

logger = logging.getLogger("tr_bot")


def get_sentiment(news_text: str) -> str:
    if not config.USE_LLM or not config.OPENAI_API_KEY:
        return "neutral"

    if cooldown_active():
        return "neutral"

    prompt = (
        "Analyze the sentiment of the following news headlines for short-term "
        "trading context. Return a JSON object with keys: "
        '"sentiment" (one of: bullish, bearish, neutral), '
        '"confidence" (number 0 to 1). '
        f"News:\n{news_text[:8000]}"
    )
    try:
        obj = chat_json(
            [{"role": "user", "content": prompt}],
            model=config.LLM_MODEL,
            timeout=float(config.LLM_TIMEOUT_SECONDS),
        )
        sentiment = str(obj.get("sentiment", "neutral")).lower()
        if sentiment not in ("bullish", "bearish", "neutral"):
            return "neutral"
        conf = obj.get("confidence")
        if isinstance(conf, (int, float)) and conf < config.LLM_MIN_CONFIDENCE:
            return "neutral"
        return sentiment
    except LLMClientError as e:
        if e.status_code == 429:
            logger.warning(
                "get_sentiment rate limited (429); using neutral (%s)",
                e,
            )
        else:
            logger.warning("get_sentiment failed (%s); using neutral", e)
        return "neutral"
    except Exception as e:
        logger.warning("get_sentiment failed (%s); using neutral", e)
        return "neutral"

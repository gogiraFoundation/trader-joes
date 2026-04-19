"""Fetch recent headlines for sentiment input (stub + RSS)."""

from __future__ import annotations

import logging
import urllib.parse

import requests
from defusedxml.ElementTree import fromstring as etree_fromstring

logger = logging.getLogger("tr_bot")


def fetch_latest_news(query: str = "cryptocurrency", max_items: int = 8) -> str:
    """
    Pull Google News RSS titles for `query`. Returns a single newline-separated block.
    On failure, returns a neutral placeholder so the bot can still run.
    """
    try:
        q = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        root = etree_fromstring(r.content)
        titles: list[str] = []
        for item in root.findall(".//item"):
            t = item.findtext("title")
            if t:
                titles.append(t.strip())
            if len(titles) >= max_items:
                break
        if not titles:
            return "No major headlines; assume neutral context."
        return "\n".join(titles)
    except Exception as e:
        logger.warning("fetch_latest_news failed: %s", e)
        return "News unavailable; assume neutral context."


def news_for_symbol(symbol: str) -> str:
    base = symbol.split("/")[0] if "/" in symbol else symbol
    return fetch_latest_news(query=f"{base} market")

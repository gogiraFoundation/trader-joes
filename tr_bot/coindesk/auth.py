"""Shared auth header builders for CoinDesk HTTP clients."""

from __future__ import annotations

from tr_bot import config


def build_auth_headers(api_key: str | None = None) -> dict[str, str]:
    key = (api_key if api_key is not None else config.COINDESK_API_KEY) or ""
    mode = config.COINDESK_AUTH_MODE
    h: dict[str, str] = {"Accept": "application/json"}
    if not key:
        return h
    if mode in ("bearer", "both"):
        h["Authorization"] = f"Bearer {key}"
    if mode in ("x_api_key", "both"):
        h["x-api-key"] = key
    if mode == "apikey_header":
        h["Authorization"] = f"Apikey {key}"
    return h

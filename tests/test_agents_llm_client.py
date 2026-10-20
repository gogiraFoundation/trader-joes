"""LLM client JSON parsing (no network)."""

from __future__ import annotations

import pytest

from tr_bot.agents.llm_client import LLMClientError, parse_json_content, _require_https_base_url


def test_parse_plain_json() -> None:
    assert parse_json_content('{"sentiment":"neutral","confidence":0.9}')["sentiment"] == "neutral"


def test_parse_whole_fenced_json() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert parse_json_content(raw)["a"] == 1


def test_parse_fenced_with_prose_prefix_raises() -> None:
    raw = 'Here you go:\n```json\n{"a": 1}\n```\n'
    with pytest.raises(LLMClientError):
        parse_json_content(raw)


def test_parse_embedded_object_raises() -> None:
    raw = 'prefix {"b": 2} suffix'
    with pytest.raises(LLMClientError):
        parse_json_content(raw)


def test_parse_empty_raises() -> None:
    with pytest.raises(LLMClientError):
        parse_json_content("   ")


def test_require_https_base_url() -> None:
    _require_https_base_url("https://api.openai.com/v1")
    with pytest.raises(LLMClientError, match="https"):
        _require_https_base_url("http://evil.example/v1")

"""Strip sensitive keys from notification payloads."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"(secret|password|token|api[_-]?key|authorization)", re.I)
_MAX_STR = 500


def sanitize(obj: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "<max_depth>"
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        if len(obj) > _MAX_STR:
            return obj[:_MAX_STR] + "…"
        return obj
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY.search(k):
                out[k] = "<redacted>"
            else:
                out[str(k)] = sanitize(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize(x, depth + 1) for x in obj[:50]]
    return str(obj)[:_MAX_STR]

"""Security helpers: validation, safe paths, log redaction."""

from __future__ import annotations

import re
from pathlib import Path


_CCXT_PAIR = re.compile(r"^[A-Z0-9]{1,20}/[A-Z0-9]{1,20}$", re.I)


def is_valid_ccxt_spot_symbol(symbol: str) -> bool:
    """CCXT pairs like BTC/USDT; tickers without slash (e.g. SPY) allowed for Yahoo-only."""
    if not symbol or len(symbol) > 64:
        return False
    s = symbol.strip()
    if "/" not in s:
        return bool(re.match(r"^[A-Z0-9][A-Z0-9.-]{0,31}$", s, re.I))
    return bool(_CCXT_PAIR.match(s))


def collapse_artifact_dir_duplication(artifact_root: Path, path: Path) -> Path:
    """
    If ``path`` is under ``artifact_root`` but the relative tail starts with the
    same name as ``artifact_root`` (e.g. ``.../artifacts/artifacts/foo``), drop
    that duplicate segment. Idempotent for already-normal paths.
    """
    root = artifact_root.resolve()
    p = path.resolve()
    try:
        rel = p.relative_to(root)
    except ValueError:
        return p
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == root.name:
        return (root / Path(*parts[1:])).resolve()
    return p


def resolve_path_under_artifact_dir(artifact_root: Path, relative_or_abs: str) -> Path:
    """
    Resolve a path under artifact_root when relative.

    If the relative path repeats the artifact directory's final segment (e.g.
    ARTIFACTS_DIR=.../artifacts and value ``artifacts/regime_model.joblib``),
    that leading segment is dropped so paths are not doubled to
    ``.../artifacts/artifacts/...``.

    Absolute paths under the same root are also collapsed via
    :func:`collapse_artifact_dir_duplication`.
    """
    s = (relative_or_abs or "").strip()
    if not s:
        return Path()
    root = artifact_root.resolve()
    p = Path(s)
    if p.is_absolute():
        return collapse_artifact_dir_duplication(root, p)
    parts = p.parts
    if len(parts) >= 2 and parts[0] == root.name:
        p = Path(*parts[1:])
    out = (root / p).resolve()
    return collapse_artifact_dir_duplication(root, out)


def safe_artifact_path(base_dir: Path, name: str) -> Path:
    """
    Resolve a file path under base_dir only (no path traversal).
    `name` must be a single filename or relative path without '..'.
    """
    base = base_dir.resolve()
    p = (base / name).resolve()
    try:
        p.relative_to(base)
    except ValueError as e:
        raise ValueError("path escapes artifact directory") from e
    return p


def redact_secrets(msg: str) -> str:
    """Best-effort redaction for logs (never log raw keys)."""
    out = msg
    for pat, repl in (
        (r"api[_-]?key[\"']?\s*[:=]\s*[\"']?[\w-]{8,}", "api_key=<redacted>"),
        (r"secret[\"']?\s*[:=]\s*[\"']?[\w-]{8,}", "secret=<redacted>"),
        (r"Bearer\s+[\w.-]+", "Bearer <redacted>"),
    ):
        out = re.sub(pat, repl, out, flags=re.I)
    return out

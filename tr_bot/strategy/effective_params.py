"""Merged strategy profile params + config fallbacks for overlay keys."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tr_bot import config
from tr_bot.strategy import deployment_io as dep
from tr_bot.strategy.profile import OVERLAY_PARAM_KEYS


def effective_params_from_config_and_profile(
    artifacts_root: Path | None = None,
) -> dict[str, Any]:
    """Snapshot tunables: profile params merged with live config for keys not in profile."""
    root = artifacts_root or Path(config.ARTIFACTS_DIR).resolve()
    tier = str(config.DEPLOYMENT_TIER)
    ptr = dep.read_pointer(root, tier)
    params: dict[str, Any] = {}
    if ptr and ptr.get("profile_id"):
        pid = str(ptr["profile_id"]).strip()
        doc = dep.read_json_file(dep.strategy_profile_path(root, pid))
        if isinstance(doc, dict) and isinstance(doc.get("params"), dict):
            params.update(doc["params"])
    for k in OVERLAY_PARAM_KEYS:
        if k in params:
            continue
        if hasattr(config, k):
            params[k] = getattr(config, k)
    return params

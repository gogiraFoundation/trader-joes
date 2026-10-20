"""
Decision audit JSONL schema (DECISION_AUDIT_JSONL_PATH).

Each line is one JSON object:
- schema_version: int (1)
- loop: int — engine loop index
- utc: str — ISO-8601 UTC timestamp
- deployment_tier: str — paper | live_canary | live_full
- profile_id: str | null — ACTIVE_STRATEGY_PROFILE_ID when set
- by_symbol: dict — per-symbol evaluation snapshot (engine-specific)
"""

from __future__ import annotations

DECISION_AUDIT_SCHEMA_VERSION = 2

# schema_version 2 by_symbol entries may include:
#   legacy / smc / shadow dual views when STRATEGY_ENGINE=shadow


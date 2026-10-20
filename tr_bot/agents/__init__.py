"""Grok/OpenAI-compatible advisory agents (supervisor + fund manager).

Agents may veto, notify, or take allowlisted control actions. They must not
open trades or call execute_trade.
"""

from __future__ import annotations

from tr_bot.agents.runner import run_agents_job

__all__ = ["run_agents_job"]

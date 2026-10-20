"""TradeProposal dataclass for SMC / scored pipeline output."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Direction = Literal["buy", "sell", "hold"]


@dataclass
class TradeProposal:
    direction: Direction
    score: float
    stop: float | None = None
    target: float | None = None
    signal_branch: str = "smc"
    pipeline_steps: dict[str, Any] = field(default_factory=dict)
    score_components: dict[str, float] = field(default_factory=dict)
    rationale: str = ""
    blocked_gate: str | None = None
    smc_factors: list[str] = field(default_factory=list)
    entry_price: float | None = None
    risk_reward: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_actionable(self) -> bool:
        return self.direction in ("buy", "sell") and self.blocked_gate is None

"""Shared SMC types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StructureBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class SmcFinding:
    kind: str
    direction: str  # bullish | bearish | neutral
    index: int
    price_low: float
    price_high: float
    mitigated: bool = False
    confidence: float = 0.5
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "index": self.index,
            "price_low": self.price_low,
            "price_high": self.price_high,
            "mitigated": self.mitigated,
            "confidence": self.confidence,
            "meta": dict(self.meta),
        }


@dataclass
class StructureState:
    bias: StructureBias
    last_swing_high: float | None
    last_swing_low: float | None
    last_swing_high_idx: int | None
    last_swing_low_idx: int | None
    swing_highs: list[tuple[int, float]] = field(default_factory=list)
    swing_lows: list[tuple[int, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bias": self.bias.value,
            "last_swing_high": self.last_swing_high,
            "last_swing_low": self.last_swing_low,
            "last_swing_high_idx": self.last_swing_high_idx,
            "last_swing_low_idx": self.last_swing_low_idx,
            "n_swing_highs": len(self.swing_highs),
            "n_swing_lows": len(self.swing_lows),
        }

"""Canonical immutable exit plan shared by engine, backtest, and replay."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

import pandas as pd

ExitMode = Literal["atr", "structural", "hybrid"]
ExitPolicy = Literal["software_polled", "exchange_native"]
Side = Literal["long", "short"]

PLAN_VERSION = 1


@dataclass(frozen=True)
class ExitPlan:
    """Frozen exit levels used for sizing and execution."""

    side: Side
    entry_price: float
    stop_price: float
    take_price: float
    stop_distance_quote: float
    exit_mode: ExitMode
    exit_policy: ExitPolicy
    plan_version: int = PLAN_VERSION

    def stop_distance_usdt(self, quote_usdt: float) -> float:
        return float(self.stop_distance_quote) * float(quote_usdt)

    def check_bar(self, bar: pd.Series) -> tuple[bool, str | None]:
        low = float(bar["low"])
        high = float(bar["high"])
        stop = float(self.stop_price)
        take = float(self.take_price)
        if self.side == "long":
            if low <= stop:
                return True, "loss"
            if high >= take:
                return True, "win"
        else:
            if high >= stop:
                return True, "loss"
            if low <= take:
                return True, "win"
        return False, None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ExitPlan:
        return cls(
            side=str(raw["side"]),  # type: ignore[arg-type]
            entry_price=float(raw["entry_price"]),
            stop_price=float(raw["stop_price"]),
            take_price=float(raw["take_price"]),
            stop_distance_quote=float(raw["stop_distance_quote"]),
            exit_mode=str(raw.get("exit_mode", "atr")),  # type: ignore[arg-type]
            exit_policy=str(raw.get("exit_policy", "software_polled")),  # type: ignore[arg-type]
            plan_version=int(raw.get("plan_version", PLAN_VERSION)),
        )

    def config_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atr_levels(side: Side, entry: float, atr: float) -> tuple[float, float]:
    if side == "long":
        return entry - atr, entry + 1.5 * atr
    return entry + atr, entry - 1.5 * atr


def _validate_side_prices(side: Side, entry: float, stop: float, take: float) -> None:
    if not all(math.isfinite(x) and x > 0 for x in (entry, stop, take)):
        raise ValueError("entry/stop/take must be finite and positive")
    if side == "long":
        if stop >= entry:
            raise ValueError("long stop must be below entry")
        if take <= entry:
            raise ValueError("long take must be above entry")
    else:
        if stop <= entry:
            raise ValueError("short stop must be above entry")
        if take >= entry:
            raise ValueError("short take must be below entry")


def build_exit_plan(
    *,
    side: Side,
    entry_price: float,
    atr: float,
    exit_mode: ExitMode = "atr",
    exit_policy: ExitPolicy = "software_polled",
    structural_stop: float | None = None,
    structural_target: float | None = None,
) -> ExitPlan:
    """Build a validated exit plan from entry context and optional structural levels."""
    if atr <= 0 or not math.isfinite(atr):
        raise ValueError("atr must be finite and positive")
    if not math.isfinite(entry_price) or entry_price <= 0:
        raise ValueError("entry_price must be finite and positive")

    atr_stop, atr_take = _atr_levels(side, entry_price, atr)
    mode = (exit_mode or "atr").strip().lower()
    if mode not in ("atr", "structural", "hybrid"):
        mode = "atr"

    stop = atr_stop
    take = atr_take
    if mode in ("structural", "hybrid") and structural_stop is not None:
        stop = float(structural_stop)
        take = float(structural_target) if structural_target is not None else atr_take

    _validate_side_prices(side, entry_price, stop, take)
    stop_distance = abs(entry_price - stop)
    if stop_distance <= 0:
        raise ValueError("stop_distance must be positive")

    policy = (exit_policy or "software_polled").strip().lower()
    if policy not in ("software_polled", "exchange_native"):
        policy = "software_polled"

    return ExitPlan(
        side=side,
        entry_price=float(entry_price),
        stop_price=float(stop),
        take_price=float(take),
        stop_distance_quote=float(stop_distance),
        exit_mode=mode,  # type: ignore[arg-type]
        exit_policy=policy,  # type: ignore[arg-type]
    )


def reconstruct_exit_plan_from_position(
    *,
    side: str,
    entry_price: float,
    atr: float,
    exit_mode: str = "atr",
    exit_policy: str = "software_polled",
    structural_stop: float | None = None,
    structural_target: float | None = None,
    exit_plan_dict: dict[str, Any] | None = None,
) -> ExitPlan:
    """Load stored plan or rebuild from legacy OpenPosition fields."""
    if exit_plan_dict:
        try:
            return ExitPlan.from_dict(exit_plan_dict)
        except (KeyError, TypeError, ValueError):
            pass
    s: Side = "long" if str(side).lower() == "long" else "short"
    mode: ExitMode = "atr"
    em = (exit_mode or "atr").strip().lower()
    if em in ("atr", "structural", "hybrid"):
        mode = em  # type: ignore[assignment]
    pol: ExitPolicy = "software_polled"
    ep = (exit_policy or "software_polled").strip().lower()
    if ep in ("software_polled", "exchange_native"):
        pol = ep  # type: ignore[assignment]
    return build_exit_plan(
        side=s,
        entry_price=entry_price,
        atr=atr,
        exit_mode=mode,
        exit_policy=pol,
        structural_stop=structural_stop,
        structural_target=structural_target,
    )


def simulate_software_polled_exit_price(
    *,
    side: Side,
    stop_price: float,
    take_price: float,
    bar: pd.Series,
    outcome: str,
) -> float:
    """
    Conservative OHLC fill for software-polled exits (slippage applied separately in pnl_costs).
    Same-bar ambiguity: stop takes precedence over take.
    """
    low = float(bar["low"])
    high = float(bar["high"])
    close = float(bar["close"])
    open_px = float(bar["open"])
    stop = float(stop_price)
    take = float(take_price)

    if side == "long":
        if outcome == "loss" and low <= stop:
            return min(open_px, stop)
        if outcome == "win" and high >= take:
            return min(close, take)
    else:
        if outcome == "loss" and high >= stop:
            return max(open_px, stop)
        if outcome == "win" and low <= take:
            return max(close, take)
    return close

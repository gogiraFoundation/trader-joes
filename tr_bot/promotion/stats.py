"""Block-bootstrap statistical gates for promotion."""

from __future__ import annotations

import random
from typing import Any, Callable

from tr_bot.promotion.gates import GateOutcome


def _net_pnls(rows: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for r in rows:
        if r.get("outcome") not in ("win", "loss"):
            continue
        try:
            out.append(float(r.get("net_pnl_usdt", 0) or 0))
        except (TypeError, ValueError):
            continue
    return out


def _profit_factor(values: list[float]) -> float:
    gross_win = sum(v for v in values if v > 0)
    gross_loss = sum(-v for v in values if v < 0)
    if gross_loss <= 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def block_bootstrap_metric(
    values: list[float],
    *,
    block_size: int,
    n_resamples: int,
    metric_fn: Callable[[list[float]], float],
    seed: int = 42,
) -> list[float]:
    """Moving-block bootstrap resamples preserving local dependence."""
    n = len(values)
    if n == 0:
        return []
    bs = max(1, min(block_size, n))
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(max(1, n_resamples)):
        resampled: list[float] = []
        while len(resampled) < n:
            start = rng.randrange(0, n)
            for j in range(bs):
                resampled.append(values[(start + j) % n])
                if len(resampled) >= n:
                    break
        samples.append(float(metric_fn(resampled[:n])))
    return samples


def gate_bootstrap_expectancy(
    rows: list[dict[str, Any]],
    *,
    min_lower_bound: float | None,
    block_size: int,
    n_resamples: int,
    alpha: float,
    seed: int = 42,
) -> GateOutcome:
    if min_lower_bound is None:
        return GateOutcome(name="bootstrap_expectancy", passed=True, detail="disabled")
    pnls = _net_pnls(rows)
    if len(pnls) < 2:
        return GateOutcome(
            name="bootstrap_expectancy",
            passed=False,
            detail="insufficient_trades_for_bootstrap",
        )

    def _expectancy(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    samples = block_bootstrap_metric(
        pnls,
        block_size=block_size,
        n_resamples=n_resamples,
        metric_fn=_expectancy,
        seed=seed,
    )
    samples.sort()
    idx = max(0, int(alpha * len(samples)) - 1)
    lower = samples[idx] if samples else 0.0
    ok = lower >= float(min_lower_bound)
    return GateOutcome(
        name="bootstrap_expectancy",
        passed=ok,
        detail=f"expectancy_lower={lower:.4f} need>={min_lower_bound}",
    )


def gate_bootstrap_profit_factor(
    rows: list[dict[str, Any]],
    *,
    min_pf_lower: float | None,
    block_size: int,
    n_resamples: int,
    alpha: float,
    seed: int = 42,
) -> GateOutcome:
    if min_pf_lower is None:
        return GateOutcome(name="bootstrap_profit_factor", passed=True, detail="disabled")
    pnls = _net_pnls(rows)
    if len(pnls) < 2:
        return GateOutcome(
            name="bootstrap_profit_factor",
            passed=False,
            detail="insufficient_trades_for_bootstrap",
        )
    samples = block_bootstrap_metric(
        pnls,
        block_size=block_size,
        n_resamples=n_resamples,
        metric_fn=_profit_factor,
        seed=seed + 1,
    )
    samples.sort()
    idx = max(0, int(alpha * len(samples)) - 1)
    lower = samples[idx] if samples else 0.0
    ok = lower >= float(min_pf_lower)
    return GateOutcome(
        name="bootstrap_profit_factor",
        passed=ok,
        detail=f"pf_lower={lower:.4f} need>={min_pf_lower}",
    )

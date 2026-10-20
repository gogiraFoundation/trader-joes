"""Composite trade scoring (HIATP-style weighted components)."""

from __future__ import annotations

from typing import Any


# MVP weights (~HIATP §4.3 subset, renormalized to 1.0)
DEFAULT_WEIGHTS: dict[str, float] = {
    "htf_alignment": 0.20,
    "regime_suitability": 0.10,
    "structure": 0.15,
    "smc_confluence": 0.20,
    "ta_confirmation": 0.15,
    "risk_reward": 0.10,
    "liquidity": 0.05,
    "price_action": 0.03,
    "quantitative": 0.02,
}


def score_htf_alignment(htf_bias: str, intended: str) -> float:
    if htf_bias == "neutral" or intended == "hold":
        return 50.0
    if htf_bias == intended:
        return 100.0
    if (htf_bias == "bullish" and intended == "buy") or (
        htf_bias == "bearish" and intended == "sell"
    ):
        return 100.0
    # map buy<->bullish
    if htf_bias == "bullish" and intended == "buy":
        return 100.0
    if htf_bias == "bearish" and intended == "sell":
        return 100.0
    return 0.0


def _dir_match(bias: str, intended: str) -> bool:
    if intended == "buy":
        return bias in ("bullish", "buy")
    if intended == "sell":
        return bias in ("bearish", "sell")
    return False


def score_htf(htf_bias: str, intended: str) -> float:
    if intended == "hold" or htf_bias == "neutral":
        return 50.0
    return 100.0 if _dir_match(htf_bias, intended) else 0.0


def score_regime(ml_regime: str, intended: str) -> float:
    """SMC prefers defined structure; both range and trend can trade; unclear → lower."""
    if not ml_regime:
        return 50.0
    if ml_regime in ("trend", "range"):
        return 80.0
    return 40.0


def score_structure(structure_bias: str, intended: str) -> float:
    if structure_bias == "neutral":
        return 40.0
    return 100.0 if _dir_match(structure_bias, intended) else 20.0


def score_smc_confluence(n_factors: int) -> float:
    if n_factors >= 3:
        return 100.0
    if n_factors == 2:
        return 70.0
    if n_factors == 1:
        return 30.0
    return 0.0


def score_ta(ta_bias: str, intended: str) -> float:
    if ta_bias == "neutral":
        return 50.0
    return 100.0 if _dir_match(ta_bias, intended) else 20.0


def score_rr(rr: float | None) -> float:
    if rr is None:
        return 40.0
    if rr >= 3.0:
        return 100.0
    if rr >= 2.0:
        return 70.0
    if rr >= 1.5:
        return 50.0
    return 30.0


def score_liquidity(has_sweep_or_pool: bool, aligned: bool) -> float:
    if has_sweep_or_pool and aligned:
        return 100.0
    if has_sweep_or_pool:
        return 60.0
    return 40.0


def score_price_action(confirmed: bool) -> float:
    return 100.0 if confirmed else 40.0


def score_quantitative(confidence: float) -> float:
    c = max(0.0, min(1.0, float(confidence)))
    if c >= 0.7:
        return 100.0
    if c >= 0.5:
        return 60.0
    return 20.0


def composite_score(
    components: dict[str, float],
    *,
    weights: dict[str, float] | None = None,
) -> float:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    total_w = 0.0
    acc = 0.0
    for k, wt in w.items():
        if k not in components:
            continue
        total_w += wt
        acc += wt * float(components[k])
    if total_w <= 0:
        return 0.0
    return round(acc / total_w, 2)


def build_components(
    *,
    htf_bias: str,
    intended: str,
    ml_regime: str,
    structure_bias: str,
    n_smc_factors: int,
    ta_bias: str,
    rr: float | None,
    liquidity_ok: bool = False,
    liquidity_aligned: bool = False,
    price_action_ok: bool = False,
    quant_confidence: float = 0.5,
) -> dict[str, float]:
    return {
        "htf_alignment": score_htf(htf_bias, intended),
        "regime_suitability": score_regime(ml_regime, intended),
        "structure": score_structure(structure_bias, intended),
        "smc_confluence": score_smc_confluence(n_smc_factors),
        "ta_confirmation": score_ta(ta_bias, intended),
        "risk_reward": score_rr(rr),
        "liquidity": score_liquidity(liquidity_ok, liquidity_aligned),
        "price_action": score_price_action(price_action_ok),
        "quantitative": score_quantitative(quant_confidence),
    }


def params_from_config(cfg: Any) -> dict[str, Any]:
    """Extract scoring / pipeline params from config module or namespace."""
    return {
        "min_trade_score": float(getattr(cfg, "SMC_MIN_TRADE_SCORE", 65)),
        "min_smc_factors": int(getattr(cfg, "SMC_MIN_SMC_FACTORS", 2)),
        "htf_tf": str(getattr(cfg, "SMC_HTF_TIMEFRAME", "4h")),
        "primary_tf": str(getattr(cfg, "SMC_PRIMARY_TIMEFRAME", "1h")),
        "entry_tf": str(getattr(cfg, "SMC_ENTRY_TIMEFRAME", "15m")),
        "exec_tf": str(getattr(cfg, "SMC_EXEC_TIMEFRAME", "5m")),
        "exit_mode": str(getattr(cfg, "EXIT_MODE", "atr")),
    }

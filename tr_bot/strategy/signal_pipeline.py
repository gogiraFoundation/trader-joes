"""
Deterministic SMC signal pipeline shared by live engine and backtest.

Gates (expanded to ~10 steps; early exits set blocked_gate):
  1 HTF bias
  2 Market regime suitability
  3 Market structure
  4 Liquidity analysis
  5 SMC confluence (OB/FVG/OTE/BOS/MSS)
  6 Price action confirmation
  7 Technical confirmation
  8 Quantitative validation
  9 Trade scoring
 10 Risk / min-score / RR validation
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tr_bot.indicators import compute_all_indicators
from tr_bot.signals import ta_confirmation_bias
from tr_bot.smc.bos_mss import detect_bos_mss
from tr_bot.smc.fvg import detect_fvgs
from tr_bot.smc.liquidity import detect_liquidity_pools, detect_sweeps
from tr_bot.smc.order_blocks import detect_order_blocks
from tr_bot.smc.structure import structure_state
from tr_bot.smc.types import StructureBias
from tr_bot.smc.zones import analyze_zones
from tr_bot.strategy.market_context import MarketContext, empty_context
from tr_bot.strategy.multi_tf import configured_timeframes, pick_exec_frame
from tr_bot.strategy.scoring import build_components, composite_score, params_from_config
from tr_bot.strategy.trade_proposal import TradeProposal


def _bias_to_direction(bias: str) -> str:
    if bias in ("bullish", "buy"):
        return "buy"
    if bias in ("bearish", "sell"):
        return "sell"
    return "hold"


def _ensure_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    need = ("rsi", "ema20", "atr")
    if all(c in df.columns for c in need):
        return df
    return compute_all_indicators(df)


def _count_smc_factors(
    *,
    direction: str,
    fvgs: list,
    obs: list,
    sweeps: list,
    bos_mss: list,
    zones: list,
) -> tuple[int, list[str]]:
    wanted = "bullish" if direction == "buy" else "bearish"
    names: list[str] = []
    if any(f.direction == wanted and not f.mitigated for f in fvgs[-5:]):
        names.append("fvg")
    if any(f.direction == wanted and not f.mitigated for f in obs[-5:]):
        names.append("order_block")
    if any(f.direction == wanted for f in sweeps[-3:]):
        names.append("liquidity_sweep")
    if any(f.direction == wanted for f in bos_mss[-3:]):
        names.append(bos_mss[-1].kind if bos_mss else "bos")
    for z in zones:
        if z.kind == "ote" and z.direction == wanted and z.meta.get("in_zone"):
            names.append("ote")
        if z.kind == "premium_discount":
            zone = (z.meta or {}).get("zone")
            if direction == "buy" and zone == "discount":
                names.append("discount")
            if direction == "sell" and zone == "premium":
                names.append("premium")
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return len(uniq), uniq


def _structural_levels(
    df: pd.DataFrame,
    direction: str,
    state,
) -> tuple[float | None, float | None, float | None]:
    entry = float(df["close"].iloc[-1])
    atr = float(df["atr"].iloc[-1]) if "atr" in df.columns and not pd.isna(df["atr"].iloc[-1]) else entry * 0.01
    if direction == "buy":
        stop = state.last_swing_low if state.last_swing_low is not None else entry - atr
        target = state.last_swing_high if state.last_swing_high is not None else entry + 1.5 * atr
        if stop is not None and stop >= entry:
            stop = entry - atr
        if target is not None and target <= entry:
            target = entry + 1.5 * atr
    else:
        stop = state.last_swing_high if state.last_swing_high is not None else entry + atr
        target = state.last_swing_low if state.last_swing_low is not None else entry - 1.5 * atr
        if stop is not None and stop <= entry:
            stop = entry + atr
        if target is not None and target >= entry:
            target = entry - 1.5 * atr
    return entry, float(stop) if stop is not None else None, float(target) if target is not None else None


def _rr(entry: float, stop: float | None, target: float | None, direction: str) -> float | None:
    if stop is None or target is None or entry <= 0:
        return None
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        return None
    return reward / risk


def analyze_symbol(
    symbol: str,
    *,
    dfs_by_tf: dict[str, pd.DataFrame],
    context: MarketContext | None = None,
    params: dict[str, Any] | None = None,
) -> TradeProposal:
    """
    Run gated SMC pipeline. Always returns a TradeProposal (may be hold / blocked).
    """
    from tr_bot import config as cfg

    p = params or params_from_config(cfg)
    ctx = context or empty_context()
    steps: dict[str, Any] = {}
    min_score = float(p.get("min_trade_score", 65))
    min_factors = int(p.get("min_smc_factors", 2))

    htf_key = str(p.get("htf_tf") or configured_timeframes()[0])
    exec_tf, exec_df = pick_exec_frame(dfs_by_tf)
    htf_df = dfs_by_tf.get(htf_key)
    if htf_df is None or htf_df.empty:
        # fall back to primary or exec
        for k in configured_timeframes():
            if k in dfs_by_tf and dfs_by_tf[k] is not None and not dfs_by_tf[k].empty:
                htf_df = dfs_by_tf[k]
                htf_key = k
                break
    if exec_df is None or exec_df.empty or len(exec_df) < 20:
        return TradeProposal(
            direction="hold",
            score=0.0,
            blocked_gate="insufficient_data",
            pipeline_steps={"data": "insufficient"},
            rationale="Not enough OHLCV for SMC analysis",
        )

    exec_df = _ensure_indicators(exec_df)
    htf_df = _ensure_indicators(htf_df) if htf_df is not None and not htf_df.empty else exec_df

    # Step 1: HTF bias
    htf_state = structure_state(htf_df)
    htf_bias = htf_state.bias.value
    steps["htf_bias"] = htf_bias
    steps["htf_tf"] = htf_key

    # Step 2: Regime
    steps["market_regime"] = ctx.ml_regime
    steps["regime_confidence"] = ctx.regime_confidence
    if ctx.vol_regime == "extreme" and float(ctx.regime_confidence) < 0.4:
        return TradeProposal(
            direction="hold",
            score=0.0,
            blocked_gate="regime_unsuitable",
            pipeline_steps=steps,
            rationale="Extreme vol with low regime confidence",
        )

    # Intended direction from HTF + exec structure
    exec_state = structure_state(exec_df)
    steps["structure"] = exec_state.bias.value
    intended = _bias_to_direction(htf_bias)
    if intended == "hold":
        intended = _bias_to_direction(exec_state.bias.value)
    if intended == "hold":
        # weak MSS/BOS can still set direction
        bos = detect_bos_mss(exec_df)
        if bos:
            intended = _bias_to_direction(bos[-1].direction)
        steps["bos_mss_seed"] = [b.to_dict() for b in bos[-2:]]

    if intended == "hold":
        return TradeProposal(
            direction="hold",
            score=0.0,
            blocked_gate="no_directional_bias",
            pipeline_steps=steps,
            rationale="No HTF/structure directional bias",
        )

    # Step 3 already captured as structure; reject opposed structure strongly
    if exec_state.bias != StructureBias.NEUTRAL:
        if intended == "buy" and exec_state.bias == StructureBias.BEARISH:
            return TradeProposal(
                direction="hold",
                score=20.0,
                blocked_gate="structure_conflict",
                pipeline_steps=steps,
                rationale="Exec structure bearish vs intended buy",
            )
        if intended == "sell" and exec_state.bias == StructureBias.BULLISH:
            return TradeProposal(
                direction="hold",
                score=20.0,
                blocked_gate="structure_conflict",
                pipeline_steps=steps,
                rationale="Exec structure bullish vs intended sell",
            )

    # Step 4: Liquidity
    pools = detect_liquidity_pools(exec_df)
    sweeps = detect_sweeps(exec_df)
    liq_aligned = any(
        (s.direction == "bullish" and intended == "buy")
        or (s.direction == "bearish" and intended == "sell")
        for s in sweeps
    )
    steps["liquidity"] = {
        "pools": len(pools),
        "sweeps": [s.to_dict() for s in sweeps[-3:]],
        "aligned": liq_aligned,
    }

    # Step 5: SMC confluence
    fvgs = detect_fvgs(exec_df)
    obs = detect_order_blocks(exec_df)
    bos_mss = detect_bos_mss(exec_df)
    zones = analyze_zones(exec_df)
    n_factors, factor_names = _count_smc_factors(
        direction=intended,
        fvgs=fvgs,
        obs=obs,
        sweeps=sweeps,
        bos_mss=bos_mss,
        zones=zones,
    )
    steps["smc_validation"] = {"n_factors": n_factors, "factors": factor_names}
    if n_factors < min_factors:
        return TradeProposal(
            direction="hold",
            score=float(30.0 * n_factors),
            blocked_gate="smc_confluence",
            pipeline_steps=steps,
            smc_factors=factor_names,
            rationale=f"Need >= {min_factors} SMC factors, got {n_factors}",
        )

    # Step 6: Price action — last candle closes in direction / rejection
    last = exec_df.iloc[-1]
    o, c = float(last["open"]), float(last["close"])
    pa_ok = (intended == "buy" and c >= o) or (intended == "sell" and c <= o)
    # sweep already counts as PA confirmation
    if liq_aligned:
        pa_ok = True
    steps["price_action"] = {"confirmed": pa_ok}

    # Step 7: TA confirmation
    ta_bias = ta_confirmation_bias(exec_df)
    steps["technical_confirmation"] = ta_bias
    if ta_bias != "neutral":
        if intended == "buy" and ta_bias == "bearish":
            return TradeProposal(
                direction="hold",
                score=40.0,
                blocked_gate="ta_conflict",
                pipeline_steps=steps,
                smc_factors=factor_names,
                rationale="TA confirmation oppose buy",
            )
        if intended == "sell" and ta_bias == "bullish":
            return TradeProposal(
                direction="hold",
                score=40.0,
                blocked_gate="ta_conflict",
                pipeline_steps=steps,
                smc_factors=factor_names,
                rationale="TA confirmation oppose sell",
            )

    # Step 8: Quantitative — regime confidence threshold soft gate
    quant_conf = float(ctx.regime_confidence)
    steps["quantitative"] = {"regime_confidence": quant_conf}
    if quant_conf < 0.35:
        return TradeProposal(
            direction="hold",
            score=35.0,
            blocked_gate="quantitative",
            pipeline_steps=steps,
            smc_factors=factor_names,
            rationale="Regime confidence too low",
        )

    # Levels + RR
    entry, stop, target = _structural_levels(exec_df, intended, exec_state)
    rr = _rr(entry or 0.0, stop, target, intended)
    steps["levels"] = {"entry": entry, "stop": stop, "target": target, "rr": rr}

    # Step 9–10: Score + thresholds
    components = build_components(
        htf_bias=htf_bias,
        intended=intended,
        ml_regime=ctx.ml_regime,
        structure_bias=exec_state.bias.value,
        n_smc_factors=n_factors,
        ta_bias=ta_bias,
        rr=rr,
        liquidity_ok=bool(sweeps or pools),
        liquidity_aligned=liq_aligned,
        price_action_ok=pa_ok,
        quant_confidence=quant_conf,
    )
    score = composite_score(components)
    score = round(score * ctx.soft_score_multiplier, 2)
    steps["trade_score"] = score
    steps["score_components"] = components
    steps["soft_multiplier"] = ctx.soft_score_multiplier

    if rr is not None and rr < 1.2:
        return TradeProposal(
            direction="hold",
            score=score,
            stop=stop,
            target=target,
            entry_price=entry,
            risk_reward=rr,
            blocked_gate="risk_reward",
            pipeline_steps=steps,
            score_components=components,
            smc_factors=factor_names,
            rationale=f"RR {rr:.2f} below 1.2",
        )

    if score < min_score:
        return TradeProposal(
            direction="hold",
            score=score,
            stop=stop,
            target=target,
            entry_price=entry,
            risk_reward=rr,
            blocked_gate="min_score",
            pipeline_steps=steps,
            score_components=components,
            smc_factors=factor_names,
            rationale=f"Score {score} < min {min_score}",
        )

    return TradeProposal(
        direction=intended,  # type: ignore[arg-type]
        score=score,
        stop=stop,
        target=target,
        entry_price=entry,
        risk_reward=rr,
        pipeline_steps=steps,
        score_components=components,
        smc_factors=factor_names,
        rationale=f"SMC {intended} score={score} factors={factor_names}",
        signal_branch="smc",
    )


def proposal_to_decision(proposal: TradeProposal | None) -> str:
    if proposal is None or not proposal.is_actionable:
        return "hold"
    return proposal.direction

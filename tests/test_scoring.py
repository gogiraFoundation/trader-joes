"""Tests for scoring + signal pipeline MVP."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tr_bot.indicators import compute_all_indicators
from tr_bot.signals import ta_confirmation_bias
from tr_bot.strategy.market_context import empty_context
from tr_bot.strategy.scoring import build_components, composite_score
from tr_bot.strategy.signal_pipeline import analyze_symbol, proposal_to_decision


def _df(n: int = 120, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.05, 0.4, n))
    # force some structure at the end
    close[-20:] = close[-21] + np.linspace(0, 8, 20)
    return compute_all_indicators(
        pd.DataFrame(
            {
                "timestamp": (np.arange(n) * 60_000).astype(np.int64),
                "open": close,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "volume": rng.integers(500, 2000, n).astype(float),
            }
        )
    )


def test_composite_score_weights() -> None:
    comps = build_components(
        htf_bias="bullish",
        intended="buy",
        ml_regime="trend",
        structure_bias="bullish",
        n_smc_factors=3,
        ta_bias="bullish",
        rr=2.5,
        liquidity_ok=True,
        liquidity_aligned=True,
        price_action_ok=True,
        quant_confidence=0.8,
    )
    s = composite_score(comps)
    assert 50 <= s <= 100


def test_ta_confirmation_bias() -> None:
    df = _df()
    bias = ta_confirmation_bias(df)
    assert bias in ("bullish", "bearish", "neutral")


def test_signal_pipeline_runs() -> None:
    df = _df(150)
    ctx = empty_context(ml_regime="trend", regime_confidence=0.7)
    dfs = {"4h": df, "1h": df, "15m": df, "5m": df}
    prop = analyze_symbol("BTC/USDT", dfs_by_tf=dfs, context=ctx, params={"min_trade_score": 30, "min_smc_factors": 1})
    assert prop is not None
    assert prop.direction in ("buy", "sell", "hold")
    assert isinstance(prop.score, float)
    # parity: same inputs → same score
    prop2 = analyze_symbol("BTC/USDT", dfs_by_tf=dfs, context=ctx, params={"min_trade_score": 30, "min_smc_factors": 1})
    assert prop.score == prop2.score
    assert prop.blocked_gate == prop2.blocked_gate
    d = proposal_to_decision(prop)
    assert d in ("buy", "sell", "hold")

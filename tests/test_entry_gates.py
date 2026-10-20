"""Post-decision entry gates and SMC fundamentals veto."""

from __future__ import annotations

import pandas as pd
import pytest

from tr_bot.strategy import entry_gates


def _last(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1000.0,
        "rsi": 20.0,
        "adx": 15.0,
        "ema20": 100.0,
        "ema50": 99.0,
    }
    base.update(kwargs)
    return base


@pytest.fixture(autouse=True)
def _safe_gate_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entry_gates.config, "USE_SIGNAL_STRENGTH_FILTER", False)
    monkeypatch.setattr(entry_gates.config, "USE_MICROSTRUCTURE_GUARD", False)
    monkeypatch.setattr(entry_gates.config, "MICRO_USE_SPREAD_DEPTH_GUARD", False)
    monkeypatch.setattr(entry_gates.config, "REGIME_MIN_CONFIDENCE", 0.55)
    monkeypatch.setattr(entry_gates.config, "PAPER_TRADING", True)


def test_low_regime_confidence_legacy() -> None:
    d, r = entry_gates.apply_post_decision_gates(
        decision="buy",
        reason=None,
        engine_mode="legacy",
        regime="range",
        signal="buy",
        regime_confidence=0.1,
        cooldown_active=False,
        last=_last(),
        prev=None,
        df=pd.DataFrame([{"close": 1.0}]),
        symbol="BTC/USDT",
        classify_volatility_fn=lambda _df: ("normal", None),
    )
    assert d == "hold"
    assert r == "low_regime_confidence"


def test_smc_skips_regime_confidence_gate() -> None:
    d, r = entry_gates.apply_post_decision_gates(
        decision="buy",
        reason=None,
        engine_mode="smc",
        regime="range",
        signal="buy",
        regime_confidence=0.1,
        cooldown_active=False,
        last=_last(),
        prev=None,
        df=pd.DataFrame([{"close": 1.0}]),
        symbol="BTC/USDT",
        classify_volatility_fn=lambda _df: ("normal", None),
    )
    assert d == "buy"
    assert r is None


def test_weak_signal_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entry_gates.config, "USE_SIGNAL_STRENGTH_FILTER", True)
    monkeypatch.setattr(entry_gates.config, "SIGNAL_RSI_OVERSOLD", 30.0)
    monkeypatch.setattr(entry_gates.config, "SIGNAL_MIN_RSI_DISTANCE", 5.0)

    d, r = entry_gates.apply_post_decision_gates(
        decision="buy",
        reason=None,
        engine_mode="legacy",
        regime="range",
        signal="buy",
        regime_confidence=0.9,
        cooldown_active=False,
        last=_last(rsi=28.0),  # distance 2 < 5
        prev=None,
        df=pd.DataFrame([{"close": 1.0}]),
        symbol="BTC/USDT",
        classify_volatility_fn=lambda _df: ("normal", None),
    )
    assert d == "hold"
    assert r == "weak_signal:rsi_distance_too_small"


def test_cooldown_active() -> None:
    d, r = entry_gates.apply_post_decision_gates(
        decision="buy",
        reason=None,
        engine_mode="smc",
        regime="range",
        signal="buy",
        regime_confidence=0.9,
        cooldown_active=True,
        last=_last(),
        prev=None,
        df=pd.DataFrame([{"close": 1.0}]),
        symbol="BTC/USDT",
        classify_volatility_fn=lambda _df: ("normal", None),
    )
    assert d == "hold"
    assert r == "cooldown_active"


def test_microstructure_guard_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entry_gates.config, "USE_MICROSTRUCTURE_GUARD", True)
    monkeypatch.setattr(entry_gates.config, "MICRO_MAX_BAR_RANGE_PCT", 1.0)

    d, r = entry_gates.apply_post_decision_gates(
        decision="buy",
        reason=None,
        engine_mode="smc",
        regime="range",
        signal="buy",
        regime_confidence=0.9,
        cooldown_active=False,
        last=_last(high=110.0, low=90.0, close=100.0),
        prev=None,
        df=pd.DataFrame([{"close": 1.0}]),
        symbol="BTC/USDT",
        classify_volatility_fn=lambda _df: ("normal", None),
    )
    assert d == "hold"
    assert r == "micro_range_too_wide"


def test_spread_depth_guard_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    d, r = entry_gates.apply_post_decision_gates(
        decision="buy",
        reason=None,
        engine_mode="smc",
        regime="range",
        signal="buy",
        regime_confidence=0.9,
        cooldown_active=False,
        last=_last(),
        prev=None,
        df=pd.DataFrame([{"close": 1.0}]),
        symbol="BTC/USDT",
        paper_trading=False,
        spread_depth_fn=lambda _s: (False, "micro_spread_too_wide"),
        classify_volatility_fn=lambda _df: ("normal", None),
    )
    assert d == "hold"
    assert r == "micro_spread_too_wide"


def test_vol_extreme_suppress() -> None:
    d, r = entry_gates.apply_post_decision_gates(
        decision="sell",
        reason=None,
        engine_mode="smc",
        regime="trend",
        signal="sell",
        regime_confidence=0.9,
        cooldown_active=False,
        last=_last(),
        prev=None,
        df=pd.DataFrame([{"close": 1.0}]),
        symbol="BTC/USDT",
        classify_volatility_fn=lambda _df: ("extreme", None),
    )
    assert d == "hold"
    assert r == "vol_extreme_suppress"


def test_hold_is_not_flipped_to_entry() -> None:
    d, r = entry_gates.apply_post_decision_gates(
        decision="hold",
        reason="pipeline",
        engine_mode="legacy",
        regime="range",
        signal="hold",
        regime_confidence=0.9,
        cooldown_active=False,
        last=_last(),
        prev=None,
        df=pd.DataFrame([{"close": 1.0}]),
        symbol="BTC/USDT",
        classify_volatility_fn=lambda _df: ("extreme", None),
    )
    assert d == "hold"
    assert r == "pipeline"


def test_smc_fundamentals_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot import engine

    class _Prop:
        direction = "buy"
        blocked_gate = None
        is_actionable = True
        score = 80
        smc_factors = ["ob"]

    class _RM:
        def predict_with_confidence(self, _df: object) -> tuple[str, float]:
            return "range", 0.9

    monkeypatch.setattr(engine.config, "USE_LLM", False)
    monkeypatch.setattr(engine.config, "USE_FUNDAMENTALS_FILTER", True)
    monkeypatch.setattr(engine.config, "CMC_GLOBAL_METRICS_ENABLED", False)
    monkeypatch.setattr(engine.config, "LOOP_BARS", 50)
    monkeypatch.setattr(engine.config, "TIMEFRAME", "5m")

    monkeypatch.setattr(
        "tr_bot.strategy.multi_tf.fetch_multi_tf",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "tr_bot.strategy.market_context.build_market_context",
        lambda **_k: object(),
    )
    monkeypatch.setattr(
        "tr_bot.strategy.signal_pipeline.analyze_symbol",
        lambda *_a, **_k: _Prop(),
    )
    monkeypatch.setattr(
        "tr_bot.strategy.signal_pipeline.proposal_to_decision",
        lambda _p: "buy",
    )
    monkeypatch.setattr(
        engine,
        "fetch_fundamentals_snapshot",
        lambda _s: {"asset_class": "crypto", "market_cap_rank": 9999},
    )
    monkeypatch.setattr(engine.config, "MAX_CRYPTO_MARKET_CAP_RANK", 500)

    df = pd.DataFrame(
        [
            {
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "rsi": 50.0,
                "ema20": 1.0,
                "ema50": 1.0,
                "atr": 0.1,
            }
        ]
        * 25
    )
    out = engine._evaluate_symbol_smc(df, "DOGE/USDT", regime_model=_RM())
    assert out["decision"] == "hold"
    assert out["fundamentals_ok"] is False
    assert out["reason"] == "fundamentals_block"


def test_smc_fundamentals_pass_when_filter_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from tr_bot import engine

    class _Prop:
        direction = "buy"
        blocked_gate = None
        is_actionable = True
        score = 80
        smc_factors = ["ob"]

    class _RM:
        def predict_with_confidence(self, _df: object) -> tuple[str, float]:
            return "range", 0.9

    monkeypatch.setattr(engine.config, "USE_LLM", False)
    monkeypatch.setattr(engine.config, "USE_FUNDAMENTALS_FILTER", False)
    monkeypatch.setattr(engine.config, "CMC_GLOBAL_METRICS_ENABLED", False)
    monkeypatch.setattr(engine.config, "LOOP_BARS", 50)
    monkeypatch.setattr(engine.config, "TIMEFRAME", "5m")
    monkeypatch.setattr("tr_bot.strategy.multi_tf.fetch_multi_tf", lambda *_a, **_k: {})
    monkeypatch.setattr(
        "tr_bot.strategy.market_context.build_market_context",
        lambda **_k: object(),
    )
    monkeypatch.setattr(
        "tr_bot.strategy.signal_pipeline.analyze_symbol",
        lambda *_a, **_k: _Prop(),
    )
    monkeypatch.setattr(
        "tr_bot.strategy.signal_pipeline.proposal_to_decision",
        lambda _p: "buy",
    )

    df = pd.DataFrame(
        [
            {
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "rsi": 50.0,
                "ema20": 1.0,
                "ema50": 1.0,
                "atr": 0.1,
            }
        ]
        * 25
    )
    out = engine._evaluate_symbol_smc(df, "BTC/USDT", regime_model=_RM())
    assert out["decision"] == "buy"
    assert out["fundamentals_ok"] is True

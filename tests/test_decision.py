"""Decision engine."""

from tr_bot.decision import decision_engine


def test_hold_when_trend_strategy_disabled() -> None:
    assert (
        decision_engine(
            "trend",
            "buy",
            "neutral",
            ta_ok=True,
            fundamentals_ok=True,
            trend_strategy_enabled=False,
        )
        == "hold"
    )


def test_buy_in_trend_when_strategy_enabled() -> None:
    assert (
        decision_engine(
            "trend",
            "buy",
            "neutral",
            ta_ok=True,
            fundamentals_ok=True,
            trend_strategy_enabled=True,
        )
        == "buy"
    )


def test_trend_buy_veto_bearish_sentiment() -> None:
    assert (
        decision_engine(
            "trend",
            "buy",
            "bearish",
            ta_ok=True,
            fundamentals_ok=True,
            trend_strategy_enabled=True,
        )
        == "hold"
    )


def test_buy_in_range_bullish_sentiment() -> None:
    assert decision_engine("range", "buy", "bullish", ta_ok=True, fundamentals_ok=True) == "buy"


def test_bullish_sentiment_never_invents_trade_from_hold() -> None:
    """Bullish is veto-capable for sells only; it must not enable a trade without a signal."""
    assert decision_engine("range", "hold", "bullish", ta_ok=True, fundamentals_ok=True) == "hold"
    assert (
        decision_engine(
            "trend",
            "hold",
            "bullish",
            ta_ok=True,
            fundamentals_ok=True,
            trend_strategy_enabled=True,
        )
        == "hold"
    )


def test_buy_veto_bearish_sentiment() -> None:
    assert decision_engine("range", "buy", "bearish", ta_ok=True, fundamentals_ok=True) == "hold"


def test_ta_filter_blocks() -> None:
    assert decision_engine("range", "buy", "neutral", ta_ok=False, fundamentals_ok=True) == "hold"


def test_fundamentals_filter_blocks() -> None:
    assert decision_engine("range", "buy", "neutral", ta_ok=True, fundamentals_ok=False) == "hold"

"""Yahoo training ticker normalization."""

from tr_bot.training.yahoo import normalize_yahoo_training_ticker


def test_normalize_maps_usdt_suffix_to_usd() -> None:
    assert normalize_yahoo_training_ticker("ETH-USDT") == "ETH-USD"
    assert normalize_yahoo_training_ticker("BTC-USDT") == "BTC-USD"
    assert normalize_yahoo_training_ticker("sol-usdt") == "SOL-USD"


def test_normalize_leaves_cross_and_equities_unchanged() -> None:
    assert normalize_yahoo_training_ticker("ETH-BTC") == "ETH-BTC"
    assert normalize_yahoo_training_ticker("SPY") == "SPY"

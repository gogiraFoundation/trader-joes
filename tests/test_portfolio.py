"""Portfolio weight parsing."""

from tr_bot.managers.portfolio import parse_portfolio_weights


def test_default_single_symbol_weight(monkeypatch) -> None:
    from tr_bot import config

    monkeypatch.setattr(config, "PORTFOLIO_MODE", False)
    monkeypatch.setattr(config, "PORTFOLIO_WEIGHTS", "")
    monkeypatch.setattr(config, "SYMBOL", "BTC/USDT")
    w = parse_portfolio_weights()
    assert w == {"BTC/USDT": 1.0}


def test_portfolio_normalized(monkeypatch) -> None:
    from tr_bot import config

    monkeypatch.setattr(config, "PORTFOLIO_MODE", True)
    monkeypatch.setattr(config, "PORTFOLIO_WEIGHTS", "BTC/USDT=0.5,ETH/USDT=0.5")
    w = parse_portfolio_weights()
    assert abs(w["BTC/USDT"] - 0.5) < 1e-9
    assert abs(w["ETH/USDT"] - 0.5) < 1e-9

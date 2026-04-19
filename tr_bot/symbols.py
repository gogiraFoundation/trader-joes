"""Symbol helpers for spot (base / quote)."""


def split_pair(symbol: str) -> tuple[str, str]:
    parts = symbol.split("/")
    if len(parts) != 2:
        raise ValueError(f"Expected CCXT pair like BTC/USDT, got {symbol!r}")
    return parts[0].strip(), parts[1].strip()

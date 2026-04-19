"""Mean reversion signal thresholds."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from tr_bot import config
from tr_bot.signals import mean_reversion_signal


def test_mean_reversion_signal_respects_rsi_oversold_config() -> None:
    df = pd.DataFrame(
        {
            "rsi": [50.0, 25.0],
            "ema20": [100.0, 100.0],
            "close": [99.0, 99.0],
        }
    )
    with patch.object(config, "SIGNAL_RSI_OVERSOLD", 20.0):
        assert mean_reversion_signal(df) == "hold"
    with patch.object(config, "SIGNAL_RSI_OVERSOLD", 30.0):
        assert mean_reversion_signal(df) == "buy"

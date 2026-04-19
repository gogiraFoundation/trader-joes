"""Offline training: Yahoo Finance, CoinMarketCap, optional exchange OHLCV."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "run_training_pipeline":
        from tr_bot.training.pipeline import run_training_pipeline

        return run_training_pipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["run_training_pipeline"]

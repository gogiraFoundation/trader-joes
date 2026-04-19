"""Logging setup."""

from __future__ import annotations

import logging
import sys


def setup_logger(name: str = "tr_bot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    # Avoid duplicate records when parent loggers also have handlers.
    logger.propagate = False
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(h)
    return logger

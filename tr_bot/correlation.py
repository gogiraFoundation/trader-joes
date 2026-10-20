"""Rolling Pearson correlation for portfolio symbols."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from tr_bot import config

logger = logging.getLogger("tr_bot.correlation")


def rolling_returns(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float).pct_change().dropna()


def pearson_matrix(returns_by_symbol: dict[str, pd.Series]) -> dict[str, dict[str, float]]:
    symbols = list(returns_by_symbol.keys())
    if len(symbols) < 2:
        return {s: {s: 1.0} for s in symbols}
    aligned = pd.DataFrame({s: returns_by_symbol[s] for s in symbols}).dropna(how="any")
    if aligned.empty or len(aligned) < 5:
        return {s: {t: (1.0 if s == t else 0.0) for t in symbols} for s in symbols}
    corr = aligned.corr(method="pearson")
    out: dict[str, dict[str, float]] = {}
    for s in symbols:
        out[s] = {}
        for t in symbols:
            v = corr.loc[s, t] if s in corr.index and t in corr.columns else (1.0 if s == t else 0.0)
            out[s][t] = float(v) if v == v else 0.0
    return out


def max_peer_correlation(matrix: dict[str, dict[str, float]], symbol: str) -> float:
    row = matrix.get(symbol) or {}
    peers = [v for k, v in row.items() if k != symbol]
    return max(peers) if peers else 0.0


def correlation_size_multiplier(corr: float) -> float:
    """HIATP-inspired correlation adjusters."""
    if corr > 0.80:
        return 0.5
    if corr >= 0.70:
        return 0.7
    if corr >= 0.50:
        return 0.85
    if corr >= 0.30:
        return 0.95
    return 1.0


def _state_path() -> Path:
    root = Path(config.ARTIFACTS_DIR).resolve()
    (root / "correlation").mkdir(parents=True, exist_ok=True)
    return root / "correlation" / "latest.json"


def persist_matrix(matrix: dict[str, dict[str, float]], *, meta: dict[str, Any] | None = None) -> Path:
    path = _state_path()
    payload = {"matrix": matrix, "meta": meta or {}}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_matrix() -> dict[str, dict[str, float]]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        m = raw.get("matrix") or {}
        return m if isinstance(m, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def correlation_penalty_for(symbol: str) -> float:
    matrix = load_matrix()
    if not matrix:
        return 1.0
    return correlation_size_multiplier(max_peer_correlation(matrix, symbol))


def update_from_ohlcv_map(ohlcv_by_symbol: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
    rets = {s: rolling_returns(df) for s, df in ohlcv_by_symbol.items() if df is not None and len(df) > 10}
    matrix = pearson_matrix(rets)
    try:
        persist_matrix(matrix)
    except OSError as e:
        logger.warning("correlation persist failed: %s", e)
    return matrix

"""Aggregated market context for SMC scoring (session, vol, ML regime, CMC, corr)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MarketContext:
    ml_regime: str = "range"
    regime_confidence: float = 0.5
    session: str = "unknown"
    vol_regime: str = "normal"
    vol_percentile: float | None = None
    btc_dominance: float | None = None
    fear_greed: float | None = None
    altcoin_season: bool | None = None
    correlation_penalty: float = 1.0  # multiply score soft factor
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @property
    def soft_score_multiplier(self) -> float:
        """Soft multiplier applied after composite score (typically 0.7–1.15)."""
        mult = float(self.correlation_penalty)
        if self.vol_regime == "extreme":
            mult *= 0.85
        elif self.vol_regime == "high":
            mult *= 0.92
        elif self.vol_regime == "low":
            mult *= 1.05
        if self.session == "asia":
            mult *= 0.95
        elif self.session in ("london_ny_overlap", "overlap"):
            mult *= 1.05
        return max(0.5, min(1.2, mult))


def empty_context(**kwargs: Any) -> MarketContext:
    return MarketContext(**kwargs)


def build_market_context(
    *,
    ml_regime: str = "range",
    regime_confidence: float = 0.5,
    df_exec: Any = None,
    now_utc: Any = None,
    symbol: str | None = None,
    include_cmc: bool = False,
) -> MarketContext:
    """
    Build context from available helpers. Imports session/vol lazily to avoid cycles.
    """
    from tr_bot.market_session import classify_session
    from tr_bot.volatility_regime import classify_volatility

    session = classify_session(now_utc)
    vol_regime = "normal"
    vol_pct: float | None = None
    if df_exec is not None and hasattr(df_exec, "__len__") and len(df_exec) > 20:
        vol_regime, vol_pct = classify_volatility(df_exec)

    ctx = MarketContext(
        ml_regime=ml_regime,
        regime_confidence=float(regime_confidence),
        session=session,
        vol_regime=vol_regime,
        vol_percentile=vol_pct,
    )

    if include_cmc:
        try:
            from tr_bot.analysis.cmc_global import get_global_metrics_snapshot
            from tr_bot.analysis.cmc_fear_greed import get_fear_greed_snapshot
            from tr_bot.analysis.cmc_altcoin_season import get_altcoin_season_snapshot

            g = get_global_metrics_snapshot()
            if g.get("ok") and isinstance(g.get("data"), dict):
                ctx.btc_dominance = g["data"].get("btc_dominance")
            fg = get_fear_greed_snapshot()
            if fg.get("ok") and isinstance(fg.get("data"), dict):
                v = fg["data"].get("value")
                ctx.fear_greed = float(v) if v is not None else None
            al = get_altcoin_season_snapshot()
            if al.get("ok"):
                ctx.altcoin_season = bool(al.get("is_altcoin_season") or al.get("data", {}).get("is_altcoin_season"))
        except Exception:
            pass

    if symbol:
        try:
            from tr_bot.correlation import correlation_penalty_for

            ctx.correlation_penalty = correlation_penalty_for(symbol)
        except Exception:
            pass

    return ctx

from __future__ import annotations

from tr_bot.promotion.stats import block_bootstrap_metric, gate_bootstrap_expectancy


def test_block_bootstrap_preserves_length():
    values = [1.0, -0.5, 2.0, -1.0, 0.5]
    samples = block_bootstrap_metric(
        values,
        block_size=2,
        n_resamples=50,
        metric_fn=lambda xs: sum(xs) / len(xs),
        seed=1,
    )
    assert len(samples) == 50


def test_bootstrap_expectancy_passes_positive_series():
    rows = [{"outcome": "win", "net_pnl_usdt": 10.0} for _ in range(20)]
    rows += [{"outcome": "loss", "net_pnl_usdt": -3.0} for _ in range(10)]
    out = gate_bootstrap_expectancy(
        rows,
        min_lower_bound=0.0,
        block_size=3,
        n_resamples=200,
        alpha=0.05,
        seed=42,
    )
    assert out.passed is True

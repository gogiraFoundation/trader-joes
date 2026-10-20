'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const Overview = require(
  path.join(__dirname, '../../tr_bot/static/dashboard/js/overview-core.js')
);

describe('TROverviewCore.livePerformanceHeadline', () => {
  it('maps closed_trade_stats and baseline into Live headlines', () => {
    const h = Overview.livePerformanceHeadline(
      {
        closed_trades: 10,
        wins: 6,
        losses: 4,
        sum_net_pnl_usdt: 12.34567,
        win_rate_pct: 60.12,
        avg_win_usdt: 3.1,
        avg_loss_usdt: -1.2,
        best_trade_usdt: 5,
        worst_trade_usdt: -2,
        scope: 'full_ledger',
        paper_mode: 'paper',
      },
      { nav: 1100, baseline_usdt: 1000 }
    );
    assert.equal(h.closed_trades, 10);
    assert.equal(h.wins, 6);
    assert.equal(h.sum_net_pnl_usdt, 12.3457);
    assert.equal(h.win_rate_pct, 60.12);
    assert.equal(h.nav_vs_baseline_pct, 10);
    assert.equal(h.scope, 'full_ledger');
  });
});

describe('TROverviewCore.backtestPerformanceHeadline', () => {
  it('keeps Sharpe unpublished when risk_metric_meta.published is false', () => {
    const h = Overview.backtestPerformanceHeadline(
      {
        total_return_pct: 1.5,
        max_drawdown_pct: 3.2,
        trades: 4,
        wins: 2,
        losses: 2,
        sharpe: 1.23,
        sortino: 1.5,
        risk_metric_meta: {
          published: false,
          comparison_guardrails: ['insufficient observations'],
        },
      },
      { LIVE_FEE_BPS: 10, BACKTEST_FEE_BPS: 10 }
    );
    assert.equal(h.total_return_pct, 1.5);
    assert.equal(h.sharpe, null);
    assert.equal(h.risk_metrics_published, false);
    assert.deepEqual(h.comparison_guardrails, ['insufficient observations']);
    assert.equal(h.live_fee_bps, 10);
  });

  it('publishes Sharpe when meta allows', () => {
    const h = Overview.backtestPerformanceHeadline({
      total_return_pct: 2,
      sharpe: 0.5,
      risk_metric_meta: { published: true },
    });
    assert.equal(h.sharpe, 0.5);
    assert.equal(h.risk_metrics_published, true);
  });
});

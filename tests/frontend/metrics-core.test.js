'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const Core = require(
  path.join(__dirname, '../../tr_bot/static/dashboard/js/metrics-core.js')
);

describe('TRMetricsCore.filterPointsByAge', () => {
  it('keeps only points inside the window', () => {
    const now = Date.parse('2026-07-16T12:00:00Z');
    const pts = [
      { t: '2026-07-01T00:00:00Z', nav: 1 },
      { t: '2026-07-15T12:00:00Z', nav: 2 },
      { t: '2026-07-16T11:00:00Z', nav: 3 },
    ];
    const out = Core.filterPointsByAge(pts, now, Core.SEVEN_DAYS_MS);
    assert.equal(out.length, 2);
    assert.equal(out[0].nav, 2);
  });
});

describe('TRMetricsCore.aggregateTradesByDay + buildCumulativePnl', () => {
  it('fills missing days and sorts newest-first input', () => {
    const now = Date.parse('2026-07-16T18:00:00Z');
    const trades = [
      { utc: '2026-07-16T10:00:00Z', net_pnl_usdt: 10 },
      { utc: '2026-07-14T10:00:00Z', net_pnl_usdt: -5 },
      { utc: '2026-07-16T12:00:00Z', net_pnl_usdt: 2 },
    ];
    const daily = Core.aggregateTradesByDay(trades, now, 7);
    assert.equal(daily.length, 7);
    const last = daily[daily.length - 1];
    assert.equal(last.day, '2026-07-16');
    assert.equal(last.count, 2);
    assert.equal(last.net_pnl_usdt, 12);
    const cum = Core.buildCumulativePnl(daily);
    assert.equal(cum[cum.length - 1].cumulative_pnl_usdt, 7);
  });

  it('returns zero buckets when empty', () => {
    const daily = Core.aggregateTradesByDay([], Date.parse('2026-07-16T00:00:00Z'), 3);
    assert.equal(daily.length, 3);
    assert.ok(daily.every((b) => b.count === 0 && b.net_pnl_usdt === 0));
  });
});

describe('TRMetricsCore.calculateTradeWindow', () => {
  it('computes win rate, PF, expectancy from newest-first trades', () => {
    const trades = [
      { net_pnl_usdt: 10 },
      { net_pnl_usdt: -5 },
      { net_pnl_usdt: 2 },
      { net_pnl_usdt: -1 },
    ];
    const s = Core.calculateTradeWindow(trades, 25);
    assert.equal(s.available, 4);
    assert.equal(s.win_rate_pct, 50);
    assert.ok(Math.abs(s.profit_factor - 12 / 6) < 1e-6);
    assert.equal(s.expectancy_usdt, 1.5);
    assert.equal(s.avg_win_usdt, 6);
    assert.equal(s.avg_loss_usdt, -3);
  });

  it('labels partial windows via available < window', () => {
    const s = Core.calculateTradeWindow([{ net_pnl_usdt: 1 }], 100);
    assert.equal(s.available, 1);
    assert.equal(s.window, 100);
  });

  it('flags infinite PF when no losses', () => {
    const s = Core.calculateTradeWindow([{ net_pnl_usdt: 3 }, { net_pnl_usdt: 1 }], 25);
    assert.equal(s.profit_factor_infinite, true);
    assert.equal(s.profit_factor, null);
  });
});

describe('TRMetricsCore severity helpers', () => {
  it('classifies latency boundaries', () => {
    assert.equal(Core.latencySeverity(9.9), 'ok');
    assert.equal(Core.latencySeverity(10), 'warn');
    assert.equal(Core.latencySeverity(50), 'warn');
    assert.equal(Core.latencySeverity(50.1), 'critical');
  });

  it('classifies limits and pauses', () => {
    assert.equal(Core.limitSeverity({ can_trade: true, daily_trades: 1, max_daily_trades: 3 }), 'ok');
    assert.equal(Core.limitSeverity({ daily_trades: 3, max_daily_trades: 3 }), 'critical');
    assert.equal(Core.limitSeverity({ daily_trades: 4, max_daily_trades: 5 }), 'warn');
    assert.equal(Core.limitSeverity({ halt_file: true }), 'critical');
    assert.equal(Core.limitSeverity({ consecutive_losses: 1, max_consecutive_losses: 3 }), 'warn');
  });

  it('classifies drawdown vs configured fraction', () => {
    assert.equal(Core.drawdownSeverity(10, 0.25), 'ok');
    assert.equal(Core.drawdownSeverity(20, 0.25), 'warn');
    assert.equal(Core.drawdownSeverity(25, 0.25), 'critical');
  });
});

describe('TRMetricsCore.sharpeFromNavPoints', () => {
  it('returns insufficient_history when too few daily returns', () => {
    const pts = [
      { t: '2026-07-14T00:00:00Z', nav: 100 },
      { t: '2026-07-15T00:00:00Z', nav: 101 },
    ];
    const sh = Core.sharpeFromNavPoints(pts, 5);
    assert.equal(sh.ok, false);
    assert.equal(sh.reason, 'insufficient_history');
  });
});

describe('TRMetricsCore.toNegativeDrawdownPoints', () => {
  it('negates positive drawdown percentages', () => {
    const rows = Core.toNegativeDrawdownPoints([
      { t: '2026-07-16T00:00:00Z', drawdown_pct: 21.73 },
    ]);
    assert.equal(rows.length, 1);
    assert.ok(rows[0].y < 0);
    assert.ok(Math.abs(rows[0].y + 21.73) < 1e-6);
  });
});

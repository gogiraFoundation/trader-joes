/**
 * Pure Overview Performance helpers (Live vs Backtest headlines).
 * Compatible with browser <script> and Node require for node:test.
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  root.TROverviewCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function fmtNum(x, digits) {
    if (x == null || x === '') return null;
    var n = Number(x);
    if (!Number.isFinite(n)) return null;
    digits = digits == null ? 2 : digits;
    return Math.round(n * Math.pow(10, digits)) / Math.pow(10, digits);
  }

  /**
   * Headline fields for the Live performance tab from pnl.closed_trade_stats
   * and optional NAV vs baseline.
   */
  function livePerformanceHeadline(closedStats, baseline) {
    closedStats = closedStats || {};
    baseline = baseline || {};
    var closed = Number(closedStats.closed_trades);
    var wins = Number(closedStats.wins);
    var losses = Number(closedStats.losses);
    var net = fmtNum(closedStats.sum_net_pnl_usdt, 4);
    var winRate = fmtNum(closedStats.win_rate_pct != null ? closedStats.win_rate_pct : closedStats.net_positive_pct, 2);
    var avgWin = fmtNum(closedStats.avg_win_usdt, 4);
    var avgLoss = fmtNum(closedStats.avg_loss_usdt, 4);
    var best = fmtNum(closedStats.best_trade_usdt, 4);
    var worst = fmtNum(closedStats.worst_trade_usdt, 4);
    var nav = fmtNum(baseline.nav, 4);
    var base = fmtNum(baseline.baseline_usdt, 4);
    var vsBase = null;
    if (nav != null && base != null && base !== 0) {
      vsBase = fmtNum(((nav - base) / Math.abs(base)) * 100, 2);
    }
    return {
      closed_trades: Number.isFinite(closed) ? closed : 0,
      wins: Number.isFinite(wins) ? wins : 0,
      losses: Number.isFinite(losses) ? losses : 0,
      sum_net_pnl_usdt: net,
      win_rate_pct: winRate,
      avg_win_usdt: avgWin,
      avg_loss_usdt: avgLoss,
      best_trade_usdt: best,
      worst_trade_usdt: worst,
      nav_usdt: nav,
      baseline_usdt: base,
      nav_vs_baseline_pct: vsBase,
      scope: closedStats.scope || null,
      paper_mode: closedStats.paper_mode || null,
    };
  }

  /**
   * Headline fields for the Backtest performance tab from backtest.last_run
   * and optional risk_metric_meta / cost assumptions.
   */
  function backtestPerformanceHeadline(lastRun, costAssumptions) {
    lastRun = lastRun || {};
    costAssumptions = costAssumptions || {};
    var meta = lastRun.risk_metric_meta || {};
    var published = meta.published !== false;
    var sharpe = published ? fmtNum(lastRun.sharpe, 4) : null;
    var sortino = published ? fmtNum(lastRun.sortino, 4) : null;
    var guardrails = Array.isArray(meta.comparison_guardrails)
      ? meta.comparison_guardrails.slice()
      : [];
    return {
      total_return_pct: fmtNum(lastRun.total_return_pct, 2),
      max_drawdown_pct: fmtNum(lastRun.max_drawdown_pct, 2),
      trades: lastRun.trades != null ? Number(lastRun.trades) : null,
      wins: lastRun.wins != null ? Number(lastRun.wins) : null,
      losses: lastRun.losses != null ? Number(lastRun.losses) : null,
      sharpe: sharpe,
      sortino: sortino,
      risk_metrics_published: published,
      comparison_guardrails: guardrails,
      live_fee_bps: costAssumptions.LIVE_FEE_BPS != null ? Number(costAssumptions.LIVE_FEE_BPS) : null,
      live_slippage_bps:
        costAssumptions.LIVE_SLIPPAGE_BPS != null ? Number(costAssumptions.LIVE_SLIPPAGE_BPS) : null,
      backtest_fee_bps:
        costAssumptions.BACKTEST_FEE_BPS != null ? Number(costAssumptions.BACKTEST_FEE_BPS) : null,
      backtest_slippage_bps:
        costAssumptions.BACKTEST_SLIPPAGE_BPS != null
          ? Number(costAssumptions.BACKTEST_SLIPPAGE_BPS)
          : null,
    };
  }

  return {
    fmtNum: fmtNum,
    livePerformanceHeadline: livePerformanceHeadline,
    backtestPerformanceHeadline: backtestPerformanceHeadline,
  };
});

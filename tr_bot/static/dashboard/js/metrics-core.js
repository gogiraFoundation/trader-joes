/**
 * Pure Metrics transformations (no DOM / Chart.js). Load before metrics.js.
 * Compatible with browser <script> and Node `require` for node:test.
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  root.TRMetricsCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var DAY_MS = 24 * 60 * 60 * 1000;
  var SEVEN_DAYS_MS = 7 * DAY_MS;

  function parseIsoMs(iso) {
    if (iso == null || iso === '') return NaN;
    try {
      var s = String(iso);
      var d = new Date(
        s.indexOf('T') !== -1 && s.slice(-1) !== 'Z' && s.indexOf('+') === -1 ? s + 'Z' : s
      );
      return d.getTime();
    } catch (e) {
      return NaN;
    }
  }

  function filterPointsByAge(points, nowMs, windowMs) {
    nowMs = nowMs == null ? Date.now() : Number(nowMs);
    windowMs = windowMs == null ? SEVEN_DAYS_MS : Number(windowMs);
    var cutoff = nowMs - windowMs;
    var out = [];
    var list = Array.isArray(points) ? points : [];
    for (var i = 0; i < list.length; i++) {
      var p = list[i] || {};
      var t = parseIsoMs(p.t);
      if (!Number.isFinite(t) || t < cutoff) continue;
      out.push(p);
    }
    return out;
  }

  function toChartPoints(points, valueKey) {
    valueKey = valueKey || 'nav';
    var out = [];
    var list = Array.isArray(points) ? points : [];
    for (var i = 0; i < list.length; i++) {
      var p = list[i] || {};
      var x = parseIsoMs(p.t);
      var y = Number(p[valueKey]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      out.push({ x: x, y: y });
    }
    return out;
  }

  /** Negate positive drawdown_pct for downward chart display. */
  function toNegativeDrawdownPoints(points) {
    var rows = toChartPoints(points, 'drawdown_pct');
    for (var i = 0; i < rows.length; i++) {
      rows[i].y = -Math.abs(rows[i].y);
    }
    return rows;
  }

  function utcDayKey(ms) {
    var d = new Date(ms);
    var y = d.getUTCFullYear();
    var m = String(d.getUTCMonth() + 1);
    if (m.length < 2) m = '0' + m;
    var day = String(d.getUTCDate());
    if (day.length < 2) day = '0' + day;
    return y + '-' + m + '-' + day;
  }

  /**
   * Aggregate closed trades into daily buckets (oldest → newest).
   * Accepts newest-first ledger rows; sorts by time internally.
   */
  function aggregateTradesByDay(trades, nowMs, days) {
    nowMs = nowMs == null ? Date.now() : Number(nowMs);
    days = days == null ? 7 : Math.max(1, Number(days) | 0);
    var cutoff = nowMs - days * DAY_MS;
    var byDay = {};
    var list = Array.isArray(trades) ? trades.slice() : [];
    list.sort(function (a, b) {
      return parseIsoMs(a && a.utc) - parseIsoMs(b && b.utc);
    });
    for (var i = 0; i < list.length; i++) {
      var row = list[i] || {};
      var t = parseIsoMs(row.utc);
      if (!Number.isFinite(t) || t < cutoff) continue;
      var key = utcDayKey(t);
      if (!byDay[key]) byDay[key] = { day: key, count: 0, net_pnl_usdt: 0 };
      byDay[key].count += 1;
      var pnl = Number(row.net_pnl_usdt);
      if (Number.isFinite(pnl)) byDay[key].net_pnl_usdt += pnl;
    }
    var out = [];
    for (var d = days - 1; d >= 0; d--) {
      var dayMs = nowMs - d * DAY_MS;
      var k = utcDayKey(dayMs);
      var bucket = byDay[k] || { day: k, count: 0, net_pnl_usdt: 0 };
      out.push({
        day: bucket.day,
        count: bucket.count,
        net_pnl_usdt: Math.round(bucket.net_pnl_usdt * 1e6) / 1e6,
        x: Date.UTC(
          Number(k.slice(0, 4)),
          Number(k.slice(5, 7)) - 1,
          Number(k.slice(8, 10)),
          12,
          0,
          0
        ),
      });
    }
    return out;
  }

  function buildCumulativePnl(dailyBuckets) {
    var list = Array.isArray(dailyBuckets) ? dailyBuckets : [];
    var cum = 0;
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var b = list[i] || {};
      cum += Number(b.net_pnl_usdt) || 0;
      out.push({
        day: b.day,
        x: b.x,
        count: b.count,
        net_pnl_usdt: b.net_pnl_usdt,
        cumulative_pnl_usdt: Math.round(cum * 1e6) / 1e6,
      });
    }
    return out;
  }

  /**
   * Rolling window stats from newest-first trades.
   * Win rate = share with net_pnl_usdt > 0.
   */
  function calculateTradeWindow(trades, n) {
    n = Math.max(1, Number(n) | 0);
    var list = Array.isArray(trades) ? trades : [];
    var slice = list.slice(0, n);
    var available = slice.length;
    var pos = [];
    var neg = [];
    var sum = 0;
    for (var i = 0; i < slice.length; i++) {
      var pnl = Number(slice[i] && slice[i].net_pnl_usdt);
      if (!Number.isFinite(pnl)) pnl = 0;
      sum += pnl;
      if (pnl > 0) pos.push(pnl);
      else if (pnl < 0) neg.push(pnl);
    }
    var grossWin = 0;
    for (var w = 0; w < pos.length; w++) grossWin += pos[w];
    var grossLossAbs = 0;
    for (var l = 0; l < neg.length; l++) grossLossAbs += -neg[l];
    var pf = null;
    if (grossLossAbs > 0) pf = Math.round((grossWin / grossLossAbs) * 1e6) / 1e6;
    else if (available > 0 && grossWin > 0) pf = null; // undefined / no losses — UI shows ∞
    var winRate = available ? Math.round((pos.length / available) * 10000) / 100 : null;
    return {
      window: n,
      available: available,
      win_rate_pct: winRate,
      profit_factor: pf,
      profit_factor_infinite: available > 0 && grossLossAbs <= 0 && grossWin > 0,
      expectancy_usdt: available ? Math.round((sum / available) * 1e6) / 1e6 : null,
      avg_win_usdt: pos.length
        ? Math.round((grossWin / pos.length) * 1e6) / 1e6
        : null,
      avg_loss_usdt: neg.length
        ? Math.round((sumOf(neg) / neg.length) * 1e6) / 1e6
        : null,
      sum_net_pnl_usdt: Math.round(sum * 1e6) / 1e6,
    };
  }

  function sumOf(arr) {
    var s = 0;
    for (var i = 0; i < arr.length; i++) s += arr[i];
    return s;
  }

  /**
   * Daily NAV returns Sharpe (sample, non-annualized). Needs >= minObs daily returns.
   */
  function sharpeFromNavPoints(points, minObs) {
    minObs = minObs == null ? 5 : Math.max(2, Number(minObs) | 0);
    var list = Array.isArray(points) ? points.slice() : [];
    list.sort(function (a, b) {
      return parseIsoMs(a && a.t) - parseIsoMs(b && b.t);
    });
    // Collapse to last NAV per UTC day
    var byDay = {};
    for (var i = 0; i < list.length; i++) {
      var p = list[i] || {};
      var t = parseIsoMs(p.t);
      var nav = Number(p.nav);
      if (!Number.isFinite(t) || !Number.isFinite(nav) || nav <= 0) continue;
      byDay[utcDayKey(t)] = nav;
    }
    var keys = Object.keys(byDay).sort();
    var rets = [];
    for (var k = 1; k < keys.length; k++) {
      var prev = byDay[keys[k - 1]];
      var cur = byDay[keys[k]];
      if (prev > 0) rets.push((cur - prev) / prev);
    }
    if (rets.length < minObs) {
      return { ok: false, observations: rets.length, sharpe: null, reason: 'insufficient_history' };
    }
    var mean = sumOf(rets) / rets.length;
    var varSum = 0;
    for (var r = 0; r < rets.length; r++) {
      var d = rets[r] - mean;
      varSum += d * d;
    }
    var std = Math.sqrt(varSum / (rets.length - 1));
    if (!(std > 0)) {
      return { ok: false, observations: rets.length, sharpe: null, reason: 'zero_variance' };
    }
    return {
      ok: true,
      observations: rets.length,
      sharpe: Math.round((mean / std) * 1e6) / 1e6,
      reason: null,
    };
  }

  function latencySeverity(ms) {
    if (ms == null || !Number.isFinite(Number(ms))) return 'unknown';
    var v = Number(ms);
    if (v < 10) return 'ok';
    if (v <= 50) return 'warn';
    return 'critical';
  }

  /**
   * @param {{ daily_trades?: number, max_daily_trades?: number, consecutive_losses?: number, max_consecutive_losses?: number, can_trade?: boolean, tripped?: boolean, halt_file?: boolean, fund_halted?: boolean }} s
   */
  function limitSeverity(s) {
    s = s || {};
    if (s.halt_file || s.fund_halted || s.tripped || s.can_trade === false) return 'critical';
    var maxT = Number(s.max_daily_trades);
    var curT = Number(s.daily_trades);
    if (Number.isFinite(maxT) && maxT > 0 && Number.isFinite(curT)) {
      if (curT >= maxT) return 'critical';
      if (curT / maxT >= 0.8) return 'warn';
    }
    var maxC = Number(s.max_consecutive_losses);
    var curC = Number(s.consecutive_losses);
    if (Number.isFinite(maxC) && maxC > 0 && Number.isFinite(curC)) {
      if (curC >= maxC) return 'critical';
      if (curC > 0) return 'warn';
    }
    return 'ok';
  }

  /**
   * Drawdown severity vs configured max (fraction, e.g. 0.25).
   * currentPct is positive percentage (e.g. 21.73).
   */
  function drawdownSeverity(currentPct, maxFraction) {
    var cur = Math.abs(Number(currentPct));
    var maxPct = Number(maxFraction);
    if (!Number.isFinite(cur)) return 'unknown';
    if (!Number.isFinite(maxPct) || maxPct <= 0) return 'ok';
    maxPct = maxPct <= 1 ? maxPct * 100 : maxPct;
    if (cur >= maxPct) return 'critical';
    if (cur >= maxPct * 0.8) return 'warn';
    return 'ok';
  }

  function clampSeries(series, maxN) {
    if (!Array.isArray(series)) return [];
    if (series.length <= maxN) return series;
    return series.slice(series.length - maxN);
  }

  /** Keep samples within a time window and cap count. */
  function pruneSamples(samples, nowMs, windowMs, maxN) {
    nowMs = nowMs == null ? Date.now() : Number(nowMs);
    windowMs = windowMs == null ? DAY_MS : Number(windowMs);
    maxN = maxN == null ? 4320 : Number(maxN);
    var cutoff = nowMs - windowMs;
    var kept = [];
    var list = Array.isArray(samples) ? samples : [];
    for (var i = 0; i < list.length; i++) {
      var s = list[i];
      if (!s || !Number.isFinite(Number(s.t))) continue;
      if (Number(s.t) < cutoff) continue;
      kept.push(s);
    }
    return clampSeries(kept, maxN);
  }

  return {
    DAY_MS: DAY_MS,
    SEVEN_DAYS_MS: SEVEN_DAYS_MS,
    parseIsoMs: parseIsoMs,
    filterPointsByAge: filterPointsByAge,
    toChartPoints: toChartPoints,
    toNegativeDrawdownPoints: toNegativeDrawdownPoints,
    utcDayKey: utcDayKey,
    aggregateTradesByDay: aggregateTradesByDay,
    buildCumulativePnl: buildCumulativePnl,
    calculateTradeWindow: calculateTradeWindow,
    sharpeFromNavPoints: sharpeFromNavPoints,
    latencySeverity: latencySeverity,
    limitSeverity: limitSeverity,
    drawdownSeverity: drawdownSeverity,
    clampSeries: clampSeries,
    pruneSamples: pruneSamples,
  };
});

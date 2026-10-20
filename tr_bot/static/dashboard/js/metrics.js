/**
 * Metrics v2 page controller: polling, Chart.js, status cards.
 * Requires Chart.js, TRDashboard (common.js), TRMetricsCore (metrics-core.js).
 */
(function () {
  'use strict';

  var $ = TRDashboard.$;
  var Core = TRMetricsCore;
  var STORAGE_KEY = 'tr_bot_metrics_samples_v2';
  var MAX_SAMPLES = 4320; // 24h @ 20s
  var POLL_MS = 20000;

  var lastStrategyEngineStatus = null;
  var inflight = false;
  var samples = loadSamples();

  var navChart = null;
  var ddChart = null;
  var tradeChart = null;
  var depsChart = null;

  function loadSamples() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      var v = JSON.parse(raw);
      return Array.isArray(v) ? v : [];
    } catch (e) {
      return [];
    }
  }

  function saveSamples(list) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(Core.pruneSamples(list, Date.now(), Core.DAY_MS, MAX_SAMPLES)));
    } catch (e) {}
  }

  function fmtUtcMs(tsMs) {
    try {
      return new Date(tsMs).toISOString().replace('T', ' ').replace('Z', ' UTC');
    } catch (e) {
      return '\u2014';
    }
  }

  function axisTickLabel(v, spanMs) {
    var s = fmtUtcMs(Number(v));
    if (spanMs > Core.DAY_MS) return s.slice(5, 16); // MM-DD HH:mm
    return s.slice(11, 16); // HH:mm
  }

  function severityClass(sev) {
    if (sev === 'ok') return 'text-emerald-300';
    if (sev === 'warn') return 'text-amber-300';
    if (sev === 'critical') return 'text-red-300';
    return 'text-slate-400';
  }

  function severityBg(sev) {
    if (sev === 'ok') return 'bg-emerald-900/40 border-emerald-700/50';
    if (sev === 'warn') return 'bg-amber-900/40 border-amber-700/50';
    if (sev === 'critical') return 'bg-red-950/50 border-red-700/50';
    return 'bg-surface/80 border-surface-border';
  }

  function makeLinearChart(canvasId, datasets, yTitle, yOpts) {
    var el = $(canvasId);
    if (!el || typeof Chart === 'undefined') return null;
    yOpts = yOpts || {};
    return new Chart(el.getContext('2d'), {
      type: 'line',
      data: { datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        normalized: true,
        scales: {
          x: {
            type: 'linear',
            ticks: {
              color: '#94a3b8',
              callback: function (v) {
                var data = this.chart && this.chart.data && this.chart.data.datasets[0]
                  ? this.chart.data.datasets[0].data
                  : [];
                var xs = data.map(function (p) { return p.x; }).filter(Number.isFinite);
                var span = xs.length ? Math.max.apply(null, xs) - Math.min.apply(null, xs) : 0;
                return axisTickLabel(v, span);
              },
            },
            grid: { color: 'rgba(148, 163, 184, 0.12)' },
          },
          y: Object.assign(
            {
              ticks: { color: '#94a3b8' },
              grid: { color: 'rgba(148, 163, 184, 0.12)' },
              title: { display: !!yTitle, text: yTitle || '', color: '#94a3b8' },
            },
            yOpts
          ),
        },
        plugins: {
          legend: { labels: { color: '#cbd5e1' } },
          tooltip: {
            mode: 'index',
            intersect: false,
            callbacks: {
              title: function (items) {
                if (!items || !items.length) return '';
                return fmtUtcMs(Number(items[0].parsed.x));
              },
            },
          },
        },
        interaction: { mode: 'index', intersect: false },
        elements: { point: { radius: 0, hoverRadius: 3 } },
      },
    });
  }

  function initCharts() {
    navChart = makeLinearChart(
      'chart-nav',
      [
        {
          label: 'NAV',
          data: [],
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56, 189, 248, 0.15)',
          tension: 0.15,
          fill: true,
        },
        {
          label: 'Baseline',
          data: [],
          borderColor: '#64748b',
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
        },
      ],
      'USDT'
    );

    ddChart = makeLinearChart(
      'chart-dd',
      [
        {
          label: 'Drawdown %',
          data: [],
          borderColor: '#f59e0b',
          backgroundColor: 'rgba(245, 158, 11, 0.12)',
          tension: 0.15,
          fill: true,
        },
        {
          label: 'Max DD',
          data: [],
          borderColor: '#ef4444',
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
        },
      ],
      '%'
    );

    var tradeEl = $('chart-trades');
    if (tradeEl && typeof Chart !== 'undefined') {
      tradeChart = new Chart(tradeEl.getContext('2d'), {
        type: 'bar',
        data: {
          datasets: [
            {
              type: 'bar',
              label: 'Trades / day',
              data: [],
              backgroundColor: 'rgba(167, 139, 250, 0.55)',
              borderColor: '#a78bfa',
              yAxisID: 'y',
              order: 2,
            },
            {
              type: 'line',
              label: 'Cumulative P&L',
              data: [],
              borderColor: '#38bdf8',
              backgroundColor: 'transparent',
              tension: 0.2,
              yAxisID: 'y1',
              order: 1,
              pointRadius: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          parsing: false,
          scales: {
            x: {
              type: 'linear',
              ticks: {
                color: '#94a3b8',
                callback: function (v) {
                  return fmtUtcMs(Number(v)).slice(5, 10);
                },
              },
              grid: { color: 'rgba(148, 163, 184, 0.12)' },
            },
            y: {
              position: 'left',
              ticks: { color: '#94a3b8', precision: 0 },
              grid: { color: 'rgba(148, 163, 184, 0.12)' },
              title: { display: true, text: 'Trades', color: '#94a3b8' },
              beginAtZero: true,
            },
            y1: {
              position: 'right',
              ticks: { color: '#94a3b8' },
              grid: { drawOnChartArea: false },
              title: { display: true, text: 'Cum P&L USDT', color: '#94a3b8' },
            },
          },
          plugins: {
            legend: { labels: { color: '#cbd5e1' } },
            tooltip: {
              mode: 'index',
              intersect: false,
              callbacks: {
                title: function (items) {
                  if (!items || !items.length) return '';
                  return fmtUtcMs(Number(items[0].parsed.x)).slice(0, 10);
                },
              },
            },
          },
        },
      });
    }

    depsChart = makeLinearChart(
      'chart-deps',
      [
        { label: 'Redis ms', data: [], borderColor: '#34d399', tension: 0.2 },
        { label: 'Postgres ms', data: [], borderColor: '#f472b6', tension: 0.2 },
      ],
      'ms'
    );
  }

  function setEmpty(canvasId, emptyId, empty) {
    var canvas = $(canvasId);
    var emptyEl = $(emptyId);
    if (emptyEl) emptyEl.classList.toggle('hidden', !empty);
    if (canvas) canvas.classList.toggle('invisible', !!empty);
  }

  function renderNav(data) {
    var disp = data && data.equity_history_display;
    var points = (disp && disp.points) || [];
    var filtered = Core.filterPointsByAge(points, Date.now(), Core.SEVEN_DAYS_MS);
    var rows = Core.toChartPoints(filtered, 'nav');
    var baseline = data && data.config && data.config.paper_equity_usdt != null
      ? Number(data.config.paper_equity_usdt)
      : null;

    setEmpty('chart-nav', 'nav-empty', rows.length === 0);
    if (navChart) {
      navChart.data.datasets[0].data = rows;
      if (Number.isFinite(baseline) && rows.length) {
        navChart.data.datasets[1].data = [
          { x: rows[0].x, y: baseline },
          { x: rows[rows.length - 1].x, y: baseline },
        ];
      } else {
        navChart.data.datasets[1].data = [];
      }
      navChart.update();
    }

    var fund = data && data.fund && data.fund.state;
    var current =
      fund && fund.last_nav != null && Number.isFinite(Number(fund.last_nav))
        ? Number(fund.last_nav)
        : rows.length
          ? rows[rows.length - 1].y
          : null;
    var el = $('nav-current');
    if (el) {
      el.textContent =
        current != null
          ? current.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : '\u2014';
    }
    var meta = $('nav-meta');
    if (meta) {
      var dropped = disp && disp.outliers_dropped != null ? Number(disp.outliers_dropped) : 0;
      meta.textContent =
        (rows.length ? rows.length + ' pts (7d)' : 'no samples') +
        (dropped > 0 ? ' · filtered ' + dropped + ' outlier(s)' : '') +
        (Number.isFinite(baseline) ? ' · baseline ' + baseline.toLocaleString() : '');
    }
  }

  function renderDrawdown(data) {
    var hist =
      data && data.metrics && data.metrics.drawdown_history
        ? data.metrics.drawdown_history
        : { points: [] };
    var points = Core.filterPointsByAge(hist.points || [], Date.now(), Core.SEVEN_DAYS_MS);
    var rows = Core.toNegativeDrawdownPoints(points);
    var maxFrac =
      data && data.config && data.config.max_drawdown_pct != null
        ? Number(data.config.max_drawdown_pct)
        : 0.25;
    var maxPct = maxFrac <= 1 ? maxFrac * 100 : maxFrac;
    var thresh = -Math.abs(maxPct);

    setEmpty('chart-dd', 'dd-empty', rows.length === 0);
    if (ddChart) {
      ddChart.data.datasets[0].data = rows;
      if (rows.length) {
        ddChart.data.datasets[1].data = [
          { x: rows[0].x, y: thresh },
          { x: rows[rows.length - 1].x, y: thresh },
        ];
      } else {
        ddChart.data.datasets[1].data = [];
      }
      var minY = thresh;
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].y < minY) minY = rows[i].y;
      }
      ddChart.options.scales.y.min = Math.min(minY * 1.1, thresh * 1.2, -5);
      ddChart.options.scales.y.max = 0;
      ddChart.update();
    }

    var fund = data && data.fund && data.fund.state;
    var current =
      fund && fund.drawdown_pct != null
        ? Number(fund.drawdown_pct)
        : hist.current_drawdown_pct != null
          ? Number(hist.current_drawdown_pct)
          : null;
    var el = $('dd-current');
    var sev = Core.drawdownSeverity(current, maxFrac);
    if (el) {
      el.textContent = current != null ? (-Math.abs(current)).toFixed(2) + '%' : '\u2014';
      el.className = 'text-2xl font-semibold tabular-nums ' + severityClass(sev);
    }
    var meta = $('dd-meta');
    if (meta) {
      meta.textContent =
        'limit ' +
        thresh.toFixed(0) +
        '% · peak ' +
        (fund && fund.peak_nav != null
          ? Number(fund.peak_nav).toLocaleString(undefined, { maximumFractionDigits: 2 })
          : '\u2014') +
        ' · ' +
        sev;
    }
  }

  function renderTradeActivity(tradePayload) {
    var rows = (tradePayload && tradePayload.rows) || [];
    var daily = Core.buildCumulativePnl(Core.aggregateTradesByDay(rows, Date.now(), 7));
    var counts = daily.map(function (b) {
      return { x: b.x, y: b.count };
    });
    var cum = daily.map(function (b) {
      return { x: b.x, y: b.cumulative_pnl_usdt };
    });
    var hasAny = daily.some(function (b) {
      return b.count > 0;
    });
    setEmpty('chart-trades', 'trades-empty', !hasAny);
    if (tradeChart) {
      tradeChart.data.datasets[0].data = counts;
      tradeChart.data.datasets[1].data = cum;
      tradeChart.update();
    }
    var warn = $('trades-warn');
    if (warn) {
      var truncated = !!(tradePayload && (tradePayload.truncated || tradePayload.has_more));
      warn.classList.toggle('hidden', !truncated);
      if (truncated) {
        warn.textContent =
          'Trade scan truncated or paginated (matched=' +
          (tradePayload.total_matched != null ? tradePayload.total_matched : '?') +
          ') — chart may be incomplete.';
      }
    }
    var meta = $('trades-meta');
    if (meta) {
      var total = daily.reduce(function (a, b) {
        return a + b.count;
      }, 0);
      var lastCum = daily.length ? daily[daily.length - 1].cumulative_pnl_usdt : 0;
      meta.textContent =
        total +
        ' trades / 7d · cum P&L ' +
        (Number.isFinite(lastCum) ? lastCum.toFixed(2) : '\u2014') +
        ' USDT';
    }
  }

  function renderPerformance(data) {
    var trades = (data && data.pnl && data.pnl.recent_trades) || [];
    var tbody = $('perf-tbody');
    if (!tbody) return;
    var windows = [25, 50, 100];
    var html = '';
    for (var i = 0; i < windows.length; i++) {
      var n = windows[i];
      var s = Core.calculateTradeWindow(trades, n);
      var pf =
        s.profit_factor_infinite
          ? '\u221e'
          : s.profit_factor != null
            ? s.profit_factor.toFixed(3)
            : '\u2014';
      var label = s.available < n ? n + ' (avail ' + s.available + ')' : String(n);
      html +=
        '<tr class="border-t border-surface-border">' +
        '<td class="py-2 pr-3 text-slate-300">' +
        TRDashboard.escapeHtml(label) +
        '</td>' +
        '<td class="py-2 pr-3 font-mono">' +
        (s.win_rate_pct != null ? s.win_rate_pct.toFixed(1) + '%' : '\u2014') +
        '</td>' +
        '<td class="py-2 pr-3 font-mono">' +
        pf +
        '</td>' +
        '<td class="py-2 pr-3 font-mono">' +
        (s.expectancy_usdt != null ? s.expectancy_usdt.toFixed(2) : '\u2014') +
        '</td>' +
        '<td class="py-2 pr-3 font-mono">' +
        (s.avg_win_usdt != null ? s.avg_win_usdt.toFixed(2) : '\u2014') +
        '</td>' +
        '<td class="py-2 pr-3 font-mono">' +
        (s.avg_loss_usdt != null ? s.avg_loss_usdt.toFixed(2) : '\u2014') +
        '</td>' +
        '</tr>';
    }
    tbody.innerHTML = html;

    var navPts =
      (data && data.equity_history_display && data.equity_history_display.points) || [];
    var sh = Core.sharpeFromNavPoints(navPts, 5);
    var sharpeEl = $('perf-sharpe');
    if (sharpeEl) {
      if (sh.ok) {
        sharpeEl.textContent =
          'NAV daily Sharpe (sample): ' + sh.sharpe.toFixed(3) + ' · n=' + sh.observations;
      } else {
        sharpeEl.textContent =
          'NAV daily Sharpe: insufficient history (need \u22655 daily returns; have ' +
          sh.observations +
          ')';
      }
    }
  }

  function renderDeps(data, sample) {
    var dep = (data && data.dependencies) || {};
    var redis = dep.redis || {};
    var pg = dep.postgres || {};
    function paint(id, ok, ms) {
      var el = $(id);
      if (!el) return;
      var sev = ok === false ? 'critical' : Core.latencySeverity(ms);
      el.textContent =
        (ok === false ? 'DOWN' : ms != null && Number.isFinite(Number(ms)) ? Number(ms).toFixed(2) + ' ms' : '\u2014');
      el.className = 'mt-2 text-xl font-semibold tabular-nums ' + severityClass(sev);
      var card = el.closest('[data-sev-card]');
      if (card) {
        card.className =
          'rounded-lg border p-4 ' + severityBg(sev);
      }
    }
    paint('deps-redis-val', redis.ok, redis.latency_ms);
    paint('deps-pg-val', pg.ok, pg.latency_ms);

    samples = Core.pruneSamples(samples, Date.now(), Core.DAY_MS, MAX_SAMPLES);
    var xs = samples.map(function (s) {
      return Number(s.t);
    });
    if (depsChart) {
      depsChart.data.datasets[0].data = xs
        .map(function (t, i) {
          return samples[i].redis_ms != null ? { x: t, y: Number(samples[i].redis_ms) } : null;
        })
        .filter(Boolean);
      depsChart.data.datasets[1].data = xs
        .map(function (t, i) {
          return samples[i].pg_ms != null ? { x: t, y: Number(samples[i].pg_ms) } : null;
        })
        .filter(Boolean);
      depsChart.update();
    }
  }

  function renderCircuit(data) {
    var circ = (data && data.circuit) || {};
    var risk = (data && data.trading && data.trading.risk) || {};
    var fund = (data && data.fund) || {};
    var st = fund.state || {};
    var daily = risk.daily_trades != null ? Number(risk.daily_trades) : null;
    var maxDaily = risk.max_daily_trades != null ? Number(risk.max_daily_trades) : null;
    var n = circ.consecutive_losses != null ? Number(circ.consecutive_losses) : null;
    var m = circ.max_consecutive_losses != null ? Number(circ.max_consecutive_losses) : null;
    var can = risk.can_trade;
    var sev = Core.limitSeverity({
      daily_trades: daily,
      max_daily_trades: maxDaily,
      consecutive_losses: n,
      max_consecutive_losses: m,
      can_trade: can,
      tripped: circ.tripped,
      halt_file: fund.halt_file,
      fund_halted: st.halted,
    });

    var card = $('circuit-card');
    if (card) {
      card.className =
        'lg:col-span-4 rounded-xl border p-6 shadow-lg ' + severityBg(sev);
    }

    var tradesEl = $('circ-trades');
    if (tradesEl) {
      tradesEl.textContent =
        (daily != null ? daily : '\u2014') + ' / ' + (maxDaily != null ? maxDaily : '\u2014');
    }
    var bar = $('circ-trades-bar');
    if (bar && maxDaily > 0 && daily != null) {
      var pct = Math.min(100, (daily / maxDaily) * 100);
      bar.style.width = pct + '%';
      bar.className =
        'h-2 rounded ' +
        (pct >= 100 ? 'bg-red-500' : pct >= 80 ? 'bg-amber-400' : 'bg-emerald-500');
    } else if (bar) {
      bar.style.width = '0%';
    }

    var circEl = $('circ-count');
    if (circEl) {
      circEl.textContent = (n != null ? n : '\u2014') + ' / ' + (m != null ? m : '\u2014');
      circEl.className =
        'mt-2 font-mono text-lg ' +
        severityClass(n >= m ? 'critical' : n > 0 ? 'warn' : 'ok');
    }

    var canEl = $('circ-can-trade');
    if (canEl) {
      canEl.textContent = can === true ? 'Yes' : can === false ? 'No' : '\u2014';
      canEl.className =
        'mt-2 text-lg font-medium ' +
        (can === true ? 'text-emerald-300' : can === false ? 'text-red-300' : 'text-slate-400');
    }

    var pauseBits = [];
    if (fund.halt_file) pauseBits.push('manual pause');
    if (st.halted) pauseBits.push(st.halt_reason || 'fund halt');
    if (circ.tripped) pauseBits.push('circuit tripped');
    var pauseEl = $('circ-pause');
    if (pauseEl) {
      pauseEl.textContent = pauseBits.length ? pauseBits.join(' · ') : 'No pause / halt';
      pauseEl.className =
        'border-t border-surface-border pt-4 text-xs leading-5 ' +
        (pauseBits.length ? 'text-red-300' : 'text-slate-500');
    }
  }

  function renderBacktest(data, job) {
    var lr = (data && data.backtest && data.backtest.last_run) || {};
    function set(id, text) {
      var el = $(id);
      if (el) el.textContent = text;
    }
    set(
      'bt-ret',
      lr.total_return_pct != null ? Number(lr.total_return_pct).toFixed(2) + '%' : '\u2014'
    );
    set(
      'bt-dd',
      lr.max_drawdown_pct != null ? Number(lr.max_drawdown_pct).toFixed(2) + '%' : '\u2014'
    );
    set('bt-trades', lr.trades != null ? String(lr.trades) : '\u2014');
    set(
      'bt-wl',
      (lr.wins != null ? lr.wins : '\u2014') + ' / ' + (lr.losses != null ? lr.losses : '\u2014')
    );
    set(
      'bt-pf',
      lr.profit_factor != null ? Number(lr.profit_factor).toFixed(3) : '\u2014'
    );
    set('bt-finished', lr.finished_utc ? TRDashboard.fmtUtc(lr.finished_utc) : '\u2014');

    var jobEl = $('bt-job');
    if (jobEl) {
      if (job && job.status) {
        jobEl.textContent = 'Job: ' + job.kind + ' · ' + job.status;
        jobEl.className =
          'text-xs mt-2 ' +
          (job.status === 'error'
            ? 'text-red-300'
            : job.status === 'running' || job.status === 'queued'
              ? 'text-amber-300'
              : 'text-slate-500');
      } else {
        jobEl.textContent = 'Job: none';
        jobEl.className = 'text-xs mt-2 text-slate-500';
      }
    }
  }

  function sampleFromSummary(data) {
    var dep = (data && data.dependencies) || {};
    return {
      t: Date.now(),
      redis_ms: dep.redis && dep.redis.latency_ms != null ? Number(dep.redis.latency_ms) : null,
      pg_ms: dep.postgres && dep.postgres.latency_ms != null ? Number(dep.postgres.latency_ms) : null,
    };
  }

  function updateHeader(data) {
    TRDashboard.pillStatus('pill-status', data);
    var mode = $('pill-mode');
    if (mode && data && data.config) {
      mode.textContent = data.config.paper_trading ? 'paper' : 'live';
      mode.className =
        'dash-pill ' +
        (data.config.paper_trading
          ? 'bg-sky-900/60 text-sky-200'
          : 'bg-amber-900/60 text-amber-200');
    }
  }

  async function loadOnce() {
    if (inflight) return;
    inflight = true;
    try {
      var fromIso = new Date(Date.now() - Core.SEVEN_DAYS_MS).toISOString();
      var toIso = new Date().toISOString();
      var data = await TRDashboard.fetchDashboardJson('/api/dashboard/summary?sections=' + encodeURIComponent('metrics,equity_history_display,fund,pnl,backtest,circuit,config,strategy_engine_status,trading'));
      if (data === null) return;
      var banner = $('error-banner');
      if (banner) banner.classList.add('hidden');

      lastStrategyEngineStatus = data.strategy_engine_status || null;
      updateHeader(data);
      renderNav(data);
      renderDrawdown(data);
      renderPerformance(data);
      renderCircuit(data);

      samples.push(sampleFromSummary(data));
      samples = Core.pruneSamples(samples, Date.now(), Core.DAY_MS, MAX_SAMPLES);
      saveSamples(samples);
      renderDeps(data);

      var tradesUrl =
        '/api/dashboard/trades?from=' +
        encodeURIComponent(fromIso) +
        '&to=' +
        encodeURIComponent(toIso) +
        '&limit=1000';
      var trades = await TRDashboard.fetchDashboardJson(tradesUrl);
      if (trades !== null) renderTradeActivity(trades);

      var job = await TRDashboard.fetchDashboardJson('/api/dashboard/jobs/current');
      renderBacktest(data, job);

      var note = $('refresh-note');
      if (note) note.textContent = 'Updated ' + fmtUtcMs(Date.now()) + ' · poll ' + POLL_MS / 1000 + 's';
    } finally {
      inflight = false;
    }
  }

  function boot() {
    initCharts();
    if (document.getElementById('dashboard-header')) {
      TRDashboard.mountHeader({ title: 'Diagnostics', subtitle: 'Metrics cockpit', showRedis: false, showMode: true });
    } else {
      TRDashboard.markActiveNav();
    }
    TRDashboard.wireCompareModal(function () {
      return lastStrategyEngineStatus;
    }, ['bt-compare-open']);
    TRDashboard.wireTokenGate(loadOnce);
    updateRollingFromStorage();
    TRDashboard.startPolling(loadOnce, POLL_MS);
  }

  function updateRollingFromStorage() {
    samples = Core.pruneSamples(samples, Date.now(), Core.DAY_MS, MAX_SAMPLES);
    renderDeps({ dependencies: {} });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();

/**
 * Shared helpers for the tr_bot dashboard pages (index/strategy/risk/scheduler/metrics).
 *
 * Every page ships the same auth-gate markup (#auth-gate, #token-input, #token-save)
 * and #error-banner element, so this module centralizes: token storage, fetch +
 * 401 handling, the deps status pill, UTC formatting, HTML escaping, active-nav
 * highlighting, and the load/poll loop. Load this before each page's own <script>.
 */
(function (global) {
  'use strict';

  var TOKEN_STORAGE_KEY = 'dashboard_token';

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    if (value == null) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmtUtc(iso) {
    if (!iso) return '\u2014';
    try {
      var s = String(iso);
      var d = new Date(s.indexOf('T') !== -1 && s.slice(-1) !== 'Z' && s.indexOf('+') === -1 ? s + 'Z' : s);
      if (isNaN(d.getTime())) return s;
      return d.toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, ' UTC');
    } catch (e) {
      return String(iso);
    }
  }

  function pillStatus(elOrId, data) {
    var el = typeof elOrId === 'string' ? $(elOrId) : elOrId;
    if (!el) return;
    var st = data && data.status;
    var dep = (data && data.dependencies) || {};
    var pgDown = !!(dep.postgres && dep.postgres.ok === false);
    // Keep dash-pill so shared header status chips stay ≥44px tall.
    if (st === 'ready') {
      el.textContent = pgDown ? 'Ready \u00b7 PG down' : 'Deps OK';
      el.className =
        'dash-pill ' +
        (pgDown ? 'bg-amber-900/60 text-amber-200' : 'bg-emerald-900/60 text-emerald-300');
    } else {
      el.textContent = 'Degraded';
      el.className = 'dash-pill bg-amber-900/60 text-amber-200';
    }
  }

  function authHeaders() {
    var token = sessionStorage.getItem(TOKEN_STORAGE_KEY);
    return token ? { Authorization: 'Bearer ' + token } : {};
  }

  /**
   * Fetch JSON from the dashboard API. Shows/hides the shared #auth-gate on 401,
   * and returns null in that case (caller should stop rendering). Throws on other
   * non-OK responses so callers can report to #error-banner.
   */
  async function fetchDashboardJson(url) {
    var r = await fetch(url, { headers: authHeaders() });
    var gate = $('auth-gate');
    if (r.status === 401) {
      if (gate) gate.classList.remove('hidden');
      return null;
    }
    if (gate) gate.classList.add('hidden');
    if (!r.ok) {
      var text = await r.text();
      throw new Error('Failed to load: ' + r.status + ' ' + text);
    }
    return r.json();
  }

  /** Wires the shared #token-save button to store the token and re-run `onSaved`. */
  function wireTokenGate(onSaved) {
    var btn = $('token-save');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var input = $('token-input');
      var v = input ? input.value.trim() : '';
      if (v) sessionStorage.setItem(TOKEN_STORAGE_KEY, v);
      var gate = $('auth-gate');
      if (gate) gate.classList.add('hidden');
      if (typeof onSaved === 'function') onSaved();
    });
  }

  /**
   * Highlights the nav link matching the current page. Links opt in via a
   * `data-nav="/path"` attribute; the current page's link gets aria-current
   * (styles live in dashboard.css .dash-nav-link[aria-current]).
   */
  function markActiveNav() {
    var path = (global.location && global.location.pathname) || '/';
    var normalized = path.replace(/\/index\.html$/, '/');
    if (normalized !== '/') normalized = normalized.replace(/\/$/, '');
    if (normalized === '' || normalized === '/dashboard') normalized = '/';
    var links = document.querySelectorAll('a[data-nav]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      var target = a.getAttribute('data-nav') || '';
      if (target === normalized) {
        a.setAttribute('aria-current', 'page');
      } else {
        a.removeAttribute('aria-current');
      }
    }
  }

  /**
   * Replace #dashboard-header (or opts.mountId) with Operator Command Console
   * chrome: fixed left rail nav + sticky top status bar.
   */
  function mountHeader(opts) {
    opts = opts || {};
    var mount = $(opts.mountId || 'dashboard-header');
    if (!mount) return;
    var title = opts.title || 'tr_bot';
    var subtitle = opts.subtitle || 'Trading dashboard';
    var showRedis = opts.showRedis !== false;
    var showMode = opts.showMode !== false;
    var brandLabel = opts.brandLabel || 'tr_bot';
    var brandSub = opts.brandSub || 'ops console';
    var html =
      '<aside class="dash-rail" aria-label="Dashboard navigation">' +
      '<div class="dash-rail-brand">' +
      '<p class="dash-rail-title">' +
      escapeHtml(brandLabel) +
      '</p>' +
      '<p class="dash-rail-sub">' +
      escapeHtml(brandSub) +
      '</p>' +
      '</div>' +
      '<nav class="dash-nav dash-nav-rail" aria-label="Dashboard">' +
      '<a href="/" data-nav="/" class="dash-nav-link">Overview</a>' +
      '<a href="/dashboard/risk.html" data-nav="/dashboard/risk.html" class="dash-nav-link">Risk</a>' +
      '<a href="/dashboard/strategy.html" data-nav="/dashboard/strategy.html" class="dash-nav-link">Strategy</a>' +
      '<a href="/dashboard/scheduler.html" data-nav="/dashboard/scheduler.html" class="dash-nav-link">Jobs</a>' +
      '<a href="/dashboard/metrics.html" data-nav="/dashboard/metrics.html" class="dash-nav-link">Diagnostics</a>' +
      '</nav>' +
      '</aside>' +
      '<header class="dash-topbar">' +
      '<div class="dash-topbar-inner">' +
      '<div class="dash-header-titles">' +
      '<h1>' +
      escapeHtml(title) +
      '</h1>' +
      '<p>' +
      escapeHtml(subtitle) +
      '</p>' +
      '</div>' +
      '<div class="dash-status" aria-label="Status">' +
      '<span id="pill-status" class="dash-pill">\u2026</span>' +
      (showRedis
        ? '<span id="pill-redis" class="dash-pill">Redis</span><span id="pill-pg" class="dash-pill">Postgres</span>'
        : '') +
      (showMode ? '<span id="pill-mode" class="dash-pill">\u2026</span>' : '') +
      '<span id="pill-tier" class="dash-pill">tier</span>' +
      '</div>' +
      '</div></header>';
    mount.outerHTML = html;
    if (document.body) document.body.classList.add('dash-shell');
    markActiveNav();
  }

  function setDialogOpen(modal, open, returnFocusEl) {
    if (!modal) return;
    if (open) {
      modal.classList.remove('hidden');
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      modal._returnFocus = returnFocusEl || document.activeElement;
      var focusable = modal.querySelector(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable && typeof focusable.focus === 'function') focusable.focus();
    } else {
      modal.classList.add('hidden');
      var back = modal._returnFocus;
      modal._returnFocus = null;
      if (back && typeof back.focus === 'function') {
        try {
          back.focus();
        } catch (e) {
          /* ignore */
        }
      }
    }
  }

  /**
   * POST JSON to a dashboard write-action endpoint with auth headers. Shows the
   * shared #auth-gate on 401 (mirroring fetchDashboardJson) and throws an Error
   * (with `.status`/`.payload`) for any other non-OK response, so callers can
   * show `err.message` (the API's `detail` field when present) to the user.
   */
  async function postAction(url, body) {
    var headers = { 'Content-Type': 'application/json' };
    var auth = authHeaders();
    for (var k in auth) headers[k] = auth[k];
    var r = await fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(body || {}),
    });
    var gate = $('auth-gate');
    if (r.status === 401) {
      if (gate) gate.classList.remove('hidden');
      var err401 = new Error('Invalid or missing dashboard token');
      err401.status = 401;
      throw err401;
    }
    if (gate) gate.classList.add('hidden');
    var payload = null;
    try {
      payload = await r.json();
    } catch (e) {
      payload = null;
    }
    if (!r.ok) {
      var detail = (payload && payload.detail) || r.status + ' ' + r.statusText;
      var err = new Error(detail);
      err.status = r.status;
      err.payload = payload;
      throw err;
    }
    return payload;
  }

  /** Download an authenticated dashboard response as a local file. */
  async function downloadDashboardFile(url, filename) {
    var r = await fetch(url, { headers: authHeaders() });
    var gate = $('auth-gate');
    if (r.status === 401) {
      if (gate) gate.classList.remove('hidden');
      var err401 = new Error('Invalid or missing dashboard token');
      err401.status = 401;
      throw err401;
    }
    if (gate) gate.classList.add('hidden');
    if (!r.ok) {
      throw new Error('Download failed: ' + r.status + ' ' + (await r.text()));
    }
    var blob = await r.blob();
    var objectUrl = global.URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = objectUrl;
    a.download = filename || 'download';
    document.body.appendChild(a);
    a.click();
    a.remove();
    global.URL.revokeObjectURL(objectUrl);
  }

  function setTabPanels(tabs, panels, activeKey) {
    Object.keys(tabs || {}).forEach(function (key) {
      var btn = tabs[key];
      var active = key === activeKey;
      if (btn) {
        btn.className =
          'rounded-lg px-3 py-1.5 text-sm font-medium ' +
          (active ? 'bg-sky-700 text-white' : 'bg-slate-700 hover:bg-slate-600 text-slate-100');
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
      }
      if (panels && panels[key]) panels[key].classList.toggle('hidden', !active);
    });
  }

  function confirmAction(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var modal = $(opts.modalId || 'confirm-modal');
      var titleEl = $(opts.titleId || 'confirm-title');
      var bodyEl = $(opts.bodyId || 'confirm-body');
      var okBtn = $(opts.okId || 'confirm-ok');
      var cancelBtn = $(opts.cancelId || 'confirm-cancel');
      var body = opts.body || 'Are you sure?';
      if (!modal || !titleEl || !bodyEl || !okBtn || !cancelBtn) {
        resolve(global.confirm(body));
        return;
      }
      titleEl.textContent = opts.title || 'Confirm action';
      bodyEl.textContent = body;
      okBtn.textContent = opts.okLabel || 'Confirm';
      setDialogOpen(modal, true, document.activeElement);
      function cleanup(result) {
        setDialogOpen(modal, false);
        okBtn.onclick = null;
        cancelBtn.onclick = null;
        resolve(result);
      }
      okBtn.onclick = function () {
        cleanup(true);
      };
      cancelBtn.onclick = function () {
        cleanup(false);
      };
    });
  }

  /**
   * Poll GET /api/dashboard/jobs/{jobId} until status is "done"/"error" (or
   * `timeoutMs` elapses). Calls `onUpdate(job)` after every poll. Resolves with
   * the final job record, or throws if the poll itself is unauthorized/times out.
   */
  async function pollJob(jobId, opts) {
    opts = opts || {};
    var intervalMs = opts.intervalMs || 1500;
    var timeoutMs = opts.timeoutMs || 10 * 60 * 1000;
    var onUpdate = opts.onUpdate;
    var start = Date.now();
    for (;;) {
      var job = await fetchDashboardJson('/api/dashboard/jobs/' + encodeURIComponent(jobId));
      if (job === null) {
        var err = new Error('Invalid or missing dashboard token');
        err.status = 401;
        throw err;
      }
      if (typeof onUpdate === 'function') onUpdate(job);
      if (job.status === 'done' || job.status === 'error') return job;
      if (Date.now() - start > timeoutMs) {
        throw new Error('Timed out waiting for job ' + jobId);
      }
      await new Promise(function (resolve) {
        global.setTimeout(resolve, intervalMs);
      });
    }
  }

  /**
   * Runs `loadFn` immediately (reporting any thrown error to #error-banner), then
   * repeats every `intervalMs` (default 20s). Returns the interval id.
   */
  function startPolling(loadFn, intervalMs) {
    async function safeLoad() {
      try {
        await loadFn();
      } catch (e) {
        var b = $('error-banner');
        if (b) {
          b.textContent = 'Dashboard load error: ' + (e && e.message ? e.message : String(e));
          b.classList.remove('hidden');
        }
      }
    }
    safeLoad();
    return global.setInterval(safeLoad, intervalMs || 20000);
  }

  /**
   * Render cached legacy vs SMC compare into #compare-summary / #compare-go-no-go / #compare-json.
   * @param {{ backtest_compare?: any, smc_go_no_go?: any }} status strategy_engine_status payload
   */
  function renderCompareModal(status) {
    status = status || {};
    var summary = $('compare-summary');
    var goNoGo = $('compare-go-no-go');
    var json = $('compare-json');
    var compare = status.backtest_compare;
    var go = status.smc_go_no_go;
    if (summary) {
      if (compare && compare.legacy && compare.smc) {
        var l = compare.legacy;
        var s = compare.smc;
        summary.textContent =
          'Legacy: ' +
          (l.total_return_pct != null ? Number(l.total_return_pct).toFixed(2) + '% return' : '\u2014') +
          ' \u00b7 SMC: ' +
          (s.total_return_pct != null ? Number(s.total_return_pct).toFixed(2) + '% return' : '\u2014');
      } else {
        summary.textContent =
          'No cached compare artifact yet \u2014 run "Compare" from Quick actions or python -m tr_bot.managers backtest-compare.';
      }
    }
    if (goNoGo) {
      if (go && typeof go.go === 'boolean') {
        goNoGo.textContent = go.go ? 'Go: SMC engine recommended' : 'No-go: legacy engine recommended';
        goNoGo.className =
          'text-sm mb-3 font-medium ' + (go.go ? 'text-emerald-400' : 'text-amber-300');
      } else {
        goNoGo.textContent = '\u2014';
        goNoGo.className = 'text-sm mb-3 text-slate-400';
      }
    }
    if (json) {
      json.textContent = JSON.stringify(
        { backtest_compare: compare || null, smc_go_no_go: go || null },
        null,
        2
      );
    }
  }

  function openCompareModal(status, returnFocusEl) {
    renderCompareModal(status);
    setDialogOpen($('compare-modal'), true, returnFocusEl || document.activeElement);
  }

  function closeCompareModal() {
    setDialogOpen($('compare-modal'), false);
  }

  /** Wire open/close for compare modal. openIds: button element ids that open the modal. */
  function wireCompareModal(getStatus, openIds) {
    openIds = openIds || ['se-compare-open', 'perf-compare-open', 'bt-compare-open'];
    for (var i = 0; i < openIds.length; i++) {
      (function (id) {
        var btn = $(id);
        if (!btn) return;
        btn.addEventListener('click', function () {
          var status = typeof getStatus === 'function' ? getStatus() : getStatus;
          openCompareModal(status || {}, btn);
        });
      })(openIds[i]);
    }
    var closeBtn = $('compare-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', closeCompareModal);
    var modal = $('compare-modal');
    if (modal) {
      modal.addEventListener('click', function (ev) {
        if (ev.target === modal) closeCompareModal();
      });
    }
  }

  global.TRDashboard = {
    $: $,
    escapeHtml: escapeHtml,
    fmtUtc: fmtUtc,
    pillStatus: pillStatus,
    authHeaders: authHeaders,
    fetchDashboardJson: fetchDashboardJson,
    postAction: postAction,
    downloadDashboardFile: downloadDashboardFile,
    setTabPanels: setTabPanels,
    confirmAction: confirmAction,
    pollJob: pollJob,
    wireTokenGate: wireTokenGate,
    markActiveNav: markActiveNav,
    mountHeader: mountHeader,
    setDialogOpen: setDialogOpen,
    startPolling: startPolling,
    renderCompareModal: renderCompareModal,
    openCompareModal: openCompareModal,
    closeCompareModal: closeCompareModal,
    wireCompareModal: wireCompareModal,
  };
})(window);

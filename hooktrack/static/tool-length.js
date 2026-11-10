/* HookTrack free "best video length" analyzer. Runs entirely in the visitor's browser: nothing is uploaded or stored.
   It finds which video lengths did best for THIS account, measured against the account's own normal (median views). */
(function (root) {
  'use strict';
  var MIN_VIDEOS = 10, GOOD_VIDEOS = 20, MAX_VIDEOS = 400;
  // upper edge in seconds (exclusive) and label
  var BUCKETS = [
    [15, 'under 15 s'], [30, '15–30 s'], [45, '30–45 s'], [60, '45–60 s'],
    [90, '1–1.5 min'], [120, '1.5–2 min'], [180, '2–3 min'], [Infinity, 'over 3 min']
  ];

  function median(values) {
    var a = values.slice().sort(function (x, y) { return x - y; }), n = a.length;
    if (!n) return 0;
    return n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2;
  }

  function toViews(tok) {
    var m = String(tok).trim().toLowerCase().match(/^(\d[\d,]*(?:\.\d+)?)\s*([km])?\s*(?:views?)?$/);
    if (!m) return NaN;
    var n = parseFloat(m[1].replace(/,/g, ''));
    if (m[2] === 'k') n *= 1e3; else if (m[2] === 'm') n *= 1e6;
    return Math.round(n);
  }

  // "45", "45s", "0:45", "1:30", "1m 30s", "1.5m"
  function parseLength(text) {
    var s = String(text == null ? '' : text).trim().toLowerCase();
    if (!s) return NaN;
    var m = s.match(/^(\d+):(\d{1,2})$/);
    if (m) return (+m[1]) * 60 + (+m[2]);
    m = s.match(/^(?:(\d+(?:\.\d+)?)\s*m(?:in(?:ute)?s?)?)?\s*(?:(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?)?$/);
    if (m && (m[1] || m[2])) return Math.round((+(m[1] || 0)) * 60 + (+(m[2] || 0)));
    if (/^\d+(\.\d+)?$/.test(s)) return Math.round(parseFloat(s));
    return NaN;
  }

  // One video per line: "length, views" or "title, length, views" (commas, pipes or tabs), or "0:34 4200" with spaces.
  function parse(text) {
    var rows = [], skipped = 0, lines = String(text || '').split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;
      // split on ; | tab and on commas, except a comma inside a number like 3,900
      var toks = line.split(/\s*[;|\t]\s*|\s*,(?!\d{3}\b)\s*/).map(function (t) { return t.trim(); }).filter(Boolean);
      if (toks.length === 1) toks = line.split(/\s+/);
      var views = NaN, secs = NaN, title = '';
      if (toks.length >= 2) {
        views = toViews(toks[toks.length - 1]);
        secs = parseLength(toks[toks.length - 2]);
        title = toks.slice(0, toks.length - 2).join(', ').slice(0, 70);
      }
      if (isNaN(views) || isNaN(secs) || secs <= 0 || views < 0) { skipped++; continue; }
      rows.push({ title: title || ('Video ' + (rows.length + 1)), seconds: secs, views: views });
    }
    return { rows: rows, skipped: skipped };
  }

  function ranks(values) {
    var idx = values.map(function (v, i) { return i; }).sort(function (a, b) { return values[a] - values[b]; });
    var r = new Array(values.length), i = 0;
    while (i < idx.length) {
      var j = i;
      while (j + 1 < idx.length && values[idx[j + 1]] === values[idx[i]]) j++;
      var avg = (i + j) / 2 + 1;
      for (var k = i; k <= j; k++) r[idx[k]] = avg;
      i = j + 1;
    }
    return r;
  }

  // Spearman rank correlation between length and views (-1 .. 1); 0 when one of them never varies
  function spearman(x, y) {
    var n = x.length;
    if (n < 3) return 0;
    var rx = ranks(x), ry = ranks(y), mx = 0, my = 0, i;
    for (i = 0; i < n; i++) { mx += rx[i]; my += ry[i]; }
    mx /= n; my /= n;
    var num = 0, dx = 0, dy = 0;
    for (i = 0; i < n; i++) { num += (rx[i] - mx) * (ry[i] - my); dx += Math.pow(rx[i] - mx, 2); dy += Math.pow(ry[i] - my, 2); }
    return dx && dy ? num / Math.sqrt(dx * dy) : 0;
  }

  function bucketOf(secs) { for (var i = 0; i < BUCKETS.length; i++) if (secs < BUCKETS[i][0]) return i; return BUCKETS.length - 1; }

  function analyze(text) {
    var p = parse(text), rows = p.rows;
    if (rows.length < MIN_VIDEOS) {
      return { ok: false, error: 'Add at least ' + MIN_VIDEOS + ' videos, each with its length and views (20 or more gives a reliable answer).' +
        (p.skipped ? ' ' + p.skipped + ' line(s) could not be read. Use a format like 0:34, 4200.' : '') };
    }
    if (rows.length > MAX_VIDEOS) rows = rows.slice(0, MAX_VIDEOS);
    var normal = median(rows.map(function (r) { return r.views; }));
    if (normal <= 0) return { ok: false, error: 'Your median is 0 views, so there is nothing to compare against. Check the numbers.' };

    var groups = BUCKETS.map(function () { return []; });
    rows.forEach(function (r) { r.multiple = r.views / normal; groups[bucketOf(r.seconds)].push(r.multiple); });
    var buckets = [];
    groups.forEach(function (g, i) { if (g.length) buckets.push({ index: i, label: BUCKETS[i][1], n: g.length, m: median(g) }); });
    var ranked = buckets.filter(function (b) { return b.n >= 2; }).sort(function (a, b) { return b.m - a.m || b.n - a.n; });
    var best = ranked[0] || null;

    var rho = spearman(rows.map(function (r) { return r.seconds; }), rows.map(function (r) { return r.views; }));
    var trend = rho >= 0.4 ? 'longer' : (rho <= -0.4 ? 'shorter' : 'flat');

    var byViews = rows.slice().sort(function (a, b) { return b.views - a.views; });
    var q = Math.max(2, Math.round(rows.length / 4));
    var topLen = median(byViews.slice(0, q).map(function (r) { return r.seconds; }));
    var botLen = median(byViews.slice(-q).map(function (r) { return r.seconds; }));

    var distinct = buckets.length;
    return {
      ok: true, count: rows.length, skipped: p.skipped, normal: normal, buckets: buckets, best: best, ranked: ranked,
      medianLength: median(rows.map(function (r) { return r.seconds; })), rho: rho, trend: trend, topLength: topLen, bottomLength: botLen,
      onlyOneBucket: distinct < 2, lowConfidence: rows.length < GOOD_VIDEOS || !best || best.n < 3
    };
  }

  var api = { analyze: analyze, parse: parse, parseLength: parseLength, spearman: spearman, median: median, BUCKETS: BUCKETS };
  root.HTLength = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  // ---------------- page ----------------
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function x(n) { return (n >= 10 ? n.toFixed(0) : n.toFixed(1)) + '×'; }
  function fmt(n) { return Math.round(n).toLocaleString('en-US'); }
  function secs(n) { n = Math.round(n); return n < 60 ? n + ' s' : Math.floor(n / 60) + ':' + (n % 60 < 10 ? '0' : '') + (n % 60); }

  function sample() {
    // 28 videos: 20 to 40 second videos do best, very short and long ones do worse, so the demo shows a clear pattern
    var plan = [[12, 3200], [18, 9800], [24, 14500], [28, 12800], [33, 13900], [38, 9400], [44, 7100], [52, 5200], [66, 4300], [95, 3100], [150, 2500], [22, 11700], [30, 15200], [36, 10100]];
    var lines = [];
    plan.forEach(function (p, i) {
      lines.push(secs(p[0]) + ', ' + p[1]);
      lines.push(secs(Math.max(8, p[0] + (i % 3) - 1)) + ', ' + Math.round(p[1] * (0.86 + (i % 5) * 0.06)));
    });
    return lines.join('\n');
  }

  function render(r, host, platform) {
    if (!r.ok) { host.hidden = false; host.innerHTML = '<div class="tool-err" role="alert">' + esc(r.error) + '</div>'; return; }
    var rows = r.buckets.map(function (b) {
      var w = Math.max(2, Math.min(100, (b.m / 3) * 100)), cls = b.m >= 1.25 ? 'winner' : (b.m <= 0.8 ? 'dud' : 'average');
      return '<tr><td class="t">' + esc(b.label) + '</td><td class="num">' + b.n + '</td><td class="num">' + x(b.m) + '</td>' +
        '<td class="barcell"><span class="bar ' + cls + '" style="width:' + w.toFixed(0) + '%"></span></td></tr>';
    }).join('');
    var trendText = { longer: 'In your videos, <b>longer ones tended to get more views</b>.', shorter: 'In your videos, <b>shorter ones tended to get more views</b>.',
      flat: '<b>Length does not seem to matter much</b> for your videos. Other things, like the hook, probably decide how they do.' }[r.trend];
    var notes = [];
    if (r.onlyOneBucket) notes.push('<div class="tool-note">Almost all your videos are about the same length, so there is nothing to compare. Try a few different lengths and check again.</div>');
    if (r.lowConfidence) notes.push('<div class="tool-note"><b>Treat this as a hint, not an answer.</b> ' + (r.count < 20 ? 'You gave ' + r.count + ' videos, and a reliable pattern needs 20 or more. ' : '') +
      'Some length ranges rest on only two or three videos, so one lucky video can decide them. Add more videos, or test your best range for a few weeks to confirm.</div>');
    if (r.skipped) notes.push('<div class="tool-note">' + r.skipped + ' line(s) could not be read and were skipped. Use a format like <code>0:34, 4200</code>.</div>');
    host.hidden = false;
    host.innerHTML =
      '<div class="stats">' +
      '<div class="stat"><span>Best length range</span><b>' + (r.best ? esc(r.best.label) : '–') + '</b><small>' + (r.best ? x(r.best.m) + ' your normal, from ' + r.best.n + ' videos' : 'not enough data') + '</small></div>' +
      '<div class="stat"><span>Your top videos</span><b>' + secs(r.topLength) + '</b><small>median length of your best quarter</small></div>' +
      '<div class="stat"><span>Your weakest videos</span><b>' + secs(r.bottomLength) + '</b><small>median length of your lowest quarter</small></div>' +
      '<div class="stat"><span>Your normal</span><b>' + fmt(r.normal) + '</b><small>median views, ' + r.count + ' videos</small></div>' +
      '</div><p>' + trendText + '</p>' + notes.join('') +
      '<h3>Every length range</h3><div class="table-wrap"><table class="vtable"><thead><tr><th>Length</th><th class="num">Videos</th><th class="num">Median vs your normal</th><th>Above or below normal</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<p class="muted small">Each row is the median of your videos in that length range, as a multiple of your overall normal. Length is only one factor, this is correlation and not proof, and the hook usually matters more. Nothing you paste leaves your browser.</p>' +
      '<div class="tool-actions"><a class="btn primary" href="/#pricing">Track every post across all your accounts automatically</a></div>';
    var top = host.getBoundingClientRect().top + window.pageYOffset - 80;
    if (window.scrollTo) window.scrollTo({ top: top, behavior: 'smooth' });
  }

  function init() {
    var box = document.getElementById('lengthtool');
    if (!box) return;
    var ta = box.querySelector('#ln-data'), out = box.querySelector('#ln-out'), platform = box.getAttribute('data-platform') || 'Video';
    box.querySelector('#ln-go').addEventListener('click', function () { render(analyze(ta.value), out, platform); });
    box.querySelector('#ln-sample').addEventListener('click', function () { ta.value = sample(); ta.focus(); });
    box.querySelector('#ln-clear').addEventListener('click', function () { ta.value = ''; out.hidden = true; ta.focus(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})(typeof window !== 'undefined' ? window : globalThis);

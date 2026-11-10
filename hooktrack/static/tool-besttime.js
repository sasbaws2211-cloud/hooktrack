/* HookTrack free "best time to post" analyzer. Runs entirely in the visitor's browser: nothing is uploaded or stored.
   It finds the days and hours when THIS channel's videos did best against its own normal (median views). */
(function (root) {
  'use strict';
  var MIN_VIDEOS = 8, GOOD_VIDEOS = 20, MAX_VIDEOS = 400;
  var DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  var BLOCKS = ['12–3 am', '3–6 am', '6–9 am', '9 am–12 pm', '12–3 pm', '3–6 pm', '6–9 pm', '9 pm–12 am'];

  function median(values) {
    var a = values.slice().sort(function (x, y) { return x - y; }), n = a.length;
    if (!n) return 0;
    return n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2;
  }

  function toViews(tok, suffix) {
    var n = parseFloat(String(tok).replace(/,/g, ''));
    if (isNaN(n)) return NaN;
    var s = (suffix || '').toLowerCase();
    if (s === 'k') n *= 1e3; else if (s === 'm') n *= 1e6;
    return Math.round(n);
  }

  // The date and time exactly as typed (no time zone shifting): ISO "2026-10-04 18:30", or anything Date understands, like
  // "Oct 4, 2026 6:30 PM" or "10/04/2026 18:30" (month first).
  function parseWhen(s) {
    s = s.trim();
    var m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})[\sT,]+(\d{1,2}):(\d{2})/);
    var d;
    if (m) d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    else {
      if (!/\d{1,2}:\d{2}/.test(s)) return null;           // a time of day is required
      if (!/\b(19|20)\d{2}\b/.test(s)) return null;        // and a year, or the weekday would be a guess
      var t = Date.parse(s);
      if (isNaN(t)) return null;
      d = new Date(t);
    }
    return isNaN(d.getTime()) ? null : d;
  }

  function parse(text) {
    var rows = [], skipped = 0, lines = String(text || '').split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;
      var m = line.match(/^(.*?)(?:^|[\s,;|\t])(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?\s*(?:views?)?\s*$/);
      var when = null, views = NaN;
      if (m) {
        views = toViews(m[2], m[3]);
        when = parseWhen(m[1].replace(/[\s,;|\t]+$/, ''));
      }
      if (!when || isNaN(views) || views < 0) { skipped++; continue; }
      rows.push({ when: when, views: views });
    }
    return { rows: rows, skipped: skipped };
  }

  function analyze(text) {
    var p = parse(text), rows = p.rows;
    if (rows.length < MIN_VIDEOS) {
      return { ok: false, error: 'Add at least ' + MIN_VIDEOS + ' videos, each with a date, a time and its views (20 or more gives a reliable answer).' +
        (p.skipped ? ' ' + p.skipped + ' line(s) could not be read. Use a format like 2026-10-04 18:30, 4200.' : '') };
    }
    if (rows.length > MAX_VIDEOS) rows = rows.slice(0, MAX_VIDEOS);
    var normal = median(rows.map(function (r) { return r.views; }));
    if (normal <= 0) return { ok: false, error: 'Your median is 0 views, so there is nothing to compare against. Check the numbers.' };

    var cells = [], d, b;
    for (d = 0; d < 7; d++) { cells.push([]); for (b = 0; b < 8; b++) cells[d].push([]); }
    var dayVals = [[], [], [], [], [], [], []], blockVals = [[], [], [], [], [], [], [], []];
    rows.forEach(function (r) {
      var mult = r.views / normal, day = (r.when.getDay() + 6) % 7, block = Math.floor(r.when.getHours() / 3);
      cells[day][block].push(mult); dayVals[day].push(mult); blockVals[block].push(mult);
    });
    var grid = cells.map(function (row) { return row.map(function (v) { return { n: v.length, m: v.length ? median(v) : null }; }); });
    function rank(list, key) {
      return list.map(function (v, i) { return { i: i, n: v.length, m: v.length ? median(v) : null }; })
        .filter(function (x) { return x.n >= 2; }).sort(function (a, b) { return b.m - a.m || b.n - a.n; });
    }
    var days = rank(dayVals), blocks = rank(blockVals);
    var slots = [];
    for (d = 0; d < 7; d++) for (b = 0; b < 8; b++) if (grid[d][b].n >= 2) slots.push({ day: d, block: b, n: grid[d][b].n, m: grid[d][b].m });
    slots.sort(function (a, c) { return c.m - a.m || c.n - a.n; });
    var best = slots[0] || null;
    var low = rows.length < GOOD_VIDEOS || !best || best.n < 3;
    return {
      ok: true, count: rows.length, skipped: p.skipped, normal: normal, grid: grid,
      bestDay: days[0] ? { day: days[0].i, m: days[0].m, n: days[0].n } : null,
      bestBlock: blocks[0] ? { block: blocks[0].i, m: blocks[0].m, n: blocks[0].n } : null,
      slots: slots.slice(0, 3), best: best, lowConfidence: low
    };
  }

  var api = { analyze: analyze, parse: parse, parseWhen: parseWhen, median: median, DAYS: DAYS, BLOCKS: BLOCKS };
  root.HTBestTime = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  // ---------------- page ----------------
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function x(n) { return (n >= 10 ? n.toFixed(0) : n.toFixed(1)) + '×'; }
  function fmt(n) { return Math.round(n).toLocaleString('en-US'); }

  function sample() {
    // 8 weeks of posts, with Thursday and Friday evenings doing best, so the demo shows what a clear pattern looks like
    var lines = [], base = new Date(2026, 8, 7);   // a Monday
    var plan = [[0, 9, 4200], [1, 12, 5200], [2, 18, 7800], [3, 18, 15500], [4, 19, 13800], [5, 10, 6100], [6, 20, 8900]];
    for (var w = 0; w < 4; w++) {
      plan.forEach(function (p, i) {
        if ((w + i) % 4 === 3) return;
        var d = new Date(base.getFullYear(), base.getMonth(), base.getDate() + w * 7 + p[0], p[1], (i * 7) % 60);
        var jitter = 0.85 + ((w * 3 + i * 5) % 7) / 20;
        var pad = function (n) { return (n < 10 ? '0' : '') + n; };
        lines.push(d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ', ' + Math.round(p[2] * jitter));
      });
    }
    return lines.join('\n');
  }

  function cellStyle(m) {
    if (m === null) return '';
    var pct = m >= 1 ? Math.min(75, 12 + (m - 1) * 60) : Math.min(55, 12 + (1 - m) * 60);
    return ' style="background:color-mix(in srgb,var(--' + (m >= 1 ? 'good' : 'bad') + ') ' + pct.toFixed(0) + '%,var(--surface))"';
  }

  function render(r, host) {
    if (!r.ok) { host.hidden = false; host.innerHTML = '<div class="tool-err" role="alert">' + esc(r.error) + '</div>'; return; }
    var head = '<tr><th></th>' + BLOCKS.map(function (b) { return '<th>' + b + '</th>'; }).join('') + '</tr>';
    var body = r.grid.map(function (row, d) {
      return '<tr><th>' + DAYS[d].slice(0, 3) + '</th>' + row.map(function (c) {
        return c.n ? '<td class="hm"' + cellStyle(c.m) + '><b>' + x(c.m) + '</b><small>' + c.n + (c.n === 1 ? ' video' : ' videos') + '</small></td>' : '<td class="hm none">–</td>';
      }).join('') + '</tr>';
    }).join('');
    var top = r.slots.map(function (s, i) {
      return '<li><b>' + DAYS[s.day] + ', ' + BLOCKS[s.block] + '</b> &middot; ' + x(s.m) + ' your normal, from ' + s.n + ' videos</li>';
    }).join('');
    var notes = [];
    if (r.lowConfidence) notes.push('<div class="tool-note"><b>Treat this as a hint, not an answer.</b> ' + (r.count < 20 ? 'You gave ' + r.count + ' videos, and a reliable pattern needs 20 or more. ' : '') +
      'Some of these slots rest on only two or three videos, so one lucky video can decide them. Add more videos, or post the same kind of video in your top slots for a few weeks to confirm.</div>');
    if (!r.best) notes.push('<div class="tool-note">No day and time had at least two videos yet, so there is no ranking. Add more videos to see a pattern.</div>');
    if (r.skipped) notes.push('<div class="tool-note">' + r.skipped + ' line(s) could not be read and were skipped. Use a format like <code>2026-10-04 18:30, 4200</code>.</div>');
    host.hidden = false;
    host.innerHTML =
      '<div class="stats">' +
      '<div class="stat"><span>Best day</span><b>' + (r.bestDay ? DAYS[r.bestDay.day] : '–') + '</b><small>' + (r.bestDay ? x(r.bestDay.m) + ' your normal' : 'not enough data') + '</small></div>' +
      '<div class="stat"><span>Best time of day</span><b>' + (r.bestBlock ? BLOCKS[r.bestBlock.block] : '–') + '</b><small>' + (r.bestBlock ? x(r.bestBlock.m) + ' your normal' : 'not enough data') + '</small></div>' +
      '<div class="stat"><span>Your normal</span><b>' + fmt(r.normal) + '</b><small>median views, ' + r.count + ' videos</small></div>' +
      '</div>' + notes.join('') +
      (top ? '<h3>Your best slots</h3><ol class="slots">' + top + '</ol>' : '') +
      '<h3>Every day and time</h3><div class="table-wrap"><table class="heat"><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>' +
      '<p class="muted small">Each cell is the median of your videos posted then, as a multiple of your overall normal. Green is above normal, red below. The times are the ones you typed, so use your own time zone. Posting time is only one factor: it is correlation, not proof, and the video itself usually matters more.</p>' +
      '<div class="tool-actions">' + (window.HTCta ? HTCta.html('Track every post across all your accounts automatically') : '<a class="btn primary" href="/#pricing">Track every post across all your accounts automatically</a>') + '</div>';
    var topPx = host.getBoundingClientRect().top + window.pageYOffset - 80;
    if (window.scrollTo) window.scrollTo({ top: topPx, behavior: 'smooth' });
  }

  function init() {
    var box = document.getElementById('besttime');
    if (!box) return;
    var ta = box.querySelector('#bt-data'), out = box.querySelector('#bt-out');
    box.querySelector('#bt-go').addEventListener('click', function () { render(analyze(ta.value), out); });
    box.querySelector('#bt-sample').addEventListener('click', function () { ta.value = sample(); ta.focus(); });
    box.querySelector('#bt-clear').addEventListener('click', function () { ta.value = ''; out.hidden = true; ta.focus(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})(typeof window !== 'undefined' ? window : globalThis);

/* HookTrack free video performance checker. Runs entirely in the visitor's browser: nothing is uploaded or stored.
   Rules mirror the app: a video is a WINNER at 2x the account's median views, a DUD at 0.5x or less, otherwise AVERAGE. */
(function (root) {
  'use strict';
  var WINNER = 2, DUD = 0.5, MIN_VIDEOS = 5, MAX_VIDEOS = 100;
  var VERDICT = { WINNER: 'Scale', AVERAGE: 'Hold', DUD: 'Tweak' };

  function toNumber(tok, suffix) {
    var n = parseFloat(String(tok).replace(/,/g, ''));
    if (isNaN(n)) return NaN;
    var s = (suffix || '').toLowerCase();
    if (s === 'k') n *= 1e3; else if (s === 'm') n *= 1e6;
    return Math.round(n);
  }

  // One video per line. "12400", "12,400", "12.4k", "1.2M", or "Title, 12400" / "Title - 12.4k views".
  function parse(text) {
    var out = [], lines = String(text || '').split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;
      var m = line.match(/(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?\s*(?:views?)?\s*$/);
      if (!m) continue;
      var views = toNumber(m[1], m[2]);
      if (isNaN(views) || views < 0) continue;
      var title = line.slice(0, m.index).replace(/[\s\-–—|,:;\t]+$/g, '').trim().slice(0, 70);
      out.push({ title: title || ('Video ' + (out.length + 1)), views: views });
    }
    return out;
  }

  function median(values) {
    var a = values.slice().sort(function (x, y) { return x - y; }), n = a.length;
    if (!n) return 0;
    return n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2;
  }

  function label(multiple) {
    if (multiple >= WINNER) return 'WINNER';
    if (multiple <= DUD) return 'DUD';
    return 'AVERAGE';
  }

  // newestFirst: whether the first line you pasted is your most recent video (used only for the "last 3 in a row" check).
  function analyze(text, newestFirst) {
    var vids = parse(text);
    if (vids.length < MIN_VIDEOS) return { ok: false, error: 'Add at least ' + MIN_VIDEOS + ' videos (10 to 30 works best). One number per line.' };
    if (vids.length > MAX_VIDEOS) vids = vids.slice(0, MAX_VIDEOS);
    var normal = median(vids.map(function (v) { return v.views; }));
    if (normal <= 0) return { ok: false, error: 'Your median is 0 views, so there is nothing to compare against. Check the numbers.' };
    var sum = 0, winners = 0, duds = 0;
    vids.forEach(function (v, i) {
      v.index = i;
      v.multiple = v.views / normal;
      v.label = label(v.multiple);
      v.verdict = VERDICT[v.label];
      sum += v.views;
      if (v.label === 'WINNER') winners++;
      if (v.label === 'DUD') duds++;
    });
    var recent = (newestFirst === false ? vids.slice().reverse() : vids).slice(0, 3);
    var stop = recent.length === 3 && recent.every(function (v) { return v.label === 'DUD'; });
    var average = sum / vids.length;
    var best = vids.reduce(function (a, b) { return b.views > a.views ? b : a; });
    var worst = vids.reduce(function (a, b) { return b.views < a.views ? b : a; });
    return {
      ok: true, videos: vids, normal: normal, average: average, count: vids.length,
      winners: winners, duds: duds, winRate: winners / vids.length, stop: stop,
      skewed: average > normal * 1.5, best: best, worst: worst
    };
  }

  var api = { analyze: analyze, parse: parse, median: median, label: label, WINNER: WINNER, DUD: DUD };
  root.HTAnalyze = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  // ---------------- page ----------------
  var SAMPLE = [
    'Morning routine that changed my skin, 18400', 'Three mistakes new creators make, 9600', 'I tried the viral hack, 52300',
    'Day in my life, 8700', 'Before and after, 11200', 'Stop doing this one thing, 4100', 'Quick tutorial, 9900',
    'Reacting to your comments, 7600', 'The truth about my first year, 38700', 'Behind the scenes, 6800',
    'Q and A, 3900', 'Weekly recap, 10300'
  ].join('\n');

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function fmt(n) { return Math.round(n).toLocaleString('en-US'); }
  function x(n) { return (n >= 10 ? n.toFixed(0) : n.toFixed(1)) + '×'; }

  function render(r, host, platform) {
    if (!r.ok) { host.hidden = false; host.innerHTML = '<div class="tool-err" role="alert">' + esc(r.error) + '</div>'; return; }
    var rows = r.videos.map(function (v) {
      var w = Math.max(2, Math.min(100, (v.multiple / 4) * 100));
      return '<tr><td class="t">' + esc(v.title) + '</td><td class="num">' + fmt(v.views) + '</td><td class="num">' + x(v.multiple) +
        '</td><td class="barcell"><span class="bar ' + v.label.toLowerCase() + '" style="width:' + w.toFixed(0) + '%"></span><span class="mark" aria-hidden="true"></span></td>' +
        '<td><span class="pill ' + v.label.toLowerCase() + '">' + v.verdict + '</span></td></tr>';
    }).join('');
    var notes = [];
    if (r.stop) notes.push('<div class="tool-note bad"><b>Your last three videos were all duds.</b> One weak video is a tweak. Three in a row usually means the angle or format needs rethinking, not another small edit.</div>');
    if (r.skewed) notes.push('<div class="tool-note"><b>A few big hits are lifting your average.</b> Your average is ' + fmt(r.average) + ' views but your normal (median) is ' + fmt(r.normal) + '. The median is the fairer yardstick, because one viral video does not move it much.</div>');
    if (!r.winners) notes.push('<div class="tool-note">No video reached 2× your normal. That is common. Look at saves, shares and comments to find the ones that did better than their views suggest.</div>');
    host.hidden = false;
    host.innerHTML =
      '<div class="stats">' +
      '<div class="stat"><span>Your normal</span><b>' + fmt(r.normal) + '</b><small>median views</small></div>' +
      '<div class="stat"><span>Winners</span><b>' + r.winners + ' of ' + r.count + '</b><small>at 2× your normal or more</small></div>' +
      '<div class="stat"><span>Duds</span><b>' + r.duds + ' of ' + r.count + '</b><small>at half your normal or less</small></div>' +
      '<div class="stat"><span>Best</span><b>' + x(r.best.views / r.normal) + '</b><small>' + esc(r.best.title) + '</small></div>' +
      '</div>' + notes.join('') +
      '<div class="table-wrap"><table class="vtable"><thead><tr><th>Video</th><th class="num">Views</th><th class="num">vs your normal</th><th>Compared with your normal (line = 1×)</th><th>Verdict</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<p class="muted small">Scale = 2× your normal or more. Hold = in between. Tweak = half your normal or less. This checker uses views only, from the numbers you pasted, and nothing leaves your browser.</p>' +
      '<div class="tool-actions"><button type="button" class="btn" id="copy">Copy summary</button> ' +
      (window.HTCta ? HTCta.html('Track this automatically across all your accounts') : '<a class="btn primary" href="/#pricing">Track this automatically across all your accounts</a>') + '</div>';
    var copy = host.querySelector('#copy');
    if (copy) copy.addEventListener('click', function () {
      var t = platform + ' check: normal ' + fmt(r.normal) + ' views, ' + r.winners + ' winners and ' + r.duds + ' duds out of ' + r.count + ' videos. Best: ' +
        r.best.title + ' (' + x(r.best.views / r.normal) + ').';
      if (navigator.clipboard) navigator.clipboard.writeText(t).then(function () { copy.textContent = 'Copied'; });
    });
    var top = host.getBoundingClientRect().top + window.pageYOffset - 80;
    if (window.scrollTo) window.scrollTo({ top: top, behavior: 'smooth' });
  }

  function init() {
    var box = document.getElementById('tool');
    if (!box) return;
    var ta = box.querySelector('#views'), out = box.querySelector('#out'), platform = box.getAttribute('data-platform') || 'Video';
    box.querySelector('#go').addEventListener('click', function () {
      render(analyze(ta.value, box.querySelector('#order').value !== 'oldest'), out, platform);
    });
    box.querySelector('#sample').addEventListener('click', function () { ta.value = SAMPLE; ta.focus(); });
    box.querySelector('#clear').addEventListener('click', function () { ta.value = ''; out.hidden = true; ta.focus(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})(typeof window !== 'undefined' ? window : globalThis);

/* HookTrack free short-form video length and spec checker. Runs entirely in the visitor's browser.
   Honesty rule: a figure is marked "confirmed" only if it was read on the platform's own help page. Anything else is
   "reported" (third-party guides, which disagree) and the checker says "check the app" instead of giving a firm pass. */
(function (root) {
  'use strict';
  var CHECKED = '10 November 2026';

  var SPECS = {
    shorts: {
      name: 'YouTube Shorts', source: 'YouTube Help (official)',
      limit: 180, limitConfirmed: true,
      ratio: 'vertical_or_square', ratioConfirmed: true,
      maxRes: 1080, maxResConfirmed: true,
      note: 'Up to 3 minutes. Square or vertical only. Maximum resolution 1080p.'
    },
    reels: {
      name: 'Instagram Reels', source: 'third-party guides (not confirmed on Instagram\'s own pages)',
      limit: 180, limitConfirmed: false, olderLimit: 90,
      ratio: 'vertical916', ratioConfirmed: false,
      note: 'Commonly reported as up to 3 minutes (older guides say 90 seconds). 9:16 vertical is the standard.'
    },
    tiktok: {
      name: 'TikTok', source: 'third-party guides (not confirmed on TikTok\'s own pages)',
      limit: 600, limitConfirmed: false,
      ratio: 'vertical916', ratioConfirmed: false,
      note: 'Commonly reported as up to 10 minutes for in-app recording and a safe upload length. Some guides report longer uploads for some accounts. 9:16 vertical is the standard.'
    }
  };

  // "45", "0:45", "1:30", "1m 30s", "90s", "1.5" (minutes if it has a colon or m, otherwise seconds)
  function parseDuration(text) {
    var s = String(text == null ? '' : text).trim().toLowerCase();
    if (!s) return NaN;
    var m = s.match(/^(\d+):(\d{1,2})(?::(\d{1,2}))?$/);
    if (m) return m[3] !== undefined ? (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]) : (+m[1]) * 60 + (+m[2]);
    m = s.match(/^(?:(\d+(?:\.\d+)?)\s*m(?:in(?:ute)?s?)?)?\s*(?:(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?)?$/);
    if (m && (m[1] || m[2])) return Math.round((+(m[1] || 0)) * 60 + (+(m[2] || 0)));
    if (/^\d+(\.\d+)?$/.test(s)) return Math.round(parseFloat(s));
    return NaN;
  }

  function classify(w, h) {
    if (!(w > 0 && h > 0)) return null;
    var r = w / h;
    if (Math.abs(r - 9 / 16) <= 0.02) return 'vertical916';
    if (r < 0.98) return 'vertical';
    if (r <= 1.02) return 'square';
    return 'horizontal';
  }

  function fmtSecs(n) {
    if (n < 60) return n + ' seconds';
    var m = Math.floor(n / 60), s = n % 60;
    return m + ' minute' + (m === 1 ? '' : 's') + (s ? ' ' + s + ' seconds' : '');
  }

  function limitWord(n) { return n >= 60 && n % 60 === 0 ? (n / 60) + '-minute' : n + '-second'; }

  // status: 'fits' | 'check' | 'over'
  function checkLength(spec, secs) {
    if (!(secs > 0)) return null;
    if (secs > spec.limit) {
      return { status: 'over', text: 'Too long: ' + fmtSecs(secs) + ' is over the ' + (spec.limitConfirmed ? '' : 'commonly reported ') + limitWord(spec.limit) + ' limit.' +
        (spec.limitConfirmed ? '' : ' Check the app: limits change and can differ by account.') };
    }
    if (spec.limitConfirmed) return { status: 'fits', text: 'Fits: ' + fmtSecs(secs) + ' is within the ' + limitWord(spec.limit) + ' limit.' };
    if (spec.olderLimit && secs > spec.olderLimit) {
      return { status: 'check', text: 'Probably fits: within the commonly reported ' + limitWord(spec.limit) + ' limit, but over the older ' + limitWord(spec.olderLimit) + ' limit. Check the app.' };
    }
    return { status: 'check', text: 'Probably fits: within the commonly reported ' + limitWord(spec.limit) + ' limit. Check the app before you rely on it.' };
  }

  function checkRatio(key, spec, kind) {
    if (!kind) return null;
    var label = { vertical916: '9:16 vertical', vertical: 'vertical (not 9:16)', square: 'square', horizontal: 'horizontal' }[kind];
    if (spec.ratio === 'vertical_or_square') {
      if (kind === 'vertical916' || kind === 'vertical' || kind === 'square') return { status: 'fits', text: 'Fits: ' + label + ' video can be a Short.' };
      return { status: 'over', text: 'Not a Short: horizontal video is not eligible. Re-export it as vertical or square.' };
    }
    if (kind === 'vertical916') return { status: 'check', text: 'Matches the standard 9:16 vertical shape for ' + spec.name + ', as reported by third-party guides. Confirm in the app.' };
    if (kind === 'horizontal') return { status: 'over', text: 'Will play with black bars or be cropped. Re-export as 9:16 vertical for ' + spec.name + '.' };
    return { status: 'check', text: label.charAt(0).toUpperCase() + label.slice(1) + ' video is accepted but may be cropped or padded. 9:16 fills the screen.' };
  }

  function checkRes(spec, w, h) {
    if (!(w > 0 && h > 0)) return null;
    var short = Math.min(w, h);
    if (spec.maxRes && short > spec.maxRes) return { status: 'check', text: 'Above ' + spec.maxRes + 'p: ' + spec.name + ' states a maximum of ' + spec.maxRes + 'p, so a higher resolution adds nothing.' };
    if (short < 720) return { status: 'check', text: 'Low resolution: under 720 pixels on the short side can look soft. 1080 by 1920 is the usual size.' };
    return { status: 'fits', text: 'Resolution looks fine. 1080 by 1920 is the usual size for vertical video.' };
  }

  // input: { seconds, width, height }  ->  { platforms: [{key, name, source, checks: [{kind, status, text}], overall}] }
  function analyze(input) {
    var secs = input.seconds, kind = classify(input.width, input.height);
    var order = ['shorts', 'reels', 'tiktok'];
    var out = order.map(function (k) {
      var spec = SPECS[k], checks = [];
      var l = checkLength(spec, secs); if (l) checks.push({ kind: 'Length', status: l.status, text: l.text });
      var a = checkRatio(k, spec, kind); if (a) checks.push({ kind: 'Shape', status: a.status, text: a.text });
      var r = checkRes(spec, input.width, input.height); if (r) checks.push({ kind: 'Resolution', status: r.status, text: r.text });
      var overall = checks.some(function (c) { return c.status === 'over'; }) ? 'over' : (checks.some(function (c) { return c.status === 'check'; }) ? 'check' : 'fits');
      return { key: k, name: spec.name, source: spec.source, note: spec.note, checks: checks, overall: overall };
    });
    return { ok: out.some(function (p) { return p.checks.length; }), platforms: out, kind: kind, seconds: secs };
  }

  var api = { analyze: analyze, parseDuration: parseDuration, classify: classify, SPECS: SPECS, CHECKED: CHECKED };
  root.HTSpecs = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  // ---------------- page ----------------
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  var ICON = { fits: '✓', check: '!', over: '✕' };
  var HEAD = { fits: 'Fits', check: 'Check the app', over: 'Does not fit' };

  function init() {
    var box = document.getElementById('specs');
    if (!box) return;
    var dur = box.querySelector('#sp-dur'), w = box.querySelector('#sp-w'), h = box.querySelector('#sp-h'), out = box.querySelector('#sp-out'), preset = box.querySelector('#sp-preset');
    preset.addEventListener('change', function () {
      var v = preset.value.split('x'); if (v.length === 2) { w.value = v[0]; h.value = v[1]; }
    });
    box.querySelector('#sp-go').addEventListener('click', function () {
      var secs = parseDuration(dur.value), width = parseInt(w.value, 10), height = parseInt(h.value, 10);
      var haveSize = width > 0 && height > 0;
      if (!(secs > 0) && !haveSize) {
        out.hidden = false; out.innerHTML = '<div class="tool-err" role="alert">Enter your video length (for example 45 or 1:30), its width and height, or both.</div>'; return;
      }
      if (dur.value.trim() && isNaN(secs)) {
        out.hidden = false; out.innerHTML = '<div class="tool-err" role="alert">I could not read that length. Use seconds (45) or minutes and seconds (1:30).</div>'; return;
      }
      var r = analyze({ seconds: secs, width: width, height: height });
      out.hidden = false;
      out.innerHTML = '<div class="sp-grid">' + r.platforms.map(function (p) {
        return '<div class="sp-card ' + p.overall + '"><h3>' + esc(p.name) + '</h3><div class="sp-verdict">' + ICON[p.overall] + ' ' + HEAD[p.overall] + '</div>' +
          '<ul>' + p.checks.map(function (c) { return '<li class="' + c.status + '"><b>' + c.kind + ':</b> ' + esc(c.text) + '</li>'; }).join('') + '</ul>' +
          '<p class="muted small">' + esc(p.note) + ' Source: ' + esc(p.source) + '.</p></div>';
      }).join('') + '</div>' +
        '<p class="muted small">Specs last checked ' + CHECKED + '. Platforms change their limits, so for anything that matters, confirm in the app or on the platform\'s help page. Nothing you type leaves your browser.</p>';
    });
    box.querySelector('#sp-clear').addEventListener('click', function () { dur.value = ''; w.value = ''; h.value = ''; preset.value = ''; out.hidden = true; });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})(typeof window !== 'undefined' ? window : globalThis);

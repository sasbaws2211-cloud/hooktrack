/* HookTrack free hook scorer. Runs entirely in the visitor's browser, with no AI and nothing sent anywhere.

   It scores the first line of a short video against seven plain checks (length, a weak opener, a clear pattern, specifics,
   speaking to the viewer, an open question, simple words). The checks are common advice about what holds attention in the first
   seconds. They are a checklist, NOT a prediction: a high score does not mean a video will get views, and the page says so. */
(function (root) {
  'use strict';

  var WEAK_OPENER = /^(hey+|hi+|hello|yo|what'?s up|whats up|welcome( back)?|good (morning|evening|afternoon)|so,? (today|in this|i)|in this video|today (i|we|i'm|im)|um+|uh+|okay,? so|ok,? so|guys|everyone|let me tell you|i wanted to)\b/i;
  var NUMBER_WORDS = /\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|hundred|thousand|million)\b/i;
  var TIMEFRAME = /\b\d+\s*(seconds?|secs?|minutes?|mins?|hours?|days?|weeks?|months?|years?|x)\b/i;
  var PATTERNS = [
    ['question', function (t) { return /\?/.test(t) || /^(why|what|how|who|when|where|can|could|do|does|did|is|are|should|would)\b/i.test(t); }, 'asks a question'],
    ['number', function (t) { return /\d/.test(t) || NUMBER_WORDS.test(t); }, 'uses a number'],
    ['warning', function (t) { return /\b(stop|don'?t|never|avoid|mistakes?|wrong|quit|ruining|killing|worst|nobody tells)\b/i.test(t); }, 'warns or tells them to stop'],
    ['result', function (t) { return TIMEFRAME.test(t) || /\b(before and after|how i|i (lost|made|gained|went|got|built|learned|saved|grew)|from .+ to .+)\b/i.test(t); }, 'shows a result or a timeframe'],
    ['curiosity', function (t) { return /\b(secret|nobody|no one|truth|the reason|reason why|what happens|the one thing|hidden|surprising|you won'?t believe)\b/i.test(t); }, 'teases something hidden'],
    ['contrast', function (t) { return /\b(but|instead|vs\.?|versus|not .+ but|without)\b/i.test(t); }, 'sets up a contrast'],
    ['claim', function (t) { return /\b(best|only|every|always|easiest|fastest|simplest)\b/i.test(t); }, 'makes a bold claim']
  ];
  var IMPERATIVE = /^(stop|watch|try|don'?t|never|learn|save|do|make|use|avoid|start|quit|listen|look|wait|think|imagine|remember|forget|ask|tell|show|get|take|keep|build|try)\b/i;
  var OPEN_LOOP = /(\?|\.{3}|…|\b(secret|nobody|truth|why|how|what happens|here'?s|until|wait|the reason|the one)\b)/i;

  function words(t) {
    return String(t).trim().split(/\s+/).map(function (w) { return w.replace(/^[^\w']+|[^\w']+$/g, ''); }).filter(Boolean);
  }

  function score(raw) {
    var text = String(raw == null ? '' : raw).replace(/\s+/g, ' ').trim();
    if (!text) return null;
    var w = words(text), n = w.length;
    var checks = [];
    function add(key, label, max, pts, pass, good, fix) {
      checks.push({ key: key, label: label, max: max, points: pts, pass: pass, text: pass ? good : fix });
    }

    // 1. length: about 6 to 14 words fits the first 3 or 4 seconds spoken aloud
    var lp = n <= 3 ? 5 : n <= 5 ? 12 : n <= 14 ? 20 : n <= 20 ? 12 : 4;
    add('length', 'Length', 20, lp, lp === 20,
      n + ' words: short enough to say in about 3 seconds.',
      n <= 5 ? 'Only ' + n + ' word' + (n === 1 ? '' : 's') + '. Add the one detail that makes it specific.'
              : n + ' words is a lot for a first line. Aim for 6 to 14 so it can be said in about 3 seconds.');

    // 2. a weak opener wastes the most valuable second
    var weak = WEAK_OPENER.test(text);
    add('opener', 'Opening words', 15, weak ? 0 : 15, !weak, 'It starts with the point, not a greeting.',
      'It opens with a greeting or warm-up ("hey guys", "so today", "in this video"). Start with the point instead.');

    // 3. a recognisable pattern
    var hit = PATTERNS.filter(function (p) { return p[1](text); });
    var pp = hit.length >= 2 ? 25 : hit.length === 1 ? 18 : 0;
    add('pattern', 'Clear pattern', 25, pp, hit.length >= 1,
      'It ' + hit.map(function (p) { return p[2]; }).join(' and ') + '.',
      'No clear pattern. Hooks usually ask a question, use a number, warn, show a result, tease something hidden, or set up a contrast.');

    // 4. specifics
    var hasNum = /\d/.test(text) || NUMBER_WORDS.test(text) || TIMEFRAME.test(text);
    var named = w.slice(1).some(function (x) { return /^[A-Z][a-z]{2,}/.test(x); });
    var sp = hasNum ? 15 : named ? 10 : 0;
    add('specific', 'Specifics', 15, sp, sp >= 10,
      hasNum ? 'It has a number or timeframe, which makes it feel concrete.' : 'It names something specific.',
      'Nothing specific. A number, a timeframe or a named thing ("3 mistakes", "in 30 days", "Ozempic") is more believable than a general claim.');

    // 5. talks to the viewer
    var you = /\b(you|your|you'?re|you'?ll|you'?ve|yourself)\b/i.test(text) || IMPERATIVE.test(text);
    add('viewer', 'Talks to the viewer', 10, you ? 10 : 0, you, 'It speaks to the viewer directly.',
      'It does not speak to the viewer. Try "you" or "your", or start with a command ("Stop doing this").');

    // 6. something left open
    var loop = OPEN_LOOP.test(text);
    add('loop', 'Open question', 10, loop ? 10 : 0, loop, 'It leaves something unanswered, which gives a reason to keep watching.',
      'Nothing is left open. Raise a question the video then answers, so there is a reason to stay.');

    // 7. plain words
    var avg = w.reduce(function (s, x) { return s + x.length; }, 0) / Math.max(1, n);
    var long = w.some(function (x) { return x.length > 14; });
    var plain = avg <= 5.5 && !long;
    add('plain', 'Plain words', 5, plain ? 5 : 0, plain, 'Short, plain words that are easy to take in at speed.',
      'Long or complicated words slow a first line down. Swap them for shorter ones.');

    var total = checks.reduce(function (s, c) { return s + c.points; }, 0);
    var label = total >= 75 ? 'strong' : total >= 50 ? 'okay' : 'weak';
    return { text: text, words: n, score: total, label: label, checks: checks, patterns: hit.map(function (p) { return p[0]; }), tryThese: suggestions(checks, text) };
  }

  // Rewrite patterns for whatever is missing. They are templates with blanks: we do not pretend to understand the topic.
  function suggestions(checks, text) {
    var miss = {};
    checks.forEach(function (c) { if (!c.pass) miss[c.key] = true; });
    var out = [];
    if (miss.specific) out.push('Add a number: "3 mistakes that ___" or "How I ___ in 30 days".');
    if (miss.viewer) out.push('Speak to them: "If you ___, watch this." or "Stop ___ if you want ___."');
    if (miss.loop || miss.pattern) out.push('Raise a question the video answers: "Why does ___ never work?" or "What happens when you ___?"');
    if (miss.opener) out.push('Cut the greeting and say the point first. Your second sentence is usually your real hook.');
    if (miss.length) out.push('Trim it to one idea of 6 to 14 words. Move the detail to the next line of the video.');
    return out.slice(0, 3);
  }

  function rank(lines) {
    return lines.map(function (l) { return score(l); }).filter(Boolean).sort(function (a, b) { return b.score - a.score; });
  }

  var SAMPLE = [
    'Hey guys, welcome back to my channel, today I am going to talk about some tips',
    'Stop doing this at the gym if you want to build muscle',
    '3 mistakes ruining your sleep (and the one fix nobody tells you)',
    'My morning routine'
  ];

  var api = { score: score, rank: rank, words: words, SAMPLE: SAMPLE };
  root.HTHook = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  // ---------------- page ----------------
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  var LABEL = { strong: 'Strong', okay: 'Okay', weak: 'Needs work' };

  function card(r, i, many) {
    var checks = r.checks.map(function (c) {
      return '<li class="' + (c.pass ? 'hk-pass' : 'hk-fail') + '"><b>' + (c.pass ? '✓' : '✕') + ' ' + esc(c.label) + '</b> <span class="muted small">' + c.points + '/' + c.max + '</span><div>' + esc(c.text) + '</div></li>';
    }).join('');
    var tries = r.tryThese.length ? '<div class="hk-try"><b>Try this</b><ul>' + r.tryThese.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul></div>' : '';
    return '<div class="hk-card ' + r.label + '">' +
      '<div class="hk-top"><div class="hk-score" aria-label="Score ' + r.score + ' out of 100"><b>' + r.score + '</b><span>/100</span></div>' +
      '<div><div class="hk-verdict">' + LABEL[r.label] + (many && i === 0 ? ' · your strongest' : '') + '</div><div class="hk-text">“' + esc(r.text) + '”</div></div></div>' +
      '<ul class="hk-checks">' + checks + '</ul>' + tries + '</div>';
  }

  function init() {
    var box = document.getElementById('hooklab');
    if (!box) return;
    var ta = box.querySelector('#hk-text'), out = box.querySelector('#hk-out');
    function run() {
      var lines = ta.value.split(/\n+/).map(function (l) { return l.trim(); }).filter(Boolean).slice(0, 10);
      if (!lines.length) { out.hidden = false; out.innerHTML = '<div class="tool-err" role="alert">Type or paste a hook first. Use one line per hook to compare up to 10.</div>'; return; }
      var ranked = rank(lines), many = ranked.length > 1;
      out.hidden = false;
      out.innerHTML = (many ? '<p class="small muted">Ranked from highest to lowest, with the reason for every point.</p>' : '') +
        ranked.map(function (r, i) { return card(r, i, many); }).join('') +
        '<p class="muted small">This is a checklist of patterns that tend to hold attention, not a prediction. A high score does not mean a video will get views, and a low score does not mean it will fail. The only way to know is to post it and compare the result with your own normal.</p>' +
        '<div class="tool-actions">' + (window.HTCta ? HTCta.html('See which hooks actually worked for your accounts') : '<a class="btn primary" href="/#pricing">See which hooks actually worked</a>') + '</div>';
    }
    box.querySelector('#hk-go').addEventListener('click', run);
    box.querySelector('#hk-sample').addEventListener('click', function () { ta.value = SAMPLE.join('\n'); run(); });
    box.querySelector('#hk-clear').addEventListener('click', function () { ta.value = ''; out.hidden = true; });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})(typeof window !== 'undefined' ? window : globalThis);

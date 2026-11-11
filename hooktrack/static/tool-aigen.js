/* HookTrack free AI tools: script, bio, caption and content-idea generators.
   Each calls our own /api/free/* endpoints, which apply per-visitor limits. What the visitor types is sent to our AI provider to
   write the answer and is not stored; the page says so before the first use. */
(function (root) {
  'use strict';
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }

  function post(path, body) {
    var ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = ctl ? setTimeout(function () { ctl.abort(); }, 100000) : null;
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin', signal: ctl ? ctl.signal : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) {
            var m = d && d.detail;
            if (Array.isArray(m)) m = 'Please check what you typed and try again.';
            var e = new Error(m || 'Something went wrong. Please try again.'); e.status = r.status; throw e;
          }
          return d;
        });
      })
      .catch(function (e) { if (e && e.name === 'AbortError') throw new Error('That took too long. Please try again.'); throw e; })
      .then(function (d) { if (timer) clearTimeout(timer); return d; }, function (e) { if (timer) clearTimeout(timer); throw e; });
  }

  function copy(text, btn) {
    function done() { var o = btn.textContent; btn.textContent = 'Copied ✓'; setTimeout(function () { btn.textContent = o; }, 1400); }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { fallback(text); done(); });
    else { fallback(text); done(); }
  }
  function fallback(text) {
    var t = document.createElement('textarea'); t.value = text; t.style.position = 'fixed'; t.style.opacity = '0'; document.body.appendChild(t);
    t.select(); try { document.execCommand('copy'); } catch (e) { /* nothing more to try */ } document.body.removeChild(t);
  }

  var RENDER = {
    script: function (d) {
      var s = d.script, sc = (root.HTHook && HTHook.score(s.hook)) || null;
      var text = s.hook + '\n\n' + s.beats.map(function (b, i) { return (i + 1) + '. ' + b.say + (b.show ? ' [' + b.show + ']' : ''); }).join('\n') + (s.cta ? '\n\n' + s.cta : '') + (s.caption ? '\n\nCaption: ' + s.caption + ' ' + s.hashtags.join(' ') : '');
      return '<div class="ai-card"><div class="ai-label">Hook' + (sc ? ' <span class="pill" title="Scored by the free hook scorer">' + sc.score + '/100</span>' : '') + '</div><div class="ai-hook">' + esc(s.hook) + '</div>' +
        '<div class="ai-label">Script (about ' + s.seconds + ' seconds)</div><ol class="ai-beats">' + s.beats.map(function (b) {
          return '<li>' + esc(b.say) + (b.show ? '<div class="muted small">On screen: ' + esc(b.show) + '</div>' : '') + '</li>'; }).join('') + '</ol>' +
        (s.cta ? '<div class="ai-label">Call to action</div><div>' + esc(s.cta) + '</div>' : '') +
        (s.caption ? '<div class="ai-label">Caption</div><div>' + esc(s.caption) + ' <span class="muted">' + esc(s.hashtags.join(' ')) + '</span></div>' : '') +
        '<div class="tool-actions"><button type="button" class="btn sm" data-copy="' + esc(text) + '">Copy the script</button></div></div>';
    },
    bio: function (d) {
      return d.items.map(function (i) {
        return '<div class="ai-card"><div class="ai-hook">' + esc(i.text) + '</div><div class="row-between"><span class="muted small">' + i.chars + ' of ' + i.limit + ' characters</span>' +
          '<button type="button" class="btn sm" data-copy="' + esc(i.text) + '">Copy</button></div></div>';
      }).join('');
    },
    caption: function (d) {
      return d.items.map(function (i) {
        var full = i.text + (i.hashtags.length ? '\n\n' + i.hashtags.join(' ') : '');
        return '<div class="ai-card"><div class="ai-hook">' + esc(i.text) + '</div><div class="muted">' + esc(i.hashtags.join(' ')) + '</div>' +
          '<div class="tool-actions"><button type="button" class="btn sm" data-copy="' + esc(full) + '">Copy caption and hashtags</button></div></div>';
      }).join('');
    },
    ideas: function (d) {
      return d.items.map(function (i, n) {
        var sc = (root.HTHook && HTHook.score(i.title)) || null;
        return '<div class="ai-card"><div class="ai-hook">' + (n + 1) + '. ' + esc(i.title) + (sc ? ' <span class="pill" title="Scored by the free hook scorer">' + sc.score + '</span>' : '') + '</div>' +
          (i.format ? '<div class="small"><b>How to film it:</b> ' + esc(i.format) + '</div>' : '') + (i.why ? '<div class="muted small">' + esc(i.why) + '</div>' : '') +
          '<div class="tool-actions"><button type="button" class="btn sm" data-copy="' + esc(i.title) + '">Copy</button></div></div>';
      }).join('');
    }
  };

  var BODY = {
    script: function (f) { return { topic: f.topic.value, platform: f.platform.value, seconds: +f.seconds.value, tone: f.tone.value }; },
    bio: function (f) { return { platform: f.platform.value, about: f.about.value, audience: f.audience.value, tone: f.tone.value }; },
    caption: function (f) { return { platform: f.platform.value, about: f.about.value, tone: f.tone.value, cta: f.cta.value }; },
    ideas: function (f) { return { niche: f.niche.value, platform: f.platform.value, audience: f.audience.value }; }
  };
  var CHECK = {
    script: function (f) { return f.topic.value.trim().length >= 3 ? '' : 'Say what the video is about (a few words is enough).'; },
    bio: function (f) { return f.about.value.trim().length >= 3 ? '' : 'Say what your account is about (a few words is enough).'; },
    caption: function (f) { return f.about.value.trim().length >= 3 ? '' : 'Say what your video is about (a few words is enough).'; },
    ideas: function (f) { return f.niche.value.trim().length >= 2 ? '' : 'Enter your niche, for example "home workouts".'; }
  };

  function init() {
    var box = document.getElementById('aigen');
    if (!box) return;
    var kind = box.getAttribute('data-kind'), form = box.querySelector('form'), out = box.querySelector('#ai-out'), btn = box.querySelector('#ai-go'), left = box.querySelector('#ai-left');
    if (!RENDER[kind] || !form) return;
    fetch('/api/free/status', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (s) {
      if (!s.enabled) { btn.disabled = true; out.hidden = false; out.innerHTML = '<div class="tool-err">This tool is not available right now. Please check back soon.</div>'; }
      else if (left) left.textContent = s.remaining + ' free use' + (s.remaining === 1 ? '' : 's') + ' left today';
    }).catch(function () { /* the tool still works; the allowance just is not shown */ });

    function run(e) {
      if (e) e.preventDefault();
      var msg = CHECK[kind](form);
      out.hidden = false;
      if (msg) { out.innerHTML = '<div class="tool-err" role="alert">' + esc(msg) + '</div>'; return; }
      btn.disabled = true; var label = btn.textContent; btn.textContent = 'Writing…';
      out.innerHTML = '<div class="muted" role="status">Writing your answer. This can take up to a minute.</div>';
      post('/api/free/' + kind, BODY[kind](form)).then(function (d) {
        out.innerHTML = RENDER[kind](d) +
          '<p class="muted small">Written by AI from what you typed. Check it before you use it and edit it so it sounds like you. Nothing is stored.</p>' +
          '<div class="tool-actions"><button type="button" class="btn" id="ai-again">Write more options</button> ' +
          (root.HTCta ? HTCta.html('See which of your videos actually worked') : '') + '</div>';
        if (left && typeof d.remaining === 'number') left.textContent = d.remaining + ' free use' + (d.remaining === 1 ? '' : 's') + ' left today';
        var again = out.querySelector('#ai-again'); if (again) again.addEventListener('click', function () { run(); });
      }).catch(function (err) {
        out.innerHTML = '<div class="tool-err" role="alert">' + esc(err.message) + (err.status === 429 ? ' <a href="/app#/signup">Create a free account</a>' : '') + '</div>';
      }).then(function () { btn.disabled = false; btn.textContent = label; });
    }
    form.addEventListener('submit', run);
    box.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-copy]');
      if (b) copy(b.getAttribute('data-copy'), b);
    });
  }

  var api = { post: post, esc: esc, RENDER: RENDER, BODY: BODY, CHECK: CHECK };
  root.HTAi = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})(typeof window !== 'undefined' ? window : globalThis);

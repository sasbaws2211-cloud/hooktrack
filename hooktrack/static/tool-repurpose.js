/* HookTrack free repurposing checklist: before you post a TikTok, Reel or Short on another platform.
   Runs entirely in the visitor's browser. Nothing is uploaded or stored. */
(function (root) {
  'use strict';
  var NAMES = { tiktok: 'TikTok', reels: 'Instagram Reels', shorts: 'YouTube Shorts' };

  // critical = the things most likely to hurt a repost, shown first when missing
  var ITEMS = [
    { id: 'watermark', g: 'File and format', critical: true,
      t: function (f, t) { return 'The video is exported without the ' + f + ' watermark, from your editing app or original file, not a screen recording or a downloaded copy.'; },
      why: function (f, t) { return 'A visible ' + f + ' watermark tells viewers the video was made elsewhere, and can make it look recycled.'; } },
    { id: 'format', g: 'File and format', critical: true,
      t: function (f, t) { return 'It is vertical (9:16) and exported at a high resolution, so ' + t + ' does not crop or stretch it.'; },
      why: function () { return '1080 by 1920 is the usual size for vertical video.'; } },
    { id: 'length', g: 'File and format', critical: true,
      t: function (f, t) { return 'The length fits the current limit for short videos on ' + t + '. Check the help pages of ' + t + ', because limits change.'; },
      why: function () { return 'Platforms have raised and changed their maximum lengths over time.'; } },
    { id: 'edges', g: 'File and format', critical: false,
      t: function (f, t) { return 'Captions and key text sit away from the edges and the bottom, where the buttons and caption on ' + t + ' will cover them.'; },
      why: function () { return 'Each app puts its buttons and text in slightly different places.'; } },
    { id: 'playback', g: 'File and format', critical: false,
      t: function () { return 'You played the final file on a phone and checked for black bars, cut-off text and distorted sound.'; },
      why: function () { return 'It takes a minute and catches most export mistakes.'; } },

    { id: 'audio', g: 'Audio and rights', critical: true,
      t: function (f, t) { return 'The music or sound is cleared for ' + t + '. Sounds from the ' + f + ' library may not be licensed there, so use your own audio or one from the library on ' + t + '.'; },
      why: function () { return 'Licences are usually specific to the platform you picked the sound on.'; } },
    { id: 'captions', g: 'Audio and rights', critical: false,
      t: function () { return 'The video still makes sense with the sound off, and any captions are easy to read on a small screen.'; },
      why: function () { return 'Many people watch short videos without sound.'; } },

    { id: 'refs', g: 'Words and calls to action', critical: true,
      t: function (f, t) { return 'Nothing in the video says or shows ' + f + ' features, such as "duet this", "follow me on ' + f + '" or ' + f + ' stickers.'; },
      why: function (f, t) { return 'Viewers on ' + t + ' cannot act on a ' + f + ' prompt, and it signals a repost.'; } },
    { id: 'hook', g: 'Words and calls to action', critical: false,
      t: function (f) { return 'The first one or two seconds still make sense to someone who has never seen you on ' + f + '.'; },
      why: function () { return 'Your hook must work without any context from the original post.'; } },
    { id: 'cta', g: 'Words and calls to action', critical: false,
      t: function (f, t) { return 'The call to action fits ' + t + ': replace any ' + f + ' action with the one you want here, such as subscribe, follow or comment.'; },
      why: function () { return 'Pick one simple next step.'; } },
    { id: 'title', g: 'Words and calls to action', critical: false,
      t: function (f, t) { return 'You wrote a fresh title or caption for ' + t + ' that says what the video is about, instead of pasting the ' + f + ' caption.'; },
      why: function () { return 'Captions that lean on one platform\'s trends or tags rarely work on another.'; } },
    { id: 'tags', g: 'Words and calls to action', critical: false,
      t: function (f, t) { return 'You added a few relevant hashtags or keywords for ' + t + ', not a copy of the trending tags on ' + f + '.'; },
      why: function () { return 'Use words people on that platform actually search for.'; } },

    { id: 'native', g: 'Posting and tracking', critical: true,
      t: function (f, t) { return 'You are uploading the file directly to ' + t + ', not posting a link or a screen recording of the ' + f + ' video.'; },
      why: function () { return 'Native uploads play properly and are treated as normal posts.'; } },
    { id: 'cover', g: 'Posting and tracking', critical: false,
      t: function (f, t) { return 'You picked a strong cover frame or thumbnail if ' + t + ' lets you choose one.'; },
      why: function () { return 'It is the first thing people see on your profile.'; } },
    { id: 'time', g: 'Posting and tracking', critical: false,
      t: function (f, t) { return 'You will post at your own best time on ' + t + ', which may not be the time you posted on ' + f + '.'; },
      why: function () { return 'Each platform has its own audience and its own busy hours.'; } },
    { id: 'track', g: 'Posting and tracking', critical: false,
      t: function (f, t) { return 'You will track the ' + t + ' views on their own and judge them against the normal for your account on ' + t + ', not against your ' + f + ' numbers.'; },
      why: function () { return 'Views mean different things on different platforms and accounts.'; } }
  ];

  function build(from, to) {
    var f = NAMES[from] || NAMES.tiktok, t = NAMES[to] || NAMES.shorts;
    return ITEMS.map(function (it) { return { id: it.id, group: it.g, critical: it.critical, text: it.t(f, t), why: it.why(f, t) }; });
  }

  // checked: { id: true }
  function score(from, to, checked) {
    var items = build(from, to), done = 0, missingCritical = [], missing = [];
    items.forEach(function (it) {
      if (checked && checked[it.id]) done++;
      else { missing.push(it); if (it.critical) missingCritical.push(it); }
    });
    var pct = Math.round(100 * done / items.length), verdict;
    if (missingCritical.length) verdict = 'fix';
    else if (pct >= 80) verdict = 'ready';
    else verdict = 'almost';
    return { total: items.length, done: done, pct: pct, missing: missing, missingCritical: missingCritical, verdict: verdict };
  }

  var api = { build: build, score: score, NAMES: NAMES, ITEMS: ITEMS };
  root.HTRepurpose = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  // ---------------- page ----------------
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }

  function init() {
    var box = document.getElementById('repurpose');
    if (!box) return;
    var fromSel = box.querySelector('#rp-from'), toSel = box.querySelector('#rp-to'), list = box.querySelector('#rp-list'), res = box.querySelector('#rp-result');
    var checked = {};
    function fill(sel, value) { sel.innerHTML = Object.keys(NAMES).map(function (k) { return '<option value="' + k + '"' + (k === value ? ' selected' : '') + '>' + esc(NAMES[k]) + '</option>'; }).join(''); }
    fill(fromSel, box.getAttribute('data-from') || 'tiktok');
    fill(toSel, box.getAttribute('data-to') || 'shorts');

    function draw() {
      var from = fromSel.value, to = toSel.value;
      if (from === to) {
        list.innerHTML = ''; res.innerHTML = '<div class="tool-err" role="alert">Pick two different platforms: where the video is from, and where you are posting it.</div>'; return;
      }
      var items = build(from, to), group = '';
      list.innerHTML = items.map(function (it) {
        var head = it.group !== group ? (group = it.group, '<h3>' + esc(it.group) + '</h3>') : '';
        return head + '<label class="chk' + (it.critical ? ' crit' : '') + '"><input type="checkbox" data-id="' + it.id + '"' + (checked[it.id] ? ' checked' : '') + '>' +
          '<span><b>' + esc(it.text) + '</b>' + (it.critical ? ' <em class="tag">must do</em>' : '') + '<small>' + esc(it.why) + '</small></span></label>';
      }).join('');
      Array.prototype.forEach.call(list.querySelectorAll('input[type=checkbox]'), function (c) {
        c.addEventListener('change', function () { checked[c.getAttribute('data-id')] = c.checked; result(); });
      });
      result();
    }

    function result() {
      var from = fromSel.value, to = toSel.value, r = score(from, to, checked);
      var head = { fix: 'Fix these first', almost: 'Almost ready', ready: 'Ready to post' }[r.verdict];
      var msg = {
        fix: 'You have ' + r.missingCritical.length + ' must-do item' + (r.missingCritical.length === 1 ? '' : 's') + ' left. These are the ones most likely to hurt a repost.',
        almost: 'The must-do items are done. A few small ones are left, and they are quick.',
        ready: 'Everything that matters is done. Post it, then compare the results with your own normal on ' + NAMES[to] + '.'
      }[r.verdict];
      var fixList = r.missingCritical.length ? '<ul class="fixlist">' + r.missingCritical.map(function (it) { return '<li>' + esc(it.text) + '</li>'; }).join('') + '</ul>' : '';
      res.innerHTML = '<div class="rp-card ' + r.verdict + '"><div class="rp-top"><b>' + head + '</b><span>' + r.done + ' of ' + r.total + ' done</span></div>' +
        '<div class="rp-bar"><span style="width:' + r.pct + '%"></span></div><p>' + esc(msg) + '</p>' + fixList +
        '<div class="tool-actions"><button type="button" class="btn" id="rp-copy">Copy what is left</button> ' +
        (window.HTCta ? HTCta.html('Track every post across all your accounts') : '<a class="btn primary" href="/#pricing">Track every post across all your accounts</a>') + '</div></div>';
      var copy = res.querySelector('#rp-copy');
      if (copy) copy.addEventListener('click', function () {
        var txt = 'Repurposing ' + NAMES[from] + ' to ' + NAMES[to] + ': still to do\n' + (r.missing.length ? r.missing.map(function (it) { return '- ' + it.text; }).join('\n') : 'Nothing, all done.');
        if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () { copy.textContent = 'Copied'; });
      });
    }

    fromSel.addEventListener('change', draw); toSel.addEventListener('change', draw);
    box.querySelector('#rp-reset').addEventListener('click', function () { checked = {}; draw(); });
    draw();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})(typeof window !== 'undefined' ? window : globalThis);

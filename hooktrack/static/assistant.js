/* HookTrack Guide: a floating helper on every signed-in page.
   It is opened by people who have already looked around and are still stuck, so it carries along where they are,
   the pages they went through and what is on their screen, and the server answers that first. */
(function () {
  'use strict';

  var PAGE_NAMES = {
    dashboard: 'Dashboard', accounts: 'Accounts', views: 'Views', today: 'Today', hooks: 'Hooks', pipeline: 'Posts',
    calendar: 'Posts calendar', scheduled: 'Scheduled', clients: 'Clients', inspiration: 'Inspiration',
    settings: 'Settings', billing: 'Billing'
  };
  // starter questions a lay person is likely to have on each page: tapping one is easier than typing
  var STARTERS = {
    dashboard: ['What do Scale, Tweak and Stop mean?', 'Why do I see no decisions yet?', 'What should I do first?'],
    accounts: ['What does Sync do?', 'How far back should I sync?', 'Why are there no videos after syncing?'],
    views: ['How do I add views?', 'What does the ~ mean on a number?'],
    today: ['How do I add today’s views?'],
    hooks: ['What is the hook score?', 'Why is the score or decision empty?', 'How do I turn a hook into a new video?'],
    pipeline: ['How do I get a card scheduled?', 'What are the stages for?', 'Why can’t I publish this card?'],
    calendar: ['How do I move a post to another day?', 'What does the 📅 mean?'],
    scheduled: ['How do I change the time?', 'Why hasn’t it posted yet?'],
    clients: ['What is a client report?', 'How do I share a report?'],
    inspiration: ['What is Inspiration for?'],
    settings: ['How do I connect my TikTok?', 'What are the decision rules?', 'How do I invite a teammate?'],
    billing: ['What does my plan include?', 'What happens when my trial ends?']
  };

  var KEY_TRAIL = 'ht_guide_trail';
  var KEY_CHAT = 'ht_guide_chat';
  var open = false, busy = false, root, panel, list, input, sendBtn, chipsBox;
  var trail = [];                               // [{page, at}] oldest first
  var lastToast = { text: '', at: 0 };
  var messages = [];                            // [{role, content}]

  function $(sel, el) { return (el || document).querySelector(sel); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function routeKey() { return (location.hash.replace('#/', '') || 'dashboard').split('?')[0]; }
  function pageName(k) { return PAGE_NAMES[k] || (k ? k.charAt(0).toUpperCase() + k.slice(1) : 'Dashboard'); }
  function signedIn() { try { return typeof state !== 'undefined' && !!state.me && !document.body.classList.contains('anon'); } catch (e) { return false; } }
  function chatKey() { try { return KEY_CHAT + '_' + ((state.me && state.me.workspace && state.me.workspace.id) || 0); } catch (e) { return KEY_CHAT; } }

  function load() {
    try { trail = JSON.parse(sessionStorage.getItem(KEY_TRAIL) || '[]'); } catch (e) { trail = []; }
    try { messages = JSON.parse(sessionStorage.getItem(chatKey()) || '[]'); } catch (e) { messages = []; }
  }
  function saveTrail() { try { sessionStorage.setItem(KEY_TRAIL, JSON.stringify(trail.slice(-20))); } catch (e) { /* ignored */ } }
  function saveChat() { try { sessionStorage.setItem(chatKey(), JSON.stringify(messages.slice(-30))); } catch (e) { /* ignored */ } }

  // remember each page the person moves through, and how long they stayed
  function noteVisit() {
    if (!signedIn()) return;
    var k = routeKey();
    var last = trail[trail.length - 1];
    if (last && last.page === k) return;
    trail.push({ page: k, at: Date.now() });
    saveTrail();
  }
  function trailForServer() {
    var out = [];
    for (var i = 0; i < trail.length; i++) {
      var end = i + 1 < trail.length ? trail[i + 1].at : Date.now();
      var secs = Math.round((end - trail[i].at) / 1000);
      var prev = out[out.length - 1];
      if (prev && prev.key === trail[i].page) { prev.seconds += secs; continue; }
      out.push({ key: trail[i].page, page: pageName(trail[i].page), seconds: secs });
    }
    return out.slice(-8).map(function (t) { return { page: t.page, seconds: t.seconds }; });
  }

  // what the person can see right now: an open window first (that is what they are looking at), then the page
  function screenText() {
    var parts = [];
    var modal = $('#modal-root');
    if (modal && modal.innerText && modal.innerText.trim()) parts.push('OPEN WINDOW: ' + modal.innerText);
    var app = $('#app');
    if (app) parts.push(app.innerText || '');
    return parts.join('\n\n').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').slice(0, 2500);
  }
  function watchToasts() {
    var t = $('#toast');
    if (!t || !window.MutationObserver) return;
    new MutationObserver(function () {
      var txt = (t.innerText || '').trim();
      if (txt) lastToast = { text: txt, at: Date.now(), err: t.classList.contains('err') };
    }).observe(t, { childList: true, characterData: true, subtree: true });
  }
  function recentProblem() {
    var p = $('#app .empty h2');
    if (p && /wrong|error|couldn/i.test(p.innerText)) return p.innerText + ' ' + (($('#app .empty p') || {}).innerText || '');
    return (lastToast.text && Date.now() - lastToast.at < 120000 && lastToast.err) ? lastToast.text : '';
  }

  // ---- rendering ----
  function inline(s) {
    s = esc(s);
    s = s.replace(/\[([^\]]{1,60})\]\((#\/[a-z]{1,20})\)/g, '<a href="$2" data-guide-link>$1</a>');
    s = s.replace(/\*\*([^*]{1,80})\*\*/g, '<b>$1</b>');
    return s;
  }
  function format(text) {
    var lines = String(text).split('\n'), html = '', mode = '';
    function close() { if (mode) { html += '</' + mode + '>'; mode = ''; } }
    lines.forEach(function (ln) {
      var ol = /^\s*\d+[.)]\s+(.*)$/.exec(ln), ul = /^\s*[-•*]\s+(.*)$/.exec(ln);
      if (ol) { if (mode !== 'ol') { close(); html += '<ol>'; mode = 'ol'; } html += '<li>' + inline(ol[1]) + '</li>'; }
      else if (ul) { if (mode !== 'ul') { close(); html += '<ul>'; mode = 'ul'; } html += '<li>' + inline(ul[1]) + '</li>'; }
      else if (!ln.trim()) { close(); }
      else { close(); html += '<p>' + inline(ln) + '</p>'; }
    });
    close();
    return html;
  }
  function opener() {
    var names = trailForServer().map(function (t) { return t.page; });
    var uniq = names.filter(function (n, i) { return names.lastIndexOf(n) === i; });
    var seen = uniq.length > 1 ? 'I can see you’ve been looking around <b>' + esc(uniq.slice(-3).join(' → ')) + '</b>.' : 'I can see you’re on <b>' + esc(pageName(routeKey())) + '</b>.';
    return '<p>' + seen + ' Something not making sense? Tell me in your own words, or tap a question below.</p>';
  }
  function draw() {
    if (!list) return;
    var html = '<div class="gd-msg gd-bot">' + opener() + '</div>';
    messages.forEach(function (m) {
      html += '<div class="gd-msg ' + (m.role === 'user' ? 'gd-me' : 'gd-bot') + '">' + (m.role === 'user' ? '<p>' + esc(m.content) + '</p>' : format(m.content)) + '</div>';
    });
    if (busy) html += '<div class="gd-msg gd-bot gd-typing" aria-label="Thinking"><i></i><i></i><i></i></div>';
    list.innerHTML = html;
    list.scrollTop = list.scrollHeight;
    drawChips();
  }
  function drawChips() {
    if (!chipsBox) return;
    var s = (STARTERS[routeKey()] || STARTERS.dashboard);
    chipsBox.innerHTML = messages.length ? '' : s.map(function (q) { return '<button type="button" class="gd-chip">' + esc(q) + '</button>'; }).join('');
  }

  // ---- talking to the server ----
  function send(text) {
    text = (text || '').trim();
    if (!text || busy) return;
    messages.push({ role: 'user', content: text.slice(0, 1500) });
    saveChat(); busy = true; draw();
    var body = {
      messages: messages.slice(-12),
      page: { route: '#/' + routeKey(), title: pageName(routeKey()), trail: trailForServer(), screen: screenText(), problem: recentProblem() }
    };
    api('POST', '/api/assistant/chat', body).then(function (r) {
      messages.push({ role: 'assistant', content: r.answer });
    }).catch(function (e) {
      var msg = (e && e.message) || '';
      if (/many questions/i.test(msg)) {
        /* keep the server's own wording */
      } else if (/isn.t set up|not set up/i.test(msg)) {
        msg = 'The guide isn’t switched on yet. In the meantime, the **?** button on the [Dashboard](#/dashboard) explains the main words. Please let your workspace admin know.';
      } else {
        /* never show a provider's raw error to someone who just wanted help */
        msg = 'Sorry, I can’t answer right now. It’s a problem on our side, not something you did. Please try again in a few minutes, or contact support if it keeps happening.';
      }
      messages.push({ role: 'assistant', content: msg });
    }).then(function () { busy = false; saveChat(); draw(); if (input) input.focus(); });
  }

  // ---- the floating button and panel ----
  function build() {
    if (root) return;
    root = document.createElement('div');
    root.id = 'guide-root';
    root.innerHTML =
      '<button type="button" id="guide-fab" aria-label="Open the help guide" aria-expanded="false"><span class="gd-ico" aria-hidden="true">?</span><span class="gd-lbl">Need help?</span></button>' +
      '<section id="guide-panel" role="dialog" aria-label="HookTrack Guide" hidden>' +
      '<header><div><b>HookTrack Guide</b><span>Ask anything about the app, in plain words</span></div>' +
      '<div class="gd-hbtns"><button type="button" id="guide-new" title="Start over">New chat</button><button type="button" id="guide-x" aria-label="Close">×</button></div></header>' +
      '<div id="guide-list" aria-live="polite"></div><div id="guide-chips"></div>' +
      '<form id="guide-form"><textarea id="guide-in" rows="1" maxlength="1500" placeholder="Type what’s confusing…" aria-label="Your question"></textarea>' +
      '<button type="submit" id="guide-send" aria-label="Send">Send</button></form>' +
      '<p class="gd-note">The guide explains and points the way. It can’t click or change anything for you.</p></section>';
    document.body.appendChild(root);
    panel = $('#guide-panel', root); list = $('#guide-list', root); input = $('#guide-in', root); sendBtn = $('#guide-send', root); chipsBox = $('#guide-chips', root);
    $('#guide-fab', root).onclick = toggle;
    $('#guide-x', root).onclick = function () { setOpen(false); };
    $('#guide-new', root).onclick = function () { messages = []; saveChat(); draw(); input.focus(); };
    $('#guide-form', root).onsubmit = function (e) { e.preventDefault(); var v = input.value; input.value = ''; autosize(); send(v); };
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#guide-form', root).requestSubmit(); } });
    input.addEventListener('input', autosize);
    chipsBox.onclick = function (e) { var b = e.target.closest('.gd-chip'); if (b) send(b.textContent); };
    list.onclick = function (e) {
      var a = e.target.closest('a[data-guide-link]');
      if (a && window.innerWidth < 700) setOpen(false);          // on a phone the panel covers the page being pointed to
    };
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && open) setOpen(false); });
  }
  function autosize() { if (!input) return; input.style.height = 'auto'; input.style.height = Math.min(110, input.scrollHeight) + 'px'; }
  function setOpen(v) {
    open = v; if (!root) return;
    panel.hidden = !v; $('#guide-fab', root).setAttribute('aria-expanded', v ? 'true' : 'false');
    root.classList.toggle('open', v);
    if (v) { draw(); setTimeout(function () { input && input.focus(); }, 50); }
  }
  function toggle() { setOpen(!open); }

  function sync() {
    var on = signedIn();
    if (on && !root) { load(); build(); watchToasts(); }
    if (root) root.hidden = !on;
    if (on) { noteVisit(); if (open) drawChips(); }
  }

  window.addEventListener('hashchange', function () { setTimeout(sync, 0); });
  document.addEventListener('DOMContentLoaded', function () { sync(); setInterval(sync, 2000); });
  if (document.readyState !== 'loading') { sync(); setInterval(sync, 2000); }
})();

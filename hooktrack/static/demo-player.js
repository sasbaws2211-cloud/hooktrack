/* HookTrack demo player: the demo video plays in a window on the page instead of sending the visitor to YouTube.

   The video is YouTube's privacy-enhanced embed (youtube-nocookie.com). It is requested only AFTER the visitor clicks.
   Our own controls drive it through YouTube's postMessage interface, so no extra script is loaded from YouTube.

   Google's embedded-player rules (Required Minimum Functionality) forbid putting overlays or other visual elements IN FRONT of
   any part of the player, and forbid hiding or blocking its branding and links. So nothing here ever covers the video: the
   close button sits in a header above it, the controls in a bar below it, and the call to action in a strip below that.
   The frame stays clickable and YouTube's own logo and title stay visible.

   Without JavaScript, or if the video cannot load, every "Watch the demo" link still goes to YouTube. */
(function (root) {
  'use strict';
  var VIDEO_ID = 'SAprelI0bJ8';
  var WATCH_URL = 'https://youtu.be/' + VIDEO_ID;
  var cfg = { embedBase: 'https://www.youtube-nocookie.com/embed/' };
  var SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
  var READY_TIMEOUT_MS = 9000;

  function fmt(t) {
    t = Math.max(0, Math.floor(t || 0));
    var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return (h ? h + ':' + (m < 10 ? '0' : '') : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }
  function pageRef() {
    var slug = ((root.location && root.location.pathname) || '').replace(/^\/+|\/+$/g, '').replace(/\//g, '-').toLowerCase().replace(/[^a-z0-9-]/g, '');
    return 'demo-' + (slug || 'home');
  }
  function inApp() { return /^\/app(\/|$)/.test((root.location && root.location.pathname) || ''); }

  var api = { fmt: fmt, pageRef: pageRef, VIDEO_ID: VIDEO_ID, WATCH_URL: WATCH_URL, SPEEDS: SPEEDS,
    configure: function (o) { for (var k in o) cfg[k] = o[k]; } };
  root.HTDemo = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  var ICON = {
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>',
    pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4.2" height="14" rx="1.2"/><rect x="13.8" y="5" width="4.2" height="14" rx="1.2"/></svg>',
    vol: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5H4z"/><path class="ln" d="M15.5 8.5a5 5 0 0 1 0 7M18 6a8.5 8.5 0 0 1 0 12"/></svg>',
    mute: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5H4z"/><path class="ln" d="M16 9.5l5 5M21 9.5l-5 5"/></svg>',
    full: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="ln" d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
    exit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="ln" d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="ln" d="M6 6l12 12M18 6L6 18"/></svg>',
    replay: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="ln" d="M4 12a8 8 0 1 0 2.6-5.9M4 4v5h5"/></svg>'
  };

  var CSS = '' +
    '.htd-overlay{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(7,9,20,.84);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);opacity:0;transition:opacity .18s ease}' +
    '.htd-overlay.htd-in{opacity:1}.htd-overlay[hidden]{display:none}' +
    '.htd-box{position:relative;display:flex;flex-direction:column;width:min(1100px,100%,calc((100vh - 170px) * 16 / 9));max-height:100%;border-radius:18px;overflow:hidden;background:#0b0f19;box-shadow:0 30px 80px -20px rgba(0,0,0,.8),0 0 0 1px rgba(255,255,255,.08);transform:translateY(10px) scale(.985);transition:transform .22s cubic-bezier(.2,.8,.2,1);outline:none;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#fff}' +
    '.htd-in .htd-box{transform:none}' +
    '.htd-box:fullscreen{width:100vw;height:100vh;max-height:none;border-radius:0}.htd-box:-webkit-full-screen{width:100vw;height:100vh;max-height:none;border-radius:0}' +
    '.htd-box:fullscreen .htd-stage{flex:1;aspect-ratio:auto}.htd-box:-webkit-full-screen .htd-stage{flex:1;aspect-ratio:auto}' +
    '.htd-head{display:flex;align-items:center;gap:10px;padding:9px 12px 9px 16px;background:#0f1424;border-bottom:1px solid rgba(255,255,255,.07)}' +
    '.htd-head img{width:26px;height:26px;border-radius:7px}.htd-title{font-weight:750;font-size:15px;letter-spacing:-.01em}.htd-hsp{flex:1}' +
    '.htd-close{display:grid;place-items:center;width:38px;height:38px;border:0;border-radius:10px;background:transparent;color:#fff;cursor:pointer}' +
    '.htd-close:hover,.htd-close:focus-visible{background:rgba(255,255,255,.14)}.htd-close svg{width:20px;height:20px;stroke:#fff;fill:none;stroke-width:2.4;stroke-linecap:round}' +
    '.htd-stage{position:relative;flex:none;aspect-ratio:16/9;background:#05070f}' +
    '.htd-frame,.htd-frame iframe{position:absolute;inset:0;width:100%;height:100%;border:0}.htd-frame{z-index:2}' +
    '.htd-spin{position:absolute;left:50%;top:50%;z-index:1;width:42px;height:42px;margin:-21px 0 0 -21px;border-radius:50%;border:4px solid rgba(255,255,255,.2);border-top-color:#fff;animation:htd-rot .8s linear infinite}' +
    '.htd-spin[hidden]{display:none}@keyframes htd-rot{to{transform:rotate(360deg)}}' +
    '.htd-err{position:absolute;inset:0;z-index:3;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;text-align:center;padding:24px}' +
    '.htd-err[hidden]{display:none}.htd-err h3{margin:0;font-size:clamp(18px,2.6vw,26px);font-weight:800}.htd-err p{margin:0;color:#aab4cf;max-width:460px;line-height:1.5}' +
    '.htd-ctrl{flex:none;padding:8px 14px 10px;background:#0f1424}' +
    '.htd-prog{position:relative;height:20px;display:flex;align-items:center;cursor:pointer;touch-action:none;outline:none}' +
    '.htd-track{position:relative;width:100%;height:5px;border-radius:99px;background:rgba(255,255,255,.2);transition:height .12s}.htd-prog:hover .htd-track,.htd-prog:focus-visible .htd-track{height:8px}' +
    '.htd-buf,.htd-fill{position:absolute;left:0;top:0;bottom:0;border-radius:99px}.htd-buf{background:rgba(255,255,255,.26)}.htd-fill{background:linear-gradient(90deg,#818cf8,#6366f1)}' +
    '.htd-thumb{position:absolute;top:50%;width:15px;height:15px;margin:-7.5px 0 0 -7.5px;border-radius:50%;background:#fff;box-shadow:0 2px 6px rgba(0,0,0,.5);transform:scale(0);transition:transform .12s}' +
    '.htd-prog:hover .htd-thumb,.htd-prog:focus-visible .htd-thumb,.htd-drag .htd-thumb{transform:scale(1)}' +
    '.htd-bar{display:flex;align-items:center;gap:6px;margin-top:2px}.htd-sp{flex:1}' +
    '.htd-ic{display:grid;place-items:center;width:38px;height:38px;border:0;border-radius:10px;background:transparent;color:#fff;cursor:pointer}' +
    '.htd-ic:hover,.htd-ic:focus-visible{background:rgba(255,255,255,.14)}.htd-ic svg{width:22px;height:22px;fill:#fff;stroke:none}.htd-ic svg .ln{fill:none;stroke:#fff;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}' +
    '.htd-time{font-size:13.5px;font-weight:600;font-variant-numeric:tabular-nums;color:#e5e9f5;margin:0 6px;white-space:nowrap}' +
    '.htd-vol{display:flex;align-items:center}.htd-vol input{width:0;opacity:0;transition:width .18s,opacity .18s;accent-color:#818cf8;cursor:pointer;height:4px}' +
    '.htd-vol:hover input,.htd-vol:focus-within input{width:78px;opacity:1;margin-right:6px}' +
    '.htd-spd{position:relative}.htd-spdbtn{width:auto;padding:0 10px;font-weight:800;font-size:14px}' +
    '.htd-menu{position:absolute;right:0;bottom:44px;z-index:5;min-width:92px;padding:6px;border-radius:12px;background:#171c30;border:1px solid rgba(255,255,255,.12);box-shadow:0 14px 34px -10px rgba(0,0,0,.7)}' +
    '.htd-menu[hidden]{display:none}.htd-menu button{display:block;width:100%;text-align:left;padding:8px 12px;border:0;border-radius:8px;background:transparent;color:#fff;font:inherit;font-size:14px;font-weight:600;cursor:pointer}' +
    '.htd-menu button:hover{background:rgba(255,255,255,.12)}.htd-menu button[aria-checked=true]{color:#a5b4fc}.htd-menu button[aria-checked=true]:before{content:"\\2713  "}' +
    '.htd-end{flex:none;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px 18px;padding:14px 16px;border-top:1px solid rgba(255,255,255,.08);background:linear-gradient(90deg,rgba(79,70,229,.3),rgba(20,184,166,.14))}' +
    '.htd-end[hidden]{display:none}.htd-end b{display:block;font-size:16px;letter-spacing:-.01em}.htd-end span{display:block;color:#b9c2db;font-size:14px;margin-top:2px}' +
    '.htd-row{display:flex;flex-wrap:wrap;gap:10px}' +
    '.htd-btn{display:inline-flex;align-items:center;gap:8px;padding:10px 18px;border-radius:12px;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.08);color:#fff;font:inherit;font-weight:700;font-size:15px;text-decoration:none;cursor:pointer}' +
    '.htd-btn:hover{background:rgba(255,255,255,.16)}.htd-btn.htd-pri{background:#4f46e5;border-color:#4f46e5}.htd-btn.htd-pri:hover{background:#6366f1}' +
    '.htd-btn svg{width:18px;height:18px;stroke:#fff;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}' +
    '.htd-lock{overflow:hidden!important}' +
    '@media (max-width:640px){.htd-overlay{padding:0;align-items:flex-start}.htd-box{width:100%;border-radius:0;max-height:none}.htd-vol input{display:none}.htd-time{margin:0 2px}.htd-ic{width:42px;height:42px}}' +
    '@media (prefers-reduced-motion:reduce){.htd-overlay,.htd-box{transition:none}.htd-spin{animation:none}}';

  var overlay, box, stage, frameWrap, frame, spin, endEl, errEl, playBtn, timeEl, prog, fillEl, bufEl, thumb, muteBtn, volInput, fullBtn, spdBtn, menu, closeBtn;
  var S, readyTimer, listenTimer, opener, built = false, dragging = false;

  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

  function build() {
    if (built) return;
    built = true;
    var st = document.createElement('style'); st.id = 'ht-demo-css'; st.textContent = CSS; document.head.appendChild(st);
    overlay = el('div', 'htd-overlay'); overlay.hidden = true; overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true'); overlay.setAttribute('aria-label', 'HookTrack demo video');
    box = el('div', 'htd-box'); box.tabIndex = -1;

    var head = el('div', 'htd-head', '<img src="/logo-mark.svg" alt="" width="26" height="26"><span class="htd-title">HookTrack demo</span><span class="htd-hsp"></span>');
    closeBtn = el('button', 'htd-close', ICON.close); closeBtn.type = 'button'; closeBtn.setAttribute('aria-label', 'Close video');
    head.appendChild(closeBtn);

    stage = el('div', 'htd-stage');
    spin = el('div', 'htd-spin'); spin.setAttribute('aria-hidden', 'true');       // sits BEHIND the video frame, never in front of it
    frameWrap = el('div', 'htd-frame');
    errEl = el('div', 'htd-err'); errEl.hidden = true;
    errEl.innerHTML = '<h3>The video could not load here</h3><p>Your browser or network blocked it. You can watch it on YouTube instead.</p><div class="htd-row"><a class="htd-btn htd-pri" target="_blank" rel="noopener" href="' + WATCH_URL + '">Watch on YouTube</a></div>';
    stage.appendChild(spin); stage.appendChild(frameWrap); stage.appendChild(errEl);

    var ctrl = el('div', 'htd-ctrl');
    prog = el('div', 'htd-prog'); prog.setAttribute('role', 'slider'); prog.setAttribute('aria-label', 'Seek'); prog.setAttribute('aria-valuemin', '0'); prog.tabIndex = 0;
    var track = el('div', 'htd-track'); bufEl = el('div', 'htd-buf'); fillEl = el('div', 'htd-fill'); thumb = el('div', 'htd-thumb');
    track.appendChild(bufEl); track.appendChild(fillEl); track.appendChild(thumb); prog.appendChild(track);
    var bar = el('div', 'htd-bar');
    playBtn = el('button', 'htd-ic', ICON.play); playBtn.type = 'button'; playBtn.setAttribute('aria-label', 'Play');
    timeEl = el('span', 'htd-time', '0:00 / 0:00');
    var vol = el('div', 'htd-vol');
    muteBtn = el('button', 'htd-ic', ICON.vol); muteBtn.type = 'button'; muteBtn.setAttribute('aria-label', 'Mute');
    volInput = el('input'); volInput.type = 'range'; volInput.min = '0'; volInput.max = '100'; volInput.value = '100'; volInput.setAttribute('aria-label', 'Volume');
    vol.appendChild(muteBtn); vol.appendChild(volInput);
    var sp = el('span', 'htd-sp');
    var spd = el('div', 'htd-spd');
    spdBtn = el('button', 'htd-ic htd-spdbtn', '1×'); spdBtn.type = 'button'; spdBtn.setAttribute('aria-label', 'Playback speed'); spdBtn.setAttribute('aria-haspopup', 'true'); spdBtn.setAttribute('aria-expanded', 'false');
    menu = el('div', 'htd-menu'); menu.hidden = true; menu.setAttribute('role', 'menu');
    SPEEDS.forEach(function (r) { var b = el('button', '', r + '×'); b.type = 'button'; b.setAttribute('role', 'menuitemradio'); b.setAttribute('aria-checked', r === 1 ? 'true' : 'false'); b.dataset.rate = r; menu.appendChild(b); });
    spd.appendChild(spdBtn); spd.appendChild(menu);
    fullBtn = el('button', 'htd-ic', ICON.full); fullBtn.type = 'button'; fullBtn.setAttribute('aria-label', 'Fullscreen');
    if (!box.requestFullscreen && !box.webkitRequestFullscreen) fullBtn.hidden = true;
    bar.appendChild(playBtn); bar.appendChild(vol); bar.appendChild(timeEl); bar.appendChild(sp); bar.appendChild(spd); bar.appendChild(fullBtn);
    ctrl.appendChild(prog); ctrl.appendChild(bar);

    endEl = el('div', 'htd-end'); endEl.hidden = true;

    box.appendChild(head); box.appendChild(stage); box.appendChild(ctrl); box.appendChild(endEl);
    overlay.appendChild(box); document.body.appendChild(overlay);

    playBtn.addEventListener('click', toggle);
    closeBtn.addEventListener('click', close);
    overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
    muteBtn.addEventListener('click', function () { send(S.muted ? 'unMute' : 'mute'); S.muted = !S.muted; paint(); });
    volInput.addEventListener('input', function () { var v = +volInput.value; send('setVolume', [v]); if (v > 0 && S.muted) { send('unMute'); S.muted = false; } S.volume = v; paint(); });
    fullBtn.addEventListener('click', toggleFull);
    spdBtn.addEventListener('click', function () { var open = menu.hidden; menu.hidden = !open; spdBtn.setAttribute('aria-expanded', String(open)); });
    menu.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      var r = +b.dataset.rate; send('setPlaybackRate', [r]); S.rate = r; menu.hidden = true; spdBtn.setAttribute('aria-expanded', 'false'); paint();
    });
    function endDrag() { dragging = false; box.classList.remove('htd-drag'); }
    prog.addEventListener('pointerdown', function (e) { dragging = true; box.classList.add('htd-drag'); try { prog.setPointerCapture(e.pointerId); } catch (x) {} seekFromEvent(e); });
    prog.addEventListener('pointermove', function (e) { if (dragging) seekFromEvent(e); });
    prog.addEventListener('pointerup', endDrag); prog.addEventListener('pointercancel', endDrag);
    document.addEventListener('pointerup', endDrag);
    prog.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); e.stopPropagation(); seekBy(e.key === 'ArrowLeft' ? -5 : 5); }
      if (e.key === 'Home') { e.preventDefault(); seekTo(0); }
      if (e.key === 'End') { e.preventDefault(); seekTo(S.duration); }
    });
    var fsChange = function () { var f = document.fullscreenElement === box || document.webkitFullscreenElement === box; fullBtn.innerHTML = f ? ICON.exit : ICON.full; fullBtn.setAttribute('aria-label', f ? 'Exit fullscreen' : 'Fullscreen'); };
    document.addEventListener('fullscreenchange', fsChange); document.addEventListener('webkitfullscreenchange', fsChange);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('message', onMessage);
    // a click on the video moves focus into YouTube's frame, where our keys cannot be heard: take it back
    window.addEventListener('blur', function () { setTimeout(function () { if (!overlay.hidden && frame && document.activeElement === frame) { try { box.focus({ preventScroll: true }); } catch (e) {} } }, 0); });
  }

  function resetState() {
    S = { ready: false, state: -1, time: 0, duration: 0, loaded: 0, muted: false, volume: 100, rate: 1, got: false, started: false, tries: 0 };
  }

  function target() { return new URL(cfg.embedBase, location.href).origin; }
  function send(func, args) {
    if (!frame || !frame.contentWindow) return;
    try { frame.contentWindow.postMessage(JSON.stringify({ event: 'command', func: func, args: args || [] }), target()); } catch (e) {}
  }
  function listen() { try { frame.contentWindow.postMessage(JSON.stringify({ event: 'listening', id: 1, channel: 'widget' }), target()); } catch (e) {} }

  function onMessage(e) {
    if (!frame || e.source !== frame.contentWindow) return;
    var d = e.data;
    if (typeof d === 'string') { try { d = JSON.parse(d); } catch (x) { return; } }
    if (!d || typeof d !== 'object') return;
    S.got = true;
    clearTimeout(readyTimer); clearInterval(listenTimer);
    var i = d.info;
    if (d.event === 'onStateChange' && typeof i === 'number') S.state = i;
    if (i && typeof i === 'object') {
      if (typeof i.currentTime === 'number' && !dragging) S.time = i.currentTime;
      if (typeof i.duration === 'number' && i.duration > 0) S.duration = i.duration;
      if (typeof i.playerState === 'number') S.state = i.playerState;
      if (typeof i.muted === 'boolean') S.muted = i.muted;
      if (typeof i.volume === 'number') S.volume = i.volume;
      if (typeof i.playbackRate === 'number') S.rate = i.playbackRate;
      if (typeof i.videoLoadedFraction === 'number') S.loaded = i.videoLoadedFraction;
    }
    if (!S.ready) { S.ready = true; send('setVolume', [S.volume || 100]); send('playVideo'); }
    paint();
  }

  function paint() {
    var st = S.state, playing = st === 1 || st === 3;
    if (st === 1) { S.started = true; endEl.hidden = true; }
    if (st === 0 && S.started) showEnd();
    spin.hidden = S.started || !errEl.hidden;
    playBtn.innerHTML = playing ? ICON.pause : ICON.play; playBtn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    var d = S.duration || 0, t = Math.min(S.time || 0, d || S.time || 0), f = d ? t / d : 0;
    fillEl.style.width = (f * 100) + '%'; thumb.style.left = (f * 100) + '%'; bufEl.style.width = (Math.max(S.loaded || 0, f) * 100) + '%';
    timeEl.textContent = fmt(t) + ' / ' + fmt(d);
    prog.setAttribute('aria-valuemax', String(Math.round(d))); prog.setAttribute('aria-valuenow', String(Math.round(t))); prog.setAttribute('aria-valuetext', fmt(t) + ' of ' + fmt(d));
    muteBtn.innerHTML = (S.muted || S.volume === 0) ? ICON.mute : ICON.vol; muteBtn.setAttribute('aria-label', S.muted ? 'Unmute' : 'Mute');
    volInput.value = S.muted ? 0 : S.volume;
    spdBtn.textContent = S.rate + '×';
    Array.prototype.forEach.call(menu.children, function (b) { b.setAttribute('aria-checked', String(+b.dataset.rate === S.rate)); });
  }

  // below the controls, never over the video
  function showEnd() {
    var app = inApp();
    var href = app ? '/app#/billing' : '/?ref=' + pageRef() + '#pricing';
    endEl.innerHTML = '<div><b>Ready to try it on your own videos?</b><span>Paste your numbers, or connect your accounts, and see which hooks to scale.</span></div>' +
      '<div class="htd-row"><a class="htd-btn htd-pri" href="' + href + '">' + (app ? 'See plans' : 'See pricing') + '</a>' +
      '<button type="button" class="htd-btn" data-act="replay">' + ICON.replay + 'Watch again</button></div>';
    endEl.hidden = false;
    endEl.querySelector('[data-act=replay]').addEventListener('click', function () { endEl.hidden = true; seekTo(0); play(); });
    endEl.querySelector('a').addEventListener('click', function () { if (app) close(); });
  }

  // the controls answer at once; YouTube's own report follows within a quarter of a second and corrects them if needed
  function play() { send('playVideo'); if (S.ready) { S.state = 1; paint(); } }
  function pause() { send('pauseVideo'); if (S.ready) { S.state = 2; paint(); } }
  function toggle() { if (!S.ready) return; (S.state === 1 || S.state === 3) ? pause() : play(); }
  function seekTo(t) { var d = S.duration || 0; t = Math.max(0, d ? Math.min(t, d - 0.1) : t); S.time = t; endEl.hidden = true; send('seekTo', [t, true]); paint(); }
  function seekBy(dt) { seekTo((S.time || 0) + dt); }
  function seekFromEvent(e) { var r = prog.getBoundingClientRect(); var f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)); if (S.duration) seekTo(f * S.duration); }
  function toggleFull() {
    if (document.fullscreenElement || document.webkitFullscreenElement) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); }
    else if (box.requestFullscreen) box.requestFullscreen().catch(function () {});
    else if (box.webkitRequestFullscreen) box.webkitRequestFullscreen();
  }

  function onKey(e) {
    if (!overlay || overlay.hidden) return;
    var k = e.key;
    if (k === 'Escape') { if (!menu.hidden) { menu.hidden = true; spdBtn.setAttribute('aria-expanded', 'false'); } else if (!document.fullscreenElement) close(); e.preventDefault(); e.stopPropagation(); return; }
    if (k === 'Tab') {   // keep keyboard focus inside the window
      var f = Array.prototype.filter.call(overlay.querySelectorAll('button,a[href],input,[tabindex="0"]'), function (n) { return !n.disabled && n.offsetParent !== null; });
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
      return;
    }
    var tag = (document.activeElement && document.activeElement.tagName) || '';
    if (tag === 'INPUT' || (tag === 'BUTTON' && (k === ' ' || k === 'Enter'))) return;
    if (k === ' ' || k === 'k' || k === 'K') { e.preventDefault(); toggle(); }
    else if (k === 'ArrowLeft') { e.preventDefault(); seekBy(-5); }
    else if (k === 'ArrowRight') { e.preventDefault(); seekBy(5); }
    else if (k === 'm' || k === 'M') { muteBtn.click(); }
    else if (k === 'f' || k === 'F') { toggleFull(); }
  }

  function open(from) {
    build(); resetState();
    opener = from || document.activeElement;
    clearTimeout(readyTimer); clearInterval(listenTimer);
    frameWrap.innerHTML = ''; endEl.hidden = true; errEl.hidden = true; menu.hidden = true;
    overlay.hidden = false; document.documentElement.classList.add('htd-lock');
    requestAnimationFrame(function () { overlay.classList.add('htd-in'); });
    paint();
    // the video is requested only now, after the visitor's click
    frame = document.createElement('iframe');
    frame.tabIndex = -1;   // Tab skips the frame; Esc and Space stay ours
    frame.title = 'HookTrack demo video'; frame.setAttribute('allow', 'autoplay; encrypted-media; picture-in-picture; fullscreen'); frame.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
    var q = '?autoplay=1&controls=0&rel=0&playsinline=1&fs=0&disablekb=1&enablejsapi=1&origin=' + encodeURIComponent(location.origin);
    frame.src = cfg.embedBase + VIDEO_ID + q;
    frame.addEventListener('load', function () { listen(); S.tries = 0; listenTimer = setInterval(function () { if (S.got || ++S.tries > 60) { clearInterval(listenTimer); return; } listen(); }, 250); });
    frameWrap.appendChild(frame);
    readyTimer = setTimeout(function () {
      if (S.got) return;
      frameWrap.innerHTML = ''; frame = null; spin.hidden = true; errEl.hidden = false;     // the player failed: replace it, never cover a working one
    }, READY_TIMEOUT_MS);
    setTimeout(function () { try { box.focus({ preventScroll: true }); } catch (e) {} }, 60);   // not on Close: Space must pause, not close
  }

  function close() {
    if (!overlay || overlay.hidden) return;
    clearTimeout(readyTimer); clearInterval(listenTimer);
    send('pauseVideo');
    if (document.fullscreenElement) { try { document.exitFullscreen(); } catch (e) {} }
    overlay.classList.remove('htd-in');
    setTimeout(function () {
      overlay.hidden = true; frameWrap.innerHTML = ''; frame = null;       // removing the frame ends playback and any connection to YouTube
      document.documentElement.classList.remove('htd-lock');
      if (opener && opener.focus) { try { opener.focus({ preventScroll: true }); } catch (e) {} }
    }, 180);
  }

  api.open = open; api.close = close; api._state = function () { return S; };

  // every "Watch the demo" link opens the player; without JavaScript they still go to YouTube
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button > 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest('a[href*="youtu.be/' + VIDEO_ID + '"],a[href*="youtube.com/watch?v=' + VIDEO_ID + '"],[data-htd-open]');
    if (!a) return;
    e.preventDefault();
    open(a);
  });
})(typeof window !== 'undefined' ? window : globalThis);

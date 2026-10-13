/* Cookie notice shared by the landing page, the legal pages and the app.
 *
 * HookTrack sets exactly one cookie, an essential HttpOnly session cookie, and runs no advertising or analytics.
 * Strictly necessary cookies need no opt-in, so this is a notice with an acknowledgement, not a set of toggles
 * for cookies that do not exist. If a non-essential category is ever added, put it in CATEGORIES and bump VERSION:
 * everyone is then asked again, and the banner switches to Accept / Reject for the optional ones.
 */
(function () {
  'use strict';
  var KEY = 'ht_cookie_notice';
  var VERSION = 1;
  var CATEGORIES = { essential: 'Keeps you signed in. Always on.' };       // add optional categories here

  var optional = Object.keys(CATEGORIES).filter(function (k) { return k !== 'essential'; });

  function read() {
    try {
      var v = JSON.parse(localStorage.getItem(KEY) || 'null');
      return v && v.v === VERSION ? v : null;
    } catch (e) { return null; }
  }
  function write(choice) {
    try { localStorage.setItem(KEY, JSON.stringify({ v: VERSION, at: new Date().toISOString(), choice: choice })); } catch (e) { /* private mode */ }
  }
  function clear() { try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ } }

  var CSS = '' +
    '#ht-cookie{position:fixed;left:16px;right:16px;bottom:16px;z-index:2147483000;max-width:640px;margin:0 auto;' +
    'background:var(--surface,#121826);color:var(--text,#e8edf7);border:1px solid var(--border,#2a3548);border-radius:14px;' +
    'box-shadow:0 18px 50px -12px rgba(0,0,0,.55);padding:16px 18px;font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}' +
    '@media (prefers-color-scheme:light){#ht-cookie{background:var(--surface,#fff);color:var(--text,#141a2b);border-color:var(--border,#dbe0ee)}}' +
    '#ht-cookie h2{margin:0 0 4px;font-size:15px}' +
    '#ht-cookie p{margin:0;color:var(--muted,#9aa6bf)}' +
    '#ht-cookie a{color:var(--accent,#818cf8);text-decoration:underline}' +
    '#ht-cookie .ht-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;justify-content:flex-end}' +
    '#ht-cookie button{font:inherit;font-weight:650;border-radius:10px;padding:8px 16px;cursor:pointer;' +
    'border:1px solid var(--border,#2a3548);background:var(--surface-2,#1a2234);color:inherit}' +
    '#ht-cookie button.ht-primary{background:var(--accent-strong,#6366f1);border-color:var(--accent-strong,#6366f1);color:#fff}' +
    '#ht-cookie button:focus-visible,#ht-cookie a:focus-visible{outline:2px solid var(--accent,#818cf8);outline-offset:2px}' +
    '@media (max-width:480px){#ht-cookie{left:10px;right:10px;bottom:10px}#ht-cookie .ht-row button{flex:1}}';

  function hide() {
    var el = document.getElementById('ht-cookie');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function show() {
    if (document.getElementById('ht-cookie')) return;
    if (!document.getElementById('ht-cookie-css')) {
      var st = document.createElement('style');
      st.id = 'ht-cookie-css';
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    var box = document.createElement('div');
    box.id = 'ht-cookie';
    box.setAttribute('role', 'region');
    box.setAttribute('aria-label', 'Cookie notice');

    var hasOptional = optional.length > 0;
    box.innerHTML =
      '<h2>Cookies</h2>' +
      '<p>' + (hasOptional
        ? 'We use an essential cookie to keep you signed in, and optional cookies you can choose below.'
        : 'HookTrack uses one essential cookie to keep you signed in. We do not use advertising or tracking cookies.') +
      ' <a href="/privacy#cookies">How we use cookies</a></p>' +
      '<div class="ht-row">' +
      (hasOptional ? '<button type="button" data-ht="reject">Essential only</button>' : '') +
      '<button type="button" class="ht-primary" data-ht="' + (hasOptional ? 'accept' : 'ack') + '">' +
      (hasOptional ? 'Accept all' : 'Got it') + '</button></div>';

    box.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('[data-ht]') : null;
      if (!t) return;
      write(t.getAttribute('data-ht'));
      hide();
    });
    document.body.appendChild(box);
  }

  function start() {
    if (!read()) show();
    // a footer link can bring the notice back: <a href="#" data-cookie-notice>Cookie notice</a>
    document.addEventListener('click', function (e) {
      var a = e.target.closest ? e.target.closest('[data-cookie-notice]') : null;
      if (!a) return;
      e.preventDefault();
      clear();
      show();
    });
  }

  window.HookTrackCookies = {
    /** what the visitor chose: null (not yet), 'ack' or, once optional cookies exist, 'accept' / 'reject' */
    choice: function () { var v = read(); return v ? v.choice : null; },
    allows: function (category) {
      if (category === 'essential') return true;
      var v = read();
      return !!(v && v.choice === 'accept' && CATEGORIES[category]);
    },
    reopen: function () { clear(); show(); }
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();

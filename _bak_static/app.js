/* HookTrack - vanilla JS single page app */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = n => (n == null ? '' : Number(n).toLocaleString());
const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const parseISO = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const mondayOf = d => addDays(d, -((d.getDay() + 6) % 7));
const browserTz = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } };
const wsTz = () => (state.me && state.me.workspace && state.me.workspace.timezone) || browserTz();
// "now" as a Date whose local fields are the current calendar date in the WORKSPACE time zone
const nowLocal = () => {
  try { return parseISO(new Intl.DateTimeFormat('en-CA', { timeZone: wsTz(), year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())); }
  catch { return new Date(); }
};
const tzOptions = cur => {
  let zones = [];
  try { zones = Intl.supportedValuesOf('timeZone'); } catch {}
  if (!zones.includes('UTC')) zones = ['UTC', ...zones];
  if (cur && !zones.includes(cur)) zones = [cur, ...zones];
  return zones.map(z => `<option value="${esc(z)}" ${z === cur ? 'selected' : ''}>${esc(z)}</option>`).join('');
};
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const PIPELINE = ['idea', 'scripted', 'filmed', 'posted', 'analysed'];
const PIPE_LABEL = { idea: 'Idea', scripted: 'Scripted', filmed: 'Filmed', posted: 'Posted', analysed: 'Analysed', planned: 'Scripted' };
const normStatus = s => (s === 'planned' ? 'scripted' : (s || 'idea'));
const short = (s, n = 28) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s || '');

const pinsKey = () => {
  const id = state.me && state.me.workspace && state.me.workspace.id;
  return id ? 'hooktrack_pins_' + id : 'hooktrack_pins';
};
const getPins = () => {
  try { return new Set(JSON.parse(localStorage.getItem(pinsKey()) || '[]').map(String)); } catch { return new Set(); }
};
const setPins = (set) => { try { localStorage.setItem(pinsKey(), JSON.stringify([...set])); } catch {} };
const togglePin = (accountId) => {
  const s = getPins();
  const id = String(accountId);
  if (s.has(id)) s.delete(id); else s.add(id);
  setPins(s);
  return s;
};
const applyTheme = () => {
  const t = localStorage.getItem('hooktrack_theme') || 'system';
  let dark = t === 'dark';
  if (t === 'system') dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
};
applyTheme();

const state = {
  me: null, accounts: [], phones: [], settings: {}, ai: { available: false }, sv: { configured: false },
  weekStart: null,   // set lazily from the workspace time zone
  calMonth: null,
  calAccount: '', hookStatus: '', hookAccount: '', postsView: 'board',
};

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.startsWith('/api/auth/')) { state.me = null; renderAuth(); throw new Error('Please sign in'); }
  if (!r.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map(x => `${(x.loc || []).slice(1).join('.')}: ${x.msg}`).join('; ') : d || r.statusText);
  }
  return data;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = ''), err ? 5000 : 2400);
}
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };

/* ---------------- one-time onboarding tips (P2) ---------------- */
const TIPS = [
  { id: 'home', hash: 'dashboard', title: 'Your home base', body: 'Finish these steps to unlock Scale / Tweak / Stop insights. You can also load sample data to explore first.' },
  { id: 'views', hash: 'views', title: 'Log views here', body: 'Type weekly view counts, or use AI screenshot entry if AI is on. This feeds the dashboard.' },
  { id: 'hooks', hash: 'hooks', title: 'Score your hooks', body: 'Rate the first 3 seconds (out of 30). High scores + strong views → Scale.' },
  { id: 'posts', hash: 'pipeline', title: 'Plan content on Posts', body: 'Use the Board for stages (Idea → Posted) or switch to Calendar for dates. Same posts either way.' },
];
function tipsKey() {
  const id = state.me && state.me.workspace && state.me.workspace.id;
  return id ? `hooktrack_tips_v1_${id}` : 'hooktrack_tips_v1';
}
function tipsSeen() {
  try { return JSON.parse(localStorage.getItem(tipsKey()) || '{}'); } catch { return {}; }
}
function markTipSeen(id) {
  const s = tipsSeen(); s[id] = true; localStorage.setItem(tipsKey(), JSON.stringify(s));
}
function tipsDismissAll() {
  const s = tipsSeen(); s._all = true; localStorage.setItem(tipsKey(), JSON.stringify(s));
  const el = $('#coach'); if (el) el.remove();
}

function openCommandPalette() {
  const m = openModal(`<h2 class="cmd-title">Search</h2>
    <input id="cmd-q" class="cmd-input" placeholder="Jump to a page, post, or account…" autocomplete="off" spellcheck="false">
    <div id="cmd-results" class="cmd-results muted small">Type to search · Esc to close</div>
    <p class="muted small" style="margin-top:8px">Shortcut: <kbd>Ctrl</kbd>/<kbd>⌘</kbd> + <kbd>K</kbd></p>`, true);
  const input = $('#cmd-q', m);
  const box = $('#cmd-results', m);
  let timer;
  const run = guard(async () => {
    const q = input.value.trim();
    const data = await api('GET', `/api/search?q=${encodeURIComponent(q)}`);
    const items = [];
    (data.pages || []).forEach(p => items.push({ kind: 'page', label: p.label, href: p.href }));
    (data.accounts || []).forEach(a => items.push({ kind: 'account', label: a.handle, sub: `${a.platform} · ${a.niche || '—'}`, href: '#/accounts' }));
    (data.posts || []).forEach(p => items.push({ kind: 'post', label: p.hook || '(no hook)', sub: `${p.handle || '—'} · ${p.post_date || ''} · ${p.status}`, id: p.id, href: '#/hooks' }));
    if (!items.length) { box.innerHTML = '<div class="cmd-empty">No matches</div>'; return; }
    box.innerHTML = items.map((it, i) => `<button type="button" class="cmd-row" data-i="${i}">
      <span class="cmd-kind">${it.kind}</span>
      <span class="cmd-label">${esc(short(it.label, 60))}</span>
      ${it.sub ? `<span class="cmd-sub">${esc(it.sub)}</span>` : ''}
    </button>`).join('');
    box._items = items;
    $$('.cmd-row', box).forEach(btn => btn.onclick = () => {
      const it = items[+btn.dataset.i];
      m.close();
      if (it.kind === 'post' && it.id) {
        location.hash = '#/hooks';
        // open after route
        setTimeout(() => {
          api('GET', `/api/posts?limit=200`).then(posts => {
            const post = posts.find(p => p.id === it.id);
            if (post) postModal(post, null, route);
          }).catch(() => {});
        }, 200);
      } else if (it.href) location.hash = it.href;
    });
  });
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 120); });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      const first = $('.cmd-row', box);
      if (first) first.click();
    }
  });
  setTimeout(() => input.focus(), 50);
  run();
}

function openHelpGlossary() {
  const m = openModal(`<h2>How HookTrack works</h2>
    <div class="glossary">
      <div class="g-item"><b>Hook score</b><p>Three ratings out of 10 (scroll-stop, specificity, conversion). Total out of 30. Used with weekly views for account decisions.</p></div>
      <div class="g-item"><b>${decisionLabel('SCALE')}</b><p>Weekly views are strong <em>and</em> you have a high-scoring hook — double down on this account / style.</p></div>
      <div class="g-item"><b>${decisionLabel('MUTATE')}</b><p>Something’s working but not enough — change the first 3 seconds of the next posts.</p></div>
      <div class="g-item"><b>${decisionLabel('KILL')}</b><p>Not producing after several posts — pause the account or rebuild the content angle.</p></div>
      <div class="g-item"><b>Posts board</b><p>Idea → Scripted → Filmed → Posted → Analysed. Drag cards between stages. Calendar is the same posts by date.</p></div>
      <div class="g-item"><b>Views</b><p>Daily/weekly view counts per account. Manual, AI screenshot, or auto-import (SociaVault add-on).</p></div>
      <div class="g-item"><b>Add-ons</b><p>Optional: AI, view auto-import, video generation, social publish. Core tracking works without them.</p></div>
    </div>
    <div class="actions"><button class="btn primary" data-close>Close</button></div>`, true);
}
function nextKey() {
  const id = state.me && state.me.workspace && state.me.workspace.id;
  return id ? `hooktrack_next_v1_${id}` : 'hooktrack_next_v1';
}
function nextSeen() {
  try { return localStorage.getItem(nextKey()) === '1'; } catch { return false; }
}
function markNextSeen() {
  try { localStorage.setItem(nextKey(), '1'); } catch {}
}
function showCoachTip() {

  const seen = tipsSeen();
  if (seen._all) return;
  const hash = (location.hash.replace('#/', '') || 'dashboard').split('?')[0];
  const tip = TIPS.find(t => t.hash === hash && !seen[t.id]);
  if (!tip) return;
  let el = $('#coach');
  if (!el) {
    el = document.createElement('div');
    el.id = 'coach';
    el.className = 'coach';
    document.body.appendChild(el);
  }
  el.innerHTML = `<div class="coach-card">
    <button type="button" class="coach-x" title="Dismiss all tips">×</button>
    <div class="coach-kicker">Quick tip</div>
    <b>${esc(tip.title)}</b>
    <p>${esc(tip.body)}</p>
    <div class="coach-actions">
      <button type="button" class="btn sm" id="coach-skip">Skip tips</button>
      <button type="button" class="btn sm primary" id="coach-ok">Got it</button>
    </div>
  </div>`;
  $('#coach-ok', el).onclick = () => { markTipSeen(tip.id); el.remove(); };
  $('#coach-skip', el).onclick = () => tipsDismissAll();
  $('.coach-x', el).onclick = () => tipsDismissAll();
}


function openModal(html, wide = false) {
  const root = $('#modal-root');
  const el = document.createElement('div');
  el.className = 'overlay';
  el.innerHTML = `<div class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true">${html}</div>`;
  root.appendChild(el);
  const close = () => { el.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape' && root.lastElementChild === el) close(); };
  document.addEventListener('keydown', onKey);
  $$('[data-close]', el).forEach(b => b.addEventListener('click', close));
  el.close = close;
  return el;
}

function busy(btn, on, label) {
  if (on) { btn.dataset.l = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spin"></span> ${label || ''}`; }
  else { btn.disabled = false; btn.innerHTML = btn.dataset.l; }
}

function parseCount(v) {
  const m = String(v).trim().toLowerCase().replace(/,/g, '').match(/^(\d+(?:\.\d+)?)\s*([km]?)$/);
  if (!m) return NaN;
  return Math.round(parseFloat(m[1]) * ({ '': 1, k: 1e3, m: 1e6 }[m[2]]));
}

const accountOptions = (sel, blank) =>
  (blank ? `<option value="">${blank}</option>` : '') +
  state.accounts.map(a => `<option value="${a.id}" ${String(sel) === String(a.id) ? 'selected' : ''}>${esc(a.handle)}</option>`).join('');
const DECISION_LABEL = { SCALE: 'Scale', MUTATE: 'Tweak', KILL: 'Stop', KILLED: 'Stop', HOLD: 'Hold' };
const decisionLabel = t => DECISION_LABEL[t] || t || '';
const pill = (t, cls) => {
  if (!t) return '';
  const label = decisionLabel(t);
  return `<span class="pill ${esc(cls || t)}" title="${esc(t)}">${esc(label)}</span>`;
};

const compact = n => {
  if (n == null) return '';
  const a = Math.abs(n);
  const f = (v, s) => (v >= 100 ? Math.round(v) : Math.round(v * 10) / 10) + s;
  return a >= 1e9 ? f(n / 1e9, 'B') : a >= 1e6 ? f(n / 1e6, 'M') : a >= 1e4 ? f(n / 1e3, 'k') : Number(n).toLocaleString();
};
const WIN_TIP = {
  WINNER: 'Beat this account\'s own normal by the winner threshold: a hook worth repeating',
  AVERAGE: 'Around this account\'s normal',
  DUD: 'Well below this account\'s normal: learn what not to repeat',
  TOO_EARLY: 'Under 2 days old: still collecting views, not judged yet',
  HOT_START: 'Under 2 days old but its first day is far above this account\'s usual: act on it now',
  NO_BASELINE: 'Needs 5+ older posts on this account to know what normal is',
};
const winnerChip = pf => {
  if (!pf) return '<span class="muted">-</span>';
  const cls = { WINNER: 'SCALE', DUD: 'KILL', HOT_START: 'SCALE' }[pf.label] || '';
  const hot = pf.label === 'HOT_START';
  const text = hot ? `${pf.early_multiple}× day 1` : (pf.multiple != null ? `${pf.multiple}×` : (pf.label === 'TOO_EARLY' ? 'early' : '-'));
  const star = pf.label === 'WINNER' ? '★ ' : hot ? '🔥 ' : '';
  const tip = `${WIN_TIP[pf.label] || ''}${pf.baseline ? ` (normal ≈ ${fmt(pf.baseline)} views)` : ''}${hot && pf.early_baseline ? ` (usual first day ≈ ${fmt(pf.early_baseline)} views)` : ''}`;
  return `<span class="pill ${cls}" title="${esc(tip)}">${star}${esc(text)}</span>${pf.high_intent ? ' <span class="chip-intent" title="People saved, shared or commented far more than usual for this account">intent</span>' : ''}`;
};

const decisionSource = p => {
  const b = p.decision_basis || '';
  return b.startsWith('Measured') ? 'measured' : b.startsWith('Fast start') ? 'fast start' : 'hook score';
};

const scoreSourceChip = p => {
  if (p.v == null) return '';
  if (p.score_by === 'ai') {
    return p.score_blind === true
      ? '<span class="chip-intent" title="Judged on the wording alone, so it is a fair test of whether wording predicts results">wording only</span>'
      : `<span class="chip-intent chip-warn" title="${p.score_blind === false ? 'The prompt included this post\'s results, so the score partly reflects them' : 'Made before this was tracked: not used when fitting thresholds'}">${p.score_blind === false ? 'saw the results' : 'origin unknown'}</span>`;
  }
  return p.score_by === 'manual' || p.score_by === 'imported' ? '<span class="chip-intent" title="A human score">your score</span>' : '';
};

const accColor = id => (state.accounts.find(a => a.id === id) || {}).phone_color || 'var(--accent)';

async function loadBase() {
  [state.accounts, state.phones, state.settings, state.ai, state.sv, state.video, state.publish, state.queued] = await Promise.all([
    api('GET', '/api/accounts'), api('GET', '/api/phones'), api('GET', '/api/settings'),
    api('GET', '/api/ai/status'),
    api('GET', '/api/connectors/sociavault').catch(() => ({ configured: false })),
    api('GET', '/api/addons/video').catch(() => ({ enabled: false })),
    api('GET', '/api/addons/publish').catch(() => ({ enabled: false })),
    api('GET', '/api/publish/queued-count').catch(() => ({ count: 0 })),
  ]);
  try {
    const pins = getPins();
    state.accounts = [...state.accounts].sort((a, b) => {
      const pa = pins.has(String(a.id)) ? 0 : 1;
      const pb = pins.has(String(b.id)) ? 0 : 1;
      return pa - pb || (a.handle || '').localeCompare(b.handle || '');
    });
  } catch {}
  const b = $('#ai-badge');
  b.textContent = state.ai.available ? 'AI on' : 'AI off';
  b.className = 'badge' + (state.ai.available ? ' on' : '');
  b.title = state.ai.available ? `Model: ${state.ai.model}` : 'AI is not configured for this workspace (Settings → AI)';
  state.baseLoaded = true;
  updateNav();
}
const isAdmin = () => state.me && ['owner', 'admin'].includes(state.me.workspace.role);
const aiNotice = () => (state.ai.available ? '' :
  `<div class="notice">AI is off for this workspace. ${isAdmin() ? 'Add an API key in <a href="#/settings">Settings → AI</a>' : 'Ask a workspace admin to add an API key in Settings → AI'} to turn it on. Manual entry works without it.</div>`);

/* ---------------- router ---------------- */
const pages = {};
async function route() {
  const hash = location.hash.replace('#/', '');
  if (hash.startsWith('invite/')) return renderInvite(hash.slice(7));
  if (hash.startsWith('reset/')) return renderReset(hash.slice(6));
  if (hash === 'forgot') return renderForgot();
  if (!state.me) {
    try { state.me = await api('GET', '/api/auth/me'); } catch { state.me = null; }
    if (!state.me) return renderAuth();
  }
  document.body.classList.remove('anon'); renderChrome();
  const tab = (hash || 'dashboard').split('?')[0];
  const page = pages[tab] || pages.dashboard;
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.tab === (pages[tab] ? tab : 'dashboard')));
  try { await loadBase(); await page($('#app')); showCoachTip(); } catch (e) {
    if (state.me) $('#app').innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p></div>`;
  }
}
window.addEventListener('hashchange', route);
window.addEventListener('keydown', e => {
  if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    openCommandPalette();
    return;
  }
  if (e.key !== 'n' && e.key !== 'N') return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
  const hash = (location.hash.replace('#/', '') || '').split('?')[0];
  if (hash === 'hooks' || hash === 'pipeline') {
    e.preventDefault();
    postModal(null, { status: hash === 'pipeline' ? 'idea' : 'scripted', account_id: state.pipeAccount || state.hookAccount || '' }, route);
  }
});


/* ---------------- chrome: workspace switcher, sign out ---------------- */
function renderChrome() {
  const sw = $('#ws-switch');
  sw.innerHTML = state.me.workspaces.map(w => `<option value="${w.id}" ${w.id === state.me.workspace.id ? 'selected' : ''}>${esc(w.name)}</option>`).join('')
    + '<option value="new">＋ New workspace…</option>';
  $('#user-email').textContent = state.me.user.email;
  let pa = $('#platform-admin-link');
  if (state.me.platform_admin) {
    if (!pa) {
      pa = document.createElement('a');
      pa.id = 'platform-admin-link';
      pa.className = 'btn sm';
      pa.href = '/admin/';
      pa.textContent = 'Platform';
      pa.title = 'Platform admin console';
      const tr = document.querySelector('.top-right');
      if (tr) tr.insertBefore(pa, tr.firstChild);
    }
    pa.style.display = '';
  } else if (pa) {
    pa.style.display = 'none';
  }
  updateNav();
}


function navPhase() {
  const n = (state.accounts || []).length;
  const setup = state.setup || {};
  if (!n) return 1;
  if (!setup.complete) return 2;
  return 3;
}

function updateNav() {
  if (!$('#nav')) return;
  const phase = navPhase();
  const pubOn = !!(state.publish && state.publish.enabled);
  const show = {
    dashboard: true,
    accounts: true,
    settings: true,
    today: true,
    views: phase >= 2,
    hooks: phase >= 2,
    pipeline: phase >= 2,   // primary posts UI ("Posts")
    calendar: false,        // secondary: open from Posts → Calendar toggle
    inspiration: phase >= 3,
    scheduled: pubOn && (phase >= 3 || ((state.queued && state.queued.attention) || 0) > 0),
  };
  $$('#nav a').forEach(a => {
    const tab = a.dataset.tab;
    if (!tab) return;
    a.style.display = show[tab] === false ? 'none' : '';
    if (tab === 'dashboard') a.textContent = phase < 3 ? 'Home' : 'Dashboard';
    if (tab === 'views') a.textContent = 'Views';
    if (tab === 'pipeline') a.textContent = 'Posts';
    if (tab === 'scheduled') {
      const n = ((state.queued && state.queued.count) || 0) + ((state.queued && state.queued.attention) || 0);
      a.textContent = n ? `Scheduled (${n})` : 'Scheduled';
    }
  });
  const hash = (location.hash.replace('#/', '') || 'dashboard').split('?')[0];
  // only judge once the account data is in; before that every gated tab looks "locked" and a refresh/bookmark would bounce
  if (state.baseLoaded && show[hash] === false) location.hash = '#/dashboard';

  // Setup progress + help in header
  let prog = $('#setup-progress');
  if (!prog) {
    prog = document.createElement('button');
    prog.id = 'setup-progress';
    prog.type = 'button';
    prog.className = 'setup-progress';
    prog.title = 'Setup progress';
    const nav = $('#nav');
    if (nav && nav.parentNode) nav.parentNode.insertBefore(prog, nav.nextSibling);
  }
  const setup = state.setup || {};
  if (!setup.complete && phase < 3) {
    const done = [setup.has_accounts, setup.has_views, setup.has_scored_hook].filter(Boolean).length;
    prog.style.display = '';
    prog.innerHTML = `<span class="sp-bar"><span style="width:${Math.round(done/3*100)}%"></span></span><span class="sp-label">${done}/3 setup</span>`;
    prog.onclick = () => { location.hash = '#/dashboard'; };
  } else {
    prog.style.display = 'none';
  }
  let help = $('#help-btn');
  if (!help) {
    help = document.createElement('button');
    help.id = 'help-btn';
    help.type = 'button';
    help.className = 'btn sm help-btn';
    help.textContent = '?';
    help.title = 'How HookTrack works';
    const tr = $('.top-right');
    if (tr) tr.insertBefore(help, tr.firstChild);
    help.onclick = () => openHelpGlossary();
  }
  let searchBtn = $('#search-btn');
  if (!searchBtn) {
    searchBtn = document.createElement('button');
    searchBtn.id = 'search-btn';
    searchBtn.type = 'button';
    searchBtn.className = 'btn sm';
    searchBtn.textContent = 'Search';
    searchBtn.title = 'Search (Ctrl/⌘ K)';
    const tr = $('.top-right');
    if (tr) tr.insertBefore(searchBtn, help);
    searchBtn.onclick = () => openCommandPalette();
  }
  let themeBtn = $('#theme-btn');
  if (!themeBtn) {
    themeBtn = document.createElement('button');
    themeBtn.id = 'theme-btn';
    themeBtn.type = 'button';
    themeBtn.className = 'btn sm theme-btn';
    themeBtn.title = 'Toggle theme: system / dark / light';
    const tr = $('.top-right');
    if (tr) tr.insertBefore(themeBtn, searchBtn);
    const syncThemeLabel = () => {
      const t = localStorage.getItem('hooktrack_theme') || 'system';
      themeBtn.textContent = t === 'dark' ? 'Light' : (t === 'light' ? 'Auto' : 'Dark');
    };
    syncThemeLabel();
    themeBtn.onclick = () => {
      const cur = localStorage.getItem('hooktrack_theme') || 'system';
      const next = cur === 'system' ? 'dark' : cur === 'dark' ? 'light' : 'system';
      localStorage.setItem('hooktrack_theme', next);
      applyTheme();
      syncThemeLabel();
    };
  }
}
function resetViewState() { state.calAccount = ''; state.hookAccount = ''; state.hookStatus = ''; }
$('#ws-switch').addEventListener('change', guard(async e => {
  if (e.target.value === 'new') {
    const name = prompt('Name for the new workspace?');
    if (!name || !name.trim()) { renderChrome(); return; }
    state.me = await api('POST', '/api/workspaces', { name: name.trim(), timezone: browserTz() });
  } else {
    state.me = await api('POST', '/api/auth/switch', { workspace_id: +e.target.value });
  }
  resetViewState(); location.hash = '#/dashboard'; route();
}));
$('#logout').addEventListener('click', guard(async () => {
  await api('POST', '/api/auth/logout'); state.me = null; resetViewState(); location.hash = ''; renderAuth();
}));

/* ---------------- sign in / sign up / invite ---------------- */
function renderAuth(mode = 'login', note = '') {
  document.body.classList.add('anon');
  note = note || state.authNote || '';
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo">▲</span> HookTrack</div>
    <h1>${mode === 'login' ? 'Sign in' : 'Create your workspace'}</h1>
    <p class="muted">${mode === 'login' ? 'Welcome back.' : 'Each workspace is private: its accounts, views, posts and AI settings are never shared with another workspace.'}</p>
    ${note ? `<div class="notice">${esc(note)}</div>` : ''}
    <form id="auth-form" class="fields" style="grid-template-columns:1fr">
      ${mode === 'signup' ? `<div><label class="f">Your name</label><input name="name" autocomplete="name" maxlength="100"></div>
        <div><label class="f">Workspace name</label><input name="workspace_name" placeholder="e.g. Acme Growth" maxlength="100"></div>` : ''}
      <div><label class="f">Email</label><input name="email" type="email" autocomplete="email" required></div>
      <div><label class="f">Password${mode === 'signup' ? ' (8+ characters)' : ''}</label><input name="password" type="password" minlength="${mode === 'signup' ? 8 : 1}" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}" required></div>
      <div id="auth-err" class="small" style="color:var(--bad)" role="alert"></div>
      <button class="btn primary">${mode === 'login' ? 'Sign in' : 'Create workspace'}</button></form>
    ${mode === 'login' ? '<p class="small" style="margin-top:10px"><a href="#/forgot">Forgot your password?</a></p>' : ''}
    <p class="small muted" style="margin-top:14px">${mode === 'login' ? 'New here? <a href="#" id="swap">Create a workspace</a>' : 'Already have an account? <a href="#" id="swap">Sign in</a>'}</p></div>`;
  $('#swap').onclick = e => { e.preventDefault(); renderAuth(mode === 'login' ? 'signup' : 'login'); };
  $('#auth-form').onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    if (mode !== 'login') body.timezone = browserTz();
    try {
      state.me = await api('POST', mode === 'login' ? '/api/auth/login' : '/api/auth/signup', body);
      state.authNote = '';
      location.hash = '#/dashboard'; route();
    } catch (err) { $('#auth-err').textContent = err.message; }
  };
}

function renderForgot() {
  document.body.classList.add('anon');
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo">▲</span> HookTrack</div>
    <h1>Reset your password</h1><p class="muted">Enter your account email and we'll send a link to choose a new password.</p>
    <form id="fg-form" class="fields" style="grid-template-columns:1fr">
      <div><label class="f">Email</label><input name="email" type="email" autocomplete="email" required></div>
      <div id="fg-msg" class="small" role="status"></div>
      <button class="btn primary">Send reset link</button></form>
    <p class="small" style="margin-top:14px"><a href="#/dashboard" id="fg-back">Back to sign in</a></p></div>`;
  $('#fg-back').onclick = e => { e.preventDefault(); location.hash = '#/dashboard'; route(); };
  $('#fg-form').onsubmit = async e => {
    e.preventDefault();
    const msg = $('#fg-msg'); msg.style.color = '';
    try {
      const r = await api('POST', '/api/auth/forgot', { email: new FormData(e.target).get('email') });
      msg.textContent = r.email_configured
        ? 'If that email has an account, a reset link is on its way. It expires in 1 hour.'
        : 'Email is not set up on this server. The reset link has been written to the server log: ask whoever runs HookTrack to send it to you.';
    } catch (err) { msg.style.color = 'var(--bad)'; msg.textContent = err.message; }
  };
}

async function renderReset(token) {
  document.body.classList.add('anon');
  let info;
  try { info = await api('GET', `/api/auth/reset/${encodeURIComponent(token)}`); }
  catch (e) {
    $('#app').innerHTML = `<div class="auth card"><h1>Link not valid</h1><p class="muted">${esc(e.message)}</p>
      <a class="btn" href="#/forgot">Send a new link</a></div>`;
    return;
  }
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo">▲</span> HookTrack</div>
    <h1>Choose a new password</h1><p class="muted">For <b>${esc(info.email)}</b>. You'll be signed out everywhere else.</p>
    <form id="rs-form" class="fields" style="grid-template-columns:1fr">
      <div><label class="f">New password (8+ characters)</label><input name="password" type="password" minlength="8" autocomplete="new-password" required></div>
      <div><label class="f">Repeat it</label><input name="again" type="password" minlength="8" autocomplete="new-password" required></div>
      <div id="rs-err" class="small" style="color:var(--bad)" role="alert"></div>
      <button class="btn primary">Set password</button></form></div>`;
  $('#rs-form').onsubmit = async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (f.password !== f.again) { $('#rs-err').textContent = "The two passwords don't match"; return; }
    try {
      await api('POST', '/api/auth/reset', { token, password: f.password });
      state.me = null; state.authNote = 'Password updated. Sign in with your new password.';
      location.hash = '#/dashboard'; renderAuth('login');
    } catch (err) { $('#rs-err').textContent = err.message; }
  };
}

async function renderInvite(token) {
  let inv;
  try { inv = await api('GET', `/api/invites/${encodeURIComponent(token)}`); }
  catch (e) { document.body.classList.add('anon'); $('#app').innerHTML = `<div class="auth card"><h1>Invite not valid</h1><p class="muted">${esc(e.message)}</p><a class="btn" href="#/dashboard">Go to HookTrack</a></div>`; return; }
  if (!state.me) { try { state.me = await api('GET', '/api/auth/me'); } catch { state.me = null; } }
  document.body.classList.add('anon');
  const signedInAsInvitee = state.me && state.me.user.email === inv.email;
  const needsLogin = inv.account_exists && !signedInAsInvitee;
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo">▲</span> HookTrack</div>
    <h1>Join ${esc(inv.workspace_name)}</h1><p class="muted">You've been invited as <b>${esc(inv.role)}</b> using <b>${esc(inv.email)}</b>.</p>
    <form id="inv-form" class="fields" style="grid-template-columns:1fr">
      ${inv.account_exists ? (needsLogin ? `<div><label class="f">Password for ${esc(inv.email)}</label><input name="password" type="password" required></div>` : '')
        : `<div><label class="f">Your name</label><input name="name" maxlength="100"></div><div><label class="f">Choose a password (8+ characters)</label><input name="password" type="password" minlength="8" required></div>`}
      <div id="inv-err" class="small" style="color:var(--bad)" role="alert"></div>
      <button class="btn primary">${needsLogin ? 'Sign in & join' : 'Join workspace'}</button></form></div>`;
  $('#inv-form').onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    try {
      if (needsLogin) await api('POST', '/api/auth/login', { email: inv.email, password: body.password });
      state.me = await api('POST', `/api/invites/${encodeURIComponent(token)}/accept`, needsLogin ? {} : body);
      resetViewState(); location.hash = '#/dashboard'; route();
    } catch (err) { $('#inv-err').textContent = err.message; }
  };
}

/* ---------------- dashboard ---------------- */
pages.dashboard = async app => {
  const d = await api('GET', '/api/dashboard');
  state.setup = d.setup || {
    has_accounts: !!d.accounts,
    has_views: (d.week_views || 0) > 0,
    has_posts: false,
    has_scored_hook: false,
    complete: false,
  };
  updateNav();

  const s = state.setup;
  // First-time / incomplete setup: checklist home
  if (!s.complete) {
    const steps = [
      { ok: s.has_accounts, title: 'Add accounts', body: 'Your TikTok / Instagram / YouTube handles (or import a spreadsheet).', href: '#/accounts', cta: 'Add accounts' },
      { ok: s.has_views, title: 'Log this week’s views', body: 'Type numbers, paste a screenshot for AI, or auto-import if configured.', href: '#/views', cta: 'Open views', need: s.has_accounts },
      { ok: s.has_scored_hook, title: 'Score one hook', body: 'Log a post and rate the first 3 seconds (hook score out of 30).', href: '#/hooks', cta: 'Open hooks', need: s.has_accounts },
      { ok: state.ai.available, title: 'Optional: turn on AI', body: 'Screenshot reading, auto-scoring, and hook ideas. Manual entry works without it.', href: '#/settings', cta: 'Settings → AI', optional: true },
    ];
    const done = steps.filter(x => x.ok || x.optional && x.ok).length;
    app.innerHTML = `<div class="page-head"><div><h1>Get started</h1>
      <div class="sub">Track views, score hooks, and see what to <b>Scale</b>, <b>Tweak</b>, or <b>Stop</b>. About 10 minutes to first insight.</div></div></div>
      <div class="card setup-checklist">
        <p class="muted" style="margin-bottom:14px">Complete the steps below. Advanced tools (pipeline, publish, video) unlock after you have accounts, views, and a scored hook.</p>
        ${!s.has_accounts && isAdmin() ? `<div class="demo-banner">
          <div><b>Want to explore first?</b><div class="muted small">Load sample accounts, views, and scored hooks — then try Scale / Tweak / Stop on the dashboard.</div></div>
          <button class="btn primary" id="seed-demo">Load sample data</button>
        </div>` : ''}
        ${s.has_accounts && state.settings && state.settings.demo_seeded ? `<div class="demo-banner subtle">
          <div class="muted small">Sample data is loaded in this workspace.</div>
          ${isAdmin() ? '<button class="btn sm" id="clear-demo">Clear sample data</button>' : ''}
        </div>` : ''}
        <ol class="setup-steps">
          ${steps.map((st, i) => {
            const locked = st.need === false;
            const cls = st.ok ? 'done' : (locked ? 'locked' : '');
            return `<li class="${cls}">
              <div class="setup-check">${st.ok ? '✓' : (i + 1)}</div>
              <div class="setup-body">
                <b>${esc(st.title)}${st.optional ? ' <span class="muted small">optional</span>' : ''}</b>
                <div class="muted small">${esc(st.body)}</div>
              </div>
              ${st.ok ? '<span class="pill posted">Done</span>' : (locked ? '<span class="muted small">Finish previous step</span>' : `<a class="btn sm primary" href="${st.href}">${esc(st.cta)}</a>`)}
            </li>`;
          }).join('')}
        </ol>
      </div>
      ${s.has_accounts ? `<div class="card" style="margin-top:14px"><h2>What the scores mean</h2>
        <p class="muted small" style="margin:0">Hook score is out of 30 (pull + specificity + conversion). Decisions use your rules in Settings:</p>
        <ul class="list" style="margin-top:10px">
          <li>${pill('SCALE')} High weekly views + a strong hook — double down</li>
          <li>${pill('MUTATE')} Promising but not there — change the first 3 seconds</li>
          <li>${pill('KILL')} Not working after several posts — stop or rework the account</li>
        </ul></div>` : ''}`;
    const seedBtn = $('#seed-demo');
    if (seedBtn) seedBtn.onclick = guard(async () => {
      busy(seedBtn, true, 'Loading…');
      try {
        const r = await api('POST', '/api/workspace/seed-demo');
        toast(r.message || 'Sample data loaded');
        // tips still useful after demo
        route();
      } finally { busy(seedBtn, false); }
    });
    const clearBtn = $('#clear-demo');
    if (clearBtn) clearBtn.onclick = guard(async () => {
      if (!confirm('Remove all accounts, views, and posts in this workspace?')) return;
      await api('POST', '/api/workspace/clear-demo');
      toast('Sample data cleared');
      route();
    });
    return;
  }
  const delta = d.prev_week_views ? Math.round((d.week_views - d.prev_week_views) / d.prev_week_views * 100) : null;
  const maxT = Math.max(1, ...d.trend.map(t => t.views));
  const maxP = Math.max(1, ...d.by_phone.map(p => p.views));
  const showNext = !nextSeen();
  app.innerHTML = `
  <div class="page-head"><div><h1>Dashboard</h1><div class="sub">Week of ${esc(d.week_start)}</div></div>
    <div class="row">
      <button type="button" class="btn sm" id="copy-summary" title="Copy weekly summary">Copy summary</button>
      <button type="button" class="btn sm" id="export-menu" title="Export CSV">Export</button>
      <button type="button" class="btn sm" id="help-dash" title="Glossary">?</button>
    </div></div>
  ${showNext ? `<div class="card next-banner" id="next-banner">
    <div><b>You’re set up — what’s next?</b>
      <ul class="next-list">
        <li><a href="#/pipeline">Plan posts</a> on the board or calendar</li>
        <li><a href="#/inspiration">Save inspiration</a> from competitors (optional)</li>
        <li><a href="#/settings">Add-ons</a> — auto-import views, video, or publish when you need them</li>
      </ul>
    </div>
    <button class="btn sm" id="next-dismiss">Dismiss</button>
  </div>` : ''}
  <div class="grid kpis">
    <div class="card kpi"><div class="label">Views this week</div><div class="value">${fmt(d.week_views)}</div>
      <div class="delta ${delta > 0 ? 'up' : delta < 0 ? 'down' : ''}">${delta == null ? 'No prior week' : `${delta > 0 ? '▲' : delta < 0 ? '▼' : ''} ${Math.abs(delta)}% vs last week`}</div></div>
    <div class="card kpi"><div class="label">Avg / account</div><div class="value">${fmt(d.avg_per_account)}</div><div class="delta">${d.accounts} accounts</div></div>
    <div class="card kpi"><div class="label">Scale</div><div class="value up">${d.decisions.SCALE}</div><div class="delta">${fmt(d.settings.scale_weekly)}+ weekly &amp; strong hook score</div></div>
    <div class="card kpi"><div class="label">Stop</div><div class="value down">${d.decisions.KILL}</div><div class="delta">stalled ${d.settings.kill_after_posts}+ posts</div></div>
  </div>
  <div class="card" style="margin-bottom:14px"><h2>Ask your data</h2>${aiNotice()}
    <form class="ask" id="ask-form"><input id="ask-q" placeholder="e.g. Which accounts should I stop this week, and why?" maxlength="1000" ${state.ai.available ? '' : 'disabled'}>
      <button class="btn ai" ${state.ai.available ? '' : 'disabled'}>Ask</button></form>
    <div class="chips">${['Which accounts should I scale?', 'Which niche is working best?', 'What do my winning hooks have in common?']
      .map(q => `<button type="button" data-q="${esc(q)}" ${state.ai.available ? '' : 'disabled'}>${esc(q)}</button>`).join('')}</div>
    <div id="answer"></div></div>
  <div class="grid cols-2" style="margin-bottom:14px">
    <div class="card"><h2>Views, last 8 weeks</h2><div class="bars">${d.trend.map((t, i) =>
      `<div class="b ${i === 7 ? 'cur' : ''}"><div class="v">${t.views >= 1000 ? Math.round(t.views / 1000) + 'k' : t.views}</div>
       <div class="fill" style="height:${Math.round(t.views / maxT * 100)}%"></div><div class="l">${t.week.slice(5)}</div></div>`).join('')}</div></div>
    <div class="card"><h2>This week by phone</h2>${d.by_phone.map(p =>
      `<div class="hbar"><span>${esc(p.phone)}</span><div class="track"><div class="fill" style="width:${Math.round(p.views / maxP * 100)}%;background:${esc(p.color)}"></div></div>
       <span class="num" style="text-align:right">${fmt(p.views)}</span></div>`).join('')}</div>
  </div>
  ${(d.movers || []).length ? `<div class="card" style="margin-bottom:14px"><h2>Week movers</h2>
    <ul class="list">${d.movers.map(m => `<li>
      <b style="flex:1">${esc(m.handle)}</b>
      <span class="num">${fmt(m.views)}</span>
      <span class="delta ${m.delta > 0 ? 'up' : m.delta < 0 ? 'down' : ''}" style="min-width:72px;text-align:right">
        ${m.delta > 0 ? '+' : ''}${fmt(m.delta)} (${m.pct > 0 ? '+' : ''}${m.pct}%)
      </span>
      ${pill(m.decision)}
    </li>`).join('')}</ul>
    <p class="muted small" style="margin-top:8px">Vs previous week · sorted by absolute change</p>
  </div>` : ''}
  <div class="card" id="activity-card" style="margin-bottom:14px"><h2>Recent activity</h2>
    <div id="activity-list" class="muted small">Loading…</div></div>
  <div class="grid cols-2">
    <div class="card"><h2>Act on this</h2>${d.act_on.length ? `<ul class="list">${d.act_on.map(a =>
      `<li>${pill(a.decision)}<div><b>${esc(a.handle)}</b><div class="muted small">${esc(a.reason)}</div></div></li>`).join('')}</ul>`
      : '<p class="muted">Nothing to scale or kill right now.</p>'}
      ${d.unscored_posts ? `<p class="small muted">${d.unscored_posts} posted hooks aren't scored yet — <a href="#/hooks">score them</a> so SCALE decisions are accurate.</p>` : ''}</div>
    <div class="card"><h2>Top hooks <span class="muted small" style="font-weight:400">vs each account's own normal</span></h2>${d.top_hooks.length ? `<ul class="list">${d.top_hooks.map(h =>
      `<li><div style="flex:1"><div>${esc(short(h.hook, 60))}</div><div class="muted small">${esc(h.handle)} · ${compact(h.views)} views${h.saves ? ` · ${compact(h.saves)} saves` : ''}${h.intent_per_1k != null ? ` · ${h.intent_per_1k} intent/1k` : ''}</div></div>${h.multiple != null ? winnerChip({ label: h.label, multiple: h.multiple, high_intent: h.high_intent }) : ''}${h.vsc != null ? ` <b>${h.vsc}/30</b>` : ''}</li>`).join('')}</ul>`
      : '<p class="muted">Log posts in <a href="#/hooks">Hooks</a> to see your best performers.</p>'}</div>
    <div class="card"><h2>Upcoming</h2>${d.upcoming.length ? `<ul class="list">${d.upcoming.map(u =>
      `<li><span class="muted small" style="width:70px">${esc(u.post_date)}</span><div style="flex:1">${esc(short(u.hook, 50) || '(no hook yet)')}<div class="muted small">${esc(u.handle)}</div></div>${pill(u.status)}</li>`).join('')}</ul>`
      : '<p class="muted">Nothing scheduled. Plan a week on the <a href="#/calendar">calendar</a>.</p>'}</div>
    <div class="card"><h2>Weekly leaders</h2><ul class="list">${d.leaders.map(l =>
      `<li><b style="flex:1">${esc(l.handle)}</b><span>${fmt(l.views)}</span>${pill(l.decision)}</li>`).join('')}</ul></div>
  </div>`;
  const ask = guard(async q => {
    if (!q.trim()) return;
    const box = $('#answer'); box.innerHTML = '<div class="answer"><span class="spin"></span> Thinking…</div>';
    try { const r = await api('POST', '/api/ai/ask', { question: q }); box.innerHTML = `<div class="answer">${esc(r.answer)}</div>`; }
    catch (e) { box.innerHTML = ''; throw e; }
  });
  $('#ask-form').addEventListener('submit', e => { e.preventDefault(); ask($('#ask-q').value); });
  $$('.chips button', app).forEach(b => b.addEventListener('click', () => { $('#ask-q').value = b.dataset.q; ask(b.dataset.q); }));
  const nd = $('#next-dismiss');
  if (nd) nd.onclick = () => { markNextSeen(); $('#next-banner')?.remove(); };
  const hd = $('#help-dash');
  if (hd) hd.onclick = () => openHelpGlossary();
  const cs = $('#copy-summary');
  if (cs) cs.onclick = guard(async () => {
    const r = await api('GET', '/api/workspace/summary');
    try {
      await navigator.clipboard.writeText(r.text);
      toast('Weekly summary copied');
    } catch {
      openModal(`<h2>Weekly summary</h2><pre class="summary-pre">${esc(r.text)}</pre><div class="actions"><button class="btn primary" data-close>Close</button></div>`, true);
    }
  });
  const ex = $('#export-menu');
  if (ex) ex.onclick = () => {
    openModal(`<h2>Export CSV</h2>
      <p class="muted">Download data for spreadsheets or backups.</p>
      <div class="actions" style="justify-content:flex-start;gap:8px;flex-wrap:wrap">
        <a class="btn primary" href="/api/export/views.csv" download>Views (90 days)</a>
        <a class="btn primary" href="/api/export/posts.csv" download>Posts</a>
        <button class="btn" data-close>Close</button>
      </div>`, true);
  };
  // activity
  (async () => {
    const box = $('#activity-list');
    if (!box) return;
    try {
      const r = await api('GET', '/api/activity?limit=12');
      const items = r.items || [];
      if (!items.length) { box.innerHTML = '<p class="muted">No recent posts yet.</p>'; return; }
      box.innerHTML = `<ul class="list">${items.map(it => `<li>
        <span class="muted small" style="width:72px">${esc((it.post_date || '').toString().slice(0,10))}</span>
        <div style="flex:1;min-width:0">
          <div>${esc(short(it.hook || '(no hook)', 55))}</div>
          <div class="muted small">${esc(it.handle || '—')} · ${esc(it.status)}${it.vsc != null ? ` · ${it.vsc}/30` : ''}</div>
        </div>
        ${pill(it.status)}
      </li>`).join('')}</ul>`;
    } catch (e) { box.textContent = 'Could not load activity'; }
  })();
};



/* ---------------- daily views ---------------- */
pages.views = async app => {
  if (!state.weekStart) state.weekStart = iso(mondayOf(nowLocal()));
  const g = await api('GET', `/api/views?start=${state.weekStart}`);
  const start = parseISO(g.start);
  app.innerHTML = `
  <div class="page-head"><div><h1>Views</h1><div class="sub">Type numbers directly (85k works), or let AI read them from a screenshot.</div></div>
    <div class="row"><button class="btn" id="prev">‹</button><b id="wk">${esc(g.start)} → ${esc(g.end)}</b><button class="btn" id="next">›</button>
      <button class="btn" id="today">This week</button><button class="btn ai" id="ai-entry">✨ AI entry</button></div></div>
  ${g.rows.length ? `<div class="table-wrap views-grid"><table><thead><tr><th>Account</th><th>Niche</th>
    ${g.days.map((d, i) => `<th class="num">${DOW[i]} ${d.slice(8)}</th>`).join('')}<th class="num">Weekly</th><th>Best hook?</th><th>Decision</th></tr></thead><tbody>
    ${g.rows.map(r => `<tr class="band" style="--band:${esc(r.phone_color || 'transparent')}">
      <td><span class="handle">${esc(r.handle)}</span><div class="muted small">${esc(r.phone_label || '')} · ${esc(r.platform)}</div></td>
      <td class="muted">${esc(r.niche)}</td>
      ${r.cells.map(c => `<td class="num"><input inputmode="numeric" data-acc="${r.id}" data-day="${c.day}" value="${c.views == null ? '' : c.views}"
        class="${c.source === 'ai' ? 'ai' : ''}" title="${c.source === 'ai' ? 'Filled by AI' : ''}" aria-label="${esc(r.handle)} ${c.day}"></td>`).join('')}
      <td class="num"><b id="tot-${r.id}">${fmt(r.weekly_total)}</b></td>
      <td id="best-${r.id}">${r.best_hook ? '✓ Yes' : '<span class="muted">—</span>'}</td>
      <td id="dec-${r.id}" title="${esc(r.reason)}">${pill(r.decision)}</td></tr>`).join('')}</tbody>
    <tfoot><tr><td>TOTAL</td><td></td>${g.day_totals.map((t, i) => `<td class="num" id="dt-${i}">${fmt(t)}</td>`).join('')}
      <td class="num" id="grand">${fmt(g.grand_total)}</td><td></td><td class="muted small">avg ${fmt(g.avg_per_account)}</td></tr></tfoot></table></div>
    <p class="muted small">Rules: Scale = ${fmt(g.settings.scale_weekly)}+ weekly views with a strong hook score (${g.settings.scale_vsc}+). Stop = no post above ${fmt(g.settings.stall_views)} views in the last ${g.settings.kill_after_posts} posts. Change them in Settings.</p>`
    : `<div class="empty card"><h2>No accounts yet</h2><p>Views need accounts first.</p>
      <div class="row" style="justify-content:center;gap:8px;margin-top:12px">
        <a class="btn primary" href="#/accounts">Add accounts</a>
        <a class="btn" href="#/dashboard">Get started checklist</a>
      </div></div>`}`;
  const go = n => { state.weekStart = iso(addDays(start, n)); route(); };
  $('#prev').onclick = () => go(-7); $('#next').onclick = () => go(7);
  $('#today').onclick = () => { state.weekStart = iso(mondayOf(nowLocal())); route(); };
  $('#ai-entry').onclick = () => aiEntryModal();

  const patch = g2 => {
    g2.rows.forEach(r => {
      $('#tot-' + r.id).textContent = fmt(r.weekly_total);
      $('#best-' + r.id).innerHTML = r.best_hook ? '✓ Yes' : '<span class="muted">—</span>';
      const dec = $('#dec-' + r.id); dec.innerHTML = pill(r.decision); dec.title = r.reason;
    });
    g2.day_totals.forEach((t, i) => ($('#dt-' + i).textContent = fmt(t)));
    $('#grand').textContent = fmt(g2.grand_total);
  };
  $$('.views-grid input', app).forEach(inp => {
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); });
    inp.addEventListener('change', guard(async () => {
      const raw = inp.value.trim();
      const val = raw === '' ? null : parseCount(raw);
      if (raw !== '' && Number.isNaN(val)) { toast('Enter a number like 1200 or 85k', true); return; }
      await api('PUT', '/api/views', { entries: [{ account_id: +inp.dataset.acc, day: inp.dataset.day, views: val, source: 'manual' }] });
      inp.value = val == null ? '' : val; inp.classList.remove('ai'); inp.title = '';
      patch(await api('GET', `/api/views?start=${state.weekStart}`));
    }));
  });
};

/* ---------------- today: quick entry ---------------- */
const dayLabel = d => {
  if (d === iso(nowLocal())) return 'Today';
  if (d === iso(addDays(nowLocal(), -1))) return 'Yesterday';
  const x = parseISO(d); return `${DOW[(x.getDay() + 6) % 7]} ${d.slice(5)}`;
};
pages.today = async app => {
  const day = state.todayDay || iso(nowLocal());
  const t = await api('GET', `/api/today?day=${day}`);
  const go = d => { state.todayDay = d; route(); };
  const deltaChip = (v, avg) => {
    if (v == null || !avg) return '';
    const pct = Math.round((v - avg) / avg * 100);
    return `<span class="delta ${pct >= 0 ? 'up' : 'down'}">${pct >= 0 ? '▲' : '▼'} ${Math.abs(pct)}% vs 7-day avg</span>`;
  };
  app.innerHTML = `
  <div class="page-head"><div><h1>${esc(dayLabel(day))}</h1><div class="sub">${esc(day)} · type each account's views and press Enter. 85k works.</div></div>
    <div class="row"><button class="btn" id="d-prev">‹</button><button class="btn" id="d-today">Today</button>
      <button class="btn" id="d-next" ${day >= iso(nowLocal()) ? 'disabled' : ''}>›</button>
      <button class="btn ai" id="ai-entry">✨ AI entry</button></div></div>
  ${t.gaps.length ? `<div class="notice">Missing views: ${t.gaps.map(g =>
      `<a href="#/today" data-gap="${esc(g.day)}">${esc(dayLabel(g.day))} (${g.missing})</a>`).join(' · ')}</div>` : ''}
  ${t.total ? `
  <div class="card today-card">
    <div class="row" style="justify-content:space-between"><b id="prog-text"></b><span class="muted small" id="all-done"></span></div>
    <div class="prog"><span id="prog-bar"></span></div>
    <div class="today-list">${t.accounts.map(a => `
      <div class="today-row" style="--band:${esc(a.phone_color || 'transparent')}">
        <div class="who"><span class="handle">${esc(a.handle)}</span>
          <div class="muted small">${esc(a.phone_label || 'No phone')} · ${esc(a.platform)}${a.status === 'warming' ? ' · warming' : ''}</div></div>
        <div class="ctx muted small">${a.prev != null ? `Yesterday ${fmt(a.prev)}` : ''}${a.avg7 != null ? `${a.prev != null ? ' · ' : ''}7-day avg ${fmt(a.avg7)}` : ''}</div>
        <div class="entry"><span id="chip-${a.id}">${deltaChip(a.views, a.avg7)}</span>
          <input inputmode="numeric" data-acc="${a.id}" data-avg="${a.avg7 ?? ''}" value="${a.views == null ? '' : a.views}"
            placeholder="${a.prev != null ? fmt(a.prev) : 'views'}" class="${a.source === 'ai' ? 'ai' : ''}" aria-label="${esc(a.handle)} views on ${esc(day)}">
          <span class="saved" id="ok-${a.id}">${a.views != null ? '✓' : ''}</span></div>
      </div>`).join('')}</div>
  </div>` : `<div class="empty card"><h2>No active accounts</h2><p>Add an account to start tracking views.</p>
      <a class="btn primary" href="#/accounts">Add accounts</a></div>`}
  <div class="card" style="margin-top:16px"><h3 style="margin-top:0">Posts due${t.due.length ? ` (${t.due.length})` : ''}</h3>
    ${t.due.length ? `<ul class="list">${t.due.map(p => `<li class="due-row">
      <span class="pill ${esc(normStatus(p.status))}">${esc(PIPE_LABEL[p.status] || p.status)}</span>
      <div style="flex:1;min-width:0"><b>${esc(p.handle || '-')}</b> <span class="muted">${esc(short(p.hook || p.body || '(no hook yet)', 70))}</span>
        <div class="small ${p.overdue ? 'overdue' : 'muted'}">${p.overdue ? 'Overdue · ' : ''}${esc(p.post_date)}</div></div>
      <button class="btn sm" data-open="${p.id}">Open</button>
      <button class="btn sm primary" data-posted="${p.id}">Mark posted</button></li>`).join('')}</ul>`
      : '<p class="muted">Nothing waiting to be posted. Plan more on <a href="#/pipeline">Posts</a>.</p>'}
  </div>`;

  $('#d-prev').onclick = () => go(iso(addDays(parseISO(day), -1)));
  $('#d-next').onclick = () => go(iso(addDays(parseISO(day), 1)));
  $('#d-today').onclick = () => go(iso(nowLocal()));
  $('#ai-entry').onclick = () => aiEntryModal();
  $$('[data-gap]', app).forEach(a => a.addEventListener('click', e => { e.preventDefault(); go(a.dataset.gap); }));
  $$('[data-open]', app).forEach(b => b.onclick = () => postModal(t.due.find(p => p.id === +b.dataset.open), null, route));
  $$('[data-posted]', app).forEach(b => b.onclick = guard(async () => {
    await api('PATCH', `/api/posts/${b.dataset.posted}/stage?stage=posted`);
    toast('Marked as posted'); route();
  }));

  const inputs = $$('.today-row input', app);
  const progress = () => {
    if (!$('#prog-text')) return;
    const done = inputs.filter(i => i.value.trim() !== '').length;
    $('#prog-text').textContent = `${done} of ${inputs.length} entered`;
    $('#prog-bar').style.width = inputs.length ? `${Math.round(done / inputs.length * 100)}%` : '0';
    $('#all-done').textContent = done === inputs.length ? 'All done for this day 🎉' : '';
  };
  progress();
  inputs.forEach((inp, i) => {
    inp.addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const next = inputs.slice(i + 1).find(x => x.value.trim() === '') || inputs[i + 1];
      if (next) next.focus(); else inp.blur();
    });
    inp.addEventListener('change', guard(async () => {
      const raw = inp.value.trim();
      const val = raw === '' ? null : parseCount(raw);
      if (raw !== '' && Number.isNaN(val)) { toast('Enter a number like 1200 or 85k', true); return; }
      await api('PUT', '/api/views', { entries: [{ account_id: +inp.dataset.acc, day, views: val, source: 'manual' }] });
      inp.value = val == null ? '' : val; inp.classList.remove('ai');
      $('#ok-' + inp.dataset.acc).textContent = val == null ? '' : '✓';
      $('#chip-' + inp.dataset.acc).innerHTML = deltaChip(val, +inp.dataset.avg || 0);
      progress();
    }));
  });
  const first = inputs.find(i => i.value.trim() === '') || inputs[0];
  if (first) first.focus();
};

function aiEntryModal() {
  const m = openModal(`<h2>✨ AI entry</h2>${aiNotice()}
    <p class="muted">Paste text like <code>1a 85k, 1b 17k yesterday</code> or drop analytics screenshots. You review everything before it's saved.</p>
    <div class="fields"><div class="full"><label class="f">Text</label><textarea id="t" placeholder="account_1a Mon 891, Tue 1177 …"></textarea></div>
    <div class="full"><div class="drop" id="drop">Drop screenshots here, click to choose, or paste an image (Ctrl+V)<input type="file" id="f" accept="image/*" multiple hidden></div>
      <div class="thumbs" id="thumbs"></div></div></div>
    <div id="preview"></div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn ai" id="go" ${state.ai.available ? '' : 'disabled'}>Extract</button></div>`, true);
  let files = [];
  const thumbs = () => { $('#thumbs', m).innerHTML = files.map(f => `<img src="${URL.createObjectURL(f)}" alt="screenshot">`).join(''); };
  const add = list => { files = files.concat([...list].filter(f => f.type.startsWith('image/'))).slice(0, 6); thumbs(); };
  const drop = $('#drop', m), fi = $('#f', m);
  drop.onclick = () => fi.click(); fi.onchange = () => add(fi.files);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); add(e.dataTransfer.files); };
  m.addEventListener('paste', e => add([...e.clipboardData.items].filter(i => i.kind === 'file').map(i => i.getAsFile())));

  $('#go', m).onclick = guard(async () => {
    const btn = $('#go', m);
    const fd = new FormData(); fd.append('text', $('#t', m).value); files.forEach(f => fd.append('files', f));
    busy(btn, true, 'Reading…');
    let r; try { r = await api('POST', '/api/ai/extract-views', fd); } finally { busy(btn, false); }
    const warn = [...(r.warnings || []), ...(r.unmatched || []).map(u => `Couldn't place "${u.account}" ${u.day} ${u.views}`)];
    $('#preview', m).innerHTML = `${r.entries.length ? `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th></th><th>Account</th><th>Day</th><th class="num">Views</th><th>Note</th></tr></thead><tbody>
      ${r.entries.map((e, i) => `<tr><td><input type="checkbox" checked data-i="${i}" style="width:auto"></td><td>${esc(e.handle)}</td><td>${esc(e.day)}</td>
        <td class="num"><input data-v="${i}" value="${e.views}" style="width:100px;text-align:right"></td><td class="muted small">${esc(e.note)}</td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted">Nothing could be matched to your accounts.</p>'}
      ${warn.length ? `<div class="notice" style="margin-top:10px">${warn.map(esc).join('<br>')}</div>` : ''}`;
    if (!r.entries.length) return;
    btn.textContent = 'Save selected'; btn.onclick = guard(async () => {
      const entries = r.entries.map((e, i) => ({ e, i })).filter(({ i }) => $(`[data-i="${i}"]`, m).checked)
        .map(({ e, i }) => ({ account_id: e.account_id, day: e.day, views: parseCount($(`[data-v="${i}"]`, m).value), source: 'ai' }));
      if (entries.some(x => Number.isNaN(x.views))) { toast('Fix the highlighted view counts', true); return; }
      await api('PUT', '/api/views', { entries });
      state.weekStart = iso(mondayOf(parseISO(entries[0].day)));
      m.close(); toast(`Saved ${entries.length} entries`); route();
    });
  });
}

/* ---------------- post editor (calendar + hooks) ---------------- */
function vscDecision(t) {
  if (t == null) return '';
  return t >= state.settings.scale_vsc ? 'SCALE' : t >= state.settings.mutate_vsc ? 'MUTATE' : 'KILL';
}

function postModal(post, defaults, onDone) {
  const p = post || { account_id: '', post_date: iso(nowLocal()), status: 'scripted', ai_prompt: '', hook: '', body: '', caption: '', views: 0, comments: 0, intent: false, v: null, s: null, c: null, ...defaults };
  const m = openModal(`<h2>${post ? 'Edit post' : 'New post'}</h2><div class="fields">
    <div><label class="f">Account</label><select id="account">${accountOptions(p.account_id, 'Choose…')}</select></div>
    <div><label class="f">Date</label><input type="date" id="date" value="${esc(p.post_date)}"></div>
    <div><label class="f">Status</label><select id="status">${PIPELINE.map(s => `<option value="${s}" ${normStatus(p.status) === s ? 'selected' : ''}>${PIPE_LABEL[s]}</option>`).join('')}</select></div>
    <div><label class="f">AI prompt / format</label><input id="prompt" value="${esc(p.ai_prompt)}" placeholder="Slideshow 5-pages, urgent tone"></div>
    <div class="full"><label class="f">Hook (overlay 1)</label><input id="hook" value="${esc(p.hook)}"></div>
    <div class="full"><label class="f">Body (overlay 2)</label><input id="body" value="${esc(p.body)}"></div>
    <div class="full"><label class="f">Caption + hashtags</label><textarea id="caption">${esc(p.caption)}</textarea></div>
    <div><label class="f">Views</label><input id="views" inputmode="numeric" value="${p.views || 0}"></div>
    <div><label class="f">Comments</label><input id="comments" inputmode="numeric" value="${p.comments || 0}"></div>
    <div class="full check"><input type="checkbox" id="intent" ${p.intent ? 'checked' : ''}><label for="intent">Intent — comments ask "what's the app?" / "where can I get this?"</label></div>
    <div class="full"><div class="score-box"><label class="f">VSC score (each /10)</label>
      <div class="score-row">${['v', 's', 'c'].map(k => `<div><label class="f">${{ v: 'V · scroll-stop', s: 'S · specificity', c: 'C · conversion' }[k]}</label><input id="${k}" type="number" min="0" max="10" value="${p[k] ?? ''}"></div>`).join('')}
      <div class="total"><span id="tot">–</span><div id="dec"></div></div></div>
      <div class="small" id="blindchip" style="margin-top:6px">${scoreSourceChip(p)}</div>
      <div class="muted small" id="notes" style="margin-top:8px">${esc(p.score_notes)}</div>
      <div id="signals" style="margin-top:12px"></div>
      <div id="vidstatus" class="small" style="margin-top:8px">${p.video_url ? `🎬 <a href="${esc(p.video_url)}" target="_blank" rel="noopener">Video ready</a>` : ''}</div>
      <div id="pubstatus" class="small" style="margin-top:4px">${p.external_post_id ? `📤 Published · ${esc(p.publish_platforms || '')} · id ${esc(p.external_post_id)}` : (p.scheduled_for ? `📅 Scheduled ${esc(String(p.scheduled_for).slice(0,16))}` : '')}</div></div></div></div>
    <div class="actions">${post ? '<button class="btn danger" id="del">Delete</button>' : ''}<span class="spacer"></span>
      ${post && post.id ? `<button class="btn" id="aiblind" ${state.ai.available ? '' : 'disabled'} title="Judge the wording alone, without the post's views or results. This is the score the threshold fit trusts.">Score wording only</button>` : ''}
      <button class="btn ai" id="aiscore" ${state.ai.available ? '' : 'disabled'} title="${state.ai.available ? 'Uses the post\'s measured results as evidence once it has some' : 'AI is off'}">✨ AI score</button>
      ${post && post.id ? `<button class="btn" id="dup" title="Duplicate as new idea">Duplicate</button>
      <button class="btn" id="genvid" title="Generate video from script">🎬 Video</button>
      <button class="btn primary" id="dopub" title="Publish to social platforms">📤 Publish</button>` : ''}
      <button class="btn" data-close>Cancel</button><button class="btn primary" id="save">Save</button></div>`);
  const calc = () => {
    const vals = ['v', 's', 'c'].map(k => $('#' + k, m).value);
    const t = vals.every(x => x !== '') ? vals.reduce((a, b) => a + +b, 0) : null;
    $('#tot', m).textContent = t == null ? '–' : `${t}/30`; $('#dec', m).innerHTML = pill(vscDecision(t));
  };
  ['v', 's', 'c'].forEach(k => $('#' + k, m).addEventListener('input', calc)); calc();

  // what the platform and the audience have shown so far: early velocity + what people asked for
  const signals = async (fresh) => {
    const el = $('#signals', m);
    if (!el || !post || !post.id) return;
    const pf = (fresh && fresh.perf) || post.perf || null;
    const parts = [];
    if (pf && pf.trajectory && pf.trajectory.length) {
      const mx = Math.max(...pf.trajectory.map(t => t[1]), 1);
      parts.push(`<div class="sig-row"><div class="sig-title">Views by day since posting</div>
        <div class="traj">${pf.trajectory.map(t => `<span style="height:${Math.max(6, Math.round(t[1] / mx * 100))}%" title="Day ${t[0]}: ${fmt(t[1])} views"><i>${t[0]}</i></span>`).join('')}</div>
        <div class="small muted">${pf.early_views != null ? `First day: ${fmt(pf.early_views)} views${pf.early_multiple != null ? ` · ${pf.early_multiple}× this account's usual (${fmt(pf.early_baseline)})` : ''}` : ''}${pf.label === 'HOT_START' ? ' · <b>🔥 fast start</b>' : ''}</div></div>`);
    }
    let c = null;
    try { c = await api('GET', `/api/posts/${post.id}/comments?kind=intent&limit=6`); } catch {}
    const read = c && c.summary && c.summary.comments_fetched != null;
    parts.push(`<div class="sig-row"><div class="sig-title">What people asked for</div>
      ${read ? `<div class="small"><b>${(c.summary.buy_comments || 0) + (c.summary.ask_comments || 0)}</b> of ${c.summary.comments_fetched} comments ask to buy it or how/where to get it
        <span class="muted">(${c.summary.buy_comments || 0} want it · ${c.summary.ask_comments || 0} ask · ${c.summary.more_comments || 0} want more)</span></div>
        ${c.comments.length ? `<ul class="sig-comments">${c.comments.map(x => `<li><span class="pill ${x.kind === 'buy' ? 'SCALE' : ''}">${x.kind === 'buy' ? 'wants it' : 'asks'}</span> ${esc(short(x.body, 140))}${x.likes ? ` <span class="muted small">♥ ${fmt(x.likes)}</span>` : ''}</li>`).join('')}</ul>` : '<div class="small muted">No buying or how-to questions in the comments read.</div>'}`
      : '<div class="small muted">Comments have not been read for this post.</div>'}
      <button class="btn sm" id="readc" type="button" ${state.sv && state.sv.configured === false ? 'disabled title="Add a SociaVault key in Settings"' : ''}>${read ? 'Read more comments' : 'Read comments'} <span class="muted">(≈2 credits)</span></button></div>`);
    el.innerHTML = parts.join('');
    const btn = $('#readc', m);
    if (btn) btn.onclick = guard(async () => {
      busy(btn, true, 'Reading…');
      try { await api('POST', `/api/posts/${post.id}/comments/fetch?pages=2`); } finally { /* re-render replaces the button */ }
      await signals(fresh);
      toast('Comments read');
    });
  };
  signals();

  const body = () => {
    const num = id => { const n = parseCount($('#' + id, m).value || '0'); return Number.isNaN(n) ? 0 : n; };
    const sc = k => { const v = $('#' + k, m).value; return v === '' ? null : Math.max(0, Math.min(10, +v)); };
    return { account_id: $('#account', m).value ? +$('#account', m).value : null, post_date: $('#date', m).value, status: $('#status', m).value,
      ai_prompt: $('#prompt', m).value, hook: $('#hook', m).value, body: $('#body', m).value, caption: $('#caption', m).value,
      views: num('views'), comments: num('comments'), intent: $('#intent', m).checked, v: sc('v'), s: sc('s'), c: sc('c') };
  };
  const save = async () => {
    const b = body();
    if (!b.post_date) throw new Error('Pick a date');
    if (post) { Object.assign(post, await api('PATCH', `/api/posts/${post.id}`, b)); }
    else { post = await api('POST', '/api/posts', b); }
    return post;
  };
  $('#save', m).onclick = guard(async () => { await save(); m.close(); toast('Saved'); onDone && onDone(); });
  const runScore = (id, blind) => guard(async () => {
    const btn = $('#' + id, m);
    if (!$('#hook', m).value.trim()) throw new Error('Write a hook first');
    busy(btn, true, 'Scoring…');
    try {
      await save(); const r = await api('POST', `/api/ai/score/${post.id}${blind ? '?blind=true' : ''}`); post = r;
      ['v', 's', 'c'].forEach(k => ($('#' + k, m).value = r[k])); calc();
      $('#notes', m).textContent = r.score_notes;
      const chip = $('#blindchip', m); if (chip) chip.innerHTML = scoreSourceChip(r);
      toast(blind ? 'Scored on the wording alone' : 'Scored by AI');
    } finally { busy(btn, false); }
  });
  $('#aiscore', m).onclick = runScore('aiscore', false);
  if ($('#aiblind', m)) $('#aiblind', m).onclick = runScore('aiblind', true);
  
  if ($('#dup', m)) $('#dup', m).onclick = guard(async () => {
    const r = await api('POST', `/api/posts/${post.id}/duplicate`);
    toast('Duplicated as idea');
    m.close();
    postModal(r, null, onDone || route);
  });
  if ($('#genvid', m)) $('#genvid', m).onclick = guard(async () => {
    const btn = $('#genvid', m);
    await save();
    busy(btn, true, 'Starting…');
    try {
      let job = await api('POST', `/api/posts/${post.id}/video`);
      toast(job.status === 'done' ? 'Video ready' : 'Video job ' + job.status);
      const tick = async () => {
        job = await api('GET', `/api/posts/${post.id}/video`);
        const el = $('#vidstatus', m);
        if (!el) return;
        if (job.status === 'done' && job.result_url) {
          el.innerHTML = `🎬 <a href="${esc(job.result_url)}" target="_blank" rel="noopener">Video ready</a>`;
          post.video_url = job.result_url;
          toast('Video ready');
          return;
        }
        if (job.status === 'failed') {
          el.textContent = 'Video failed: ' + (job.error || '');
          toast(job.error || 'Video failed');
          return;
        }
        el.textContent = '🎬 Rendering… (' + job.status + ')';
        setTimeout(tick, 4000);
      };
      if (job.status === 'done' && job.result_url) {
        $('#vidstatus', m).innerHTML = `🎬 <a href="${esc(job.result_url)}" target="_blank" rel="noopener">Video ready</a>`;
      } else if (job.status !== 'failed') {
        $('#vidstatus', m).textContent = '🎬 Rendering…';
        setTimeout(tick, 3000);
      } else {
        $('#vidstatus', m).textContent = 'Video failed: ' + (job.error || '');
      }
    } finally { busy(btn, false); }
  });

  if ($('#dopub', m)) $('#dopub', m).onclick = guard(async () => {
    await save();
    const platforms = (state.publish && state.publish.config && state.publish.config.default_platforms) || ['tiktok'];
    const platStr = Array.isArray(platforms) ? platforms.join(', ') : String(platforms);
    // default: tomorrow 9:00 local, or empty for "now"
    const def = new Date(Date.now() + 864e5);
    def.setMinutes(0, 0, 0);
    const pad = n => String(n).padStart(2, '0');
    const localVal = `${def.getFullYear()}-${pad(def.getMonth()+1)}-${pad(def.getDate())}T${pad(def.getHours())}:${pad(def.getMinutes())}`;
    const sm = openModal(`<h2>Publish / schedule</h2>
      <p class="muted">Platforms: <b>${esc(platStr)}</b>. Caption and video URL are sent to your publish provider.</p>
      <div class="fields">
        <div class="full"><label class="f"><input type="radio" name="when" value="now" checked> Publish now</label></div>
        <div class="full"><label class="f"><input type="radio" name="when" value="later"> Schedule for</label>
          <input type="datetime-local" id="sched" value="${localVal}" style="margin-top:6px"></div>
      </div>
      <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="pubgo">Go</button></div>`, true);
    $('#pubgo', sm).onclick = guard(async () => {
      const later = $$('input[name=when]', sm).find(r => r.checked)?.value === 'later';
      let schedule_at = null;
      if (later) {
        const v = $('#sched', sm).value;
        if (!v) throw new Error('Pick a date and time');
        schedule_at = new Date(v).toISOString();
        if (new Date(schedule_at) <= new Date()) throw new Error('Schedule time must be in the future');
      }
      const btn = $('#pubgo', sm);
      busy(btn, true, later ? 'Scheduling…' : 'Publishing…');
      try {
        const body = { platforms: Array.isArray(platforms) ? platforms : undefined };
        if (schedule_at) body.schedule_at = schedule_at;
        const job = await api('POST', `/api/posts/${post.id}/publish`, body);
        sm.close();
        const el = $('#pubstatus', m);
        if (job.status === 'queued') {
          if (el) el.textContent = `📅 Scheduled ${esc((job.scheduled_for || schedule_at || '').toString().slice(0, 16))}`;
          toast('Scheduled — will publish automatically');
        } else if (job.status === 'done' || job.status === 'scheduled') {
          if (el) el.textContent = `📤 ${job.status} · id ${job.external_id || ''}`;
          const st = $('#status', m); if (st) st.value = 'posted';
          post.status = 'posted';
          post.external_post_id = job.external_id || '';
          toast(job.status === 'scheduled' ? 'Scheduled at provider' : 'Published');
        } else {
          toast(job.error || 'Publish failed', true);
        }
      } finally { busy(btn, false); }
    });
  });

if (post) $('#del', m).onclick = guard(async () => {
    if (!confirm('Delete this post?')) return;
    const snapshot = {
      account_id: post.account_id, post_date: post.post_date, status: post.status || 'idea',
      ai_prompt: post.ai_prompt || '', hook: post.hook || '', body: post.body || '', caption: post.caption || '',
      views: post.views || 0, comments: post.comments || 0, intent: !!post.intent,
      v: post.v, s: post.s, c: post.c,
    };
    await api('DELETE', `/api/posts/${post.id}`);
    m.close();
    toast('Post deleted');
    const t = $('#toast');
    if (t) {
      const undo = document.createElement('button');
      undo.className = 'btn sm';
      undo.textContent = 'Undo';
      undo.style.marginLeft = '8px';
      t.appendChild(undo);
      const timer = setTimeout(() => undo.remove(), 8000);
      undo.onclick = guard(async () => {
        clearTimeout(timer);
        undo.remove();
        await api('POST', '/api/posts', snapshot);
        toast('Post restored');
        if (onDone) onDone(); else route();
      });
    }
    if (onDone) onDone();
  });
  return m;
}



/* ---------------- scheduled publishes ---------------- */
pages.scheduled = async app => {
  const [rows, attention] = await Promise.all([
    api('GET', '/api/publish/scheduled'), api('GET', '/api/publish/attention').catch(() => [])]);
  app.innerHTML = `${attention.length ? `<div class="card attention"><h3 style="margin-top:0">Needs your attention (${attention.length})</h3>
    <p class="muted small">These were sent to your provider when the server stopped, so HookTrack can't tell whether they went live.
      Check the account, then tell it what happened. It will not retry on its own.</p>
    ${attention.map(a => `<div class="attn-row"><div style="flex:1;min-width:0"><b>${esc(a.handle || '-')}</b>
        <span class="muted">${esc(short(a.hook || a.caption || '', 70))}</span>
        <div class="small muted">Sent ${esc((a.dispatched_at || '').slice(0, 16).replace('T', ' '))} UTC</div></div>
      <button class="btn sm" data-resolve="${a.id}" data-act="not_posted">It wasn't posted</button>
      <button class="btn sm primary" data-resolve="${a.id}" data-act="posted">It was posted</button></div>`).join('')}</div>` : ''}
  <div class="page-head"><div><h1>Scheduled</h1>
    <div class="sub">Posts queued to publish later. HookTrack sends them to your provider when due (every ~45s).</div></div>
    <a class="btn" href="#/pipeline">Pipeline</a></div>
    ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>Account</th><th>Hook</th><th>Status</th><th>Provider</th><th></th></tr></thead><tbody>
      ${rows.map(r => `<tr>
        <td>${esc((r.scheduled_for || '').toString().slice(0, 16).replace('T',' '))}</td>
        <td>${esc(r.handle || '—')}</td>
        <td class="wrap">${esc(short(r.hook || r.caption || '', 50))}</td>
        <td>${pill(r.status)}</td>
        <td class="muted">${esc(r.provider || '')}</td>
        <td><button class="btn sm danger" data-cancel="${r.id}">Cancel</button></td>
      </tr>`).join('')}</tbody></table></div>`
      : '<div class="empty card"><h2>Nothing scheduled</h2><p>Use <b>📤 Publish</b> on a post and choose <b>Schedule for</b>.</p></div>'}`;
  $$('[data-resolve]', app).forEach(b => b.onclick = guard(async () => {
    const posted = b.dataset.act === 'posted';
    if (!confirm(posted ? 'Mark this post as published?' : 'Confirm it was NOT posted? You can then publish it again.')) return;
    await api('POST', `/api/publish/jobs/${b.dataset.resolve}/resolve`, { action: b.dataset.act });
    toast(posted ? 'Marked as posted' : 'Marked as not posted'); route();
  }));
  $$('[data-cancel]', app).forEach(b => b.onclick = guard(async () => {
    if (!confirm('Cancel this scheduled publish?')) return;
    await api('DELETE', `/api/publish/scheduled/${b.dataset.cancel}`);
    toast('Cancelled'); route();
  }));
};

/* ---------------- pipeline kanban ---------------- */
pages.pipeline = async app => {
  const view = state.postsView === 'calendar' ? 'calendar' : 'board';
  const accountFilter = state.pipeAccount || state.calAccount || '';

  const toggle = `<div class="seg" role="tablist">
      <button type="button" class="seg-btn ${view === 'board' ? 'on' : ''}" data-view="board">Board</button>
      <button type="button" class="seg-btn ${view === 'calendar' ? 'on' : ''}" data-view="calendar">Calendar</button>
    </div>`;

  if (view === 'calendar') {
    await renderPostsCalendar(app, { accountFilter, toggle });
    return;
  }

  const q = accountFilter ? `?account_id=${accountFilter}` : '';
  const board = await api('GET', '/api/pipeline' + q);
  const stages = board.stages || PIPELINE;
  app.innerHTML = `<div class="page-head"><div><h1>Posts</h1>
    <div class="sub">Drag to move · <kbd>Shift</kbd>+click to multi-select · bulk-move selected. Same posts as the calendar.</div></div>
    <div class="row">
      ${toggle}
      <select id="pa" style="width:auto">${accountOptions(accountFilter, 'All accounts')}</select>
      <select id="bulk-stage" style="width:auto">
        <option value="">Move selected to…</option>
        ${stages.map(s => `<option value="${s}">${PIPE_LABEL[s] || s}</option>`).join('')}
      </select>
      <button class="btn primary" id="new">+ Post</button>
    </div></div>
    <div id="bulk-bar" class="bulk-bar" hidden><span id="bulk-count">0 selected</span>
      <button type="button" class="btn sm" id="bulk-clear">Clear selection</button></div>
    <div class="pipeline" id="board">
      ${stages.map(s => {
        const cards = board.columns[s] || [];
        return `<div class="pipe-col" data-stage="${s}">
          <div class="pipe-col-h"><span>${PIPE_LABEL[s] || s}</span><span class="n">${cards.length}</span></div>
          <div class="pipe-cards" data-stage="${s}">
            ${cards.map(p => `<div class="pipe-card" draggable="true" data-id="${p.id}">
              <div class="h">${esc(short(p.hook || p.body || 'Untitled', 72))}</div>
              <div class="meta">
                <span class="band-dot" style="background:${esc(p.phone_color || 'var(--accent)')}"></span>
                <span>${esc(p.handle || '—')}</span>
                <span>${esc(p.post_date || '')}</span>
                ${p.vsc != null ? `<span>${p.vsc}/30</span>` : ''}
                ${p.views ? `<span>${fmt(p.views)} views</span>` : ''}
                ${p.video_url ? '<span title="Has video">🎬</span>' : ''}
              </div>
            </div>`).join('') || '<div class="muted small" style="padding:8px">Drop cards here</div>'}
          </div>
        </div>`;
      }).join('')}
    </div>`;

  $$('[data-view]', app).forEach(b => b.onclick = () => { state.postsView = b.dataset.view; route(); });
  $('#pa').onchange = e => { state.pipeAccount = e.target.value; state.calAccount = e.target.value; route(); };
  $('#new').onclick = () => postModal(null, { status: 'idea', account_id: state.pipeAccount || '' }, route);

  let dragId = null;
  const selected = new Set();
  const syncBulk = () => {
    const n = selected.size;
    const bar = $('#bulk-bar');
    if (bar) { bar.hidden = n === 0; }
    const cnt = $('#bulk-count');
    if (cnt) cnt.textContent = n ? `${n} selected` : '0 selected';
    $$('.pipe-card', app).forEach(c => c.classList.toggle('selected', selected.has(c.dataset.id)));
  };
  $$('.pipe-card', app).forEach(card => {
    card.addEventListener('dragstart', e => {
      dragId = card.dataset.id;
      e.dataTransfer.setData('text/plain', dragId);
      e.dataTransfer.effectAllowed = 'move';
      card.style.opacity = '0.5';
    });
    card.addEventListener('dragend', () => { card.style.opacity = ''; });
    card.addEventListener('click', e => {
      if (e.defaultPrevented) return;
      if (e.shiftKey) {
        e.preventDefault();
        const id = card.dataset.id;
        if (selected.has(id)) selected.delete(id); else selected.add(id);
        syncBulk();
        return;
      }
      const all = Object.values(board.columns).flat();
      const post = all.find(p => p.id === +card.dataset.id);
      if (post) postModal(post, null, route);
    });
  });
  const bulkStage = $('#bulk-stage');
  if (bulkStage) bulkStage.onchange = guard(async () => {
    const stage = bulkStage.value;
    if (!stage || !selected.size) { bulkStage.value = ''; return; }
    const ids = [...selected].map(Number);
    const r = await api('PATCH', '/api/posts/bulk-stage', { post_ids: ids, stage });
    toast(`Moved ${r.updated} to ${PIPE_LABEL[stage] || stage}`);
    route();
  });
  const bulkClear = $('#bulk-clear');
  if (bulkClear) bulkClear.onclick = () => { selected.clear(); syncBulk(); };
  $$('.pipe-col', app).forEach(col => {
    col.addEventListener('dragover', e => { e.preventDefault(); col.classList.add('drag-over'); });
    col.addEventListener('dragleave', () => col.classList.remove('drag-over'));
    col.addEventListener('drop', guard(async e => {
      e.preventDefault();
      col.classList.remove('drag-over');
      const id = e.dataTransfer.getData('text/plain') || dragId;
      const stage = col.dataset.stage;
      if (!id || !stage) return;
      await api('PATCH', `/api/posts/${id}/stage?stage=${encodeURIComponent(stage)}`);
      toast(`Moved to ${PIPE_LABEL[stage] || stage}`);
      route();
    }));
  });
};

async function renderPostsCalendar(app, { accountFilter, toggle }) {
  if (!state.calMonth) { const n0 = nowLocal(); state.calMonth = iso(new Date(n0.getFullYear(), n0.getMonth(), 1)); }
  const first = parseISO(state.calMonth);
  const gridStart = mondayOf(first);
  const gridEnd = addDays(gridStart, 41);
  const q = `start=${iso(gridStart)}&end=${iso(gridEnd)}` + (accountFilter ? `&account_id=${accountFilter}` : '');
  const posts = await api('GET', `/api/posts?${q}`);
  const by = {}; posts.forEach(p => (by[p.post_date] = by[p.post_date] || []).push(p));
  const today = iso(nowLocal());
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = addDays(gridStart, i), k = iso(d), list = (by[k] || []).slice().reverse();
    cells.push(`<div class="day ${d.getMonth() !== first.getMonth() ? 'out' : ''} ${k === today ? 'today' : ''}" data-day="${k}">
      <div class="dn"><span>${d.getDate()}</span>${list.length ? `<span>${list.length}</span>` : ''}</div>
      ${list.slice(0, 3).map(p => `<button class="chip ${normStatus(p.status)}" data-id="${p.id}" style="--c:${esc(p.phone_color || 'var(--accent)')}" title="${esc((p.handle || '') + ': ' + p.hook)}">${esc(short(p.hook, 24) || p.handle || '…')}</button>`).join('')}
      ${list.length > 3 ? `<div class="more">+${list.length - 3} more</div>` : ''}</div>`);
  }
  app.innerHTML = `<div class="page-head"><div><h1>Posts</h1>
    <div class="sub">Plan by day. Same posts as the board — switch views anytime.</div></div>
    <div class="row">
      ${toggle}
      <select id="cf" style="width:auto">${accountOptions(accountFilter, 'All accounts')}</select>
      <button class="btn" id="prev">‹</button><b style="min-width:130px;text-align:center">${first.toLocaleString('default', { month: 'long', year: 'numeric' })}</b>
      <button class="btn" id="next">›</button><button class="btn" id="today">Today</button>
      <button class="btn ai" id="plan">✨ AI plan week</button>
    </div></div>
    <div class="cal">${DOW.map(d => `<div class="dow">${d}</div>`).join('')}${cells.join('')}</div>`;

  $$('[data-view]', app).forEach(b => b.onclick = () => { state.postsView = b.dataset.view; route(); });
  const shift = n => { state.calMonth = iso(new Date(first.getFullYear(), first.getMonth() + n, 1)); route(); };
  $('#prev').onclick = () => shift(-1); $('#next').onclick = () => shift(1);
  $('#today').onclick = () => { const n0 = nowLocal(); state.calMonth = iso(new Date(n0.getFullYear(), n0.getMonth(), 1)); route(); };
  $('#cf').onchange = e => { state.calAccount = e.target.value; state.pipeAccount = e.target.value; route(); };
  $('#plan').onclick = () => planModal();
  $$('.day', app).forEach(el => el.addEventListener('click', e => {
    const chip = e.target.closest('.chip');
    if (chip) { postModal(posts.find(p => p.id === +chip.dataset.id), null, route); return; }
    postModal(null, { post_date: el.dataset.day, status: 'scripted', account_id: state.calAccount || state.pipeAccount || '' }, route);
  }));
}


pages.calendar = async app => {
  // Calendar is a secondary view of Posts
  state.postsView = 'calendar';
  location.hash = '#/pipeline';
};


/* ---------------- inspiration library ---------------- */
pages.inspiration = async app => {
  const rows = await api('GET', '/api/inspiration');
  app.innerHTML = `<div class="page-head"><div><h1>Inspiration</h1>
    <div class="sub">Save competitor hooks (URL, screenshot, notes). Items marked for AI are used as few-shot examples when generating hooks.</div></div>
    <button class="btn primary" id="add">+ Add</button></div>
    ${rows.length ? `<div class="insp-grid">${rows.map(r => `
      <div class="insp-card" data-id="${r.id}">
        ${r.image_url ? `<img src="${esc(r.image_url)}" alt="">` : ''}
        <div class="body">
          <div class="row" style="gap:6px;flex-wrap:wrap">
            ${r.platform ? `<span class="pill">${esc(r.platform)}</span>` : ''}
            ${r.use_in_ai ? '<span class="pill SCALE" title="Used in AI generation">AI</span>' : '<span class="pill">off</span>'}
          </div>
          <div class="hook">${esc(r.hook_text || r.title || 'Untitled')}</div>
          ${r.title && r.hook_text ? `<div class="muted small">${esc(r.title)}</div>` : ''}
          ${r.notes ? `<div class="notes">${esc(short(r.notes, 140))}</div>` : ''}
          ${r.source_url ? `<a class="small" href="${esc(r.source_url)}" target="_blank" rel="noopener">Source ↗</a>` : ''}
          <div class="actions row">
            <button class="btn sm" data-toggle="${r.id}" data-on="${r.use_in_ai ? 1 : 0}">${r.use_in_ai ? 'Exclude AI' : 'Use in AI'}</button>
            <button class="btn sm danger" data-del="${r.id}">Delete</button>
          </div>
        </div>
      </div>`).join('')}</div>`
      : `<div class="empty card"><h2>No inspiration yet</h2><p>Paste a competitor URL, note the hook text, or drop a screenshot. AI ideas will pull these in as style references.</p></div>`}`;

  $('#add').onclick = () => inspirationModal(route);
  $$('[data-del]', app).forEach(b => b.onclick = guard(async () => {
    if (!confirm('Delete this inspiration item?')) return;
    await api('DELETE', `/api/inspiration/${b.dataset.del}`);
    toast('Deleted'); route();
  }));
  $$('[data-toggle]', app).forEach(b => b.onclick = guard(async () => {
    const on = b.dataset.on === '1';
    await api('PATCH', `/api/inspiration/${b.dataset.toggle}`, { use_in_ai: !on });
    route();
  }));
};

function inspirationModal(onDone) {
  const m = openModal(`<h2>Add inspiration</h2>
    <p class="muted">Competitor or reference hook. Text is what the AI uses; screenshot is for you.</p>
    <div class="fields">
      <div class="full"><label class="f">Hook text (on-screen / spoken)</label><input id="hook" maxlength="1000" placeholder="Stop scrolling if you still text your ex at 2am"></div>
      <div><label class="f">Title / label</label><input id="title" maxlength="200" placeholder="Optional"></div>
      <div><label class="f">Platform</label><input id="plat" maxlength="40" placeholder="TikTok"></div>
      <div class="full"><label class="f">Source URL</label><input id="url" maxlength="500" placeholder="https://…"></div>
      <div class="full"><label class="f">Notes</label><textarea id="notes" rows="2" maxlength="2000" placeholder="Why it works, niche, structure…"></textarea></div>
      <div class="full"><label class="f">Screenshot</label><input type="file" id="file" accept="image/png,image/jpeg,image/webp,image/gif"></div>
      <div class="full"><label class="f" style="display:flex;align-items:center;gap:8px"><input type="checkbox" id="ai" checked> Use in AI hook generation</label></div>
    </div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="save">Save</button></div>`, true);
  $('#save', m).onclick = guard(async () => {
    const fd = new FormData();
    fd.append('hook_text', $('#hook', m).value);
    fd.append('title', $('#title', m).value);
    fd.append('platform', $('#plat', m).value);
    fd.append('source_url', $('#url', m).value);
    fd.append('notes', $('#notes', m).value);
    fd.append('use_in_ai', $('#ai', m).checked ? 'true' : 'false');
    const f = $('#file', m).files[0];
    if (f) fd.append('file', f);
    const res = await fetch('/api/inspiration', { method: 'POST', body: fd, credentials: 'same-origin' });
    if (!res.ok) {
      let msg = 'Save failed';
      try { msg = (await res.json()).detail || msg; } catch {}
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    toast('Saved to library');
    m.close();
    if (onDone) onDone();
  });
}

function planModal() {
  const m = openModal(`<h2>✨ AI plan week</h2>${aiNotice()}
    <p class="muted">Drafts the next 7 days of posts for every active account using your winning hooks. You choose which to add.</p>
    <div class="fields"><div><label class="f">Start date</label><input type="date" id="ps" value="${iso(nowLocal())}"></div></div>
    <div id="out"></div><div class="actions"><button class="btn" data-close>Close</button><button class="btn ai" id="go" ${state.ai.available ? '' : 'disabled'}>Generate plan</button></div>`, true);
  $('#go', m).onclick = guard(async () => {
    const btn = $('#go', m); busy(btn, true, 'Planning…');
    let r; try { r = await api('POST', '/api/ai/plan-week', { start: $('#ps', m).value }); } finally { busy(btn, false); }
    $('#out', m).innerHTML = r.posts.length ? `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th></th><th>Day</th><th>Account</th><th>Hook</th><th>Format</th></tr></thead><tbody>
      ${r.posts.map((p, i) => `<tr><td><input type="checkbox" checked data-i="${i}" style="width:auto"></td><td>${esc(p.post_date)}</td><td>${esc(p.handle)}</td>
        <td class="wrap">${esc(p.hook)}</td><td class="wrap muted">${esc(p.ai_prompt)}</td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted">The model returned no usable posts. Try again.</p>';
    if (!r.posts.length) return;
    btn.textContent = 'Add selected to calendar';
    btn.onclick = guard(async () => {
      const chosen = r.posts.filter((_, i) => $(`[data-i="${i}"]`, m).checked);
      if (!chosen.length) { toast('Tick at least one post', true); return; }
      await api('POST', '/api/posts/bulk', { posts: chosen });
      m.close(); toast(`Added ${chosen.length} posts`); state.calMonth = chosen[0].post_date.slice(0, 8) + '01'; route();
    });
  });
}

/* ---------------- hooks ---------------- */
pages.hooks = async app => {
  const q = [state.hookStatus && `status=${state.hookStatus}`, state.hookAccount && `account_id=${state.hookAccount}`].filter(Boolean).join('&');
  let posts = await api('GET', `/api/posts?${q}`);
  const hot = (await api('GET', '/api/velocity?days=3&limit=6').catch(() => [])).filter(p => p.perf && p.perf.label === 'HOT_START');
  const winnerCount = posts.filter(p => p.perf && p.perf.label === 'WINNER').length;
  if (state.hookWinners) posts = posts.filter(p => p.perf && p.perf.label === 'WINNER');
  const hasSS = posts.some(p => p.saves != null || p.shares != null);      // only show columns that have data
  const hasLikes = posts.some(p => p.likes != null);
  app.innerHTML = `<div class="page-head"><div><h1>Hooks</h1><div class="sub">Log every post and score the hook (out of 30). Rules: Scale ${state.settings.scale_vsc}+, Tweak ${state.settings.mutate_vsc}–${state.settings.scale_vsc - 1}, Stop below.</div></div>
    <div class="row"><select id="fs" style="width:auto"><option value="">All statuses</option>${PIPELINE.map(s => `<option value="${s}" ${state.hookStatus === s ? 'selected' : ''}>${PIPE_LABEL[s]}</option>`).join('')}</select>
      <button class="btn" id="abtest" title="Same body, different hooks">A/B hooks</button>
      <button class="btn" id="simdec" title="What if thresholds changed?">Simulate thresholds</button>
      <select id="fa" style="width:auto">${accountOptions(state.hookAccount, 'All accounts')}</select>
      <label class="check small" title="Posts that beat their own account's normal"><input type="checkbox" id="winonly" ${state.hookWinners ? 'checked' : ''}> Winners only (${winnerCount})</label>
      <button class="btn ai" id="ideas">✨ AI ideas</button><button class="btn primary" id="new">+ New post</button></div></div>
    ${hot.length ? `<div class="card hot-card"><h3 style="margin:0 0 4px">🔥 Hot starts <span class="muted small" style="font-weight:400">posted in the last 2 days, already far above that account's usual first day</span></h3>
      <ul class="list">${hot.map(h => `<li><div style="flex:1;min-width:0"><b>${esc(h.handle || '-')}</b> <span class="muted">${esc(short(h.hook || '(no hook)', 70))}</span>
        <div class="small muted">${fmt(h.perf.early_views)} views on day 1 · ${h.perf.early_multiple}× usual (${fmt(h.perf.early_baseline)})</div></div>
        <button class="btn sm" data-hot="${h.id}">Open</button></li>`).join('')}</ul>
      <p class="small muted" style="margin:6px 0 0">A strong first day is the earliest sign of a winner. Make two or three variations of it now, while it is still fresh.</p></div>` : ''}
    ${posts.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Account</th><th>Hook</th><th>Status</th><th class="num">Views</th><th class="num" title="Views vs this account's normal (median of its recent posts)">× normal</th>${hasLikes ? '<th class="num">Likes</th>' : ''}<th class="num">Comments</th>${hasSS ? '<th class="num" title="Saves">Saves</th><th class="num">Shares</th>' : ''}<th class="num" title="Saves + shares + comments per 1,000 views">Intent /1k</th><th class="num" title="Comments asking to buy it, or how/where to get it, out of the comments read">Asking</th><th>Intent</th><th class="num">Hook score</th><th>Decision</th><th></th></tr></thead><tbody>
    ${posts.map(p => `<tr class="band" style="--band:${esc(p.phone_color || 'transparent')};cursor:pointer" data-id="${p.id}"><td>${esc(p.post_date)}</td><td class="handle">${esc(p.handle || '—')}</td>
      <td class="wrap">${p.variant_label ? `<span class="badge" title="A/B variant">${esc(p.variant_label)}</span> ` : ''}${esc(p.hook || '—')}<div class="muted small">${esc(short(p.ai_prompt, 60))}</div></td><td>${pill(p.status)}</td>
      <td class="num">${fmt(p.views)}</td><td class="num">${winnerChip(p.perf)}</td>${hasLikes ? `<td class="num">${p.likes == null ? '<span class="muted">-</span>' : fmt(p.likes)}</td>` : ''}<td class="num">${fmt(p.comments)}</td>${hasSS ? `<td class="num">${p.saves == null ? '<span class="muted">-</span>' : fmt(p.saves)}</td><td class="num">${p.shares == null ? '<span class="muted">-</span>' : fmt(p.shares)}</td>` : ''}<td class="num">${p.perf && p.perf.intent_per_1k != null ? p.perf.intent_per_1k : '<span class="muted">-</span>'}</td><td class="num">${p.perf && p.perf.comments_read ? `<span title="${p.perf.buy_comments} want to buy, ${p.perf.ask_comments} ask how/where; ${p.perf.comments_read} comments read">${p.perf.intent_comments}<span class="muted"> / ${p.perf.comments_read}</span></span>` : '<span class="muted" title="Open the post and read its comments">-</span>'}</td><td>${p.intent ? '✓' : ''}</td>
      <td class="num">${p.vsc == null ? '<span class="muted">—</span>' : p.vsc + '/30'}${p.score_by === 'ai' ? ` <span title="${p.score_blind === true ? 'AI score on the wording alone' : p.score_blind === false ? 'AI score that saw the results' : 'AI score'}">${p.score_blind === false ? '✨*' : '✨'}</span>` : ''}${p.score_confidence != null ? ` <span class="muted small" title="Confidence">${Math.round(p.score_confidence*100)}%</span>` : ''}</td><td title="${esc(p.decision_basis || '')}">${pill(p.decision)}${p.decision ? `<div class="muted small">${decisionSource(p)}</div>` : ''}</td>
      <td><button class="btn sm" data-score="${p.id}" ${state.ai.available && p.hook ? '' : 'disabled'} title="AI score">✨</button></td></tr>`).join('')}</tbody></table></div>`
      : `<div class="empty card"><h2>No posts yet</h2><p>Log a post and score the hook (out of 30) to unlock Scale / Tweak / Stop.</p>
      <div class="row" style="justify-content:center;gap:8px;margin-top:12px">
        <button class="btn primary" id="empty-new-post">+ New post</button>
        <a class="btn" href="#/dashboard">Checklist</a>
      </div></div>`}`;
  const en = $('#empty-new-post');
  if (en) en.onclick = () => postModal(null, { status: 'scripted', account_id: state.hookAccount || '' }, route);
  $('#fs').onchange = e => { state.hookStatus = e.target.value; route(); };
  $('#fa').onchange = e => { state.hookAccount = e.target.value; route(); };
  $$('[data-hot]', app).forEach(b => (b.onclick = () => postModal(hot.find(h => h.id === +b.dataset.hot), null, route)));
  const wo = $('#winonly'); if (wo) wo.onchange = e => { state.hookWinners = e.target.checked; route(); };
  $('#new').onclick = () => postModal(null, { status: 'posted', account_id: state.hookAccount || '' }, route);
  $('#ideas').onclick = () => ideasModal();
  $('#abtest').onclick = () => experimentModal();
  $('#simdec').onclick = () => simulateThresholdsModal();
  $$('tr[data-id]', app).forEach(tr => tr.addEventListener('click', e => {
    if (e.target.closest('[data-score]')) return;
    postModal(posts.find(p => p.id === +tr.dataset.id), null, route);
  }));
  $$('[data-score]', app).forEach(b => b.addEventListener('click', guard(async () => {
    busy(b, true); try { await api('POST', `/api/ai/score/${b.dataset.score}`); } finally { busy(b, false); }
    toast('Scored by AI'); route();
  })));
};


function experimentModal() {
  const m = openModal(`<h2>A/B hook experiment</h2>
    <p class="muted">Same body, different first 3 seconds (hooks). Creates planned posts you can post and compare.</p>
    <div class="fields">
      <div><label class="f">Account</label><select id="acc">${accountOptions(state.hookAccount, 'Choose…')}</select></div>
      <div><label class="f">Name</label><input id="en" placeholder="Hook openers test" maxlength="120"></div>
      <div class="full"><label class="f">Shared body (overlay 2)</label><textarea id="body" rows="2" maxlength="1000"></textarea></div>
      <div class="full"><label class="f">Variant A hook</label><input id="h0" maxlength="500" placeholder="First 3 seconds — version A"></div>
      <div class="full"><label class="f">Variant B hook</label><input id="h1" maxlength="500" placeholder="First 3 seconds — version B"></div>
      <div class="full"><label class="f">Variant C hook (optional)</label><input id="h2" maxlength="500"></div>
    </div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="go">Create experiment</button></div>`, true);
  $('#go', m).onclick = guard(async () => {
    const acc = $('#acc', m).value; if (!acc) throw new Error('Choose an account');
    const variants = [];
    [['A', 'h0'], ['B', 'h1'], ['C', 'h2']].forEach(([label, id]) => {
      const hook = $(`#${id}`, m).value.trim();
      if (hook) variants.push({ label, hook });
    });
    if (variants.length < 2) throw new Error('Need at least two hooks');
    const r = await api('POST', '/api/experiments', {
      account_id: +acc, name: $('#en', m).value, body: $('#body', m).value, variants,
    });
    toast(`Created ${r.variant_ids.length} variants`);
    m.close(); route();
  });
}

function simulateThresholdsModal() {
  const S = state.settings;
  const m = openModal(`<h2>Simulate thresholds</h2>
    <p class="muted">Current rules: Scale at ${S.scale_vsc}+, Tweak at ${S.mutate_vsc}–${S.scale_vsc - 1}. Change the numbers to see which scored hooks would flip.</p>
    <div class="fields">
      <div><label class="f">Scale when score ≥</label><input type="number" id="sc" min="1" max="30" value="${S.scale_vsc}"></div>
      <div><label class="f">Tweak when score ≥</label><input type="number" id="mu" min="0" max="29" value="${S.mutate_vsc}"></div>
    </div>
    <div id="sugg" class="card" style="margin-top:12px"><span class="muted small">Checking how your hook scores relate to your results…</span></div>
    <div id="out" style="margin-top:12px"></div>
    <div class="actions"><button class="btn" data-close>Close</button><button class="btn primary" id="go">Preview</button></div>`, true);
  const loadSugg = () => api('GET', '/api/decisions/suggest').then(r => {
    const row = (name, k) => { const x = r.by_result[k]; return x.n ? `<tr><td>${name}</td><td class="num">${x.n}</td><td class="num">${x.median}</td><td class="num">${x.p25}–${x.p75}</td></tr>` : ''; };
    const q = r.quality_suggested, qn = r.quality_now;
    $('#sugg', m).innerHTML = `<h3 style="margin:0 0 6px">Fitted to your results</h3>
      <p class="small ${r.enough_data ? '' : 'muted'}" style="margin:0 0 8px">${esc(r.message)}</p>
      <p class="small muted" style="margin:0 0 8px">Uses only scores made <b>without</b> knowing the result (yours, or AI scores of the wording alone). Based on <b>${r.scored_and_measured}</b> measured posts${r.excluded_informed_or_unknown ? `; ${r.excluded_informed_or_unknown} others were scored with the results in view or of unknown origin, so they are left out` : ''}.</p>
      ${r.measured_without_blind_score && state.ai.available ? `<div class="row" style="gap:8px;margin:0 0 10px"><button class="btn sm" id="blindgo">Score the wording of ${r.measured_without_blind_score} measured post${r.measured_without_blind_score === 1 ? '' : 's'}</button><span class="small muted" id="blindprog">One AI call each; your visible scores are not changed.</span></div>` : ''}
      ${r.scored_and_measured ? `<div class="table-wrap" style="box-shadow:none"><table><thead><tr><th>How the post did</th><th class="num">Posts</th><th class="num">Median score</th><th class="num">Middle half</th></tr></thead><tbody>
        ${row('Winners', 'winner')}${row('About normal', 'average')}${row('Duds', 'dud')}</tbody></table></div>` : ''}
      ${r.enough_data ? `<p style="margin:10px 0 4px">Suggested: <b>Scale at ${r.suggestion.scale_vsc}+</b>, <b>Tweak at ${r.suggestion.mutate_vsc}–${r.suggestion.scale_vsc - 1}</b>.
        <span class="muted small">Your current lines (Scale ${r.current.scale_vsc}+) catch ${qn.winners_caught}% of winners and ${qn.precision == null ? 'flag nothing' : qn.precision + '% of what they flag is a real winner'};
        the suggestion catches ${q.winners_caught}% and ${q.precision == null ? 'flags nothing' : q.precision + '% of what it flags is a real winner'}.</span></p>
        <div class="row" style="gap:8px"><button class="btn sm" id="usesugg">Preview these</button>${isAdmin() ? '<button class="btn sm primary" id="savesugg">Use as my rules</button>' : ''}</div>` : ''}`;
    const bg = $('#blindgo', m);
    if (bg) bg.onclick = guard(async () => {
      bg.disabled = true;
      const total = r.measured_without_blind_score;
      let done = 0, stop = false;
      const failed = [];
      while (!stop) {
        const out = await api('POST', `/api/ai/score-blind?limit=1${failed.length ? '&skip=' + failed.join(',') : ''}`);
        done += out.scored.length;
        out.errors.forEach(e => failed.push(e.post_id));
        const el = $('#blindprog', m);
        if (el) el.textContent = `Scored ${done} of ${total}${failed.length ? ` (${failed.length} failed, skipped)` : ''}…`;
        if (out.errors.length && failed.length >= 3) { toast(out.errors[0].error, true); stop = true; }
        if (!out.scored.length && !out.errors.length) stop = true;
        if (out.remaining <= 0 || done >= 12) stop = true;     // a sitting's worth; run it again for more
      }
      toast(`Scored ${done} post${done === 1 ? '' : 's'} on their wording`);
      await loadSugg();
    });
    const use = $('#usesugg', m);
    if (use) use.onclick = () => { $('#sc', m).value = r.suggestion.scale_vsc; $('#mu', m).value = r.suggestion.mutate_vsc; $('#go', m).click(); };
    const save = $('#savesugg', m);
    if (save) save.onclick = guard(async () => {
      state.settings = await api('PUT', '/api/settings', { scale_vsc: r.suggestion.scale_vsc, mutate_vsc: r.suggestion.mutate_vsc });
      toast('Rules updated'); m.close(); route();
    });
  }).catch(() => { const el = $('#sugg', m); if (el) el.remove(); });
  loadSugg();
  $('#go', m).onclick = guard(async () => {
    const r = await api('GET', `/api/decisions/preview?scale_vsc=${+$('#sc', m).value}&mutate_vsc=${+$('#mu', m).value}`);
    const flips = r.posts.filter(p => p.changed);
    $('#out', m).innerHTML = `<p><b>${r.changed}</b> of ${r.total_scored} scored posts would change decision.</p>
      ${flips.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Account</th><th>Hook</th><th>VSC</th><th>Now</th><th>If</th></tr></thead><tbody>
      ${flips.slice(0, 40).map(p => `<tr><td>${esc(p.post_date || '')}</td><td>${esc(p.handle || '')}</td><td class="wrap">${esc(short(p.hook, 40))}</td>
        <td class="num">${p.vsc}</td><td>${pill(p.decision_now)}</td><td>${pill(p.decision_if)}</td></tr>`).join('')}
      </tbody></table></div>` : '<p class="muted">No flips at these thresholds.</p>'}`;
  });
}

function ideasModal() {
  const m = openModal(`<h2>✨ AI hook ideas</h2>${aiNotice()}<p class="muted small">Uses your top posts plus Inspiration items marked for AI as few-shot style references.</p>
    <div class="fields"><div><label class="f">For account</label><select id="acc">${accountOptions(state.hookAccount, 'Choose…')}</select></div>
    <div><label class="f">How many</label><input type="number" id="n" value="5" min="1" max="12"></div>
    <div class="full"><label class="f">Direction (optional)</label><input id="brief" maxlength="500" placeholder="e.g. lean into long-distance anxiety, slideshow format"></div></div>
    <div id="out"></div><div class="actions"><button class="btn" data-close>Close</button><button class="btn ai" id="go" ${state.ai.available ? '' : 'disabled'}>Generate</button></div>`, true);
  $('#go', m).onclick = guard(async () => {
    const acc = $('#acc', m).value; if (!acc) throw new Error('Choose an account');
    const btn = $('#go', m); busy(btn, true, 'Writing…');
    let r; try { r = await api('POST', '/api/ai/hooks', { account_id: +acc, n: +$('#n', m).value, brief: $('#brief', m).value }); } finally { busy(btn, false); }
    $('#out', m).innerHTML = r.ideas.map((x, i) => `<div class="card" style="margin-top:10px"><b>${esc(x.hook)}</b>
      <div class="muted">${esc(x.body || '')}</div><div class="small muted">${esc(x.ai_prompt || '')}</div><div class="small">${esc(x.caption)}</div>
      <div class="row" style="margin-top:8px"><input type="date" data-d="${i}" value="${iso(nowLocal())}" style="width:auto"><button class="btn sm primary" data-add="${i}">Add to calendar</button></div></div>`).join('');
    $$('[data-add]', m).forEach(b => (b.onclick = guard(async () => {
      const x = r.ideas[+b.dataset.add];
      await api('POST', '/api/posts', { account_id: +acc, post_date: $(`[data-d="${b.dataset.add}"]`, m).value, status: 'scripted',
        ai_prompt: x.ai_prompt || '', hook: x.hook, body: x.body || '', caption: x.caption || '' });
      b.textContent = 'Added ✓'; b.disabled = true;
    })));
  });
}

/* ---------------- accounts ---------------- */
pages.accounts = async app => {
  const pins = getPins();
  state.accounts = [...state.accounts].sort((a, b) => {
    const pa = pins.has(String(a.id)) ? 0 : 1;
    const pb = pins.has(String(b.id)) ? 0 : 1;
    return pa - pb || (a.handle || '').localeCompare(b.handle || '');
  });
  const groups = state.phones.map(p => ({ p, rows: state.accounts.filter(a => a.phone_id === p.id) }));
  const loose = state.accounts.filter(a => !a.phone_id);
  if (loose.length) groups.push({ p: { id: null, label: 'No phone', color: '#94a3b8' }, rows: loose });
  app.innerHTML = `<div class="page-head"><div><h1>Accounts</h1><div class="sub">${state.accounts.length} accounts across ${state.phones.length} phones</div></div>
    <div class="row"><button class="btn" id="addphone">+ Phone</button><button class="btn primary" id="addacc">+ Account</button>
      <button class="btn" id="syncall" title="Sync all accounts with Auto-sync on">Sync all</button></div></div>
    ${groups.map(({ p, rows }) => `<div class="card" style="margin-bottom:14px"><div class="row" style="margin-bottom:8px">
      <span class="dot" style="background:${esc(p.color)}"></span><b>${esc(p.label)}</b><span class="muted">${rows.length} accounts</span><span class="spacer"></span>
      ${p.id ? `<button class="btn sm" data-ep="${p.id}">Edit</button>` : ''}</div>
      ${rows.length ? `<div class="table-wrap" style="box-shadow:none"><table><thead><tr><th>Handle</th><th>Platform</th><th>Niche</th><th>Status</th><th class="num" title="From the platform profile, refreshed weekly (or on a full sync)">Followers</th><th class="num">Posts/day</th><th>Content hook</th><th>Sync</th><th></th></tr></thead><tbody>
      ${rows.map(a => `<tr><td class="handle"><button type="button" class="pin-btn ${getPins().has(String(a.id)) ? 'on' : ''}" data-pin="${a.id}" title="Pin account">${getPins().has(String(a.id)) ? '★' : '☆'}</button> ${esc(a.handle)}</td><td>${esc(a.platform)}</td><td>${esc(a.niche)}</td><td>${pill(a.status, a.status === 'active' ? 'posted' : a.status === 'killed' ? 'KILL' : 'HOLD')}</td>
        <td class="num" title="${a.followers_day ? `as of ${esc(String(a.followers_day).slice(0, 10))}` : 'no snapshot yet: run a sync'}">${a.followers == null ? '<span class="muted">-</span>' : compact(a.followers)}${a.followers_change ? `<div class="small ${a.followers_change > 0 ? 'up' : 'down'}" title="since ${esc(String(a.followers_since).slice(0, 10))}">${a.followers_change > 0 ? '+' : ''}${compact(a.followers_change)}</div>` : ''}</td>
        <td class="num">${a.times_per_day}</td><td class="muted">${esc(a.content_hook)}</td>
        <td class="muted small">${a.sync_enabled ? 'on' : 'off'}${a.last_synced_at ? `<br><span title="${esc(a.last_synced_at)}">${esc(String(a.last_synced_at).slice(0,10))}</span>` : ''}</td>
        <td class="row" style="gap:4px;flex-wrap:nowrap"><button class="btn sm" data-sync="${a.id}" title="Pull posts & views from SociaVault">Sync</button><button class="btn sm" data-ea="${a.id}">Edit</button></td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">No accounts on this phone.</p>'}</div>`).join('')}
    ${state.accounts.length ? '' : `<div class="empty card"><h2>No accounts yet</h2><p>Add a phone and handles, or import a spreadsheet. Or load sample data from <a href="#/dashboard">Home</a>.</p></div>`}
    <div class="grid cols-2">    <div class="card"><h2>Import from spreadsheet</h2><p class="muted">Upload "The Ugly Tracker" workbook (.xlsx). Accounts, daily views and the hook log are imported; re-importing never duplicates. The workbook has no dates for Day 1–7, so choose the Monday they belong to.</p>
      <div class="fields"><div><label class="f">Week starting (Monday)</label><input type="date" id="iw" value="${iso(mondayOf(nowLocal()))}"></div>
      <div><label class="f">File</label><input type="file" id="ifile" accept=".xlsx"></div></div>
      <div class="actions"><button class="btn primary" id="import">Import</button></div></div></div>`;

  $('#addphone').onclick = () => phoneModal();
  $('#addacc').onclick = () => accountModal();
  $$('[data-ep]', app).forEach(b => (b.onclick = () => phoneModal(state.phones.find(p => p.id === +b.dataset.ep))));
  $$('[data-pin]', app).forEach(b => b.onclick = e => {
    e.stopPropagation();
    togglePin(b.dataset.pin);
    route();
  });
  $$('[data-ea]', app).forEach(b => (b.onclick = () => accountModal(state.accounts.find(a => a.id === +b.dataset.ea))));
  $$('[data-sync]', app).forEach(b => (b.onclick = guard(async () => {
    const btn = b; busy(btn, true, '…');
    try {
      const r = await api('POST', `/api/accounts/${b.dataset.sync}/sync?days_back=14`);
      toast(`Synced @${r.handle || ''}: ${r.upserted} posts (~${r.credits_est || '?'} credits)`);
      route();
    } finally { busy(btn, false, 'Sync'); }
  })));
  const sa = $('#syncall', app);
  if (sa) sa.onclick = guard(async () => {
    if (!(state.sv && state.sv.configured)) { toast('Add a SociaVault API key in Settings first', true); return; }
    busy(sa, true, 'Syncing…');
    try {
      const r = await api('POST', '/api/sync/all?days_back=14');
      toast(`Synced ${r.synced}/${r.total} accounts`);
      route();
    } finally { busy(sa, false, 'Sync all'); }
  });
  $('#import').onclick = guard(async () => {
    const f = $('#ifile').files[0]; if (!f) throw new Error('Choose a .xlsx file first');
    const fd = new FormData(); fd.append('file', f); fd.append('week_start', $('#iw').value);
    const b = $('#import'); busy(b, true, 'Importing…');
    let r; try { r = await api('POST', '/api/import/xlsx', fd); } finally { busy(b, false); }
    state.weekStart = $('#iw').value; toast(`Imported ${r.accounts} accounts, ${r.views} view entries, ${r.posts} posts`); route();
  });
};

function phoneModal(p) {
  const m = openModal(`<h2>${p ? 'Edit phone' : 'New phone'}</h2><div class="fields">
    <div><label class="f">Label</label><input id="l" value="${esc(p ? p.label : `Phone ${state.phones.length + 1}`)}"></div>
    <div><label class="f">Color band</label><input id="c" type="color" value="${esc(p ? p.color : '#6366f1')}" style="height:38px;padding:3px"></div></div>
    <div class="actions">${p ? '<button class="btn danger" id="del">Delete</button>' : ''}<span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="save">Save</button></div>`);
  $('#save', m).onclick = guard(async () => {
    const b = { label: $('#l', m).value.trim(), color: $('#c', m).value, position: p ? p.position : state.phones.length + 1 };
    if (!b.label) throw new Error('Label is required');
    await (p ? api('PATCH', `/api/phones/${p.id}`, b) : api('POST', '/api/phones', b)); m.close(); route();
  });
  if (p) $('#del', m).onclick = guard(async () => {
    if (!confirm('Delete this phone? Its accounts are kept but unassigned.')) return;
    await api('DELETE', `/api/phones/${p.id}`); m.close(); route();
  });
}

function accountModal(a) {
  const x = a || { handle: '', phone_id: state.phones[0] ? state.phones[0].id : '', platform: 'TikTok', niche: '', status: 'active', times_per_day: 1, content_hook: '', model: '', sync_enabled: false };
  const m = openModal(`<h2>${a ? 'Edit account' : 'New account'}</h2><div class="fields">
    <div><label class="f">Handle or profile link</label><input id="h" value="${esc(x.handle)}" placeholder="@account_1a  or  tiktok.com/@name">
      <div class="small" id="hcheck" style="margin-top:4px"><button class="btn sm" id="hverify" type="button">Check it exists <span class="muted">(1 credit)</span></button></div></div>
    <div><label class="f">Phone</label><select id="ph"><option value="">None</option>${state.phones.map(p => `<option value="${p.id}" ${p.id === x.phone_id ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></div>
    <div><label class="f">Platform</label><select id="pf">${['TikTok', 'Instagram', 'YouTube'].map(p => `<option ${String(x.platform).toLowerCase() === p.toLowerCase() ? 'selected' : ''}>${p}</option>`).join('')}${['TikTok', 'Instagram', 'YouTube'].some(p => p.toLowerCase() === String(x.platform).toLowerCase()) ? '' : `<option selected>${esc(x.platform)}</option>`}</select></div>
    <div><label class="f">Niche</label><input id="n" value="${esc(x.niche)}"></div>
    <div><label class="f">Status</label><select id="st">${['active', 'warming', 'paused', 'killed'].map(s => `<option ${x.status === s ? 'selected' : ''}>${s}</option>`).join('')}</select></div>
    <div><label class="f">Posts per day</label><input id="t" type="number" min="0" max="50" value="${x.times_per_day}"></div>
    <div><label class="f">Content hook style</label><input id="ch" value="${esc(x.content_hook)}"></div>
    <div><label class="f">AI model / tool</label><input id="md" value="${esc(x.model)}"></div>
    <div style="grid-column:1/-1"><label class="f" style="display:flex;align-items:center;gap:8px;font-weight:500">
      <input type="checkbox" id="sy" ${x.sync_enabled ? 'checked' : ''}> Auto-sync via SociaVault (include in Sync all)</label>
      <div class="muted small">Pulls public posts &amp; view counts. Requires a key in Settings → SociaVault.</div></div></div>
    <div class="actions">${a ? '<button class="btn danger" id="del">Delete</button>' : ''}<span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="save">Save</button></div>`);
  // a pasted profile link tells us the platform: set it without spending anything
  const guessPlatform = v => (/tiktok\.com/i.test(v) ? 'TikTok' : /instagram\.com/i.test(v) ? 'Instagram' : /youtu(\.be|be\.com)/i.test(v) ? 'YouTube' : '');
  $('#h', m).addEventListener('input', e => { const g = guessPlatform(e.target.value); if (g) $('#pf', m).value = g; $('#hcheck', m).innerHTML = '<button class="btn sm" id="hverify" type="button">Check it exists <span class="muted">(1 credit)</span></button>'; bindVerify(); });
  const bindVerify = () => {
    const vb = $('#hverify', m);
    if (!vb) return;
    vb.onclick = guard(async () => {
      const handle = $('#h', m).value.trim();
      if (!handle) { toast('Type a handle first', true); return; }
      busy(vb, true, 'Checking…');
      let r;
      try { r = await api('POST', '/api/accounts/lookup', { handle, platform: $('#pf', m).value }); }
      catch (e) { $('#hcheck', m).innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; return; }
      if (r.found) {
        $('#h', m).value = r.handle; $('#pf', m).value = r.platform;
        $('#hcheck', m).innerHTML = `<span class="up">✓ ${esc(r.display_name || r.handle)}${r.verified ? ' (verified)' : ''}</span> <span class="muted">· ${r.followers != null ? compact(r.followers) + ' followers' : r.platform}</span>`;
      } else {
        $('#hcheck', m).innerHTML = `<span style="color:var(--bad)">${esc(r.message)}</span>`;
      }
    });
  };
  bindVerify();
  $('#save', m).onclick = guard(async () => {
    const b = { handle: $('#h', m).value.trim(), phone_id: $('#ph', m).value ? +$('#ph', m).value : null, platform: $('#pf', m).value, niche: $('#n', m).value,
      status: $('#st', m).value, times_per_day: +$('#t', m).value || 0, content_hook: $('#ch', m).value, model: $('#md', m).value,
      sync_enabled: $('#sy', m).checked };
    if (!b.handle) throw new Error('Handle is required');
    await (a ? api('PATCH', `/api/accounts/${a.id}`, b) : api('POST', '/api/accounts', b)); m.close(); route();
  });
  if (a) $('#del', m).onclick = guard(async () => {
    if (!confirm(`Delete ${a.handle}? This also deletes its views and posts.`)) return;
    await api('DELETE', `/api/accounts/${a.id}`); m.close(); route();
  });
}


/* ---------------- settings ---------------- */
pages.settings = async app => {
  const admin = isAdmin();
  const [members, cfg, invites, sv, vid, pub] = await Promise.all([
    api('GET', '/api/workspace/members'),
    admin ? api('GET', '/api/ai/config') : null,
    admin ? api('GET', '/api/workspace/invites') : [],
    admin ? api('GET', '/api/connectors/sociavault') : state.sv,
    api('GET', '/api/addons/video'),
    api('GET', '/api/addons/publish'),
  ]);
  state.sv = sv || state.sv;
  state.video = vid;
  state.publish = pub;
  const S = state.settings, me = state.me;
  const roleSel = (m) => `<select data-role="${m.id}" style="width:auto" ${admin ? '' : 'disabled'}>${['owner', 'admin', 'member'].map(r => `<option ${m.role === r ? 'selected' : ''}>${r}</option>`).join('')}</select>`;

  const addonsOpen = !!(vid && vid.enabled) || !!(pub && pub.enabled) || !!(sv && sv.configured) || state.settingsOpenAddons;
  app.innerHTML = `<div class="page-head"><div><h1>Settings</h1><div class="sub">${esc(me.workspace.name)} · you are ${esc(me.workspace.role)}</div></div></div>
  <div class="grid" style="max-width:860px">

  <div class="settings-section"><h2 class="settings-h">Getting started</h2>
  <p class="muted small" style="margin:-4px 0 12px">Core setup for this workspace. Everything else is optional.</p>

  <div class="card" id="ai-card"><h2>✨ AI <span class="muted small" style="font-weight:500">(optional)</span></h2>
    ${admin ? `
    <p class="muted" id="ai-status">${cfg.source === 'workspace' ? `Using this workspace's own ${esc(cfg.providers[cfg.provider].label)} settings${cfg.has_workspace_key ? ` (key <code>${esc(cfg.key_hint)}</code>)` : ''}.`
      : cfg.source === 'server' ? 'Using the server\'s default key. Add your own below to bill your own account.'
      : 'Not configured. Choose a provider to turn on screenshot reading, scoring, hook ideas, planning and Ask.'}</p>
    <div class="fields">
      <div class="full"><label class="f">Provider</label>
        <select id="ai-provider">${Object.entries(cfg.providers).map(([id, p]) => `<option value="${id}" ${id === cfg.provider ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></div>
      <div class="full" id="url-row"><label class="f">Endpoint base URL</label>
        <input id="ai-url" value="${esc(cfg.base_url)}" placeholder="https://api.groq.com/openai/v1" spellcheck="false">
        <div class="muted small" style="margin-top:4px">Any server that speaks the OpenAI chat API: Groq <code>https://api.groq.com/openai/v1</code>, OpenRouter <code>https://openrouter.ai/api/v1</code>, Together <code>https://api.together.xyz/v1</code>, Mistral <code>https://api.mistral.ai/v1</code>, vLLM, LiteLLM, Ollama…
        Addresses on private networks (like localhost) are blocked unless the server operator allows them.</div></div>
      <div class="full"><label class="f" id="key-label">API key</label>
        <input id="ai-key" type="password" autocomplete="off" spellcheck="false"></div>
      <div class="full"><label class="f">Model</label>
        <input id="ai-model" list="models" value="${esc(cfg.model)}" spellcheck="false"><datalist id="models"></datalist>
        <div class="muted small" id="model-hint" style="margin-top:4px"></div></div>
    </div>
    <div id="move-warn" class="notice" style="display:none;margin-top:12px"></div>
    <div class="actions"><span id="ai-result" class="small spacer"></span>
      ${cfg.has_workspace_key ? '<button class="btn danger" id="ai-clear">Remove key</button>' : ''}
      <button class="btn" id="ai-test">Test connection</button><button class="btn primary" id="ai-save">Save</button></div>
    <p class="small muted">Keys are encrypted before they're stored, never shown again, and only ever sent to the provider you picked for this workspace. Reading screenshots needs a vision-capable model. Usage is billed to the key's owner.</p>`
    : `<p class="muted">${state.ai.available ? `AI is on (${esc(state.ai.model)}).` : 'AI is off for this workspace.'} Only workspace admins can change this.</p>`}
  </div>

  <div class="card"><h2>Decision rules</h2>
    <p class="muted small">When to Scale, Tweak, or Stop an account based on weekly views and hook scores.</p>
    <div class="fields">
    ${[['scale_weekly', 'Weekly views to Scale'], ['scale_vsc', 'Hook score to Scale (of 30)'], ['mutate_vsc', 'Hook score to Tweak (of 30)'], ['kill_after_posts', 'Posts before a stall = Stop'], ['stall_views', 'Views that count as a breakout'], ['winner_pct', 'Winner = % of an account\'s normal views (200 = 2×)']]
      .map(([k, l]) => `<div><label class="f">${l}</label><input data-s="${k}" type="number" min="0" value="${S[k]}" ${admin ? '' : 'disabled'}></div>`).join('')}</div>
    ${admin ? '<div class="actions"><button class="btn primary" id="saverules">Save rules</button></div>' : ''}</div>

  <div class="card"><h2>Import spreadsheet</h2>
    <p class="muted small">Bring in your existing accounts and weekly views from the HookTrack Excel template.</p>
    <p class="muted">Go to <a href="#/accounts">Accounts</a> and use <b>Import .xlsx</b> at the bottom of the page.</p>
  </div>
  <div class="card"><h2>Export CSV</h2>
    <p class="muted small">Download views and posts for spreadsheets or backups.</p>
    <div class="row" style="gap:8px;flex-wrap:wrap">
      <a class="btn" href="/api/export/views.csv" download>Views CSV</a>
      <a class="btn" href="/api/export/posts.csv" download>Posts CSV</a>
    </div>
  </div>
  </div><!-- /getting started -->

  <div class="settings-section">
  <button type="button" class="settings-h settings-toggle" id="addons-toggle" aria-expanded="${addonsOpen ? 'true' : 'false'}">
    Add-ons ${addonsOpen ? '▾' : '▸'}
  </button>
  <p class="muted small" style="margin:-4px 0 12px">Optional tools. Enable only what you need — core tracking works without them.</p>
  <div id="addons-panel" style="display:${addonsOpen ? 'block' : 'none'}">

  <div class="card" id="sv-card"><h2>SociaVault <span class="muted small" style="font-weight:500">auto-import views</span></h2>
    ${admin ? `
    <p class="muted">Pull public posts and view counts from TikTok, Instagram, and YouTube into this workspace.
      ${sv.configured ? (sv.source === 'workspace' ? `Key saved (<code>${esc(sv.key_hint)}</code>).` : 'Using the server-wide key.') : 'No key configured yet.'}
      Get a key at <a href="https://sociavault.com" target="_blank" rel="noopener">sociavault.com</a> (credits never expire).</p>
    <div class="fields">
      <div class="full"><label class="f">API key</label>
        <input id="sv-key" type="password" autocomplete="off" spellcheck="false" placeholder="${sv.key_hint ? '•••• leave blank to keep current' : 'sk_live_…'}"></div>
    </div>
    <div class="actions"><span id="sv-result" class="small spacer"></span>
      ${sv.source === 'workspace' && sv.key_hint ? '<button class="btn danger" id="sv-clear">Remove key</button>' : ''}
      <button class="btn" id="sv-test">Test connection</button><button class="btn primary" id="sv-save">Save</button></div>
    <p class="small muted">Public data only — no private Insights. Enable <b>Auto-sync</b> on each account; the server runs a daily <b>light</b> sync (~1 credit/account) across all workspaces automatically.
      Manual <b>Sync</b> / <b>Sync all</b> still available. Keys are encrypted like AI keys.</p>`
    : `<p class="muted">${state.sv && state.sv.configured ? 'SociaVault is configured for this workspace.' : 'SociaVault is not configured.'} Only workspace admins can change the API key.</p>`}
  </div>


  <div class="card" id="video-card"><h2>🎬 Video generator</h2>
    ${admin ? `
    <p class="muted">Optional add-on: turn scripts (hook + body) into videos via an external provider (BYOK). When a job finishes, the post moves to <b>Filmed</b>.</p>
    <div class="fields">
      <div class="full"><label class="f" style="display:flex;align-items:center;gap:8px">
        <input type="checkbox" id="vid-on" ${vid.enabled ? 'checked' : ''}> Enable video generator</label></div>
      <div class="full"><label class="f">Provider</label>
        <select id="vid-provider">${Object.entries(vid.providers || {}).map(([id, p]) =>
          `<option value="${id}" ${id === vid.provider ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select>
        <div class="muted small" id="vid-hint" style="margin-top:4px"></div></div>
      <div class="full" id="vid-url-row"><label class="f">Webhook URL</label>
        <input id="vid-url" value="${esc((vid.config || {}).webhook_url || '')}" placeholder="https://your-service.com/generate" spellcheck="false"></div>
      <div class="full" id="vid-tpl-row" style="display:none"><label class="f">Creatomate template ID</label>
        <input id="vid-tpl" value="${esc((vid.config || {}).template_id || '')}" placeholder="template uuid"></div>
      <div class="full" id="vid-env-row" style="display:none"><label class="f">Shotstack env</label>
        <select id="vid-env"><option value="stage" ${(vid.config || {}).shotstack_env !== 'prod' ? 'selected' : ''}>stage (sandbox)</option>
        <option value="prod" ${(vid.config || {}).shotstack_env === 'prod' ? 'selected' : ''}>production</option></select></div>
      <div class="full" id="vid-ai-row" style="display:none">
        <label class="f">AI model (optional)</label>
        <input id="vid-model" value="${esc((vid.config || {}).model || '')}" placeholder="runway: gen3a_turbo · luma: ray-2 · replicate: kwaivgi/kling-v1.6-standard">
        <label class="f" style="margin-top:8px">Style / look</label>
        <input id="vid-style" value="${esc((vid.config || {}).style || '')}" placeholder="vertical 9:16 short-form, high energy, clean">
        <label class="f" style="margin-top:8px">Duration (seconds, if supported)</label>
        <input id="vid-dur" type="number" min="3" max="15" value="${(vid.config || {}).duration || 5}">
        <label class="f" style="margin-top:8px">Aspect ratio</label>
        <input id="vid-aspect" value="${esc((vid.config || {}).aspect_ratio || (vid.config || {}).ratio || '9:16')}" placeholder="9:16">
      </div>
      <div class="full"><label class="f">API key ${vid.has_secret ? `(saved <code>${esc(vid.key_hint)}</code>)` : ''}</label>
        <input id="vid-key" type="password" autocomplete="off" spellcheck="false" placeholder="${vid.has_secret ? 'Leave blank to keep current key' : 'Paste key (if required)'}"></div>
    </div>
    <div class="actions"><button class="btn" id="vid-test">Test</button><button class="btn primary" id="vid-save">Save</button>
      ${vid.has_secret ? '<button class="btn danger" id="vid-clear">Clear key</button>' : ''}</div>
    <p class="small muted">AI video: <b>Runway</b>, <b>Luma</b>, <b>Replicate</b> (text-to-video from your script). Also webhook, Shotstack, Creatomate, Mock. Keys encrypted like AI keys.</p>`
    : `<p class="muted">Ask a workspace admin to enable the video generator.</p>
      <p class="small muted">Status: ${vid.enabled ? 'On · ' + esc(vid.provider) : 'Off'}</p>`}
  </div>


  <div class="card" id="publish-card"><h2>📤 Social publish</h2>
    ${admin ? `
    <p class="muted">Optional add-on: publish a post (caption + video URL) to TikTok / Instagram / YouTube / etc. via an external API. <b>Never auto-posts</b> — only when you click Publish.</p>
    <div class="fields">
      <div class="full"><label class="f" style="display:flex;align-items:center;gap:8px">
        <input type="checkbox" id="pub-on" ${pub.enabled ? 'checked' : ''}> Enable social publish</label></div>
      <div class="full"><label class="f">Provider</label>
        <select id="pub-provider">${Object.entries(pub.providers || {}).map(([id, p]) =>
          `<option value="${id}" ${id === pub.provider ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select>
        <div class="muted small" id="pub-hint" style="margin-top:4px"></div></div>
      <div class="full"><label class="f">Default platforms (comma-separated)</label>
        <input id="pub-platforms" value="${esc(((pub.config || {}).default_platforms || ['tiktok']).join ? ((pub.config || {}).default_platforms || ['tiktok']).join(', ') : (pub.config || {}).default_platforms || 'tiktok')}" placeholder="tiktok, instagram, youtube"></div>
      <div class="full" id="pub-profile-row" style="display:none"><label class="f">Ayrshare Profile-Key (optional)</label>
        <input id="pub-profile" value="${esc((pub.config || {}).profile_key || '')}" placeholder="For multi-user profiles"></div>
      <div class="full" id="pub-user-row" style="display:none"><label class="f">Upload-Post user / profile (optional)</label>
        <input id="pub-user" value="${esc((pub.config || {}).user || (pub.config || {}).profile || '')}" placeholder="Profile id if required"></div>
      <div class="full"><label class="f">API key ${pub.has_secret ? `(saved <code>${esc(pub.key_hint)}</code>)` : ''}</label>
        <input id="pub-key" type="password" autocomplete="off" spellcheck="false" placeholder="${pub.has_secret ? 'Leave blank to keep current key' : 'Paste API key'}"></div>
    </div>
    <div class="actions"><button class="btn" id="pub-test">Test</button><button class="btn primary" id="pub-save">Save</button>
      ${pub.has_secret ? '<button class="btn danger" id="pub-clear">Clear key</button>' : ''}</div>
    <p class="small muted">Recommended: <b>Upload-Post</b> (cheapest). Schedule times are queued in HookTrack by default and published when due. Connect accounts in the provider dashboard.</p>`
    : `<p class="muted">Ask a workspace admin to enable social publish.</p>
      <p class="small muted">Status: ${pub.enabled ? 'On · ' + esc(pub.provider) : 'Off'}</p>`}
  </div>

  </div><!-- /addons-panel -->
  </div><!-- /addons section -->

  <div class="settings-section"><h2 class="settings-h">Team &amp; account</h2>

  <div class="card"><h2>Workspace</h2>
    ${admin ? `<div class="row" style="margin-bottom:12px"><input id="ws-name" value="${esc(me.workspace.name)}" maxlength="100" style="max-width:320px"><button class="btn" id="ws-rename">Rename</button></div>
    <div class="row" style="margin-bottom:12px"><label class="f" for="ws-tz" style="margin:0">Time zone</label>
      <select id="ws-tz" style="max-width:320px">${tzOptions(me.workspace.timezone)}</select><button class="btn" id="ws-tz-save">Save</button>
      <span class="muted small">"Today" and weeks (Mon-Sun) follow this zone. Currently ${esc(nowLocal().toDateString())}.</span></div>` : ''}
    <div class="table-wrap" style="box-shadow:none"><table><thead><tr><th>Member</th><th>Role</th><th></th></tr></thead><tbody>
    ${members.map(m => `<tr><td>${esc(m.email)}${m.name ? ` <span class="muted">· ${esc(m.name)}</span>` : ''}${m.id === me.user.id ? ' <span class="pill">you</span>' : ''}</td>
      <td>${roleSel(m)}</td><td class="num">${admin || m.id === me.user.id ? `<button class="btn sm danger" data-rm="${m.id}">${m.id === me.user.id ? 'Leave' : 'Remove'}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>
    ${admin ? `<h2 style="margin-top:18px">Invite someone</h2>
      <div class="row"><input id="inv-email" type="email" placeholder="teammate@company.com" style="max-width:300px"><select id="inv-role" style="width:auto"><option>member</option><option>admin</option></select>
        <button class="btn primary" id="inv-go">Create invite link</button></div>
      <div id="inv-link"></div>
      ${invites.length ? `<p class="small muted" style="margin:12px 0 4px">Pending invites</p><ul class="list">${invites.map(i =>
        `<li><span style="flex:1">${esc(i.email)} <span class="muted">· ${esc(i.role)}</span></span><button class="btn sm" data-revoke="${esc(i.id)}">Revoke</button></li>`).join('')}</ul>` : ''}` : ''}
  </div>

  <div class="card"><h2>Your account</h2><p class="muted">${esc(me.user.email)}</p>
    <div class="fields"><div><label class="f">Current password</label><input id="pw-cur" type="password" autocomplete="current-password"></div>
      <div><label class="f">New password (8+ characters)</label><input id="pw-new" type="password" autocomplete="new-password"></div></div>
    <div class="actions"><button class="btn" id="pw-go">Change password</button></div>
    <p class="small muted">Changing your password signs you out everywhere else.</p></div>
  ${me.workspace.role === 'owner' ? `<div class="card danger-zone"><h2>Delete this workspace</h2>
    <p class="muted">Permanently deletes <b>${esc(me.workspace.name)}</b> and everything in it: accounts, views, posts, settings, keys and uploads. Other members lose access. This cannot be undone.</p>
    <div class="fields"><div><label class="f">Type the workspace name to confirm</label><input id="del-name" autocomplete="off"></div>
      <div><label class="f">Your password</label><input id="del-pw" type="password" autocomplete="current-password"></div></div>
    <div class="actions"><button class="btn danger" id="del-go">Delete workspace</button></div></div>` : ''}
  </div>`;


  const addonsBtn = $('#addons-toggle');
  if (addonsBtn) addonsBtn.onclick = () => {
    state.settingsOpenAddons = !state.settingsOpenAddons;
    const panel = $('#addons-panel');
    const open = !!state.settingsOpenAddons;
    if (panel) panel.style.display = open ? 'block' : 'none';
    addonsBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    addonsBtn.textContent = 'Add-ons ' + (open ? '▾' : '▸');
  };
  if (admin) {
    const sel = $('#ai-provider');
    const sync = () => {
      const p = cfg.providers[sel.value], same = sel.value === cfg.provider;
      $('#url-row').style.display = p.needs_url ? '' : 'none';
      $('#key-label').textContent = p.needs_key ? `${p.label.split(' ')[0]} API key` : 'API key (optional for local/open servers)';
      $('#ai-key').placeholder = cfg.has_workspace_key && same ? 'Saved — leave blank to keep it' : (p.needs_key ? (sel.value === 'anthropic' ? 'sk-ant-…' : 'sk-…') : 'Optional');
      $('#models').innerHTML = p.suggestions.map(m => `<option value="${esc(m)}">`).join('');
      $('#ai-model').placeholder = p.default_model ? `${p.default_model} (default)` : 'e.g. llama-3.3-70b-versatile';
      $('#model-hint').textContent = p.needs_url ? 'Required — use the exact model id your provider lists.' : 'Leave blank for the default. Smaller models are cheaper and fine for reading screenshots.';
      const moved = !same || (p.needs_url && $('#ai-url').value.trim().replace(/\/+$/, '') !== cfg.base_url);
      const w = $('#move-warn');
      w.style.display = moved && cfg.has_workspace_key ? '' : 'none';
      w.textContent = 'Changing the provider or endpoint removes the saved key — enter the new key before saving.';
    };
    sel.onchange = () => { if (sel.value !== cfg.provider) $('#ai-model').value = ''; else $('#ai-model').value = cfg.model; sync(); };
    $('#ai-url').oninput = sync; sync();
    const payload = () => {
      const b = { provider: sel.value, model: $('#ai-model').value };
      if (cfg.providers[sel.value].needs_url) b.base_url = $('#ai-url').value;
      const k = $('#ai-key').value.trim(); if (k) b.api_key = k;
      return b;
    };
    const result = (msg, ok) => { const r = $('#ai-result'); r.textContent = msg; r.style.color = ok ? 'var(--good)' : 'var(--bad)'; };
    $('#ai-save').onclick = guard(async () => { await api('PUT', '/api/ai/config', payload()); toast('AI settings saved'); route(); });
    const svResult = (msg, ok) => { const r = $('#sv-result'); if (r) { r.textContent = msg; r.style.color = ok ? 'var(--good)' : 'var(--bad)'; } };
    $('#sv-save').onclick = guard(async () => {
      const k = $('#sv-key').value.trim();
      if (!k) { toast('Enter an API key to save', true); return; }
      await api('PUT', '/api/connectors/sociavault', { api_key: k }); toast('SociaVault key saved'); route();
    });
    $('#sv-test').onclick = guard(async () => {
      const btn = $('#sv-test'); busy(btn, true, 'Testing…'); svResult('', true);
      try {
        const body = {}; const k = $('#sv-key').value.trim(); if (k) body.api_key = k;
        const r = await api('POST', '/api/connectors/sociavault/test', body);
        svResult(`Connected — ${r.reply}`, true);
      } catch (e) { svResult(e.message, false); }
      finally { busy(btn, false); }
    });
    const svClr = $('#sv-clear');
    if (svClr) svClr.onclick = guard(async () => {
      if (!confirm('Remove this workspace\'s SociaVault key?')) return;
      await api('PUT', '/api/connectors/sociavault', { clear_key: true }); toast('Key removed'); route();
    });

    const vidHint = () => {
      const p = $('#vid-provider') && $('#vid-provider').value;
      if (!p) return;
      const meta = (vid.providers || {})[p] || {};
      if ($('#vid-hint')) $('#vid-hint').textContent = meta.hint || '';
      if ($('#vid-url-row')) $('#vid-url-row').style.display = p === 'custom_webhook' ? '' : 'none';
      if ($('#vid-tpl-row')) $('#vid-tpl-row').style.display = p === 'creatomate' ? '' : 'none';
      if ($('#vid-env-row')) $('#vid-env-row').style.display = p === 'shotstack' ? '' : 'none';
      if ($('#vid-ai-row')) $('#vid-ai-row').style.display = ['runway','luma','replicate'].includes(p) ? '' : 'none';
    };
    if ($('#vid-provider')) { $('#vid-provider').onchange = vidHint; vidHint(); }
    if ($('#vid-save')) $('#vid-save').onclick = guard(async () => {
      const provider = $('#vid-provider').value;
      const config = {};
      if (provider === 'custom_webhook') config.webhook_url = ($('#vid-url').value || '').trim();
      if (provider === 'creatomate') config.template_id = ($('#vid-tpl').value || '').trim();
      if (provider === 'shotstack') config.shotstack_env = $('#vid-env').value;
      if (['runway','luma','replicate'].includes(provider)) {
        const model = ($('#vid-model').value || '').trim(); if (model) config.model = model;
        const style = ($('#vid-style').value || '').trim(); if (style) config.style = style;
        const dur = +($('#vid-dur').value || 0); if (dur) config.duration = dur;
        const aspect = ($('#vid-aspect').value || '').trim();
        if (aspect) { config.aspect_ratio = aspect; if (provider === 'runway') config.ratio = aspect.includes(':') && !aspect.includes('x') ? (aspect === '9:16' ? '768:1280' : aspect === '16:9' ? '1280:768' : aspect) : aspect; }
      }
      const body = { enabled: $('#vid-on').checked, provider, config };
      const k = ($('#vid-key').value || '').trim(); if (k) body.api_key = k;
      state.video = await api('PUT', '/api/addons/video', body);
      toast('Video add-on saved'); route();
    });
    if ($('#vid-test')) $('#vid-test').onclick = guard(async () => {
      const provider = $('#vid-provider').value;
      const config = {};
      if (provider === 'custom_webhook') config.webhook_url = ($('#vid-url').value || '').trim();
      if (provider === 'creatomate') config.template_id = ($('#vid-tpl').value || '').trim();
      if (provider === 'shotstack') config.shotstack_env = $('#vid-env').value;
      if (['runway','luma','replicate'].includes(provider)) {
        const model = ($('#vid-model').value || '').trim(); if (model) config.model = model;
        const style = ($('#vid-style').value || '').trim(); if (style) config.style = style;
        const dur = +($('#vid-dur').value || 0); if (dur) config.duration = dur;
        const aspect = ($('#vid-aspect').value || '').trim();
        if (aspect) { config.aspect_ratio = aspect; if (provider === 'runway') config.ratio = aspect === '9:16' ? '768:1280' : aspect === '16:9' ? '1280:768' : aspect; }
      }
      const body = { enabled: $('#vid-on').checked, provider, config };
      const k = ($('#vid-key').value || '').trim(); if (k) body.api_key = k;
      await api('PUT', '/api/addons/video', body);
      const r = await api('POST', '/api/addons/video/test');
      toast(r.message || 'OK');
    });
    if ($('#vid-clear')) $('#vid-clear').onclick = guard(async () => {
      if (!confirm('Clear saved video API key?')) return;
      state.video = await api('PUT', '/api/addons/video', {
        enabled: $('#vid-on').checked, provider: $('#vid-provider').value, clear_key: true, config: vid.config || {},
      });
      toast('Key cleared'); route();
    });

    const pubHint = () => {
      const p = $('#pub-provider') && $('#pub-provider').value;
      if (!p) return;
      const meta = (pub.providers || {})[p] || {};
      if ($('#pub-hint')) $('#pub-hint').textContent = meta.hint || '';
      if ($('#pub-profile-row')) $('#pub-profile-row').style.display = p === 'ayrshare' ? '' : 'none';
      if ($('#pub-user-row')) $('#pub-user-row').style.display = p === 'upload_post' ? '' : 'none';
    };
    if ($('#pub-provider')) { $('#pub-provider').onchange = pubHint; pubHint(); }
    if ($('#pub-save')) $('#pub-save').onclick = guard(async () => {
      const provider = $('#pub-provider').value;
      const platforms = ($('#pub-platforms').value || 'tiktok').split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
      const config = { default_platforms: platforms };
      if (provider === 'ayrshare' && $('#pub-profile').value.trim()) config.profile_key = $('#pub-profile').value.trim();
      if (provider === 'upload_post' && $('#pub-user').value.trim()) config.user = $('#pub-user').value.trim();
      const body = { enabled: $('#pub-on').checked, provider, config };
      const k = ($('#pub-key').value || '').trim(); if (k) body.api_key = k;
      state.publish = await api('PUT', '/api/addons/publish', body);
      toast('Publish add-on saved'); route();
    });
    if ($('#pub-test')) $('#pub-test').onclick = guard(async () => {
      // save first
      if ($('#pub-save')) await $('#pub-save').onclick();
      const r = await api('POST', '/api/addons/publish/test');
      toast(r.message || 'OK');
    });
    if ($('#pub-clear')) $('#pub-clear').onclick = guard(async () => {
      if (!confirm('Clear saved publish API key?')) return;
      state.publish = await api('PUT', '/api/addons/publish', {
        enabled: $('#pub-on').checked, provider: $('#pub-provider').value, clear_key: true, config: pub.config || {},
      });
      toast('Key cleared'); route();
    });
    $('#ai-test').onclick = guard(async () => {
      const btn = $('#ai-test'); busy(btn, true, 'Testing…'); result('', true);
      try { const r = await api('POST', '/api/ai/test', payload()); result(`Connected — ${r.model} replied.`, true); }
      catch (e) { result(e.message, false); } finally { busy(btn, false); }
    });
    const clr = $('#ai-clear');
    if (clr) clr.onclick = guard(async () => {
      if (!confirm('Remove this workspace\'s API key? AI features will stop working unless the server has a default key.')) return;
      await api('PUT', '/api/ai/config', { clear_key: true }); toast('Key removed'); route();
    });
    $('#saverules').onclick = guard(async () => {
      const v = {}; $$('[data-s]', app).forEach(i => (v[i.dataset.s] = +i.value));
      await api('PUT', '/api/settings', v); toast('Rules saved');
    });
    $('#ws-tz-save').onclick = guard(async () => {
      state.me = await api('PATCH', '/api/workspace', { timezone: $('#ws-tz').value });
      state.weekStart = null; state.calMonth = null; state.todayDay = null;
      toast('Time zone saved'); route();
    });
    $('#ws-rename').onclick = guard(async () => {
      state.me = await api('PATCH', '/api/workspace', { name: $('#ws-name').value }); toast('Renamed'); route();
    });
    $('#inv-go').onclick = guard(async () => {
      const r = await api('POST', '/api/workspace/invites', { email: $('#inv-email').value, role: $('#inv-role').value });
      const url = location.origin + r.path;
      $('#inv-link').innerHTML = `<div class="notice" style="margin-top:12px;background:var(--accent-soft);color:var(--text)">Send this link to ${esc(r.email)} — it works once and expires in ${r.expires_days} days. It can't be shown again.
        <div class="row" style="margin-top:8px"><input readonly value="${esc(url)}" id="inv-url"><button class="btn sm" id="inv-copy">Copy</button></div></div>`;
      $('#inv-copy').onclick = () => { $('#inv-url').select(); navigator.clipboard?.writeText(url); toast('Copied'); };
    });
    $$('[data-revoke]', app).forEach(b => (b.onclick = guard(async () => { await api('DELETE', `/api/workspace/invites/${b.dataset.revoke}`); route(); })));
    $$('[data-role]', app).forEach(sel => sel.addEventListener('change', guard(async () => {
      try { await api('PATCH', `/api/workspace/members/${sel.dataset.role}`, { role: sel.value }); toast('Role updated'); }
      finally { route(); }
    })));
  }
  $$('[data-rm]', app).forEach(b => (b.onclick = guard(async () => {
    const self = +b.dataset.rm === me.user.id;
    if (!confirm(self ? 'Leave this workspace?' : 'Remove this member?')) return;
    await api('DELETE', `/api/workspace/members/${b.dataset.rm}`);
    if (self) { state.me = await api('GET', '/api/auth/me'); resetViewState(); location.hash = '#/dashboard'; }
    route();
  })));
  const delBtn = $('#del-go');
  if (delBtn) delBtn.onclick = guard(async () => {
    if (!confirm(`Delete "${me.workspace.name}" and all its data forever?`)) return;
    await api('DELETE', '/api/workspace', { confirm_name: $('#del-name').value, password: $('#del-pw').value });
    toast('Workspace deleted');
    state.me = await api('GET', '/api/auth/me');
    if (!state.me.workspace) { state.me = null; state.authNote = 'Workspace deleted. Sign in or create a new workspace.'; await api('POST', '/api/auth/logout'); location.hash = '#/dashboard'; renderAuth('login'); return; }
    location.hash = '#/dashboard'; route();
  });
  $('#pw-go').onclick = guard(async () => {
    await api('POST', '/api/auth/password', { current: $('#pw-cur').value, new: $('#pw-new').value });
    $('#pw-cur').value = ''; $('#pw-new').value = ''; toast('Password changed');
  });
};

route();

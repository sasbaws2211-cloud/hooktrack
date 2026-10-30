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
// a UTC timestamp read in the WORKSPACE time zone, so a schedule means the same thing whatever the browser's zone is
const zoneParts = iso => {
  try {
    const f = new Intl.DateTimeFormat('en-GB', { timeZone: wsTz(), year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(new Date(iso));
    const g = t => (f.find(x => x.type === t) || {}).value || '';
    return { date: `${g('year')}-${g('month')}-${g('day')}`, time: `${g('hour') === '24' ? '00' : g('hour')}:${g('minute')}` };
  } catch { return null; }
};
const fmtWhen = iso => {
  if (!iso) return '';
  try { return new Intl.DateTimeFormat('en-GB', { timeZone: wsTz(), weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso)); }
  catch { return String(iso).slice(0, 16); }
};
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
  if (r.status === 402 && data.code === 'trial_ended') {
    refreshTrialBar(true);
    if (!location.hash.startsWith('#/billing')) setTimeout(() => { openPlanPicker().catch(() => (location.hash = '#/billing')); }, 600);
  }
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

/* In-app confirmation. Native await askConfirm()/prompt() are unreliable: some embedded browsers (including the Claude app's
   pane) suppress them and return false, so a "Delete" would silently do nothing. These return a Promise. */
function askConfirm(message, opts = {}) {
  return new Promise(resolve => {
    let settled = false;
    const finish = v => { if (!settled) { settled = true; obs.disconnect(); resolve(v); } };
    const m = openModal(`<h2>${esc(opts.title || 'Please confirm')}</h2>
      <p style="white-space:pre-wrap;margin:6px 0 14px">${esc(message)}</p>
      <div class="actions"><span class="spacer"></span><button class="btn" data-close type="button" id="ask-no">Cancel</button>
        <button class="btn ${opts.danger === false ? 'primary' : 'danger'}" type="button" id="ask-yes">${esc(opts.ok || 'Confirm')}</button></div>`);
    const obs = new MutationObserver(() => { if (!document.body.contains(m)) finish(false); });   // Escape, Cancel or a click outside
    obs.observe(document.body, { childList: true, subtree: true });
    $('#ask-yes', m).onclick = () => { finish(true); m.close(); };
    $('#ask-yes', m).focus();
  });
}

function askText(message, opts = {}) {
  return new Promise(resolve => {
    let settled = false;
    const finish = v => { if (!settled) { settled = true; obs.disconnect(); resolve(v); } };
    const m = openModal(`<h2>${esc(opts.title || 'Enter a value')}</h2>
      <p style="white-space:pre-wrap;margin:6px 0 10px">${esc(message)}</p>
      <input id="ask-in" maxlength="120" value="${esc(opts.value || '')}" placeholder="${esc(opts.placeholder || '')}" style="width:100%">
      <div class="actions"><span class="spacer"></span><button class="btn" data-close type="button">Cancel</button>
        <button class="btn primary" type="button" id="ask-ok">${esc(opts.ok || 'OK')}</button></div>`);
    const obs = new MutationObserver(() => { if (!document.body.contains(m)) finish(null); });
    obs.observe(document.body, { childList: true, subtree: true });
    const go = () => { finish($('#ask-in', m).value); m.close(); };
    $('#ask-ok', m).onclick = go;
    $('#ask-in', m).addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
    $('#ask-in', m).focus();
  });
}

/* ---------------- one-time onboarding tips (P2) ---------------- */
const TIPS = [
  { id: 'home', hash: 'dashboard', title: 'Your home base', body: 'Finish these steps to unlock Scale / Tweak / Stop insights. You can also load sample data to explore first.' },
  { id: 'views', hash: 'views', title: 'Log views here', body: 'Type weekly view counts, or use AI screenshot entry if AI is on. This feeds the dashboard.' },
  { id: 'hooks', hash: 'hooks', title: 'Your hooks, scored for you', body: 'Each posted video gets a score out of 30 from its real results, and a Scale / Tweak / Hold / Stop call. Press Score hooks to refresh them.' },
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
      <div class="g-item"><b>${decisionLabel('MUTATE')}</b><p>Above or below its account's normal, but not a winner: strengthen it or rework the first 3 seconds. Hold means about normal.</p></div>
      <div class="g-item"><b>${decisionLabel('KILL')}</b><p>Weak again and again (2 of its last 3 posts) — stop this angle or rebuild the content.</p></div>
      <div class="g-item"><b>Posts board</b><p>Idea → Scripted → Filmed → Posted → Analysed. Drag cards between stages. Calendar is the same posts by date.</p></div>
      <div class="g-item"><b>Views</b><p>Daily/weekly view counts per account. Pulled in automatically from your connected handles, or typed in by hand.</p></div>
      <div class="g-item"><b>Add-ons</b><p>Optional: AI help, and publishing to your connected accounts. Core tracking works without them.</p></div>
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
  const per = pf.reach_multiple != null && pf.growth_note
    ? `<div class="small muted" title="${esc(pf.growth_note)}">per follower ${pf.reach_multiple}×</div>` : '';
  return `<span class="pill ${cls}" title="${esc(tip)}">${star}${esc(text)}</span>${per}${pf.high_intent ? ' <span class="chip-intent" title="People saved, shared or commented far more than usual for this account">intent</span>' : ''}`;
};

const decisionSource = p => {
  const b = p.decision_basis || '';
  if (b.startsWith('Measured') && p.perf && p.perf.confidence) {
    return `measured · ${p.perf.confidence} confidence${p.perf.settled === false ? ' · may still move' : ''}`;
  }
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

// Moving through pages quickly must not start the same eight requests again for each one: share the load in flight.
let baseInFlight = null;
function loadBase() {
  if (!baseInFlight) baseInFlight = loadBaseNow().finally(() => { baseInFlight = null; });
  return baseInFlight;
}
async function loadBaseNow() {
  [state.accounts, state.phones, state.settings, state.ai, state.sv, state.publish, state.queued, state.clientList] = await Promise.all([
    api('GET', '/api/accounts'), api('GET', '/api/phones'), api('GET', '/api/settings'),
    api('GET', '/api/ai/status'),
    api('GET', '/api/connectors/sociavault').catch(() => ({ configured: false })),
    api('GET', '/api/addons/publish').catch(() => ({ enabled: false })),
    api('GET', '/api/publish/queued-count').catch(() => ({ count: 0 })),
    api('GET', '/api/clients').catch(() => ({ clients: [] })),
  ]);
  state.clients = (state.clientList && state.clientList.clients) || [];
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
  b.title = state.ai.available ? `Model: ${state.ai.model}` : 'AI is not set up on this server yet';
  state.baseLoaded = true;
  updateNav();
}
const isAdmin = () => state.me && ['owner', 'admin'].includes(state.me.workspace.role);
const aiNotice = () => (state.ai.available ? '' :
  `<div class="notice">AI is not set up on this server yet. Manual entry works without it.</div>`);

/* ---------------- router ---------------- */
const pages = {};
/* The pages that used to be top-level tabs live under their parent as a visible strip. */
function sectionStrip(tab) {
  const app = $('#app'); if (!app) return;
  const old = $('#section-strip'); if (old) old.remove();
  const q = state.queued || {};
  const n = (q.count || 0) + (q.attention || 0);
  let label = '', links = null;
  if (['dashboard', 'accounts', 'today', 'views'].includes(tab)) {
    label = 'Log views';
    links = [['today', 'Today', '#/today'], ['views', 'This week', '#/views']];
  } else if (['pipeline', 'calendar', 'scheduled'].includes(tab)) {
    label = 'Posts';
    links = [['pipeline', 'Board & calendar', '#/pipeline'], ['scheduled', n ? `Scheduled (${n})` : 'Scheduled', '#/scheduled']];
  } else if (['settings', 'billing'].includes(tab)) {
    label = 'Account';
    links = [['settings', 'Settings', '#/settings'], ['billing', 'Plan & billing', '#/billing']];
  }
  if (!links) return;
  const el = document.createElement('div');
  el.id = 'section-strip'; el.className = 'section-strip';
  el.innerHTML = `<span class="muted small">${esc(label)}</span>` + links.map(([k, t, h]) =>
    `<a href="${h}" class="${k === tab ? 'on' : ''}">${esc(t)}</a>`).join('');
  app.insertBefore(el, app.firstChild);
}

/* ---------------- page-change feedback ----------------
   Moving between pages waits on the server, and the old page used to just sit there until the new one replaced it in one
   jump. Now a thin progress bar starts at once, and a page-shaped placeholder appears if the wait is longer than a blink
   (fast loads show neither, so nothing flickers). A re-render of the page you are already on only gets the bar. */
let routeSeq = 0, lastRouteTab = null, loadingTab = null;
function loadBar() {
  let el = $('#loadbar');
  if (!el) { el = document.createElement('div'); el.id = 'loadbar'; el.hidden = true; document.body.appendChild(el); }
  return el;
}
function skeletonFor(tab) {
  const block = (h, w = '100%') => `<div class="skel" style="height:${h}px;width:${w}"></div>`;
  const cards = n => `<div class="grid kpis">${Array.from({ length: n }, () => `<div class="card">${block(12, '45%')}${block(30, '70%')}${block(10, '55%')}</div>`).join('')}</div>`;
  const rows = n => `<div class="card" style="margin-top:14px">${Array.from({ length: n }, () => `<div class="skel-row">${block(14, '22%')}${block(14, '38%')}${block(14, '14%')}${block(14, '12%')}</div>`).join('')}</div>`;
  const head = `<div class="page-head"><div>${block(30, '220px')}<div style="height:8px"></div>${block(12, '320px')}</div></div>`;
  if (tab === 'pipeline') return `<div class="skel-wrap" aria-busy="true">${head}<div class="pipeline">${Array.from({ length: 5 }, () => `<div class="pipe-col">${block(14, '50%')}<div style="height:10px"></div>${block(64)}<div style="height:8px"></div>${block(64)}</div>`).join('')}</div></div>`;
  if (tab === 'dashboard') return `<div class="skel-wrap" aria-busy="true">${head}${cards(4)}${rows(4)}</div>`;
  return `<div class="skel-wrap" aria-busy="true">${head}${rows(6)}</div>`;
}
function startRouteLoading(tab) {
  const my = ++routeSeq, navigating = tab !== lastRouteTab;
  loadingTab = tab;
  const bar = loadBar(); bar.hidden = false; bar.className = 'run';
  const app = $('#app');
  if (navigating && app) {
    setTimeout(() => {
      if (my !== routeSeq || bar.hidden) return;            // it finished (or another page change took over)
      app.classList.remove('page-in'); app.innerHTML = skeletonFor(tab);
    }, 180);
  }
  return my;
}
function endRouteLoading(my, tab) {
  if (my !== routeSeq) {                                       // a newer page change owns the bar now
    // this older page may have just drawn itself over the newer one's placeholder: put the placeholder back
    const stale = $('#app');
    if (loadingTab && stale) { stale.classList.remove('page-in'); stale.innerHTML = skeletonFor(loadingTab); }
    return;
  }
  loadingTab = null;
  const bar = loadBar(); bar.className = 'done';
  setTimeout(() => { if (my === routeSeq) { bar.hidden = true; bar.className = ''; } }, 260);
  lastRouteTab = tab;
  const app = $('#app');
  if (app) { app.classList.remove('page-in'); void app.offsetWidth; app.classList.add('page-in'); }
}

async function route() {
  const hash = location.hash.replace('#/', '');
  if (hash.startsWith('invite/')) return renderInvite(hash.slice(7));
  if (hash.startsWith('reset/')) return renderReset(hash.slice(6));
  if (hash.startsWith('verify/')) return renderVerify(hash.slice(7));
  if (hash.startsWith('digest-off/')) return renderDigestOff(hash.slice(11));
  if (hash.startsWith('hot-off/')) return renderDigestOff(hash.slice(8), 'hot');
  if (hash === 'forgot') return renderForgot();
  if (!state.me) {
    try { state.me = await api('GET', '/api/auth/me'); } catch { state.me = null; }
    if (!state.me) {
      if (hash === '') { location.replace('/welcome'); return; }       // a stranger at the root sees the landing page
      return renderAuth(hash === 'signup' ? 'signup' : 'login');
    }
  }
  document.body.classList.remove('anon'); renderChrome();
  const tab = (hash || 'dashboard').split('?')[0];
  const page = pages[tab] || pages.dashboard;
  const NAV_HOME = { today: 'accounts', views: 'accounts', calendar: 'pipeline', scheduled: 'pipeline', billing: 'settings' };
  const navTab = NAV_HOME[tab] || (pages[tab] ? tab : 'dashboard');
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.tab === navTab));
  const myLoad = startRouteLoading(tab);
  try { await loadBase(); await page($('#app')); sectionStrip(tab); showCoachTip(); } catch (e) {
    if (state.me && myLoad === routeSeq) $('#app').innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p></div>`;
  } finally { endRouteLoading(myLoad, tab); }
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
  if (hash === 'pipeline') {
    e.preventDefault();
    postModal(null, { status: 'idea', account_id: state.pipeAccount || state.hookAccount || '' }, route);
  }
});


/* ---------------- chrome: workspace switcher, sign out ---------------- */
// A thin bar under the header while on a trial, and a red one once it has ended.
let trialBarAt = 0;
async function refreshTrialBar(force) {
  const bar = $('#trialbar');
  if (!bar) return;
  if (!force && Date.now() - trialBarAt < 60000) return;
  trialBarAt = Date.now();
  let b;
  try { b = await api('GET', '/api/billing'); } catch { return; }
  if (!b.enabled || !['trial', 'expired'].includes(b.state)) { bar.hidden = true; return; }
  const left = b.trial.days_left;
  const ended = b.state === 'expired';
  bar.hidden = false;
  bar.className = 'trialbar' + (ended ? ' ended' : left <= 2 ? ' soon' : '');
  bar.innerHTML = ended
    ? `<span><b>${b.subscription.lapsed ? 'Your plan has ended.' : b.trial.reason === 'trial_used' ? 'You have already used your free trial.' : 'Your free trial has ended.'}</b> You can view everything, but changes are paused until you choose a plan.</span> <button type="button" class="btn sm primary" data-plans>Choose a plan</button>`
    : `<span><b>Free trial:</b> ${left} day${left === 1 ? '' : 's'} left.</span> <button type="button" class="btn sm" data-plans>Choose a plan</button>`;
  bar.onclick = e => { if (e.target.closest('[data-plans]')) openPlanPicker().catch(err => toast(err.message, true)); };
}

function renderVerifyBar() {
  const bar = $('#verifybar');
  if (!bar) return;
  const me = state.me;
  if (!me || !me.verification_required || me.user.email_verified) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.className = 'trialbar soon';
  bar.innerHTML = `<span><b>Confirm your email.</b> We sent a link to ${esc(me.user.email)}. Syncing starts once you click it.</span>
    <button class="btn sm primary" id="verify-resend">Resend</button> <button class="btn sm" id="verify-done">I've confirmed</button>`;
  $('#verify-resend').onclick = guard(async () => { await api('POST', '/api/auth/resend-verification'); toast('Sent. Check your inbox and spam folder.'); });
  $('#verify-done').onclick = guard(async () => { state.me = await api('GET', '/api/auth/me'); renderVerifyBar();
    if (!state.me.user.email_verified) toast("Not confirmed yet: open the link in the email first", true); });
}

async function renderDigestOff(token, kind) {
  document.body.classList.add('anon');
  let ok = true, err = '';
  try { await api('POST', kind === 'hot' ? '/api/hot/unsubscribe' : '/api/digest/unsubscribe', { token }); } catch (e) { ok = false; err = e.message; }
  state.me = null;
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo">▲</span> HookTrack</div>
    ${ok ? `<h1>${kind === 'hot' ? 'Breakout alerts' : 'Weekly email'} switched off</h1><p class="muted">You will not get ${kind === 'hot' ? 'breakout alerts' : 'the weekly summary'} any more. You can turn it back on under Settings, Your account.</p>`
         : `<h1>Link not valid</h1><p class="muted">${esc(err)}</p>`}
    <a class="btn primary" href="#/dashboard">Go to HookTrack</a></div>`;
}

async function renderVerify(token) {
  document.body.classList.add('anon');
  let done = null, err = '';
  try { done = await api('POST', '/api/auth/verify', { token }); } catch (e) { err = e.message; }
  state.me = null;
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo">▲</span> HookTrack</div>
    ${done ? `<h1>Email confirmed</h1><p class="muted">${esc(done.email)} is confirmed. You can add accounts and sync now.</p>`
           : `<h1>Link not valid</h1><p class="muted">${esc(err)}</p>`}
    <a class="btn primary" href="#/dashboard">Go to HookTrack</a></div>`;
}

function renderChrome() {
  refreshTrialBar();
  renderVerifyBar();
  const sw = $('#ws-switch');
  sw.innerHTML = state.me.workspaces.map(w => `<option value="${w.id}" ${w.id === state.me.workspace.id ? 'selected' : ''}>${esc(w.name)}</option>`).join('')
    + '<option value="new">＋ New workspace…</option>';
  $('#user-email').textContent = state.me.user.email;
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
  $$('#nav a').forEach(a => { a.style.display = ''; });
  const hash = (location.hash.replace('#/', '') || 'dashboard').split('?')[0];
  // only judge once the account data is in; before that every gated tab looks "locked" and a refresh/bookmark would bounce

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
    const name = await askText('Name for the new workspace?', {title: 'New workspace', ok: 'Create'});
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
      <button class="btn primary">${mode === 'login' ? 'Sign in' : 'Create workspace'}</button>
      ${mode === 'signup' ? '<p class="small muted" style="margin:0">By creating a workspace you agree to the <a href="/terms" target="_blank" rel="noopener">Terms</a> and acknowledge the <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>.</p>' : ''}</form>
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
      { ok: s.has_views, title: 'Pull in your videos and views', body: 'Press Sync on Hooks: your posts and their views come in by themselves from your handles. You can also log views by hand.', href: '#/hooks', cta: 'Open hooks', need: s.has_accounts },
      { ok: s.has_scored_hook, title: 'See your first decisions', body: 'Once a few videos are in, each gets Scale / Tweak / Stop against its own account’s normal.', href: '#/hooks', cta: 'Open hooks', need: s.has_accounts },
      { ok: state.ai.available, title: 'AI help', body: state.ai.available ? 'On: screenshot reading, hook ideas and weekly plans.' : 'Not set up on this server yet. Manual entry works without it.', href: '#/settings', cta: 'Settings', optional: true },
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
      if (!await askConfirm('Remove all accounts, views, and posts in this workspace?')) return;
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
        <li><a href="#/settings">Settings</a> — connect your TikTok, Instagram and YouTube accounts to publish</li>
      </ul>
    </div>
    <button class="btn sm" id="next-dismiss">Dismiss</button>
  </div>` : ''}
  <div class="grid kpis">
    <div class="card kpi"><div class="label">Views this week${d.week_estimated ? ' <span title="Some days are estimated from your videos\' totals" class="muted">(~)</span>' : ''}</div><div class="value">${d.week_estimated ? '~' : ''}${fmt(d.week_views)}</div>
      <div class="delta" title="What your synced videos gained so far today">${d.today_views ? `${fmt(d.today_views)} so far today` : 'today not counted yet'}</div>
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
  <div class="card" id="next-card" style="margin-bottom:14px"><h2>Next actions</h2><div id="next-body" class="muted small">Loading…</div><div id="credit-foot"></div></div>
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
  api('GET', '/api/credits').then(c => {
    const el = $('#credit-foot'); if (!el) return;
    const ago = c.last_synced_at ? (() => { const h = Math.max(0, Math.round((Date.now() - new Date(c.last_synced_at).getTime()) / 36e5));
      return h < 1 ? 'less than an hour ago' : h < 48 ? h + ' h ago' : Math.round(h / 24) + ' days ago'; })() : 'never';
    const kinds = { sync: 'syncing', read: 'video reads', comments: 'comments', lookup: 'lookups' };
    const parts = Object.entries(c.by_kind || {}).map(([k, v]) => `${kinds[k] || k} ${v}`).join(' · ');
    const near = c.budget > 0 && c.month >= c.budget * 0.8;
    const foot = el;
    foot.className = 'small ' + (near ? '' : 'muted');
    foot.style.cssText = 'margin-top:10px;padding-top:8px;border-top:1px solid var(--border)';
    foot.innerHTML = `${c.auto_sync ? `Auto-sync is on (about every ${Math.round(c.interval_hours)} h)` : 'Auto-sync is off'} · last synced ${esc(ago)}<br>
      Credits used this month: <b${near ? ' style="color:var(--bad,#ef4444)"' : ''}>${c.month}${c.budget ? ' of ' + c.budget : ''}</b>${parts ? ` <span class="muted">(${esc(parts)})</span>` : ''}${near ? ' — close to your budget' : ''}`;
  }).catch(() => {});
  api('GET', '/api/next-actions').then(r => {
    const el = $('#next-body'); if (!el) return;
    const items = r.items || [];
    const pc = r.pace || {};
    const pace = pc.target > 0
      ? `<div style="margin:0 0 10px"><div class="row" style="justify-content:space-between"><b>This week: ${pc.done} of ${pc.target} filmed</b>
          <span class="muted small">${pc.done >= pc.target ? 'target reached' : (pc.target - pc.done) + ' to go'}</span></div>
          <div class="prog"><span style="width:${Math.min(100, Math.round(pc.done / pc.target * 100))}%"></span></div></div>`
      : '<p class="small muted" style="margin:0 0 8px">Set a weekly target in <a href="#/settings">Settings → Decision rules</a> to see your pace here.</p>';
    el.className = ''; el.innerHTML = pace + (items.length
      ? `<ul class="list">${items.map(x => `<li><span class="badge" style="min-width:26px;text-align:center">${x.count}</span>
          <a href="${x.href}" style="flex:1;${x.quiet ? 'opacity:.7' : ''}">${esc(x.text)}</a></li>`).join('')}</ul>`
      : '<p class="muted">Nothing is waiting on you. Sync to pull in new videos.</p>');
  }).catch(() => { const el = $('#next-body'); if (el) el.textContent = ''; });
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
        class="${srcClass(c.source)}" title="${srcTitle(c.source)}" aria-label="${esc(r.handle)} ${c.day}"></td>`).join('')}
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
            placeholder="${a.prev != null ? fmt(a.prev) : 'views'}" class="${srcClass(a.source)}" title="${srcTitle(a.source)}" aria-label="${esc(a.handle)} views on ${esc(day)}">
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
  // ---- the editor shows what this stage needs and tucks the rest away ----
  const STEPS = [['idea', 'Idea'], ['scripted', 'Scripted'], ['filmed', 'Filmed'], ['posted', 'Posted']];
  const stepOf = st => (st === 'analysed' ? 3 : Math.max(0, STEPS.findIndex(x => x[0] === st)));
  const STAGE_HELP = {
    idea: 'An idea. Write the hook and caption, then mark it as scripted.',
    scripted: 'Scripted. Film it, then add the video here when you have it.',
    filmed: 'Filmed and ready. Choose when it goes out.',
    posted: 'Posted. Paste the video’s link so its real results come back.',
    analysed: 'Analysed. This is how it did.',
  };
  const ctaButtons = (() => {
    if (!(post && post.id)) return '';
    if (post.external_id) {
      return POST_AS[post.decision] && !post.followup
        ? `<button class="btn primary" id="topost" title="Add this hook to Posts as ${POST_AS[post.decision]}">Post</button>` : '';
    }
    const on = !!(state.publish && state.publish.enabled);
    const media = !!(p.has_video_file || p.video_url || (state.publish && state.publish.config && state.publish.config.default_media_url));
    const held = !!(state.publish && state.publish.held);
    const why = held ? state.publish.held_message
      : !on ? 'Publishing is not set up on this server yet'
      : !media ? 'Attach a video first: move the card back to Scripted, upload it, then move it to Filmed' : 'Publish to social platforms';
    return `<button class="btn primary" id="dopub" ${normStatus(p.status) === 'filmed' ? '' : 'hidden'} ${on && media && !held ? '' : 'disabled'} title="${esc(why)}">📤 Publish</button>
      <button class="btn primary" id="filmsched" hidden ${on && !held ? '' : 'disabled'} title="The video is attached: mark this card Filmed and choose when it goes out">🎬 Mark filmed &amp; schedule</button>`;
  })();
  const st0 = normStatus(p.status);
  const steps = p.external_id ? '' : `<ol class="pm-steps" id="steps" aria-label="Where this post is">${STEPS.map(([k, l], i) =>
    `<li data-step="${i}" class="${i < stepOf(st0) ? 'done' : i === stepOf(st0) ? 'now' : ''}">${l}</li>`).join('')}</ol>`;
  const postedOnly = ['posted', 'analysed'].includes(st0) || !!p.external_id;
  const m = openModal(`<div class="pm-head"><h2>${post ? 'Edit post' : 'New post'}</h2><button class="pm-x" type="button" data-close aria-label="Close">×</button></div>
    ${steps}
    <div class="fields pm-core">
      <div class="full"><label class="f">Hook (first 3 seconds)</label><input id="hook" value="${esc(p.hook)}" placeholder="The line that stops the scroll"></div>
      <div class="full"><label class="f">Account</label><select id="account">${accountOptions(p.account_id, 'Choose…')}</select></div>
      <div class="full"><label class="f">Caption + hashtags</label><textarea id="caption">${esc(p.caption)}</textarea></div>
    </div>
    <div class="stage-panel" id="stage-panel">
      ${p.external_id ? '' : `<p class="stage-help" id="stage-help">${esc(STAGE_HELP[st0] || '')}</p>`}
      ${p.external_id ? '' : '<div class="row" style="gap:8px;margin:6px 0"><button class="btn primary" id="nextstage" type="button" hidden>Mark as scripted</button></div>'}
      <div id="vidupload" style="margin-top:6px"></div>
      <div id="vidstatus" class="small" style="margin-top:6px">${p.video_url ? `🎬 <a href="${esc(p.video_url)}" target="_blank" rel="noopener">Video ready</a>` : ''}</div>
      <div id="pubstatus" class="small" style="margin-top:4px">${p.external_post_id ? `📤 Published · ${esc(p.publish_platforms || '')} · id ${esc(p.external_post_id)}` : (p.scheduled_for ? `📅 Scheduled ${esc(fmtWhen(p.scheduled_for))} (${esc(wsTz())})` : '')}</div>
    ${p.external_id ? '' : `<div class="full" id="linkblock" ${['posted', 'analysed'].includes(normStatus(p.status)) || p.url || p.result_post_id ? '' : 'hidden'}><label for="posturl">Posted video link</label>
      <input id="posturl" type="url" inputmode="url" placeholder="https://www.tiktok.com/@you/video/…" value="${esc(p.url || '')}">
      ${!p.result_post_id && p.id ? '<div style="margin-top:6px"><button class="btn sm" id="findvid" type="button">🔎 Find it in synced videos</button></div>' : ''}
      <div class="small muted" style="margin-top:4px">${p.result_post_id
        ? '✓ Linked to the synced video: its results show on this card.'
        : 'Paste the full address of the video once it is posted. After the next sync this card shows how it really did. (Short vm.tiktok.com links cannot be matched: open the video and copy the address bar.)'}</div></div>`}
      <div class="row pm-cta" style="gap:8px;margin-top:10px;flex-wrap:wrap">${ctaButtons}</div>
    </div>
    <details class="pm-more" id="pm-more"><summary>More details</summary>
      <div class="fields">
        <div><label class="f">Status</label><select id="status">${PIPELINE.map(s => `<option value="${s}" ${st0 === s ? 'selected' : ''}>${PIPE_LABEL[s]}</option>`).join('')}</select></div>
        <div><label class="f">Due date (when you plan to post)</label><input type="date" id="date" value="${esc(p.post_date)}"></div>
        <div class="full"><label class="f">Video idea / format</label><input id="prompt" value="${esc(p.ai_prompt)}" placeholder="Slideshow 5 pages, urgent tone"></div>
        <div class="full"><label class="f">Text on screen</label><input id="body" value="${esc(p.body)}"></div>
        <div id="postedfields" class="full fields" style="padding:0" ${postedOnly ? '' : 'hidden'}>
          <div><label class="f">Views</label><input id="views" inputmode="numeric" value="${p.views || 0}"></div>
          <div><label class="f">Comments</label><input id="comments" inputmode="numeric" value="${p.comments || 0}"></div>
          <div class="full check"><input type="checkbox" id="intent" ${p.intent ? 'checked' : ''}><label for="intent">Intent: comments ask "what's the app?" / "where can I get this?"</label></div>
        </div>
      </div>
    </details>
    <details class="pm-more" id="pm-insights" ${p.external_id ? 'open' : ''}><summary>Insights</summary>
      <div id="signals" style="margin-top:10px"></div>
      <div id="baseline" style="margin-top:12px"></div>
    </details>
    <div class="actions">
      ${post ? `<details class="pm-menu"><summary class="btn" aria-label="More actions">⋯</summary><div class="pm-menu-list">
        ${post.id && !post.external_id ? '<button class="btn" id="dup" type="button" title="Duplicate as new idea">Duplicate</button>' : ''}
        <button class="btn danger" id="del" type="button">Delete</button></div></details>` : ''}
      <span class="spacer"></span><button class="btn primary" id="save">Save</button></div>`);
  m.querySelector('.modal').classList.add('post-editor');

  // what the platform and the audience have shown so far: early velocity + what people asked for
  const signals = async (fresh) => {
    const el = $('#signals', m);
    if (!el || !post || !post.id) return;
    // views by day and comments exist only for a video the sync pulled in; a card of your own has neither yet
    if (!post.external_id) { el.innerHTML = ''; return; }
    const pf = (fresh && fresh.perf) || post.perf || null;
    const parts = [];
    if (pf && pf.trajectory && pf.trajectory.length) {
      const mx = Math.max(...pf.trajectory.map(t => t[1]), 1);
      parts.push(`<div class="sig-row"><div class="sig-title">Views by day since posting</div>
        <div class="traj">${pf.trajectory.map(t => `<span style="height:${Math.max(6, Math.round(t[1] / mx * 100))}%" title="Day ${t[0]}: ${fmt(t[1])} views"><i>${t[0]}</i></span>`).join('')}</div>
        <div class="small muted">${pf.early_views != null ? `First day: ${fmt(pf.early_views)} views${pf.early_multiple != null ? ` · ${pf.early_multiple}× this account's usual (${fmt(pf.early_baseline)})` : ''}` : ''}${pf.label === 'HOT_START' ? ' · <b>🔥 fast start</b>' : ''}</div></div>`);
    }
    if (pf && pf.growth_note) {
      parts.push(`<div class="sig-row"><div class="sig-title">Audience size</div><div class="small">${esc(pf.growth_note)}</div>
        <div class="small muted">This is a second reading. The call above still compares views with the account's own normal.</div></div>`);
    }
    let c = null;
    try { c = await api('GET', `/api/posts/${post.id}/comments?kind=intent&limit=6`); } catch {}
    const read = c && c.summary && c.summary.comments_fetched != null;
    parts.push(`<div class="sig-row"><div class="sig-title">What people asked for</div>
      ${read ? `<div class="small"><b>${(c.summary.buy_comments || 0) + (c.summary.ask_comments || 0)}</b> of ${c.summary.comments_fetched} comments ask to buy it or how/where to get it
        <span class="muted">(${c.summary.buy_comments || 0} want it · ${c.summary.ask_comments || 0} ask · ${c.summary.more_comments || 0} want more)</span></div>
        ${c.comments.length ? `<ul class="sig-comments">${c.comments.map(x => `<li><span class="pill ${x.kind === 'buy' ? 'SCALE' : ''}">${x.kind === 'buy' ? 'wants it' : 'asks'}</span> ${esc(short(x.body, 140))}${x.likes ? ` <span class="muted small">♥ ${fmt(x.likes)}</span>` : ''}</li>`).join('')}</ul>` : '<div class="small muted">No buying or how-to questions in the comments read.</div>'}`
      : '<div class="small muted">Comments have not been read for this post.</div>'}
      <button class="btn sm" id="readc" type="button" ${state.sv && state.sv.configured === false ? 'disabled title="Not available: the platform has no SociaVault connection set up"' : ''}>${read ? 'Read more comments' : 'Read comments'} <span class="muted">(≈2 credits)</span></button></div>`);
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

  // The exact winning video as a baseline: what is said, how it is built, and clones made from it.
  const baselinePanel = async () => {
    const el = $('#baseline', m);
    if (!el || !post || !post.id) return;
    let b;
    try { b = await api('GET', `/api/posts/${post.id}/baseline`); } catch { return; }
    post._winner = !!b.is_winner; post._bl = b;
    uploadBlock();                                   // the winner's reference and its matching videos come from this lookup
    const bp = b.blueprint;
    const beats = bp && bp.beats ? bp.beats : [];
    const sig = (bp && bp.signals) || {};
    const clones = b.clones || [];
    const inspBlock = () => {
      const items = b.inspiration || [];
      return `<div class="sig-row"><div class="sig-title">Inspiration</div>
        ${items.length ? items.map(x => `<div class="small" data-insp-item="${x.id}" style="margin:6px 0 8px"><b>“${esc(x.hook || x.title || 'Untitled')}”</b>
          ${x.platform ? `<span class="muted"> · ${esc(x.platform)}</span>` : ''}
          ${x.notes ? `<div class="muted">${esc(short(x.notes, 160))}</div>` : ''}
          ${embedHtml(x.embed, x.source_url)}
          <button class="btn sm" type="button" data-insp-detach="${x.id}" style="margin-top:4px">Remove</button></div>`).join('')
          : '<div class="small muted">Nothing attached. Attach something that has the move you want to try, then make it yours.</div>'}
        <div class="row" style="margin-top:6px"><button class="btn sm" id="insp-add" type="button">+ Add inspiration</button></div></div>`;
    };
    const wireInsp = () => {
      (b.inspiration || []).forEach(x => { const box = $(`[data-insp-item="${x.id}"]`, el); if (box) wireEmbeds(box, x.embed); });
      $$('[data-insp-detach]', el).forEach(x => (x.onclick = guard(async () => {
        await api('DELETE', `/api/posts/${post.id}/inspiration/${x.dataset.inspDetach}`); await baselinePanel();
      })));
      const add = $('#insp-add', m);
      if (add) add.onclick = guard(async () => {
        const lib = await api('GET', '/api/inspiration');
        const have = new Set((b.inspiration || []).map(i => i.id));
        const pick = lib.filter(i => !have.has(i.id));
        const mm = openModal(`<h2>Attach inspiration</h2>${pick.length ? `<ul class="list">${pick.map(i => `<li>
            <span style="flex:1;min-width:0"><b>${esc(short(i.hook_text || i.title || 'Untitled', 70))}</b>
              <div class="small muted">${esc(i.platform || '')}${i.notes ? ' · ' + esc(short(i.notes, 60)) : ''}</div></span>
            <button class="btn sm primary" data-att="${i.id}" type="button">Attach</button></li>`).join('')}</ul>`
          : '<p class="muted">Nothing left to attach. Save references on the <a href="#/inspiration">Inspiration</a> page first.</p>'}
          <div class="actions"><button class="btn" data-close>Close</button></div>`);
        $$('[data-att]', mm).forEach(y => (y.onclick = guard(async () => {
          await api('POST', `/api/posts/${post.id}/inspiration`, { inspiration_id: +y.dataset.att });
          mm.close(); await baselinePanel();
        })));
      });
    };
    if (!post.external_id && !b.brief) {   // an own card with no hook behind it (say, from Inspiration): its brief is the inspiration
      el.innerHTML = inspBlock();
      wireInsp();
      return;
    }
    if (b.brief) {                         // a card made from a hook: its job is a brief, not a reading of its own video
      const br = b.brief;
      const scale = br.kind === 'scale';
      el.innerHTML = `<div class="sig-row"><div class="sig-title">Brief ${scale ? '<span class="pill SCALE">scale</span>' : (br.promoted ? '' : '<span class="pill MUTATE">tweak</span>')}</div>
        <div class="small" style="margin:0 0 6px"><b>${scale
          ? 'Make a new video that works like the winner: the same opening move and format, with a new subject.'
          : 'Rework this hook a little: keep what worked and change one thing (the first words, the first shot or the angle).'}</b></div>
        <div class="small"><b>Original hook:</b> “${esc(br.hook || '(no hook)')}”
          ${br.multiple != null ? `<span class="muted"> · ${fmt(br.views)} views · ${br.multiple}× its account's normal</span>` : ''}</div>
        ${br.spoken_hook ? `<div class="small"><b>First words:</b> “${esc(br.spoken_hook)}”</div>` : ''}
        ${br.sound ? `<div class="small"><b>Sound:</b> ${esc(br.sound)}</div>` : ''}
        ${br.format ? `<div class="small"><b>Format:</b> ${esc(br.format)}${br.duration_s ? ' · about ' + Math.round(br.duration_s) + ' s' : ''}</div>` : ''}
        ${br.structure ? `<div class="small"><b>Built like this:</b> ${esc(br.structure)}</div>` : ''}
        ${br.beats.length ? `<ol class="small" style="margin:4px 0 0 18px;padding:0">${br.beats.map(x => `<li><b>${esc(x.role || '')}</b>: ${esc(x.what || '')}</li>`).join('')}</ol>` : ''}
        ${br.read ? '' : '<div class="small muted" style="margin-top:6px">Its first words and structure have not been read yet: open the original on Hooks and press “Read the video” (about 1 credit).</div>'}
        ${scale ? '' : embedHtml(br.embed, br.source_url)}
        <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap">
          ${br.hook_id ? '<button class="btn sm" id="br-open" type="button">Open the original hook</button>' : ''}
          ${!scale && normStatus(post.status) === 'idea' && !post.parent_id ? '<button class="btn sm" id="br-variants" type="button" title="Three copies, each changing one thing: the first words, the first shot or the angle">Make 3 variants</button>' : ''}</div></div>`;
      el.innerHTML = `<div id="brief-part">${el.innerHTML}</div>` + inspBlock();
      if (!scale) wireEmbeds($('#brief-part', el), br.embed);
      wireInsp();
      const mv = $('#br-variants', m);
      if (mv) mv.onclick = guard(async () => {
        const r = await api('POST', `/api/posts/${post.id}/variants`);
        toast(r.created ? `Made ${r.variants.length} variants: ${r.variants.map(v => v.label).join(', ')}` : 'Variants already exist');
        m.close(); if (onDone) onDone();
      });
      const op = $('#br-open', m);
      if (op) op.onclick = guard(async () => { const h = await api('GET', `/api/posts/${br.hook_id}`); postModal(h, null, onDone || route); });
      return;
    }
    const chips = [
      sig.opens_with_question ? 'opens with a question' : '', sig.has_number_in_hook ? 'number in the hook' : '',
      sig.speaks_to_viewer ? 'speaks to "you"' : '', sig.call_to_action ? `ends with “${esc(sig.call_to_action)}”` : '',
      sig.words_per_second ? `${sig.words_per_second} words/s` : '',
    ].filter(Boolean);
    el.innerHTML = `<div class="sig-row"><div class="sig-title">Video baseline ${b.is_winner ? '<span class="pill SCALE">winner</span>' : ''}</div>
      ${b.skipped && !b.has_transcript ? `<div class="small muted">Marked as having no speech, so nothing was read and no credit was spent. The clones will work from the results, caption, sound and your note below.</div>`
        : b.read && !b.has_transcript ? `<div class="small muted">Read: <b>no speech was found</b> (music-only or silent). It will not be read again unless you ask. The clones will work from the results, caption, sound and your note below, so describe what is on screen.</div>`
        : !b.has_transcript ? `<div class="small muted">Read what is actually said in this video, so new videos can be matched to it. If it has no speech (music only), skip the read: it is free and you just describe it below.
          ${b.has_url ? '' : ' This post has no public link yet: sync it from the platform first.'}</div>` : `
        <div class="small"><b>First words${bp && bp.spoken_at > 3.5 ? ' (start at ' + bp.spoken_at + ' s)' : ''}:</b> ${b.spoken_hook ? `“${esc(b.spoken_hook)}”` : '<span class="muted">no speech found (music-only?). Add a note below.</span>'}</div>
        ${b.sound ? `<div class="small"><b>Sound:</b> ${esc(b.sound)} <span class="muted">(use the same one)</span></div>` : ''}
        ${chips.length ? `<div class="small muted" style="margin-top:4px">${chips.join(' · ')}</div>` : ''}
        ${bp && bp.structure ? `<div class="small" style="margin-top:6px"><b>Built like this:</b> ${esc(bp.structure)}</div>` : ''}
        ${beats.length ? `<ol class="small" style="margin:6px 0 0 18px;padding:0">${beats.map(x => `<li><span class="muted">${x.at == null ? '' : x.at + 's · '}</span>${esc(x.says)}</li>`).join('')}</ol>` : ''}`}
      <label class="f" style="margin-top:8px">What is on screen (your note, optional)</label>
      <textarea id="vnotes" rows="2" maxlength="1500" placeholder="e.g. creator walks into frame holding the product, text overlay says '3 things I stopped buying'">${esc(b.video_notes || '')}</textarea>
      <div class="row" style="gap:8px;margin-top:8px;flex-wrap:wrap">
        <button class="btn sm" id="bl-read" type="button" ${b.has_url && state.sv && state.sv.configured !== false ? '' : 'disabled'}
          title="${b.has_url ? 'Reads the transcript (about 1 SociaVault credit)' : 'No public link for this post'}">${b.has_transcript || b.read || b.skipped ? 'Read again (≈1 credit)' : 'Read the video (≈1 credit)'}</button>
        ${b.has_transcript || b.read || b.skipped ? '' : '<button class="btn sm" id="bl-skip" type="button" title="No speech in this video: build the baseline without reading it. Free.">No speech: skip (free)</button>'}
        <button class="btn sm" id="bl-notes" type="button">Save note</button>
</div>
      <div class="small muted" style="margin-top:6px">Use this on your own videos. For other people's, take the pattern, not their script.</div>
</div>`;
    $('#bl-read', m).onclick = guard(async () => {
      busy($('#bl-read', m), true, 'Reading…');
      let r;
      try { r = await api('POST', `/api/posts/${post.id}/baseline${b.has_transcript || b.read || b.skipped ? '?refresh=true' : ''}`); }
      finally { await baselinePanel(); }
      toast(r && r.no_speech ? 'Read: no speech found. Add a note describing what is on screen.' : 'Video read');
    });
    if ($('#bl-skip', m)) $('#bl-skip', m).onclick = guard(async () => {
      busy($('#bl-skip', m), true, 'Saving…');
      try { await api('POST', `/api/posts/${post.id}/baseline?skip_transcript=true`); }
      finally { await baselinePanel(); }
      toast('Marked as no speech. Add a note describing what is on screen.');
    });
    $('#bl-notes', m).onclick = guard(async () => {
      await api('PUT', `/api/posts/${post.id}/video-notes`, { notes: $('#vnotes', m).value }); toast('Note saved');
    });
  };
  baselinePanel();

  // A video you upload from your own computer: the alternative to one a provider generates.
  const sizeText = n => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');
  const VIDEO_EXT = /\.(mp4|mov|m4v|webm)$/i;
  const checkVideo = f => {
    if (!(String(f.type).startsWith('video/') || VIDEO_EXT.test(f.name || ''))) throw new Error('Drop an MP4, MOV or WebM video.');
    if (f.size > 300 * 1024 * 1024) throw new Error('That file is over 300 MB. Export a smaller one.');
  };
  const busyZone = text => { const z = $('#viddrop', m); if (z) { z.classList.add('busy'); z.querySelector('.dz-main').textContent = text; } };
  const isWinningHook = () => !!(post && (post._winner || (post.perf && post.perf.label === 'WINNER') || post.decision === 'SCALE'));
  const isMatching = () => !!(post && post.cloned_from);

  // a matching post's own video: the file is attached to THIS post
  const sendOwn = async f => {
    if (!f) return;
    checkVideo(f);
    if (post.video_url && !await askConfirm('This post already has a generated video. Uploading a file replaces it as the post\'s video. Continue?', {danger: false, ok: 'Replace it'})) return;
    busyZone('Uploading ' + (f.name || 'video') + ' · ' + sizeText(f.size) + '…');
    try {
      const fd = new FormData(); fd.append('file', f);
      const r = await api('POST', `/api/posts/${post.id}/video/upload`, fd);
      post.has_video_file = true; post.video_file_name = r.name; post.video_file_size = r.size; post.video_url = '';
      if (m._afterUpload) m._afterUpload();
      const vs = $('#vidstatus', m); if (vs) vs.innerHTML = '';
      toast('Matching video uploaded');
      if (onDone) onDone();
    } finally { uploadBlock(); }
  };

  // dropped on a winning hook: a NEW video that matches it, saved as its own post linked to the winner
  const sendMatching = async f => {
    if (!f) return;
    checkVideo(f);
    busyZone('Uploading ' + (f.name || 'video') + ' · ' + sizeText(f.size) + '…');
    try {
      const fd = new FormData(); fd.append('file', f);
      const r = await api('POST', `/api/posts/${post.id}/matching-video`, fd);
      toast(`Matching video added as ${r.label}. It is under Posts, ready to edit and schedule.`);
      if (onDone) onDone();
    } finally { await baselinePanel(); }
  };


  // the original, played in the page. Nothing from the platform is loaded until the viewer presses play.
  const embedHtml = (embed, srcUrl) => embed ? `<div class="embed" data-embed>
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
        <button class="btn sm primary" type="button" data-play>▶ Play the original here</button>
        ${srcUrl ? `<a class="small" href="${esc(srcUrl)}" target="_blank" rel="noopener">Open on ${esc(embed.label)} ↗</a>` : ''}</div>
      <div class="small muted" style="margin-top:4px">The video loads from ${esc(embed.label)} only when you press play, and ${esc(embed.label)} may set its own cookies. If the player shows an error, ${esc(embed.label)} is limiting embeds right now: use “Open on ${esc(embed.label)}”.</div>
      <div class="embed-frame" hidden></div></div>`
    : (srcUrl ? `<div class="small"><a href="${esc(srcUrl)}" target="_blank" rel="noopener">Open the original ↗</a></div>` : '');
  const wireEmbeds = (root, embed) => $$('[data-embed]', root).forEach(box => {
    const play = box.querySelector('[data-play]'), frame = box.querySelector('.embed-frame');
    if (!play || !frame || !embed) return;
    play.onclick = () => {
      if (!frame.hidden) { frame.hidden = true; frame.innerHTML = ''; play.textContent = '▶ Play the original here'; return; }
      const wrap = document.createElement('div');
      wrap.className = 'embed-wrap';
      if (embed.max_width) wrap.style.maxWidth = embed.max_width + 'px';
      const f = document.createElement('iframe');
      f.src = embed.src;                                   // built by the server from a validated ID on the platform's embed route
      f.title = 'Original winning hook video (' + embed.label + ')';
      f.setAttribute('allow', 'autoplay; encrypted-media; picture-in-picture; fullscreen');
      f.setAttribute('allowfullscreen', '');
      f.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
      f.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox');
      f.style.cssText = 'width:100%;border:0;border-radius:10px;background:#000;' +
        (embed.aspect ? 'aspect-ratio:' + embed.aspect + ';' : 'height:' + (embed.height || 600) + 'px;');
      wrap.appendChild(f);
      frame.innerHTML = ''; frame.appendChild(wrap); frame.hidden = false;
      play.textContent = '✕ Close the player';
    };
  });
  const statusOf = c => (c.has_file ? `uploaded${c.video_file_name ? ': ' + esc(short(c.video_file_name, 24)) : ''}`
    : c.has_url || c.job_status === 'done' ? 'generated'
    : ['pending', 'running', 'processing', 'queued'].includes(c.job_status) ? 'rendering…'
    : c.job_status === 'failed' ? 'render failed' : 'script only');
  const zoneHtml = (main, sub, compact) => `<div id="viddrop" class="dropzone ${compact ? 'compact' : ''}" role="button" tabindex="0" aria-label="${esc(main)}">
      <div class="dz-main">${esc(main)}</div><div class="dz-sub">${esc(sub)}</div></div>
      <input type="file" id="vidfile" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.m4v,.webm" hidden>`;
  const fileCard = () => `<div class="small"><b>${esc(post.video_file_name || 'video')}</b> <span class="muted">· ${sizeText(post.video_file_size || 0)}</span></div>
      <video controls preload="metadata" playsinline src="/api/posts/${post.id}/video/file" style="width:100%;max-height:260px;margin-top:6px;border-radius:10px;background:#000"></video>`;

  const wireZone = (send) => {
    const zone = $('#viddrop', m), input = $('#vidfile', m);
    if (!zone || !input) return;
    const choose = () => input.click();
    zone.onclick = choose;
    zone.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(); } };
    const pick = $('#vidpick', m); if (pick) pick.onclick = choose;
    input.onchange = guard(async e => { const f = e.target.files && e.target.files[0]; e.target.value = ''; await send(f); });
    const hasFiles = e => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
    zone.addEventListener('dragenter', e => { if (hasFiles(e)) { e.preventDefault(); zone.classList.add('over'); } });
    zone.addEventListener('dragover', e => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; zone.classList.add('over'); } });
    zone.addEventListener('dragleave', e => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('over'); });
    zone.addEventListener('drop', guard(async e => {
      e.preventDefault(); e.stopPropagation();
      zone.classList.remove('over');
      const files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      if (files.length > 1) toast('One video at a time: using ' + files[0].name);
      await send(files[0]);
    }));
  };
  const wireRemove = () => {
    const rm = $('#vidrm', m);
    if (rm) rm.onclick = guard(async () => {
      if (!await askConfirm('Remove the video file from this post?')) return;
      await api('DELETE', `/api/posts/${post.id}/video/file`);
      post.has_video_file = false; post.video_file_name = ''; post.video_file_size = 0;
      toast('Video removed'); uploadBlock(); if (onDone) onDone();
    });
  };


  const uploadBlock = () => {
    const el = $('#vidupload', m);
    if (!el) return;
    // a card that is already posted (or analysed) is past the filming step: no matching-video section on it
    if (post && post.id && !post.external_id && ['posted', 'analysed'].includes($('#status', m) ? $('#status', m).value : normStatus(post.status))) { el.innerHTML = ''; return; }
    const has = !!(post && post.id && post.has_video_file);
    // a Filmed card keeps the video it was filmed with: uploading and drag and drop are off until it is back on Scripted
    if (post && post.id && !post.external_id && ($('#status', m) ? $('#status', m).value : normStatus(post.status)) === 'filmed') {
      el.innerHTML = `<div class="sig-row"><div class="sig-title">Video</div>
        ${has ? fileCard() : '<div class="small muted">No video file is attached to this card.</div>'}
        <div class="small muted" style="margin-top:6px">🔒 Upload and drag and drop are disabled on a Filmed card. To add or change the video, move the card back to Scripted.</div></div>`;
      return;
    }
    const bl = (post && post._bl) || {};
    const matching = isMatching(), winner = isWinningHook();
    if (!winner && !matching && !has) { el.innerHTML = ''; return; }        // only winning hooks and their matches have this

    if (winner && !matching) {
      const clones = bl.clones || [];
      const mult = post.perf && post.perf.multiple;
      el.innerHTML = `<div class="sig-row"><div class="sig-title">Winning Hook Video</div>
        <div class="small" style="margin:0 0 8px">This is the video that proved the hook converts. Press <b>Post</b> to send it to Posts and make a new video that matches it.</div>
        ${mult ? `<div class="small muted">${mult}× this account's normal</div>` : ''}
        ${embedHtml(bl.embed, bl.source_url)}
        ${bl.spoken_hook ? `<div class="small" style="margin-top:4px"><b>First words:</b> “${esc(bl.spoken_hook)}”</div>` : ''}
        ${bl.sound ? `<div class="small"><b>Sound:</b> ${esc(bl.sound)}</div>` : ''}
        ${has ? `<div style="margin-top:8px">${fileCard()}<div class="row" style="margin-top:6px"><button class="btn sm danger" id="vidrm" type="button">Remove this file</button></div></div>` : ''}</div>`;
      wireRemove();
      wireEmbeds(el, bl.embed);
      return;
    }

    // a matching post (or an older post that already holds a file): the video belongs to THIS post
    const parent = bl.parent;
    el.innerHTML = `<div class="sig-row"><div class="sig-title">${matching ? 'Matching video' : 'Video file'}</div>
      <div class="small" style="margin:0 0 8px">${matching
        ? `${parent ? `Matches the winning hook video “${esc(short(parent.hook || '', 80))}”. ` : ''}Upload your new matching video here.`
        : 'The video file attached to this post.'}</div>
      ${matching && parent ? `<div style="margin:0 0 8px">${embedHtml(parent.embed, parent.source_url)}</div>` : ''}
      ${has ? fileCard() : ''}
      ${zoneHtml(has ? 'Drop a new video here to replace it, or click to choose' : 'Drag and drop your matching video here, or click to choose a file',
                 'MP4, MOV or WebM · up to 300 MB · stays private to this workspace', has)}
      <div class="row" style="gap:8px;margin-top:8px;flex-wrap:wrap">
        <button class="btn sm" id="vidpick" type="button">${has ? 'Replace file' : '⬆ Upload a video'}</button>
        ${has ? '<button class="btn sm danger" id="vidrm" type="button">Remove file</button>' : ''}</div></div>`;
    wireZone(sendOwn);
    wireRemove();
    if (matching && parent) wireEmbeds(el, parent.embed);
  };
  // a file dropped anywhere else in the editor must not make the browser navigate away to it
  const dropElsewhere = e => {
    if (!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files'))) return;
    if (e.target && e.target.closest && e.target.closest('#viddrop')) return;
    e.preventDefault();
    if (e.type === 'drop') {
      toast($('#viddrop', m) ? 'Drop the video on the highlighted box.'
            : post && !post.external_id && $('#status', m) && $('#status', m).value === 'filmed' ? 'Uploads are disabled on a Filmed card.'
            : 'Videos are added on your own cards. Send this hook to Posts first.', true);
    }
  };
  m.addEventListener('dragover', dropElsewhere);
  m.addEventListener('drop', dropElsewhere);
  uploadBlock();
  if ($('#status', m)) $('#status', m).addEventListener('change', () => {
    uploadBlock();
    const pb = $('#dopub', m);                       // publishing and scheduling happen from a Filmed card
    if (pb) pb.hidden = $('#status', m).value !== 'filmed';
  });

  const body = () => {
    const num = id => { const n = parseCount($('#' + id, m).value || '0'); return Number.isNaN(n) ? 0 : n; };
    return { account_id: $('#account', m).value ? +$('#account', m).value : null, post_date: $('#date', m).value, status: $('#status', m).value,
      ai_prompt: $('#prompt', m).value, hook: $('#hook', m).value, body: $('#body', m).value, caption: $('#caption', m).value,
      views: num('views'), comments: num('comments'), intent: $('#intent', m).checked,
      ...($('#posturl', m) ? { url: $('#posturl', m).value.trim() } : {}) };
  };
  const save = async () => {
    const b = body();
    if (!b.post_date) throw new Error('Pick a date');
    if (post) { Object.assign(post, await api('PATCH', `/api/posts/${post.id}`, b)); }
    else {
      post = await api('POST', '/api/posts', b);
      if (b.url) post = await api('PATCH', `/api/posts/${post.id}`, { url: b.url });
    }
    return post;
  };
  $('#save', m).onclick = guard(async () => { await save(); m.close(); toast('Saved'); onDone && onDone(); });
  
  if ($('#dup', m)) $('#dup', m).onclick = guard(async () => {
    const r = await api('POST', `/api/posts/${post.id}/duplicate`);
    toast('Duplicated as idea');
    m.close();
    postModal(r, null, onDone || route);
  });
  if ($('#findvid', m)) $('#findvid', m).onclick = () => findVideoModal(post, () => { m.close(); onDone && onDone(); });
  if ($('#topost', m)) $('#topost', m).onclick = guard(async () => {
    if (state.accounts.length > 1) { sendHookModal(post, () => { m.close(); onDone && onDone(); }); return; }
    const r = await api('POST', `/api/posts/${post.id}/followup`);
    toast(r.created ? `Added to Posts as ${PIPE_LABEL[r.status] || r.status}` : 'Already in Posts');
    m.close(); onDone && onDone();
  });
  // keep the step row, the one-line help and the stage-only fields in step with the Status choice
  const refreshStage = () => {
    const st = $('#status', m) ? $('#status', m).value : normStatus(post && post.status);
    const si = stepOf(st);
    $$('#steps li', m).forEach((li, i) => { li.className = i < si ? 'done' : i === si ? 'now' : ''; });
    const help = $('#stage-help', m); if (help) help.textContent = STAGE_HELP[st] || '';
    const pf = $('#postedfields', m); if (pf) pf.hidden = !(['posted', 'analysed'].includes(st) || (post && post.external_id));
    const ns = $('#nextstage', m); if (ns) ns.hidden = !(st === 'idea' && post && post.id);
  };
  refreshStage();
  if ($('#status', m)) $('#status', m).addEventListener('change', refreshStage);
  if ($('#nextstage', m)) $('#nextstage', m).onclick = guard(async () => {
    $('#status', m).value = 'scripted';
    $('#status', m).dispatchEvent(new Event('change'));
    await save();
    toast('Marked as scripted');
    if (onDone) onDone();
  });
  const hasVideo = () => !!(post && (post.has_video_file || post.video_url));
  const refreshFilmSched = () => {
    const fs = $('#filmsched', m); if (!fs) return;
    const st = $('#status', m) ? $('#status', m).value : normStatus(post && post.status);
    fs.hidden = !(post && post.id && ['idea', 'scripted'].includes(st) && hasVideo());
  };
  refreshFilmSched();
  if ($('#status', m)) $('#status', m).addEventListener('change', refreshFilmSched);
  m._afterUpload = refreshFilmSched;
  // nothing is posted yet on an Idea, Scripted or Filmed card, so there is no video to link or find: the link
  // field and the match button appear only once the card is Posted (or Analysed)
  const refreshLinkBlock = () => {
    const lb = $('#linkblock', m); if (!lb) return;
    const st = $('#status', m) ? $('#status', m).value : normStatus(post && post.status);
    lb.hidden = !(['posted', 'analysed'].includes(st) || (post && (post.url || post.result_post_id)));
  };
  if ($('#status', m)) $('#status', m).addEventListener('change', refreshLinkBlock);
  const startPublish = async () => {
    await save();
    scheduleDialog(post, {
      onPublished: job => {
        const el = $('#pubstatus', m);
        if (job.status === 'queued') {
          if (el) el.textContent = `📅 Scheduled ${fmtWhen(job.scheduled_for)} (${wsTz()})`;
          toast('Scheduled: it will publish automatically');
        } else if (job.status === 'done' || job.status === 'scheduled') {
          if (el) el.textContent = `📤 ${job.status} · id ${job.external_id || ''}`;
          const st = $('#status', m); if (st) st.value = 'posted';
          post.status = 'posted';
          post.external_post_id = job.external_id || '';
          refreshLinkBlock();
          toast(job.status === 'scheduled' ? 'Scheduled at provider' : 'Published');
        } else {
          toast(job.error || 'Publish failed', true);
        }
        if (onDone) onDone();
      },
    });
  };
  if ($('#dopub', m)) $('#dopub', m).onclick = guard(startPublish);
  if ($('#filmsched', m)) $('#filmsched', m).onclick = guard(async () => {
    if ($('#status', m)) $('#status', m).value = 'filmed';
    const pb = $('#dopub', m); if (pb) pb.hidden = false;
    await startPublish();
  });

if (post) $('#del', m).onclick = guard(async () => {
    if (!await askConfirm('Delete this post?')) return;
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



/* ---------------- publish / schedule dialog ---------------- */
const PLATFORM_LABEL = { tiktok: 'TikTok', instagram: 'Instagram', youtube: 'YouTube' };
// mode 'publish': choose platforms and now/later. mode 'reschedule': move a queued publish to a new time.
async function scheduleDialog(post, opts = {}) {
  const resched = opts.mode === 'reschedule';
  const defaults = (state.publish && state.publish.config && state.publish.config.default_platforms) || ['tiktok'];
  const acct = state.accounts.find(a => a.id === post.account_id);
  const own = acct && String(acct.platform || '').toLowerCase();
  const chosen = new Set(own && PLATFORM_LABEL[own] ? [own] : (Array.isArray(defaults) ? defaults : [String(defaults)]));
  const zone = wsTz();
  const sm = openModal(`<h2>${resched ? 'Move to a new time' : 'Publish / schedule'}</h2>
    ${resched ? '' : `<div class="fields"><div class="full"><label class="f">Publish to</label>
      <div class="row" style="gap:12px;flex-wrap:wrap;align-items:center" id="plats"></div>
      <div class="small" id="connnote" style="margin-top:6px"></div></div></div>`}
    <div class="fields">
      ${resched ? '' : '<div class="full"><label class="f"><input type="radio" name="when" value="now"> Publish now</label></div>'}
      <div class="full">${resched ? '' : '<label class="f"><input type="radio" name="when" value="later" checked> Schedule for</label>'}
        <div class="chips" id="slots"><span class="muted small">Finding free times…</span></div>
        <input type="datetime-local" id="sched" value="" style="margin-top:6px">
        <div class="small muted" style="margin-top:4px">Time zone: ${esc(zone)} (your workspace setting). Suggested times are free slots for this account.</div></div>
    </div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="pubgo">${resched ? 'Move' : 'Go'}</button></div>`, true);
  // which platforms are connected: a platform with no account is offered a Connect button right here, and
  // after signing in on the platform the user lands back on this card with the dialog open again
  const managed = !!(state.publish && state.publish.managed_available);
  const held = !!(state.publish && state.publish.held);
  let connected = null;                                    // null = not known (own provider keys, or still loading)
  const goBtn = () => $('#pubgo', sm);
  const renderPlats = () => {
    const box = $('#plats', sm); if (!box) return;
    box.innerHTML = Object.entries(PLATFORM_LABEL).map(([k, v]) => {
      const ok = connected === null || connected.has(k);
      return `<span class="row" style="gap:6px"><label class="check small"><input type="checkbox" data-plat="${k}" ${ok && chosen.has(k) ? 'checked' : ''} ${ok ? '' : 'disabled'}> ${v}</label>${ok ? '' :
        (isAdmin() ? `<button type="button" class="btn sm" data-conn="${k}" ${held ? 'disabled title="Unlocks with a paid plan"' : ''}>Connect ${v}</button>` : '<span class="muted small">not connected: ask an admin</span>')}</span>`;
    }).join('');
    const sel = () => $$('[data-plat]', box).filter(c => c.checked && !c.disabled);
    const note = $('#connnote', sm);
    const wanted = [...chosen].filter(k => connected && !connected.has(k));
    if (note) note.innerHTML = wanted.length
      ? `<span style="color:var(--bad, #ef4444)">${wanted.map(k => PLATFORM_LABEL[k]).join(', ')} is not connected yet.</span> Connect it to publish this card: you will sign in on the platform and come straight back here.`
      : '';
    const sync = () => { const g = goBtn(); if (g) g.disabled = !sel().length; };
    $$('[data-plat]', box).forEach(c => c.onchange = sync);
    sync();
    $$('[data-conn]', box).forEach(b => b.onclick = guard(async () => {
      busy(b, true, 'Opening…');
      try {
        try { sessionStorage.setItem('ht_resume_publish', JSON.stringify({ id: post.id, platform: b.dataset.conn })); } catch (e) { /* ignored */ }
        const r2 = await api('POST', '/api/publish/managed/connect', { platform: b.dataset.conn, return_to: '#/pipeline' });
        location.href = r2.auth_url;
      } finally { busy(b, false); }
    }));
  };
  if (!resched) {
    renderPlats();
    if (managed) {
      if ($('#pubgo', sm)) $('#pubgo', sm).disabled = true;      // until we know what is connected
      api('GET', '/api/publish/managed').then(r => {
        if (r.error) { connected = null; } else { connected = new Set((r.accounts || []).map(a => String(a.platform).toLowerCase())); }
        renderPlats();
      }).catch(() => { connected = null; renderPlats(); });
    }
  }
  // the dialog is usable at once; free-slot suggestions fill in when they arrive
  api('GET', `/api/publish/suggest-times?account_id=${post.account_id || ''}&n=4`).then(r => {
    const box = $('#slots', sm); if (!box) return;
    const slots = r.slots || [];
    box.innerHTML = slots.map(x => `<button type="button" class="btn sm" data-slot="${esc(x.local)}">${esc(x.label)}</button>`).join('');
    if (slots[0] && !$('#sched', sm).value) $('#sched', sm).value = slots[0].local;
    $$('[data-slot]', box).forEach(b => b.onclick = () => {
      $('#sched', sm).value = b.dataset.slot;
      const rd = $$('input[name=when]', sm).find(x => x.value === 'later'); if (rd) rd.checked = true;
    });
  }).catch(() => { const box = $('#slots', sm); if (box) box.innerHTML = ''; });
  $('#pubgo', sm).onclick = guard(async () => {
    const later = resched || $$('input[name=when]', sm).find(r => r.checked)?.value === 'later';
    const local = $('#sched', sm).value;
    if (later) {
      if (!local) throw new Error('Pick a date and time');
      const z = zoneParts(new Date().toISOString());
      if (z && local <= `${z.date}T${z.time}`) throw new Error('Schedule time must be in the future');
    }
    const btn = $('#pubgo', sm);
    busy(btn, true, resched ? 'Moving…' : later ? 'Scheduling…' : 'Publishing…');
    try {
      if (resched) {
        const job = await api('POST', `/api/posts/${post.id}/reschedule`, { schedule_local: local });
        sm.close(); toast(`Moved to ${fmtWhen(job.scheduled_for)}`); if (opts.onMoved) opts.onMoved(job);
        return;
      }
      const platforms = $$('[data-plat]', sm).filter(c => c.checked && !c.disabled).map(c => c.dataset.plat);
      if (!platforms.length) throw new Error('Choose at least one platform');
      const body = { platforms };
      if (later) body.schedule_local = local;
      const job = await api('POST', `/api/posts/${post.id}/publish`, body);
      sm.close();
      if (opts.onPublished) opts.onPublished(job);
    } finally { busy(btn, false); }
  });
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
    <div class="sub">Posts queued to publish later, in your workspace time zone (${esc(wsTz())}). HookTrack sends them to your provider when due (every ~45s).</div></div>
    <a class="btn" href="#/pipeline">Pipeline</a></div>
    ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>Account</th><th>Hook</th><th>Status</th><th>Provider</th><th></th></tr></thead><tbody>
      ${rows.map(r => `<tr>
        <td title="${esc(wsTz())}">${esc(fmtWhen(r.scheduled_for))}</td>
        <td>${esc(r.handle || '—')}</td>
        <td class="wrap">${esc(short(r.hook || r.caption || '', 50))}</td>
        <td>${pill(r.status)}</td>
        <td class="muted">${esc(r.provider || '')}</td>
        <td class="row" style="gap:4px;flex-wrap:nowrap">${r.status === 'queued' ? `<button class="btn sm" data-move="${r.post_id}">Reschedule</button>` : ''}<button class="btn sm danger" data-cancel="${r.id}">Cancel</button></td>
      </tr>`).join('')}</tbody></table></div>`
      : '<div class="empty card"><h2>Nothing scheduled</h2><p>Use <b>📤 Publish</b> on a post and choose <b>Schedule for</b>.</p></div>'}`;
  $$('[data-resolve]', app).forEach(b => b.onclick = guard(async () => {
    const posted = b.dataset.act === 'posted';
    if (!await askConfirm(posted ? 'Mark this post as published?' : 'Confirm it was NOT posted? You can then publish it again.', {danger: false, ok: 'Yes'})) return;
    await api('POST', `/api/publish/jobs/${b.dataset.resolve}/resolve`, { action: b.dataset.act });
    toast(posted ? 'Marked as posted' : 'Marked as not posted'); route();
  }));
  $$('[data-move]', app).forEach(b => b.onclick = guard(async () => {
    const row = rows.find(r => r.post_id === +b.dataset.move);
    scheduleDialog({ id: +b.dataset.move, account_id: row && row.account_id }, { mode: 'reschedule', onMoved: () => route() });
  }));
  $$('[data-cancel]', app).forEach(b => b.onclick = guard(async () => {
    if (!await askConfirm('Cancel this scheduled publish?', {ok: 'Cancel the publish'})) return;
    await api('DELETE', `/api/publish/scheduled/${b.dataset.cancel}`);
    toast('Cancelled'); route();
  }));
};

// back from connecting a platform in the middle of a publish: reopen the dialog on the same card
async function resumePendingPublish() {
  let raw = null;
  try { raw = sessionStorage.getItem('ht_resume_publish'); sessionStorage.removeItem('ht_resume_publish'); } catch (e) { return; }
  if (!raw) return;
  try {
    const { id, platform } = JSON.parse(raw);
    const p = await api('GET', `/api/posts/${id}`);
    state.publish = await api('GET', '/api/addons/publish').catch(() => state.publish);
    toast(`${PLATFORM_LABEL[platform] || 'Your account'} connected: choose when to publish`);
    scheduleDialog(p, { onPublished: job => { toast(job.status === 'queued' ? 'Scheduled: it will publish automatically' : (job.error || 'Done'), job.status === 'failed'); route(); } });
  } catch (e) { /* the card is gone or the session expired: nothing to resume */ }
}

/* ---------------- pipeline kanban ---------------- */
pages.pipeline = async app => {
  setTimeout(resumePendingPublish, 700);
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
      <button class="btn ai" id="ideas" title="AI suggests new Idea cards from your winners and your inspiration">✨ Ideas</button>
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
              ${['filmed', 'posted', 'analysed'].includes(s) ? `<button type="button" class="pipe-del" data-pipe-del="${p.id}" title="Delete this post" aria-label="Delete this post">🗑</button>` : ''}
              <div class="h">${esc(short(p.hook || p.body || 'Untitled', 72))}</div>
              <div class="meta">
                <span class="band-dot" style="background:${esc(p.phone_color || 'var(--accent)')}"></span>
                <span>${esc(p.handle || '—')}</span>
                ${['idea', 'scripted', 'filmed'].includes(s) && p.post_date ? (() => {
                  const d = Math.round((parseISO(p.post_date) - parseISO(iso(nowLocal()))) / 864e5);
                  const t = d < 0 ? `overdue ${-d}d` : d === 0 ? 'due today' : `due in ${d}d`;
                  return `<span class="${d < 0 ? 'stale' : ''}" title="The card's date is when it is due. Change it in the editor.">${t}</span>`;
                })() : `<span>${esc(p.post_date || '')}</span>`}
                ${p.vsc != null ? `<span>${p.vsc}/30</span>` : ''}
                ${p.views ? `<span>${fmt(p.views)} views</span>` : ''}
                ${p.video_url || p.has_video_file ? '<span title="Has video">🎬</span>' : ''}
                ${(() => {
                  const vid = !!(p.video_url || p.has_video_file);
                  if (p.scheduled_for && !p.external_post_id && s === 'filmed') return `<span class="tag good" title="Queued to publish (${esc(wsTz())})">📅 ${esc(fmtWhen(p.scheduled_for))}</span>`;
                  if (s === 'scripted' && !vid) return '<span class="tag warn" title="Upload the filmed video on this card before it can be scheduled">needs video</span>';
                  if (['scripted', 'filmed'].includes(s) && vid && !p.external_post_id) return '<span class="tag good" title="The video is attached: open the card to schedule it">ready to schedule</span>';
                  return '';
                })()}
                ${['idea', 'scripted'].includes(s) && p.created_at ? (() => {
                  const d = Math.floor((Date.now() - new Date(p.created_at).getTime()) / 864e5);
                  return `<span class="${d >= 14 ? 'stale' : ''}" title="${d >= 14 ? 'Waiting two weeks or more: film it or delete it' : 'Time on the board'}">${d < 1 ? 'today' : d + 'd'}</span>`;
                })() : ''}
              </div>
              ${p.result ? `<div class="pipe-result" title="Real results from the synced video">📊 ${fmt(p.result.views)} views${p.result.multiple != null ? ` · ${p.result.multiple}× normal` : ''}${p.result.original_multiple != null ? ` <span class="muted">(original ${p.result.original_multiple}×)</span>` : ''}
                  ${(() => {
                    const v = p.verdict || p.result.verdict;
                    const t = { beat: '✓ beat the original', matched: '≈ matched the original', fell_short: '↓ fell short of the original' }[v];
                    return t ? `<div class="verdict ${v}">${t}</div>` : '';
                  })()}
                  ${s === 'posted' ? (p.result.settled
                    ? `<button type="button" class="btn sm" data-analyse="${p.id}" title="Move to Analysed and keep this verdict on the hook">Mark analysed</button>`
                    : '<div class="muted small" style="font-weight:500">still settling: verdict in a few days</div>') : ''}</div>`
                : (s === 'posted' ? (() => {
                    // a pasted link, or a post HookTrack published itself, is matched by the sync without any help:
                    // the manual match is only offered when that has had its chance and still found nothing
                    if (p.url) return '<div class="pipe-result muted" title="The next sync will find this video from the link you pasted">⏳ waiting for sync</div>';
                    const ageDays = Math.round((parseISO(iso(nowLocal())) - parseISO(p.post_date)) / 864e5);
                    if (p.external_post_id && ageDays < 3) return '<div class="pipe-result muted" title="HookTrack published this: its link comes back on its own after the next sync">⏳ link coming back</div>';
                    return `<button type="button" class="btn sm" data-find="${p.id}" title="No link was pasted and the sync has not matched this card: pick the posted video yourself">🔎 Link my video</button>`;
                  })() : '')}
            </div>`).join('') || '<div class="muted small" style="padding:8px">Drop cards here</div>'}
          </div>
        </div>`;
      }).join('')}
    </div>`;

  $$('[data-view]', app).forEach(b => b.onclick = () => { state.postsView = b.dataset.view; route(); });
  $('#pa').onchange = e => { state.pipeAccount = e.target.value; state.calAccount = e.target.value; route(); };
  $('#new').onclick = () => postModal(null, { status: 'idea', account_id: state.pipeAccount || '' }, route);
  $('#ideas').onclick = () => ideasModal(route);

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
  $$('[data-find]', app).forEach(b => {
    b.addEventListener('mousedown', e => e.stopPropagation());
    b.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      const card = Object.values(board.columns).flat().find(p => p.id === +b.dataset.find);
      if (card) findVideoModal(card, route);
    });
  });
  $$('[data-analyse]', app).forEach(b => {
    b.addEventListener('mousedown', e => e.stopPropagation());
    b.addEventListener('click', guard(async e => {
      e.preventDefault(); e.stopPropagation();
      const r = await api('POST', `/api/posts/${b.dataset.analyse}/analyse`);
      toast({ beat: 'Analysed: it beat the original', matched: 'Analysed: it matched the original', fell_short: 'Analysed: it fell short of the original' }[r.verdict] || 'Analysed');
      route();
    }));
  });
  $$('[data-pipe-del]', app).forEach(b => {
    b.addEventListener('mousedown', e => e.stopPropagation());
    b.addEventListener('click', guard(async e => {
      e.preventDefault(); e.stopPropagation();
      const all = Object.values(board.columns).flat();
      const post = all.find(p => p.id === +b.dataset.pipeDel);
      if (!await askConfirm(`Delete this post${post && post.hook ? ': “' + short(post.hook, 60) + '”' : ''}? This cannot be undone.`)) return;
      await api('DELETE', `/api/posts/${b.dataset.pipeDel}`);
      toast('Post deleted');
      route();
    }));
  });
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
  // moving a card to Posted with no video and no posted link is usually a slip: ask, never block
  const bare = ids => {
    const all = Object.values(board.columns).flat();
    return ids.map(i => all.find(p => p.id === +i)).filter(p => p && !(p.video_url || p.has_video_file || p.url));
  };
  const confirmPosted = async (stage, ids) => stage !== 'posted' || !bare(ids).length
    || await askConfirm(`${bare(ids).length > 1 ? bare(ids).length + ' of these cards have' : 'This card has'} no video and no posted link, so its real results cannot be found later. Move to Posted anyway?`, {danger: false, ok: 'Move it'});
  const bulkStage = $('#bulk-stage');
  if (bulkStage) bulkStage.onchange = guard(async () => {
    const stage = bulkStage.value;
    if (!stage || !selected.size) { bulkStage.value = ''; return; }
    const ids = [...selected].map(Number);
    if (!(await confirmPosted(stage, ids))) { bulkStage.value = ''; return; }
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
      if (!(await confirmPosted(stage, [id]))) return;
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
  const queued = p => !!(p.scheduled_for && !p.external_post_id && !p.external_id);   // waiting in HookTrack's queue
  const dayOf = p => (queued(p) && zoneParts(p.scheduled_for) || {}).date || p.post_date;
  const by = {}; posts.forEach(p => (by[dayOf(p)] = by[dayOf(p)] || []).push(p));
  const today = iso(nowLocal());
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = addDays(gridStart, i), k = iso(d), list = (by[k] || []).slice().reverse();
    cells.push(`<div class="day ${d.getMonth() !== first.getMonth() ? 'out' : ''} ${k === today ? 'today' : ''}" data-day="${k}">
      <div class="dn"><span>${d.getDate()}</span>${list.length ? `<span>${list.length}</span>` : ''}</div>
      ${list.slice(0, 3).map(p => {
        const when = queued(p) ? (zoneParts(p.scheduled_for) || {}).time : '';
        const movable = !p.external_id && !['posted', 'analysed'].includes(normStatus(p.status));
        return `<button class="chip ${normStatus(p.status)}" data-id="${p.id}" ${movable ? 'draggable="true"' : ''} style="--c:${esc(p.phone_color || 'var(--accent)')}" title="${esc((p.handle || '') + ': ' + p.hook + (when ? ' · publishes ' + fmtWhen(p.scheduled_for) + ' (' + wsTz() + ')' : '') + (movable ? ' · drag to another day to move it' : ''))}">${when ? '📅 ' + esc(when) + ' ' : ''}${esc(short(p.hook, 24) || p.handle || '…')}</button>`;
      }).join('')}
      ${list.length > 3 ? `<div class="more">+${list.length - 3} more</div>` : ''}</div>`);
  }
  app.innerHTML = `<div class="page-head"><div><h1>Posts</h1>
    <div class="sub">Plan by day. Same posts as the board — switch views anytime. Drag a card to another day to move it; 📅 cards are queued to publish at that time (${esc(wsTz())}).</div></div>
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
  // drag a card to another day: a queued publish keeps its time of day, any other card just changes its date
  let dragPost = null;
  $$('.chip[draggable]', app).forEach(c => {
    c.addEventListener('dragstart', e => { dragPost = posts.find(p => p.id === +c.dataset.id); e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', c.dataset.id); } catch (err) { /* ignored */ } });
    c.addEventListener('dragend', () => { dragPost = null; $$('.day.over', app).forEach(d => d.classList.remove('over')); });
  });
  $$('.day', app).forEach(el => {
    el.addEventListener('dragover', e => { if (dragPost) { e.preventDefault(); el.classList.add('over'); } });
    el.addEventListener('dragleave', () => el.classList.remove('over'));
    el.addEventListener('drop', guard(async e => {
      e.preventDefault(); el.classList.remove('over');
      const p = dragPost; dragPost = null;
      if (!p || el.dataset.day === dayOf(p)) return;
      if (queued(p)) {
        const t = (zoneParts(p.scheduled_for) || {}).time || '09:00';
        const job = await api('POST', `/api/posts/${p.id}/reschedule`, { schedule_local: `${el.dataset.day}T${t}` });
        toast(`Moved to ${fmtWhen(job.scheduled_for)}`);
      } else {
        await api('PATCH', `/api/posts/${p.id}`, { post_date: el.dataset.day });
        toast(`Moved to ${el.dataset.day}`);
      }
      route();
    }));
  });
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
            <button class="btn sm primary" data-use="${r.id}" title="Make an Idea card on Posts from this">Use this</button>
            <button class="btn sm" data-toggle="${r.id}" data-on="${r.use_in_ai ? 1 : 0}">${r.use_in_ai ? 'Exclude AI' : 'Use in AI'}</button>
            <button class="btn sm danger" data-del="${r.id}">Delete</button>
          </div>
        </div>
      </div>`).join('')}</div>`
      : `<div class="empty card"><h2>No inspiration yet</h2><p>Paste a competitor URL, note the hook text, or drop a screenshot. AI ideas will pull these in as style references.</p></div>`}`;

  $('#add').onclick = () => inspirationModal(route);
  $$('[data-use]', app).forEach(b => (b.onclick = guard(async () => {
    const send = async accountId => {
      const r = await api('POST', `/api/inspiration/${b.dataset.use}/use`, accountId ? { account_id: accountId } : {});
      toast(`Added to Posts as an Idea (${r.label})`);
      location.hash = '#/pipeline';
    };
    if (state.accounts.length <= 1) { await send(null); return; }
    const mm = openModal(`<h2>Make an Idea from this</h2><p class="muted small">Which account is the new video for?</p>
      <div class="fields"><div class="full"><label class="f">For account</label>
        <select id="ia">${state.accounts.map(a => `<option value="${a.id}">${esc(a.handle)}</option>`).join('')}</select></div></div>
      <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="ig">Add to Posts</button></div>`);
    $('#ig', mm).onclick = guard(async () => { const v = +$('#ia', mm).value; mm.close(); await send(v); });
  })));
  $$('[data-del]', app).forEach(b => b.onclick = guard(async () => {
    if (!await askConfirm('Delete this inspiration item?')) return;
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
      <div class="full"><label class="f">Source URL</label>
        <div class="row" style="gap:8px;flex-wrap:nowrap"><input id="url" maxlength="500" placeholder="https://… paste a TikTok, Instagram or YouTube link" style="flex:1;min-width:0">
          <button class="btn" id="fill" type="button" title="Reads the caption, account and numbers from the link (about 1 credit)">Fill from link</button></div>
        <div class="small muted" id="fill-note" style="margin-top:4px">Paste a link and press Fill: the caption, account and numbers are read for you (about 1 credit).</div></div>
      <div class="full"><label class="f">Notes</label><textarea id="notes" rows="2" maxlength="2000" placeholder="Why it works, niche, structure…"></textarea></div>
      <div class="full"><label class="f">Screenshot</label><input type="file" id="file" accept="image/png,image/jpeg,image/webp,image/gif"></div>
      <div class="full"><label class="f" style="display:flex;align-items:center;gap:8px"><input type="checkbox" id="ai" checked> Use in AI hook generation</label></div>
    </div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="save">Save</button></div>`, true);
  $('#fill', m).onclick = guard(async () => {
    const u = $('#url', m).value.trim();
    if (!u) throw new Error('Paste a video link first');
    const b = $('#fill', m); busy(b, true, 'Reading…');
    let r;
    try { r = await api('POST', '/api/inspiration/lookup', { url: u }); } finally { busy(b, false); }
    const setIfEmpty = (id, v) => { const el = $(id, m); if (el && v && !el.value.trim()) el.value = v; };
    setIfEmpty('#hook', (r.caption || '').slice(0, 1000));
    setIfEmpty('#plat', r.platform);
    setIfEmpty('#title', [r.handle ? '@' + r.handle.replace(/^@/, '') : '', r.posted].filter(Boolean).join(' · '));
    const facts = [r.views != null ? `${fmt(r.views)} views` : '', r.likes != null ? `${fmt(r.likes)} likes` : '',
      r.comments != null ? `${fmt(r.comments)} comments` : '', r.duration_s ? `${Math.round(r.duration_s)} s` : '',
      r.sound ? `sound: ${r.sound}` : ''].filter(Boolean).join(' · ');
    setIfEmpty('#notes', facts);
    $('#fill-note', m).textContent = `Read ${r.platform} video${r.handle ? ' by @' + r.handle.replace(/^@/, '') : ''}. Edit anything, then save.`;
  });
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
const POST_AS = { SCALE: 'Scripted', MUTATE: 'Idea' };
// where a day's number came from: typed, a screenshot read by AI, the sync's formula, or an estimate
const srcClass = s => ({ ai: 'ai', sociavault: 'sv', estimated: 'est' }[s] || '');
const srcTitle = s => ({ ai: 'Read from a screenshot by AI', sociavault: 'Synced: what your videos gained that day',
  estimated: 'Estimated from your videos\' totals; replaced by the exact number as syncs arrive', manual: 'Typed by hand' }[s] || '');
const SETTLE_DAYS = 3;
const stillGrowing = p => {
  const d = p && p.post_date ? parseISO(p.post_date) : null;
  return !!d && (nowLocal() - d) / 864e5 < SETTLE_DAYS;
};
// "Is this your video?": synced videos that could be the one a card became, so the link need not be pasted.
async function findVideoModal(card, done) {
  const m = openModal(`<h2>Find the posted video</h2>
    <p class="muted small">Videos from <b>${esc(card.handle || 'this account')}</b> posted since this card was made, best match first.
      Confirm yours and its results come back to the card.</p><div id="fv-list" class="muted small">Looking…</div>
    <div class="actions"><button class="btn" data-close>Close</button></div>`);
  let r;
  try { r = await api('GET', `/api/posts/${card.id}/candidates`); }
  catch (e) { $('#fv-list', m).textContent = e.message; return; }
  const list = r.candidates || [];
  $('#fv-list', m).className = '';
  $('#fv-list', m).innerHTML = list.length ? `<ul class="list">${list.map(c => `<li>
      <span style="flex:1;min-width:0"><b>${esc(short(c.hook || '(no caption)', 70))}</b>
        <div class="small muted">${esc(c.post_date)} · ${fmt(c.views)} views${c.similarity ? ` · ${Math.round(c.similarity * 100)}% same words` : ''}
        ${c.url ? ` · <a href="${esc(c.url)}" target="_blank" rel="noopener">open ↗</a>` : ''}</div></span>
      <button class="btn sm primary" data-pick="${c.post_id}" type="button">This one</button></li>`).join('')}</ul>`
    : '<p class="muted">No synced video from this account since the card was made. Post it, press Sync on Hooks, then try again.</p>';
  $$('[data-pick]', m).forEach(b => (b.onclick = guard(async () => {
    await api('POST', `/api/posts/${card.id}/link`, { post_id: +b.dataset.pick });
    toast('Linked: its results are on the card');
    m.close(); if (done) done();
  })));
}

// Which account is the new video for? Defaults to the hook's own; with several accounts the user picks.
function sendHookModal(hook, done) {
  const m = openModal(`<h2>Send to Posts</h2>
    <p class="muted small">“${esc(short(hook.hook || '(no hook)', 90))}” is a <b>${esc(PIPE_LABEL[hook.decision === 'SCALE' ? 'scripted' : 'idea'])}</b> next step.
      Which account is the new video for? Pick another account in the same niche to try the winner there.</p>
    <div class="fields"><div class="full"><label class="f">For account</label>
      <select id="sh-acc">${state.accounts.map(a => `<option value="${a.id}" ${a.id === hook.account_id ? 'selected' : ''}>${esc(a.handle)}${a.id === hook.account_id ? ' (same account)' : ''}</option>`).join('')}</select></div></div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="sh-go">Send to Posts</button></div>`);
  $('#sh-go', m).onclick = guard(async () => {
    const r = await api('POST', `/api/posts/${hook.id}/followup?account_id=${$('#sh-acc', m).value}`);
    toast((r.created ? `Added to Posts as ${PIPE_LABEL[r.status] || r.status}` : 'Already on Posts for that account')
      + (r.read === 'done' ? ' · video read' : r.read === 'failed' ? ` · could not read the video (${r.read_error})` : ''), r.read === 'failed');
    m.close(); if (done) done();
  });
}
const postCell = p => {
  if (p.followup) return `<span class="small" title="On Posts as ${esc(PIPE_LABEL[normStatus(p.followup.status)] || '')}">✓ ${esc(PIPE_LABEL[normStatus(p.followup.status)] || normStatus(p.followup.status))}</span>${
    p.followup.judged ? `<div class="small muted" title="Cards made from this hook that were marked analysed">${p.followup.beat} of ${p.followup.judged} beat it</div>` : ''}${
    state.accounts.length > 1 && POST_AS[p.decision] ? ` <button class="btn sm" type="button" data-post-hook="${p.id}" title="Send it to another account too">＋</button>` : ''}`;
  if (!POST_AS[p.decision]) return '<span class="muted">—</span>';
  return `<button class="btn sm" type="button" data-post-hook="${p.id}" title="Add this hook to Posts as ${POST_AS[p.decision]}">Post → ${POST_AS[p.decision]}</button>${
    stillGrowing(p) ? '<div class="small muted" title="Posted in the last 3 days: its views are still growing, so this decision can still change">⏳ still growing</div>' : ''}`;
};
pages.hooks = async app => {
  const q = [state.hookStatus && `status=${state.hookStatus}`, state.hookAccount && `account_id=${state.hookAccount}`].filter(Boolean).join('&');
  // Hooks are the posted videos the sync pulled in. A Scale or Tweak hook is sent to Posts by its Post button.
  let posts = await api('GET', `/api/posts?source=synced${q ? '&' + q : ''}`);   // only what the sync pulled in
  const hot = (await api('GET', '/api/velocity?days=3&limit=6').catch(() => [])).filter(p => p.perf && p.perf.label === 'HOT_START');
  const winnerCount = posts.filter(p => p.perf && p.perf.label === 'WINNER').length;
  if (state.hookWinners) posts = posts.filter(p => p.perf && p.perf.label === 'WINNER');
  const hasSS = posts.some(p => p.saves != null || p.shares != null);      // only show columns that have data
  const hasLikes = posts.some(p => p.likes != null);
  app.innerHTML = `<div class="page-head"><div><h1>Hooks</h1><div class="sub">Your posted videos, pulled in from your accounts. Press Post on a Scale hook (Scripted) or a Tweak hook (Idea) to send it to Posts. Rules: Scale ${state.settings.scale_vsc}+, Tweak ${state.settings.mutate_vsc}–${state.settings.scale_vsc - 1}, Stop below.</div></div>
    <div class="row"><select id="fs" style="width:auto"><option value="">All statuses</option>${['posted', 'analysed'].map(s => `<option value="${s}" ${state.hookStatus === s ? 'selected' : ''}>${PIPE_LABEL[s]}</option>`).join('')}</select>
      <button class="btn" id="scorehooks" title="Score every judged hook from its real results (free, no AI). This also runs after every sync.">Score hooks</button>
      <button class="btn" id="simdec" title="What if thresholds changed?">Simulate thresholds</button>
      <select id="fa" style="width:auto">${accountOptions(state.hookAccount, 'All accounts')}</select>
      <label class="check small" title="Posts that beat their own account's normal"><input type="checkbox" id="winonly" ${state.hookWinners ? 'checked' : ''}> Winners only (${winnerCount})</label>
      <button class="btn" id="top5" title="Send the 5 strongest Scale hooks that have settled (3+ days old) and are not on Posts yet">Send top 5 Scale</button></div></div>
    ${hot.length ? `<div class="card hot-card"><h3 style="margin:0 0 4px">🔥 Hot starts <span class="muted small" style="font-weight:400">posted in the last 2 days, already far above that account's usual first day</span></h3>
      <ul class="list">${hot.map(h => `<li><div style="flex:1;min-width:0"><b>${esc(h.handle || '-')}</b> <span class="muted">${esc(short(h.hook || '(no hook)', 70))}</span>
        <div class="small muted">${fmt(h.perf.early_views)} views on day 1 · ${h.perf.early_multiple}× usual (${fmt(h.perf.early_baseline)})</div></div>
        <button class="btn sm" data-hot="${h.id}">Open</button></li>`).join('')}</ul>
      <p class="small muted" style="margin:6px 0 0">A strong first day is the earliest sign of a winner. Make two or three variations of it now, while it is still fresh.</p></div>` : ''}
    ${posts.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Account</th><th>Hook</th><th>Status</th><th class="num">Views</th><th class="num" title="Views vs this account's normal (median of its recent posts)">× normal</th>${hasLikes ? '<th class="num">Likes</th>' : ''}<th class="num">Comments</th>${hasSS ? '<th class="num" title="Saves">Saves</th><th class="num">Shares</th>' : ''}<th class="num" title="Saves + shares + comments per 1,000 views">Intent /1k</th><th class="num" title="Comments asking to buy it, or how/where to get it, out of the comments read">Asking</th><th>Intent</th><th class="num">Hook score</th><th>Decision</th><th>Post</th></tr></thead><tbody>
    ${posts.map(p => `<tr class="band" style="--band:${esc(p.phone_color || 'transparent')};cursor:pointer" data-id="${p.id}"><td>${esc(p.post_date)}</td><td class="handle">${esc(p.handle || '—')}</td>
      <td class="wrap">${p.variant_label ? `<span class="badge" title="A/B variant">${esc(p.variant_label)}</span> ` : ''}${esc(p.hook || '—')}${p.made_from ? `<div class="small" style="color:var(--accent)" title="A video you made from another hook">↻ made from “${esc(short(p.made_from.hook || '', 40))}” · generation ${p.made_from.generation}</div>` : ''}<div class="muted small">${esc(short(p.ai_prompt, 60))}</div></td><td>${pill(p.status)}</td>
      <td class="num">${fmt(p.views)}</td><td class="num">${winnerChip(p.perf)}</td>${hasLikes ? `<td class="num">${p.likes == null ? '<span class="muted">-</span>' : fmt(p.likes)}</td>` : ''}<td class="num">${fmt(p.comments)}</td>${hasSS ? `<td class="num">${p.saves == null ? '<span class="muted">-</span>' : fmt(p.saves)}</td><td class="num">${p.shares == null ? '<span class="muted">-</span>' : fmt(p.shares)}</td>` : ''}<td class="num">${p.perf && p.perf.intent_per_1k != null ? p.perf.intent_per_1k : '<span class="muted">-</span>'}</td><td class="num">${p.perf && p.perf.comments_read ? `<span title="${p.perf.buy_comments} want to buy, ${p.perf.ask_comments} ask how/where; ${p.perf.comments_read} comments read">${p.perf.intent_comments}<span class="muted"> / ${p.perf.comments_read}</span></span>` : '<span class="muted" title="Open the post and read its comments">-</span>'}</td><td>${p.intent ? '✓' : ''}</td>
      <td class="num">${p.vsc == null ? '<span class="muted">—</span>' : p.vsc + '/30'}${p.score_by === 'auto' ? ` <span class="muted small" title="${esc(p.score_reasoning || 'Calculated from this video’s real results against the account’s normal')}">auto</span>` : ''}${p.score_by === 'ai' ? ` <span title="${p.score_blind === true ? 'AI score on the wording alone' : p.score_blind === false ? 'AI score that saw the results' : 'AI score'}">${p.score_blind === false ? '✨*' : '✨'}</span>` : ''}${p.score_confidence != null ? ` <span class="muted small" title="Confidence">${Math.round(p.score_confidence*100)}%</span>` : ''}</td><td title="${esc(p.decision_basis || '')}">${pill(p.decision)}${p.decision ? `<div class="muted small">${decisionSource(p)}</div>` : ''}</td>
      <td>${postCell(p)}</td></tr>`).join('')}</tbody></table></div>`
      : `<div class="empty card"><h2>No posted videos yet</h2><p>Hooks fills itself from your accounts: add your TikTok, Instagram or YouTube handles, then press Sync. Posted videos and their views appear here.</p>
      <div class="row" style="justify-content:center;gap:8px;margin-top:12px">
        <a class="btn primary" href="#/accounts">Add accounts</a>
      </div></div>`}`;
  $('#scorehooks').onclick = async e => {
    const b = e.currentTarget; busy(b, true, 'Scoring…');
    try {
      const r = await api('POST', '/api/hooks/score');
      toast(r.scored ? `Scored ${r.scored} hook${r.scored === 1 ? '' : 's'}` : (r.judged ? 'Scores are up to date' : 'Nothing to score yet: hooks need a few days of views and 5+ posts on the account'));
      if (r.scored) route();
    } catch (err) { toast(err.message || 'Could not score hooks'); }
    finally { busy(b, false, 'Score hooks'); }
  };
  $('#fs').onchange = e => { state.hookStatus = e.target.value; route(); };
  $('#fa').onchange = e => { state.hookAccount = e.target.value; route(); };
  $$('[data-hot]', app).forEach(b => (b.onclick = () => postModal(hot.find(h => h.id === +b.dataset.hot), null, route)));
  const wo = $('#winonly'); if (wo) wo.onchange = e => { state.hookWinners = e.target.checked; route(); };
  $$('[data-post-hook]', app).forEach(b => (b.onclick = guard(async e => {
    e.stopPropagation();
    const h = posts.find(x => x.id === +b.dataset.postHook);
    if (h && h.made_from && !await askConfirm(`This video is itself a copy you made (generation ${h.made_from.generation}). Make another copy of it?`, {danger: false, ok: 'Make another copy'})) return;
    if (state.accounts.length > 1) { sendHookModal(posts.find(h => h.id === +b.dataset.postHook), route); return; }
    const r = await api('POST', `/api/posts/${b.dataset.postHook}/followup`);
    toast(`Added to Posts as ${PIPE_LABEL[r.status] || r.status}` + (r.read === 'done' ? ' · video read' : r.read === 'failed' ? ` · could not read the video (${r.read_error})` : ''), r.read === 'failed');
    route();
  })));
  $('#top5').onclick = guard(async () => {
    const b = $('#top5'); busy(b, true, 'Sending…');
    let r;
    try { r = await api('POST', `/api/followups/top?limit=5${state.hookAccount ? '&account_id=' + state.hookAccount : ''}`); }
    finally { busy(b, false); }
    const n = r.sent.length;
    const reads = r.sent.filter(x => x.read === 'done').length;
    toast(n ? `Sent ${n} Scale hook${n > 1 ? 's' : ''} to Posts as Scripted${reads ? ` · ${reads} video${reads > 1 ? 's' : ''} read` : ''}${r.waiting ? ` · ${r.waiting} more ready` : ''}${r.growing ? ` · ${r.growing} still growing` : ''}`
            : (r.growing ? `Nothing to send yet: ${r.growing} Scale hook${r.growing > 1 ? 's are' : ' is'} still growing` : 'No Scale hooks waiting to be sent'));
    route();
  });
  $('#simdec').onclick = () => simulateThresholdsModal();
  api('GET', '/api/clones/scorecard').then(sc => {
    if (!sc || !sc.clones || !$('#app')) return;
    const pct = x => (x == null ? '–' : Math.round(x * 100) + '%');
    const card = document.createElement('div');
    card.className = 'card';
    card.style.margin = '0 0 12px';
    card.innerHTML = `<h3 style="margin:0 0 4px">Matching videos of winners</h3>
      <div class="small muted" style="margin-bottom:6px">${sc.clones} made · ${sc.measured} measured so far</div>
      ${sc.measured ? `<div class="row" style="gap:18px;flex-wrap:wrap"><div><b>${sc.winners}</b> <span class="muted small">winners</span></div>
        <div><b>${sc.average}</b> <span class="muted small">about normal</span></div><div><b>${sc.duds}</b> <span class="muted small">duds</span></div>
        <div><b>${pct(sc.win_rate)}</b> <span class="muted small">won</span></div><div><b>${pct(sc.held_rate)}</b> <span class="muted small">at least normal</span></div></div>
        ${sc.enough ? '' : '<div class="small muted" style="margin-top:6px">Too few measured clones to say much yet. Judge it after 5 or more.</div>'}`
        : '<div class="small muted">Post some clones and give them two days to be measured.</div>'}`;
    const host = $('#app');
    host.insertBefore(card, host.firstElementChild && host.firstElementChild.nextSibling);
  }).catch(() => {});
  $$('tr[data-id]', app).forEach(tr => tr.addEventListener('click', e => {
    postModal(posts.find(p => p.id === +tr.dataset.id), null, route);
  }));
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
      ${r.scored_and_measured ? `<div class="table-wrap" style="box-shadow:none"><table><thead><tr><th>How the post did</th><th class="num">Posts</th><th class="num">Median score</th><th class="num">Middle half</th></tr></thead><tbody>
        ${row('Winners', 'winner')}${row('About normal', 'average')}${row('Duds', 'dud')}</tbody></table></div>` : ''}
      ${r.enough_data ? `<p style="margin:10px 0 4px">Suggested: <b>Scale at ${r.suggestion.scale_vsc}+</b>, <b>Tweak at ${r.suggestion.mutate_vsc}–${r.suggestion.scale_vsc - 1}</b>.
        <span class="muted small">Your current lines (Scale ${r.current.scale_vsc}+) catch ${qn.winners_caught}% of winners and ${qn.precision == null ? 'flag nothing' : qn.precision + '% of what they flag is a real winner'};
        the suggestion catches ${q.winners_caught}% and ${q.precision == null ? 'flags nothing' : q.precision + '% of what it flags is a real winner'}.</span></p>
        <div class="row" style="gap:8px"><button class="btn sm" id="usesugg">Preview these</button>${isAdmin() ? '<button class="btn sm primary" id="savesugg">Use as my rules</button>' : ''}</div>` : ''}`;
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

function ideasModal(onDone) {
  const m = openModal(`<h2>✨ AI hook ideas</h2>${aiNotice()}<p class="muted small">Uses your best and worst posts plus Inspiration items marked for AI as style references. Nothing is added until you press <b>Add to Posts</b>.</p>
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
      <div class="row" style="margin-top:8px"><button class="btn sm primary" data-add="${i}">Add to Posts as an Idea</button></div></div>`).join('').join('');
    $$('[data-add]', m).forEach(b => (b.onclick = guard(async () => {
      const x = r.ideas[+b.dataset.add];
      await api('POST', '/api/posts', { account_id: +acc, post_date: iso(addDays(nowLocal(), 7)), status: 'idea',
        ai_prompt: x.ai_prompt || '', hook: x.hook, body: x.body || '', caption: x.caption || '' });
      b.textContent = 'Added ✓'; b.disabled = true;
      if (onDone) onDone();
    })));
  });
}


/* ---------------- billing ---------------- */
/* Monthly and yearly are one plan billed two ways. Group them into tiers and show one card per tier for the chosen
   billing period, instead of listing every variant. A tier with only one period is shown in both views. */
const planTierKey = p => String(p.code).replace(/[_-](year|yearly|annual|annually)$/i, '');
function planTiers(plans, interval) {
  const tiers = new Map();
  plans.forEach(p => {
    const k = planTierKey(p);
    if (!tiers.has(k)) tiers.set(k, {});
    tiers.get(k)[p.interval === 'year' ? 'year' : 'month'] = p;
  });
  return [...tiers.values()].map(t => {
    const plan = t[interval] || t.month || t.year;
    const save = t.month && t.year && t.month.price_minor > 0
      ? Math.round(100 * (1 - t.year.price_minor / (12 * t.month.price_minor))) : null;
    return { plan, monthly: t.month, yearly: t.year, exact: !!t[interval], save: save > 0 ? save : null };
  });
}
const planTierName = ({ plan, monthly }) => (plan.interval === 'year' && monthly ? monthly.name : plan.name).replace(/\s*\((yearly|annual|annually)\)\s*$/i, '');

// USD is the headline when we know the rate; the amount actually charged sits smaller beneath it.
const priceBlock = (p, fx) => {
  if (!p.price_minor) return '<div style="font-size:22px;font-weight:700;margin:6px 0">Free</div>';
  const fmtMoney = (minor, cur) => { try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(minor / 100); } catch { return `${minor / 100} ${cur}`; } };
  const per = `<span class="muted small" style="font-weight:400"> / ${esc(p.interval)}</span>`;
  if (p.charge_currency && p.charge_currency !== p.currency) {      // priced in USD, charged in local currency
    return `<div style="margin:6px 0"><div style="font-size:22px;font-weight:700">${fmtMoney(p.price_minor, p.currency)}${per}</div>
      <div style="font-size:17px;font-weight:600;margin-top:2px;color:var(--accent)" title="This is the amount you are actually charged, at today's rate">≈ ${fmtMoney(p.charge_minor, p.charge_currency)}${per}</div>
      <div class="muted" style="font-size:11.5px">charged at checkout, at today's rate</div></div>`;
  }
  const rate = fx[p.currency];
  if (p.currency === 'USD' || !rate) return `<div style="font-size:22px;font-weight:700;margin:6px 0">${fmtMoney(p.price_minor, p.currency)}${per}</div>`;
  const usd = p.price_minor / rate;                           // minor units of the plan currency -> USD minor units
  return `<div style="margin:6px 0"><div style="font-size:22px;font-weight:700">≈ ${fmtMoney(usd, 'USD')}${per}</div>
    <div class="muted" style="font-size:12px;margin-top:2px" title="This is the amount you are actually charged">${fmtMoney(p.price_minor, p.currency)} charged</div></div>`;
};

const cardPanel = b => {
  const c = b.card || {};
  if (!c.has_card) return '';
  const when = c.next_charge_at ? new Date(c.next_charge_at).toLocaleDateString() : '';
  return `<div class="card" style="margin-top:12px"><div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px">
    <div><b>${esc(c.brand || 'Card')} ending ${esc(c.last4)}</b> <span class="muted small">${c.exp ? 'expires ' + esc(c.exp) : ''}</span>
      <div class="small ${c.auto_renew ? '' : 'muted'}">${c.auto_renew ? `Renews automatically${when ? ' on ' + when : ''}.` : 'Automatic renewal is off.'}</div>
      ${c.renew_error ? `<div class="small" style="color:var(--bad)">Last renewal attempt failed: ${esc(c.renew_error)}${c.auto_renew ? ' We will try again.' : ' Pay again to keep your plan.'}</div>` : ''}</div>
    ${b.can_manage ? `<div class="row" style="gap:8px"><button class="btn sm" id="ar-toggle">${c.auto_renew ? 'Turn off auto-renew' : 'Turn on auto-renew'}</button><button class="btn sm danger" id="ar-remove">Remove card</button></div>` : ''}</div></div>`;
};


/* ---------------- paying for a plan without leaving the page ---------------- */
const fmtMoney = (minor, cur) => { try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(minor / 100); } catch { return `${minor / 100} ${cur}`; } };
const loadPaystackJs = () => window.PaystackPop ? Promise.resolve() : new Promise((res, rej) => {
  const sc = document.createElement('script'); sc.src = 'https://js.paystack.co/v2/inline.js';
  sc.onload = res; sc.onerror = () => rej(new Error('Could not load the Paystack checkout. Check your connection or ad blocker.'));
  document.head.appendChild(sc);
});
// Ask our server (which asks Paystack) until the payment settles.
async function pollPayment(ref, { everyMs = 3000, maxMs = 5 * 60 * 1000, closed = () => false, alive = () => true } = {}) {
  const t0 = Date.now();
  for (;;) {
    let r;
    try { r = await api('GET', `/api/billing/payments/${encodeURIComponent(ref)}`); } catch (e) { r = null; }
    if (!alive()) return 'left';
    if (r && r.status === 'success') return 'success';
    if (r && r.status === 'failed') return 'failed';
    if (closed()) return 'closed';
    if (Date.now() - t0 >= maxMs) return 'timeout';
    await new Promise(res => setTimeout(res, everyMs));
  }
}
// One path for every "pay for a plan" button: quote (credit for unused time), Paystack popup, confirmation.
async function payForPlan(planCode, { saveCard = false, banner = () => {}, alive = () => true, onDone = () => route() } = {}) {
  const q = await api('GET', `/api/billing/quote?plan_code=${encodeURIComponent(planCode)}`);
  if (q.credit_minor > 0) {
    const ok = await askConfirm(`Switch from ${q.from_plan} to ${q.plan}?\n\nYou have ${q.remaining_days} day${q.remaining_days === 1 ? '' : 's'} left on ${q.from_plan}, worth ${fmtMoney(q.credit_minor, q.currency)}. That is taken off the price, so ${q.covers_all ? 'there is nothing to pay today' : `you pay ${fmtMoney(q.charge_minor, q.currency)} today`}. ${q.plan} starts now.`, { danger: false, ok: q.covers_all ? 'Switch now' : 'Continue to payment' });
    if (!ok) return 'cancelled';
  }
  const r = await api('POST', '/api/billing/checkout', { plan_code: planCode, save_card: !!saveCard });
  if (r.applied) { toast(`Switched to ${r.plan}. The unused time on your old plan covered it.`); refreshTrialBar(true); onDone(); return 'success'; }
  await loadPaystackJs();
  let popupClosed = false, paidSeen = false;
  banner('Complete the payment in the Paystack window…');
  const pollP = pollPayment(r.reference, { closed: () => popupClosed && !paidSeen, alive });
  new window.PaystackPop().resumeTransaction(r.access_code, {
    onSuccess: () => { paidSeen = true; banner('Waiting for payment confirmation…'); },
    onCancel: () => { popupClosed = true; },
    onError: e => { popupClosed = true; toast((e && e.message) || 'Paystack reported a problem', true); },
  });
  const outcome = await pollP;
  banner('');
  if (outcome === 'left') return outcome;
  if (outcome === 'success') { toast('Payment received. Your plan is updated.'); refreshTrialBar(true); onDone(); }
  else if (outcome === 'failed') toast('The payment did not go through. You have not been charged for a plan.', true);
  else if (outcome === 'timeout') toast('Still waiting for Paystack. You can leave this page: your plan updates automatically once the payment is confirmed.', true);
  else toast('Checkout closed. No payment was made.');
  return outcome;
}

let planPickerEl = null;
// The plan list as a window over whatever page the person is on, so the trial bar and a blocked action can lead
// straight to paying instead of to another page.
async function openPlanPicker(opts = {}) {
  if (planPickerEl && planPickerEl.isConnected) return;
  const b = await api('GET', '/api/billing');
  if (!b.enabled) return;
  const hasYear = b.plans.some(p => p.interval === 'year'), hasMonth = b.plans.some(p => p.interval !== 'year');
  let iv = hasYear && hasMonth ? (state.billingInterval || (b.plan && b.plan.interval === 'year' ? 'year' : 'month')) : (hasYear ? 'year' : 'month');
  let saveCard = b.card.auto_renew || !b.card.has_card;
  const lim = n => (n === -1 ? 'Unlimited' : Number(n).toLocaleString());
  const why = opts.reason || (b.state === 'expired'
    ? (b.subscription.lapsed ? 'Your plan has ended.' : b.trial.reason === 'trial_used' ? 'This email has already used its free trial.' : 'Your free trial has ended.') + ' Choose a plan to keep working.'
    : b.state === 'trial' ? `You have ${b.trial.days_left} day${b.trial.days_left === 1 ? '' : 's'} left on your free trial. You can pick a plan any time and keep everything.` : 'Pick the plan that fits.');
  const m = openModal('<div id="pp"></div>', true);
  planPickerEl = m;
  const paint = () => {
    const shown = planTiers(b.plans, iv);
    const bestSave = Math.max(0, ...shown.map(t => t.save || 0));
    $('#pp', m).innerHTML = `<h2 style="margin-top:0">Choose a plan</h2>
      <p class="muted" style="margin:0 0 12px">${esc(why)}</p>
      <div id="pp-banner" class="notice" style="margin-bottom:10px" hidden></div>
      ${!b.can_manage ? '<div class="notice" style="margin-bottom:10px">Only a workspace owner or admin can pay. Ask one of them to open this window.</div>' : ''}
      ${!b.payments_ready ? '<div class="notice" style="margin-bottom:10px">Payments are not available right now. Please try again later or contact support.</div>' : ''}
      ${hasYear && hasMonth ? `<div class="row" style="gap:6px;margin:0 0 12px" role="group" aria-label="Billing period">
        <button type="button" class="btn sm ${iv === 'month' ? 'primary' : ''}" data-iv="month" aria-pressed="${iv === 'month'}">Monthly</button>
        <button type="button" class="btn sm ${iv === 'year' ? 'primary' : ''}" data-iv="year" aria-pressed="${iv === 'year'}">Yearly${bestSave ? ` · save up to ${bestSave}%` : ''}</button></div>` : ''}
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px">
      ${shown.map(t => { const p = t.plan; return `<div class="card" style="margin:0;${[t.monthly, t.yearly].some(x => x && x.code === b.plan.code) && b.state === 'paid' ? 'border-color:var(--accent)' : ''}">
        <h3 style="margin:0">${esc(planTierName(t))}</h3>
        ${priceBlock(p, b.fx_usd || {})}
        ${p.interval === 'year' && p.price_minor ? `<div class="muted small" style="margin-top:-2px">${fmtMoney(Math.round(p.price_minor / 12), p.currency)} / month${p.charge_currency && p.charge_currency !== p.currency ? ` (≈ ${fmtMoney(Math.round(p.charge_minor / 12), p.charge_currency)})` : ''}, billed yearly${t.save ? ` · <b>save ${t.save}%</b>` : ''}</div>` : ''}
        <ul style="padding-left:18px;margin:8px 0;font-size:13px"><li>${lim(p.max_accounts)} accounts</li><li>${lim(p.max_members)} team member${p.max_members === 1 ? '' : 's'}</li><li>${lim(p.max_ai_calls)} AI requests / month</li></ul>
        ${p.price_minor ? `<button type="button" class="btn primary" style="width:100%" data-pick="${esc(p.code)}" ${b.can_manage && b.payments_ready ? '' : 'disabled'}>${p.code === b.plan.code && b.state === 'paid' ? 'Renew' : 'Choose ' + esc(planTierName(t))}</button>` : ''}
      </div>`; }).join('')}</div>
      ${b.can_manage ? `<label class="check" style="margin:12px 0 0;display:flex;gap:8px;align-items:flex-start"><input type="checkbox" id="pp-card" ${saveCard ? 'checked' : ''} style="width:auto;margin-top:3px">
        <span>Renew automatically with my card <span class="muted small">(pay by card in the next step; turn it off any time. Mobile money cannot be saved.)</span></span></label>` : ''}
      <div class="actions"><a class="btn" href="#/billing" data-close>See full details</a><button class="btn" data-close>Not now</button></div>`;
    $$('[data-close]', m).forEach(x => x.addEventListener('click', m.close));
    $$('[data-iv]', m).forEach(x => (x.onclick = () => { iv = x.dataset.iv; state.billingInterval = iv; paint(); }));
    if ($('#pp-card', m)) $('#pp-card', m).onchange = e => { saveCard = e.target.checked; };
    $$('[data-pick]', m).forEach(btn => (btn.onclick = guard(async () => {
      const ban = msg => { const el = $('#pp-banner', m); if (el) { el.hidden = !msg; el.textContent = msg || ''; } };
      busy(btn, true, 'Opening checkout…');
      let out;
      try {
        out = await payForPlan(btn.dataset.pick, { saveCard, banner: ban, onDone: () => { m.close(); route(); } });
      } finally { if (btn.isConnected) busy(btn, false); }
      return out;
    })));
  };
  paint();
}

pages.billing = async app => {
  const b = await api('GET', '/api/billing');
  const money = (minor, cur) => { try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(minor / 100); } catch { return `${minor / 100} ${cur}`; } };
  const lim = n => (n === -1 ? 'Unlimited' : Number(n).toLocaleString());
  const meter = (label, used, limit) => `<div style="margin-top:12px"><div class="row" style="justify-content:space-between"><span>${label}</span>
    <b>${Number(used).toLocaleString()} <span class="muted" style="font-weight:400">of ${lim(limit)}</span></b></div>
    <div style="height:8px;border-radius:99px;background:var(--surface-2);overflow:hidden;margin-top:4px"><div style="height:100%;width:${limit === -1 || !limit ? 0 : Math.min(100, used / limit * 100)}%;background:${limit !== -1 && used >= limit ? 'var(--bad)' : 'var(--accent)'}"></div></div></div>`;
  if (!b.enabled) {
    app.innerHTML = `<div class="page-head"><h1>Billing</h1></div><div class="card"><p style="margin:0">Plans are not turned on for this platform, so there are no limits on your workspace.</p></div>`;
    return;
  }
  const sub = b.subscription;
  const hasYear = b.plans.some(p => p.interval === 'year'), hasMonth = b.plans.some(p => p.interval !== 'year');
  const iv = hasYear && hasMonth ? (state.billingInterval || (b.plan && b.plan.interval === 'year' ? 'year' : 'month')) : (hasYear ? 'year' : 'month');
  const shown = planTiers(b.plans, iv);
  const bestSave = Math.max(0, ...shown.map(t => t.save || 0));
  app.innerHTML = `<div class="page-head"><div><h1>Billing</h1><div class="sub">${esc(state.me.workspace.name)}</div></div></div>
    <div id="paybanner" class="notice" style="margin-bottom:12px;display:none" hidden></div>
    ${b.state === 'expired' ? `<div class="notice" style="margin-bottom:12px;border-color:var(--bad)"><b>${sub.lapsed ? 'Your paid period has ended.' : b.trial.reason === 'trial_used' ? 'This email has already used its free trial on another workspace.' : 'Your free trial has ended.'}</b> You can still look around, but nothing can be changed until you choose a plan below. Your data is safe.</div>` : ''}
    <div class="card"><h2 style="margin:0">${b.state === 'trial' ? `Free trial: ${b.trial.days_left} day${b.trial.days_left === 1 ? '' : 's'} left` : b.state === 'expired' ? 'No active plan' : 'Current plan: ' + esc(b.plan.name)} ${sub.status === 'comp' ? '<span class="badge">complimentary</span>' : ''}</h2>
      ${b.state === 'trial' ? `<div class="muted small">Ends ${new Date(b.trial.ends_at).toLocaleDateString()}. Pick a plan any time: you keep everything you have set up.</div>` : ''}
      <div class="muted small">${b.state === 'trial' || b.state === 'expired' ? '' : (sub.status === 'comp' ? (sub.current_period_end ? `Complimentary until ${new Date(sub.current_period_end).toLocaleDateString()}.` : 'Complimentary plan, no end date.') : sub.current_period_end && b.plan.price_minor ? `Paid until ${new Date(sub.current_period_end).toLocaleDateString()}. ${b.card && b.card.auto_renew ? 'It renews automatically with your saved card.' : 'Automatic renewal is off: renew before it ends to keep your plan.'}` : (sub.current_period_end ? `Until ${new Date(sub.current_period_end).toLocaleDateString()}` : ''))}</div>
      ${meter('Accounts', b.usage.accounts, b.limits.accounts)}${meter('Team members', b.usage.members, b.limits.members)}${meter('AI requests this month', b.usage.ai_calls, b.limits.ai_calls)}</div>
    ${cardPanel(b)}
    ${b.state !== 'expired' && b.addons ? `<h2 style="margin:20px 0 8px">Add-ons</h2>
    <p class="muted small" style="margin:0 0 8px">Need more than your plan includes? Buy only what you need. Accounts last 30 days; credits last until the end of this month.</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px">
    ${b.addons.map(a => `<div class="card"><h3 style="margin:0">${esc(a.label)}</h3>
      <div style="font-size:20px;font-weight:700;margin:6px 0">${esc(new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(a.price_minor / 100))}<span class="muted small" style="font-weight:400"> each · ${esc(a.lasts)}</span></div>
      <div class="muted small">${esc(a.what)}</div>
      ${b.can_manage ? `<div class="row" style="gap:8px;margin-top:10px"><select id="aq-${esc(a.kind)}" style="width:auto">${[1, 2, 3, 5, 10].map(n => `<option value="${n}">${n}</option>`).join('')}</select>
        <button class="btn" data-addon="${esc(a.kind)}" ${b.payments_ready ? '' : 'disabled'}>Buy</button></div>` : '<div class="muted small" style="margin-top:8px">Only workspace admins can buy add-ons.</div>'}
    </div>`).join('')}</div>
    ${(b.addon_purchases || []).length ? `<ul class="list" style="margin-top:10px">${b.addon_purchases.map(p => `<li>
      <span style="flex:1;min-width:0"><b>${fmt(p.units)} × ${esc((b.addons.find(a => a.kind === p.kind) || {}).label || p.kind)}</b>
        <div class="small muted">${p.renew_state === 'renewed' ? 'Renewed: continues after' : p.auto_renew ? 'Renews automatically on' : 'Ends on'} ${new Date(p.expires_at).toLocaleDateString()}${p.renew_state === 'failed' ? ' · the last renewal was declined' : ''}</div></span>
      ${p.renewable && b.can_manage && p.renew_state !== 'renewed' ? `<button class="btn sm" data-renew="${p.id}" data-on="${p.auto_renew ? 0 : 1}" type="button">${p.auto_renew ? 'Turn off auto-renew' : 'Renew automatically'}</button>` : ''}</li>`).join('')}</ul>` : ''}` : ''}
    <h2 style="margin:20px 0 8px">Plans</h2>
    ${b.can_manage ? `<label class="check" style="margin:0 0 10px;display:flex;gap:8px;align-items:flex-start"><input type="checkbox" id="savecard" ${b.card.auto_renew || !b.card.has_card ? 'checked' : ''} style="width:auto;margin-top:3px">
      <span>Renew automatically with my card <span class="muted small">(pay by card in the next step: we keep it securely with Paystack and charge it a day before your plan ends. Turn it off any time. Mobile money cannot be saved.)</span></span></label>` : ''}
    ${hasYear && hasMonth ? `<div class="row" style="gap:6px;margin:0 0 12px" role="group" aria-label="Billing period">
      <button type="button" class="btn sm ${iv === 'month' ? 'primary' : ''}" data-iv="month" aria-pressed="${iv === 'month'}">Monthly</button>
      <button type="button" class="btn sm ${iv === 'year' ? 'primary' : ''}" data-iv="year" aria-pressed="${iv === 'year'}">Yearly${bestSave ? ` · save up to ${bestSave}%` : ''}</button></div>` : ''}
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px">
    ${shown.map(t => { const p = t.plan; return `<div class="card" style="${[t.monthly, t.yearly].some(x => x && x.code === b.plan.code) ? 'border-color:var(--accent)' : ''}">
      <h2 style="margin:0">${esc(planTierName(t))}</h2>
      ${priceBlock(p, b.fx_usd || {})}
      ${p.interval === 'year' && p.price_minor ? `<div class="muted small" style="margin-top:-2px">${money(Math.round(p.price_minor / 12), p.currency)} / month${p.charge_currency && p.charge_currency !== p.currency ? ` (≈ ${money(Math.round(p.charge_minor / 12), p.charge_currency)})` : ''}, billed yearly${t.save ? ` · <b>save ${t.save}%</b>` : ''}</div>` : ''}
      ${!t.exact ? `<div class="muted small">${iv === 'year' ? 'Billed monthly only' : 'Billed yearly only'}</div>` : ''}
      <div class="muted small">${esc(p.description)}</div>
      <ul style="padding-left:18px;margin:8px 0"><li>${lim(p.max_accounts)} accounts</li><li>${lim(p.max_members)} team members</li><li>${lim(p.max_ai_calls)} AI requests / month</li><li>${lim(p.max_connected)} connected publishing account${p.max_connected === 1 ? '' : 's'}</li><li>${lim(p.max_credits)} data credits / month</li><li>${p.max_reports === 0 ? 'No shareable client reports' : lim(p.max_reports) + ' shareable client report' + (p.max_reports === 1 ? '' : 's')}</li></ul>
      ${p.code === b.plan.code && b.state !== 'trial' ? (p.price_minor && b.can_manage ? `<button class="btn" data-buy="${esc(p.code)}" ${b.payments_ready ? '' : 'disabled'}>Renew</button>` : '<span class="badge">Current</span>')
        : (p.price_minor ? `<button class="btn primary" data-buy="${esc(p.code)}" ${b.can_manage && b.payments_ready ? '' : 'disabled'} title="${b.can_manage ? (b.payments_ready ? '' : 'Payments are not set up yet') : 'Only workspace admins can change the plan'}">Choose ${esc(p.name)}</button>` : '')}
    </div>`; }).join('')}</div>
    ${b.payments.length ? `<h2 style="margin:20px 0 8px">Payment history</h2><div class="table-wrap"><table><thead><tr><th>Date</th><th>Plan</th><th class="num">Amount</th><th>Status</th></tr></thead><tbody>
      ${b.payments.map(p => `<tr><td>${new Date(p.created_at).toLocaleDateString()}</td><td>${esc(p.plan_code)}</td><td class="num">${money(p.amount_minor, p.currency)}</td><td>${esc(p.status)}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
  const rerender = () => route();
  $$('[data-iv]', app).forEach(btn => (btn.onclick = () => { state.billingInterval = btn.dataset.iv; route(); }));
  $('#ar-toggle') && ($('#ar-toggle').onclick = guard(async () => {
    await api('PATCH', '/api/billing/auto-renew', { enabled: !b.card.auto_renew }); toast(b.card.auto_renew ? 'Automatic renewal is off' : 'Automatic renewal is on'); rerender();
  }));
  $('#ar-remove') && ($('#ar-remove').onclick = guard(async () => {
    if (!await askConfirm('Remove the saved card? Your plan will not renew automatically.')) return;
    await api('DELETE', '/api/billing/card'); toast('Card removed'); rerender();
  }));
  const banner = msg => { const el = $('#paybanner'); if (el) { el.hidden = !msg; el.style.display = msg ? '' : 'none'; el.textContent = msg || ''; } };
  const loadPaystack = () => window.PaystackPop ? Promise.resolve() : new Promise((res, rej) => {
    const sc = document.createElement('script'); sc.src = 'https://js.paystack.co/v2/inline.js';
    sc.onload = res; sc.onerror = () => rej(new Error('Could not load the Paystack checkout. Check your connection or ad blocker.'));
    document.head.appendChild(sc);
  });
  const onBilling = () => location.hash.startsWith('#/billing');
  // Ask our server (which asks Paystack) until the payment settles. Stops when the page is left.
  const poll = async (ref, { everyMs = 3000, maxMs = 5 * 60 * 1000, closed = () => false } = {}) => {
    const t0 = Date.now();
    for (;;) {
      let r;
      try { r = await api('GET', `/api/billing/payments/${encodeURIComponent(ref)}`); } catch (e) { r = null; }
      if (!onBilling()) return 'left';
      if (r && r.status === 'success') return 'success';
      if (r && r.status === 'failed') return 'failed';
      if (closed()) return 'closed';
      if (Date.now() - t0 >= maxMs) return 'timeout';
      await new Promise(res => setTimeout(res, everyMs));
    }
  };
  const finish = outcome => {
    if (outcome === 'left') return;
    banner('');
    if (outcome === 'success') { toast('Payment received. Your plan is updated.'); refreshTrialBar(true); route(); }
    else if (outcome === 'failed') { toast('The payment did not go through. You have not been charged for a plan.', true); route(); }
    else if (outcome === 'timeout') toast('Still waiting for Paystack. You can leave this page: your plan updates automatically once the payment is confirmed.', true);
    else toast('Checkout closed. No payment was made.');
  };
  $$('[data-buy]', app).forEach(btn => (btn.onclick = guard(async () => {
    busy(btn, true, 'Opening checkout…');
    try {
      await payForPlan(btn.dataset.buy, { saveCard: !!($('#savecard') && $('#savecard').checked), banner, alive: onBilling, onDone: rerender });
    } finally { if (btn.isConnected) busy(btn, false); }
  })));
  $$('[data-renew]', app).forEach(btn => (btn.onclick = guard(async () => {
    await api('PATCH', `/api/billing/addons/${btn.dataset.renew}/auto-renew`, { on: btn.dataset.on === '1' });
    toast(btn.dataset.on === '1' ? 'This add-on will renew automatically' : 'Auto-renew is off for this add-on'); route();
  })));
  $$('[data-addon]', app).forEach(btn => (btn.onclick = guard(async () => {
    const qty = +($(`#aq-${btn.dataset.addon}`) || { value: 1 }).value;
    busy(btn, true, 'Opening checkout…');
    let r;
    try { r = await api('POST', '/api/billing/addons/checkout', { kind: btn.dataset.addon, qty }); await loadPaystack(); }
    finally { busy(btn, false); }
    let popupClosed = false, paidSeen = false;
    banner('Complete the payment in the Paystack window…');
    const pollP = poll(r.reference, { closed: () => popupClosed && !paidSeen });
    new window.PaystackPop().resumeTransaction(r.access_code, {
      onSuccess: () => { paidSeen = true; banner('Waiting for payment confirmation…'); },
      onCancel: () => { popupClosed = true; },
      onError: e => { popupClosed = true; toast((e && e.message) || 'Paystack reported a problem', true); },
    });
    finish(await pollP);
  })));
  // a payment started earlier (page reloaded, tab closed): check it once, quietly
  if (b.pending) poll(b.pending.reference, { maxMs: 0 }).then(o => { if (o === 'success') finish(o); });
};

/* ---------------- accounts ---------------- */
function syncDays() {
  let d = 14;
  try { d = +localStorage.getItem('ht_sync_days') || 14; } catch (e) {}
  return [14, 30, 60, 90].includes(d) ? d : 14;
}

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
      <select id="syncdays" style="width:auto" title="How far back a manual sync reads (TikTok; Instagram and YouTube return what the platform lists). Longer reads use more credits.">${[14, 30, 60, 90].map(d => `<option value="${d}" ${syncDays() === d ? 'selected' : ''}>Last ${d} days</option>`).join('')}</select>
      <button class="btn" id="syncall" title="Sync all accounts with Auto-sync on">Sync all</button></div></div>
    ${groups.map(({ p, rows }) => `<div class="card" style="margin-bottom:14px"><div class="row" style="margin-bottom:8px">
      <span class="dot" style="background:${esc(p.color)}"></span><b>${esc(p.label)}</b><span class="muted">${rows.length} accounts</span><span class="spacer"></span>
      ${p.id ? `<button class="btn sm" data-ep="${p.id}">Edit</button>` : ''}</div>
      ${rows.length ? `<div class="table-wrap" style="box-shadow:none"><table><thead><tr><th>Handle</th><th>Platform</th><th>Niche</th><th>Status</th><th class="num" title="From the platform profile, refreshed weekly (or on a full sync)">Followers</th><th class="num">Posts/day</th><th>Content hook</th><th>Sync</th><th></th></tr></thead><tbody>
      ${rows.map(a => `<tr><td class="handle"><button type="button" class="pin-btn ${getPins().has(String(a.id)) ? 'on' : ''}" data-pin="${a.id}" title="Pin account">${getPins().has(String(a.id)) ? '★' : '☆'}</button> ${esc(a.handle)}${a.client_id ? `<div class="small muted">${esc((state.clients.find(c => c.id === a.client_id) || {}).name || '')}</div>` : ''}</td><td>${esc(a.platform)}</td><td>${esc(a.niche)}</td><td>${pill(a.status, a.status === 'active' ? 'posted' : a.status === 'killed' ? 'KILL' : 'HOLD')}</td>
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
      const r = await api('POST', `/api/accounts/${b.dataset.sync}/sync?days_back=${syncDays()}`);
      toast(`Synced @${r.handle || ''}: ${r.upserted} posts (~${r.credits_est || '?'} credits)`);
      route();
    } finally { busy(btn, false, 'Sync'); }
  })));
  const sd = $('#syncdays', app);
  if (sd) sd.onchange = () => { try { localStorage.setItem('ht_sync_days', sd.value); } catch (e) {} };
  const sa = $('#syncall', app);
  if (sa) sa.onclick = guard(async () => {
    if (!(state.sv && state.sv.configured)) { toast('Reading from the platforms is not set up on this server yet', true); return; }
    busy(sa, true, 'Syncing…');
    try {
      const r = await api('POST', `/api/sync/all?days_back=${syncDays()}`);
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

pages.clients = async app => {
  const [cl, rp] = await Promise.all([api('GET', '/api/clients'), api('GET', '/api/reports')]);
  state.clients = cl.clients;
  const links = rp.links;
  const unassigned = state.accounts.filter(a => !a.client_id).length;
  const admin = isAdmin();
  const limitNote = rp.limit === -1 ? '' : rp.limit === 0
    ? 'Your plan lets you preview client reports. Sharing a live link needs a bigger plan (Billing).'
    : `Your plan includes ${rp.limit} shareable report link${rp.limit === 1 ? '' : 's'} (${links.length} used).`;
  app.innerHTML = `<div class="page-head"><div><h1>Clients</h1><div class="sub">Group accounts by the brand they belong to, and send each client a read-only report.</div></div>
      <div class="row">${admin ? '<button class="btn primary" id="addclient">+ Client</button>' : ''}</div></div>
    ${limitNote ? `<div class="notice" style="margin-bottom:12px">${esc(limitNote)}</div>` : ''}
    ${cl.clients.map(c => {
      const mine = links.filter(l => l.client_id === c.id);
      return `<div class="card" style="margin-bottom:14px"><div class="row" style="margin-bottom:6px"><b style="font-size:16px">${esc(c.name)}</b>
        <span class="muted">${c.accounts} account${c.accounts === 1 ? '' : 's'}</span><span class="spacer"></span>
        ${admin ? `<button class="btn sm" data-ca="${c.id}">Accounts</button><button class="btn sm" data-ce="${c.id}">Rename</button>` : ''}</div>
        ${c.notes ? `<p class="muted small" style="margin:0 0 6px">${esc(c.notes)}</p>` : ''}
        <p class="small" style="margin:0 0 10px">${c.handles.length ? c.handles.map(h => `<span class="pill">${esc(h)}</span>`).join(' ') : '<span class="muted">No accounts yet. Use Accounts to add some.</span>'}</p>
        <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn sm" data-prev="${c.id}" ${c.accounts ? '' : 'disabled'}>Preview report</button>
          ${admin ? `<button class="btn sm primary" data-share="${c.id}" ${c.accounts ? '' : 'disabled'}>Create share link</button>` : ''}</div>
        ${mine.length ? `<div style="margin-top:10px">${mine.map(l => `<div class="row small" style="gap:8px;padding:6px 0;border-top:1px solid var(--line,#e5e7eb);flex-wrap:wrap">
          <span style="flex:1;min-width:180px"><b>${esc(l.title || 'Report')}</b> <span class="muted">· last ${l.days} days · ${l.view_count} view${l.view_count === 1 ? '' : 's'}${l.last_viewed_at ? ', last ' + esc(String(l.last_viewed_at).slice(0, 10)) : ''}</span></span>
          <button class="btn sm" data-copy="${esc(l.path)}">Copy link</button><a class="btn sm" href="${esc(l.path)}" target="_blank" rel="noopener">Open</a>
          ${admin ? `<button class="btn sm danger" data-revoke="${l.id}">Revoke</button>` : ''}</div>`).join('')}</div>` : ''}</div>`;
    }).join('') || `<div class="empty card"><h2>No clients yet</h2><p>Add a client for each brand you manage, put its accounts under it, then share a report.</p></div>`}
    ${unassigned && cl.clients.length ? `<p class="muted small">${unassigned} account${unassigned === 1 ? ' is' : 's are'} not assigned to a client.</p>` : ''}`;
  const find = id => cl.clients.find(c => c.id === +id);
  $('#addclient') && ($('#addclient').onclick = () => clientModal());
  $$('[data-ce]', app).forEach(b => (b.onclick = () => clientModal(find(b.dataset.ce))));
  $$('[data-ca]', app).forEach(b => (b.onclick = () => clientAccountsModal(find(b.dataset.ca))));
  $$('[data-prev]', app).forEach(b => (b.onclick = () => reportPreview(find(b.dataset.prev))));
  $$('[data-share]', app).forEach(b => (b.onclick = () => shareModal(find(b.dataset.share))));
  $$('[data-copy]', app).forEach(b => (b.onclick = guard(async () => {
    await navigator.clipboard.writeText(location.origin + b.dataset.copy); toast('Link copied');
  })));
  $$('[data-revoke]', app).forEach(b => (b.onclick = guard(async () => {
    if (!await askConfirm('Revoke this link? Anyone who has it will see "not available" straight away.')) return;
    await api('DELETE', `/api/reports/${b.dataset.revoke}`); route();
  })));
};

function clientModal(c) {
  const m = openModal(`<h2>${c ? 'Edit client' : 'New client'}</h2><div class="fields">
    <div><label class="f">Name</label><input id="cn" value="${esc(c ? c.name : '')}" maxlength="80" placeholder="Brand or client name"></div>
    <div style="grid-column:1/-1"><label class="f">Notes (only you see these)</label><input id="cno" value="${esc(c ? c.notes : '')}" maxlength="500"></div></div>
    <div class="actions">${c ? '<button class="btn danger" id="cdel">Delete</button>' : ''}<span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="csave">Save</button></div>`);
  $('#csave', m).onclick = guard(async () => {
    const b = { name: $('#cn', m).value.trim(), notes: $('#cno', m).value };
    if (!b.name) throw new Error('Give the client a name');
    await (c ? api('PATCH', `/api/clients/${c.id}`, b) : api('POST', '/api/clients', b)); m.close(); route();
  });
  if (c) $('#cdel', m).onclick = guard(async () => {
    if (!await askConfirm('Delete this client? Its accounts are kept, but its report links stop working.')) return;
    await api('DELETE', `/api/clients/${c.id}`); m.close(); route();
  });
}

function clientAccountsModal(c) {
  const taken = a => a.client_id && a.client_id !== c.id;
  const m = openModal(`<h2>Accounts for ${esc(c.name)}</h2>
    <div style="max-height:50vh;overflow:auto">${state.accounts.map(a => `<label class="check" style="display:flex;gap:8px;padding:4px 0">
      <input type="checkbox" value="${a.id}" ${a.client_id === c.id ? 'checked' : ''}> <span>${esc(a.handle)} <span class="muted small">${esc(a.platform)}${taken(a) ? ' · currently ' + esc((state.clients.find(x => x.id === a.client_id) || {}).name || 'another client') + ' (moves here)' : ''}</span></span></label>`).join('') || '<p class="muted">Add accounts first.</p>'}</div>
    <div class="actions"><span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="asave">Save</button></div>`);
  $('#asave', m).onclick = guard(async () => {
    const ids = $$('input[type=checkbox]:checked', m).map(i => +i.value);
    await api('PUT', `/api/clients/${c.id}/accounts`, { account_ids: ids }); m.close(); route();
  });
}

async function reportPreview(c, opts) {
  const body = Object.assign({ client_id: c.id, days: 30, show_hooks: true, title: '' }, opts || {});
  const m = openModal(`<div class="row" style="gap:8px;margin-bottom:8px"><label class="f" style="margin:0">Period</label>
      <select id="rpdays" style="width:auto">${[7, 14, 30, 90].map(d => `<option value="${d}" ${d === body.days ? 'selected' : ''}>Last ${d} days</option>`).join('')}</select>
      <span class="spacer"></span><button class="btn" data-close>Close</button></div><div id="rpbox" class="muted">Building the report…</div>`, true);
  const load = guard(async () => {
    body.days = +$('#rpdays', m).value;
    const d = await api('POST', '/api/reports/preview', body);
    window.renderReport($('#rpbox', m), d);
  });
  $('#rpdays', m).onchange = load;
  load();
}

function shareModal(c) {
  const m = openModal(`<h2>Share a report with ${esc(c.name)}</h2>
    <p class="muted small">Anyone with the link can see this report and nothing else: no other clients, accounts, settings or keys. It is always current. Revoke it any time.</p>
    <div class="fields"><div><label class="f">Title (optional)</label><input id="st1" maxlength="120" placeholder="${esc(c.name)}: performance report"></div>
      <div><label class="f">Period</label><select id="sd">${[7, 14, 30, 90].map(d => `<option value="${d}" ${d === 30 ? 'selected' : ''}>Last ${d} days</option>`).join('')}</select></div>
      <div style="grid-column:1/-1"><label class="check"><input type="checkbox" id="sh" checked> Show the hook text of winning videos</label></div></div>
    <div class="actions"><span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn" id="spv">Preview first</button><button class="btn primary" id="sgo">Create link</button></div>`);
  const opts = () => ({ title: $('#st1', m).value.trim(), days: +$('#sd', m).value, show_hooks: $('#sh', m).checked });
  $('#spv', m).onclick = () => reportPreview(c, opts());
  $('#sgo', m).onclick = guard(async () => {
    const l = await api('POST', '/api/reports', { client_id: c.id, ...opts() });
    try { await navigator.clipboard.writeText(location.origin + l.path); toast('Link created and copied'); } catch { toast('Link created'); }
    m.close(); route();
  });
}

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
    if (!await askConfirm('Delete this phone? Its accounts are kept but unassigned.')) return;
    await api('DELETE', `/api/phones/${p.id}`); m.close(); route();
  });
}

function accountModal(a) {
  const editing = !!a;
  const x = a || { handle: '', phone_id: state.phones[0] ? state.phones[0].id : '', platform: 'TikTok', niche: '', status: 'active', times_per_day: 1, content_hook: '', model: '', sync_enabled: true, client_id: null };
  const PLATFORMS = ['TikTok', 'Instagram', 'YouTube'];
  const known = PLATFORMS.some(p => p.toLowerCase() === String(x.platform).toLowerCase());
  const m = openModal(`<h2>${editing ? 'Edit account' : 'Add an account'}</h2>
    <label class="f">Handle or profile link</label>
    <div class="row" style="gap:8px;flex-wrap:nowrap">
      <input id="h" value="${esc(x.handle)}" placeholder="@name  or  paste the profile link" autocomplete="off" style="flex:1;min-width:0">
      <select id="pf" style="width:auto">${PLATFORMS.map(p => `<option ${String(x.platform).toLowerCase() === p.toLowerCase() ? 'selected' : ''}>${p}</option>`).join('')}${known ? '' : `<option selected>${esc(x.platform)}</option>`}</select></div>
    <div class="small" id="hcheck" style="margin:6px 0 10px;min-height:22px">${editing
      ? '<button class="btn sm" id="hverify" type="button">Check it exists <span class="muted">(1 credit)</span></button>'
      : '<span class="muted">Paste a profile link or type a handle. We look it up and fill in the rest.</span>'}</div>
    <details id="more" ${editing ? 'open' : ''}><summary class="small muted" style="cursor:pointer;margin-bottom:8px">More options</summary>
      <div class="fields">
        <div><label class="f">Phone</label><select id="ph"><option value="">None</option>${state.phones.map(p => `<option value="${p.id}" ${p.id === x.phone_id ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></div>
        <div><label class="f">Client / brand</label><select id="cl"><option value="">None</option>${(state.clients || []).map(c => `<option value="${c.id}" ${c.id === x.client_id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></div>
        <div><label class="f">Niche</label><input id="n" value="${esc(x.niche)}"></div>
        <div><label class="f">Status</label><select id="st">${['active', 'warming', 'paused', 'killed'].map(s => `<option ${x.status === s ? 'selected' : ''}>${s}</option>`).join('')}</select></div>
        <div><label class="f">Posts per day</label><input id="t" type="number" min="0" max="50" value="${x.times_per_day}"></div>
        <div><label class="f">Content hook style</label><input id="ch" value="${esc(x.content_hook)}"></div>
        <div><label class="f">AI model / tool</label><input id="md" value="${esc(x.model)}"></div>
        <div style="grid-column:1/-1"><label class="f" style="display:flex;align-items:center;gap:8px;font-weight:500">
          <input type="checkbox" id="sy" ${x.sync_enabled ? 'checked' : ''}> Auto-sync (include in Sync all)</label>
          <div class="muted small">Pulls public posts &amp; view counts automatically.</div></div></div></details>
    <div class="actions">${editing ? '<button class="btn danger" id="del">Delete</button>' : ''}<span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="save">${editing ? 'Save' : 'Add account'}</button></div>`);

  const guessPlatform = v => (/tiktok\.com/i.test(v) ? 'TikTok' : /instagram\.com/i.test(v) ? 'Instagram' : /youtu(\.be|be\.com)/i.test(v) ? 'YouTube' : '');
  const looksLikeLink = v => /(tiktok|instagram|youtube|youtu)\./i.test(v);
  const hcheck = $('#hcheck', m);
  const lookupAvailable = () => !!(state.sv && state.sv.configured);
  const memo = {};                                  // handle|platform -> result: one lookup (one credit) per distinct account
  let nicheTouched = !!(editing && x.niche);
  $('#n', m).addEventListener('input', () => { nicheTouched = true; });
  const show = html => { hcheck.innerHTML = html; };

  // Look the account up and fill in what it tells us. Returns the result, or null when no lookup was possible.
  const lookup = async () => {
    const raw = $('#h', m).value.trim();
    if (!raw) return null;
    const g = guessPlatform(raw);
    if (g) $('#pf', m).value = g;
    if (!lookupAvailable()) { show('<span class="muted">Not checked: reading profiles is not set up on this server, so the account will be added as typed.</span>'); return null; }
    const key = raw.toLowerCase() + '|' + $('#pf', m).value;
    if (!memo[key]) {
      show('<span class="muted">Looking it up…</span>');
      try { memo[key] = await api('POST', '/api/accounts/lookup', { handle: raw, platform: $('#pf', m).value }); }
      catch (e) { show(`<span style="color:var(--bad)">${esc(e.message)}</span> <span class="muted">You can still add it.</span>`); return null; }
    }
    const r = memo[key];
    if (r.found) {
      $('#h', m).value = r.handle; $('#pf', m).value = r.platform;
      if (!nicheTouched && r.niche && !$('#n', m).value.trim()) $('#n', m).value = r.niche;
      show(`<span class="up">✓ ${esc(r.display_name || r.handle)}${r.verified ? ' (verified)' : ''}</span>
        <span class="muted">· ${esc(r.platform)}${r.followers != null ? ' · ' + compact(r.followers) + ' followers' : ''}${r.video_count != null ? ' · ' + compact(r.video_count) + ' videos' : ''}${r.niche ? ' · niche: ' + esc(r.niche) : ''}</span>`);
    } else {
      show(`<span style="color:var(--bad)">${esc(r.message)}</span>`);
    }
    return r;
  };

  if (!editing) {
    let t = null;
    const h = $('#h', m);
    h.addEventListener('input', () => {
      clearTimeout(t);
      const v = h.value.trim(), g = guessPlatform(v);
      if (g) $('#pf', m).value = g;
      if (looksLikeLink(v)) t = setTimeout(lookup, 700);      // a pasted link is complete: look it up by itself
      else if (v) show('<span class="muted">Press Enter or click away to look it up.</span>');
    });
    h.addEventListener('blur', () => { clearTimeout(t); if (h.value.trim() && !memo[h.value.trim().toLowerCase() + '|' + $('#pf', m).value]) lookup(); });
    h.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(t); lookup(); } });
    $('#pf', m).addEventListener('change', () => { if (h.value.trim()) lookup(); });
    setTimeout(() => h.focus(), 50);
  } else {
    // editing an existing account: the manual check stays, because it spends a credit and nothing is being created
    const vb = $('#hverify', m);
    if (vb) vb.onclick = guard(async () => {
      if (!$('#h', m).value.trim()) { toast('Type a handle first', true); return; }
      busy(vb, true, 'Checking…');
      try { await lookup(); } finally { const b = $('#hverify', m); if (b) busy(b, false); }
    });
    $('#h', m).addEventListener('input', e => { const g = guessPlatform(e.target.value); if (g) $('#pf', m).value = g; });
  }

  $('#save', m).onclick = guard(async () => {
    const handle = $('#h', m).value.trim();
    if (!handle) throw new Error('Enter a handle or a profile link');
    if (!editing) {                                          // verify before creating, so a typo never becomes an account
      const r = await lookup();
      if (r && !r.found && !(await askConfirm(`${r.message}\n\nAdd it anyway?`, { title: 'Account not found', danger: false, ok: 'Add anyway' }))) return;
    }
    const b = { handle: $('#h', m).value.trim(), phone_id: $('#ph', m).value ? +$('#ph', m).value : null, platform: $('#pf', m).value, niche: $('#n', m).value,
      status: $('#st', m).value, times_per_day: +$('#t', m).value || 0, content_hook: $('#ch', m).value, model: $('#md', m).value,
      sync_enabled: $('#sy', m).checked, client_id: $('#cl', m).value ? +$('#cl', m).value : null };
    await (editing ? api('PATCH', `/api/accounts/${a.id}`, b) : api('POST', '/api/accounts', b));
    m.close();
    if (!editing) toast(`Added ${b.handle}. Press Sync to pull its posts.`);
    route();
  });
  if (editing) $('#del', m).onclick = guard(async () => {
    if (!await askConfirm(`Delete ${a.handle}? This also deletes its views and posts.`)) return;
    await api('DELETE', `/api/accounts/${a.id}`); m.close(); route();
  });
}


/* ---------------- settings ---------------- */
pages.settings = async app => {
  const admin = isAdmin();
  const [members, invites, pub] = await Promise.all([
    api('GET', '/api/workspace/members'),
    admin ? api('GET', '/api/workspace/invites') : [],
    api('GET', '/api/addons/publish'),
  ]);
  state.publish = pub;
  const S = state.settings, me = state.me;
  const roleSel = (m) => `<select data-role="${m.id}" style="width:auto" ${admin ? '' : 'disabled'}>${['owner', 'admin', 'member'].map(r => `<option ${m.role === r ? 'selected' : ''}>${r}</option>`).join('')}</select>`;

  app.innerHTML = `<div class="page-head"><div><h1>Settings</h1><div class="sub">${esc(me.workspace.name)} · you are ${esc(me.workspace.role)}</div></div></div>
  <div class="grid" style="max-width:860px">

  <div class="settings-section"><h2 class="settings-h">Getting started</h2>
  <p class="muted small" style="margin:-4px 0 12px">Core setup for this workspace. Everything else is optional.</p>

  <div class="card"><h2>Decision rules</h2>
    <p class="muted small">When to Scale, Tweak, or Stop an account based on weekly views and hook scores.</p>
    <div class="fields">
    ${[['scale_weekly', 'Weekly views to Scale'], ['scale_vsc', 'Hook score to Scale (of 30)'], ['mutate_vsc', 'Hook score to Tweak (of 30)'], ['weekly_target', 'Videos to film per week (0 = no target)'], ['credit_budget', 'Monthly budget for syncing and reading videos, in credits (0 = no budget)'], ['hold_low_pct', 'Hold starts at (% of normal, 50-99)'], ['hold_high_pct', 'Hold ends at (% of normal, from 100)'], ['kill_after_posts', 'Posts before a stall = Stop'], ['stall_views', 'Views that count as a breakout'], ['winner_pct', 'Winner = % of an account\'s normal views (200 = 2×)']]
      .map(([k, l]) => `<div><label class="f">${l}</label><input data-s="${k}" type="number" min="0" value="${S[k]}" ${admin ? '' : 'disabled'}></div>`).join('')}</div>
    <div class="small muted" id="calib" style="margin-top:10px">Checking how reliable the calls have been…</div>
    <div class="check" style="margin-top:10px"><input type="checkbox" id="read-on-send" data-s="read_on_send" ${S.read_on_send ? 'checked' : ''} ${admin ? '' : 'disabled'}>
      <label for="read-on-send">Read a Scale hook's video when I send it to Posts <span class="muted">(about 1 credit each; its first words and structure then arrive in the card's brief)</span></label></div>
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

  <div class="settings-section"><h2 class="settings-h">Publishing</h2>
  <div class="card" id="publish-card"><h2>📤 Connect your accounts</h2>
    ${pub.managed_available && pub.held ? `<div class="notice" style="margin-bottom:10px"><b>Publishing starts when you choose a plan.</b>
      Your trial includes tracking, decisions, cards and briefs. <a href="#/billing">Choose a plan</a> to connect accounts and publish.</div>` : ''}
    ${pub.managed_available ? `
    <p class="muted">Connect the TikTok, Instagram and YouTube accounts you publish to. Press <b>Publish</b> on a Filmed card and HookTrack posts it for you.
      You sign in on each platform's own page: HookTrack never sees your password. <b>Nothing is ever posted unless you press Publish.</b></p>
    <div id="pub-managed-body" class="small muted">Loading…</div>`
    : `<p class="muted">Publishing is not set up on this server yet. Ask the platform admin to add the publishing key.</p>`}
  </div>
  </div><!-- /publishing section -->

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
    <label class="check" style="margin-top:14px"><input type="checkbox" id="digest-on" ${me.user.digest === false ? '' : 'checked'}> Email me a weekly summary (winners, matching videos that beat their original, what is waiting)</label>
    <label class="check"><input type="checkbox" id="hot-on" ${me.user.hot_alerts === false ? '' : 'checked'}> Email me when a video is breaking out (a very strong first day)</label>
    <div class="actions"><button class="btn" id="pw-go">Change password</button>${isAdmin() ? ' <button class="btn" id="digest-preview">Preview this week&rsquo;s summary</button>' : ''}</div>
    <pre id="digest-text" class="small" style="white-space:pre-wrap;display:none;margin-top:10px"></pre>
    <p class="small muted">Changing your password signs you out everywhere else.</p></div>
  <div class="card danger-zone" id="acct-del"><h2>Delete my account</h2>
    <p class="muted">Permanently deletes <b>${esc(me.user.email)}</b>. Workspaces where you are the only member are deleted with everything in them; in workspaces shared with others you are simply removed. This cannot be undone, and any paid time left on a deleted workspace is lost.</p>
    <div id="acct-plan"></div>
    <div class="actions"><button class="btn" id="acct-review">Review what will be deleted</button></div></div>
  ${me.workspace.role === 'owner' ? `<div class="card danger-zone"><h2>Delete this workspace</h2>
    <p class="muted">Permanently deletes <b>${esc(me.workspace.name)}</b> and everything in it: accounts, views, posts, settings, keys and uploads. Other members lose access. This cannot be undone.</p>
    <div class="fields"><div><label class="f">Type the workspace name to confirm</label><input id="del-name" autocomplete="off"></div>
      <div><label class="f">Your password</label><input id="del-pw" type="password" autocomplete="current-password"></div></div>
    <div class="actions"><button class="btn danger" id="del-go">Delete workspace</button></div></div>` : ''}
  </div>`;



  // Publishing: the accounts this workspace has connected, and buttons to connect more
  const mbody = $('#pub-managed-body');
  if (mbody && pub.managed_available) {
    (async () => {
      let r;
      try { r = await api('GET', '/api/publish/managed'); } catch (e) { mbody.textContent = e.message; return; }
      const names = { tiktok: 'TikTok', instagram: 'Instagram', youtube: 'YouTube' };
      mbody.className = 'small';
      mbody.innerHTML = `${(r.accounts || []).length ? `<ul class="list">${r.accounts.map(a => `<li><span class="badge">${esc(names[a.platform] || a.platform)}</span>
          <span style="flex:1">${esc(a.display_name || a.username)}${a.username ? ` <span class="muted">@${esc(String(a.username).replace(/^@/, ''))}</span>` : ''}</span><span class="pill SCALE">connected</span>
          ${admin ? `<button class="btn sm danger" type="button" data-disconnect="${esc(a.id)}" data-name="${esc(a.display_name || a.username || '')}">Disconnect</button>` : ''}</li>`).join('')}</ul>`
        : '<div class="muted" style="margin-bottom:6px">No account connected yet.</div>'}
        ${r.error ? `<div class="muted" style="margin-top:4px">${esc(r.error)}</div>` : ''}
        ${admin ? `<div class="row" style="gap:8px;margin-top:10px;flex-wrap:wrap">${['tiktok', 'instagram', 'youtube'].map(p =>
          `<button class="btn" type="button" data-connect="${p}" ${pub.held ? 'disabled title="Unlocks with a paid plan"' : ''}>Connect ${names[p]}</button>`).join('')}</div>`
          : '<div class="muted">Ask a workspace admin to connect accounts.</div>'}`;
      $$('[data-disconnect]', mbody).forEach(b => (b.onclick = guard(async () => {
        const n = r.scheduled || 0;
        if (!await askConfirm(`Disconnect ${b.dataset.name || 'this account'}? HookTrack can no longer publish to it until you connect it again.`
          + (n ? `\n\n${n} post${n === 1 ? ' is' : 's are'} scheduled or queued: any that were meant for this account will fail.` : ''))) return;
        busy(b, true, 'Disconnecting…');
        try { await api('DELETE', `/api/publish/managed/accounts/${encodeURIComponent(b.dataset.disconnect)}`); }
        finally { busy(b, false); }
        toast('Disconnected'); route();
      })));
      $$('[data-connect]', mbody).forEach(b => (b.onclick = guard(async () => {
        busy(b, true, 'Opening…');
        try {
          const r2 = await api('POST', '/api/publish/managed/connect', { platform: b.dataset.connect });
          location.href = r2.auth_url;
        } finally { busy(b, false); }
      })));
    })();
  }
  api('GET', '/api/decisions/calibration').then(c => {
    const el = $('#calib'); if (!el) return;
    el.textContent = c.called_scale_early
      ? `How reliable have the calls been? Of ${c.called_scale_early} hook${c.called_scale_early === 1 ? '' : 's'} called Scale around day 3, ${c.still_winning_later} still beat the account's normal around day 30 (${Math.round((c.hit_rate || 0) * 100)}%).`
      : 'How reliable have the calls been? Not enough history yet: it needs hooks measured around day 3 and again around day 30. Every sync adds to it.';
  }).catch(() => { const el = $('#calib'); if (el) el.remove(); });
  if (admin) {
    $('#saverules').onclick = guard(async () => {
      const v = {}; $$('[data-s]', app).forEach(i => (v[i.dataset.s] = i.type === 'checkbox' ? (i.checked ? 1 : 0) : +i.value));
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
    if (!await askConfirm(self ? 'Leave this workspace?' : 'Remove this member?')) return;
    await api('DELETE', `/api/workspace/members/${b.dataset.rm}`);
    if (self) { state.me = await api('GET', '/api/auth/me'); resetViewState(); location.hash = '#/dashboard'; }
    route();
  })));
  $('#acct-review').onclick = guard(async () => {
    const plan = await api('GET', '/api/auth/account/deletion-preview');
    const label = { delete: 'will be deleted', leave: 'you will leave it', blocked: 'you are the only owner' };
    const blocked = plan.blocked.length > 0;
    $('#acct-plan').innerHTML = `<ul class="list">${plan.workspaces.map(w => `<li><span style="flex:1"><b>${esc(w.name)}</b> <span class="muted small">· ${w.members} member${w.members === 1 ? '' : 's'}</span>
        ${w.has_paid_plan ? `<div class="small" style="color:var(--bad)">Has a paid plan${w.paid_until ? ' until ' + new Date(w.paid_until).toLocaleDateString() : ''}${w.saved_card ? ' and a saved card (removed, so it will not renew)' : ''}. Remaining time is not refunded.</div>` : ''}</span>
        <span class="badge ${w.action === 'delete' ? 'bad' : ''}">${label[w.action]}</span></li>`).join('') || '<li class="muted">You are not in any workspace.</li>'}</ul>
      ${blocked ? `<div class="notice" style="margin:8px 0">You are the only owner of ${plan.blocked.map(w => esc(w.name)).join(', ')}, which other people use. Make another member an owner first (Settings → Members), or tick the box below to delete ${plan.blocked.length > 1 ? 'them' : 'it'} for everyone.</div>
        <label class="check" style="display:flex;gap:8px;align-items:flex-start"><input type="checkbox" id="acct-shared" style="width:auto;margin-top:3px"><span>Also delete the workspaces above for everyone, including their data</span></label>` : ''}
      <div class="fields" style="margin-top:10px"><div><label class="f">Type your email to confirm</label><input id="acct-email" autocomplete="off"></div>
        <div><label class="f">Your password</label><input id="acct-pw" type="password" autocomplete="current-password"></div></div>
      <div class="actions"><button class="btn danger" id="acct-go">Permanently delete my account</button></div>`;
    $('#acct-go').onclick = guard(async () => {
      if (!await askConfirm('Delete your account and the data described above forever?')) return;
      await api('DELETE', '/api/auth/account', { confirm_email: $('#acct-email').value, password: $('#acct-pw').value,
        delete_shared_workspaces: !!($('#acct-shared') && $('#acct-shared').checked) });
      state.me = null; state.authNote = 'Your account was deleted.';
      location.href = '/welcome';
    });
  });
  const delBtn = $('#del-go');
  if (delBtn) delBtn.onclick = guard(async () => {
    if (!await askConfirm(`Delete "${me.workspace.name}" and all its data forever?`)) return;
    await api('DELETE', '/api/workspace', { confirm_name: $('#del-name').value, password: $('#del-pw').value });
    toast('Workspace deleted');
    state.me = await api('GET', '/api/auth/me');
    if (!state.me.workspace) { state.me = null; state.authNote = 'Workspace deleted. Sign in or create a new workspace.'; await api('POST', '/api/auth/logout'); location.hash = '#/dashboard'; renderAuth('login'); return; }
    location.hash = '#/dashboard'; route();
  });
  $('#digest-on').onchange = guard(async e => { await api('PUT', '/api/auth/digest', { enabled: e.target.checked });
    state.me.user.digest = e.target.checked; toast(e.target.checked ? 'Weekly summary on' : 'Weekly summary off'); });
  $('#hot-on').onchange = guard(async e => { await api('PUT', '/api/auth/hot-alerts', { enabled: e.target.checked });
    state.me.user.hot_alerts = e.target.checked; toast(e.target.checked ? 'Breakout alerts on' : 'Breakout alerts off'); });
  const dp = $('#digest-preview');
  if (dp) dp.onclick = guard(async () => {
    const r = await api('POST', '/api/digest/preview');
    const pre = $('#digest-text'); pre.style.display = ''; pre.textContent = (r.empty ? '(Nothing to report yet, so no email would be sent. This is what it would look like.)\n\n' : '') + r.subject + '\n\n' + r.text;
    toast(r.sent ? 'Also sent to your inbox' : 'Shown below (email is not set up here, so nothing was mailed)');
  });
  $('#pw-go').onclick = guard(async () => {
    await api('POST', '/api/auth/password', { current: $('#pw-cur').value, new: $('#pw-new').value });
    $('#pw-cur').value = ''; $('#pw-new').value = ''; toast('Password changed');
  });
};

route();

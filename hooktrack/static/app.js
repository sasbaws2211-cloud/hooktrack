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
  calAccount: '', hookStatus: '', hookAccount: '', hookQuick: 'all', hookSearch: '', postsView: 'board', discNiche: null, discBand: '', discPlatform: '',
  campaignDraftId: null, campaignAssetId: null, campaignInterests: [],
};

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.startsWith('/api/auth/') && data.detail === 'Not signed in') { state.me = null; renderAuth(); throw new Error('Please sign in'); }
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
  { id: 'hooks', hash: 'hooks', title: 'Your hooks, scored for you', body: 'Each posted hook gets a score out of 30 from its real results, and a Scale / Tweak / Hold / Stop call. Press Score hooks to refresh them.' },
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
// Every post gets a content_type from the sync (short_video, long_video, image, carousel, text), or 'mixed'
// on older rows the sync hasn't re-classified yet (it corrects itself on the next sync). Shown as a small
// badge so a Short and a long-form video, or a scored video and an unscored photo/text post, don't look
// identical in a shared table.
const CONTENT_TYPE_INFO = {
  short_video: { icon: '🎬', label: 'Short video' },
  long_video: { icon: '🎥', label: 'Long-form video' },
  image: { icon: '🖼', label: 'Photo' },
  carousel: { icon: '🖼', label: 'Carousel' },
  text: { icon: '📝', label: 'Text post' },
};
const contentTypeBadge = ct => {
  const info = CONTENT_TYPE_INFO[ct];
  if (!info) return ct === 'mixed' ? '<span class="chip-type muted" title="Not yet classified by type: will update on the next sync">❔</span>' : '';
  return `<span class="chip-type" title="${esc(info.label)}">${info.icon}</span>`;
};
// A post missing perf entirely (rather than e.g. TOO_EARLY) means its platform never reports a view count
// for this kind of post (LinkedIn text, Facebook/Instagram photo posts): there is nothing to compare, not
// a zero to be ashamed of.
const viewsCell = p => (!p.perf && !p.views
  ? '<span class="muted" title="This kind of post has no view count on this platform, so there is nothing to track here">not tracked</span>'
  : fmt(p.views));
const winnerChip = (pf, p) => {
  if (!pf) {
    if (p && !p.views) return '<span class="muted" title="No view count to compare: this kind of post is not scored by views">not scored</span>';
    return '<span class="muted">-</span>';
  }
  const cls = { WINNER: 'SCALE', DUD: 'KILL', HOT_START: 'SCALE' }[pf.label] || '';
  const hot = pf.label === 'HOT_START';
  const text = hot ? `${pf.early_multiple}× day 1` : (pf.multiple != null ? `${pf.multiple}×` : (pf.label === 'TOO_EARLY' ? 'early' : '-'));
  const star = pf.label === 'WINNER' ? '★ ' : hot ? '🔥 ' : '';
  const peer = pf.baseline_source === 'peer' && pf.peer;
  const tip = (peer
    ? `Early read: compared with ${pf.peer.who} (typical ≈ ${fmt(pf.baseline)} views). ${pf.own_needed > 0 ? `${pf.own_needed} more judged post${pf.own_needed === 1 ? '' : 's'} and this account gets its own baseline.` : ''}`
    : `${WIN_TIP[pf.label] || ''}${pf.baseline ? ` (normal ≈ ${fmt(pf.baseline)} views)` : ''}`)
    + (hot && pf.early_baseline ? ` (usual first day ≈ ${fmt(pf.early_baseline)} views)` : '');
  const per = pf.reach_multiple != null && pf.growth_note
    ? `<div class="small muted" title="${esc(pf.growth_note)}">per follower ${pf.reach_multiple}×</div>` : '';
  return `<span class="pill ${cls}" title="${esc(tip)}">${star}${esc(text)}</span>${per}${pf.high_intent ? ' <span class="chip-intent" title="People saved, shared or commented far more than usual for this account">intent</span>' : ''}${peer ? ' <span class="chip-intent" title="Compared with similar accounts, not its own history yet">vs peers</span>' : ''}`;
};

const decisionSource = p => {
  const b = p.decision_basis || '';
  if (b.startsWith('Measured') && p.perf && p.perf.confidence) {
    return `measured · ${p.perf.confidence} confidence${p.perf.settled === false ? ' · may still move' : ''}`;
  }
  if (b.includes('compared with similar accounts')) return 'early read · vs similar accounts';
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
  [state.accounts, state.phones, state.settings, state.ai, state.sv, state.publish, state.queued, state.clientList, state.shotstack, state.wavespeed] = await Promise.all([
    api('GET', '/api/accounts'), api('GET', '/api/phones'), api('GET', '/api/settings'),
    api('GET', '/api/ai/status'),
    api('GET', '/api/connectors/sociavault').catch(() => ({ configured: false })),
    api('GET', '/api/addons/publish').catch(() => ({ enabled: false })),
    api('GET', '/api/publish/queued-count').catch(() => ({ count: 0 })),
    api('GET', '/api/clients').catch(() => ({ clients: [] })),
    api('GET', '/api/shotstack/status').catch(() => ({ configured: false })),
    api('GET', '/api/wavespeed/status').catch(() => ({ configured: false })),
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
  } else if (['discover', 'inspiration'].includes(tab)) {
    label = 'Ideas';
    links = [['discover', 'Discover', '#/discover'], ['inspiration', 'Saved', '#/inspiration']];
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
  document.body.classList.toggle('studio-fit', hash.split('?')[0] === 'studio');
  document.body.classList.toggle('wide-workspace', ['accounts', 'discover', 'inbox', 'clients', 'ads'].includes(hash.split('?')[0]));
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
  const NAV_HOME = { today: 'accounts', views: 'accounts', calendar: 'pipeline', scheduled: 'pipeline', billing: 'settings', inspiration: 'discover' };
  const navTab = NAV_HOME[tab] || (pages[tab] ? tab : 'dashboard');
  $$('#nav a').forEach(a => {
    const active = a.dataset.tab === navTab;
    a.classList.toggle('active', active);
    if (active) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  const myLoad = startRouteLoading(tab);
  try { await loadBase(); await page($('#app')); sectionStrip(tab); showCoachTip(); } catch (e) {
    if (state.me && myLoad === routeSeq) $('#app').innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p></div>`;
  } finally { endRouteLoading(myLoad, tab); }
}
window.addEventListener('hashchange', route);
const sidebar = $('#sidebar');
const sidebarToggle = $('#sidebar-toggle');
const sidebarBackdrop = $('#sidebar-backdrop');
const sidebarDesktop = window.matchMedia('(min-width: 861px)');
let sidebarCollapsed = false;
try { sidebarCollapsed = localStorage.getItem('hooktrack_sidebar_collapsed') === 'true'; } catch (e) { /* storage may be unavailable */ }
const closeSidebar = () => {
  sidebar.classList.remove('open');
  sidebarBackdrop.hidden = true;
  if (sidebarDesktop.matches) syncSidebarLayout();
  else {
    sidebarToggle.setAttribute('aria-expanded', 'false');
    sidebarToggle.setAttribute('aria-label', 'Open navigation');
    sidebarToggle.title = 'Open navigation';
  }
};
const syncSidebarLayout = () => {
  if (sidebarDesktop.matches) {
    sidebar.classList.remove('open');
    sidebarBackdrop.hidden = true;
    document.querySelector('.app-shell').classList.toggle('sidebar-collapsed', sidebarCollapsed);
    sidebarToggle.setAttribute('aria-expanded', String(!sidebarCollapsed));
    sidebarToggle.setAttribute('aria-label', sidebarCollapsed ? 'Expand navigation' : 'Collapse navigation');
    sidebarToggle.title = sidebarCollapsed ? 'Expand navigation' : 'Collapse navigation';
  } else {
    document.querySelector('.app-shell').classList.remove('sidebar-collapsed');
    sidebarToggle.setAttribute('aria-expanded', String(sidebar.classList.contains('open')));
    sidebarToggle.setAttribute('aria-label', sidebar.classList.contains('open') ? 'Close navigation' : 'Open navigation');
    sidebarToggle.title = sidebar.classList.contains('open') ? 'Close navigation' : 'Open navigation';
  }
};
if (sidebarToggle) {
  $$('#nav a').forEach(a => { a.title = a.querySelector('span')?.textContent.trim() || ''; });
  sidebarToggle.onclick = () => {
    if (sidebarDesktop.matches) {
      sidebarCollapsed = !document.querySelector('.app-shell').classList.contains('sidebar-collapsed');
      try { localStorage.setItem('hooktrack_sidebar_collapsed', String(sidebarCollapsed)); } catch (e) { /* preference remains active for this page */ }
      syncSidebarLayout();
      return;
    }
    if (sidebar.classList.contains('open')) closeSidebar();
    else {
      sidebar.classList.add('open');
      sidebarBackdrop.hidden = false;
      sidebarToggle.setAttribute('aria-expanded', 'true');
      sidebarToggle.setAttribute('aria-label', 'Close navigation');
      sidebarToggle.title = 'Close navigation';
    }
  };
  sidebarBackdrop.onclick = closeSidebar;
  $$('#nav a').forEach(a => a.addEventListener('click', closeSidebar));
  window.addEventListener('resize', syncSidebarLayout);
  window.addEventListener('keydown', e => { if (e.key === 'Escape' && sidebar.classList.contains('open')) closeSidebar(); });
  syncSidebarLayout();
}
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
  if (b.enabled && b.state === 'free') {
    bar.hidden = false; bar.className = 'trialbar';
    bar.innerHTML = `<span><b>Free plan:</b> YouTube tracking and decisions. Upgrade to track TikTok and Instagram and to publish.</span> <button type="button" class="btn sm" data-plans>See plans</button>`;
    bar.onclick = e => { if (e.target.closest('[data-plans]')) openPlanPicker().catch(err => toast(err.message, true)); };
    return;
  }
  if (!b.enabled || !['trial', 'expired'].includes(b.state)) { bar.hidden = true; return; }
  const left = b.trial.days_left;
  const ended = b.state === 'expired';
  bar.hidden = false;
  bar.className = 'trialbar' + (ended ? ' ended' : left <= 2 ? ' soon' : '');
  bar.innerHTML = ended
    ? `<span><b>${b.subscription.lapsed ? 'Your plan has ended.' : 'Choose a plan to get started.'}</b> You can view everything, but changes are paused until you do.</span> <button type="button" class="btn sm primary" data-plans>Choose a plan</button>`
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
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo"><img src="/logo-mark.svg" alt="" width="30" height="30"></span> HookTrack</div>
    ${ok ? `<h1>${kind === 'hot' ? 'Breakout alerts' : 'Weekly email'} switched off</h1><p class="muted">You will not get ${kind === 'hot' ? 'breakout alerts' : 'the weekly summary'} any more. You can turn it back on under Settings, Your account.</p>`
         : `<h1>Link not valid</h1><p class="muted">${esc(err)}</p>`}
    <a class="btn primary" href="#/dashboard">Go to HookTrack</a></div>`;
}

async function renderVerify(token) {
  document.body.classList.add('anon');
  let done = null, err = '';
  try { done = await api('POST', '/api/auth/verify', { token }); } catch (e) { err = e.message; }
  state.me = null;
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo"><img src="/logo-mark.svg" alt="" width="30" height="30"></span> HookTrack</div>
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

  let help = $('#help-btn');
  if (!help) {
    help = document.createElement('button');
    help.id = 'help-btn';
    help.type = 'button';
    help.className = 'btn sm help-btn';
    help.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M9.6 9a2.5 2.5 0 1 1 4.7 1.2c-.8 1.2-2.3 1.4-2.3 3"></path><path d="M12 17h.01"></path></svg>';
    help.title = 'How HookTrack works';
    help.setAttribute('aria-label', 'Help');
    const tools = $('#top-utilities');
    if (tools) tools.appendChild(help);
    help.onclick = () => openHelpGlossary();
  }
  let searchBtn = $('#search-btn');
  if (!searchBtn) {
    searchBtn = document.createElement('button');
    searchBtn.id = 'search-btn';
    searchBtn.type = 'button';
    searchBtn.className = 'btn sm';
    searchBtn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.8"></circle><path d="m16 16 4.5 4.5"></path></svg><span>Search anything</span><kbd>Ctrl K</kbd>';
    searchBtn.title = 'Search (Ctrl/⌘ K)';
    searchBtn.setAttribute('aria-label', 'Search (Ctrl/⌘ K)');
    const search = $('#utility-search');
    if (search) search.appendChild(searchBtn);
    searchBtn.onclick = () => openCommandPalette();
  }
  let themeBtn = $('#theme-btn');
  if (!themeBtn) {
    themeBtn = document.createElement('button');
    themeBtn.id = 'theme-btn';
    themeBtn.type = 'button';
    themeBtn.className = 'btn sm theme-btn';
    themeBtn.title = 'Toggle theme: system / dark / light';
    const tools = $('#top-utilities');
    if (tools) tools.appendChild(themeBtn);
    const syncThemeLabel = () => {
      const t = localStorage.getItem('hooktrack_theme') || 'system';
      const next = t === 'dark' ? 'light' : (t === 'light' ? 'system' : 'dark');
      const label = next === 'light' ? 'Light' : (next === 'system' ? 'Auto' : 'Dark');
      const icon = next === 'system'
        ? '<rect x="3" y="4" width="18" height="13" rx="2"></rect><path d="M8 21h8m-4-4v4"></path>'
        : next === 'light'
          ? '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"></path>'
          : '<path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5 8.5 8.5 0 1 0 20.5 14.2Z"></path>';
      themeBtn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icon}</svg><span>${label}</span>`;
      themeBtn.setAttribute('aria-label', `Switch to ${label.toLowerCase()} theme`);
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
function resetViewState() { state.calAccount = ''; state.hookAccount = ''; state.hookStatus = ''; state.hookQuick = 'all'; state.hookSearch = ''; }
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
  document.body.classList.remove('studio-fit');
  document.body.classList.add('anon');
  note = note || state.authNote || '';
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo"><img src="/logo-mark.svg" alt="" width="30" height="30"></span> HookTrack</div>
    <h1>${mode === 'login' ? 'Sign in' : 'Create your workspace'}</h1>
    <p class="muted">${mode === 'login' ? 'Welcome back.' : 'Each workspace is private: its accounts, views, posts and AI settings are never shared with another workspace.'}</p>
    ${note ? `<div class="notice">${esc(note)}</div>` : ''}
    <form id="auth-form" class="fields" style="grid-template-columns:1fr">
      ${mode === 'signup' ? `<div><label class="f">Your name</label><input name="name" autocomplete="name" maxlength="100"></div>
        <div><label class="f">Workspace name</label><input name="workspace_name" placeholder="e.g. Acme Growth" maxlength="100"></div>
        <div style="position:absolute;left:-9999px;top:auto;width:1px;height:1px;overflow:hidden" aria-hidden="true"><label>Leave this empty<input name="website" tabindex="-1" autocomplete="off"></label></div>` : ''}
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
    if (mode !== 'login') { body.timezone = browserTz(); const r = new URLSearchParams(location.search).get('ref'); if (r) body.ref = r; }
    try {
      state.me = await api('POST', mode === 'login' ? '/api/auth/login' : '/api/auth/signup', body);
      state.authNote = '';
      location.hash = '#/dashboard'; route();
    } catch (err) { $('#auth-err').textContent = err.message; }
  };
}

function renderForgot() {
  document.body.classList.add('anon');
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo"><img src="/logo-mark.svg" alt="" width="30" height="30"></span> HookTrack</div>
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
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo"><img src="/logo-mark.svg" alt="" width="30" height="30"></span> HookTrack</div>
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
  $('#app').innerHTML = `<div class="auth card"><div class="brand" style="margin-bottom:6px"><span class="logo"><img src="/logo-mark.svg" alt="" width="30" height="30"></span> HookTrack</div>
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
      { ok: s.has_accounts, title: 'Add accounts', body: 'Your TikTok, Instagram, YouTube, X, LinkedIn or Facebook handles.', href: '#/accounts', cta: 'Add accounts' },
      { ok: s.has_views, title: 'Pull in your posts and views', body: 'Press Sync on Hooks: your posts and their views come in by themselves from your handles. You can also log views by hand.', href: '#/hooks', cta: 'Open hooks', need: s.has_accounts },
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
          <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn primary" id="seed-demo">Load sample data</button><button class="btn" id="watch-demo">▶ Watch the demo</button></div>
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
    const wd = $('#watch-demo');
    if (wd) wd.onclick = () => { if (window.HTDemo) HTDemo.open(wd); else window.open('https://youtu.be/R4SvZlUu6P8', '_blank', 'noopener'); };
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
  const trendPoints = d.trend.map((t, i) => ({
    x: 16 + i * (568 / Math.max(1, d.trend.length - 1)),
    y: 142 - (t.views / maxT) * 112,
    week: t.week,
    views: t.views,
  }));
  const trendLine = trendPoints.map(p => `${p.x},${p.y}`).join(' ');
  const trendArea = trendPoints.length
    ? `M${trendPoints[0].x},150 L${trendPoints.map(p => `${p.x},${p.y}`).join(' L')} L${trendPoints[trendPoints.length - 1].x},150 Z`
    : '';
  const firstName = (state.me && state.me.user && state.me.user.name || '').trim().split(/\s+/)[0];
  const upcoming = d.upcoming.map(u => {
    const date = parseISO(String(u.post_date).slice(0, 10));
    return {
      ...u,
      day: new Intl.DateTimeFormat(undefined, { day: '2-digit' }).format(date),
      month: new Intl.DateTimeFormat(undefined, { month: 'short' }).format(date),
      weekday: new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(date),
    };
  });
  const showNext = !nextSeen();
  app.innerHTML = `
  <div class="page-head dash-head"><div class="dash-welcome"><div class="eyebrow">WEEK OF ${esc(d.week_start)}</div>
    <h1>${firstName ? `Welcome back, ${esc(firstName)}` : 'Your content, at a glance'}</h1>
    <div class="sub">See what is working, what needs attention, and what is coming up next.</div></div>
    <div class="dash-head-actions"><div class="row dash-create">
      <button type="button" class="btn primary" id="create-post-dash">+ New post</button>
    </div><div class="row dash-utilities">
      <button type="button" class="btn sm" id="copy-summary" title="Copy weekly summary">Copy summary</button>
      <button type="button" class="btn sm" id="export-menu" title="Export CSV">Export</button>
      <button type="button" class="btn sm" id="demo-dash" title="Watch the demo video">▶ Demo</button>
      <button type="button" class="btn sm" id="help-dash" title="Glossary">Help</button>
    </div></div></div>
  ${showNext ? `<div class="card next-banner" id="next-banner">
    <div><b>You’re set up — what’s next?</b>
      <ul class="next-list">
        <li><a href="#/discover">See what is working</a> for creators like you</li>
        <li><a href="#/inspiration">Keep saved ideas</a> from competitors (optional)</li>
        <li><a href="#/pipeline">Plan posts</a> on the board or calendar</li>
        <li><a href="#/settings">Settings</a> — connect your accounts to publish (TikTok, Instagram, YouTube, X, LinkedIn or Facebook)</li>
      </ul>
    </div>
    <button class="btn sm" id="next-dismiss">Dismiss</button>
  </div>` : ''}
  <div class="grid kpis">
    <div class="card kpi" data-metric="views"><div class="kpi-top"><div class="label">Views this week${d.week_estimated ? ' <span title="Some days are estimated from your posts\' totals" class="muted">(~)</span>' : ''}</div><span class="kpi-icon">◉</span></div><div class="value">${d.week_estimated ? '~' : ''}${fmt(d.week_views)}</div>
      <div class="delta" title="What your synced posts gained so far today">${d.today_views ? `${fmt(d.today_views)} so far today` : 'today not counted yet'}</div>
      <div class="delta ${delta > 0 ? 'up' : delta < 0 ? 'down' : ''}">${delta == null ? 'No prior week' : `${delta > 0 ? '▲' : delta < 0 ? '▼' : ''} ${Math.abs(delta)}% vs last week`}</div></div>
    <div class="card kpi" data-metric="average"><div class="kpi-top"><div class="label">Avg. per account</div><span class="kpi-icon">↗</span></div><div class="value">${fmt(d.avg_per_account)}</div><div class="delta">Across ${d.accounts} account${d.accounts === 1 ? '' : 's'}</div></div>
    <div class="card kpi" data-metric="scale"><div class="kpi-top"><div class="label">Ready to scale</div><span class="kpi-icon">↑</span></div><div class="value up">${d.decisions.SCALE}</div><div class="delta">${fmt(d.settings.scale_weekly)}+ weekly &amp; strong hook score</div></div>
    <div class="card kpi" data-metric="stop"><div class="kpi-top"><div class="label">Need a rethink</div><span class="kpi-icon">!</span></div><div class="value down">${d.decisions.KILL}</div><div class="delta">Stalled for ${d.settings.kill_after_posts}+ posts</div></div>
  </div>
  <div class="dash-layout">
    <div class="dash-main">
      <div class="card" id="next-card"><h2>Next actions</h2><div id="next-body" class="muted small">Loading…</div><div id="credit-foot"></div></div>
      <div class="card agenda-card"><div class="dash-card-head"><div><h2>Upcoming content</h2><div class="muted small">Your next posts, all in one place</div></div><a class="text-link" href="#/calendar">Open calendar <span aria-hidden="true">→</span></a></div>
        ${upcoming.length ? `<div class="agenda-list">${upcoming.map(u => `<a class="agenda-item" href="#/calendar">
          <span class="agenda-date"><span>${esc(u.weekday)}</span><b>${esc(u.day)}</b><span>${esc(u.month)}</span></span>
          <span class="agenda-copy"><b>${esc(short(u.hook, 68) || '(No hook yet)')}</b><span>${esc(u.handle || 'No account selected')}</span></span>
          ${pill(u.status)}</a>`).join('')}</div>` : `<div class="agenda-empty"><div class="agenda-empty-icon">▦</div><div><b>Your next idea starts here</b><div class="muted small">Plan upcoming content and keep your week moving.</div></div><a class="btn sm" href="#/calendar">Plan content</a></div>`}
      </div>
      <div class="card"><h2>Act on this</h2>${d.act_on.length ? `<ul class="list">${d.act_on.map(a =>
        `<li>${pill(a.decision)}<div><b>${esc(a.handle)}</b><div class="muted small">${esc(a.reason)}</div></div></li>`).join('')}</ul>`
        : '<p class="muted">Nothing to scale or kill right now.</p>'}
        ${d.unscored_posts ? `<p class="small muted">${d.unscored_posts} posted hooks aren't scored yet — <a href="#/hooks">score them</a> so SCALE decisions are accurate.</p>` : ''}</div>
      <div class="card"><h2>Top hooks <span class="muted small" style="font-weight:400">vs each account's own normal (similar accounts for new ones)</span></h2>${d.top_hooks.length ? `<ul class="list">${d.top_hooks.map(h =>
        `<li><div style="flex:1"><div>${esc(short(h.hook, 60))}</div><div class="muted small">${esc(h.handle)} · ${compact(h.views)} views${h.saves ? ` · ${compact(h.saves)} saves` : ''}${h.intent_per_1k != null ? ` · ${h.intent_per_1k} intent/1k` : ''}</div></div>${h.multiple != null ? winnerChip({ label: h.label, multiple: h.multiple, high_intent: h.high_intent, baseline: h.baseline, baseline_source: h.baseline_source, peer: h.peer, own_needed: h.own_needed }) : ''}${h.vsc != null ? ` <b>${h.vsc}/30</b>` : ''}</li>`).join('')}</ul>`
        : '<p class="muted">Log posts in <a href="#/hooks">Hooks</a> to see your best performers.</p>'}</div>
      <div class="card" id="activity-card"><h2>Recent activity</h2>
        <div id="activity-list" class="muted small">Loading…</div></div>
    </div>
    <div class="dash-rail">
      <div class="card ai-card"><div class="dash-card-head"><div><h2>AI assistant</h2><div class="muted small">Ask about your performance</div></div><span class="ai-spark" aria-hidden="true">✦</span></div>${aiNotice()}
        <form class="ask" id="ask-form"><input id="ask-q" placeholder="e.g. Which accounts should I stop this week, and why?" maxlength="1000" ${state.ai.available ? '' : 'disabled'}>
          <button class="btn ai" ${state.ai.available ? '' : 'disabled'}>Ask</button></form>
        <div class="chips">${['Which accounts should I scale?', 'Which niche is working best?', 'What do my winning hooks have in common?']
          .map(q => `<button type="button" data-q="${esc(q)}" ${state.ai.available ? '' : 'disabled'}>${esc(q)}</button>`).join('')}</div>
        <div id="answer"></div></div>
      <div class="card trend-card"><div class="dash-card-head"><div><h2>Performance trend</h2><div class="muted small">Weekly views · last 8 weeks</div></div><a class="text-link" href="#/views">Details <span aria-hidden="true">→</span></a></div>
        <div class="trend-chart-wrap"><svg class="trend-chart" viewBox="0 0 600 176" role="img" aria-label="Weekly views over the last eight weeks">
          <line x1="12" y1="30" x2="588" y2="30"></line><line x1="12" y1="86" x2="588" y2="86"></line><line x1="12" y1="150" x2="588" y2="150"></line>
          ${trendArea ? `<path class="trend-area" d="${trendArea}"></path><polyline class="trend-line" points="${trendLine}"></polyline>` : ''}
          ${trendPoints.map(p => `<circle class="trend-point" cx="${p.x}" cy="${p.y}" r="4"><title>${esc(p.week)}: ${fmt(p.views)} views</title></circle>`).join('')}
        </svg><div class="trend-labels">${trendPoints.map(p => `<span>${esc(p.week.slice(5))}</span>`).join('')}</div></div>
        <div class="trend-total"><span class="trend-dot"></span><span>This week</span><b>${fmt(d.week_views)} views</b></div></div>
      <div class="card"><h2>This week by phone</h2>${d.by_phone.map(p =>
        `<div class="hbar"><span>${esc(p.phone)}</span><div class="track"><div class="fill" style="width:${Math.round(p.views / maxP * 100)}%;background:${esc(p.color)}"></div></div>
         <span class="num" style="text-align:right">${fmt(p.views)}</span></div>`).join('')}</div>
      ${(d.movers || []).length ? `<div class="card"><h2>Week movers</h2>
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
      <div class="card"><h2>Weekly leaders</h2><ul class="list">${d.leaders.map(l =>
        `<li><b style="flex:1">${esc(l.handle)}</b><span>${fmt(l.views)}</span>${pill(l.decision)}</li>`).join('')}</ul></div>
    </div>
  </div>`;
  const ask = guard(async q => {
    if (!q.trim()) return;
    const box = $('#answer'); box.innerHTML = '<div class="answer"><span class="spin"></span> Thinking…</div>';
    try { const r = await api('POST', '/api/ai/ask', { question: q }); box.innerHTML = `<div class="answer">${esc(r.answer)}</div>`; }
    catch (e) { box.innerHTML = ''; throw e; }
  });
  $('#create-post-dash').onclick = () => postModal(null, { status: 'idea' }, route);
  $('#ask-form').addEventListener('submit', e => { e.preventDefault(); ask($('#ask-q').value); });
  $$('.chips button', app).forEach(b => b.addEventListener('click', () => { $('#ask-q').value = b.dataset.q; ask(b.dataset.q); }));
  const nd = $('#next-dismiss');
  if (nd) nd.onclick = () => { markNextSeen(); $('#next-banner')?.remove(); };
  const hd = $('#help-dash');
  if (hd) hd.onclick = () => openHelpGlossary();
  const dd = $('#demo-dash');
  if (dd) dd.onclick = () => { if (window.HTDemo) HTDemo.open(dd); else window.open('https://youtu.be/R4SvZlUu6P8', '_blank', 'noopener'); };
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
      : '<p class="muted">Nothing is waiting on you. Sync to pull in new content.</p>');
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
      <td><span class="handle">${esc(r.handle)}</span><div class="muted small">${esc(r.phone_label || '')} · ${window.socialPlatformIdentity(r.platform)}</div></td>
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
          <div class="muted small">${esc(a.phone_label || 'No phone')} · ${window.socialPlatformIdentity(a.platform)}${a.status === 'warming' ? ' · warming' : ''}</div></div>
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

// A card of your own is planned as one of these (never 'mixed': that's logic.py's own placeholder for a
// synced item the sync hasn't told apart yet, not something a person planning a post would pick). Video,
// photo and carousel can be uploaded and published here; a text post has nothing to attach, so it falls back
// to "post it on the platform yourself, then paste the link" the same way every format did before this.
const FORMAT_OPTIONS = [['short_video', '🎬 Short video'], ['long_video', '🎥 Long-form video'],
  ['image', '🖼 Photo'], ['carousel', '🖼 Carousel'], ['text', '📝 Text post']];
const isVideoFormat = ct => ct === 'short_video' || ct === 'long_video';
const isImageFormat = ct => ct === 'image' || ct === 'carousel';
const canUpload = ct => isVideoFormat(ct) || isImageFormat(ct);
function postModal(post, defaults, onDone) {
  const p = post || { account_id: '', post_date: iso(nowLocal()), status: 'scripted', ai_prompt: '', hook: '', body: '', caption: '', views: 0, comments: 0, intent: false, v: null, s: null, c: null, content_type: 'short_video', ...defaults };
  const initialFormat = p.content_type || 'short_video';
  const reviewStatus = p.team_review_status || 'not_requested';
  const reviewText = (status, note, reviewer) => ({
    not_requested: 'No team review requested. Publishing is not gated.',
    pending: 'Waiting for an owner or admin to review this post.',
    changes_requested: note || 'Changes are needed before this post can be approved.',
    approved: `Approved${reviewer ? ` by ${reviewer}` : ''}. Editing the content will require review again.`,
  }[status] || 'No team review requested. Publishing is not gated.');
  const reviewActions = status => {
    if (!post || !post.id) return '';
    if (status === 'pending') return isAdmin()
      ? '<button class="btn sm primary" data-team-review="approve" type="button">Approve</button><button class="btn sm" data-team-review="changes" type="button">Request changes</button>' : '';
    if (status === 'approved') return '';
    return `<button class="btn sm" data-team-review="request" type="button">${status === 'changes_requested' ? 'Resubmit for review' : 'Request team review'}</button>`;
  };
  // ---- the editor shows what this stage needs and tucks the rest away ----
  const STEPS = [['idea', 'Idea'], ['scripted', 'Scripted'], ['filmed', 'Filmed'], ['posted', 'Posted']];
  const stepOf = st => (st === 'analysed' ? 3 : Math.max(0, STEPS.findIndex(x => x[0] === st)));
  const stageHelp = (st, fmt) => ({
    idea: 'An idea. Write the hook and caption, then mark it as scripted.',
    scripted: isVideoFormat(fmt) ? 'Scripted. Film it, then drag your video into the box below.'
      : isImageFormat(fmt) ? 'Scripted. Add your image' + (fmt === 'carousel' ? 's' : '') + ' below.'
      : 'Scripted. Post it directly on the platform, then paste its link below.',
    filmed: isVideoFormat(fmt) ? 'Filmed and ready. Choose when it goes out.'
      : isImageFormat(fmt) ? 'Ready. Choose when it goes out.'
      : 'Ready. Post it on the platform, then paste its link below.',
    posted: 'Posted. Paste its link so its real results come back.',
    analysed: 'Analysed. This is how it did.',
  }[st] || '');
  const ctaButtons = (() => {
    if (!(post && post.id)) return '';
    if (post.external_id) {
      return POST_AS[post.decision] && !post.followup
        ? `<button class="btn primary" id="topost" title="Add this hook to Posts as ${POST_AS[post.decision]}">Post</button>` : '';
    }
    const on = !!(state.publish && state.publish.enabled);
    const media = !!(p.has_video_file || p.video_url || (p.image_count > 0) || (state.publish && state.publish.config && state.publish.config.default_media_url));
    const held = !!(state.publish && state.publish.held);
    const teamBlocked = ['pending', 'changes_requested'].includes(post.team_review_status);
    const why = teamBlocked ? 'Waiting for team approval before publishing'
      : held ? state.publish.held_message
      : !on ? 'Publishing is not set up on this server yet'
      : !canUpload(initialFormat) ? 'Only video, photo and carousel posts can be published from here today: post this directly on the platform, then paste its link'
      : !media ? (isVideoFormat(initialFormat) ? 'Attach a video first: move the card back to Scripted, upload it, then move it to Filmed'
          : 'Add at least one image first: move the card back to Scripted, upload it, then move it to Filmed')
      : 'Publish to social platforms';
    return `<button class="btn primary" id="dopub" ${normStatus(p.status) === 'filmed' ? '' : 'hidden'} ${on && media && !held && !teamBlocked ? '' : 'disabled'} title="${esc(why)}">📤 Publish</button>
      <button class="btn primary" id="filmsched" hidden ${on && !held && !teamBlocked ? '' : 'disabled'} title="${esc(teamBlocked ? why : 'The video is attached: mark this card Filmed and choose when it goes out')}">🎬 Mark filmed &amp; schedule</button>`;
  })();
  const st0 = normStatus(p.status);
  const steps = p.external_id ? '' : `<ol class="pm-steps" id="steps" aria-label="Where this post is">${STEPS.map(([k, l], i) =>
    `<li data-step="${i}" class="${i < stepOf(st0) ? 'done' : i === stepOf(st0) ? 'now' : ''}">${l}</li>`).join('')}</ol>`;
  const postedOnly = ['posted', 'analysed'].includes(st0) || !!p.external_id;
  const m = openModal(`<div class="pm-head"><h2>${post ? 'Edit post' : 'New post'}</h2><button class="pm-x" type="button" data-close aria-label="Close">×</button></div>
    ${steps}
    <div class="fields pm-core">
      <div class="full"><label class="f" id="hook-label">Hook (first 3 seconds)</label><input id="hook" value="${esc(p.hook)}" placeholder="The line that stops the scroll"></div>
      <div class="full"><label class="f">Account</label><select id="account">${accountOptions(p.account_id, 'Choose…')}</select></div>
      ${p.external_id ? '' : `<div class="full"><label class="f">Format</label><select id="format">${FORMAT_OPTIONS.map(([v, l]) => `<option value="${v}" ${initialFormat === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>`}
      <div class="full"><label class="f">Caption + hashtags</label><textarea id="caption">${esc(p.caption)}</textarea></div>
    </div>
    ${p.external_id ? '' : `<section class="team-workflow">
      <h3>Team ownership &amp; review</h3>
      <label class="f" for="assigned-to">Assigned to</label>
      <select id="assigned-to"><option value="">Unassigned</option></select>
      <div id="team-review-state" class="small muted" aria-live="polite">${esc(reviewText(reviewStatus, p.team_review_note, p.team_reviewer_name))}</div>
      <div id="team-review-actions" class="row team-review-actions" style="gap:8px;flex-wrap:wrap">${post && post.id ? reviewActions(reviewStatus) : '<span class="small muted">Save the post before requesting a review.</span>'}</div>
    </section>`}
    <div class="stage-panel" id="stage-panel">
      ${p.external_id ? '' : `<p class="stage-help" id="stage-help">${esc(stageHelp(st0, initialFormat))}</p>`}
      ${p.external_id ? '' : '<div class="row" style="gap:8px;margin:6px 0"><button class="btn primary" id="nextstage" type="button" hidden>Mark as scripted</button></div>'}
      <div id="vidupload" style="margin-top:6px">${post ? '' : '<div class="small muted">Create the post first' + (isVideoFormat(initialFormat) ? ', then drag your video in here.' : isImageFormat(initialFormat) ? ', then add your image(s) here.' : '.') + '</div>'}</div>
      <div id="vidstatus" class="small" style="margin-top:6px">${p.video_url ? `🎬 <a href="${esc(p.video_url)}" target="_blank" rel="noopener">Video ready</a>` : ''}</div>
      <div id="pubstatus" class="small" style="margin-top:4px">${p.external_post_id ? `📤 Published · ${esc(p.publish_platforms || '')} · id ${esc(p.external_post_id)}`
        : approvalStatusLine(p.approval) || (p.scheduled_for ? `📅 Scheduled ${esc(fmtWhen(p.scheduled_for))} (${esc(wsTz())})` : '')}</div>
    ${p.external_id ? '' : `<div class="full" id="linkblock" ${['posted', 'analysed'].includes(normStatus(p.status)) || p.url || p.result_post_id || !canUpload(initialFormat) ? '' : 'hidden'}><label for="posturl" id="posturl-label">Posted video link</label>
      <input id="posturl" type="url" inputmode="url" placeholder="https://www.tiktok.com/@you/video/…" value="${esc(p.url || '')}">
      ${!p.result_post_id && p.id ? '<div style="margin-top:6px"><button class="btn sm" id="findvid" type="button">🔎 Find it in synced content</button></div>' : ''}
      <div class="small muted" style="margin-top:4px">${p.result_post_id
        ? '✓ Linked to the synced post: its results show on this card.'
        : `Paste the full address once it is posted. After the next sync this card shows how it really did. (Short vm.tiktok.com links cannot be matched: open the ${isVideoFormat(initialFormat) ? 'video' : isImageFormat(initialFormat) ? 'photo' : 'post'} and copy the address bar.)`}</div></div>`}
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
      <span class="spacer"></span><span class="save-state" id="savestate" aria-live="polite"></span><button class="btn primary" id="save">${post ? 'Done' : 'Create post'}</button></div>`);
  m.querySelector('.modal').classList.add('post-editor');
  const curFormat = () => ($('#format', m) ? $('#format', m).value : initialFormat);
  const assignee = $('#assigned-to', m);
  if (assignee) {
    assignee.disabled = true;
    api('GET', '/api/workspace/members').then(members => {
      assignee.innerHTML = '<option value="">Unassigned</option>' + members.map(member =>
        `<option value="${member.id}">${esc(member.name || member.email)}</option>`).join('');
      assignee.value = p.assigned_to == null ? '' : String(p.assigned_to);
      assignee.disabled = false;
    }).catch(error => toast(error.message || 'Could not load workspace members', true));
  }

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
    if (pf && pf.baseline_source === 'peer' && pf.peer) {
      parts.push(`<div class="sig-row"><div class="sig-title">Compared with similar accounts</div>
        <div class="small">This account has ${pf.own_mature} judged post${pf.own_mature === 1 ? '' : 's'} so far, so this post is measured against ${esc(pf.peer.who)}: a typical account posts about <b>${fmt(pf.baseline)}</b> views (middle half: ${fmt(pf.peer.p25)} to ${fmt(pf.peer.p75)}). ${pf.own_needed > 0 ? `After ${pf.own_needed} more judged post${pf.own_needed === 1 ? '' : 's'} it is measured against this account's own history.` : ''}</div>
        <div class="small muted">Based on ${pf.peer.n_accounts} ${pf.peer.source === 'users' ? 'HookTrack accounts (anonymous)' : 'public accounts we sampled'}.${pf.peer.exact ? '' : ' A broader group is used because this niche and size has too few accounts yet.'} It is an early read: low confidence.</div></div>`);
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
      return `<div class="sig-row"><div class="sig-title">Saved ideas</div>
        ${items.length ? items.map(x => `<div class="small" data-insp-item="${x.id}" style="margin:6px 0 8px"><b>“${esc(x.hook || x.title || 'Untitled')}”</b>
          ${x.platform ? `<span class="muted"> · ${esc(x.platform)}</span>` : ''}
          ${x.notes ? `<div class="muted">${esc(short(x.notes, 160))}</div>` : ''}
          ${embedHtml(x.embed, x.source_url)}
          <button class="btn sm" type="button" data-insp-detach="${x.id}" style="margin-top:4px">Remove</button></div>`).join('')
          : '<div class="small muted">Nothing attached. Attach something that has the move you want to try, then make it yours.</div>'}
        <div class="row" style="margin-top:6px"><button class="btn sm" id="insp-add" type="button">+ Add a saved idea</button></div></div>`;
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
        const mm = openModal(`<h2>Attach a saved idea</h2>${pick.length ? `<ul class="list">${pick.map(i => `<li>
            <span style="flex:1;min-width:0"><b>${esc(short(i.hook_text || i.title || 'Untitled', 70))}</b>
              <div class="small muted">${esc(i.platform || '')}${i.notes ? ' · ' + esc(short(i.notes, 60)) : ''}</div></span>
            <button class="btn sm primary" data-att="${i.id}" type="button">Attach</button></li>`).join('')}</ul>`
          : '<p class="muted">Nothing left to attach. Save references on the <a href="#/inspiration">Saved</a> page first.</p>'}
          <div class="actions"><button class="btn" data-close>Close</button></div>`);
        $$('[data-att]', mm).forEach(y => (y.onclick = guard(async () => {
          await api('POST', `/api/posts/${post.id}/inspiration`, { inspiration_id: +y.dataset.att });
          mm.close(); await baselinePanel();
        })));
      });
    };
    if (!post.external_id && !b.brief) {   // an own card with no hook behind it (say, from Saved): its brief is the inspiration
      el.innerHTML = inspBlock();
      wireInsp();
      return;
    }
    if (b.brief) {                         // a card made from a hook: its job is a brief, not a reading of its own video
      const br = b.brief;
      const scale = br.kind === 'scale';
      el.innerHTML = `<div class="sig-row"><div class="sig-title">Brief ${scale ? '<span class="pill SCALE">scale</span>' : (br.promoted ? '' : '<span class="pill MUTATE">tweak</span>')}</div>
        <div class="small" style="margin:0 0 6px"><b>${scale
          ? 'Make a new post that works like the winner: the same opening move and format, with a new subject.'
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
    const fmt = b.content_type || post.content_type || 'short_video';
    if (!isVideoFormat(fmt)) {
      // no transcript to read on a photo, carousel or text post: the reference is just its own hook/caption
      const isImg = isImageFormat(fmt);
      const noun = isImg ? (fmt === 'carousel' ? 'carousel' : 'photo') : 'text post';
      const verb = isImg ? 'shows' : 'says';
      el.innerHTML = `<div class="sig-row"><div class="sig-title">Post baseline ${b.is_winner ? '<span class="pill SCALE">winner</span>' : ''}</div>
        <div class="small"><b>Hook:</b> ${post.hook ? `“${esc(post.hook)}”` : '<span class="muted">(none saved)</span>'}</div>
        <div class="small muted" style="margin-top:4px">This ${esc(noun)} has nothing to transcribe: match it by its hook and caption${isImg ? " - and, for a photo or carousel, what's actually in the image" : ''}.</div>
        <label class="f" style="margin-top:8px">What this ${esc(noun)} ${verb} (your note, optional)</label>
        <textarea id="vnotes" rows="2" maxlength="1500" placeholder="${isImg ? "e.g. a 3-slide carousel: bold claim on slide 1, proof on slide 2, offer on slide 3" : 'e.g. opens with a bold claim, backs it up with one stat, ends with a direct ask'}">${esc(b.video_notes || '')}</textarea>
        <div class="row" style="gap:8px;margin-top:8px;flex-wrap:wrap"><button class="btn sm" id="bl-notes" type="button">Save note</button></div>
        <div class="small muted" style="margin-top:6px">Use this on your own posts. For other people's, take the pattern, not their words.</div>
      </div>`;
      if ($('#bl-notes', m)) $('#bl-notes', m).onclick = guard(async () => {
        await api('PUT', `/api/posts/${post.id}/video-notes`, { notes: $('#vnotes', m).value }); toast('Note saved');
      });
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
    if ($('#bl-read', m)) $('#bl-read', m).onclick = guard(async () => {
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
    if ($('#bl-notes', m)) $('#bl-notes', m).onclick = guard(async () => {
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
      toast(isMatching() ? 'Matching video uploaded' : 'Video uploaded');
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

  // ---- images: a photo post (one image) or a carousel (several, in order) ----
  // post.images (from _get_post) only ever reaches here when the post object came from that exact endpoint;
  // a card opened from the Pipeline board or Hooks table carries only image_count, not the full list - so
  // this is always loaded fresh on demand instead of trusted from whatever object postModal was opened with.
  let imgList = (post && post.id) ? null : [];   // null = not loaded yet
  const ensureImages = async () => {
    if (imgList !== null) return;
    try { imgList = await api('GET', `/api/posts/${post.id}/images`); } catch { imgList = []; }
  };
  const IMAGE_EXT = /\.(jpe?g|png|webp)$/i;
  const checkImage = f => {
    if (!(String(f.type).startsWith('image/') || IMAGE_EXT.test(f.name || ''))) throw new Error('Drop a JPEG, PNG or WebP image.');
    if (f.size > 20 * 1024 * 1024) throw new Error('That image is over 20 MB. Export a smaller one.');
  };
  const imgCap = () => (curFormat() === 'carousel' ? 20 : 1);
  const zoneHtmlImg = (main, sub) => `<div id="imgdrop" class="dropzone compact" role="button" tabindex="0" aria-label="${esc(main)}">
      <div class="dz-main">${esc(main)}</div><div class="dz-sub">${esc(sub)}</div></div>
      <input type="file" id="imgfile" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" multiple hidden>`;
  const saveImageOrder = () => api('PATCH', `/api/posts/${post.id}/images/reorder`, { ids: imgList.map(x => x.id) });
  const wireImageActions = () => {
    $$('[data-img-rm]', m).forEach(b => b.onclick = guard(async () => {
      const id = +b.dataset.imgRm;
      if (!await askConfirm('Remove this image?')) return;
      await api('DELETE', `/api/posts/${post.id}/images/${id}`);
      imgList = imgList.filter(x => x.id !== id);
      imageBlock(); if (m._afterUpload) m._afterUpload(); if (onDone) onDone();
    }));
    $$('[data-img-up]', m).forEach(b => b.onclick = guard(async () => {
      const i = imgList.findIndex(x => x.id === +b.dataset.imgUp);
      if (i > 0) { [imgList[i - 1], imgList[i]] = [imgList[i], imgList[i - 1]]; await saveImageOrder(); imageBlock(); }
    }));
    $$('[data-img-down]', m).forEach(b => b.onclick = guard(async () => {
      const i = imgList.findIndex(x => x.id === +b.dataset.imgDown);
      if (i < imgList.length - 1) { [imgList[i], imgList[i + 1]] = [imgList[i + 1], imgList[i]]; await saveImageOrder(); imageBlock(); }
    }));
  };
  const wireImageZone = () => {
    const zone = $('#imgdrop', m), input = $('#imgfile', m);
    if (!zone || !input) return;
    const choose = () => input.click();
    zone.onclick = choose;
    zone.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(); } };
    const handleFiles = async files => {
      const room = Math.max(0, imgCap() - imgList.length);
      const list = Array.from(files || []).slice(0, room);
      if (!list.length) { toast(imgCap() === 1 ? 'A photo post takes one image. Switch Format to Carousel to add more.' : 'This carousel is full.', true); return; }
      zone.classList.add('busy');
      let added = 0;
      try {
        for (const f of list) {
          try {
            checkImage(f);
            const fd = new FormData(); fd.append('file', f);
            const r = await api('POST', `/api/posts/${post.id}/images/upload`, fd);
            imgList.push({ id: r.id, file_name: r.name, file_size: r.size, content_type: r.type, position: r.position });
            added++;
          } catch (e) { toast(e.message || 'Could not upload that image', true); }
        }
      } finally {
        if (added) { toast(added > 1 ? `${added} images added` : 'Image added'); if (m._afterUpload) m._afterUpload(); if (onDone) onDone(); }
        imageBlock();
      }
    };
    input.onchange = guard(async e => { const files = e.target.files; e.target.value = ''; await handleFiles(files); });
    const hasFiles = e => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
    zone.addEventListener('dragenter', e => { if (hasFiles(e)) { e.preventDefault(); zone.classList.add('over'); } });
    zone.addEventListener('dragover', e => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; zone.classList.add('over'); } });
    zone.addEventListener('dragleave', e => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('over'); });
    zone.addEventListener('drop', guard(async e => {
      e.preventDefault(); e.stopPropagation(); zone.classList.remove('over');
      if (hasFiles(e)) await handleFiles(e.dataTransfer.files);
    }));
  };
  const studioButtonHtml = () => (post && post.id && !post.external_id &&
    ((state.shotstack && state.shotstack.configured) || (state.wavespeed && state.wavespeed.configured)))
    ? `<a class="btn sm" href="#/studio?post=${post.id}" id="studio-open" target="_blank" rel="noopener">✂️ Edit in Studio</a>` : '';
  const imageBlock = () => {
    const el = $('#vidupload', m);
    if (!el) return;
    const label = curFormat() === 'carousel' ? 'Carousel images' : 'Photo';
    const grid = imgList.map((img, i) => `<div class="img-thumb">
        <img src="/api/posts/${post.id}/images/${img.id}/file" alt="" loading="lazy">
        <div class="img-thumb-bar">
          ${i > 0 ? `<button type="button" data-img-up="${img.id}" title="Move earlier">↑</button>` : ''}
          ${i < imgList.length - 1 ? `<button type="button" data-img-down="${img.id}" title="Move later">↓</button>` : ''}
          <button type="button" data-img-rm="${img.id}" title="Remove">✕</button>
        </div></div>`).join('');
    const canAddMore = imgList.length < imgCap();
    el.innerHTML = `<div class="sig-row"><div class="sig-title">${esc(label)}</div>
      ${grid ? `<div class="img-grid">${grid}</div>` : ''}
      ${canAddMore ? zoneHtmlImg(imgList.length ? 'Add another image' : 'Drag and drop image(s) here, or click to choose',
          curFormat() === 'carousel' ? 'JPEG, PNG or WebP · up to 20 images' : 'JPEG, PNG or WebP · up to 20 MB')
        : `<div class="small muted" style="margin-top:6px">${curFormat() === 'carousel' ? 'This carousel has its maximum 20 images.' : 'A photo post takes one image. Switch Format to Carousel to add more.'}</div>`}
      <div class="row" style="margin-top:8px">${studioButtonHtml()}</div>
      </div>`;
    wireImageZone();
    wireImageActions();
  };

  const uploadBlock = () => {
    const el = $('#vidupload', m);
    if (!el) return;
    // a card that is already posted (or analysed) is past the filming step: no matching-video section on it
    if (post && post.id && !post.external_id && ['posted', 'analysed'].includes($('#status', m) ? $('#status', m).value : normStatus(post.status))) { el.innerHTML = ''; return; }
    // a text post has nothing to attach: it is posted directly on the platform and linked by its posted
    // address instead (see #linkblock) - video and photo/carousel both upload and publish from here
    if (post && post.id && !post.external_id && !canUpload(curFormat())) {
      el.innerHTML = `<div class="sig-row"><div class="sig-title">No upload needed</div>
        <div class="small">HookTrack can't upload or publish a text post yet. Post it directly on the platform, then paste its link below once it's live: the next sync links it automatically and its real results show here.</div></div>`;
      return;
    }
    const curStatusForLock = $('#status', m) ? $('#status', m).value : normStatus(post && post.status);
    // a Filmed card keeps the media it was filmed with: no changes until it is back on Scripted
    if (post && post.id && !post.external_id && curStatusForLock === 'filmed' && isImageFormat(curFormat())) {
      if (imgList === null) {
        el.innerHTML = '<div class="small muted">Loading…</div>';
        ensureImages().then(() => { if (m._afterUpload) m._afterUpload(); if (($('#status', m) ? $('#status', m).value : normStatus(post.status)) === 'filmed') uploadBlock(); });
        return;
      }
      const lockedGrid = imgList.map(img => `<div class="img-thumb"><img src="/api/posts/${post.id}/images/${img.id}/file" alt="" loading="lazy"></div>`).join('');
      el.innerHTML = `<div class="sig-row"><div class="sig-title">${curFormat() === 'carousel' ? 'Carousel images' : 'Photo'}</div>
        ${lockedGrid ? `<div class="img-grid">${lockedGrid}</div>` : '<div class="small muted">No image is attached to this card.</div>'}
        <div class="small muted" style="margin-top:6px">🔒 Changes are disabled on a Filmed card. To add, remove or reorder images, move the card back to Scripted.</div></div>`;
      return;
    }
    if (post && post.id && !post.external_id && isImageFormat(curFormat())) {
      if (imgList === null) {
        el.innerHTML = '<div class="small muted">Loading…</div>';
        ensureImages().then(() => { if (m._afterUpload) m._afterUpload(); if (isImageFormat(curFormat())) imageBlock(); });
        return;
      }
      imageBlock();
      return;
    }
    const has = !!(post && post.id && post.has_video_file);
    // a Filmed card keeps the video it was filmed with: uploading and drag and drop are off until it is back on Scripted
    if (post && post.id && !post.external_id && curStatusForLock === 'filmed') {
      el.innerHTML = `<div class="sig-row"><div class="sig-title">Video</div>
        ${has ? fileCard() : '<div class="small muted">No video file is attached to this card.</div>'}
        <div class="small muted" style="margin-top:6px">🔒 Upload and drag and drop are disabled on a Filmed card. To add or change the video, move the card back to Scripted.</div></div>`;
      return;
    }
    const bl = (post && post._bl) || {};
    const matching = isMatching(), winner = isWinningHook();
    const curStatus = $('#status', m) ? $('#status', m).value : normStatus(post && post.status);
    const plainCard = !!(post && post.id && !post.external_id && ['idea', 'scripted'].includes(curStatus));   // any card of your own
    if (!winner && !matching && !has && !plainCard) { el.innerHTML = ''; return; }

    if (winner && !matching && !plainCard) {
      const mult = post.perf && post.perf.multiple;
      const fmt = post.content_type || 'short_video';
      const title = isVideoFormat(fmt) ? 'Winning Hook Video' : isImageFormat(fmt) ? 'Winning Hook Post' : 'Winning Hook Text';
      const what = isVideoFormat(fmt) ? 'video' : isImageFormat(fmt) ? (fmt === 'carousel' ? 'carousel' : 'photo') : 'text post';
      el.innerHTML = `<div class="sig-row"><div class="sig-title">${title}</div>
        <div class="small" style="margin:0 0 8px">This is the ${esc(what)} that proved the hook converts. Press <b>Post</b> to send it to Posts and make a new one that matches it.</div>
        ${mult ? `<div class="small muted">${mult}× this account's normal</div>` : ''}
        ${embedHtml(bl.embed, bl.source_url)}
        ${isVideoFormat(fmt) && bl.spoken_hook ? `<div class="small" style="margin-top:4px"><b>First words:</b> “${esc(bl.spoken_hook)}”</div>` : ''}
        ${isVideoFormat(fmt) && bl.sound ? `<div class="small"><b>Sound:</b> ${esc(bl.sound)}</div>` : ''}
        ${isVideoFormat(fmt) && has ? `<div style="margin-top:8px">${fileCard()}<div class="row" style="margin-top:6px"><button class="btn sm danger" id="vidrm" type="button">Remove this file</button></div></div>` : ''}</div>`;
      wireRemove();
      wireEmbeds(el, bl.embed);
      return;
    }

    // a matching post (or an older post that already holds a file): the video belongs to THIS post
    const parent = bl.parent;
    el.innerHTML = `<div class="sig-row"><div class="sig-title">${matching ? 'Matching video' : 'Video'}</div>
      <div class="small" style="margin:0 0 8px">${matching
        ? `${parent ? `Matches the winning hook video “${esc(short(parent.hook || '', 80))}”. ` : ''}Upload your new matching video here.`
        : has ? 'The video attached to this card. It is ready to schedule.'
          : 'Add the video you filmed for this card. Once it is attached you can mark it Filmed and schedule it.'}</div>
      ${matching && parent ? `<div style="margin:0 0 8px">${embedHtml(parent.embed, parent.source_url)}</div>` : ''}
      ${has ? fileCard() : ''}
      ${zoneHtml(has ? 'Drop a new video here to replace it, or click to choose' : (matching ? 'Drag and drop your matching video here, or click to choose a file' : 'Drag and drop your video here, or click to choose a file'),
                 'MP4, MOV or WebM · up to 300 MB · stays private to this workspace', has)}
      <div class="row" style="gap:8px;margin-top:8px;flex-wrap:wrap">
        <button class="btn sm" id="vidpick" type="button">${has ? 'Replace file' : '⬆ Upload a video'}</button>
        ${has ? '<button class="btn sm danger" id="vidrm" type="button">Remove file</button>' : ''}
        ${studioButtonHtml()}</div></div>`;
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
      ...($('#assigned-to', m) ? { assigned_to: $('#assigned-to', m).value ? +$('#assigned-to', m).value : null } : {}),
      ...($('#format', m) ? { content_type: $('#format', m).value } : {}),
      ...($('#posturl', m) ? { url: $('#posturl', m).value.trim() } : {}) };
  };
  let lastSaved = JSON.stringify(body());           // what the server has, for working out what changed
  const save = async () => {
    const b = body();
    if (!b.post_date) throw new Error('Pick a date');
    if (post) { Object.assign(post, await api('PATCH', `/api/posts/${post.id}`, b)); syncTeamReview(); }
    else {
      post = await api('POST', '/api/posts', b);
      if (b.url) post = await api('PATCH', `/api/posts/${post.id}`, { url: b.url });
      syncTeamReview();
    }
    lastSaved = JSON.stringify(body());
    return post;
  };
  const syncTeamReview = () => {
    if (!post) return;
    const status = post.team_review_status || 'not_requested';
    const state = $('#team-review-state', m);
    if (state) state.textContent = reviewText(status, post.team_review_note, post.team_reviewer_name);
    const actions = $('#team-review-actions', m);
    if (actions) {
      if (post.id) actions.innerHTML = reviewActions(status);
      wireTeamReviewActions();
    }
    if (['pending', 'changes_requested'].includes(status)) {
      ['#dopub', '#filmsched'].forEach(selector => {
        const button = $(selector, m);
        if (button) {
          button.disabled = true;
          button.title = 'Waiting for team approval before publishing';
        }
      });
    }
  };
  const wireTeamReviewActions = () => {
    const request = $('[data-team-review="request"]', m);
    if (request) request.onclick = guard(async () => {
      await save();
      await api('POST', `/api/posts/${post.id}/team-review/request`);
      toast('Sent to the workspace admins for review');
      m.close(); onDone && onDone();
    });
    const approve = $('[data-team-review="approve"]', m);
    if (approve) approve.onclick = guard(async () => {
      await save();
      await api('POST', `/api/posts/${post.id}/team-review/decision`, { action: 'approve' });
      toast('Post approved');
      m.close(); onDone && onDone();
    });
    const changes = $('[data-team-review="changes"]', m);
    if (changes) changes.onclick = () => {
      const reviewModal = openModal(`<h2>Request changes</h2>
        <p class="muted small">Explain what needs updating. The post cannot be published until it is revised and approved.</p>
        <textarea id="team-review-note" maxlength="2000" rows="4" placeholder="Describe the changes needed"></textarea>
        <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="team-review-note-save">Send feedback</button></div>`, true);
      $('#team-review-note-save', reviewModal).onclick = guard(async () => {
        const note = $('#team-review-note', reviewModal).value.trim();
        if (!note) throw new Error('Add a note describing the changes needed');
        await save();
        await api('POST', `/api/posts/${post.id}/team-review/decision`, { action: 'request_changes', note });
        reviewModal.close();
        toast('Changes requested');
        m.close(); onDone && onDone();
      });
    };
  };
  wireTeamReviewActions();

  // ---- autosave: an existing post saves itself a moment after you stop typing, sending only what changed ----
  const stateEl = $('#savestate', m);
  let saveTimer = null, inflight = false, again = false, touched = false;
  const setState = (t, bad) => { if (stateEl) { stateEl.textContent = t; stateEl.className = 'save-state' + (bad ? ' bad' : ''); } };
  const AUTOSAVE_FIELDS = new Set(['hook', 'account', 'assigned-to', 'caption', 'status', 'date', 'prompt', 'body', 'views', 'comments', 'intent', 'posturl', 'format']);
  const flush = async () => {
    if (!post || !post.id) return;
    if (inflight) { again = true; return; }
    const now = body(), prev = JSON.parse(lastSaved), changed = {};
    Object.keys(now).forEach(k => { if (JSON.stringify(now[k]) !== JSON.stringify(prev[k])) changed[k] = now[k]; });
    if (!Object.keys(changed).length) return;
    if ('post_date' in changed && !changed.post_date) return;           // a half-cleared date is not worth saving
    inflight = true; setState('Saving…');
    try {
      Object.assign(post, await api('PATCH', `/api/posts/${post.id}`, changed));
      syncTeamReview();
      lastSaved = JSON.stringify(now); touched = true; setState('Saved ✓');
    } catch (e) { setState('Not saved: ' + (e.message || 'try again'), true); }
    finally { inflight = false; if (again) { again = false; flush(); } }
  };
  const scheduleSave = ms => { if (!post || !post.id) return; clearTimeout(saveTimer); setState(''); saveTimer = setTimeout(flush, ms); };
  m.addEventListener('input', e => { if (AUTOSAVE_FIELDS.has(e.target.id) && e.target.id !== 'posturl') scheduleSave(900); });
  m.addEventListener('change', e => { if (AUTOSAVE_FIELDS.has(e.target.id)) scheduleSave(e.target.id === 'caption' || e.target.id === 'hook' ? 900 : 250); });
  // closing the window (the x, Escape) lets a pending save finish, then refreshes the page behind it
  const finalize = async () => {
    clearTimeout(saveTimer);
    await flush();
    while (inflight) await new Promise(r => setTimeout(r, 80));
    if (touched && onDone) onDone();
  };
  m.addEventListener('click', e => { if (e.target.closest('[data-close]')) finalize(); }, true);
  const onEsc = e => {
    if (!m.isConnected) { document.removeEventListener('keydown', onEsc); return; }
    if (e.key === 'Escape' && $('#modal-root').lastElementChild === m) finalize();
  };
  document.addEventListener('keydown', onEsc);
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
    const help = $('#stage-help', m); if (help) help.textContent = stageHelp(st, curFormat());
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
  const hasMedia = () => !!(post && (post.has_video_file || post.video_url
    || (isImageFormat(curFormat()) && Array.isArray(imgList) && imgList.length > 0)));
  const refreshFilmSched = () => {
    const fs = $('#filmsched', m); if (!fs) return;
    const st = $('#status', m) ? $('#status', m).value : normStatus(post && post.status);
    fs.hidden = !(post && post.id && ['idea', 'scripted'].includes(st) && hasMedia());
  };
  refreshFilmSched();
  if ($('#status', m)) $('#status', m).addEventListener('change', refreshFilmSched);
  m._afterUpload = refreshFilmSched;
  // nothing is posted yet on an Idea, Scripted or Filmed card, so there is no video to link or find: the link
  // field and the match button appear only once the card is Posted (or Analysed)
  const refreshLinkBlock = () => {
    const lb = $('#linkblock', m); if (!lb) return;
    const st = $('#status', m) ? $('#status', m).value : normStatus(post && post.status);
    lb.hidden = !(['posted', 'analysed'].includes(st) || (post && (post.url || post.result_post_id)) || !canUpload(curFormat()));
    const lbl = $('#posturl-label', m);
    if (lbl) lbl.textContent = isVideoFormat(curFormat()) ? 'Posted video link' : 'Posted link';
  };
  refreshLinkBlock();
  if ($('#status', m)) $('#status', m).addEventListener('change', refreshLinkBlock);
  if ($('#format', m)) $('#format', m).addEventListener('change', () => { uploadBlock(); refreshStage(); refreshLinkBlock(); refreshFilmSched(); });
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
      onApprovalRequested: approval => {
        post.approval = approval;
        const el = $('#pubstatus', m);
        if (el) el.textContent = `⏳ Waiting for client approval (sent ${fmtWhen(approval.created_at)})`;
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
const PLATFORM_LABEL = { tiktok: 'TikTok', instagram: 'Instagram', youtube: 'YouTube', twitter: 'X', linkedin: 'LinkedIn', facebook: 'Facebook' };

function fullLink(path) { return /^https?:\/\//.test(path) ? path : location.origin + path; }

function approvalStatusLine(approval) {
  if (!approval) return '';
  if (approval.status === 'pending') return `⏳ Waiting for client approval (sent ${esc(fmtWhen(approval.created_at))})`;
  if (approval.status === 'rejected') return `✎ Client asked for changes: "${esc(short(approval.client_note || '', 160))}"`;
  if (approval.status === 'approved' && approval.publish_error) return `✓ Approved, but it didn't go out: ${esc(short(approval.publish_error, 160))}`;
  return '';
}

// A client this post's account belongs to asked to approve posts first: show the request's state instead of a
// scheduler, since there is nothing to pick until the client has decided.
function pendingApprovalDialog(approval, opts) {
  const link = fullLink(approval.link);
  const m = openModal(`<h2>Waiting for client approval</h2>
    <p class="muted small">Sent ${esc(fmtWhen(approval.created_at))} for ${(approval.platforms || []).map(p => window.socialPlatformIdentity(p, PLATFORM_LABEL[p] || p)).join(' ')},
      to go out ${esc(fmtWhen(approval.schedule_at))} once approved.</p>
    <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn sm" id="ap-copy">Copy link</button>
      <a class="btn sm" href="${esc(link)}" target="_blank" rel="noopener">Open link</a></div>
    <div class="actions"><button class="btn" data-close>Close</button><button class="btn danger" id="ap-cancel">Cancel request</button></div>`, true);
  $('#ap-copy', m).onclick = guard(async () => { await navigator.clipboard.writeText(link); toast('Link copied'); });
  $('#ap-cancel', m).onclick = guard(async () => {
    if (!await askConfirm('Cancel this approval request? The link will stop working.')) return;
    await api('DELETE', `/api/approvals/${approval.id}`);
    m.close(); toast('Request cancelled'); if (opts.onMoved) opts.onMoved({});
  });
  return m;
}

// mode 'publish': choose platforms and now/later. mode 'reschedule': move a queued publish to a new time.
async function scheduleDialog(post, opts = {}) {
  const resched = opts.mode === 'reschedule';
  const acct = state.accounts.find(a => a.id === post.account_id);
  const client = !resched && acct && acct.client_id ? (state.clients || []).find(c => c.id === acct.client_id) : null;
  const gated = !!(client && client.requires_approval);
  if (gated && post.approval && post.approval.status === 'pending') {
    return pendingApprovalDialog(post.approval, opts);
  }
  const defaults = (state.publish && state.publish.config && state.publish.config.default_platforms) || ['tiktok'];
  const acctPlatform = acct ? String(acct.platform || '').toLowerCase() : '';
  const own = acct && ({ x: 'twitter' }[acctPlatform] || acctPlatform);
  const chosen = new Set(own && PLATFORM_LABEL[own] ? [own] : (Array.isArray(defaults) ? defaults : [String(defaults)]));
  const zone = wsTz();
  const sm = openModal(`<h2>${resched ? 'Move to a new time' : gated ? 'Send for client approval' : 'Publish / schedule'}</h2>
    ${gated ? `<p class="muted small">${esc(client.name)} approves posts before they go out. Pick a time and HookTrack will send them a link; it publishes automatically once they approve.</p>` : ''}
    ${resched ? '' : `<div class="fields"><div class="full"><label class="f">Publish to</label>
      <div class="row" style="gap:12px;flex-wrap:wrap;align-items:center" id="plats"></div>
      <div class="small" id="connnote" style="margin-top:6px"></div></div></div>`}
    <div class="fields">
      ${resched || gated ? '' : '<div class="full"><label class="f"><input type="radio" name="when" value="now"> Publish now</label></div>'}
      <div class="full">${resched || gated ? '' : '<label class="f"><input type="radio" name="when" value="later" checked> Schedule for</label>'}
        <div class="chips" id="slots"><span class="muted small">Finding free times…</span></div>
        <input type="datetime-local" id="sched" value="" style="margin-top:6px">
        <div class="small muted" style="margin-top:4px">Time zone: ${esc(zone)} (your workspace setting). Suggested times are free slots for this account.</div></div>
    </div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="pubgo">${resched ? 'Move' : gated ? 'Send for approval' : 'Go'}</button></div>`, true);
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
      return `<span class="row" style="gap:6px"><label class="check small"><input type="checkbox" data-plat="${k}" ${ok && chosen.has(k) ? 'checked' : ''} ${ok ? '' : 'disabled'}> ${window.socialPlatformIdentity(k, v)}</label>${ok ? '' :
        (isAdmin() ? `<button type="button" class="btn sm" data-conn="${k}" ${held ? 'disabled title="Unlocks with a paid plan"' : ''}>Connect ${window.socialPlatformIdentity(k, v)}</button>` : '<span class="muted small">not connected: ask an admin</span>')}</span>`;
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
    const later = resched || gated || $$('input[name=when]', sm).find(r => r.checked)?.value === 'later';
    const local = $('#sched', sm).value;
    if (later) {
      if (!local) throw new Error('Pick a date and time');
      const z = zoneParts(new Date().toISOString());
      if (z && local <= `${z.date}T${z.time}`) throw new Error('Schedule time must be in the future');
    }
    const btn = $('#pubgo', sm);
    busy(btn, true, resched ? 'Moving…' : gated ? 'Sending…' : later ? 'Scheduling…' : 'Publishing…');
    try {
      if (resched) {
        const job = await api('POST', `/api/posts/${post.id}/reschedule`, { schedule_local: local });
        sm.close(); toast(`Moved to ${fmtWhen(job.scheduled_for)}`); if (opts.onMoved) opts.onMoved(job);
        return;
      }
      const platforms = $$('[data-plat]', sm).filter(c => c.checked && !c.disabled).map(c => c.dataset.plat);
      if (!platforms.length) throw new Error('Choose at least one platform');
      if (gated) {
        const approval = await api('POST', `/api/posts/${post.id}/approval`, { platforms, schedule_local: local });
        sm.close();
        toast(`Sent to ${client.name} for approval`);
        if (opts.onApprovalRequested) opts.onApprovalRequested(approval);
        return;
      }
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
  const [rows, attention, failed] = await Promise.all([
    api('GET', '/api/publish/scheduled'), api('GET', '/api/publish/attention').catch(() => []),
    api('GET', '/api/publish/failed').catch(() => [])]);
  app.innerHTML = `${failed.length ? `<div class="card attention"><h3 style="margin-top:0">Didn't go out (${failed.length})</h3>
    <p class="muted small">These came due and could not be published. Open the card to publish it again, or dismiss it.</p>
    ${failed.map(f => `<div class="attn-row"><div style="flex:1;min-width:0"><b>${esc(f.handle || '-')}</b>
        <span class="muted">${esc(short(f.hook || '', 70))}</span>
        <div class="small">Planned ${esc(fmtWhen(f.scheduled_for))}: <span style="color:var(--bad)">${esc(f.error || 'it did not go out')}</span></div></div>
      <button class="btn sm primary" data-open-failed="${f.post_id}">Open card</button>
      <button class="btn sm" data-dismiss-failed="${f.id}">Dismiss</button></div>`).join('')}</div>` : ''}
  ${attention.length ? `<div class="card attention"><h3 style="margin-top:0">Needs your attention (${attention.length})</h3>
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
  $$('[data-open-failed]', app).forEach(b => b.onclick = guard(async () => {
    const p = await api('GET', `/api/posts/${b.dataset.openFailed}`);
    postModal(p, null, route);
  }));
  $$('[data-dismiss-failed]', app).forEach(b => b.onclick = guard(async () => {
    await api('DELETE', `/api/publish/failed/${b.dataset.dismissFailed}`); toast('Dismissed'); route();
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
  const allPosts = Object.values(board.columns || {}).flat();
  const countIn = names => names.reduce((sum, stage) => sum + ((board.columns && board.columns[stage]) || []).length, 0);
  app.innerHTML = `<div class="posts-page">
    <div class="page-head posts-head"><div><div class="eyebrow">CONTENT WORKSPACE</div><h1>Posts</h1>
      <div class="sub">Move each idea from first draft to published, and keep every account’s content moving.</div></div></div>
    <div class="grid posts-overview">
      <div class="card posts-metric"><span class="posts-metric-icon">▤</span><div><span>In your pipeline</span><b>${allPosts.length}</b><small>Across all stages</small></div></div>
      <div class="card posts-metric"><span class="posts-metric-icon ideas">✎</span><div><span>Ideas &amp; scripts</span><b>${countIn(['idea', 'scripted'])}</b><small>Ready for the next production step</small></div></div>
      <div class="card posts-metric"><span class="posts-metric-icon filmed">▶</span><div><span>Filmed</span><b>${countIn(['filmed'])}</b><small>Ready to schedule or publish</small></div></div>
      <div class="card posts-metric"><span class="posts-metric-icon published">✓</span><div><span>Published</span><b>${countIn(['posted', 'analysed'])}</b><small>Results can be reviewed</small></div></div>
    </div>
    <div class="posts-toolbar">
      <div class="posts-toolbar-note"><b>Production board</b><span>Drag cards to move them · Select cards for bulk actions</span></div>
      <div class="row posts-actions">
      ${toggle}
      <select id="pa" style="width:auto">${accountOptions(accountFilter, 'All accounts')}</select>
      <input id="pipeline-search" type="search" placeholder="Search posts…" aria-label="Search posts by title or account">
      <select id="bulk-stage" style="width:auto">
        <option value="">Move selected to…</option>
        ${stages.map(s => `<option value="${s}">${PIPE_LABEL[s] || s}</option>`).join('')}
      </select>
      <button class="btn ai" id="ideas" title="AI suggests new Idea cards from your winners and your saved ideas">✨ Ideas</button>
      <button class="btn primary" id="new">+ Post</button>
      </div>
    </div>
    <div id="bulk-bar" class="bulk-bar" hidden><span id="bulk-count">0 selected</span>
      <button type="button" class="btn sm" id="bulk-clear">Clear selection</button></div>
    <div class="pipeline" id="board">
      ${stages.map(s => {
        const cards = board.columns[s] || [];
        const emptyGuidance = {
          idea: 'Capture a topic or post idea to get your pipeline moving.',
          scripted: 'Move an idea here when it is ready to be shaped into a script.',
          filmed: 'Move a scripted post here once its video is ready.',
          posted: 'Move a post here after it goes live.',
          analysed: 'Move a published post here when you are ready to review its results.',
        }[s] || 'Move a post here when it is ready for this stage.';
        return `<div class="pipe-col" data-stage="${s}">
          <div class="pipe-col-h"><span>${PIPE_LABEL[s] || s}</span><span class="n" data-stage-count="${s}">${cards.length}</span></div>
          <div class="pipe-cards" data-stage="${s}">
            ${cards.map(p => `<div class="pipe-card" draggable="true" data-id="${p.id}"
              data-search="${esc([p.hook, p.body, p.caption, p.handle].filter(Boolean).join(' ').toLowerCase())}">
              <label class="pipe-select"><input type="checkbox" data-pipe-select="${p.id}" aria-label="Select ${esc(short(p.hook || p.body || 'Untitled post', 72))}"> Select</label>
              ${['filmed', 'posted', 'analysed'].includes(s) ? `<button type="button" class="pipe-del" data-pipe-del="${p.id}" title="Delete this post" aria-label="Delete this post">🗑</button>` : ''}
              <div class="h">${esc(short(p.hook || p.body || 'Untitled', 72))}</div>
              <div class="meta">
                <span class="band-dot" style="background:${esc(p.phone_color || 'var(--accent)')}"></span>
                <span class="handle-s">${esc(p.handle || '—')}</span>
                ${(() => {
                  // at most two chips: what the card needs now, and its due date only when that is worth a look
                  const vid = !!(p.video_url || p.has_video_file);
                  const chips = [];
                  if (p.publish_failed) chips.push(`<span class="tag warn" title="${esc(p.publish_failed)}">didn't post</span>`);
                  if (p.team_review_status === 'pending') chips.push('<span class="tag">⏳ team review</span>');
                  else if (p.team_review_status === 'changes_requested') chips.push(`<span class="tag warn" title="${esc(p.team_review_note || '')}">✎ team changes</span>`);
                  else if (p.team_review_status === 'approved') chips.push('<span class="tag good">✓ team approved</span>');
                  if (p.approval && p.approval.status === 'pending') chips.push(`<span class="tag" title="Sent ${esc(fmtWhen(p.approval.created_at))}">⏳ pending approval</span>`);
                  else if (p.approval && p.approval.status === 'rejected') chips.push(`<span class="tag warn" title="${esc(p.approval.client_note || '')}">✎ changes requested</span>`);
                  else if (p.approval && p.approval.status === 'approved' && p.approval.publish_error) chips.push(`<span class="tag warn" title="${esc(p.approval.publish_error)}">approved, didn't post</span>`);
                  if (p.scheduled_for && !p.external_post_id && s === 'filmed') chips.push(`<span class="tag good" title="Queued to publish (${esc(wsTz())})">📅 ${esc(fmtWhen(p.scheduled_for))}</span>`);
                  else if (s === 'scripted' && !vid) chips.push('<span class="tag warn" title="Add the filmed video before this can be scheduled">needs video</span>');
                  else if (['scripted', 'filmed'].includes(s) && vid && !p.external_post_id) chips.push('<span class="tag good" title="The video is attached: open the card to schedule it">ready</span>');
                  else if (['posted', 'analysed'].includes(s) && !p.result && p.views) chips.push(`<span>${fmt(p.views)} views</span>`);
                  if (['idea', 'scripted', 'filmed'].includes(s) && p.post_date && !(p.scheduled_for && s === 'filmed')) {
                    const d = Math.round((parseISO(p.post_date) - parseISO(iso(nowLocal()))) / 864e5);
                    if (d < 0) chips.push(`<span class="stale" title="This card's due date has passed">overdue ${-d}d</span>`);
                    else if (d <= 1) chips.push(`<span title="Due date">${d === 0 ? 'due today' : 'due tomorrow'}</span>`);
                  }
                  if (['idea', 'scripted'].includes(s) && p.created_at && Math.floor((Date.now() - new Date(p.created_at).getTime()) / 864e5) >= 14) chips.push('<span class="stale" title="Waiting two weeks or more: film it or delete it">14d+</span>');
                  return chips.join('');
                })()}
              </div>
              ${p.assignee_name ? `<div class="small muted pipe-assignee">Assigned to ${esc(p.assignee_name)}</div>` : ''}
              ${p.result ? `<div class="pipe-result" title="Real results from the synced post">📊 ${fmt(p.result.views)} views${p.result.multiple != null ? ` · ${p.result.multiple}× normal` : ''}${p.result.original_multiple != null ? ` <span class="muted">(original ${p.result.original_multiple}×)</span>` : ''}
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
                    if (p.url) return '<div class="pipe-result muted" title="The next sync will find this post from the link you pasted">⏳ waiting for sync</div>';
                    const ageDays = Math.round((parseISO(iso(nowLocal())) - parseISO(p.post_date)) / 864e5);
                    if (p.external_post_id && ageDays < 3) return '<div class="pipe-result muted" title="HookTrack published this: its link comes back on its own after the next sync">⏳ link coming back</div>';
                    return `<button type="button" class="btn sm" data-find="${p.id}" title="No link was pasted and the sync has not matched this card: pick the posted content yourself">🔎 Link my post</button>`;
                  })() : '')}
            </div>`).join('')}
            <div class="pipe-empty" data-empty-stage="${s}" ${cards.length ? 'hidden' : ''}>
              <b data-empty-title>No ${esc((PIPE_LABEL[s] || s).toLowerCase())} posts yet</b>
              <span data-empty-guidance>${esc(emptyGuidance)}</span>
              ${s === 'idea' && !cards.length ? '<button type="button" class="btn sm" data-empty-create="idea">＋ Add an idea</button>' : ''}
            </div>
          </div>
        </div>`;
      }).join('')}
    </div>
  </div>`;

  $$('[data-view]', app).forEach(b => b.onclick = () => { state.postsView = b.dataset.view; route(); });
  $('#pa').onchange = e => { state.pipeAccount = e.target.value; state.calAccount = e.target.value; route(); };
  $('#new').onclick = () => postModal(null, { status: 'idea', account_id: state.pipeAccount || '' }, route);
  $('#ideas').onclick = () => ideasModal(route);
  $$('[data-empty-create]', app).forEach(button => {
    button.onclick = () => postModal(null, { status: button.dataset.emptyCreate, account_id: state.pipeAccount || '' }, route);
  });

  let dragId = null;
  const selected = new Set();
  const search = $('#pipeline-search');
  const filterCards = () => {
    const query = search.value.trim().toLowerCase();
    $$('.pipe-col', app).forEach(col => {
      const cards = $$('.pipe-card', col);
      let visible = 0;
      cards.forEach(card => {
        const matches = !query || card.dataset.search.includes(query);
        card.hidden = !matches;
        if (matches) visible++;
      });
      const count = $('[data-stage-count]', col);
      if (count) count.textContent = query ? `${visible}/${cards.length}` : String(cards.length);
      const empty = $('[data-empty-stage]', col);
      if (empty) {
        empty.hidden = visible > 0;
        const title = $('[data-empty-title]', empty);
        const guidance = $('[data-empty-guidance]', empty);
        const addIdea = $('[data-empty-create]', empty);
        if (title) title.textContent = query ? 'No matching posts' : `No ${(PIPE_LABEL[col.dataset.stage] || col.dataset.stage).toLowerCase()} posts yet`;
        if (guidance) guidance.textContent = query ? 'Try another title or account.' : ({
          idea: 'Capture a topic or post idea to get your pipeline moving.',
          scripted: 'Move an idea here when it is ready to be shaped into a script.',
          filmed: 'Move a scripted post here once its video is ready.',
          posted: 'Move a post here after it goes live.',
          analysed: 'Move a published post here when you are ready to review its results.',
        }[col.dataset.stage] || 'Move a post here when it is ready for this stage.');
        if (addIdea) addIdea.hidden = !!query;
      }
    });
  };
  search.oninput = filterCards;
  const syncBulk = () => {
    const n = selected.size;
    const bar = $('#bulk-bar');
    if (bar) { bar.hidden = n === 0; }
    const cnt = $('#bulk-count');
    if (cnt) cnt.textContent = n ? `${n} selected` : '0 selected';
    $$('.pipe-card', app).forEach(c => {
      const isSelected = selected.has(c.dataset.id);
      c.classList.toggle('selected', isSelected);
      const checkbox = $('[data-pipe-select]', c);
      if (checkbox) checkbox.checked = isSelected;
    });
  };
  $$('[data-pipe-select]', app).forEach(checkbox => {
    checkbox.addEventListener('click', e => e.stopPropagation());
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) selected.add(checkbox.dataset.pipeSelect);
      else selected.delete(checkbox.dataset.pipeSelect);
      syncBulk();
    });
  });
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
      if (e.defaultPrevented || e.target.closest('button, input, label')) return;
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
  const posts = await api('GET', `/api/posts?${q}&source=pipeline`);
  const queued = p => !!(p.scheduled_for && !p.external_post_id && !p.external_id);   // waiting in HookTrack's queue
  const dayOf = p => (queued(p) && zoneParts(p.scheduled_for) || {}).date || p.post_date;
  const by = {}; posts.forEach(p => (by[dayOf(p)] = by[dayOf(p)] || []).push(p));
  const monthStart = iso(first);
  const monthEnd = iso(new Date(first.getFullYear(), first.getMonth() + 1, 0));
  const monthPosts = posts.filter(p => dayOf(p) >= monthStart && dayOf(p) <= monthEnd);
  const queuedCount = monthPosts.filter(queued).length;
  const activeCount = monthPosts.filter(p => ['idea', 'scripted', 'filmed'].includes(normStatus(p.status))).length;
  const scheduledDays = new Set(monthPosts.map(dayOf)).size;
  const today = iso(nowLocal());
  const cells = [];
  const agendaDays = [];
  const renderCalendarPost = (p, index) => {
    const status = normStatus(p.status);
    const when = queued(p) ? (zoneParts(p.scheduled_for) || {}).time : '';
    const movable = !p.external_id && !['posted', 'analysed'].includes(status);
    const statusLabel = queued(p) ? 'Queued' : ['posted', 'analysed'].includes(status) ? 'Published' : PIPE_LABEL[status] || status;
    const account = p.handle || 'Account';
    const title = short(p.hook || p.caption || 'Untitled post', 52);
    return `<button class="chip ${status} ${queued(p) ? 'queued' : ''} ${index >= 3 ? 'chip-extra' : ''}"
      data-id="${p.id}" ${movable ? 'draggable="true"' : ''}
      style="--c:${esc(p.phone_color || 'var(--accent)')}"
      aria-label="${esc(`${account}, ${statusLabel}${when ? ` at ${when}` : ''}: ${p.hook || p.caption || 'Untitled post'}`)}"
      title="${esc(`${account} · ${statusLabel}: ${p.hook || p.caption || 'Untitled post'}${when ? ` · publishes ${fmtWhen(p.scheduled_for)} (${wsTz()})` : ''}${movable ? ' · drag to another day to move it' : ''}`)}">
      <span class="chip-copy">${when ? `<span class="chip-time">${esc(when)}</span>` : ''}
        <span class="chip-title">${esc(title)}</span>
        <span class="chip-account">${esc(account)}${p.assignee_name ? ` · ${esc(p.assignee_name)}` : ''}</span></span>
      <span class="chip-status" title="${esc(p.team_review_status === 'pending' ? 'Waiting for team review' : p.team_review_status === 'changes_requested' ? 'Team changes requested' : p.team_review_status === 'approved' ? 'Team approved' : '')}">${esc(p.team_review_status === 'pending' ? 'Review' : p.team_review_status === 'changes_requested' ? 'Changes' : statusLabel)}</span></button>`;
  };
  for (let i = 0; i < 42; i++) {
    const d = addDays(gridStart, i), k = iso(d), list = (by[k] || []).slice().reverse();
    const inMonth = d.getMonth() === first.getMonth();
    const dayLabel = d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
    const chips = list.map(renderCalendarPost).join('');
    cells.push(`<div class="day ${inMonth ? '' : 'out'} ${k === today ? 'today' : ''}" data-day="${k}"
        role="gridcell" tabindex="0" aria-label="${esc(`${dayLabel}, ${list.length} post${list.length === 1 ? '' : 's'}. Press Enter or Space to add a post.`)}">
      <div class="dn"><span>${d.getDate()}</span>${list.length ? `<span class="day-count">${list.length}</span>` : ''}</div>
      ${chips}
      ${list.length > 3 ? `<button type="button" class="calendar-more" data-calendar-more aria-expanded="false">+${list.length - 3} more</button>` : ''}</div>`);
    if (inMonth) agendaDays.push(`<section class="calendar-agenda-day ${k === today ? 'today' : ''}">
      <div class="calendar-agenda-date"><b>${d.toLocaleDateString(undefined, { weekday: 'short' })}</b>
        <span>${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span></div>
      <div class="calendar-agenda-items">${list.length ? list.map((p, index) => renderCalendarPost(p, index)).join('') :
        '<span class="calendar-agenda-empty">No posts planned</span>'}</div>
      <button type="button" class="btn sm calendar-agenda-add" data-day-create="${k}" aria-label="Add a post on ${esc(dayLabel)}">+ Add</button>
    </section>`);
  }
  const weekRows = Array.from({ length: 6 }, (_, week) =>
    `<div class="calendar-row" role="row">${cells.slice(week * 7, week * 7 + 7).join('')}</div>`).join('');
  app.innerHTML = `<div class="posts-page calendar-page">
    <div class="page-head posts-head"><div><div class="eyebrow">CONTENT WORKSPACE</div><h1>Posts calendar</h1>
      <div class="sub">Plan your publishing rhythm. Drag a post to change its date; queued posts keep their publish time (${esc(wsTz())}).</div></div></div>
    <div class="grid posts-overview">
      <div class="card posts-metric"><span class="posts-metric-icon">▤</span><div><span>Posts this month</span><b>${monthPosts.length}</b><small>${first.toLocaleString('default', { month: 'long' })}</small></div></div>
      <div class="card posts-metric"><span class="posts-metric-icon queued">◷</span><div><span>Queued to publish</span><b>${queuedCount}</b><small>Scheduled through HookTrack</small></div></div>
      <div class="card posts-metric"><span class="posts-metric-icon ideas">✎</span><div><span>In production</span><b>${activeCount}</b><small>Ideas, scripts, and filmed posts</small></div></div>
      <div class="card posts-metric"><span class="posts-metric-icon published">▦</span><div><span>Active days</span><b>${scheduledDays}</b><small>Days this month with posts</small></div></div>
    </div>
    <div class="posts-toolbar calendar-toolbar">
      <div class="posts-toolbar-note"><b>Publishing calendar</b><span>Choose a day to add or edit a post</span></div>
      <div class="row posts-actions">
      ${toggle}
      <select id="cf" style="width:auto">${accountOptions(accountFilter, 'All accounts')}</select>
      <button class="btn" id="prev">‹</button><b style="min-width:130px;text-align:center">${first.toLocaleString('default', { month: 'long', year: 'numeric' })}</b>
      <button class="btn" id="next">›</button><button class="btn" id="today">Today</button>
      <button class="btn ai" id="plan">✨ AI plan week</button>
      </div>
    </div>
    <div class="calendar-legend" aria-label="Post status legend">
      <span><i class="calendar-status-dot idea"></i>Idea</span><span><i class="calendar-status-dot scripted"></i>Scripted</span>
      <span><i class="calendar-status-dot filmed"></i>Filmed</span><span><i class="calendar-status-dot queued"></i>Queued</span>
      <span><i class="calendar-status-dot posted"></i>Published</span><span class="calendar-color-note">Left border color identifies the account group.</span>
    </div>
    <div class="cal posts-calendar" role="grid" aria-rowcount="7" aria-colcount="7"
      aria-label="${esc(first.toLocaleString('default', { month: 'long', year: 'numeric' }))}">
      <div class="calendar-row" role="row">${DOW.map(d => `<div class="dow" role="columnheader">${d}</div>`).join('')}</div>
      ${weekRows}</div>
    <div class="calendar-agenda" aria-label="Monthly post agenda">${agendaDays.join('')}</div>
  </div>`;

  $$('[data-view]', app).forEach(b => b.onclick = () => { state.postsView = b.dataset.view; route(); });
  const shift = n => { state.calMonth = iso(new Date(first.getFullYear(), first.getMonth() + n, 1)); route(); };
  $('#prev').onclick = () => shift(-1); $('#next').onclick = () => shift(1);
  $('#today').onclick = () => { const n0 = nowLocal(); state.calMonth = iso(new Date(n0.getFullYear(), n0.getMonth(), 1)); route(); };
  $('#cf').onchange = e => { state.calAccount = e.target.value; state.pipeAccount = e.target.value; route(); };
  $('#plan').onclick = () => planModal();
  $$('[data-calendar-more]', app).forEach(button => button.onclick = e => {
    e.stopPropagation();
    const day = button.closest('.day');
    const expanded = day.classList.toggle('expanded');
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? 'Show less' : `+${day.querySelectorAll('.chip-extra').length} more`;
  });
  $$('[data-day-create]', app).forEach(button => button.onclick = e => {
    e.stopPropagation();
    postModal(null, { post_date: button.dataset.dayCreate, status: 'scripted', account_id: state.calAccount || state.pipeAccount || '' }, route);
  });
  $$('.chip[data-id]', app).forEach(button => button.onclick = e => {
    e.stopPropagation();
    postModal(posts.find(p => p.id === +button.dataset.id), null, route);
  });
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
  $$('.day', app).forEach(el => {
    el.addEventListener('click', e => {
      if (e.target.closest('button')) return;
      postModal(null, { post_date: el.dataset.day, status: 'scripted', account_id: state.calAccount || state.pipeAccount || '' }, route);
    });
    el.addEventListener('keydown', e => {
      if (e.target !== el || !['Enter', ' '].includes(e.key)) return;
      e.preventDefault();
      postModal(null, { post_date: el.dataset.day, status: 'scripted', account_id: state.calAccount || state.pipeAccount || '' }, route);
    });
  });
}


pages.calendar = async app => {
  // Calendar is a secondary view of Posts
  state.postsView = 'calendar';
  location.hash = '#/pipeline';
};


/* ---------------- studio: the in-app video/photo editor (Shotstack Studio), opened on one post ---------------- */
// The prebuilt bundle (studio-build/, see index.html) loads as a deferred <script>, so by the time a user
// actually opens the editor it's normally already on window - this just covers the rare case of asking for
// it before that deferred script has run.
async function loadStudioSdk() {
  for (let i = 0; i < 100 && !window.HookTrackStudioBundle; i++) await new Promise(r => setTimeout(r, 50));
  if (!window.HookTrackStudioBundle) throw new Error('The editor failed to load. Refresh the page and try again.');
  return window.HookTrackStudioBundle;
}
// PixiJS (inside Studio) picks an asset parser from the URL's extension alone, never from the real
// Content-Type header - confirmed live (an extension-less URL silently failed to load at all). media.py's
// file routes accept a harmless ".ext" suffix for exactly this; these map a MIME type to the right one.
const MIME_EXT = { 'video/mp4': 'mp4', 'video/quicktime': 'mov', 'video/webm': 'webm',
  'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp' };
// Seeds the Edit from the post's own media (same-origin authenticated routes - no signed URL needed here,
// that's only for Shotstack's own cloud render servers later, which have no cookie of ours to send).
function studioSeedEdit(post) {
  const images = post.images || [];
  if (isVideoFormat(post.content_type) && (post.has_video_file || post.video_url)) {
    const ext = MIME_EXT[post.video_file_type] || 'mp4';
    const src = post.has_video_file ? `${location.origin}/api/posts/${post.id}/video/file.${ext}` : post.video_url;
    return { timeline: { background: '#000000', tracks: [{ clips: [{ asset: { type: 'video', src }, start: 0, length: 'auto' }] }] },
      output: { format: 'mp4', resolution: 'sd' } };
  }
  if (isImageFormat(post.content_type) && images.length) {
    const clips = images.map((img, i) => ({
      asset: { type: 'image', src: `${location.origin}/api/posts/${post.id}/images/${img.id}/file.${MIME_EXT[img.content_type] || 'jpg'}` },
      start: i * 3, length: 3 }));
    return { timeline: { background: '#000000', tracks: [{ clips }] }, output: { format: 'mp4', resolution: 'sd' } };
  }
  // A track needs at least one clip - Shotstack's own Edit constructor rejects an empty one outright - so
  // a post with no media yet gets a placeholder text clip instead of a literally empty timeline. The user
  // replaces it with "+ Add media" or "+ Add AI clip".
  return { timeline: { background: '#000000', tracks: [{ clips: [{ asset: { type: 'text', text: 'Add your media above to get started' }, start: 0, length: 3 }] }] },
    output: { format: 'mp4', resolution: 'sd' } };
}
pages.studio = async app => {
  const q = new URLSearchParams((location.hash.split('?')[1] || ''));
  const postId = +q.get('post');
  if (!postId) { app.innerHTML = '<div class="empty card"><h2>No post to edit</h2><p class="muted">Open a card from Posts or Hooks and press "Edit in Studio" there.</p></div>'; return; }
  let post;
  try { post = await api('GET', `/api/posts/${postId}`); } catch (e) {
    app.innerHTML = `<div class="empty card"><h2>Could not load this post</h2><p class="muted">${esc(e.message)}</p></div>`;
    return;
  }
  const sessionDraftKey = `hooktrack:studio-draft:${post.id}`;
  const brandKitStorageKey = `hooktrack:studio-brand-kit:${state.me && state.me.workspace && state.me.workspace.id || 'browser'}`;
  const defaultBrandKit = { logoText: '', primaryColor: '#ffffff', accentColor: '#ffe15a', fontFamily: 'Roboto' };
  const studioFonts = ['Roboto', 'Arial', 'Montserrat', 'Poppins', 'Inter'];
  const readStudioBrandKit = () => {
    let saved;
    try { saved = localStorage.getItem(brandKitStorageKey); } catch (error) {
      throw new Error(`Could not read the saved brand kit: ${error.message || error}`);
    }
    if (!saved) return { ...defaultBrandKit };
    let parsed;
    try { parsed = JSON.parse(saved); } catch (error) {
      throw new Error(`The saved brand kit is invalid: ${error.message || error}`);
    }
    const color = value => /^#[0-9a-f]{6}$/i.test(value || '') ? value : null;
    return {
      logoText: typeof parsed.logoText === 'string' ? parsed.logoText.slice(0, 60) : '',
      primaryColor: color(parsed.primaryColor) || defaultBrandKit.primaryColor,
      accentColor: color(parsed.accentColor) || defaultBrandKit.accentColor,
      fontFamily: studioFonts.includes(parsed.fontFamily) ? parsed.fontFamily : defaultBrandKit.fontFamily,
    };
  };
  let sessionDraft = null;
  try {
    const saved = sessionStorage.getItem(sessionDraftKey);
    if (saved) {
      const parsed = JSON.parse(saved);
      const cachedEdit = parsed && parsed.edit ? parsed.edit : parsed;
      if (!cachedEdit || !cachedEdit.timeline || !Array.isArray(cachedEdit.timeline.tracks)) {
        throw new Error('The saved timeline data is invalid.');
      }
      const serverDraftAt = Date.parse(post.studio_draft_at || '');
      const cacheIsNewer = Number.isFinite(parsed.updatedAt) &&
        (!post.studio_draft || Number.isFinite(serverDraftAt) && parsed.updatedAt > serverDraftAt);
      if (cacheIsNewer) sessionDraft = cachedEdit;
      else sessionStorage.removeItem(sessionDraftKey);
    }
  } catch (error) {
    let message = `Could not restore pending Studio changes: ${error.message || error}`;
    try { sessionStorage.removeItem(sessionDraftKey); } catch (clearError) {
      message += ` Could not clear the bad recovery data: ${clearError.message || clearError}`;
    }
    toast(message, true);
  }
  const locked = normStatus(post.status) === 'filmed';
  const transitionPresets = [
    ['fade', 'Fade'], ['reveal', 'Reveal'], ['wipeLeft', 'Wipe left'], ['wipeRight', 'Wipe right'],
    ['slideLeft', 'Slide left'], ['slideRight', 'Slide right'], ['slideUp', 'Slide up'], ['slideDown', 'Slide down'],
    ['carouselLeft', 'Carousel left'], ['carouselRight', 'Carousel right'], ['carouselUp', 'Carousel up'],
    ['carouselDown', 'Carousel down'], ['shuffleTopRight', 'Shuffle top right'], ['shuffleRightTop', 'Shuffle right top'],
    ['shuffleRightBottom', 'Shuffle right bottom'], ['shuffleBottomRight', 'Shuffle bottom right'],
    ['shuffleBottomLeft', 'Shuffle bottom left'], ['shuffleLeftBottom', 'Shuffle left bottom'],
    ['shuffleLeftTop', 'Shuffle left top'], ['shuffleTopLeft', 'Shuffle top left'], ['zoom', 'Zoom'],
  ];
  const transitionOptions = ['<option value="none">None</option>'].concat(
    transitionPresets.flatMap(([value, label]) => [
      `<option value="${value}">${label}</option>`,
      ...(value === 'zoom' ? [] : [
        `<option value="${value}Fast">${label} (fast)</option>`,
        `<option value="${value}Slow">${label} (slow)</option>`,
      ]),
    ]),
  ).join('');
  const motionPresets = [['zoomIn', 'Zoom in'], ['zoomOut', 'Zoom out'], ['slideLeft', 'Pan left'],
    ['slideRight', 'Pan right'], ['slideUp', 'Pan up'], ['slideDown', 'Pan down']];
  const motionOptions = ['<option value="">None</option>'].concat(
    motionPresets.flatMap(([value, label]) => [
      `<option value="${value}">${label}</option>`,
      `<option value="${value}Fast">${label} (fast)</option>`,
      `<option value="${value}Slow">${label} (slow)</option>`,
    ]),
  ).join('');
  app.innerHTML = `<div class="page-head"><div><h1>Edit in Studio</h1><div class="sub">${esc(short(post.hook || post.caption || 'Untitled', 90))}</div></div>
    <div class="row"><button class="btn" id="studio-back">← Back</button></div></div>
    ${locked ? '<div class="card" style="margin-bottom:10px"><p class="muted">This card is Filmed, so its finished video/images are locked for publishing. You can still draft here, but move it back to Scripted before using "Finish".</p></div>' : ''}
    <div class="card studio-workspace">
      <div id="studio-toolbar" class="studio-toolbar" role="toolbar" aria-label="Studio editing tools">
        <div class="studio-toolbar-groups">
          <section class="studio-tool-group" aria-label="Add to timeline">
            <div class="studio-tool-label">Add to timeline</div>
            <div class="studio-tool-buttons">
              <button class="btn sm primary" id="studio-add-media" title="Add a video or image from your device">+ Media</button>
              <button class="btn sm" id="studio-add-audio" disabled title="Add audio without needing another clip selected">+ Audio</button>
              <button class="btn sm" id="studio-voiceover" disabled title="Record a voiceover or clean an audio file">Voiceover / clean</button>
              <button class="btn sm" id="studio-add-text" disabled title="Add text without needing another clip selected">+ Text</button>
              ${state.wavespeed && state.wavespeed.configured ? '<button class="btn sm" id="studio-add-ai" disabled title="Generate an image or video with WaveSpeedAI">+ AI clip</button>' : ''}
              <input type="file" id="studio-upload" accept="video/mp4,video/quicktime,video/webm,image/jpeg,image/png,image/webp" hidden>
              <input type="file" id="studio-audio-upload" accept="audio/mpeg,audio/mp3,audio/mp4,audio/wav,audio/x-wav,audio/aac,audio/ogg" hidden>
              <input type="file" id="studio-clean-audio-upload" accept="audio/*" hidden>
            </div>
          </section>
          <section class="studio-tool-group studio-tool-group-creator" aria-label="Creator tools">
            <div class="studio-tool-label">Creator tools</div>
            <div class="studio-tool-buttons">
              <select id="studio-format-preset" aria-label="Output format" title="Choose an output format">
                <option value="">Current format</option>
                <option value="9:16">TikTok / Reels / Shorts · 9:16</option><option value="1:1">Square feed · 1:1</option>
                <option value="4:5">Portrait feed · 4:5</option><option value="16:9">YouTube / landscape · 16:9</option>
              </select>
              <button class="btn sm" id="studio-safe-area" type="button" aria-pressed="false" title="Show guides for keeping important content clear of interface overlays">Safe area</button>
              <button class="btn sm" id="studio-brand-kit" type="button">Brand kit</button>
              <button class="btn sm" id="studio-cover-editor" type="button" title="Create and download a cover image from the current frame">Cover</button>
            </div>
          </section>
          <section class="studio-tool-group studio-tool-group-selection" aria-label="Selected clip tools">
            <div class="studio-tool-label">Selected clip</div>
            <div class="studio-tool-buttons">
              <button class="btn sm" id="studio-duplicate-clip" disabled title="Select a clip to duplicate it at the end of the timeline">Duplicate</button>
              <button class="btn sm danger" id="studio-delete-clip" disabled title="Select a clip to delete it">Delete</button>
              <button class="btn sm" id="studio-trim-start" disabled title="Select a video and place the playhead inside it">Trim start</button>
              <button class="btn sm" id="studio-trim-end" disabled title="Select a video and place the playhead inside it">Trim end</button>
              <button class="btn sm" id="studio-split-clip" disabled title="Select a video clip and place the playhead inside it">Split</button>
              <button class="btn sm" id="studio-continue-video" disabled title="Select a video clip to generate a continuation">Continue video</button>
            </div>
          </section>
          <section class="studio-tool-group" aria-label="Draft and history">
            <div class="studio-tool-label">Draft</div>
            <div class="studio-tool-buttons">
              <button class="btn sm" id="studio-undo" disabled title="Undo the last edit">Undo</button>
              <button class="btn sm" id="studio-redo" disabled title="Redo the last undone edit">Redo</button>
              <button class="btn sm" id="studio-save-draft" disabled>Save draft</button>
              ${post.studio_draft || sessionDraft ? '<button class="btn sm" id="studio-discard-draft" disabled>Start over</button>' : ''}
            </div>
          </section>
          <section class="studio-tool-group studio-tool-group-export" aria-label="Export">
            <div class="studio-tool-label">Export</div>
            <div class="studio-tool-buttons">
              <button class="btn sm primary" id="studio-finish" disabled ${locked ? `title="Move this card back to Scripted first"` : ''}>${isImageFormat(post.content_type) ? 'Finish images' : 'Finish video'}</button>
            </div>
          </section>
          <section class="studio-tool-group" aria-label="Clip properties">
            <div class="studio-tool-label">Adjust</div>
            <div class="studio-tool-buttons">
              <button class="btn sm" id="studio-properties-toggle" type="button" aria-expanded="false" aria-controls="studio-properties" disabled>Properties</button>
            </div>
          </section>
          <section class="studio-tool-group" aria-label="Captions">
            <div class="studio-tool-label">Captions</div>
            <div class="studio-tool-buttons">
              <select id="studio-caption-style" aria-label="Caption animation" title="Caption word animation">
                <option value="highlight">Highlight words</option><option value="karaoke">Karaoke</option>
                <option value="pop">Pop words</option><option value="typewriter">Typewriter</option>
                <option value="fade">Fade words</option><option value="slide">Slide words</option>
                <option value="bounce">Bounce words</option>
                <option value="none">No animation</option>
              </select>
              <button class="btn sm" id="studio-add-captions" type="button" disabled title="Select a video clip to transcribe">Transcribe on device</button>
            </div>
          </section>
        </div>
        <section id="studio-properties" class="studio-properties" aria-label="Selected clip properties" hidden>
          <div class="studio-properties-heading">
            <b id="studio-properties-title">Clip properties</b>
            <span class="small muted" id="studio-properties-hint">Select a clip to adjust it.</span>
          </div>
          <div class="studio-properties-grid">
            <label id="studio-start-field">Start (seconds)
              <input id="studio-prop-start" type="number" min="0" step="0.1">
            </label>
            <label id="studio-length-field">Duration (seconds)
              <input id="studio-prop-length" type="number" min="0.05" step="0.1" placeholder="Auto">
            </label>
            <label id="studio-fit-field">Fit
              <select id="studio-prop-fit">
                <option value="crop">Crop to fill</option><option value="contain">Fit inside</option>
                <option value="cover">Stretch to fill</option><option value="none">Original size</option>
              </select>
            </label>
            <label id="studio-position-field">Position
              <select id="studio-prop-position">
                <option value="center">Center</option><option value="top">Top</option><option value="topRight">Top right</option>
                <option value="right">Right</option><option value="bottomRight">Bottom right</option><option value="bottom">Bottom</option>
                <option value="bottomLeft">Bottom left</option><option value="left">Left</option><option value="topLeft">Top left</option>
              </select>
            </label>
            <label id="studio-scale-field">Scale <output id="studio-prop-scale-value">100%</output>
              <input id="studio-prop-scale" type="range" min="10" max="200" step="5" value="100">
            </label>
            <label id="studio-opacity-field">Opacity <output id="studio-prop-opacity-value">100%</output>
              <input id="studio-prop-opacity" type="range" min="0" max="100" step="5" value="100">
            </label>
            <label id="studio-volume-field">Volume <output id="studio-prop-volume-value">100%</output>
              <input id="studio-prop-volume" type="range" min="0" max="100" step="5" value="100">
            </label>
            <label id="studio-fade-field">Audio fade
              <select id="studio-prop-fade">
                <option value="none">No fade</option><option value="fadeIn">Fade in</option>
                <option value="fadeOut">Fade out</option><option value="fadeInFadeOut">Fade in and out</option>
              </select>
            </label>
            <label id="studio-filter-field">Filter
              <select id="studio-prop-filter">
                <option value="none">None</option><option value="blur">Blur</option>
                <option value="boost">Boost</option><option value="contrast">Contrast</option>
                <option value="darken">Darken</option><option value="greyscale">Greyscale</option>
                <option value="lighten">Lighten</option><option value="muted">Muted</option>
                <option value="negative">Negative</option>
              </select>
            </label>
            <label id="studio-transition-in-field">Transition in
              <select id="studio-prop-transition-in">${transitionOptions}</select>
            </label>
            <label id="studio-transition-out-field">Transition out
              <select id="studio-prop-transition-out">${transitionOptions}</select>
            </label>
            <label id="studio-motion-field">Motion
              <select id="studio-prop-motion">${motionOptions}</select>
            </label>
            <label id="studio-rotation-field">Rotation (degrees)
              <input id="studio-prop-rotation" type="number" min="-180" max="180" step="1" value="0">
            </label>
            <label id="studio-flip-horizontal-field">Flip horizontally
              <input id="studio-prop-flip-horizontal" type="checkbox">
            </label>
            <label id="studio-flip-vertical-field">Flip vertically
              <input id="studio-prop-flip-vertical" type="checkbox">
            </label>
          </div>
          <p id="studio-export-effects-note" class="studio-effects-note" hidden>
            Filters, transitions, and motion may not be supported by browser export.
          </p>
        </section>
        <div class="studio-toolbar-status">
          <span class="studio-start-hint">You can add media, audio, text, or AI clips before selecting anything.</span>
          <span class="small muted" id="studio-status" role="status" aria-live="polite">Loading editor…</span>
        </div>
        <p id="studio-captions-note" class="studio-effects-note">
          Captions are transcribed on this device. The speech model downloads the first time you use it; your selected clip's audio is not uploaded for transcription.
        </p>
      </div>
      <div data-shotstack-studio style="width:100%;background:#000"></div>
      <div data-shotstack-timeline style="width:100%;overflow:hidden;background:#0d1117"></div>
    </div>`;
  $('#studio-back').onclick = () => { location.hash = '#/pipeline'; };
  if ($('#studio-discard-draft')) $('#studio-discard-draft').onclick = guard(async () => {
    if (!await askConfirm('Discard your saved draft and start over from this post\'s current media? This cannot be undone.', { title: 'Start over' })) return;
    clearInterval(autosave);
    clearTimeout(draftSaveTimer);
    if (draftSaveInFlight) {
      try { await draftSaveInFlight; } catch (error) {
        setStatus(`Pending save failed; discarding the saved draft: ${error.message || error}`, true);
      }
    }
    await api('DELETE', `/api/posts/${post.id}/studio-draft`);
    try { sessionStorage.removeItem(sessionDraftKey); } catch (error) {
      toast(`Could not clear this tab's saved Studio changes: ${error.message || error}`, true);
      return;
    }
    post.studio_draft = null;
    draftDirty = false;
    await pages.studio(app);   // re-enter fresh with the draft gone, same hash won't fire hashchange on its own
  });
  const timelineSummary = () => {
    if (!edit) return '';
    const tracks = edit.getEdit().timeline.tracks || [];
    const clips = tracks.reduce((count, track) => count + (track.clips || []).length, 0);
    const duration = Number.isFinite(edit.totalDuration) ? edit.totalDuration : 0;
    return `${tracks.length} tracks · ${clips} clips · ${duration.toFixed(1)}s`;
  };
  const setStatus = (text, includeTimeline = false) => {
    const el = $('#studio-status', app);
    if (!el) return;
    const summary = timelineSummary();
    el.textContent = includeTimeline && summary ? `${text} · ${summary}` : text;
    el.title = summary ? `Timeline: ${summary}` : '';
  };
  async function chooseGeneratedImage(urls) {
    return new Promise(resolve => {
      let settled = false;
      const finish = value => {
        if (settled) return;
        settled = true;
        observer.disconnect();
        resolve(value);
      };
      const modal = openModal(`<h2>Choose an image variation</h2>
        <p class="small muted">Select the result to add to your timeline. Other generated results won’t be added.</p>
        <div id="ai-image-variations" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px"></div>
        <div class="actions"><span class="spacer"></span><button class="btn" type="button" data-close>Cancel</button></div>`, true);
      const observer = new MutationObserver(() => {
        if (!document.body.contains(modal)) finish(null);
      });
      observer.observe(document.body, { childList: true, subtree: true });
      const gallery = $('#ai-image-variations', modal);
      urls.forEach((url, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn';
        button.setAttribute('aria-label', `Choose image variation ${index + 1}`);
        button.style.cssText = 'padding:6px;min-width:0;overflow:hidden';
        const image = document.createElement('img');
        image.src = url;
        image.alt = `Generated image variation ${index + 1}`;
        image.loading = 'lazy';
        image.style.cssText = 'display:block;width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px';
        const label = document.createElement('span');
        label.className = 'small';
        label.textContent = `Variation ${index + 1}`;
        button.append(image, label);
        button.addEventListener('click', () => {
          finish(url);
          modal.close();
        });
        gallery.appendChild(button);
      });
    });
  }
  // The track actually holding the image/video clips, wherever it ended up - text overlays insert a new
  // track at index 0 (so they render on top), which shifts this one down, so its index can't be assumed.
  const baseTrackIndex = () => {
    const tracks = edit.getEdit().timeline.tracks;
    const idx = tracks.findIndex(t => (t.clips || []).some(c => c.asset && (c.asset.type === 'image' || c.asset.type === 'video')));
    return idx >= 0 ? idx : 0;
  };
  const hasStudioContent = () => edit.getEdit().timeline.tracks.some(track =>
    (track.clips || []).some(clip => clip.asset &&
      !(clip.asset.type === 'text' && clip.asset.text === 'Add your media above to get started')));
  const hasStudioImage = () => edit.getEdit().timeline.tracks.some(track =>
    (track.clips || []).some(clip => clip.asset && clip.asset.type === 'image'));
  const hasStudioVisual = () => edit.getEdit().timeline.tracks.some(track =>
    (track.clips || []).some(clip => clip.asset && (clip.asset.type === 'image' || clip.asset.type === 'video')));
  const hasCloudTranscribedCaptions = () => edit.getEdit().timeline.tracks.some(track =>
    (track.clips || []).some(clip => clip.asset && clip.asset.type === 'rich-caption' &&
      typeof clip.asset.src === 'string' && clip.asset.src.startsWith('alias://')));
  const localCaptionMarker = clip => `hooktrack_local_caption_${clip.id || clip.alias || Math.round(clip.start * 1000)}`;

  let edit, canvas, timeline, controls, ui, StudioSdk, draftDirty = !!sessionDraft, draftRevision = sessionDraft ? 1 : 0;
  let draftSaveTimer = null, draftSaveInFlight = null, scheduleDraftSave = () => {}, selectedClipLocation = null, selectedClipId = null;
  let localCacheFailureNotified = false;
  let syncStudioClipActions = () => {}, syncStudioOutputButtons = () => {}, syncStudioProperties = () => {};
  const cacheStudioDraft = () => {
    try {
      sessionStorage.setItem(sessionDraftKey, JSON.stringify({ edit: edit.getEdit(), updatedAt: Date.now() }));
      return true;
    } catch (error) {
      return false;
    }
  };
  try {
    StudioSdk = await loadStudioSdk();
    const { Edit, Canvas, Controls, Timeline, UIController } = StudioSdk;
    // A saved draft (in-progress crops, filters, clip order, added media) wins over re-seeding from the
    // post's current media - that re-seed is only the starting point the very first time, or after
    // "Start over" explicitly asks for it.
    const seed = sessionDraft || post.studio_draft || studioSeedEdit(post);
    edit = new Edit(seed);
    canvas = new Canvas(edit);
    ui = UIController.create(edit, canvas, { mergeFields: true });
    await canvas.load();
    await edit.load();
    const studioPreview = $('[data-shotstack-studio]', app);
    const formatPreset = $('#studio-format-preset');
    const currentAspectRatio = edit.getOutputAspectRatio();
    if (currentAspectRatio && Array.from(formatPreset.options).some(option => option.value === currentAspectRatio)) {
      formatPreset.value = currentAspectRatio;
    }
    const safeAreaOverlay = document.createElement('div');
    safeAreaOverlay.className = 'studio-safe-area-overlay';
    safeAreaOverlay.setAttribute('aria-hidden', 'true');
    safeAreaOverlay.innerHTML = '<span>Keep key text and faces inside this area</span><i></i>';
    safeAreaOverlay.hidden = true;
    studioPreview.style.position = 'relative';
    studioPreview.append(safeAreaOverlay);
    formatPreset.onchange = guard(async event => {
      if (!event.target.value) return;
      const result = await edit.setOutputAspectRatio(event.target.value);
      if (result.status !== 'success') throw new Error(result.message || 'Could not change the output format.');
      setStatus(`Output format set to ${event.target.options[event.target.selectedIndex].text}`, true);
    });
    $('#studio-safe-area').onclick = event => {
      const enabled = safeAreaOverlay.hidden;
      safeAreaOverlay.hidden = !enabled;
      event.currentTarget.setAttribute('aria-pressed', String(enabled));
    };
    $('#studio-brand-kit').onclick = guard(async () => {
      const brand = readStudioBrandKit();
      const modal = openModal(`
        <div class="modal-head"><h2>Workspace brand kit</h2><button class="icon-btn" type="button" data-close aria-label="Close">×</button></div>
        <p class="small muted">Saved in this browser for this workspace. Applied to generated captions and cover exports.</p>
        <label>Logo or brand name<input id="studio-brand-logo" maxlength="60" value="${esc(brand.logoText)}" placeholder="Your brand"></label>
        <div class="studio-brand-fields">
          <label>Primary color<input id="studio-brand-primary" type="color" value="${brand.primaryColor}"></label>
          <label>Accent color<input id="studio-brand-accent" type="color" value="${brand.accentColor}"></label>
          <label>Font
            <select id="studio-brand-font">${studioFonts.map(font => `<option value="${font}"${font === brand.fontFamily ? ' selected' : ''}>${font}</option>`).join('')}</select>
          </label>
        </div>
        <div class="modal-actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" id="studio-brand-save" type="button">Save brand kit</button></div>
      `);
      $('#studio-brand-save', modal).onclick = guard(async () => {
        const next = {
          logoText: $('#studio-brand-logo', modal).value.trim(),
          primaryColor: $('#studio-brand-primary', modal).value,
          accentColor: $('#studio-brand-accent', modal).value,
          fontFamily: $('#studio-brand-font', modal).value,
        };
        try { localStorage.setItem(brandKitStorageKey, JSON.stringify(next)); } catch (error) {
          throw new Error(`Could not save the brand kit in this browser: ${error.message || error}`);
        }
        modal.close();
        toast('Brand kit saved for this workspace in this browser.');
      });
    });
    $('#studio-cover-editor').onclick = guard(async () => {
      if (!hasStudioVisual()) throw new Error('Add video or image media before creating a cover.');
      const brand = readStudioBrandKit();
      let savedCover = post.studio_cover;
      const coverStyle = savedCover || brand;
      const modal = openModal(`
        <div class="modal-head"><h2>Create a cover image</h2><button class="icon-btn" type="button" data-close aria-label="Close">×</button></div>
        <p class="small muted">Capture a frame, style it, then save it to this post. Your saved cover remains available when you reopen the post.</p>
        <div id="studio-saved-cover"${savedCover && savedCover.available ? '' : ' hidden'}>
          <label class="small muted">Saved cover</label>
          <img id="studio-saved-cover-image" class="studio-cover-preview" src="${savedCover && savedCover.available ? `${savedCover.url}?v=${encodeURIComponent(savedCover.updated_at || '')}` : ''}" alt="Saved cover image">
          <div class="modal-actions">
            <a class="btn" id="studio-saved-cover-download" href="${savedCover && savedCover.available ? esc(savedCover.url) : ''}" download="hooktrack-cover-${post.id}.png">Download saved cover</a>
            <button class="btn danger" id="studio-cover-delete" type="button">Remove saved cover</button>
          </div>
        </div>
        <label>Cover title<input id="studio-cover-title" maxlength="100" value="${esc(savedCover ? savedCover.title : short(post.hook || post.caption || '', 100))}" placeholder="Add a short, readable title"></label>
        <label>Brand name<input id="studio-cover-logo" maxlength="60" value="${esc(coverStyle.logo_text || coverStyle.logoText || '')}" placeholder="Optional"></label>
        <div class="studio-brand-fields">
          <label>Text color<input id="studio-cover-primary" type="color" value="${coverStyle.primary_color || coverStyle.primaryColor}"></label>
          <label>Accent color<input id="studio-cover-accent" type="color" value="${coverStyle.accent_color || coverStyle.accentColor}"></label>
          <label>Font
            <select id="studio-cover-font">${studioFonts.map(font => `<option value="${font}"${font === (coverStyle.font_family || coverStyle.fontFamily) ? ' selected' : ''}>${font}</option>`).join('')}</select>
          </label>
        </div>
        <canvas id="studio-cover-preview" class="studio-cover-preview" aria-label="Cover image preview"></canvas>
        <div class="modal-actions">
          <button class="btn" type="button" data-close>Close</button>
          <button class="btn" id="studio-cover-download" type="button" disabled>Download PNG</button>
          <button class="btn primary" id="studio-cover-save" type="button" disabled>Save cover to post</button>
        </div>
      `, true);
      const preview = $('#studio-cover-preview', modal);
      const download = $('#studio-cover-download', modal);
      const saveCoverButton = $('#studio-cover-save', modal);
      edit.seek(Math.max(0, Number(edit.playbackTime) || 0));
      await new Promise(resolve => setTimeout(resolve, 50));
      const frameData = await canvas.captureFrame({ format: 'png' });
      const frame = new Image();
      frame.src = frameData;
      await frame.decode();
      const drawCover = () => {
        if (!frame.naturalWidth || !frame.naturalHeight) throw new Error('The captured frame has no image dimensions.');
        preview.width = frame.naturalWidth;
        preview.height = frame.naturalHeight;
        const ctx = preview.getContext('2d');
        if (!ctx) throw new Error('Could not prepare the cover image preview.');
        ctx.drawImage(frame, 0, 0);
        const shade = ctx.createLinearGradient(0, preview.height * 0.38, 0, preview.height);
        shade.addColorStop(0, 'rgba(0,0,0,0)');
        shade.addColorStop(1, 'rgba(0,0,0,0.78)');
        ctx.fillStyle = shade;
        ctx.fillRect(0, 0, preview.width, preview.height);
        const title = $('#studio-cover-title', modal).value.trim();
        const logo = $('#studio-cover-logo', modal).value.trim();
        const scale = preview.width / 1080;
        const fontFamily = $('#studio-cover-font', modal).value;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.shadowColor = 'rgba(0,0,0,0.8)';
        ctx.shadowBlur = 12 * scale;
        if (logo) {
          ctx.font = `700 ${Math.round(34 * scale)}px "${fontFamily}", sans-serif`;
          ctx.fillStyle = $('#studio-cover-accent', modal).value;
          ctx.fillText(logo, preview.width / 2, preview.height * 0.13, preview.width * 0.82);
        }
        if (title) {
          const fontSize = Math.round(Math.min(82 * scale, preview.width * 0.095));
          ctx.font = `800 ${fontSize}px "${fontFamily}", sans-serif`;
          ctx.fillStyle = $('#studio-cover-primary', modal).value;
          const lines = [];
          let line = '';
          for (const word of title.split(/\s+/)) {
            const candidate = line ? `${line} ${word}` : word;
            if (line && ctx.measureText(candidate).width > preview.width * 0.82) {
              lines.push(line);
              line = word;
            } else line = candidate;
          }
          if (line) lines.push(line);
          const lineHeight = fontSize * 1.12;
          const top = preview.height * 0.76 - (lines.length - 1) * lineHeight / 2;
          lines.forEach((text, index) => ctx.fillText(text, preview.width / 2, top + index * lineHeight, preview.width * 0.84));
        }
        ctx.shadowBlur = 0;
        download.disabled = false;
        saveCoverButton.disabled = false;
      };
      ['studio-cover-title', 'studio-cover-logo', 'studio-cover-primary', 'studio-cover-accent', 'studio-cover-font']
        .forEach(id => $(`#${id}`, modal).addEventListener('input', guard(drawCover)));
      download.onclick = guard(async () => {
        const blob = await new Promise(resolve => preview.toBlob(resolve, 'image/png'));
        if (!blob) throw new Error('The cover image could not be encoded as PNG.');
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `hooktrack-cover-${post.id}.png`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast('Cover image downloaded.');
      });
      saveCoverButton.onclick = guard(async () => {
        const blob = await new Promise(resolve => preview.toBlob(resolve, 'image/png'));
        if (!blob) throw new Error('The cover image could not be encoded as PNG.');
        saveCoverButton.disabled = true;
        saveCoverButton.textContent = 'Saving…';
        try {
          const form = new FormData();
          form.append('file', blob, `cover-${post.id}.png`);
          form.append('title', $('#studio-cover-title', modal).value.trim());
          form.append('logo_text', $('#studio-cover-logo', modal).value.trim());
          form.append('primary_color', $('#studio-cover-primary', modal).value);
          form.append('accent_color', $('#studio-cover-accent', modal).value);
          form.append('font_family', $('#studio-cover-font', modal).value);
          savedCover = await api('PUT', `/api/posts/${post.id}/studio-cover`, form);
          post.studio_cover = savedCover;
          if (!savedCover || !savedCover.available || !savedCover.url) {
            throw new Error('The cover was saved but could not be read back from storage.');
          }
          const savedSection = $('#studio-saved-cover', modal);
          savedSection.hidden = false;
          $('#studio-saved-cover-image', modal).src = `${savedCover.url}?v=${encodeURIComponent(savedCover.updated_at || Date.now())}`;
          $('#studio-saved-cover-download', modal).href = savedCover.url;
          setStatus('Cover saved to this post', true);
          toast('Cover saved to this post.');
        } finally {
          saveCoverButton.disabled = false;
          saveCoverButton.textContent = 'Save cover to post';
        }
      });
      const removeCoverButton = $('#studio-cover-delete', modal);
      if (removeCoverButton) removeCoverButton.onclick = guard(async () => {
        if (!await askConfirm('Remove the saved cover image from this post?', { title: 'Remove cover' })) return;
        await api('DELETE', `/api/posts/${post.id}/studio-cover`);
        savedCover = null;
        post.studio_cover = null;
        $('#studio-saved-cover', modal).hidden = true;
        toast('Saved cover removed from this post.');
      });
      drawCover();
    });
    const sdkCanDeleteClip = edit.canDeleteClip.bind(edit);
    const sdkDeleteClip = edit.deleteClip.bind(edit);
    const countEditClips = () => edit.getEdit().timeline.tracks.reduce(
      (count, track) => count + (track.clips || []).length, 0);
    // Shotstack disables deleting the last clip, so make the singleton-audio case removable
    // through its canvas toolbar, timeline toolbar, keyboard shortcut, and HookTrack toolbar.
    edit.canDeleteClip = (trackIndex, clipIndex) => {
      const clip = edit.getClip(trackIndex, clipIndex);
      return !!clip && clip.asset && clip.asset.type === 'audio' && countEditClips() === 1
        ? true
        : sdkCanDeleteClip(trackIndex, clipIndex);
    };
    edit.deleteClip = async (trackIndex, clipIndex) => {
      const clip = edit.getClip(trackIndex, clipIndex);
      if (clip && clip.asset && clip.asset.type === 'audio' && countEditClips() === 1) {
        return edit.updateClip(trackIndex, clipIndex, {
          asset: { type: 'text', text: 'Add your media above to get started' },
          start: 0,
          length: 3,
        });
      }
      return sdkDeleteClip(trackIndex, clipIndex);
    };
    const timelineContainer = $('[data-shotstack-timeline]', app);
    timeline = new Timeline(edit, timelineContainer);
    await timeline.load();
    controls = new Controls(edit);
    await controls.load();
    setStatus(sessionDraft ? 'Restored pending changes from this tab' : post.studio_draft ? 'Ready — restored your saved draft' : 'Ready', true);
    syncStudioOutputButtons = () => {
      const hasContent = hasStudioContent();
      const canFinish = isImageFormat(post.content_type) ? hasStudioImage() : hasContent;
      const finish = $('#studio-finish', app);
      const needsCloudRender = hasCloudTranscribedCaptions();
      finish.disabled = locked || !canFinish || needsCloudRender;
      finish.title = locked
        ? 'Move this card back to Scripted first'
        : needsCloudRender ? 'Remove legacy Shotstack captions before finishing in the browser'
          : canFinish ? '' : isImageFormat(post.content_type)
          ? 'Add at least one image to the timeline before finishing'
          : 'Add content to the timeline before finishing';
    };
    syncStudioOutputButtons();
    $('#studio-save-draft', app).disabled = false;
    $('#studio-add-audio', app).disabled = false;
    $('#studio-voiceover', app).disabled = false;
    $('#studio-add-text', app).disabled = false;
    if ($('#studio-discard-draft', app)) $('#studio-discard-draft', app).disabled = false;
    if ($('#studio-add-ai', app)) $('#studio-add-ai', app).disabled = false;
    const undoButton = $('#studio-undo', app), redoButton = $('#studio-redo', app);
    const syncHistoryButtons = () => {
      undoButton.disabled = !edit.canUndo;
      redoButton.disabled = !edit.canRedo;
    };
    const markEditChanged = () => {
      draftDirty = true;
      draftRevision++;
      const cached = cacheStudioDraft();
      setStatus(cached ? 'Edited' : 'Edited — local recovery unavailable', true);
      if (!cached && !localCacheFailureNotified) {
        toast('Browser storage is unavailable; save the Studio draft before reloading.', true);
        localCacheFailureNotified = true;
      }
      syncHistoryButtons();
      syncStudioOutputButtons();
      syncStudioProperties();
      syncStudioClipActions();
      scheduleDraftSave();
    };
    edit.events.on('edit:changed', markEditChanged);
    edit.events.on('edit:undo', markEditChanged);
    edit.events.on('edit:redo', markEditChanged);
    undoButton.onclick = guard(async () => { await edit.undo(); syncHistoryButtons(); });
    redoButton.onclick = guard(async () => { await edit.redo(); syncHistoryButtons(); });
    syncHistoryButtons();
    const splitButton = $('#studio-split-clip', app);
    const continueVideoButton = $('#studio-continue-video', app);
    const duplicateButton = $('#studio-duplicate-clip', app), deleteButton = $('#studio-delete-clip', app);
    const trimStartButton = $('#studio-trim-start', app), trimEndButton = $('#studio-trim-end', app);
    const propertiesToggle = $('#studio-properties-toggle', app), propertiesPanel = $('#studio-properties', app);
    const captionsButton = $('#studio-add-captions', app);
    const getSelectedLocation = () => {
      if (selectedClipId) {
        const location = edit.getClipPositionById(selectedClipId);
        return location;
      }
      return selectedClipLocation;
    };
    const getSelectedClip = () => {
      const location = getSelectedLocation();
      return location && edit.getClip(location.trackIndex, location.clipIndex);
    };
    const getSelectedVideo = () => {
      const clip = getSelectedClip();
      return clip && clip.asset && clip.asset.type === 'video' ? clip : null;
    };
    syncStudioClipActions = () => {
      const clip = getSelectedClip();
      const clipCount = edit.getEdit().timeline.tracks.reduce(
        (count, track) => count + (track.clips || []).length, 0);
      duplicateButton.disabled = !clip;
      deleteButton.disabled = !clip || (clipCount <= 1 && clip.asset.type !== 'audio');
      duplicateButton.title = clip ? 'Duplicate this clip at the end of the timeline' : 'Select a clip to duplicate it';
      deleteButton.title = clipCount <= 1 && clip && clip.asset.type !== 'audio'
        ? 'The timeline must keep at least one clip'
        : clip ? 'Delete this clip' : 'Select a clip to delete it';
      const selected = !!getSelectedVideo();
      splitButton.disabled = !selected;
      trimStartButton.disabled = !selected;
      trimEndButton.disabled = !selected;
      splitButton.title = selected ? 'Split the selected video at the playhead' : 'Select a video clip to split it';
      trimStartButton.title = selected ? 'Remove the selected video from its start to the playhead' : 'Select a video clip to trim';
      trimEndButton.title = selected ? 'Remove the selected video from the playhead to its end' : 'Select a video clip to trim';
      continueVideoButton.disabled = locked || !selected;
      continueVideoButton.title = locked ? 'Move this card back to Scripted before editing'
        : selected ? 'Generate a new AI segment from this video’s last frame'
          : 'Select a video clip to generate a continuation';
      const selectedVideo = getSelectedVideo();
      const alreadyCaptioned = selectedVideo &&
        edit.getEdit().timeline.tracks.some(track => (track.clips || []).some(clip =>
          clip.asset && clip.asset.type === 'rich-caption' &&
          (clip.asset.src === `alias://${selectedVideo.alias}` || clip.alias === localCaptionMarker(selectedVideo))));
      captionsButton.disabled = !selectedVideo || isImageFormat(post.content_type) || !!alreadyCaptioned;
      captionsButton.title = !selectedVideo ? 'Select a video clip to generate captions'
        : alreadyCaptioned ? 'This video already has captions; remove them before generating another set'
          : 'Transcribe this clip locally and add editable, timed captions';
      $('#studio-captions-note', app).textContent =
        'Transcription runs on this device. The speech model downloads on first use; your clip audio is not uploaded for transcription.';
      syncStudioProperties();
    };
    const propertyField = id => $(`#studio-${id}-field`, app);
    const propertySelect = (id, value) => {
      const input = $(`#studio-prop-${id}`, app);
      if (input) input.value = value;
    };
    const propertyRange = (id, value) => {
      const input = $(`#studio-prop-${id}`, app), output = $(`#studio-prop-${id}-value`, app);
      if (!input || !output) return;
      const percent = Math.round(Math.max(0, Math.min(2, Number(value))) * 100);
      input.value = String(percent);
      output.value = `${percent}%`;
      output.textContent = `${percent}%`;
    };
    syncStudioProperties = () => {
      const clip = getSelectedClip();
      const asset = clip && clip.asset;
      const type = asset && asset.type;
      const visual = ['image', 'video', 'text', 'rich-text', 'title', 'shape', 'svg'].includes(type);
      const hasVideoAudio = type === 'video', hasAudio = type === 'audio' || hasVideoAudio;
      propertiesToggle.disabled = !clip;
      propertiesToggle.setAttribute('aria-expanded', String(!propertiesPanel.hidden));
      propertiesToggle.title = clip ? `Adjust selected ${type} clip` : 'Select a clip to edit its properties';
      if (!clip || !asset) {
        propertiesPanel.hidden = true;
        propertiesToggle.setAttribute('aria-expanded', 'false');
        $('#studio-properties-title', app).textContent = 'Clip properties';
        $('#studio-properties-hint', app).textContent = 'Select a clip to adjust it.';
        return;
      }
      $('#studio-properties-title', app).textContent = `${type.charAt(0).toUpperCase()}${type.slice(1)} properties`;
      $('#studio-properties-hint', app).textContent = 'Changes are saved with your Studio draft.';
      const startInput = $('#studio-prop-start', app), lengthInput = $('#studio-prop-length', app);
      startInput.value = Number.isFinite(Number(clip.start)) ? String(clip.start) : '';
      startInput.disabled = !Number.isFinite(Number(clip.start));
      lengthInput.value = Number.isFinite(Number(clip.length)) ? String(clip.length) : '';
      lengthInput.placeholder = clip.length === 'auto' ? 'Auto' : '';
      propertyField('fit').hidden = !(type === 'image' || type === 'video');
      propertyField('position').hidden = !visual;
      propertyField('scale').hidden = !visual;
      propertyField('opacity').hidden = !visual;
      propertyField('volume').hidden = !hasAudio;
      propertyField('fade').hidden = !hasAudio;
      propertyField('filter').hidden = !visual;
      propertyField('transition-in').hidden = !visual;
      propertyField('transition-out').hidden = !visual;
      propertyField('motion').hidden = !visual;
      propertyField('rotation').hidden = !visual;
      propertyField('flip-horizontal').hidden = !visual;
      propertyField('flip-vertical').hidden = !visual;
      $('#studio-export-effects-note', app).hidden = !visual;
      propertySelect('fit', clip.fit || 'crop');
      propertySelect('position', clip.position || 'center');
      propertySelect('filter', clip.filter || 'none');
      propertySelect('transition-in', clip.transition && clip.transition.in || 'none');
      propertySelect('transition-out', clip.transition && clip.transition.out || 'none');
      propertySelect('motion', clip.effect || '');
      propertyRange('scale', Array.isArray(clip.scale) ? 1 : (clip.scale == null ? 1 : clip.scale));
      propertyRange('opacity', Array.isArray(clip.opacity) ? 1 : (clip.opacity == null ? 1 : clip.opacity));
      propertyRange('volume', Array.isArray(asset.volume) ? 1 : (asset.volume == null ? 1 : asset.volume));
      propertySelect('fade', hasVideoAudio ? (asset.volumeEffect || 'none') : (asset.effect || 'none'));
      const rotation = clip.transform && clip.transform.rotate && clip.transform.rotate.angle;
      $('#studio-prop-rotation', app).value = Number.isFinite(Number(rotation)) ? String(rotation || 0) : '0';
      $('#studio-prop-flip-horizontal', app).checked = !!(clip.transform && clip.transform.flip && clip.transform.flip.horizontal);
      $('#studio-prop-flip-vertical', app).checked = !!(clip.transform && clip.transform.flip && clip.transform.flip.vertical);
    };
    propertiesToggle.onclick = () => {
      if (propertiesToggle.disabled) return;
      propertiesPanel.hidden = !propertiesPanel.hidden;
      propertiesToggle.setAttribute('aria-expanded', String(!propertiesPanel.hidden));
    };
    const updateSelectedClip = async updates => {
      const location = getSelectedLocation(), clip = getSelectedClip();
      if (!location || !clip) throw new Error('Select a clip before changing its properties.');
      const result = clip.id
        ? await edit.updateClipById(clip.id, updates)
        : await edit.updateClip(location.trackIndex, location.clipIndex, updates);
      if (result.status !== 'success') throw new Error(result.message || 'The selected clip could not be updated.');
      syncStudioProperties();
    };
    const propertyUpdate = (id, buildUpdates) => {
      const input = $(`#studio-prop-${id}`, app);
      input.onchange = guard(async () => {
        const clip = getSelectedClip();
        if (!clip) return;
        if (!input.checkValidity()) {
          input.reportValidity();
          syncStudioProperties();
          return;
        }
        const updates = buildUpdates(input, clip);
        if (!updates) {
          syncStudioProperties();
          return;
        }
        await updateSelectedClip(updates);
        setStatus('Clip properties updated', true);
      });
    };
    propertyUpdate('fit', input => ({ fit: input.value }));
    propertyUpdate('position', input => ({ position: input.value }));
    propertyUpdate('start', input => ({ start: Number(input.value) }));
    propertyUpdate('length', (input, clip) => input.value === '' && clip.length !== 'auto'
      ? null
      : { length: input.value === '' ? 'auto' : Number(input.value) });
    propertyUpdate('scale', input => ({ scale: Number(input.value) / 100 }));
    propertyUpdate('opacity', input => ({ opacity: Number(input.value) / 100 }));
    propertyUpdate('volume', (input, clip) => ({
      asset: { ...clip.asset, volume: Number(input.value) / 100 },
    }));
    propertyUpdate('fade', (input, clip) => ({
      asset: clip.asset.type === 'video'
        ? { ...clip.asset, volumeEffect: input.value }
        : { ...clip.asset, effect: input.value },
    }));
    propertyUpdate('filter', input => ({ filter: input.value }));
    propertyUpdate('transition-in', (input, clip) => ({
      transition: { ...(clip.transition || {}), in: input.value },
    }));
    propertyUpdate('transition-out', (input, clip) => ({
      transition: { ...(clip.transition || {}), out: input.value },
    }));
    propertyUpdate('motion', input => ({ effect: input.value || undefined }));
    propertyUpdate('rotation', (input, clip) => ({
      transform: {
        ...(clip.transform || {}),
        rotate: { ...((clip.transform && clip.transform.rotate) || {}), angle: Number(input.value) },
      },
    }));
    propertyUpdate('flip-horizontal', (input, clip) => ({
      transform: {
        ...(clip.transform || {}),
        flip: { ...((clip.transform && clip.transform.flip) || {}), horizontal: input.checked },
      },
    }));
    propertyUpdate('flip-vertical', (input, clip) => ({
      transform: {
        ...(clip.transform || {}),
        flip: { ...((clip.transform && clip.transform.flip) || {}), vertical: input.checked },
      },
    }));
    edit.events.on('clip:selected', selection => {
      selectedClipLocation = { trackIndex: selection.trackIndex, clipIndex: selection.clipIndex };
      selectedClipId = selection.clip && selection.clip.id || null;
      syncStudioClipActions();
    });
    edit.events.on('selection:cleared', () => {
      selectedClipLocation = null;
      selectedClipId = null;
      syncStudioClipActions();
      syncStudioProperties();
    });
    captionsButton.onclick = guard(async () => {
      const clip = getSelectedVideo();
      if (!clip) throw new Error('Select a video clip before transcribing captions.');
      if (isImageFormat(post.content_type)) throw new Error('Captions are only available for video posts.');
      if (typeof clip.start !== 'number' || clip.start < 0) {
        throw new Error('This video has an unsupported start time; set its start time to a number first.');
      }
      if (edit.getEdit().timeline.tracks.some(track => (track.clips || []).some(c =>
        c.asset && c.asset.type === 'rich-caption' &&
        (c.asset.src === `alias://${clip.alias}` || c.alias === localCaptionMarker(clip))))) {
        throw new Error('This video already has captions. Remove that caption clip before generating another set.');
      }
      const brandKit = readStudioBrandKit();
      const presets = {
        classic: { color: brandKit.primaryColor, active: brandKit.accentColor, background: false, stroke: 2, weight: 700 },
        bold: { color: brandKit.primaryColor, active: brandKit.accentColor, background: false, stroke: 3, weight: 900 },
        neon: { color: brandKit.primaryColor, active: brandKit.accentColor, background: false, stroke: 2, weight: 800 },
        boxed: { color: brandKit.primaryColor, active: brandKit.accentColor, background: true, stroke: 0, weight: 700 },
        minimal: { color: brandKit.primaryColor, active: brandKit.primaryColor, background: false, stroke: 0, weight: 500 },
      };
      const modal = openModal(`
        <div class="modal-head"><h2>Transcribe on this device</h2><button class="icon-btn" type="button" data-close aria-label="Close">×</button></div>
        <p class="studio-caption-privacy">Your selected clip's audio is processed in this browser and is not uploaded for transcription. Word timing is estimated within Whisper's timestamped segments.</p>
        <div class="studio-caption-progress" role="status" aria-live="polite">
          <span id="studio-caption-progress-label">Ready to transcribe.</span>
          <progress id="studio-caption-progress" max="100" value="0"></progress>
        </div>
        <label class="studio-caption-transcript">Caption text
          <textarea id="studio-caption-text" rows="6" placeholder="Transcribed captions will appear here, grouped into short lines." disabled></textarea>
        </label>
        <div class="studio-caption-options">
          <label>Style preset
            <select id="studio-caption-preset">
              <option value="classic">Classic highlight</option><option value="bold">Bold creator</option>
              <option value="neon">Neon accent</option><option value="boxed">Caption pill</option>
              <option value="minimal">Minimal</option>
            </select>
          </label>
          <label>Position
            <select id="studio-caption-position">
              <option value="bottom">Bottom center</option><option value="top">Top center</option>
              <option value="center">Center</option><option value="left">Center left</option><option value="right">Center right</option>
              <option value="bottomLeft">Bottom left</option>
              <option value="bottomRight">Bottom right</option><option value="topLeft">Top left</option>
              <option value="topRight">Top right</option>
            </select>
          </label>
          <label>Text color <input id="studio-caption-color" type="color" value="${brandKit.primaryColor}"></label>
          <label>Active word <input id="studio-caption-active-color" type="color" value="${brandKit.accentColor}"></label>
          <label>Font size <output id="studio-caption-size-value">44 px</output>
            <input id="studio-caption-size" type="range" min="24" max="80" step="2" value="44">
          </label>
          <label>Text case
            <select id="studio-caption-case">
              <option value="none">Original</option><option value="uppercase">Uppercase</option>
              <option value="capitalize">Capitalize</option><option value="lowercase">Lowercase</option>
            </select>
          </label>
          <label class="studio-caption-check"><input id="studio-caption-background" type="checkbox"> Background pill</label>
          <label>Slide direction
            <select id="studio-caption-direction">
              <option value="up">Up</option><option value="down">Down</option>
              <option value="left">From left</option><option value="right">From right</option>
            </select>
          </label>
        </div>
        <div class="modal-actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" id="studio-caption-add" type="button" disabled>Add captions</button></div>
      `, true);
      const progressLabel = $('#studio-caption-progress-label', modal);
      const progress = $('#studio-caption-progress', modal);
      const transcript = $('#studio-caption-text', modal);
      const addCaptionButton = $('#studio-caption-add', modal);
      const presetInput = $('#studio-caption-preset', modal);
      const backgroundInput = $('#studio-caption-background', modal);
      const textColorInput = $('#studio-caption-color', modal);
      const activeColorInput = $('#studio-caption-active-color', modal);
      let worker = null, words = null, transcriptGroups = null, cancelled = false;
      const abortController = new AbortController();
      const originalClose = modal.close;
      modal.close = () => {
        cancelled = true;
        abortController.abort();
        if (worker) worker.terminate();
        originalClose();
        syncStudioClipActions();
      };
      const groupWords = inputWords => {
        const groups = [];
        let group = [];
        for (const word of inputWords) {
          group.push(word);
          if (group.length >= 6 || /[.!?]$/.test(word.text)) {
            groups.push(group);
            group = [];
          }
        }
        if (group.length) groups.push(group);
        return groups;
      };
      const textOfWords = inputWords => inputWords.map(word => word.text).join(' ')
        .replace(/\s+([,.;:!?])/g, '$1')
        .replace(/([(])\s+/g, '$1');
      const timecode = milliseconds => {
        const ms = Math.max(0, Math.round(milliseconds));
        const hours = Math.floor(ms / 3600000);
        const minutes = Math.floor(ms / 60000) % 60;
        const seconds = Math.floor(ms / 1000) % 60;
        return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(ms % 1000).padStart(3, '0')}`;
      };
      const makeEditedWords = () => {
        const lines = transcript.value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
        if (!lines.length) throw new Error('Enter at least one caption line.');
        const originalText = transcriptGroups.map(group => textOfWords(group)).join('\n');
        if (lines.join('\n') === originalText) return transcriptGroups.flat();
        const totalStart = transcriptGroups[0][0].start;
        const totalEnd = transcriptGroups[transcriptGroups.length - 1][transcriptGroups[transcriptGroups.length - 1].length - 1].end;
        return lines.flatMap((line, lineIndex) => {
          const tokens = line.split(/\s+/).filter(Boolean);
          const sourceIndex = Math.min(transcriptGroups.length - 1,
            Math.floor(lineIndex * transcriptGroups.length / lines.length));
          const source = transcriptGroups[sourceIndex];
          const start = lines.length === transcriptGroups.length ? source[0].start
            : totalStart + (totalEnd - totalStart) * lineIndex / lines.length;
          const end = lines.length === transcriptGroups.length ? source[source.length - 1].end
            : totalStart + (totalEnd - totalStart) * (lineIndex + 1) / lines.length;
          return tokens.map((text, index) => {
            const original = lines.length === transcriptGroups.length && tokens.length === source.length
              ? source[index] : null;
            if (original) return { ...original, text };
            const wordStart = Math.round(start + (end - start) * index / tokens.length);
            return {
              text,
              start: wordStart,
              end: Math.max(Math.round(start + (end - start) * (index + 1) / tokens.length), wordStart + 1),
            };
          });
        });
      };
      const applyPreset = () => {
        const preset = presets[presetInput.value] || presets.classic;
        textColorInput.value = preset.color;
        activeColorInput.value = preset.active;
        backgroundInput.checked = preset.background;
      };
      presetInput.onchange = applyPreset;
      $('#studio-caption-size', modal).oninput = event => {
        $('#studio-caption-size-value', modal).textContent = `${event.target.value} px`;
      };
      const failTranscription = message => {
        progressLabel.textContent = message;
        progress.removeAttribute('value');
        setStatus('Transcription failed', true);
      };
      modal.querySelector('[data-close]').focus();
      const startTranscription = async () => {
        captionsButton.disabled = true;
        addCaptionButton.disabled = true;
        transcript.disabled = true;
        try {
          const source = clip.asset && clip.asset.src;
          if (typeof source !== 'string' || !source) throw new Error('The selected video has no readable media source.');
          progressLabel.textContent = 'Reading the selected clip…';
          const mediaResponse = await fetch(new URL(source, location.href), {
            credentials: 'same-origin',
            signal: abortController.signal,
          });
          if (!mediaResponse.ok) throw new Error(`Could not read the selected video (${mediaResponse.status}).`);
          const blob = await mediaResponse.blob();
          if (cancelled) return;
          worker = new Worker('/studio-transcriber-worker.js?v=studio-localcaptions4', { type: 'module' });
          worker.onmessage = event => {
            const message = event.data || {};
            if (message.type === 'progress') {
              progressLabel.textContent = message.detail;
              if (Number.isFinite(message.progress)) {
                progress.value = message.progress;
              } else {
                progress.removeAttribute('value');
              }
              setStatus(message.detail);
              return;
            }
            if (message.type === 'error') {
              failTranscription(message.message || 'On-device transcription failed.');
              worker.terminate();
              worker = null;
              captionsButton.disabled = false;
              return;
            }
            if (message.type !== 'done') return;
            words = message.words;
            transcriptGroups = groupWords(words);
            transcript.value = transcriptGroups.map(group => textOfWords(group)).join('\n');
            transcript.disabled = false;
            addCaptionButton.disabled = false;
            progressLabel.textContent = `Transcribed ${message.wordCount} words on this device. Edit the lines and styles, then add them to the timeline.`;
            progress.value = 100;
            setStatus('Local transcription complete', true);
            worker.terminate();
            worker = null;
            captionsButton.disabled = false;
          };
          worker.onerror = event => {
            failTranscription(event.message || 'The local transcription worker failed to load.');
            worker.terminate();
            worker = null;
            captionsButton.disabled = false;
          };
          worker.postMessage({
            id: Date.now(),
            blob,
            clip: {
              trim: clip.asset.trim || 0,
              speed: clip.asset.speed == null ? 1 : clip.asset.speed,
              length: clip.length == null ? 'auto' : clip.length,
            },
          });
          progressLabel.textContent = 'Preparing the local transcription model…';
          setStatus('Preparing on-device transcription…');
        } catch (error) {
          if (error.name === 'AbortError' || cancelled) return;
          failTranscription(error.message || 'Could not transcribe this clip on this device.');
          captionsButton.disabled = false;
        }
      };
      addCaptionButton.onclick = guard(async () => {
        if (!words || !transcriptGroups) throw new Error('Transcribe the clip before adding captions.');
        const captionWords = makeEditedWords();
        const vtt = `WEBVTT\n\n${captionWords.map((word, index) =>
          `${index + 1}\n${timecode(word.start)} --> ${timecode(word.end)}\n${word.text.replace(/-->/g, '—>')}\n`,
        ).join('\n')}`;
        const captionPosition = $('#studio-caption-position', modal).value;
        const horizontal = captionPosition === 'left' || captionPosition.endsWith('Left') ? 'left'
          : captionPosition === 'right' || captionPosition.endsWith('Right') ? 'right' : 'center';
        const vertical = captionPosition.startsWith('top') ? 'top'
          : captionPosition.startsWith('bottom') ? 'bottom' : 'middle';
        const preset = presets[presetInput.value] || presets.classic;
        const caption = {
          asset: {
            type: 'rich-caption',
            src: `data:text/vtt;charset=utf-8,${encodeURIComponent(vtt)}`,
            font: {
              family: brandKit.fontFamily,
              size: Number($('#studio-caption-size', modal).value),
              weight: preset.weight,
              color: textColorInput.value,
            },
            style: { textTransform: $('#studio-caption-case', modal).value },
            stroke: { width: preset.stroke, color: '#000000' },
            align: { horizontal, vertical },
            active: { font: { color: activeColorInput.value } },
            animation: {
              style: $('#studio-caption-style', app).value,
              direction: $('#studio-caption-direction', modal).value,
            },
            ...(backgroundInput.checked
              ? { background: { color: '#000000', opacity: 0.72, borderRadius: 12, wrap: true, padding: 14 } }
              : {}),
          },
          start: clip.start,
          length: clip.length === 'auto' || clip.length == null ? Math.max(0.1, words[words.length - 1].end / 1000) : clip.length,
          // Rich-caption sprites fill the output canvas; align controls text placement within it.
          position: 'center',
          alias: localCaptionMarker(clip),
        };
        const result = await edit.addTrack(0, { clips: [caption] });
        if (result.status !== 'success') throw new Error(result.message || 'Could not add the local captions to the timeline.');
        modal.close();
        syncStudioClipActions();
        syncStudioOutputButtons();
        setStatus('Local captions added — ready for browser export', true);
        toast('Captions added. Your transcript is stored in this Studio draft and rendered in your browser.');
      });
      startTranscription();
    });
    duplicateButton.onclick = guard(async () => {
      const clip = getSelectedClip();
      const location = getSelectedLocation();
      if (!clip || !location) return;
      const { trackIndex } = location;
      const duplicate = structuredClone(clip);
      delete duplicate.id;
      duplicate.start = Math.max(0, edit.totalDuration) + 0.5;
      const result = await edit.addClip(trackIndex, duplicate);
      if (result.status !== 'success') throw new Error(result.message || 'The selected clip could not be duplicated.');
      setStatus('Clip duplicated at the end of the timeline', true);
      toast('Clip duplicated at the end of the timeline.');
    });
    deleteButton.onclick = guard(async () => {
      const clip = getSelectedClip();
      const location = getSelectedLocation();
      if (!clip || !location) return;
      const result = clip.id
        ? await edit.deleteClipById(clip.id)
        : await edit.deleteClip(location.trackIndex, location.clipIndex);
      if (result.status !== 'success') throw new Error(result.message || 'The selected clip could not be deleted.');
      selectedClipLocation = null;
      selectedClipId = null;
      syncStudioClipActions();
      clearTimeout(draftSaveTimer);
      await saveDraft();
      setStatus('Clip deleted', true);
      toast('Clip deleted. Use Undo to restore it.');
    });
    const getVideoTiming = async clip => {
      const start = typeof clip.start === 'number' ? clip.start : NaN;
      let length = typeof clip.length === 'number' ? clip.length : NaN;
      if (!Number.isFinite(length) && clip.length === 'auto' && clip.asset.src) {
        setStatus('Checking video duration…');
        length = await new Promise(resolve => {
          const video = document.createElement('video');
          const timeout = setTimeout(() => finish(NaN), 10000);
          const finish = value => {
            clearTimeout(timeout);
            video.onloadedmetadata = null;
            video.onerror = null;
            video.removeAttribute('src');
            video.load();
            resolve(value);
          };
          video.preload = 'metadata';
          video.onloadedmetadata = () => {
            const duration = Number.isFinite(video.duration) ? video.duration : NaN;
            const trim = Number(clip.asset.trim) || 0;
            const speed = typeof clip.asset.speed === 'number' && clip.asset.speed > 0 ? clip.asset.speed : 1;
            finish(Number.isFinite(duration) ? Math.max(0, duration - trim) / speed : NaN);
          };
          video.onerror = () => finish(NaN);
          video.src = clip.asset.src;
          video.load();
        });
      }
      return { start, length };
    };
    const getTrimSelection = async () => {
      const location = getSelectedLocation();
      if (!location) return null;
      const { trackIndex, clipIndex } = location;
      const clip = getSelectedVideo();
      if (!clip) {
        selectedClipLocation = null;
        selectedClipId = null;
        syncStudioClipActions();
        return null;
      }
      const { start, length } = await getVideoTiming(clip);
      const playhead = edit.playbackTime;
      const elapsed = playhead - start;
      if (!Number.isFinite(start) || !Number.isFinite(length) || elapsed <= 0.05 || elapsed >= length - 0.05) {
        setStatus('Ready', true);
        toast('Place the playhead inside the selected video clip before trimming.', true);
        return null;
      }
      return { trackIndex, clipIndex, clip, start, length, playhead, elapsed };
    };
    continueVideoButton.onclick = guard(async () => {
      if (locked) throw new Error('Move this card back to Scripted before editing.');
      const location = getSelectedLocation();
      const sourceClip = getSelectedVideo();
      if (!location || !sourceClip) throw new Error('Select a video clip to continue.');
      const { start, length } = await getVideoTiming(sourceClip);
      if (!Number.isFinite(start) || !Number.isFinite(length) || length <= 0) {
        throw new Error('Could not determine the selected video’s end time.');
      }

      const previousTime = Number(edit.playbackTime) || 0;
      let startingFrame;
      try {
        setStatus('Capturing the selected video’s last frame…');
        edit.seek(Math.max(start, start + length - 0.05));
        await new Promise(resolve => setTimeout(resolve, 50));
        startingFrame = await canvas.captureFrame({ format: 'png' });
        if (typeof startingFrame !== 'string' || !startingFrame.startsWith('data:image/png')) {
          throw new Error('Studio could not capture a PNG from the selected video.');
        }
      } finally {
        edit.seek(previousTime);
      }

      const choice = await new Promise(resolve => {
        let settled = false;
        const finish = value => {
          if (settled) return;
          settled = true;
          observer.disconnect();
          resolve(value);
        };
        const modal = openModal(`<h2>Continue video with AI</h2>
          <p class="small muted">WaveSpeedAI will use the selected clip’s last frame to generate a new segment after it. Continuity may vary. Generation uses your WaveSpeedAI credits.</p>
          <label class="small muted">Describe what happens next</label>
          <textarea id="studio-continue-prompt" rows="3" maxlength="4000" style="width:100%"
            placeholder="The camera slowly pulls back as the subject walks toward the door"></textarea>
          <label class="small muted">Aspect ratio</label>
          <select id="studio-continue-size" style="width:100%;margin-bottom:10px">
            <option value="832*480">Landscape · 16:9</option><option value="480*832">Portrait · 9:16</option>
          </select>
          <label class="small muted">Duration</label>
          <select id="studio-continue-duration" style="width:100%;margin-bottom:10px">
            <option value="5">5 seconds</option><option value="10">10 seconds</option>
          </select>
          <label class="small muted">Negative prompt <span class="muted">(optional)</span></label>
          <textarea id="studio-continue-negative" rows="2" maxlength="1000" style="width:100%"
            placeholder="Things to avoid, e.g. blur, jitter, distorted hands"></textarea>
          <div class="actions"><span class="spacer"></span><button class="btn" type="button" data-close>Cancel</button>
            <button class="btn primary" id="studio-continue-submit" type="button">Generate continuation</button></div>`, true);
        const observer = new MutationObserver(() => {
          if (!document.body.contains(modal)) finish(null);
        });
        observer.observe(document.body, { childList: true, subtree: true });
        $('#studio-continue-submit', modal).onclick = () => {
          const prompt = $('#studio-continue-prompt', modal).value.trim();
          if (!prompt) {
            toast('Describe what happens next.', true);
            return;
          }
          finish({
            prompt,
            size: $('#studio-continue-size', modal).value,
            duration: Number($('#studio-continue-duration', modal).value),
            negativePrompt: $('#studio-continue-negative', modal).value.trim(),
          });
          modal.close();
        };
        $('#studio-continue-prompt', modal).focus();
      });
      if (!choice) return;

      const result = await edit.addClip(location.trackIndex, {
        asset: {
          type: 'video',
          prompt: choice.prompt,
          model: 'wavespeed-ai/wan-2.1/i2v-480p',
          options: {
            inputSrc: startingFrame,
            duration: choice.duration,
            size: choice.size,
            negativePrompt: choice.negativePrompt,
          },
        },
        start: start + length,
        length: choice.duration,
      });
      if (result.status !== 'success') {
        throw new Error(result.message || 'Could not add the continuation clip.');
      }
      setStatus('Generating continuation after selected video…', true);
    });
    const runTrim = guard(async mode => {
      const selected = await getTrimSelection();
      if (!selected) return;
      const { trackIndex, clipIndex, clip, length, playhead, elapsed } = selected;
      const speed = typeof clip.asset.speed === 'number' && clip.asset.speed > 0 ? clip.asset.speed : 1;
      const updates = mode === 'start'
        ? { start: playhead, length: length - elapsed, asset: { ...clip.asset, trim: (Number(clip.asset.trim) || 0) + elapsed * speed } }
        : { length: elapsed };
      const button = mode === 'start' ? trimStartButton : trimEndButton;
      button.disabled = true;
      setStatus(mode === 'start' ? 'Trimming clip start…' : 'Trimming clip end…');
      try {
        const result = await edit.updateClip(trackIndex, clipIndex, updates);
        if (result.status !== 'success') throw new Error(result.message || 'The selected video could not be trimmed.');
        setStatus('Clip trimmed', true);
        toast(mode === 'start' ? 'Video trimmed from the start to the playhead.' : 'Video trimmed from the playhead to the end.');
      } finally {
        syncStudioClipActions();
      }
    });
    trimStartButton.onclick = () => runTrim('start');
    trimEndButton.onclick = () => runTrim('end');
    splitButton.onclick = guard(async () => {
      const selected = await getTrimSelection();
      if (!selected) return;
      const { trackIndex, clipIndex, clip, length, playhead, elapsed } = selected;
      const speed = typeof clip.asset.speed === 'number' && clip.asset.speed > 0 ? clip.asset.speed : 1;
      const nextClip = structuredClone(clip);
      delete nextClip.id;
      nextClip.start = playhead;
      nextClip.length = length - elapsed;
      nextClip.asset.trim = (Number(clip.asset.trim) || 0) + elapsed * speed;
      splitButton.disabled = true;
      setStatus('Splitting clip…');
      try {
        const updated = await edit.updateClip(trackIndex, clipIndex, { length: elapsed });
        if (updated.status !== 'success') throw new Error(updated.message || 'Could not shorten the first part of the clip.');
        try {
          const added = await edit.addClip(trackIndex, nextClip);
          if (added.status !== 'success') throw new Error(added.message || 'Could not add the second part of the clip.');
        } catch (error) {
          await edit.updateClip(trackIndex, clipIndex, { length: clip.length });
          throw error;
        }
        setStatus('Clip split', true);
        toast('Video split at the playhead. Select and delete either part to trim it.');
      } catch (error) {
        setStatus('Ready', true);
        throw error;
      } finally {
        syncStudioClipActions();
      }
    });
    // Studio's own Generate toolbar delegates the prompt to this provider callback. Image/video
    // generation is billed to the WaveSpeedAI account configured on the server.
    edit.registerAssetGenerator(async request => {
      let row;
      const options = request.asset.options && typeof request.asset.options === 'object'
        ? request.asset.options : {};
      if (request.asset.model === 'wavespeed-ai/wan-2.1/i2v-480p') {
        if (typeof options.inputSrc !== 'string' || !options.inputSrc) {
          throw new Error('Choose a source image from the timeline first.');
        }
        let imageResponse;
        try { imageResponse = await fetch(options.inputSrc); }
        catch (_) { throw new Error('Could not access the selected image. Try an image uploaded to this post.'); }
        if (!imageResponse.ok) throw new Error('Could not load the selected source image.');
        const imageBlob = await imageResponse.blob();
        setStatus('Uploading source image to WaveSpeedAI…');
        const form = new FormData();
        form.append('prompt', request.asset.prompt);
        form.append('negative_prompt', typeof options.negativePrompt === 'string' ? options.negativePrompt : '');
        form.append('duration', String(options.duration || 5));
        form.append('size', options.size || '832*480');
        form.append('post_id', String(post.id));
        form.append('file', imageBlob, 'studio-source-image');
        row = await api('POST', '/api/wavespeed/generate-image-to-video', form);
      } else {
        row = await api('POST', '/api/wavespeed/generate',
          { asset: {
            type: request.asset.type, prompt: request.asset.prompt, model: request.asset.model,
            duration: options.duration, size: options.size, negative_prompt: options.negativePrompt,
            num_images: options.numImages,
          },
            post_id: post.id });
      }
      if (state.wavespeed && state.wavespeed.ai_quota) {
        const currentQuota = state.wavespeed.ai_quota;
        currentQuota.used = (Number(currentQuota.used) || 0) + 1;
        if (currentQuota.limit !== -1) {
          currentQuota.remaining = Math.max(0, (Number(currentQuota.limit) || 0) - currentQuota.used);
        }
      }
      const predictionId = row.prediction_id;
      if (!predictionId) throw new Error('WaveSpeedAI did not return a prediction id.');
      const terminal = new Set(['completed', 'failed', 'cancelled', 'timeout', 'deleted']);
      const startedAt = Date.now();
      while (!terminal.has(String(row.status || '').toLowerCase())) {
        if (Date.now() - startedAt > 10 * 60 * 1000) throw new Error('WaveSpeedAI generation timed out. Check your WaveSpeedAI history before retrying.');
        await new Promise(r => setTimeout(r, 2000));
        row = await api('GET', `/api/wavespeed/generate/${encodeURIComponent(predictionId)}`);
      }
      if (String(row.status).toLowerCase() !== 'completed' || !row.output_url) {
        throw new Error(row.error || `WaveSpeedAI generation ended with status "${row.status}".`);
      }
      const outputUrls = Array.isArray(row.output_urls) && row.output_urls.length
        ? row.output_urls : [row.output_url];
      const selectedUrl = outputUrls.length > 1 ? await chooseGeneratedImage(outputUrls) : outputUrls[0];
      if (!selectedUrl) throw new Error('Image selection cancelled; generated results were not added to the timeline.');
      return { url: selectedUrl };
    });
  } catch (e) {
    setStatus('');
    $('[data-shotstack-studio]', app).outerHTML = `<div class="empty" style="padding:20px"><p class="muted">The editor could not load: ${esc(e.message || e)}</p></div>`;
    return;
  }

  async function saveDraft() {
    clearTimeout(draftSaveTimer);
    draftSaveTimer = null;
    if (draftSaveInFlight) {
      await draftSaveInFlight;
      return draftDirty ? saveDraft() : 'saved';
    }
    if (!draftDirty) return 'clean';
    const revision = draftRevision;
    const snapshot = edit.getEdit();
    draftDirty = false;
    draftSaveInFlight = api('PUT', `/api/posts/${post.id}/studio-draft`, { edit: snapshot });
    try {
      await draftSaveInFlight;
      if (draftRevision === revision) {
        try { sessionStorage.removeItem(sessionDraftKey); } catch (error) {
          setStatus(`Draft saved; browser recovery cache could not be cleared: ${error.message || error}`, true);
        }
      } else {
        draftDirty = true;
      }
      return 'saved';
    } catch (error) {
      draftDirty = true;
      throw error;
    } finally {
      draftSaveInFlight = null;
    }
  }
  let autosaveFailureNotified = false;
  const autosaveDraft = async () => {
    try {
      const result = await saveDraft();
      if (result === 'saved') setStatus('Draft saved automatically', true);
      autosaveFailureNotified = false;
    } catch (error) {
      setStatus(`Autosave failed: ${error.message || error}`, true);
      if (!autosaveFailureNotified) {
        toast('Studio draft could not be autosaved. Check your connection and use Save draft to retry.', true);
        autosaveFailureNotified = true;
      }
    }
  };
  scheduleDraftSave = () => {
    clearTimeout(draftSaveTimer);
    draftSaveTimer = setTimeout(autosaveDraft, 500);
  };
  $('#studio-save-draft').onclick = guard(async () => {
    setStatus('Saving draft…');
    try {
      const result = await saveDraft();
      setStatus(result === 'saved' ? 'Draft saved' : 'Draft already up to date', true);
      autosaveFailureNotified = false;
    } catch (error) {
      setStatus(`Draft save failed: ${error.message || error}`, true);
      throw error;
    }
  });
  const autosave = setInterval(() => { if (draftDirty) autosaveDraft(); }, 20000);
  const flushPendingDraft = () => {
    clearTimeout(draftSaveTimer);
    if (draftDirty) autosaveDraft();
  };
  const flushWhenHidden = () => { if (document.visibilityState === 'hidden') flushPendingDraft(); };
  document.addEventListener('visibilitychange', flushWhenHidden);
  window.addEventListener('pagehide', flushPendingDraft, { once: true });
  window.addEventListener('hashchange', () => {
    clearInterval(autosave);
    document.removeEventListener('visibilitychange', flushWhenHidden);
    flushPendingDraft();
  }, { once: true });
  if (sessionDraft) scheduleDraftSave();

  async function finishedDraftCleanup() {
    clearInterval(autosave);
    clearTimeout(draftSaveTimer);
    if (draftSaveInFlight) {
      try { await draftSaveInFlight; } catch (error) {
        setStatus(`Pending draft save failed before cleanup: ${error.message || error}`, true);
      }
    }
    draftDirty = false;
    try {
      await api('DELETE', `/api/posts/${post.id}/studio-draft`);
      sessionStorage.removeItem(sessionDraftKey);
    } catch (error) {
      toast(`Video finished, but its saved draft could not be cleared: ${error.message || error}`, true);
    }
  }

  $('#studio-add-media').onclick = () => $('#studio-upload', app).click();
  if ($('#studio-add-ai')) $('#studio-add-ai').onclick = guard(async () => {
    const quota = state.wavespeed && state.wavespeed.ai_quota;
    const quotaHint = quota
      ? `<p class="small muted" style="margin:6px 0 10px">Subscription AI requests this month: ${Number(quota.used) || 0}${quota.limit === -1 ? ' used · unlimited' : ` of ${Number(quota.limit) || 0} used`}. Each generation counts once; image variations are part of one request.</p>`
      : '';
    const picked = await new Promise(resolve => {
      let settled = false;
      const finish = v => { if (!settled) { settled = true; obs.disconnect(); resolve(v); } };
      const m = openModal(`<h2>Add an AI-generated clip</h2>
        <p class="small muted" style="margin:6px 0 10px">Images and videos are generated by WaveSpeedAI using the API key configured on this server. Generation uses your WaveSpeedAI account credits.</p>
        ${quotaHint}
        <label class="small muted">Type</label>
        <select id="ai-type" style="width:100%;margin-bottom:10px"><option value="image">Image</option><option value="video">Video</option></select>
        <label class="small muted" id="ai-video-mode-label" hidden>Video generation</label>
        <select id="ai-video-mode" style="width:100%;margin-bottom:10px" hidden>
          <option value="text">Text to video</option><option value="image">Animate a timeline image</option>
        </select>
        <label class="small muted" id="ai-source-image-label" hidden>Starting image</label>
        <select id="ai-source-image" style="width:100%;margin-bottom:10px" hidden></select>
        <div id="ai-image-options">
          <label class="small muted">Variations</label>
          <select id="ai-image-count" style="width:100%;margin-bottom:10px">
            <option value="1">1 image</option><option value="2">2 images</option>
            <option value="3">3 images</option><option value="4">4 images</option>
          </select>
          <p class="small muted" style="margin:0 0 10px">More variations use more WaveSpeedAI credits.</p>
        </div>
        <div id="ai-video-options" hidden>
          <label class="small muted">Aspect ratio</label>
          <select id="ai-video-size" style="width:100%;margin-bottom:10px">
            <option value="832*480">Landscape · 16:9</option><option value="480*832">Portrait · 9:16</option>
          </select>
          <label class="small muted">Duration</label>
          <select id="ai-video-duration" style="width:100%;margin-bottom:10px">
            <option value="5">5 seconds</option><option value="10">10 seconds</option>
          </select>
          <label class="small muted">Negative prompt <span class="muted">(optional)</span></label>
          <textarea id="ai-negative-prompt" rows="2" maxlength="1000" style="width:100%;margin-bottom:10px"
            placeholder="Things to avoid, e.g. blur, jitter, distorted hands"></textarea>
        </div>
        <label class="small muted">Model</label>
        <select id="ai-model" style="width:100%;margin-bottom:10px"></select>
        <label class="small muted">Prompt</label>
        <textarea id="ai-prompt" rows="3" style="width:100%" placeholder="A lighthouse on a rocky coast at sunset, cinematic lighting"></textarea>
        <div class="actions"><span class="spacer"></span><button class="btn" data-close type="button">Cancel</button>
          <button class="btn primary" type="button" id="ai-ok">Add clip</button></div>`);
      const obs = new MutationObserver(() => { if (!document.body.contains(m)) finish(null); });
      obs.observe(document.body, { childList: true, subtree: true });
      const typeInput = $('#ai-type', m), videoModeInput = $('#ai-video-mode', m);
      const sourceImageInput = $('#ai-source-image', m), modelInput = $('#ai-model', m);
      const timelineImages = edit.getEdit().timeline.tracks.flatMap((track, trackIndex) =>
        (track.clips || []).flatMap((clip, clipIndex) =>
          clip.asset && clip.asset.type === 'image' && typeof clip.asset.src === 'string' && clip.asset.src
            ? [{ src: clip.asset.src, start: Number(clip.start) || 0, trackIndex, clipIndex }]
            : []));
      const syncModels = () => {
        const imageToVideo = typeInput.value === 'video' && videoModeInput.value === 'image';
        const imageGeneration = typeInput.value === 'image';
        $('#ai-image-options', m).hidden = !imageGeneration;
        $('#ai-video-options', m).hidden = imageGeneration;
        $('#ai-video-mode-label', m).hidden = imageGeneration;
        videoModeInput.hidden = imageGeneration;
        $('#ai-source-image-label', m).hidden = !imageToVideo;
        sourceImageInput.hidden = !imageToVideo;
        sourceImageInput.innerHTML = timelineImages.map((source, index) =>
          `<option value="${index}">Image · ${source.start.toFixed(1)}s · Track ${source.trackIndex + 1}</option>`).join('');
        sourceImageInput.disabled = !timelineImages.length;
        modelInput.innerHTML = (imageGeneration
        ? [['wavespeed-ai/flux-dev', 'FLUX Dev · image'], ['wavespeed-ai/flux-schnell', 'FLUX Schnell · fast image']]
        : imageToVideo
          ? [['wavespeed-ai/wan-2.1/i2v-480p', 'Wan 2.1 · image to video · 480p']]
          : [['wavespeed-ai/wan-2.1/t2v-480p', 'Wan 2.1 · text to video · 480p']])
          .map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
      };
      typeInput.onchange = syncModels;
      videoModeInput.onchange = syncModels;
      syncModels();
      $('#ai-ok', m).onclick = () => {
        const prompt = $('#ai-prompt', m).value.trim();
        if (!prompt) { toast('Enter a prompt first', true); return; }
        const source = videoModeInput.value === 'image' ? timelineImages[Number(sourceImageInput.value)] : null;
        if (typeInput.value === 'video' && videoModeInput.value === 'image' && !source) {
          toast('Add an image to the timeline before choosing image-to-video.', true);
          return;
        }
        const duration = typeInput.value === 'video' ? Number($('#ai-video-duration', m).value) : 5;
        finish({
          type: typeInput.value, prompt, model: modelInput.value, image_src: source && source.src,
          duration, size: $('#ai-video-size', m).value,
          negative_prompt: $('#ai-negative-prompt', m).value.trim(),
          num_images: typeInput.value === 'image' ? Number($('#ai-image-count', m).value) : 1,
        });
        m.close();
      };
      $('#ai-prompt', m).focus();
    });
    if (!picked) return;
    const options = picked.type === 'image'
      ? { numImages: picked.num_images }
      : { inputSrc: picked.image_src, duration: picked.duration, size: picked.size,
        negativePrompt: picked.negative_prompt };
    const clip = { asset: { type: picked.type, prompt: picked.prompt, model: picked.model, options },
      start: 0, length: picked.type === 'image' ? 3 : picked.duration };
    await edit.addClip(baseTrackIndex(), clip);
  });
  $('#studio-add-text').onclick = guard(async () => {
    const text = await askText('Text to show on the video', { title: 'Add text', placeholder: 'Your text here' });
    if (!text) return;
    // Track 0 renders on top of later tracks, so an overlay goes at index 0 (shifting the rest down) -
    // appending at the end would put it UNDER the existing visual track, hidden behind it. A new track
    // needs its first clip right in the same call, addTrack rejects an empty one.
    await edit.addTrack(0, { clips: [{ asset: { type: 'text', text }, start: 0, length: 3 }] });
  });

  async function addStudioAudio(file) {
    setStatus('Uploading audio…');
    const fd = new FormData();
    fd.append('file', file, file.name || 'voiceover.wav');
    const r = await api('POST', `/api/posts/${post.id}/studio-audio/upload`, fd);
    const ext = { 'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/mp4': 'm4a', 'audio/ogg': 'ogg' }[r.type] || 'mp3';
    const src = `${location.origin}/api/posts/${post.id}/studio-audio/${r.id}/file.${ext}`;
    const trackIdx = edit.getEdit().timeline.tracks.length;
    const added = await edit.addTrack(trackIdx, { clips: [{ asset: { type: 'audio', src }, start: 0, length: 'auto' }] });
    if (added.status !== 'success') throw new Error(added.message || 'Could not add the uploaded audio clip.');
    const addedClip = edit.getClip(trackIdx, 0);
    if (addedClip) {
      selectedClipLocation = { trackIndex: trackIdx, clipIndex: 0 };
      selectedClipId = addedClip.id || null;
      syncStudioClipActions();
    }
    setStatus('Added', true);
  }
  $('#studio-add-audio').onclick = () => $('#studio-audio-upload', app).click();
  $('#studio-audio-upload').onchange = guard(async event => {
    const file = event.target.files && event.target.files[0];
    event.target.value = '';
    if (file) await addStudioAudio(file);
  });
  $('#studio-voiceover').onclick = guard(async () => {
    const modal = openModal(`
      <div class="modal-head"><h2>Voiceover &amp; audio cleanup</h2><button class="icon-btn" type="button" data-close aria-label="Close">×</button></div>
      <p class="studio-caption-privacy">Record from your microphone or choose an audio file. Processing happens in this browser; the result uploads only when you add it to the timeline.</p>
      <div class="studio-voiceover-actions">
        <button class="btn primary" id="studio-voice-record" type="button">Start recording</button>
        <button class="btn" id="studio-voice-stop" type="button" disabled>Stop recording</button>
        <span id="studio-voice-timer" class="small muted" role="status" aria-live="polite">Ready · up to 3 minutes</span>
      </div>
      <label class="studio-caption-check"><input id="studio-voice-noise-reduction" type="checkbox" checked> Reduce microphone background noise</label>
      <label class="studio-voiceover-file">Clean an existing recording
        <input id="studio-clean-audio-file" type="file" accept="audio/*">
      </label>
      <label class="studio-caption-check"><input id="studio-voice-cleanup" type="checkbox" checked> Voice cleanup (low rumble filter, compressor, and level normalization)</label>
      <label class="studio-caption-check"><input id="studio-voice-gate" type="checkbox"> Reduce quiet room noise between phrases (noise gate)</label>
      <p class="small muted">Microphone recording also requests the browser's echo cancellation and noise suppression. The noise gate can soften very quiet speech.</p>
      <div id="studio-voice-processing" class="small muted" role="status" aria-live="polite"></div>
      <audio id="studio-voice-preview" class="studio-voice-preview" controls hidden></audio>
      <div class="modal-actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" id="studio-voice-add" type="button" disabled>Add audio to timeline</button></div>
    `, true);
    const recordButton = $('#studio-voice-record', modal);
    const stopButton = $('#studio-voice-stop', modal);
    const timerLabel = $('#studio-voice-timer', modal);
    const statusLabel = $('#studio-voice-processing', modal);
    const preview = $('#studio-voice-preview', modal);
    const addButton = $('#studio-voice-add', modal);
    const audioFileInput = $('#studio-clean-audio-file', modal);
    let recorder = null, stream = null, timer = null, startedAt = 0, elapsedSeconds = 0;
    let chunks = [], processedFile = null, previewUrl = '', closed = false, processing = false;
    const maxRecordingSeconds = 180;
    const releaseStream = () => {
      if (stream) stream.getTracks().forEach(track => track.stop());
      stream = null;
      recorder = null;
      clearInterval(timer);
      timer = null;
      recordButton.disabled = false;
      stopButton.disabled = true;
    };
    const originalClose = modal.close;
    modal.close = () => {
      closed = true;
      if (recorder && recorder.state !== 'inactive') recorder.stop();
      releaseStream();
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      originalClose();
    };
    const encodeWav = buffer => {
      const channels = buffer.numberOfChannels;
      const frames = buffer.length;
      const bytesPerSample = 2;
      const dataSize = frames * channels * bytesPerSample;
      const out = new ArrayBuffer(44 + dataSize);
      const view = new DataView(out);
      const writeString = (offset, value) => {
        for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
      };
      writeString(0, 'RIFF');
      view.setUint32(4, 36 + dataSize, true);
      writeString(8, 'WAVE');
      writeString(12, 'fmt ');
      view.setUint32(16, 16, true);
      view.setUint16(20, 1, true);
      view.setUint16(22, channels, true);
      view.setUint32(24, buffer.sampleRate, true);
      view.setUint32(28, buffer.sampleRate * channels * bytesPerSample, true);
      view.setUint16(32, channels * bytesPerSample, true);
      view.setUint16(34, 16, true);
      writeString(36, 'data');
      view.setUint32(40, dataSize, true);
      let offset = 44;
      for (let frame = 0; frame < frames; frame++) {
        for (let channel = 0; channel < channels; channel++) {
          const sample = Math.max(-1, Math.min(1, buffer.getChannelData(channel)[frame]));
          view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
          offset += bytesPerSample;
        }
      }
      return new Blob([out], { type: 'audio/wav' });
    };
    const processAudio = async inputBlob => {
      processing = true;
      addButton.disabled = true;
      recordButton.disabled = true;
      audioFileInput.disabled = true;
      statusLabel.textContent = 'Processing audio in this browser…';
      try {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        const OfflineContextClass = window.OfflineAudioContext || window.webkitOfflineAudioContext;
        if (!AudioContextClass || !OfflineContextClass) throw new Error('This browser does not support local audio processing.');
        const decodeContext = new AudioContextClass();
        let decoded;
        try { decoded = await decodeContext.decodeAudioData(await inputBlob.arrayBuffer()); } finally {
          await decodeContext.close();
        }
        if (!decoded.length || decoded.duration > maxRecordingSeconds) {
          throw new Error('Choose or record audio that is 3 minutes or shorter.');
        }
        const outputSampleRate = Math.min(decoded.sampleRate, 48000);
        const offline = new OfflineContextClass(1, Math.ceil(decoded.duration * outputSampleRate), outputSampleRate);
        const source = offline.createBufferSource();
        source.buffer = decoded;
        let output = source;
        if ($('#studio-voice-cleanup', modal).checked) {
          const highpass = offline.createBiquadFilter();
          highpass.type = 'highpass';
          highpass.frequency.value = 85;
          highpass.Q.value = 0.7;
          const compressor = offline.createDynamicsCompressor();
          compressor.threshold.value = -24;
          compressor.knee.value = 18;
          compressor.ratio.value = 3;
          compressor.attack.value = 0.004;
          compressor.release.value = 0.22;
          output.connect(highpass);
          highpass.connect(compressor);
          output = compressor;
        }
        output.connect(offline.destination);
        source.start(0);
        const cleaned = await offline.startRendering();
        let peak = 0;
        for (let channel = 0; channel < cleaned.numberOfChannels; channel++) {
          const samples = cleaned.getChannelData(channel);
          for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i]));
        }
        const normalizeGain = peak > 0 ? Math.min(8, 0.89125 / peak) : 1;
        for (let channel = 0; channel < cleaned.numberOfChannels; channel++) {
          const samples = cleaned.getChannelData(channel);
          for (let i = 0; i < samples.length; i++) samples[i] *= normalizeGain;
        }
        if ($('#studio-voice-gate', modal).checked) {
          const channels = Array.from({ length: cleaned.numberOfChannels }, (_, i) => cleaned.getChannelData(i));
          const windowSize = Math.max(1, Math.round(cleaned.sampleRate * 0.01));
          const threshold = 0.008;
          const reduceCoefficient = 1 - Math.exp(-1 / (cleaned.sampleRate * 0.015));
          const restoreCoefficient = 1 - Math.exp(-1 / (cleaned.sampleRate * 0.08));
          let envelope = 1;
          for (let start = 0; start < cleaned.length; start += windowSize) {
            const end = Math.min(cleaned.length, start + windowSize);
            let squareSum = 0, count = 0;
            for (const samples of channels) {
              for (let i = start; i < end; i++) { squareSum += samples[i] * samples[i]; count++; }
            }
            const rms = Math.sqrt(squareSum / Math.max(1, count));
            const target = rms < threshold ? 0.22 : 1;
            const coefficient = target < envelope ? reduceCoefficient : restoreCoefficient;
            for (let i = start; i < end; i++) {
              envelope += (target - envelope) * coefficient;
              for (const samples of channels) samples[i] *= envelope;
            }
          }
        }
        if (closed) return;
        processedFile = encodeWav(cleaned);
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(processedFile);
        preview.src = previewUrl;
        preview.hidden = false;
        addButton.disabled = false;
        statusLabel.textContent = `Ready · ${cleaned.duration.toFixed(1)} seconds · mono WAV normalized to -1 dB peak`;
      } finally {
        processing = false;
        if (!closed) {
          recordButton.disabled = false;
          audioFileInput.disabled = false;
        }
      }
    };
    const formatElapsed = seconds => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    recordButton.onclick = guard(async () => {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
        throw new Error('Microphone recording is not supported in this browser. You can still choose an audio file to clean.');
      }
      const acquiredStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: $('#studio-voice-noise-reduction', modal).checked,
          noiseSuppression: $('#studio-voice-noise-reduction', modal).checked,
          autoGainControl: true,
        },
      });
      if (closed) {
        acquiredStream.getTracks().forEach(track => track.stop());
        return;
      }
      stream = acquiredStream;
      chunks = [];
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = event => {
        releaseStream();
        toast(`Microphone recording failed: ${event.error && event.error.message || 'unknown error'}`, true);
      };
      recorder.onstop = async () => {
        const recorded = new Blob(chunks, { type: recorder && recorder.mimeType || 'audio/webm' });
        releaseStream();
        if (!closed && recorded.size) {
          try { await processAudio(recorded); } catch (error) { statusLabel.textContent = error.message || String(error); }
        }
      };
      recorder.start(1000);
      startedAt = Date.now();
      elapsedSeconds = 0;
      recordButton.disabled = true;
      stopButton.disabled = false;
      audioFileInput.disabled = true;
      processedFile = null;
      addButton.disabled = true;
      preview.hidden = true;
      statusLabel.textContent = 'Recording…';
      timerLabel.textContent = '0:00 / 3:00';
      timer = setInterval(() => {
        elapsedSeconds = Math.floor((Date.now() - startedAt) / 1000);
        timerLabel.textContent = `${formatElapsed(elapsedSeconds)} / 3:00`;
        if (elapsedSeconds >= maxRecordingSeconds && recorder && recorder.state === 'recording') recorder.stop();
      }, 250);
    });
    stopButton.onclick = () => {
      if (recorder && recorder.state === 'recording') {
        stopButton.disabled = true;
        recordButton.disabled = true;
        statusLabel.textContent = 'Finishing recording…';
        recorder.stop();
      }
    };
    audioFileInput.onchange = guard(async event => {
      const file = event.target.files && event.target.files[0];
      event.target.value = '';
      if (!file) return;
      statusLabel.textContent = `Loading ${file.name}…`;
      try { await processAudio(file); } catch (error) { statusLabel.textContent = error.message || String(error); throw error; }
    });
    $('#studio-voice-add', modal).onclick = guard(async () => {
      if (!processedFile || processing) return;
      await addStudioAudio(processedFile);
      modal.close();
    });
    modal.querySelector('[data-close]').focus();
  });

  $('#studio-upload').onchange = guard(async e => {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!f) return;
    const isImg = String(f.type).startsWith('image/');
    setStatus('Uploading…');
    const fd = new FormData();
    fd.append('file', f);
    try {
      const r = await api('POST', `/api/posts/${post.id}/${isImg ? 'images/upload' : 'video/upload'}`, fd);
      const ext = MIME_EXT[r.type] || (isImg ? 'jpg' : 'mp4');
      const src = `${location.origin}/api/posts/${post.id}/${isImg ? `images/${r.id}/file.${ext}` : `video/file.${ext}`}`;
      await edit.addClip(baseTrackIndex(), { asset: { type: isImg ? 'image' : 'video', src }, start: 0, length: isImg ? 3 : 'auto' });
      setStatus('Added', true);
    } catch (err) { toast(err.message || 'Could not upload that file', true); setStatus('Ready'); }
  });

  $('#studio-finish').onclick = guard(isImageFormat(post.content_type) ? finishImages : finishVideo);

  async function finishVideo() {
    const finishBtn = $('#studio-finish', app), addBtn = $('#studio-add-media', app);
    finishBtn.disabled = true; addBtn.disabled = true;
    const exporter = new StudioSdk.VideoExporter(edit, canvas);
    try {
      const blob = await exporter.exportBlob({
        onProgress: (fraction, stage) => setStatus(`${stage}… ${Math.round(fraction * 100)}%`),
      });
      setStatus('Uploading…');
      const fd = new FormData();
      fd.append('file', blob, 'studio-export.mp4');
      await api('POST', `/api/posts/${post.id}/video/upload`, fd);
      await finishedDraftCleanup();
      setStatus('Done — video attached to this post');
      toast('Video attached to this post');
    } catch (err) {
      // ExportError carries .issues when the edit uses something the browser can't export (filters,
      // transitions, chroma key, custom fonts, AI clips still pending, ...) - those messages are already
      // written for a human, so show them as-is rather than just the generic ExportError message.
      const issues = (err && err.issues) || [];
      toast(issues.length ? issues.map(i => i.message).join(' ') : (err.message || 'Could not finish this video in the browser.'), true);
      setStatus('Ready');
    } finally {
      exporter.dispose();
      syncStudioOutputButtons(); addBtn.disabled = false;
    }
  }

  // A photo/carousel post publishes as still images, not a video, so "finish" here means capturing each
  // image clip's own edited frame (crop, filter, text overlay, whatever Studio applied) and replacing the
  // post's images with those stills, in timeline order - not exporting a slideshow video. All frames are
  // captured (pure client-side canvas work, no server round-trip) before anything is uploaded, so a capture
  // failure never touches the post's existing images. Each new image then replaces its OLD counterpart one
  // at a time (delete old, then upload new) rather than uploading everything first and deleting after - a
  // carousel already at the 20-image cap would otherwise reject every new upload before any old one is
  // freed up. That does mean a failed upload mid-way loses that one slot (not the whole carousel); the
  // remaining old images stay untouched, matching the server's own "a post can hold at most 20 images" limit.
  async function finishImages() {
    const finishBtn = $('#studio-finish', app), addBtn = $('#studio-add-media', app);
    finishBtn.disabled = true; addBtn.disabled = true;
    try {
      // Image clips can be on any track now that text/audio get their own (text goes in at index 0 so it
      // overlays on top, pushing the original image track down) - search all of them, not just track 0.
      const clips = edit.getEdit().timeline.tracks.flatMap(t => t.clips || [])
        .filter(c => c.asset && c.asset.type === 'image')
        .sort((a, b) => a.start - b.start);
      if (!clips.length) { toast('No images on the timeline to finish.', true); return; }
      const before = await api('GET', `/api/posts/${post.id}/images`);
      const captured = [];
      for (let i = 0; i < clips.length; i++) {
        setStatus(`Capturing image ${i + 1} of ${clips.length}…`);
        edit.seek(clips[i].start + clips[i].length / 2);
        // captureFrame renders its own frame rather than reading back whatever the live ticker last drew,
        // so this only needs to wait for seek()'s own state update to land, not a real animation frame -
        // which matters because rAF never fires at all while this tab sits in the background.
        await new Promise(r => setTimeout(r, 50));
        const dataUrl = await canvas.captureFrame({ format: 'jpeg', quality: 0.92 });
        captured.push(await (await fetch(dataUrl)).blob());
      }
      setStatus('Uploading…');
      for (let i = 0; i < captured.length; i++) {
        setStatus(`Uploading ${i + 1} of ${captured.length}…`);
        if (before[i]) await api('DELETE', `/api/posts/${post.id}/images/${before[i].id}`);
        const fd = new FormData();
        fd.append('file', captured[i], `studio-export-${i + 1}.jpg`);
        await api('POST', `/api/posts/${post.id}/images/upload`, fd);
      }
      for (let i = captured.length; i < before.length; i++) await api('DELETE', `/api/posts/${post.id}/images/${before[i].id}`);
      await finishedDraftCleanup();
      setStatus(`Done — ${captured.length} image${captured.length === 1 ? '' : 's'} attached to this post`);
      toast('Images attached to this post');
    } catch (err) {
      toast(err.message || 'Could not finish these images in the browser.', true);
      setStatus('Ready');
    } finally {
      syncStudioOutputButtons(); addBtn.disabled = false;
    }
  }
};


/* ---------------- saved ideas (the inspiration library; routes, tables and API paths keep that name) ---------------- */
/* ---------------- discover: what is working for similar creators ---------------- */
const PLAT_NAME = { youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram' };
// each platform's own official embed player, so a video plays here without leaving HookTrack
function embedUrl(x) {
  const id = encodeURIComponent(x.video_id || '');
  if (!id) return '';
  if (x.platform === 'tiktok') return `https://www.tiktok.com/embed/v2/${id}`;
  if (x.platform === 'instagram') return `https://www.instagram.com/reel/${id}/embed`;
  return `https://www.youtube.com/embed/${id}?autoplay=1&rel=0&playsinline=1`;
}
function playVideoModal(x) {
  const src = embedUrl(x);
  if (!src) { window.open(x.url, '_blank', 'noopener'); return; }
  const el = openModal(`<div class="vid-head"><div class="hook">${esc(x.title)}</div><button class="btn sm" data-close>Close</button></div>
    <div class="vid-frame ${x.platform === 'youtube' ? '' : 'tall'}"><iframe src="${esc(src)}" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin" title="${esc(x.title)}"></iframe></div>
    <div class="small muted" style="margin-top:8px">Not playing? <a href="${esc(x.url)}" target="_blank" rel="noopener">Watch it on ${PLAT_NAME[x.platform] || 'the platform'} ↗</a></div>`);
  el.querySelector('.modal').classList.add('vid-modal');
  el.addEventListener('click', e => { if (e.target === el) el.close(); });   // click outside closes, and the video stops
}
pages.discover = async app => {
  const q = new URLSearchParams();
  if (state.discPlatform) q.set('platform', state.discPlatform);
  if (state.discNiche === null) q.set('mine', '1');           // first visit: start on the niche and size that fit this workspace
  else {
    if (state.discNiche) q.set('niche', state.discNiche);
    if (state.discBand) q.set('band', state.discBand);
  }
  const d = await api('GET', '/api/discover' + (q.toString() ? '?' + q : ''));
  if (state.discNiche === null) { state.discNiche = d.applied.niche; state.discBand = d.applied.band; }
  const fmtViews = n => fmt(n);
  const creators = new Set(d.items.map(x => x.channel).filter(Boolean)).size;
  const patterns = new Set(d.items.map(x => x.pattern).filter(Boolean)).size;
  const lifts = d.items.map(x => Number(x.multiple)).filter(Number.isFinite).sort((a, b) => a - b);
  const typicalLift = lifts.length ? lifts[Math.floor(lifts.length / 2)] : null;
  const empty = !d.available
    ? 'This feed is built from YouTube data, which is not switched on for this platform yet.'
    : 'Nothing here yet. The feed is rebuilt every couple of days for each niche, so check back soon, or try another niche.';
  app.innerHTML = `<div class="discover-page">
    <div class="page-head discover-head"><div><div class="eyebrow">CREATOR RESEARCH</div><h1>Discover what’s working</h1>
      <div class="sub">Study short-form posts outperforming their creator’s usual results, then make a fresh version that fits your voice.</div></div>
      <a class="btn" href="#/inspiration">♡ Saved ideas</a></div>
    <div class="grid discover-overview">
      <div class="card discover-metric"><span class="discover-metric-icon">▣</span><div><span>Ideas in this feed</span><b>${d.items.length}</b><small>Matched to your filters</small></div></div>
      <div class="card discover-metric"><span class="discover-metric-icon creators">◉</span><div><span>Creators to learn from</span><b>${creators}</b><small>Distinct channels represented</small></div></div>
      <div class="card discover-metric"><span class="discover-metric-icon lift">↗</span><div><span>Typical outperformance</span><b>${typicalLift == null ? '—' : `${typicalLift}×`}</b><small>Compared with creator’s usual</small></div></div>
      <div class="card discover-metric"><span class="discover-metric-icon pattern">✳</span><div><span>Hook patterns</span><b>${patterns}</b><small>Different opening approaches</small></div></div>
    </div>
    <div class="discover-controls">
      <div class="discover-controls-heading"><div><h2>Explore the feed</h2><span class="muted small">${esc(d.rule)}</span></div>
        <a href="#/inspiration" class="text-link">Open idea library →</a></div>
      <div class="row discover-filters">
      ${d.platforms.length > 1 ? `<label class="small muted">Platform</label>
      <select id="dp" style="width:auto"><option value="">All</option>${d.platforms.map(p => `<option value="${esc(p.key)}" ${state.discPlatform === p.key ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select>` : ''}
      <label class="small muted">Niche</label>
      <select id="dn" style="width:auto"><option value="">All niches</option>${d.niches.map(n => `<option value="${esc(n.key)}" ${state.discNiche === n.key ? 'selected' : ''}>${esc(n.label)}${n.count ? ` (${n.count})` : ''}</option>`).join('')}</select>
      <label class="small muted">Creator size</label>
      <select id="db" style="width:auto"><option value="">Any size</option>${d.bands.map(b => `<option value="${esc(b.key)}" ${state.discBand === b.key ? 'selected' : ''}>${esc(b.label)}</option>`).join('')}</select>
      </div>
      <div class="small muted discover-caution">${esc(d.caution)}</div>
    </div>
    ${d.items.length ? `<div class="discover-results-head"><h2>Ideas for your next post</h2><span class="muted small">${d.items.length} examples · sorted by outperformance</span></div><div class="insp-grid disc-grid">${d.items.map((x, i) => `<div class="insp-card disc-card">
      <a href="${esc(x.url)}" class="disc-play" data-play="${i}" title="Play it here">${x.thumb ? `<img src="${esc(x.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<div class="disc-tile">${PLAT_NAME[x.platform] || 'Video'}</div>`}
        <span class="disc-chip plat">${window.socialPlatformIdentity(x.platform, PLAT_NAME[x.platform] || 'Video')}</span>
        <span class="disc-chip views">${fmtViews(x.views)} views</span>
        <span class="disc-play-btn" aria-hidden="true">▶</span></a>
      <div class="body">
        <div class="disc-tags"><span class="pill SCALE" title="${esc(x.note)}">${esc(x.multiple_label.replace('x', '×'))} their usual</span>
          <span class="pill" title="Creator size">${esc(x.band_label)}</span></div>
        <div class="hook disc-title" title="${esc(x.title)}">${esc(x.title)}</div>
        <div class="disc-meta">${esc(x.channel)} · ${x.days_ago === 0 ? 'today' : x.days_ago + 'd ago'}</div>
        ${[['♥', 'Likes', x.likes], ['💬', 'Comments', x.comments], ['↗', 'Shares', x.shares]].filter(e => e[2] != null).length
          ? `<div class="disc-stats">${[['♥', 'Likes', x.likes], ['💬', 'Comments', x.comments], ['↗', 'Shares', x.shares]].filter(e => e[2] != null).map(e => `<span title="${e[1]}">${e[0]} ${fmt(e[2])}</span>`).join('')}</div>` : ''}
        <div class="disc-why">${esc(x.pattern)}. ${esc(x.note)}.</div>
        <div class="actions row disc-actions">
          <button class="btn sm primary" data-mk="${i}" title="Write your own take, with AI">Make my version</button>
          <button class="btn sm" data-sv="${i}" title="Keep it on your Saved page">Save</button>
          <button class="btn sm" data-play="${i}" title="Play it here, without leaving HookTrack">▶ Play</button>
        </div></div></div>`).join('')}</div>`
      : `<div class="empty card discover-empty"><span class="discover-empty-icon">⌕</span><h2>Nothing to show yet</h2><p>${esc(empty)}</p></div>`}
    </div>`;
  if ($('#dp', app)) $('#dp', app).onchange = e => { state.discPlatform = e.target.value; route(); };
  $('#dn', app).onchange = e => { state.discNiche = e.target.value; route(); };
  $('#db', app).onchange = e => { state.discBand = e.target.value; route(); };
  $$('[data-play]', app).forEach(b => (b.onclick = e => { e.preventDefault(); playVideoModal(d.items[+b.dataset.play]); }));
  $$('[data-mk]', app).forEach(b => (b.onclick = guard(async () => {
    const x = d.items[+b.dataset.mk];
    if (!state.accounts.length) { toast('Add an account first (Accounts page) so the ideas are written for it.', true); return; }
    ideasModal(route, { brief: `Write my own version of this hook that worked for a similar creator: "${x.title}" (${x.pattern.toLowerCase()}). Keep the idea, change the subject and wording so it is mine.` });
  })));
  $$('[data-sv]', app).forEach(b => (b.onclick = guard(async () => {
    const x = d.items[+b.dataset.sv];
    const f = new FormData();
    f.set('title', x.title); f.set('hook_text', x.title); f.set('source_url', x.url); f.set('platform', PLAT_NAME[x.platform] || 'YouTube');
    f.set('notes', `${x.note}. ${x.pattern}.`); f.set('use_in_ai', 'true');
    await api('POST', '/api/inspiration', f);
    b.textContent = 'Saved ✓'; b.disabled = true;
  })));
};

pages.inspiration = async app => {
  const rows = await api('GET', '/api/inspiration');
  app.innerHTML = `<div class="page-head"><div><h1>Saved</h1>
    <div class="sub">Ideas you keep: hooks from Discover, or a competitor's URL, screenshot and notes. Items marked for AI are used as examples when generating hooks.</div></div>
    <button class="btn primary" id="add">+ Add</button></div>
    ${rows.length ? `<div class="insp-grid">${rows.map(r => `
      <div class="insp-card" data-id="${r.id}">
        ${r.image_url ? `<img src="${esc(r.image_url)}" alt="">` : ''}
        <div class="body">
          <div class="row" style="gap:6px;flex-wrap:wrap">
            ${r.platform ? `<span class="pill">${window.socialPlatformIdentity(r.platform)}</span>` : ''}
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
      : `<div class="empty card"><h2>Nothing saved yet</h2><p>Paste a competitor URL, note the hook text, or drop a screenshot. AI ideas will pull these in as style references.</p></div>`}`;

  $('#add').onclick = () => inspirationModal(route);
  $$('[data-use]', app).forEach(b => (b.onclick = guard(async () => {
    const send = async accountId => {
      const r = await api('POST', `/api/inspiration/${b.dataset.use}/use`, accountId ? { account_id: accountId } : {});
      toast(`Added to Posts as an Idea (${r.label})`);
      location.hash = '#/pipeline';
    };
    if (state.accounts.length <= 1) { await send(null); return; }
    const mm = openModal(`<h2>Make an Idea from this</h2><p class="muted small">Which account is the new post for?</p>
      <div class="fields"><div class="full"><label class="f">For account</label>
        <select id="ia">${state.accounts.map(a => `<option value="${a.id}">${esc(a.handle)}</option>`).join('')}</select></div></div>
      <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="ig">Add to Posts</button></div>`);
    $('#ig', mm).onclick = guard(async () => { const v = +$('#ia', mm).value; mm.close(); await send(v); });
  })));
  $$('[data-del]', app).forEach(b => b.onclick = guard(async () => {
    if (!await askConfirm('Delete this saved idea?')) return;
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
  const m = openModal(`<h2>Add a saved idea</h2>
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
    if (!u) throw new Error('Paste a post link first');
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
    $('#fill-note', m).textContent = `Read ${r.platform} post${r.handle ? ' by @' + r.handle.replace(/^@/, '') : ''}. Edit anything, then save.`;
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
const srcTitle = s => ({ ai: 'Read from a screenshot by AI', sociavault: 'Synced: what your posts gained that day',
  estimated: 'Estimated from your posts\' totals; replaced by the exact number as syncs arrive', manual: 'Typed by hand' }[s] || '');
const SETTLE_DAYS = 3;
const stillGrowing = p => {
  const d = p && p.post_date ? parseISO(p.post_date) : null;
  return !!d && (nowLocal() - d) / 864e5 < SETTLE_DAYS;
};
// "Is this your post?": synced posts that could be the one a card became, so the link need not be pasted.
async function findVideoModal(card, done) {
  const m = openModal(`<h2>Find the posted content</h2>
    <p class="muted small">Posts from <b>${esc(card.handle || 'this account')}</b> made since this card was made, best match first.
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
    : '<p class="muted">No synced post from this account since the card was made. Post it, press Sync on Hooks, then try again.</p>';
  $$('[data-pick]', m).forEach(b => (b.onclick = guard(async () => {
    await api('POST', `/api/posts/${card.id}/link`, { post_id: +b.dataset.pick });
    toast('Linked: its results are on the card');
    m.close(); if (done) done();
  })));
}

// Which account is the new post for? Defaults to the hook's own; with several accounts the user picks.
function sendHookModal(hook, done) {
  const m = openModal(`<h2>Send to Posts</h2>
    <p class="muted small">“${esc(short(hook.hook || '(no hook)', 90))}” is a <b>${esc(PIPE_LABEL[hook.decision === 'SCALE' ? 'scripted' : 'idea'])}</b> next step.
      Which account is the new post for? Pick another account in the same niche to try the winner there.</p>
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
  if (p.followup) return `<span class="pill hook-added" title="Already added to the production board">✓ Added · ${esc(PIPE_LABEL[normStatus(p.followup.status)] || normStatus(p.followup.status))}</span>${
    p.followup.judged ? `<div class="small muted" title="Cards made from this hook that were marked analysed">${p.followup.beat} of ${p.followup.judged} beat it</div>` : ''}${
    state.accounts.length > 1 && POST_AS[p.decision] ? ` <button class="btn sm" type="button" data-post-hook="${p.id}" title="Create this as a post for another account">＋ Another account</button>` : ''}`;
  if (!POST_AS[p.decision]) return '<span class="muted">—</span>';
  return `<button class="btn sm primary hook-post-action" type="button" data-post-hook="${p.id}" title="Add this hook to the production board as ${POST_AS[p.decision]}">Post to Posts <span aria-hidden="true">→</span> ${POST_AS[p.decision]}</button>${
    stillGrowing(p) ? '<div class="small muted" title="Posted in the last 3 days: its views are still growing, so this decision can still change">⏳ still growing</div>' : ''}`;
};
pages.hooks = async app => {
  const q = [state.hookStatus && `status=${state.hookStatus}`, state.hookAccount && `account_id=${state.hookAccount}`].filter(Boolean).join('&');
  // Hooks are the posted videos the sync pulled in. A Scale or Tweak hook is sent to Posts by its Post button.
  const syncedPosts = await api('GET', `/api/posts?source=synced${q ? '&' + q : ''}`);   // only what the sync pulled in
  const hot = (await api('GET', '/api/velocity?days=3&limit=6').catch(() => [])).filter(p => p.perf && p.perf.label === 'HOT_START');
  const winnerCount = syncedPosts.filter(p => p.perf && p.perf.label === 'WINNER').length;
  const scoredCount = syncedPosts.filter(p => p.vsc != null).length;
  const totalViews = syncedPosts.reduce((sum, p) => sum + (Number(p.views) || 0), 0);
  const readyCount = syncedPosts.filter(p => POST_AS[p.decision] && !p.followup && !stillGrowing(p)).length;
  const growingCount = syncedPosts.filter(stillGrowing).length;
  const posts = syncedPosts.filter(p => {
    if (state.hookQuick === 'winners') return p.perf && p.perf.label === 'WINNER';
    if (state.hookQuick === 'growing') return stillGrowing(p);
    if (state.hookQuick === 'ready') return POST_AS[p.decision] && !p.followup && !stillGrowing(p);
    return true;
  });
  const quickLabel = { all: 'posts', winners: 'winning hooks', growing: 'posts still growing', ready: 'hooks ready to add' }[state.hookQuick] || 'posts';
  const hasSS = posts.some(p => p.saves != null || p.shares != null);      // only show columns that have data
  const hasLikes = posts.some(p => p.likes != null);
  app.innerHTML = `<div class="hooks-page">
  <div class="page-head hooks-head"><div><div class="eyebrow">PERFORMANCE LIBRARY</div><h1>Hooks</h1>
    <div class="sub">Learn from posts that beat your account’s normal—and turn the strongest ideas into your next post.</div></div>
    <div class="row hooks-primary-actions"><button class="btn" id="scorehooks" title="Score every judged hook from its real results (free, no AI). This also runs after every sync.">↻ Score hooks</button>
      <button class="btn primary" id="top5" title="Send the 5 strongest Scale hooks that have settled (3+ days old) and are not on Posts yet">Send top 5 to Posts</button></div></div>
  <div class="grid hooks-overview">
    <div class="card hooks-metric"><span class="hooks-metric-icon">▤</span><div><span class="hooks-metric-label">Synced posts</span><b>${syncedPosts.length}</b><small>From connected accounts</small></div></div>
    <div class="card hooks-metric"><span class="hooks-metric-icon winner">↗</span><div><span class="hooks-metric-label">Beating their normal</span><b>${winnerCount}</b><small>Winning hooks in this view</small></div></div>
    <div class="card hooks-metric"><span class="hooks-metric-icon score">✓</span><div><span class="hooks-metric-label">Scored</span><b>${scoredCount}<small> / ${syncedPosts.length}</small></b><small>From real post results</small></div></div>
    <div class="card hooks-metric"><span class="hooks-metric-icon views">◉</span><div><span class="hooks-metric-label">Views recorded</span><b>${compact(totalViews)}</b><small>Across synced posts</small></div></div>
  </div>
  ${hot.length ? `<section class="hot-starts">
    <div class="hot-starts-head"><span class="hot-starts-icon">↗</span><div><h2>Breaking out early</h2><p>Recent posts already far above their account’s usual first-day views.</p></div><span class="hot-starts-count">${hot.length} hot start${hot.length === 1 ? '' : 's'}</span></div>
    <div class="hot-starts-list">${hot.map(h => `<article class="hot-start-item"><div class="hot-start-handle">${esc(h.handle || 'Account')}</div>
      <div class="hot-start-copy"><b>${esc(short(h.hook || '(No hook text)', 74))}</b><span>${fmt(h.perf.early_views)} views on day one</span></div>
      <div class="hot-start-lift"><b>${esc(String(h.perf.early_multiple))}×</b><span>usual</span></div>
      <button class="btn sm" data-hot="${h.id}">Open post</button></article>`).join('')}</div>
    <div class="hot-starts-foot">Early momentum is a useful signal. Consider making a fresh variation while the format is working.</div>
  </section>` : ''}
  <div class="hooks-toolbar">
    <div class="hooks-toolbar-title"><h2>Synced content</h2><span class="muted small"><span id="hooks-result-count">${posts.length}</span> ${quickLabel} · Scale ≥ ${state.settings.scale_vsc} · Tweak ${state.settings.mutate_vsc}–${state.settings.scale_vsc - 1} · Stop below ${state.settings.mutate_vsc}</span></div>
    <div class="hooks-filters">
      <input id="hook-search" type="search" value="${esc(state.hookSearch)}" placeholder="Search hooks or accounts…" aria-label="Search hooks by text or account">
      <select id="fa" aria-label="Filter by account">${accountOptions(state.hookAccount, 'All accounts')}</select>
      <select id="fs" aria-label="Filter by status"><option value="">All statuses</option>${['posted', 'analysed'].map(s => `<option value="${s}" ${state.hookStatus === s ? 'selected' : ''}>${PIPE_LABEL[s]}</option>`).join('')}</select>
      <button class="btn" id="simdec" title="What if thresholds changed?">Simulate rules</button>
    </div>
  </div>
  <div class="hook-quick-filters" role="group" aria-label="Quick filters">
    <button type="button" class="hook-filter ${state.hookQuick === 'all' ? 'on' : ''}" data-hook-filter="all" aria-pressed="${state.hookQuick === 'all'}">All <span>${syncedPosts.length}</span></button>
    <button type="button" class="hook-filter ${state.hookQuick === 'winners' ? 'on' : ''}" data-hook-filter="winners" aria-pressed="${state.hookQuick === 'winners'}">Winners <span>${winnerCount}</span></button>
    <button type="button" class="hook-filter ${state.hookQuick === 'growing' ? 'on' : ''}" data-hook-filter="growing" aria-pressed="${state.hookQuick === 'growing'}">Still growing <span>${growingCount}</span></button>
    <button type="button" class="hook-filter ${state.hookQuick === 'ready' ? 'on' : ''}" data-hook-filter="ready" aria-pressed="${state.hookQuick === 'ready'}">Ready to post <span>${readyCount}</span></button>
  </div>
    ${posts.length ? `<div class="table-wrap hooks-table"><table><thead><tr><th>Date</th><th>Account</th><th>Hook</th><th>Status</th><th class="num">Views</th><th class="num" title="Views compared with this account’s usual post performance">vs usual</th><th>More metrics</th><th>Decision</th><th>Next step</th></tr></thead><tbody>
    ${posts.map(p => {
      const score = p.vsc == null ? '<span class="muted">Not scored</span>' : `<b class="hook-score">${p.vsc}/30</b>${p.score_by === 'auto' ? ' <span class="muted small" title="Calculated from real post results against this account’s normal">Auto</span>' : p.score_by === 'ai' ? ` <span class="muted small" title="${p.score_blind === true ? 'AI score based on wording only' : p.score_blind === false ? 'AI score informed by post results' : 'AI-generated score'}">AI${p.score_blind === false ? ' · results' : ' · wording'}</span>` : ''}${p.score_confidence != null ? ` <span class="muted small" title="Confidence">${Math.round(p.score_confidence * 100)}% confidence</span>` : ''}`;
      const searchText = [p.hook, p.ai_prompt, p.handle, p.decision].filter(Boolean).join(' ').toLowerCase();
      const detailItems = [
        hasLikes && `Likes: ${p.likes == null ? '—' : fmt(p.likes)}`,
        `Comments: ${fmt(p.comments)}`,
        hasSS && `Saves: ${p.saves == null ? '—' : fmt(p.saves)}`,
        hasSS && `Shares: ${p.shares == null ? '—' : fmt(p.shares)}`,
        `Intent / 1k: ${p.perf && p.perf.intent_per_1k != null ? p.perf.intent_per_1k : '—'}`,
        `Asking comments: ${p.perf && p.perf.comments_read ? `${p.perf.intent_comments} / ${p.perf.comments_read} read` : 'Not read'}`,
        `Purchase intent: ${p.intent ? 'Detected' : 'None recorded'}`,
      ].filter(Boolean);
      return `<tr class="band" style="--band:${esc(p.phone_color || 'transparent')};cursor:pointer" data-id="${p.id}" data-search="${esc(searchText)}">
      <td data-label="Date">${esc(p.post_date)}</td><td data-label="Account" class="handle">${esc(p.handle || '—')}</td>
      <td data-label="Hook" class="wrap hook-copy">${contentTypeBadge(p.content_type)} ${p.variant_label ? `<span class="badge" title="A/B variant">${esc(p.variant_label)}</span> ` : ''}${esc(p.hook || '—')}${p.made_from ? `<div class="small" style="color:var(--accent)" title="A video you made from another hook">↻ made from “${esc(short(p.made_from.hook || '', 40))}” · generation ${p.made_from.generation}</div>` : ''}<div class="muted small">${esc(short(p.ai_prompt, 60))}</div></td>
      <td data-label="Status">${pill(p.status)}</td><td data-label="Views" class="num">${viewsCell(p)}</td>
      <td data-label="vs usual" class="num">${winnerChip(p.perf, p)}</td>
      <td data-label="Metrics"><details class="hook-metrics"><summary>View metrics</summary><div>${detailItems.map(item => `<span>${item}</span>`).join('')}</div></details></td>
      <td data-label="Decision" class="hook-decision-cell" title="${esc(p.decision_basis || '')}"><div>${pill(p.decision)}</div><div class="hook-scoreline">${score}</div>${p.decision ? `<details class="hook-why"><summary>Why this decision?</summary><span>${esc(p.decision_basis || decisionSource(p) || 'Based on this post’s performance and scoring rules.')}</span></details>` : ''}</td>
      <td data-label="Next step" class="hook-next-cell">${postCell(p)}</td></tr>`;
    }).join('')}</tbody></table></div><div class="empty card hooks-search-empty" id="hooks-search-empty" hidden><h2>No matching hooks</h2><p>Try a different search or quick filter.</p></div>`
      : `<div class="empty card"><h2>${state.hookSearch ? 'No matching hooks' : state.hookQuick !== 'all' ? `No ${quickLabel} in this view` : 'No synced posts in this view'}</h2><p>${state.hookSearch ? 'Try a different search or clear the search field.' : state.hookQuick !== 'all' ? 'Try another quick filter or adjust the account and status filters.' : 'Hooks fills itself from your accounts. Add an account and sync it to start reviewing performance.'}</p>
      <div class="row" style="justify-content:center;gap:8px;margin-top:12px">
        ${state.hookQuick !== 'all' || state.hookStatus || state.hookAccount || state.hookSearch ? '<button class="btn" id="clear-hook-filters">Clear filters</button>' : '<a class="btn primary" href="#/accounts">Manage accounts</a>'}
      </div></div>`}</div>`;
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
  const clearHookFilters = $('#clear-hook-filters', app);
  if (clearHookFilters) clearHookFilters.onclick = () => { state.hookStatus = ''; state.hookAccount = ''; state.hookQuick = 'all'; state.hookSearch = ''; route(); };
  $$('[data-hot]', app).forEach(b => (b.onclick = () => postModal(hot.find(h => h.id === +b.dataset.hot), null, route)));
  const hookSearch = $('#hook-search', app);
  const filterHookRows = () => {
    const query = hookSearch.value.trim().toLowerCase();
    state.hookSearch = hookSearch.value;
    const rows = $$('tbody tr[data-id]', app);
    let visible = 0;
    rows.forEach(row => {
      const matches = !query || row.dataset.search.includes(query);
      row.style.display = matches ? '' : 'none';
      if (matches) visible++;
    });
    const count = $('#hooks-result-count', app);
    if (count) count.textContent = visible;
    const noMatches = $('#hooks-search-empty', app);
    if (noMatches) noMatches.hidden = visible > 0;
  };
  if (hookSearch) { hookSearch.oninput = filterHookRows; filterHookRows(); }
  $$('[data-hook-filter]', app).forEach(button => button.onclick = () => { state.hookQuick = button.dataset.hookFilter; route(); });
  $$('[data-post-hook]', app).forEach(b => (b.onclick = guard(async e => {
    e.stopPropagation();
    const h = posts.find(x => x.id === +b.dataset.postHook);
    if (h && h.made_from && !await askConfirm(`This post is itself a copy you made (generation ${h.made_from.generation}). Make another copy of it?`, {danger: false, ok: 'Make another copy'})) return;
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
    card.innerHTML = `<h3 style="margin:0 0 4px">Matching posts of winners</h3>
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
    if (e.target.closest('button, a, input, select, textarea, details, summary')) return;
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

function ideasModal(onDone, pre = {}) {
  const m = openModal(`<h2>✨ AI hook ideas</h2>${aiNotice()}<p class="muted small">Uses your best and worst posts plus saved ideas marked for AI as style references. Nothing is added until you press <b>Add to Posts</b>.</p>
    <div class="fields"><div><label class="f">For account</label><select id="acc">${accountOptions(pre.account || state.hookAccount, 'Choose…')}</select></div>
    <div><label class="f">How many</label><input type="number" id="n" value="5" min="1" max="12"></div>
    <div class="full"><label class="f">Direction (optional)</label><input id="brief" maxlength="500" value="${esc(pre.brief || '')}" placeholder="e.g. lean into long-distance anxiety, slideshow format"></div></div>
    <div id="out"></div><div class="actions"><button class="btn" data-close>Close</button><button class="btn ai" id="go" ${state.ai.available ? '' : 'disabled'}>Generate</button></div>`, true);
  $('#go', m).onclick = guard(async () => {
    const acc = $('#acc', m).value; if (!acc) throw new Error('Choose an account');
    const btn = $('#go', m); busy(btn, true, 'Writing…');
    let r; try { r = await api('POST', '/api/ai/hooks', { account_id: +acc, n: +$('#n', m).value, brief: $('#brief', m).value }); } finally { busy(btn, false); }
    $('#out', m).innerHTML = r.ideas.map((x, i) => `<div class="card" style="margin-top:10px"><b>${esc(x.hook)}</b>
      <div class="muted">${esc(x.body || '')}</div><div class="small muted">${esc(x.ai_prompt || '')}</div><div class="small">${esc(x.caption)}</div>
      <div class="row" style="margin-top:8px"><button class="btn sm primary" data-add="${i}">Add to Posts as an Idea</button></div></div>`).join('');
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
// Plain-language lines for what a plan's limits mean, so "3 accounts" is never ambiguous.
const trackLine = n => (n === -1 ? 'Track unlimited accounts: any mix of TikTok, Instagram, YouTube, X, LinkedIn and Facebook' : `Track up to ${n} account${n === 1 ? '' : 's'}: any mix of TikTok, Instagram, YouTube, X, LinkedIn and Facebook`);
const publishLine = n => (n === -1 ? 'Publish to unlimited accounts' : n === 0 ? 'No publishing accounts' : `Publish to ${n} account${n === 1 ? '' : 's'} (any platform)`);

// The one-time lifetime deal. `attr` is the button attribute the page's click handler listens for (data-buy or data-pick).
function lifetimeCard(b, attr) {
  const o = b.lifetime_offer;
  if (!o) return '';
  const money = (minor, cur) => { try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(minor / 100); } catch { return `${minor / 100} ${cur}`; } };
  const inc = o.includes || {};
  const local = o.charge_currency && o.charge_currency !== o.currency ? `<div class="muted small" style="margin-top:-2px">charged as about ${money(o.charge_minor, o.charge_currency)} at today's rate</div>` : '';
  return `<div class="card" style="border-color:var(--accent);margin:0 0 14px">
    <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center"><h2 style="margin:0">Lifetime deal</h2>
      <span class="pill ${o.sold_out ? '' : 'SCALE'}">${o.sold_out ? 'sold out' : `${o.left} of ${o.cap} spots left`}</span></div>
    <div style="font-size:26px;font-weight:700;margin:6px 0">${money(o.price_minor, o.currency)} <span class="muted small" style="font-weight:400">one time</span></div>${local}
    <ul style="padding-left:18px;margin:8px 0;font-size:14px">
      <li>Track ${inc.other_accounts} TikTok, Instagram, X, LinkedIn or Facebook account</li><li>Track up to ${inc.youtube_accounts} YouTube channels</li>
      <li>${publishLine(inc.connected)}</li><li>Discover, AI help and all the free tools</li>
      <li>About ${inc.credits} data credits a month, and no limit on AI</li></ul>
    <button type="button" class="btn primary" ${attr}="lifetime" ${b.can_manage && b.payments_ready && !o.sold_out ? '' : 'disabled'}>${o.sold_out ? 'Sold out' : 'Get the lifetime deal'}</button>
    <div class="muted small" style="margin-top:6px">Pay once and keep it for as long as we run HookTrack. 60-day money-back guarantee. Need more accounts or another publishing account? They are add-ons.${b.can_manage ? '' : ' Only a workspace owner or admin can buy.'}</div></div>`;
}

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
  let b = await api('GET', '/api/billing');
  if (!b.enabled) return;
  const hasYear = b.plans.some(p => p.interval === 'year'), hasMonth = b.plans.some(p => p.interval !== 'year');
  let iv = hasYear && hasMonth ? (state.billingInterval || (b.plan && b.plan.interval === 'year' ? 'year' : 'month')) : (hasYear ? 'year' : 'month');
  let saveCard = b.card.auto_renew || !b.card.has_card;
  const lim = n => (n === -1 ? 'Unlimited' : Number(n).toLocaleString());
  const why = opts.reason || (b.state === 'expired'
    ? (b.subscription.lapsed ? 'Your plan has ended.' : 'Choose a plan to get started.')
    : b.state === 'free' ? 'You are on the free plan: YouTube tracking and decisions. A paid plan adds TikTok and Instagram, publishing, add-ons and more room.'
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
      ${lifetimeCard(b, 'data-pick')}
      ${hasYear && hasMonth ? `<div class="row" style="gap:6px;margin:0 0 12px" role="group" aria-label="Billing period">
        <button type="button" class="btn sm ${iv === 'month' ? 'primary' : ''}" data-iv="month" aria-pressed="${iv === 'month'}">Monthly</button>
        <button type="button" class="btn sm ${iv === 'year' ? 'primary' : ''}" data-iv="year" aria-pressed="${iv === 'year'}">Yearly${bestSave ? ` · save up to ${bestSave}%` : ''}</button></div>` : ''}
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px">
      ${shown.map(t => { const p = t.plan; return `<div class="card" style="margin:0;${[t.monthly, t.yearly].some(x => x && x.code === b.plan.code) && b.state === 'paid' ? 'border-color:var(--accent)' : ''}">
        <h3 style="margin:0">${esc(planTierName(t))}</h3>
        ${priceBlock(p, b.fx_usd || {})}
        ${p.interval === 'year' && p.price_minor ? `<div class="muted small" style="margin-top:-2px">${fmtMoney(Math.round(p.price_minor / 12), p.currency)} / month${p.charge_currency && p.charge_currency !== p.currency ? ` (≈ ${fmtMoney(Math.round(p.charge_minor / 12), p.charge_currency)})` : ''}, billed yearly${t.save ? ` · <b>save ${t.save}%</b>` : ''}</div>` : ''}
        <ul style="padding-left:18px;margin:8px 0;font-size:13px"><li>${trackLine(p.max_accounts)}</li><li>${publishLine(p.max_connected)}</li><li>${lim(p.max_members)} team member${p.max_members === 1 ? '' : 's'}</li><li>${lim(p.max_ai_calls)} AI requests / month</li></ul>
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
  let b = await api('GET', '/api/billing');
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
    ${b.state === 'expired' ? `<div class="notice" style="margin-bottom:12px;border-color:var(--bad)"><b>${sub.lapsed ? 'Your paid period has ended.' : 'Choose a plan to get started.'}</b> You can still look around, but nothing can be changed until you choose a plan below. Your data is safe.</div>` : ''}
    <div class="card"><h2 style="margin:0">${b.state === 'trial' ? `Free trial: ${b.trial.days_left} day${b.trial.days_left === 1 ? '' : 's'} left` : b.state === 'free' ? 'Free plan: YouTube tracking' : b.state === 'expired' ? 'No active plan' : 'Current plan: ' + esc(b.plan.name)} ${sub.status === 'comp' ? '<span class="badge">complimentary</span>' : ''}</h2>
      ${b.state === 'trial' ? `<div class="muted small">Ends ${new Date(b.trial.ends_at).toLocaleDateString()}. Pick a plan any time: you keep everything you have set up.</div>` : ''}
      ${b.state === 'free' ? `<div class="muted small">Track up to ${b.limits.accounts} YouTube channel${b.limits.accounts === 1 ? '' : 's'} and see what to scale, tweak or stop. TikTok and Instagram tracking, publishing and add-ons need a paid plan, and nothing you have saved is deleted if you upgrade.</div>` : ''}
      <div class="muted small">${b.state === 'trial' || b.state === 'expired' ? '' : (sub.status === 'comp' ? (sub.current_period_end ? `Complimentary until ${new Date(sub.current_period_end).toLocaleDateString()}.` : 'Complimentary plan, no end date.') : sub.current_period_end && b.plan.price_minor ? `Paid until ${new Date(sub.current_period_end).toLocaleDateString()}. ${b.card && b.card.auto_renew ? 'It renews automatically with your saved card.' : 'Automatic renewal is off: renew before it ends to keep your plan.'}` : (sub.current_period_end ? `Until ${new Date(sub.current_period_end).toLocaleDateString()}` : ''))}</div>
      ${meter('Accounts', b.usage.accounts, b.limits.accounts)}${meter('Team members', b.usage.members, b.limits.members)}${meter('AI requests this month', b.usage.ai_calls, b.limits.ai_calls)}</div>
    ${cardPanel(b)}
    ${b.state !== 'expired' && b.state !== 'free' && b.addons ? `<h2 style="margin:20px 0 8px">Add-ons</h2>
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
    ${b.has_lifetime ? `<div class="notice" style="margin:20px 0 8px"><b>You have the lifetime plan.</b> You paid once and it does not renew. For more tracked accounts, another publishing account or more data credits, use the add-ons above.</div>` : `${lifetimeCard(b, 'data-buy')}
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
      <ul style="padding-left:18px;margin:8px 0"><li>${trackLine(p.max_accounts)}</li><li>${publishLine(p.max_connected)}</li><li>${lim(p.max_members)} team member${p.max_members === 1 ? '' : 's'}</li><li>${lim(p.max_ai_calls)} AI requests / month</li><li>${lim(p.max_credits)} data credits / month</li><li>${p.max_reports === 0 ? 'No shareable client reports' : lim(p.max_reports) + ' shareable client report' + (p.max_reports === 1 ? '' : 's')}</li></ul>
      ${p.code === b.plan.code && b.state !== 'trial' ? (p.price_minor && b.can_manage ? `<button class="btn" data-buy="${esc(p.code)}" ${b.payments_ready ? '' : 'disabled'}>Renew</button>` : '<span class="badge">Current</span>')
        : (p.price_minor ? `<button class="btn primary" data-buy="${esc(p.code)}" ${b.can_manage && b.payments_ready ? '' : 'disabled'} title="${b.can_manage ? (b.payments_ready ? '' : 'Payments are not set up yet') : 'Only workspace admins can change the plan'}">Choose ${esc(p.name)}</button>` : '')}
    </div>`; }).join('')}</div>`}
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
    if (btn.dataset.buy === 'lifetime' && b.state === 'paid' && b.plan && b.plan.code !== 'lifetime' && b.plan.price_minor
        && !await askConfirm(`Buying the lifetime deal replaces your ${b.plan.name} plan, and any time you have left on it is not refunded or carried over.\n\nContinue?`, { danger: false, ok: 'Buy lifetime' })) return;
    busy(btn, true, 'Opening checkout…');
    try {
      await payForPlan(btn.dataset.buy, { saveCard: btn.dataset.buy !== 'lifetime' && !!($('#savecard') && $('#savecard').checked), banner, alive: onBilling, onDone: rerender });
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
  const activeCount = state.accounts.filter(a => a.status === 'active').length;
  const syncingCount = state.accounts.filter(a => a.sync_enabled).length;
  const baselineCount = state.accounts.filter(a => !a.baseline_needed).length;
  const accountCard = a => {
    const client = state.clients.find(c => c.id === a.client_id);
    const search = [a.handle, a.platform, a.niche, client && client.name].filter(Boolean).join(' ').toLowerCase();
    const synced = a.last_synced_at ? String(a.last_synced_at).slice(0, 10) : '';
    const platform = String(a.platform || '').toLowerCase();
    return `<article class="account-row" data-account-search="${esc(search)}">
      <div class="account-brandmark ${esc(platform)}" aria-hidden="true">${window.socialPlatformIcon(platform) || esc(String(a.platform || '?').slice(0, 1).toUpperCase())}</div>
      <div class="account-identity">
        <div class="account-name-line">
          <button type="button" class="pin-btn ${pins.has(String(a.id)) ? 'on' : ''}" data-pin="${a.id}" title="${pins.has(String(a.id)) ? 'Unpin account' : 'Pin account'}" aria-label="${pins.has(String(a.id)) ? 'Unpin account' : 'Pin account'}">${pins.has(String(a.id)) ? '★' : '☆'}</button>
          <b>${esc(a.handle)}</b>${pill(a.status, a.status === 'active' ? 'posted' : a.status === 'killed' ? 'KILL' : 'HOLD')}
        </div>
        <div class="account-meta">${window.socialPlatformIdentity(platform, a.platform)} <span>·</span> ${esc(a.niche)}${client ? ` <span>·</span> ${esc(client.name)}` : ''}</div>
        <div class="account-hook">${a.content_hook ? esc(a.content_hook) : '<span class="muted">Add a content focus to help tailor your ideas</span>'}</div>
      </div>
      <div class="account-stat"><span>Followers</span><b title="${a.followers_day ? `As of ${esc(String(a.followers_day).slice(0, 10))}` : 'No snapshot yet: run a sync'}">${a.followers == null ? '—' : compact(a.followers)}</b>
        ${a.followers_change ? `<small class="${a.followers_change > 0 ? 'up' : 'down'}">${a.followers_change > 0 ? '+' : ''}${compact(a.followers_change)}</small>` : ''}</div>
      <div class="account-stat"><span>Posting pace</span><b>${a.times_per_day} <small>per day</small></b><small class="account-baseline" title="${a.baseline_needed > 0 ? `${a.judged_posts} of ${a.judged_posts + a.baseline_needed} judged posts to build its own baseline` : 'Using this account’s own history'}">${a.baseline_needed > 0 ? `${a.judged_posts}/${a.judged_posts + a.baseline_needed} to baseline` : 'Baseline ready'}</small></div>
      <div class="account-sync"><span class="sync-indicator ${a.sync_enabled ? 'on' : ''}"></span><span><b>${a.sync_enabled ? 'Auto-sync on' : 'Auto-sync off'}</b><small>${synced ? `Last sync ${esc(synced)}` : 'Not synced yet'}</small></span></div>
      <div class="account-actions"><button class="btn sm" data-sync="${a.id}" title="Pull posts and views from this account">Sync</button><button class="btn sm" data-ea="${a.id}">Edit</button></div>
    </article>`;
  };
  app.innerHTML = `<div class="accounts-page">
    <div class="page-head accounts-head"><div><div class="eyebrow">WORKSPACE</div><h1>Accounts</h1>
      <div class="sub">Add the channels you publish on and keep their performance data in sync.</div></div>
      <div class="row account-primary-actions"><button class="btn" id="addphone">+ Phone</button><button class="btn primary" id="addacc">+ Add account</button></div></div>
    <div class="grid account-overview">
      <div class="card account-overview-item"><span class="account-overview-icon">◎</span><div><span class="account-overview-label">Tracked accounts</span><b>${state.accounts.length}</b></div></div>
      <div class="card account-overview-item"><span class="account-overview-icon active">✓</span><div><span class="account-overview-label">Active</span><b>${activeCount}<small> of ${state.accounts.length}</small></b></div></div>
      <div class="card account-overview-item"><span class="account-overview-icon sync">↻</span><div><span class="account-overview-label">Auto-sync enabled</span><b>${syncingCount}<small> of ${state.accounts.length}</small></b></div></div>
      <div class="card account-overview-item"><span class="account-overview-icon baseline">◌</span><div><span class="account-overview-label">Baseline ready</span><b>${baselineCount}<small> of ${state.accounts.length}</small></b></div></div>
    </div>
    <div class="account-toolbar">
      <label class="account-search"><span aria-hidden="true">⌕</span><input id="account-search" type="search" placeholder="Search handles, platforms, niches…" aria-label="Search accounts"></label>
      <div class="row account-sync-controls">
        <label class="small muted" for="syncdays">Sync range</label>
        <select id="syncdays" title="How far back a manual sync reads. Longer reads use more credits.">${[14, 30, 60, 90].map(d => `<option value="${d}" ${syncDays() === d ? 'selected' : ''}>Last ${d} days</option>`).join('')}</select>
        <button class="btn" id="syncall" title="Sync all accounts with Auto-sync on">↻ Sync all</button>
      </div>
    </div>
    ${groups.length ? groups.map(({ p, rows }) => `<section class="account-group" data-account-group>
      <div class="account-group-head"><div class="account-group-title"><span class="account-group-dot" style="--group-color:${esc(p.color)}"></span>
        <div><h2>${esc(p.label)}</h2><span class="muted small">${rows.length} account${rows.length === 1 ? '' : 's'}</span></div></div>
        ${p.id ? `<button class="btn sm" data-ep="${p.id}">Edit phone</button>` : ''}</div>
      ${rows.length ? `<div class="account-list">${rows.map(accountCard).join('')}</div>` : '<div class="account-group-empty">No accounts on this phone yet.</div>'}
    </section>`).join('') : `<div class="empty card account-empty"><h2>No accounts connected yet</h2><p>Add a phone and connect your first channel to start tracking performance.</p><div class="row" style="justify-content:center;margin-top:16px"><button class="btn" id="empty-addphone">+ Add phone</button><button class="btn primary" id="empty-addacc">+ Add account</button></div></div>`}
    <div id="account-no-results" class="empty card" hidden><h2>No matching accounts</h2><p>Try a different handle, platform, niche, or client name.</p></div>
    </div>`;

  $('#addphone').onclick = () => phoneModal();
  $('#addacc').onclick = () => accountModal();
  if ($('#empty-addphone', app)) $('#empty-addphone').onclick = () => phoneModal();
  if ($('#empty-addacc', app)) $('#empty-addacc').onclick = () => accountModal();
  $('#account-search', app).oninput = e => {
    const query = e.target.value.trim().toLowerCase();
    let visible = 0;
    $$('[data-account-group]', app).forEach(group => {
      const rows = $$('[data-account-search]', group);
      let groupVisible = 0;
      rows.forEach(row => {
        const matches = !query || row.dataset.accountSearch.includes(query);
        row.hidden = !matches;
        if (matches) groupVisible++;
      });
      group.hidden = !!query && groupVisible === 0;
      visible += groupVisible;
    });
    $('#account-no-results', app).hidden = visible > 0 || !query;
  };
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
      toast(`Synced @${r.handle || ''}: ${r.upserted} posts (~${r.credits_est || '?'} credits)${r.note ? ' — ' + r.note : ''}`);
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
};

pages.ads = async app => {
  const d = await api('GET', '/api/ads');
  let campaignReport = { campaigns: [] };
  let campaignReportError = '';
  if (d.configured) {
    try { campaignReport = await api('GET', '/api/ads/campaigns'); }
    catch (error) { campaignReportError = error.message; }
  }
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  const connectNotice = params.get('error_message') || params.get('error')
    ? `<div class="notice" style="margin-bottom:12px">${esc(params.get('error_message') || `Ads connection was not completed (${params.get('error')}).`)}</div>`
    : params.get('connected')
      ? '<div class="notice" style="margin-bottom:12px">Ads account connected. Choose an ad account below to build a campaign or boost a post.</div>'
      : '';
  const names = {
    metaads: 'Meta Ads', tiktokads: 'TikTok Ads', googleads: 'Google Ads', linkedinads: 'LinkedIn Ads',
    facebook: 'Facebook', instagram: 'Instagram', tiktok: 'TikTok', linkedin: 'LinkedIn',
  };
  const connectCard = platform => {
    const adsKey = `${platform}ads`;
    const connection = d.ads_connections.find(a => a.platform === adsKey);
    const parentPlatforms = { meta: ['facebook', 'instagram'], tiktok: ['tiktok'], linkedin: ['linkedin'], google: [] }[platform];
    const posting = d.posting_accounts.filter(a => parentPlatforms.includes(String(a.platform).toLowerCase()));
    const linkedPosting = connection && d.posting_accounts.find(a => a.id === connection.parent_account_id);
    const accountList = connection
      ? (connection.ad_accounts || []).map(a => `<span class="pill">${esc(a.name)}${a.currency ? ` · ${esc(a.currency)}` : ''} · ${a.selected ? 'selected' : 'not selected'}</span>`).join(' ')
        || '<span class="muted small">No usable ad accounts were returned. Check account access and selection in the ad platform.</span>'
      : '<span class="muted small">Not connected</span>';
    return `<div class="card ads-connection-card ads-connection-${platform}">
      <div class="row ads-connection-head"><span class="ads-platform-mark">${platform === 'google' ? 'G' : platform === 'linkedin' ? 'in' : platform === 'tiktok' ? '♪' : 'M'}</span><div class="ads-connection-title"><h2>${names[adsKey]}</h2>
        <p class="muted small" style="margin:5px 0 10px">${connection ? 'Connected to this workspace' : platform === 'google' ? 'Connect Google Ads directly; no Google publishing account is required.' : platform === 'linkedin' ? 'Connect the LinkedIn account that has access to the Company Page and ad account.' : platform === 'tiktok' ? 'Connect TikTok Ads and link the matching TikTok publishing account to enable Spark Ads.' : 'Connect an ad account you are authorized to manage.'}</p>
        ${connection && platform === 'tiktok' && linkedPosting ? `<p class="muted small">Linked TikTok identity: ${esc(linkedPosting.display_name || linkedPosting.username || 'TikTok account')}${linkedPosting.username ? ` · @${esc(linkedPosting.username.replace(/^@/, ''))}` : ''}</p>` : ''}
        </div>${connection ? '<span class="ads-connected-badge"><i></i> Connected</span>' : ''}</div>
        <div class="ads-connection-content">${connection ? accountList : ''}${connection && connection.error ? `<p class="small" style="color:var(--bad)">${esc(connection.error)}</p>` : ''}
        ${!connection && d.can_manage && d.configured ? platform === 'google'
          ? `<button class="btn primary" type="button" data-ads-connect="${platform}">Connect ${names[adsKey]}</button>`
          : posting.length || platform === 'meta'
          ? `${platform === 'meta' ? `<label class="small muted" for="ads-meta-login-mode">Meta Ads login mode</label>
             <select id="ads-meta-login-mode" style="width:100%;margin:4px 0 8px">
               <option value="classic" ${posting.length ? 'selected' : ''}>Classic — use a connected Facebook or Instagram account</option>
               <option value="business" ${posting.length ? '' : 'selected'}>Facebook Login for Business — connect Meta Ads directly</option>
             </select>` : ''}
             ${posting.length ? `<div id="ads-parent-wrap-${platform}" style="flex:0 0 100%">
               <label class="small muted" for="ads-parent-${platform}">Linked publishing account</label>
               <select id="ads-parent-${platform}" style="width:100%;margin:4px 0 8px">${posting.map(a =>
                 `<option value="${esc(a.id)}">${esc(a.display_name || a.username || a.platform)}${a.username ? ` · @${esc(a.username.replace(/^@/, ''))}` : ''}</option>`).join('')}</select>
             </div>` : ''}
             <button class="btn primary" type="button" data-ads-connect="${platform}">Connect ${names[adsKey]}</button>`
          : `<p class="small muted">Connect a ${platform === 'tiktok' ? 'TikTok' : platform === 'linkedin' ? 'LinkedIn' : 'Facebook or Instagram'} publishing account first in Settings → Publishing.</p>`
          : ''}</div>
    </div>`;
  };
  const metaConnection = d.ads_connections.find(item => item.platform === 'metaads');
  const tiktokConnection = d.ads_connections.find(item => item.platform === 'tiktokads');
  const googleConnection = d.ads_connections.find(item => item.platform === 'googleads');
  const linkedinConnection = d.ads_connections.find(item => item.platform === 'linkedinads');
  const metaAdAccounts = (metaConnection && metaConnection.ad_accounts || []).filter(item => item.selected);
  const tiktokAdAccounts = (tiktokConnection && tiktokConnection.ad_accounts || []).filter(item => item.selected);
  const googleAdAccounts = (googleConnection && googleConnection.ad_accounts || []).filter(item => item.selected);
  const linkedinAdAccounts = (linkedinConnection && linkedinConnection.ad_accounts || []).filter(item => item.selected);
  const activeCampaignCount = (d.campaign_drafts || []).filter(item => item.status === 'active').length;
  const campaignConnections = {
    meta: metaConnection, tiktok: tiktokConnection, google: googleConnection, linkedin: linkedinConnection,
  };
  const campaignAccountLists = {
    meta: metaAdAccounts, tiktok: tiktokAdAccounts, google: googleAdAccounts, linkedin: linkedinAdAccounts,
  };
  const builderPlatform = state.campaignPlatform || (
    metaConnection ? 'meta' : tiktokConnection ? 'tiktok' : googleConnection ? 'google' : linkedinConnection ? 'linkedin' : 'meta');
  const builderConnection = campaignConnections[builderPlatform];
  const builderAdAccounts = campaignAccountLists[builderPlatform];
  const campaignMediaOptions = [
    ...(d.existing_media || []).map(item => ({
      value: `post:${item.post_id}`, label: `${item.creative_type} · ${item.label} · ${item.handle || item.platform}`,
      type: item.creative_type,
      platform: String(item.platform).toLowerCase() === 'tiktok' ? 'tiktok'
        : String(item.platform).toLowerCase() === 'linkedin' ? 'linkedin' : 'meta',
    })),
    ...(d.campaign_assets || []).map(item => ({
      value: `asset:${item.id}`, label: `${String(item.content_type).startsWith('video/') ? 'video' : 'image'} · ${item.file_name}`,
      type: String(item.content_type).startsWith('video/') ? 'video' : 'image',
      platform: 'both',
    })),
  ];
  const campaignDraftCards = (d.campaign_drafts || []).map(draft => {
    const form = draft.draft_json || {};
    const reported = (campaignReport.campaigns || []).find(item =>
      String(item.platformCampaignId) === String(draft.platform_campaign_id));
    const metrics = reported && reported.metrics || {};
    const action = draft.status === 'draft' || draft.status === 'failed'
      ? `${d.can_manage ? `<button class="btn sm" type="button" data-campaign-edit="${draft.id}">Edit</button>
         <button class="btn sm" type="button" data-campaign-delete="${draft.id}">Delete</button>
         <button class="btn primary sm" type="button" data-campaign-create="${draft.id}">Create paused</button>` : ''}`
      : draft.status === 'unknown'
          ? `${d.can_manage && draft.retry_safe ? `<button class="btn sm" type="button" data-campaign-retry="${draft.id}">Retry create safely</button>` : ''}`
        : draft.pending_status
          ? `${d.can_manage ? `<button class="btn sm" type="button" data-campaign-status="${draft.id}" data-status="${draft.pending_status}">Retry ${draft.pending_status}</button>` : ''}`
          : d.can_manage
            ? `<button class="btn sm" type="button" data-campaign-status="${draft.id}" data-status="${draft.status === 'active' ? 'paused' : 'active'}">${draft.status === 'active' ? 'Pause' : 'Launch'}</button>`
            : '';
    return `<article class="card ads-campaign-card">
      <div class="row" style="align-items:flex-start"><b style="flex:1">${esc(draft.campaign_name)}</b><span class="pill">${esc(draft.status.replace('_', ' '))}</span></div>
      <p class="muted small">${esc(names[`${draft.platform}ads`] || 'Meta Ads')} · ${esc(form.objective || form.google_campaign_type || '')} · ${esc(String(form.daily_budget || ''))}/day · ends ${esc(String(form.end_date || ''))} · ${esc(form.creative_type || '')}</p>
      <p class="small">${form.headline ? `<b>${esc(form.headline)}</b><br>` : ''}${esc(short(form.body || '', 180))}<br><span class="muted">${esc(form.link_url || '')}</span></p>
      ${draft.platform_campaign_id ? `<p class="small">Spend ${fmt(metrics.spend || 0)} · ${fmt(metrics.impressions || 0)} impressions · ${fmt(metrics.clicks || 0)} clicks${reported && reported.currency ? ` · ${esc(reported.currency)}` : ''}${reported ? ` · ${esc(names[`${draft.platform}ads`] || 'Meta Ads')} status: ${esc(reported.status || 'unknown')}` : ''}</p>` : ''}
      ${draft.status === 'unknown' && !draft.retry_safe ? `<p class="small" style="color:var(--bad)">Automatic retry window expired. Check ${esc(names[`${draft.platform}ads`] || 'Meta Ads')} before taking action.</p>` : ''}
      ${draft.error ? `<p class="small" style="color:var(--bad)">${esc(draft.error)}</p>` : ''}
      <div class="actions">${action}</div>
    </article>`;
  }).join('');
  const campaignBuilder = `<section class="card ads-builder" id="campaign-builder">
    <div class="ads-section-heading"><div><span class="ads-kicker">CAMPAIGN WORKSPACE</span><h2>Build a campaign</h2>
      <p class="small muted">Set up your audience and creative, save a draft, then review before launch.</p></div>
      <span class="ads-safety-note"><i></i> Campaigns start paused</span></div>
    <div style="max-width:320px;margin:10px 0"><label class="small muted" for="camp-platform">Ad platform</label>
      <select id="camp-platform" style="width:100%"><option value="meta" ${builderPlatform === 'meta' ? 'selected' : ''}>Meta</option><option value="tiktok" ${builderPlatform === 'tiktok' ? 'selected' : ''}>TikTok</option><option value="google" ${builderPlatform === 'google' ? 'selected' : ''}>Google Ads (Search + YouTube)</option><option value="linkedin" ${builderPlatform === 'linkedin' ? 'selected' : ''}>LinkedIn</option></select></div>
    <p id="camp-connection-notice" class="notice" ${builderConnection ? 'hidden' : ''}>Connect ${names[`${builderPlatform}ads`] || 'Meta Ads'} above and select an available ad account to start a draft.</p>
    <p id="camp-connection-error" class="notice" style="color:var(--bad)" ${builderConnection?.error ? '' : 'hidden'}>${esc(builderConnection?.error || '')}</p>
    <p id="camp-platform-note" class="small muted"></p>
    <form id="campaign-builder-form">
      <div class="grid ads-form-grid" style="gap:10px">
        <div class="ads-form-divider"><span>Campaign setup</span></div>
        <div><label class="small muted" for="camp-account"><span id="camp-account-label">${names[`${builderPlatform}ads`] || 'Meta Ads'} ad account</span></label>
          <select id="camp-account" required style="width:100%">${builderAdAccounts.map(item =>
            `<option value="${esc(item.id)}">${esc(item.name)}${item.currency ? ` · ${esc(item.currency)}` : ''}</option>`).join('')}</select></div>
        <div><label class="small muted" for="camp-name">Campaign name</label><input id="camp-name" maxlength="100" required style="width:100%"></div>
        <div id="camp-google-type-wrap" ${builderPlatform === 'google' ? '' : 'hidden'}><label class="small muted" for="camp-google-type">Google campaign type</label>
          <select id="camp-google-type" style="width:100%"><option value="search">Search campaign</option><option value="demand_gen">YouTube video (Demand Gen)</option></select></div>
        <div id="camp-objective-wrap"><label class="small muted" for="camp-objective">Objective</label>
          <select id="camp-objective" style="width:100%">
            ${[
              ['traffic', 'Traffic'], ['awareness', 'Awareness'], ['engagement', 'Engagement'],
              ['video_views', 'Video views'], ['conversions', 'Sales conversions'],
              ['lead_generation', 'Lead generation'], ['lead_conversion', 'Lead conversions'],
              ['app_promotion', 'App promotion'], ['catalog_sales', 'Catalog sales'],
              ['page_likes', 'Page likes'], ['page_visits', 'Page visits'],
            ].map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}
          </select></div>
        <div id="camp-meta-objective-fields" hidden>
          <div data-meta-objective="lead_generation">
            <label class="small muted" for="camp-meta-form">Instant form ID</label><input id="camp-meta-form" maxlength="100" style="width:100%">
            <label class="small muted" for="camp-meta-page">Facebook Page ID (optional if Zernio can infer it)</label><input id="camp-meta-page" maxlength="100" style="width:100%">
          </div>
          <div data-meta-objective="lead_conversion conversions catalog_sales">
            <label class="small muted" for="camp-meta-custom-conversion">Custom Conversion ID (optional)</label><input id="camp-meta-custom-conversion" maxlength="100" style="width:100%">
            <label class="small muted" for="camp-meta-custom-event">Custom event name (only when event is OTHER)</label><input id="camp-meta-custom-event" maxlength="100" style="width:100%">
          </div>
          <div data-meta-objective="app_promotion">
            <label class="small muted" for="camp-meta-app">Meta application ID</label><input id="camp-meta-app" maxlength="100" style="width:100%">
            <label class="small muted" for="camp-meta-store-url">App Store URL (HTTPS)</label><input id="camp-meta-store-url" type="url" maxlength="2048" style="width:100%">
          </div>
          <div data-meta-objective="catalog_sales">
            <label class="small muted" for="camp-meta-catalog">Product catalog ID (optional)</label><input id="camp-meta-catalog" maxlength="100" style="width:100%">
            <label class="small muted" for="camp-meta-product-set">Product set ID</label><input id="camp-meta-product-set" maxlength="100" style="width:100%">
          </div>
          <div data-meta-objective="conversions lead_conversion catalog_sales">
            <label class="small muted" for="camp-meta-offline-dataset">Offline conversion dataset ID (optional)</label><input id="camp-meta-offline-dataset" maxlength="100" style="width:100%">
          </div>
        </div>
        <div><label class="small muted" for="camp-budget"><span id="camp-budget-label">Daily budget</span> (<span id="camp-currency">${esc(builderAdAccounts[0]?.currency || 'account currency')}</span>)</label>
          <input id="camp-budget" type="number" min="1" step="0.01" required style="width:100%"></div>
        <div><label class="small muted" for="camp-end-date">Campaign end date</label>
          <input id="camp-end-date" type="date" required style="width:100%"></div>
        <div class="ads-form-divider"><span>Creative and destination</span></div>
        <div id="camp-media-wrap" ${['google'].includes(builderPlatform) ? 'hidden' : ''}><label class="small muted" for="camp-media">Creative media</label>
          <select id="camp-media" required style="width:100%">${campaignMediaOptions.filter(item =>
            (item.platform === 'both' || item.platform === builderPlatform) &&
            (builderPlatform !== 'tiktok' || item.type === 'video')).map(item =>
            `<option value="${esc(item.value)}" data-kind="${item.type}" ${state.campaignAssetId && item.value === `asset:${state.campaignAssetId}` ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select>
          <label class="small muted" for="camp-upload">Or upload creative</label>
          <input id="camp-upload" type="file" accept="image/jpeg,image/png,image/gif,video/mp4,video/quicktime" style="width:100%">
          <span id="camp-media-note" class="small muted">${!campaignMediaOptions.some(item =>
            (item.platform === 'both' || item.platform === builderPlatform) && (builderPlatform !== 'tiktok' || item.type === 'video'))
            ? 'Upload a creative to begin. Existing published post media appears here when available.' : ''}</span></div>
        <div id="camp-headline-wrap" ${builderPlatform === 'tiktok' ? 'hidden' : ''}><label class="small muted" for="camp-headline">Headline</label><input id="camp-headline" maxlength="255" ${builderPlatform === 'tiktok' ? '' : 'required'} style="width:100%"></div>
        <div><label class="small muted" for="camp-body"><span id="camp-body-label">${builderPlatform === 'tiktok' ? 'Video caption' : 'Primary text'}</span></label><textarea id="camp-body" maxlength="5000" required style="width:100%;min-height:84px"></textarea></div>
        <div><label class="small muted" for="camp-link">Landing page URL (HTTPS)</label><input id="camp-link" type="url" required placeholder="https://example.com" style="width:100%"></div>
        <div id="camp-google-search-fields" hidden>
          <label class="small muted" for="camp-google-keywords">Search keywords (one per line)</label><textarea id="camp-google-keywords" style="width:100%;min-height:70px" placeholder="project management software"></textarea>
          <label class="small muted" for="camp-google-headlines">Additional headlines (one per line; at least two)</label><textarea id="camp-google-headlines" style="width:100%;min-height:70px" placeholder="Plan projects together&#10;Try it free today"></textarea>
          <label class="small muted" for="camp-google-description">Second description (90 characters maximum)</label><input id="camp-google-description" maxlength="90" style="width:100%">
        </div>
        <div id="camp-google-video-fields" hidden>
          <div><label class="small muted" for="camp-google-business">Business name (25 characters maximum)</label><input id="camp-google-business" maxlength="25" style="width:100%"></div>
          <div><label class="small muted" for="camp-google-long-headline">Long headline (90 characters maximum)</label><input id="camp-google-long-headline" maxlength="90" style="width:100%"></div>
          <div><label class="small muted" for="camp-youtube-video">Public YouTube video ID</label><input id="camp-youtube-video" maxlength="20" placeholder="11-character video ID" style="width:100%"></div>
          <div><label class="small muted" for="camp-brand-logo">Uploaded square logo (JPEG or PNG)</label>
            <select id="camp-brand-logo" style="width:100%"><option value="">Choose an uploaded image logo</option>${(d.campaign_assets || []).filter(item =>
              String(item.content_type).startsWith('image/')).map(item =>
              `<option value="${item.id}" ${String(state.campaignLogoAssetId || '') === String(item.id) ? 'selected' : ''}>${esc(item.file_name)}</option>`).join('')}</select>
            <label class="small muted" for="camp-logo-upload">Or upload a logo</label>
            <input id="camp-logo-upload" type="file" accept="image/jpeg,image/png" style="width:100%"></div>
        </div>
        <div id="camp-linkedin-fields" hidden><label class="small muted" for="camp-linkedin-org">LinkedIn Company Page ID (optional if Zernio can infer it)</label>
          <input id="camp-linkedin-org" maxlength="100" placeholder="Numeric ID or urn:li:organization:123" style="width:100%"></div>
        <div id="camp-cta-wrap"><label class="small muted" for="camp-cta">Call to action</label>
          <select id="camp-cta" style="width:100%">${[
            ['LEARN_MORE', 'Learn more'], ['SHOP_NOW', 'Shop now'], ['SIGN_UP', 'Sign up'], ['CONTACT_US', 'Contact us'],
            ['DOWNLOAD', 'Download'], ['GET_OFFER', 'Get offer'], ['GET_QUOTE', 'Get quote'], ['SUBSCRIBE', 'Subscribe'],
            ['WATCH_MORE', 'Watch more'], ['ADD_TO_CART', 'Add to cart'], ['APPLY_NOW', 'Apply now'], ['BOOK_NOW', 'Book now'],
            ['BUY_TICKETS', 'Buy tickets'], ['DONATE', 'Donate'], ['LISTEN_NOW', 'Listen now'], ['ORDER_NOW', 'Order now'],
            ['REQUEST_TIME', 'Request time'], ['SEE_MENU', 'See menu'], ['START_ORDER', 'Start order'], ['BUY_NOW', 'Buy now'],
          ].map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select></div>
        <div><label class="small muted" for="camp-countries">Countries (ISO codes, comma-separated)</label><input id="camp-countries" value="US" placeholder="US, CA" style="width:100%"></div>
        <div class="row" style="gap:8px"><div><label class="small muted" for="camp-age-min">Minimum age</label><input id="camp-age-min" type="number" min="18" max="65" value="18" style="width:100%"></div>
          <div><label class="small muted" for="camp-age-max">Maximum age</label><input id="camp-age-max" type="number" min="18" max="65" value="65" style="width:100%"></div>
          <div><label class="small muted" for="camp-gender">Gender</label><select id="camp-gender"><option value="all">All</option><option value="female">Women</option><option value="male">Men</option></select></div></div>
      </div>
      <details style="margin:10px 0"><summary>Audience, placements and tracking</summary>
        <div style="padding:10px 0">
          <div id="camp-interest-wrap">
            <label class="small muted" for="camp-interest-search">Interest targeting (search ${builderPlatform === 'tiktok' ? 'TikTok' : 'Meta'})</label>
            <div class="row"><input id="camp-interest-search" placeholder="Search interests" style="flex:1"><button class="btn sm" type="button" id="camp-interest-button">Search</button></div>
            <div id="camp-interest-results" class="small" style="margin:6px 0"></div><div id="camp-interests" class="small muted">No interests selected.</div>
          </div>
          <div id="camp-placement-wrap" ${builderPlatform === 'tiktok' ? 'hidden' : ''}>
            <p class="small muted">Leave placements unchecked for Meta automatic placements; choose one or more platforms for manual placement control.</p>
            <div class="row" style="gap:12px;flex-wrap:wrap">${[['facebook','Facebook'],['instagram','Instagram'],['threads','Threads'],['messenger','Messenger']].map(([value,label]) =>
              `<label class="check"><input type="checkbox" name="camp-placement" value="${value}"> ${label}</label>`).join('')}</div>
          </div>
          ${builderPlatform === 'tiktok' ? '<p class="small muted">TikTok delivery uses TikTok placements automatically.</p>' : ''}
          <fieldset id="camp-category-wrap" style="margin:10px 0"><legend class="small muted">Special ad category (if applicable)</legend>
            ${[['HOUSING','Housing'],['EMPLOYMENT','Employment'],['CREDIT','Credit'],['FINANCIAL_PRODUCTS_SERVICES','Financial products/services'],['ISSUES_ELECTIONS_POLITICS','Issues/elections/politics'],['ONLINE_GAMBLING_AND_GAMING','Online gambling/gaming']].map(([value,label]) =>
              `<label class="check" data-camp-category-option="${value}"><input type="checkbox" name="camp-category" value="${value}"> ${label}</label>`).join('')}</fieldset>
          <div id="camp-pixel-wrap" class="row"><div style="flex:1"><label class="small muted" for="camp-pixel"><span id="camp-pixel-label">${builderPlatform === 'tiktok' ? 'TikTok' : 'Meta'} pixel</span></label><select id="camp-pixel" style="width:100%"><option value="">No pixel</option></select></div>
            <button class="btn sm" type="button" id="camp-load-pixels">Load pixels</button>
            <div style="flex:1"><label class="small muted" for="camp-event">Conversion event</label><select id="camp-event" style="width:100%"><optgroup id="camp-meta-events" label="Meta">${[
              'PURCHASE','COMPLETE_REGISTRATION','ADD_TO_CART','INITIATE_CHECKOUT','ADD_PAYMENT_INFO',
              'SUBSCRIBE','START_TRIAL','VIEW_CONTENT','SEARCH','LEAD','CONTACT','SUBMIT_APPLICATION','SCHEDULE','OTHER',
            ].map(event => `<option value="${event}">${event.replaceAll('_',' ')}</option>`).join('')}</optgroup><optgroup id="camp-tiktok-events" label="TikTok" ${builderPlatform === 'tiktok' ? '' : 'hidden'}>${[
              'ON_WEB_ORDER','INITIATE_ORDER','ON_WEB_CART','ON_WEB_REGISTER','ON_WEB_DETAIL',
              'FORM','ON_WEB_SEARCH','ON_WEB_ADD_TO_WISHLIST','LANDING_PAGE_VIEW',
            ].map(event => `<option value="${event}">${event.replaceAll('_',' ')}</option>`).join('')}</optgroup></select></div></div>
          <div id="camp-url-tags-wrap" ${builderPlatform === 'tiktok' ? 'hidden' : ''}>
            <label class="small muted" for="camp-url-tags">URL tracking tags (key=value&amp;key=value)</label>
            <input id="camp-url-tags" placeholder="utm_source=meta&amp;utm_medium=paid_social" style="width:100%">
          </div>
        </div>
      </details>
      <p id="camp-upload-note" class="small muted"></p>
      ${d.can_manage ? `<div class="actions"><button id="camp-save" class="btn primary" type="submit">Save campaign draft</button></div>` : '<p class="small muted">Ask a workspace admin to create and launch campaigns.</p>'}
    </form>
  </section>
  <section class="ads-section ads-campaigns"><div class="ads-section-heading"><div><span class="ads-kicker">CAMPAIGNS</span><h2>Campaign drafts and reports</h2></div><button class="btn sm" type="button" id="camp-report-refresh">Refresh reports</button></div>
    ${campaignReportError ? `<p class="notice" style="color:var(--bad)">${esc(campaignReportError)}</p>` : ''}
    ${(d.campaign_drafts || []).length ? `<div class="grid ads-campaign-grid">${campaignDraftCards}</div>` : '<div class="empty card"><p>No campaign drafts yet. Use the campaign workspace above to get started.</p></div>'}
    ${(campaignReport.campaigns || []).length ? `<details class="ads-report-details"><summary>Campaigns from connected ad accounts (${campaignReport.campaigns.length})</summary><div class="card"><ul class="list">${campaignReport.campaigns.map(campaign =>
      `<li><span style="flex:1"><b>${esc(campaign.campaignName || campaign.platformCampaignId || 'Campaign')}</b><span class="muted"> · ${esc(campaign.platform || '')} · ${esc(campaign.status || '')} · ${esc(campaign.currency || '')}</span><div class="small muted">Spend ${fmt(campaign.metrics?.spend || 0)} · ${fmt(campaign.metrics?.impressions || 0)} impressions · ${fmt(campaign.metrics?.clicks || 0)} clicks</div></span></li>`).join('')}</ul></div></details>` : ''}
  </section>`;
  const platformForPost = post => {
    const platform = String(post.platform || '').toLowerCase();
    return platform === 'tiktok' ? 'tiktok' : ['facebook', 'instagram'].includes(platform) ? 'meta' : '';
  };
  const cards = d.posts.map(post => {
    const platform = platformForPost(post);
    const connection = d.ads_connections.find(a => a.platform === (platform === 'meta' ? 'metaads' : 'tiktokads'));
    const existing = d.boosts.find(b => b.post_id === post.id && b.platform === platform
      && ['pending', 'created', 'unknown'].includes(b.status));
    const tiktokIdentityMatches = platform !== 'tiktok' || (post.tiktok_posting_account_id
      && (!connection?.parent_account_id || connection.parent_account_id === post.tiktok_posting_account_id));
    const canBoost = platform && connection && tiktokIdentityMatches && (connection.ad_accounts || []).some(a => a.selected)
      && d.can_manage && !existing;
    return `<article class="card ads-boost-card">
      <div class="row" style="gap:8px;align-items:flex-start"><span class="pill">${esc(post.platform || '')}</span><b style="flex:1">${esc(short(post.hook || post.caption || 'Published post', 100))}</b></div>
      <p class="muted small" style="margin:8px 0">${esc(post.handle || '')} · ${esc(String(post.post_date || '').slice(0, 10))} · ${fmt(post.views || 0)} views · ${fmt(post.comments || 0)} comments</p>
      ${existing ? `<p class="small">${existing.status === 'created' ? 'Boost submitted' : existing.status === 'unknown' ? 'Submission needs confirmation' : 'Submission in progress'}${existing.error ? ` · ${esc(existing.error)}` : ''}</p>
        ${existing.status === 'unknown' && d.can_manage ? `<button class="btn sm" data-boost-retry="${existing.id}">Retry safely</button>` : ''}`
        : canBoost ? `<button class="btn primary" type="button" data-boost-post="${post.id}" data-boost-platform="${platform}">Boost on ${platform === 'meta' ? 'Meta' : 'TikTok'}</button>`
          : `<span class="muted small">${!platform ? 'This network is not supported in this first release.' : !connection ? `Connect ${platform === 'meta' ? 'Meta' : 'TikTok'} Ads above to boost.` : !d.can_manage ? 'Ask a workspace admin to launch ads.' : !tiktokIdentityMatches ? 'This post belongs to a different TikTok identity than the one linked to TikTok Ads.' : 'No selected ad account is available.'}</span>`}
    </article>`;
  }).join('');
  const metaToday = new Date().toISOString().slice(0, 10);
  const metaMonthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const metaToolsPresets = [
    {label:'Create a campaign',method:'POST',path:'/ads/create',query:{},body:{name:'Spring campaign',goal:'traffic',budgetAmount:25,budgetType:'daily',status:'PAUSED',headline:'Headline',body:'Primary text',imageUrl:'https://example.com/image.jpg',linkUrl:'https://example.com',countries:['US']}},
    {label:'Read campaign tree',method:'GET',path:'/ads/tree',query:{fromDate:metaMonthAgo,toDate:metaToday},body:{}},
    {label:'Search targeting',method:'GET',path:'/ads/targeting/search',query:{q:'gardening',dimension:'interest'},body:{}},
    {label:'Create audience',method:'POST',path:'/ads/audiences',query:{},body:{type:'customer_list',name:'New customer list'}},
    {label:'List audiences',method:'GET',path:'/ads/audiences',query:{},body:{}},
    {label:'Create instant lead form',method:'POST',path:'/ads/lead-forms',query:{},body:{name:'Lead form',pageId:'',questions:[]}},
    {label:'List leads',method:'GET',path:'/ads/leads',query:{},body:{}},
    {label:'Create standalone creative',method:'POST',path:'/ads/creatives',query:{},body:{name:'Creative',headline:'Headline',body:'Primary text',imageUrl:'https://example.com/image.jpg',linkUrl:'https://example.com',callToAction:'LEARN_MORE'}},
    {label:'Read Meta insights',method:'GET',path:'/ads/insights',query:{fromDate:metaMonthAgo,toDate:metaToday},body:{}},
    {label:'Request Reach & Frequency prediction',method:'POST',path:'/ads/rf-predictions',query:{},body:{name:'Reach estimate',goal:'reach',budgetAmount:100,budgetType:'daily',countries:['US']}},
    {label:'Create click-to-message ad',method:'POST',path:'/ads/messaging',query:{},body:{destination:'messenger',goal:'engagement',budgetAmount:25,budgetType:'daily',status:'PAUSED'}},
    {label:'Create call ad',method:'POST',path:'/ads/call',query:{},body:{goal:'traffic',budgetAmount:25,budgetType:'daily',status:'PAUSED'}},
    {label:'List conversion destinations',method:'GET',path:'/ads/conversions/destinations',query:{},body:{}},
  ];
  const activeAdsTab = state.adsTab || 'campaigns';
  app.innerHTML = `<div class="ads-page">
    <div class="page-head ads-hero"><div><div class="eyebrow">PAID PROMOTION</div><h1>Advertising</h1>
    <div class="sub">Create and manage campaigns across Meta, TikTok, Google and LinkedIn.</div></div>
      <div class="ads-hero-actions"><button class="btn primary" type="button" data-ads-scroll="campaign-builder">Create campaign <span aria-hidden="true">↗</span></button><button class="btn" type="button" data-ads-scroll="ads-connections">Manage connections</button></div>
    </div>
    <div class="grid ads-overview" aria-label="Advertising overview">
      <article class="card ads-overview-item"><span class="ads-overview-icon">↗</span><div><span>Connected platforms</span><b>${d.ads_connections.length}<small> / 4</small></b></div></article>
      <article class="card ads-overview-item"><span class="ads-overview-icon drafts">▤</span><div><span>Campaign drafts</span><b>${(d.campaign_drafts || []).length}</b></div></article>
      <article class="card ads-overview-item"><span class="ads-overview-icon active">◉</span><div><span>Active campaigns</span><b>${activeCampaignCount}</b></div></article>
      <article class="card ads-overview-item"><span class="ads-overview-icon spend">#</span><div><span>Reported campaigns</span><b>${(campaignReport.campaigns || []).length}</b></div></article>
    </div>
    ${connectNotice}
    ${!d.configured ? '<div class="notice" style="margin-bottom:12px">Managed publishing and Ads are not configured on this server yet. Ask the platform admin to set up Zernio.</div>' : ''}
    <div class="ads-tabs" role="tablist" aria-label="Advertising tools">
      ${[['campaigns', 'Campaigns'], ['boosts', 'Boosts'], ['meta', 'Meta tools'], ['connections', 'Connections']].map(([tab, label]) =>
        `<button class="ads-tab${activeAdsTab === tab ? ' active' : ''}" type="button" role="tab"
          id="ads-tab-${tab}" aria-controls="ads-panel-${tab}" aria-selected="${activeAdsTab === tab}"
          data-ads-tab="${tab}">${label}</button>`).join('')}
    </div>
    <div id="ads-panel-connections" role="tabpanel" aria-labelledby="ads-tab-connections" ${activeAdsTab === 'connections' ? '' : 'hidden'}>
    <section id="ads-connections" class="ads-section"><div class="ads-section-heading"><div><span class="ads-kicker">ACCOUNTS</span><h2>Ad platform connections</h2><p class="small muted">Connect the accounts your workspace is authorized to manage.</p></div></div>
      <div class="grid ads-connection-grid">${connectCard('meta')}${connectCard('tiktok')}${connectCard('google')}${connectCard('linkedin')}</div>
    </section>
    </div>
    <div id="ads-panel-meta" role="tabpanel" aria-labelledby="ads-tab-meta" ${activeAdsTab === 'meta' ? '' : 'hidden'}>
      <section class="ads-section ads-meta-tools">
        <div class="ads-section-heading"><div><span class="ads-kicker">ZERNIO META ADS API</span><h2>Meta Ads tools</h2>
          <p class="small muted">Run Zernio’s documented Meta Ads operations for this workspace. See the <a href="https://docs.zernio.com/platforms/meta-ads" target="_blank" rel="noopener noreferrer">Zernio Meta Ads docs</a> for request fields and limits.</p></div></div>
        ${metaConnection && metaAdAccounts.length && d.can_manage ? `<form id="meta-tools-form" class="card">
          <p class="small muted">This advanced workspace covers campaign hierarchy, audience and pixel operations, creative tools, previews, lead forms, messaging/call ads, insights, conversion events and Reach &amp; Frequency. Write requests can create or change ads; verify the selected ad account and request before submitting.</p>
          <label class="small muted" for="meta-tools-preset">Start from an operation template</label>
          <select id="meta-tools-preset" style="width:100%;margin-bottom:10px"><option value="">Choose a template…</option>
            ${metaToolsPresets.map((preset, index) => `<option value="${index}">${esc(preset.label)}</option>`).join('')}</select>
          <div class="row"><div style="flex:1"><label class="small muted" for="meta-tools-account">Meta ad account</label>
            <select id="meta-tools-account" required style="width:100%">${metaAdAccounts.map(item =>
              `<option value="${esc(item.id)}">${esc(item.name)}${item.currency ? ` · ${esc(item.currency)}` : ''}</option>`).join('')}</select></div>
            <div style="flex:1"><label class="small muted" for="meta-tools-method">Method</label>
              <select id="meta-tools-method"><option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option></select></div></div>
          <label class="small muted" for="meta-tools-path">Zernio Ads API path</label>
          <input id="meta-tools-path" required maxlength="512" placeholder="/ads/audiences" style="width:100%">
          <label class="small muted" for="meta-tools-query">Query parameters (JSON object)</label>
          <textarea id="meta-tools-query" spellcheck="false" style="width:100%;min-height:64px;font-family:monospace">{}</textarea>
          <label class="small muted" for="meta-tools-body">Request body (JSON object)</label>
          <textarea id="meta-tools-body" spellcheck="false" style="width:100%;min-height:150px;font-family:monospace">{}</textarea>
          <div class="actions"><button id="meta-tools-run" class="btn primary" type="submit">Run Meta Ads request</button></div>
          <pre id="meta-tools-result" class="card small" style="display:none;white-space:pre-wrap;overflow:auto;max-height:420px"></pre>
        </form>` : `<div class="empty card"><p>${!d.can_manage ? 'Ask a workspace admin to manage Meta Ads.' : !metaConnection ? 'Connect Meta Ads to this workspace first.' : 'Select a Meta ad account in the provider before using Meta Ads tools.'}</p></div>`}
      </section>
    </div>
    <div id="ads-panel-campaigns" role="tabpanel" aria-labelledby="ads-tab-campaigns" ${activeAdsTab === 'campaigns' ? '' : 'hidden'}>
    ${campaignBuilder}
    </div>
    <div id="ads-panel-boosts" role="tabpanel" aria-labelledby="ads-tab-boosts" ${activeAdsTab === 'boosts' ? '' : 'hidden'}>
    <section class="ads-section"><div class="ads-section-heading"><div><span class="ads-kicker">PROMOTION</span><h2>Boost a published post</h2><p class="small muted">Promote a post that is already performing well on Facebook, Instagram or TikTok.</p></div><button class="btn sm" id="ads-refresh">Refresh</button></div>
    ${cards ? `<div class="grid ads-boost-grid">${cards}</div>` : '<div class="empty card"><h2>No boostable posts yet</h2><p>Sync a published TikTok, Instagram, or Facebook post first. The post must have its platform ID in Hooks.</p></div>'}
    </section>
    <section class="ads-section ads-history"><div class="ads-section-heading"><div><span class="ads-kicker">ACTIVITY</span><h2>Recent boosts</h2></div></div>
    ${d.boosts.length ? `<div class="card"><ul class="list">${d.boosts.map(b => `<li><span style="flex:1;min-width:0"><b>${esc(b.campaign_name)}</b>
      <span class="muted"> · ${esc(names[b.platform === 'meta' ? 'metaads' : 'tiktokads'])} · ${esc(String(b.daily_budget))}/day for ${b.duration_days} days · ${esc(b.status)}</span>
      ${b.error ? `<div class="small" style="color:var(--bad)">${esc(b.error)}</div>` : ''}</span>
      ${b.status === 'unknown' && d.can_manage ? `<button class="btn sm" data-boost-retry="${b.id}">Retry safely</button>` : ''}</li>`).join('')}</ul></div>`
      : '<div class="empty card"><p>No boost activity yet.</p></div>'}
    </section>
    </div>
  </div>`;

  $('#ads-refresh', app).onclick = () => route();
  if ($('#ads-meta-login-mode', app)) $('#ads-meta-login-mode', app).onchange = () => {
    const postingWrap = $('#ads-parent-wrap-meta', app);
    if (postingWrap) postingWrap.hidden = $('#ads-meta-login-mode', app).value === 'business';
  };
  if ($('#meta-tools-preset', app)) $('#meta-tools-preset', app).onchange = () => {
    const preset = metaToolsPresets[Number($('#meta-tools-preset', app).value)];
    if (!preset) return;
    $('#meta-tools-method', app).value = preset.method;
    $('#meta-tools-path', app).value = preset.path;
    $('#meta-tools-query', app).value = JSON.stringify(preset.query, null, 2);
    $('#meta-tools-body', app).value = JSON.stringify(preset.body, null, 2);
  };
  if ($('#meta-tools-form', app)) $('#meta-tools-form', app).onsubmit = guard(async event => {
    event.preventDefault();
    const method = $('#meta-tools-method', app).value;
    let query, body;
    try {
      query = JSON.parse($('#meta-tools-query', app).value || '{}');
      body = JSON.parse($('#meta-tools-body', app).value || '{}');
    } catch (error) {
      toast(`Request JSON is invalid: ${error.message}`, true);
      return;
    }
    if (!query || Array.isArray(query) || typeof query !== 'object'
      || !body || Array.isArray(body) || typeof body !== 'object') {
      toast('Query and request body must each be JSON objects.', true);
      return;
    }
    if (method !== 'GET' && !await askConfirm(
      'This request may create, update, delete, or spend against Meta ads. Continue only if you reviewed the selected account and JSON.',
      { title: 'Run Meta Ads write request', ok: 'Run request', danger: true },
    )) return;
    const button = $('#meta-tools-run', app);
    const result = $('#meta-tools-result', app);
    result.style.display = 'block';
    result.textContent = 'Request in progress…';
    busy(button, true, 'Running…');
    try {
      const response = await api('POST', '/api/ads/meta/request', {
        ad_connection_id: metaConnection.id,
        ad_account_id: $('#meta-tools-account', app).value,
        method,
        path: $('#meta-tools-path', app).value.trim(),
        query,
        body,
      });
      result.textContent = JSON.stringify(response, null, 2);
    } catch (error) {
      result.textContent = error.message;
      throw error;
    } finally { if (button.isConnected) busy(button, false); }
  });
  $$('[data-ads-tab]', app).forEach(button => button.onclick = () => {
    state.adsTab = button.dataset.adsTab;
    $$('[data-ads-tab]', app).forEach(tab => {
      const selected = tab === button;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', String(selected));
      $(`#ads-panel-${tab.dataset.adsTab}`, app).hidden = !selected;
    });
  });
  $$('[data-ads-scroll]', app).forEach(button => button.onclick = () => {
    const panel = button.dataset.adsScroll === 'ads-connections' ? 'connections' : 'campaigns';
    if (state.adsTab !== panel) {
      state.adsTab = panel;
      $$('[data-ads-tab]', app).find(tab => tab.dataset.adsTab === panel)?.click();
    }
    document.getElementById(button.dataset.adsScroll)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  const mediaSelect = $('#camp-media', app);
  const uploadInput = $('#camp-upload', app);
  const logoUploadInput = $('#camp-logo-upload', app);
  const uploadNote = $('#camp-upload-note', app);
  const interestList = $('#camp-interests', app);
  const renderSelectedInterests = () => {
    const selected = state.campaignInterests || [];
    interestList.innerHTML = selected.length
      ? selected.map(item => `<button class="pill" type="button" data-camp-interest-remove="${esc(item.id)}">${esc(item.name)} ×</button>`).join(' ')
      : 'No interests selected.';
  };
  const campaignConnectionFor = platform => campaignConnections[platform];
  const campaignAccountsFor = platform => campaignAccountLists[platform] || [];
  const renderCampaignMedia = (platform, selectedValue = mediaSelect.value) => {
    const options = campaignMediaOptions.filter(item =>
      (item.platform === 'both' || item.platform === platform) &&
      (platform !== 'tiktok' || item.type === 'video'));
    mediaSelect.innerHTML = options.map(item =>
      `<option value="${esc(item.value)}" data-kind="${item.type}">${esc(item.label)}</option>`).join('');
    if (options.some(item => item.value === selectedValue)) mediaSelect.value = selectedValue;
    else if (options.length) mediaSelect.selectedIndex = 0;
    mediaSelect.required = platform !== 'google';
    const note = $('#camp-media-note', app);
    if (note) note.textContent = options.length ? '' : 'Upload a creative to begin. Existing published post media appears here when available.';
  };
  const setGoogleCampaignType = () => {
    const demandGen = $('#camp-platform', app).value === 'google' && $('#camp-google-type', app).value === 'demand_gen';
    const search = $('#camp-platform', app).value === 'google' && !demandGen;
    $('#camp-google-search-fields', app).hidden = !search;
    $('#camp-google-video-fields', app).hidden = !demandGen;
    $('#camp-google-keywords', app).required = search;
    $('#camp-google-description', app).required = search;
    $('#camp-google-business', app).required = demandGen;
    $('#camp-google-long-headline', app).required = demandGen;
    $('#camp-youtube-video', app).required = demandGen;
    $('#camp-brand-logo', app).required = demandGen;
    $('#camp-objective-wrap', app).hidden = demandGen;
    $('#camp-cta-wrap', app).hidden = search || demandGen;
    $('#camp-headline', app).maxLength = search ? 30 : demandGen ? 40 : 255;
    $('#camp-body', app).maxLength = search || demandGen ? 90 : 5000;
  };
  const setMetaObjectiveFields = () => {
    const meta = $('#camp-platform', app).value === 'meta';
    const objective = $('#camp-objective', app).value;
    const fields = $('#camp-meta-objective-fields', app);
    fields.hidden = !meta;
    $$('[data-meta-objective]', fields).forEach(group => {
      group.hidden = !group.dataset.metaObjective.split(' ').includes(objective);
    });
  };
  const setCampaignPlatform = (platform, preserveInterests = false) => {
    state.campaignPlatform = platform;
    if (!preserveInterests) state.campaignInterests = [];
    const connection = campaignConnectionFor(platform);
    const accounts = campaignAccountsFor(platform);
    const accountSelect = $('#camp-account', app);
    accountSelect.innerHTML = accounts.length ? accounts.map(item =>
      `<option value="${esc(item.id)}">${esc(item.name)}${item.currency ? ` · ${esc(item.currency)}` : ''}</option>`).join('')
      : '<option value="">No selected ad account</option>';
    $('#camp-account-label', app).textContent = `${names[`${platform}ads`] || 'Meta Ads'} ad account`;
    $('#camp-currency', app).textContent = accounts[0]?.currency || 'account currency';
    const minimumBudget = { meta: 1, tiktok: 20, google: 1, linkedin: 10 }[platform];
    $('#camp-budget', app).min = String(minimumBudget);
    $('#camp-budget', app).step = platform === 'google' ? '1' : '0.01';
    $('#camp-budget-label', app).textContent = minimumBudget > 1
      ? `Daily budget (minimum ${minimumBudget})`
      : platform === 'google' ? 'Daily budget (whole currency units)' : 'Daily budget';
    $('#camp-headline-wrap', app).hidden = platform === 'tiktok';
    $('#camp-headline', app).required = platform !== 'tiktok';
    $('#camp-body-label', app).textContent = platform === 'tiktok' ? 'Video caption'
      : platform === 'linkedin' ? 'Introductory text' : 'Description';
    $('#camp-upload', app).accept = platform === 'tiktok'
      ? 'video/mp4,video/quicktime'
      : 'image/jpeg,image/png,video/mp4,video/quicktime';
    $('#camp-media-wrap', app).hidden = platform === 'google';
    $('#camp-placement-wrap', app).hidden = platform !== 'meta';
    $('#camp-url-tags-wrap', app).hidden = platform !== 'meta';
    $('#camp-interest-wrap', app).hidden = !['meta', 'tiktok'].includes(platform);
    $('#camp-category-wrap', app).hidden = !['meta', 'tiktok'].includes(platform);
    $('#camp-pixel-wrap', app).hidden = !['meta', 'tiktok'].includes(platform);
    $('#camp-linkedin-fields', app).hidden = platform !== 'linkedin';
    $('#camp-google-type-wrap', app).hidden = platform !== 'google';
    const supportedObjectives = {
      meta: ['traffic', 'awareness', 'engagement', 'video_views', 'conversions', 'lead_generation',
        'lead_conversion', 'app_promotion', 'catalog_sales', 'page_likes', 'page_visits'],
      tiktok: ['traffic', 'awareness', 'engagement', 'video_views', 'conversions'],
      google: ['traffic', 'awareness', 'engagement'],
      linkedin: ['traffic', 'awareness', 'engagement', 'video_views'],
    }[platform];
    $$('option', $('#camp-objective', app)).forEach(option => {
      option.hidden = !supportedObjectives.includes(option.value);
    });
    if (!supportedObjectives.includes($('#camp-objective', app).value)) {
      $('#camp-objective', app).value = 'traffic';
    }
    setMetaObjectiveFields();
    if (platform === 'tiktok') $('#camp-url-tags', app).value = '';
    $('#camp-pixel-label', app).textContent = `${platform === 'tiktok' ? 'TikTok' : 'Meta'} pixel`;
    $('#camp-pixel', app).innerHTML = '<option value="">No pixel</option>';
    $('#camp-meta-events', app).hidden = platform === 'tiktok';
    $('#camp-tiktok-events', app).hidden = platform !== 'tiktok';
    if (platform === 'tiktok' && !$('#camp-tiktok-events', app).querySelector('option:checked')) {
      $('#camp-event', app).value = 'ON_WEB_ORDER';
    } else if (platform === 'meta' && !$('#camp-meta-events', app).querySelector('option:checked')) {
      $('#camp-event', app).value = 'PURCHASE';
    }
    $$('[data-camp-category-option]', app).forEach(item => {
      const supported = platform === 'meta' || ['HOUSING', 'EMPLOYMENT', 'CREDIT'].includes(item.dataset.campCategoryOption);
      item.hidden = !supported;
      if (!supported) $('input', item).checked = false;
    });
    const linkedinCtas = new Set([
      'LEARN_MORE', 'SIGN_UP', 'DOWNLOAD', 'SUBSCRIBE', 'REGISTER', 'JOIN', 'ATTEND',
      'REQUEST_DEMO', 'VIEW_QUOTE', 'APPLY', 'SEE_MORE', 'SHOP_NOW', 'BUY_NOW',
    ]);
    $$('option', $('#camp-cta', app)).forEach(option => {
      option.hidden = platform === 'linkedin' && !linkedinCtas.has(option.value);
    });
    if (platform === 'linkedin' && !linkedinCtas.has($('#camp-cta', app).value)) {
      $('#camp-cta', app).value = 'LEARN_MORE';
    }
    const notice = $('#camp-connection-notice', app);
    notice.hidden = Boolean(connection);
    notice.textContent = `Connect ${names[`${platform}ads`] || 'Meta Ads'} above and select an available ad account to start a draft.`;
    const connectionError = $('#camp-connection-error', app);
    connectionError.hidden = !connection?.error;
    connectionError.textContent = connection?.error || '';
    renderCampaignMedia(platform);
    const notes = {
      meta: '',
      tiktok: "TikTok campaigns require video and at least 20 per day. Provider rules apply to clip length, dimensions and targeting.",
      google: 'Google Search uses text and keywords; Demand Gen creates video ads on YouTube. Google budgets use whole units of account currency.',
      linkedin: 'LinkedIn supports single-image/video ads; minimum daily budget is 10 in account currency. A Company Page and suitable Page/ad-account permissions are required.',
    };
    $('#camp-platform-note', app).textContent = notes[platform];
    setGoogleCampaignType();
    renderSelectedInterests();
  };
  $('#camp-platform', app).onchange = () => setCampaignPlatform($('#camp-platform', app).value);
  $('#camp-google-type', app).onchange = setGoogleCampaignType;
  $('#camp-objective', app).onchange = setMetaObjectiveFields;
  setCampaignPlatform(builderPlatform, Boolean(state.campaignDraftId));
  if ($('#camp-end-date', app)) {
    const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
    $('#camp-end-date', app).min = tomorrow;
    if (!$('#camp-end-date', app).value) $('#camp-end-date', app).value = tomorrow;
  }
  if ($('#camp-account', app)) {
    $('#camp-account', app).onchange = () => {
      const platform = $('#camp-platform', app).value;
      const selected = campaignAccountsFor(platform).find(item => item.id === $('#camp-account', app).value);
      $('#camp-currency', app).textContent = selected && selected.currency || 'account currency';
      $('#camp-pixel', app).innerHTML = '<option value="">No pixel</option>';
    };
  }
  if (mediaSelect) mediaSelect.onchange = () => {
    const option = mediaSelect.selectedOptions[0];
    if ($('#camp-objective', app).value === 'video_views' && option?.dataset.kind !== 'video') {
      $('#camp-objective', app).value = 'traffic';
    }
  };
  if ($('#camp-brand-logo', app)) $('#camp-brand-logo', app).onchange = () => {
    state.campaignLogoAssetId = $('#camp-brand-logo', app).value || null;
  };
  if (uploadInput) uploadInput.onchange = guard(async () => {
    const file = uploadInput.files && uploadInput.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    uploadNote.textContent = 'Uploading creative…';
    try {
      const asset = await api('POST', '/api/ads/assets/upload', form);
      state.campaignAssetId = asset.id;
      state.campaignDraftId = null;
      state.campaignInterests = [];
      toast('Campaign media uploaded');
      await pages.ads(app);
    } finally {
      if (uploadNote.isConnected) uploadNote.textContent = '';
    }
  });
  if (logoUploadInput) logoUploadInput.onchange = guard(async () => {
    const file = logoUploadInput.files && logoUploadInput.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    uploadNote.textContent = 'Uploading logo…';
    try {
      const asset = await api('POST', '/api/ads/assets/upload', form);
      if (!String(asset.content_type || '').startsWith('image/')) {
        toast('Upload a JPEG or PNG logo image.', true);
        return;
      }
      state.campaignLogoAssetId = asset.id;
      toast('Campaign logo uploaded');
      await pages.ads(app);
    } finally {
      if (uploadNote.isConnected) uploadNote.textContent = '';
    }
  });
  if ($('#camp-load-pixels', app)) $('#camp-load-pixels', app).onclick = guard(async button => {
    const platform = $('#camp-platform', app).value;
    const connection = campaignConnectionFor(platform);
    const accountId = $('#camp-account', app).value;
    if (!accountId || !connection) { toast(`Connect ${names[`${platform}ads`] || 'Meta Ads'} and choose an ad account first.`, true); return; }
    busy($('#camp-load-pixels', app), true, 'Loading…');
    try {
      const result = await api('GET', `/api/ads/pixels?platform=${platform}&ad_connection_id=${encodeURIComponent(connection.id)}&ad_account_id=${encodeURIComponent(accountId)}`);
      $('#camp-pixel', app).innerHTML = `<option value="">No pixel</option>${(result.pixels || []).map(pixel =>
        `<option value="${esc(pixel.id)}">${esc(pixel.name || pixel.id)}${pixel.status ? ` · ${esc(pixel.status)}` : ''}</option>`).join('')}`;
      if (!result.pixels?.length) toast(`No ${platform === 'tiktok' ? 'TikTok' : 'Meta'} pixels were found for this ad account.`);
    } finally { busy($('#camp-load-pixels', app), false); }
  });
  if ($('#camp-interest-button', app)) $('#camp-interest-button', app).onclick = guard(async () => {
    const platform = $('#camp-platform', app).value;
    const connection = campaignConnectionFor(platform);
    if (!connection) { toast(`Connect ${names[`${platform}ads`] || 'Meta Ads'} first.`, true); return; }
    const query = $('#camp-interest-search', app).value.trim();
    if (query.length < 2) { toast('Enter at least two characters to search interests.', true); return; }
    const button = $('#camp-interest-button', app);
    busy(button, true, 'Searching…');
    try {
      const result = await api('GET', `/api/ads/targeting-search?q=${encodeURIComponent(query)}&dimension=interest&platform=${platform}&ad_connection_id=${encodeURIComponent(connection.id)}`);
      $('#camp-interest-results', app).innerHTML = (result.results || []).map(item =>
        `<button class="btn sm" type="button" data-camp-interest-id="${esc(item.id)}" data-camp-interest-name="${esc(item.name)}">${esc(item.name)}</button>`).join(' ')
        || '<span class="muted">No matching interests found.</span>';
      $$('[data-camp-interest-id]', app).forEach(item => item.onclick = () => {
        state.campaignInterests = state.campaignInterests || [];
        if (!state.campaignInterests.some(selected => selected.id === item.dataset.campInterestId)) {
          state.campaignInterests.push({ id: item.dataset.campInterestId, name: item.dataset.campInterestName });
        }
        renderSelectedInterests();
      });
    } finally { busy(button, false); }
  });
  if (interestList) interestList.onclick = event => {
    const button = event.target.closest('[data-camp-interest-remove]');
    if (!button) return;
    state.campaignInterests = (state.campaignInterests || []).filter(item => item.id !== button.dataset.campInterestRemove);
    renderSelectedInterests();
  };
  if ($('#campaign-builder-form', app)) {
    const form = $('#campaign-builder-form', app);
    form.onsubmit = guard(async event => {
      event.preventDefault();
      const mediaChoice = mediaSelect.value.split(':');
      const dailyBudget = Number($('#camp-budget', app).value);
      const platform = $('#camp-platform', app).value;
      const googleType = $('#camp-google-type', app).value;
      const googleText = id => $(`#${id}`, app).value.trim();
      const usesMedia = platform !== 'google';
      const values = {
        platform,
        ad_connection_id: campaignConnectionFor(platform)?.id || '',
        ad_account_id: $('#camp-account', app).value,
        campaign_name: $('#camp-name', app).value.trim(),
        objective: $('#camp-objective', app).value,
        daily_budget: dailyBudget,
        end_date: $('#camp-end-date', app).value,
        creative_type: platform === 'google' ? (googleType === 'search' ? 'text' : 'video')
          : mediaSelect.selectedOptions[0]?.dataset.kind || '',
        media_asset_id: usesMedia && mediaChoice[0] === 'asset' ? Number(mediaChoice[1]) : null,
        post_id: usesMedia && mediaChoice[0] === 'post' ? Number(mediaChoice[1]) : null,
        brand_logo_asset_id: platform === 'google' && googleType === 'demand_gen'
          ? Number($('#camp-brand-logo', app).value) || null : null,
        headline: $('#camp-headline', app).value.trim(),
        body: $('#camp-body', app).value.trim(),
        link_url: $('#camp-link', app).value.trim(),
        google_campaign_type: googleType,
        google_keywords: googleText('camp-google-keywords').split(/\r?\n/).map(item => item.trim()).filter(Boolean),
        google_extra_headlines: googleText('camp-google-headlines').split(/\r?\n/).map(item => item.trim()).filter(Boolean),
        google_extra_description: googleText('camp-google-description'),
        google_business_name: googleText('camp-google-business'),
        google_long_headline: googleText('camp-google-long-headline'),
        youtube_video_id: googleText('camp-youtube-video'),
        linkedin_organization_id: googleText('camp-linkedin-org'),
        call_to_action: $('#camp-cta', app).value,
        countries: $('#camp-countries', app).value.split(',').map(country => country.trim().toUpperCase()).filter(Boolean),
        age_min: Number($('#camp-age-min', app).value),
        age_max: Number($('#camp-age-max', app).value),
        gender: $('#camp-gender', app).value,
        interests: state.campaignInterests || [],
        placement_platforms: $$('input[name="camp-placement"]:checked', app).map(input => input.value),
        special_ad_categories: $$('input[name="camp-category"]:checked', app).map(input => input.value),
        pixel_id: $('#camp-pixel', app).value,
        conversion_event: $('#camp-event', app).value,
        meta_page_id: googleText('camp-meta-page'),
        meta_lead_gen_form_id: googleText('camp-meta-form'),
        meta_application_id: googleText('camp-meta-app'),
        meta_object_store_url: googleText('camp-meta-store-url'),
        meta_product_catalog_id: googleText('camp-meta-catalog'),
        meta_product_set_id: googleText('camp-meta-product-set'),
        meta_custom_conversion_id: googleText('camp-meta-custom-conversion'),
        meta_custom_event_str: googleText('camp-meta-custom-event'),
        meta_offline_dataset_id: googleText('camp-meta-offline-dataset'),
        url_tags: $('#camp-url-tags', app).value.trim(),
      };
      const minimumBudget = { meta: 1, tiktok: 20, google: 1, linkedin: 10 }[platform];
      if (!Number.isFinite(dailyBudget) || dailyBudget < minimumBudget
        || (platform === 'google' && !Number.isInteger(dailyBudget))) {
        toast(platform === 'google' ? 'Google Ads budget must be a whole currency amount.'
          : `Enter a daily budget of at least ${minimumBudget} in the ad account currency.`, true); return;
      }
      if (values.objective === 'video_views' && values.creative_type !== 'video') {
        toast('Choose a video creative for a video views campaign.', true); return;
      }
      const button = $('#camp-save', app);
      busy(button, true, 'Saving…');
      try {
        if (state.campaignDraftId) await api('PUT', `/api/ads/drafts/${state.campaignDraftId}`, values);
        else await api('POST', '/api/ads/drafts', values);
        toast('Campaign draft saved');
        state.campaignDraftId = null;
        state.campaignAssetId = null;
        state.campaignLogoAssetId = null;
        state.campaignInterests = [];
        await pages.ads(app);
      } finally { if (button.isConnected) busy(button, false); }
    });
  }
  $$('[data-campaign-edit]', app).forEach(button => button.onclick = () => {
    const draft = d.campaign_drafts.find(item => item.id === Number(button.dataset.campaignEdit));
    if (!draft) return;
    const values = draft.draft_json || {};
    const platform = draft.platform || values.platform || 'meta';
    state.campaignDraftId = draft.id;
    state.campaignInterests = values.interests || [];
    state.campaignAssetId = values.media_asset_id;
    state.campaignLogoAssetId = values.brand_logo_asset_id || null;
    $('#camp-platform', app).value = platform;
    setCampaignPlatform(platform, true);
    if ($('#camp-google-type', app)) $('#camp-google-type', app).value = values.google_campaign_type || 'search';
    setGoogleCampaignType();
    if ($('#camp-account', app)) $('#camp-account', app).value = values.ad_account_id;
    if ($('#camp-name', app)) $('#camp-name', app).value = values.campaign_name || '';
    if ($('#camp-objective', app)) $('#camp-objective', app).value = values.objective || 'traffic';
    setMetaObjectiveFields();
    if ($('#camp-budget', app)) $('#camp-budget', app).value = values.daily_budget || '';
    if ($('#camp-end-date', app)) $('#camp-end-date', app).value = values.end_date || '';
    if (mediaSelect) mediaSelect.value = values.media_asset_id ? `asset:${values.media_asset_id}` : `post:${values.post_id}`;
    if ($('#camp-headline', app)) $('#camp-headline', app).value = values.headline || '';
    if ($('#camp-body', app)) $('#camp-body', app).value = values.body || '';
    if ($('#camp-link', app)) $('#camp-link', app).value = values.link_url || '';
    if ($('#camp-google-keywords', app)) $('#camp-google-keywords', app).value = (values.google_keywords || []).join('\n');
    if ($('#camp-google-headlines', app)) $('#camp-google-headlines', app).value = (values.google_extra_headlines || []).join('\n');
    if ($('#camp-google-description', app)) $('#camp-google-description', app).value = values.google_extra_description || '';
    if ($('#camp-google-business', app)) $('#camp-google-business', app).value = values.google_business_name || '';
    if ($('#camp-google-long-headline', app)) $('#camp-google-long-headline', app).value = values.google_long_headline || '';
    if ($('#camp-youtube-video', app)) $('#camp-youtube-video', app).value = values.youtube_video_id || '';
    if ($('#camp-brand-logo', app)) $('#camp-brand-logo', app).value = values.brand_logo_asset_id || '';
    if ($('#camp-linkedin-org', app)) $('#camp-linkedin-org', app).value = values.linkedin_organization_id || '';
    if ($('#camp-cta', app)) $('#camp-cta', app).value = values.call_to_action || 'LEARN_MORE';
    if ($('#camp-countries', app)) $('#camp-countries', app).value = (values.countries || []).join(', ');
    if ($('#camp-age-min', app)) $('#camp-age-min', app).value = values.age_min || 18;
    if ($('#camp-age-max', app)) $('#camp-age-max', app).value = values.age_max || 65;
    if ($('#camp-gender', app)) $('#camp-gender', app).value = values.gender || 'all';
    if ($('#camp-url-tags', app)) $('#camp-url-tags', app).value = values.url_tags || '';
    if ($('#camp-pixel', app) && values.pixel_id) {
      $('#camp-pixel', app).innerHTML = `<option value="${esc(values.pixel_id)}" selected>${esc(values.pixel_id)}</option>`;
    }
    if ($('#camp-event', app)) $('#camp-event', app).value = values.conversion_event || 'PURCHASE';
    if ($('#camp-meta-page', app)) $('#camp-meta-page', app).value = values.meta_page_id || '';
    if ($('#camp-meta-form', app)) $('#camp-meta-form', app).value = values.meta_lead_gen_form_id || '';
    if ($('#camp-meta-app', app)) $('#camp-meta-app', app).value = values.meta_application_id || '';
    if ($('#camp-meta-store-url', app)) $('#camp-meta-store-url', app).value = values.meta_object_store_url || '';
    if ($('#camp-meta-catalog', app)) $('#camp-meta-catalog', app).value = values.meta_product_catalog_id || '';
    if ($('#camp-meta-product-set', app)) $('#camp-meta-product-set', app).value = values.meta_product_set_id || '';
    if ($('#camp-meta-custom-conversion', app)) $('#camp-meta-custom-conversion', app).value = values.meta_custom_conversion_id || '';
    if ($('#camp-meta-custom-event', app)) $('#camp-meta-custom-event', app).value = values.meta_custom_event_str || '';
    if ($('#camp-meta-offline-dataset', app)) $('#camp-meta-offline-dataset', app).value = values.meta_offline_dataset_id || '';
    $$('input[name="camp-placement"]', app).forEach(input => { input.checked = (values.placement_platforms || []).includes(input.value); });
    $$('input[name="camp-category"]', app).forEach(input => { input.checked = (values.special_ad_categories || []).includes(input.value); });
    renderSelectedInterests();
    $('#camp-save', app).textContent = 'Update campaign draft';
    $('#campaign-builder-form', app).scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  $$('[data-campaign-delete]', app).forEach(button => button.onclick = guard(async () => {
    if (!await askConfirm('Delete this unsent campaign draft?', { title: 'Delete draft', ok: 'Delete', danger: true })) return;
    await api('DELETE', `/api/ads/drafts/${button.dataset.campaignDelete}`);
    toast('Campaign draft deleted');
    await pages.ads(app);
  }));
  $$('[data-campaign-create]', app).forEach(button => button.onclick = guard(async () => {
    const draft = d.campaign_drafts.find(item => item.id === Number(button.dataset.campaignCreate));
    const platform = names[`${draft?.platform}ads`] || 'Meta Ads';
    if (!await askConfirm(`Create this campaign in ${platform} as paused? It will not deliver or spend until you separately choose Launch.`, {
      title: 'Create paused campaign', ok: 'Create paused', danger: false,
    })) return;
    busy(button, true, 'Creating…');
    try {
      await api('POST', `/api/ads/drafts/${button.dataset.campaignCreate}/create`);
      toast(`Campaign created paused in ${platform}. Review it, then choose Launch when ready.`);
    } finally { await pages.ads(app); }
  }));
  $$('[data-campaign-retry]', app).forEach(button => button.onclick = guard(async () => {
    if (!await askConfirm('Retry the exact saved request with the same idempotency key? The provider key is time-limited.', {
      title: 'Retry campaign safely', ok: 'Retry', danger: false,
    })) return;
    busy(button, true, 'Checking…');
    try {
      await api('POST', `/api/ads/drafts/${button.dataset.campaignRetry}/retry`);
      toast('Campaign create confirmed. It remains paused until you launch it.');
    } finally { await pages.ads(app); }
  }));
  $$('[data-campaign-status]', app).forEach(button => button.onclick = guard(async () => {
    const nextStatus = button.dataset.status;
    const draft = d.campaign_drafts.find(item => item.id === Number(button.dataset.campaignStatus));
    const platform = names[`${draft?.platform}ads`] || 'Meta Ads';
    const title = nextStatus === 'active' ? `Launch ${platform} campaign` : `Pause ${platform} campaign`;
    const message = nextStatus === 'active'
      ? `Activate this campaign? ${platform} will review it and, when approved, spend from the selected ad account up to your daily budget.`
      : `Pause this campaign? ${platform} will stop delivery for this campaign.`;
    if (!await askConfirm(message, { title, ok: nextStatus === 'active' ? 'Launch campaign' : 'Pause campaign', danger: nextStatus === 'active' })) return;
    busy(button, true, nextStatus === 'active' ? 'Launching…' : 'Pausing…');
    try {
      await api('PUT', `/api/ads/drafts/${button.dataset.campaignStatus}/status`, { status: nextStatus });
      toast(nextStatus === 'active'
        ? `Campaign activated in ${platform}; delivery waits for platform review.`
        : `Campaign paused in ${platform}.`);
    } finally { await pages.ads(app); }
  }));
  if ($('#camp-report-refresh', app)) $('#camp-report-refresh', app).onclick = () => pages.ads(app);
  $$('[data-ads-connect]', app).forEach(button => button.onclick = guard(async () => {
    const platform = button.dataset.adsConnect;
    const loginMode = platform === 'meta' ? $('#ads-meta-login-mode', app).value : 'classic';
    const postingAccountId = platform === 'google' || loginMode === 'business'
      ? '' : $(`#ads-parent-${platform}`, app)?.value || '';
    busy(button, true, 'Opening…');
    try {
      const result = await api('POST', '/api/ads/connect', {
        platform, posting_account_id: postingAccountId, login_mode: loginMode,
      });
      if (result.already_connected) { toast('Ads account already connected'); route(); return; }
      location.href = result.auth_url;
    } finally { busy(button, false); }
  }));
  const openBoost = async (postId, platform) => {
    const post = d.posts.find(item => item.id === Number(postId));
    const connection = d.ads_connections.find(item => item.platform === (platform === 'meta' ? 'metaads' : 'tiktokads'));
    const adAccounts = (connection && connection.ad_accounts || []).filter(account => account.selected);
    if (!post || !adAccounts.length) { toast('No selected ad account is available.', true); return; }
    const title = short(post.hook || post.caption || 'Published post', 70);
    const currency = adAccounts[0].currency || 'account currency';
    const videoPost = ['short_video', 'long_video'].includes(post.content_type);
    const m = openModal(`<h2>Boost this post</h2>
      <p class="muted">${esc(title)} · ${esc(post.platform)}</p>
      <div class="notice" style="margin:10px 0">This submits a paid ad campaign immediately. Delivery may wait for platform review. It will run for the selected number of days at the daily budget below. HookTrack does not fund or bill ad spend.</div>
      <label class="small muted" for="boost-campaign-name">Campaign name</label>
      <input id="boost-campaign-name" maxlength="100" value="${esc(short(title, 80))}" style="width:100%;margin:4px 0 10px">
      <label class="small muted" for="boost-ad-account">Ad account</label>
      <select id="boost-ad-account" style="width:100%;margin:4px 0 10px">${adAccounts.map(account =>
        `<option value="${esc(account.id)}">${esc(account.name)}${account.currency ? ` · ${esc(account.currency)}` : ''}</option>`).join('')}</select>
      <label class="small muted" for="boost-budget">Daily budget (<span id="boost-currency">${esc(currency)}</span>)</label>
      <input id="boost-budget" type="number" min="0.01" step="0.01" required style="width:100%;margin:4px 0 10px">
      <label class="small muted" for="boost-duration">Run for</label>
      <select id="boost-duration" style="width:100%;margin:4px 0 10px">${[1, 3, 7, 14, 30].map(days =>
        `<option value="${days}" ${days === 7 ? 'selected' : ''}>${days} day${days === 1 ? '' : 's'}</option>`).join('')}</select>
      <label class="small muted" for="boost-goal">Campaign goal</label>
      <select id="boost-goal" style="width:100%;margin:4px 0 10px">${videoPost ? '<option value="video_views">Video views</option>' : ''}<option value="awareness" ${videoPost ? '' : 'selected'}>Awareness</option><option value="engagement">Engagement</option></select>
      <fieldset style="margin:4px 0 10px"><legend class="small muted">Special ad category (if applicable)</legend>
        ${['HOUSING', 'EMPLOYMENT', 'CREDIT', ...(platform === 'meta' ? ['SOCIAL_ISSUES_ELECTIONS_POLITICS'] : [])].map(category =>
          `<label class="check"><input type="checkbox" name="boost-category" value="${category}"> ${category.replaceAll('_', ' ').toLowerCase()}</label>`).join('')}
      </fieldset>
      <p class="small muted">An end date is set automatically from the selected duration. You can pause or edit the live ad in ${platform === 'meta' ? 'Meta' : 'TikTok'} Ads Manager.</p>
      <div class="actions"><span class="spacer"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" id="boost-submit" type="button">Start paid boost</button></div>`);
    $('#boost-ad-account', m).onchange = () => {
      const selected = adAccounts.find(account => account.id === $('#boost-ad-account', m).value);
      $('#boost-currency', m).textContent = selected && selected.currency || 'account currency';
    };
    const submit = $('#boost-submit', m);
    submit.onclick = guard(async () => {
      const dailyBudget = Number($('#boost-budget', m).value);
      const durationDays = Number($('#boost-duration', m).value);
      const campaignName = $('#boost-campaign-name', m).value.trim();
      if (!Number.isFinite(dailyBudget) || dailyBudget <= 0 || !campaignName) {
        toast('Enter a campaign name and a valid daily budget.', true);
        return;
      }
      const total = dailyBudget * durationDays;
      const selected = adAccounts.find(account => account.id === $('#boost-ad-account', m).value);
      const selectedCurrency = selected && selected.currency || 'account currency';
      if (!await askConfirm(`Start this ${platform === 'meta' ? 'Meta' : 'TikTok'} boost now?\n\nDaily budget: ${dailyBudget.toFixed(2)} ${selectedCurrency}\nEstimated scheduled budget: ${total.toFixed(2)} ${selectedCurrency} over ${durationDays} day${durationDays === 1 ? '' : 's'}.\n\nThe ad platform charges the connected ad account.`, { title: 'Confirm paid ad', ok: 'Start ad', danger: false })) return;
      busy(submit, true, 'Submitting…');
      try {
        const result = await api('POST', '/api/ads/boost', {
          post_id: post.id, platform, ad_connection_id: connection.id, ad_account_id: $('#boost-ad-account', m).value,
          campaign_name: campaignName, daily_budget: dailyBudget, duration_days: durationDays,
          goal: $('#boost-goal', m).value,
          special_ad_categories: $$('input[name="boost-category"]:checked', m).map(input => input.value),
        });
        m.close();
        toast(`Boost submitted: ${result.campaign_name}`);
      } finally {
        await pages.ads(app);
      }
    });
  };
  $$('[data-boost-post]', app).forEach(button => button.onclick = () => openBoost(button.dataset.boostPost, button.dataset.boostPlatform));
  $$('[data-boost-retry]', app).forEach(button => button.onclick = guard(async () => {
    if (!await askConfirm('The previous provider response was not confirmed. Retry with the same idempotency key and request so it cannot create a duplicate?', { title: 'Retry boost safely', ok: 'Retry', danger: false })) return;
    busy(button, true, 'Checking…');
    try {
      const result = await api('POST', `/api/ads/boost/${button.dataset.boostRetry}/retry`);
      toast(`Boost confirmed: ${result.campaign_name}`);
    } finally { await pages.ads(app); }
  }));
};

pages.clients = async app => {
  const [cl, rp, av] = await Promise.all([api('GET', '/api/clients'), api('GET', '/api/reports'), api('GET', '/api/approvals').catch(() => ({ approvals: [] }))]);
  state.clients = cl.clients;
  const links = rp.links;
  const approvalsByClient = {};
  (av.approvals || []).forEach(a => { (approvalsByClient[a.client_name] = approvalsByClient[a.client_name] || []).push(a); });
  const unassigned = state.accounts.filter(a => !a.client_id).length;
  const admin = isAdmin();
  const limitNote = rp.limit === -1 ? '' : rp.limit === 0
    ? 'Your plan lets you preview client reports. Sharing a live link needs a bigger plan (Billing).'
    : `Your plan includes ${rp.limit} shareable report link${rp.limit === 1 ? '' : 's'} (${links.length} used).`;
  const assignedAccounts = cl.clients.reduce((sum, c) => sum + Number(c.accounts || 0), 0);
  const pendingCount = (av.approvals || []).filter(a => a.status === 'pending').length;
  app.innerHTML = `<div class="clients-page">
    <div class="page-head clients-head"><div><div class="eyebrow">CLIENT WORKSPACE</div><h1>Clients</h1>
      <div class="sub">Organize brand accounts, review approvals, and share clear performance reports.</div></div>
      <div class="row">${admin ? '<button class="btn primary" id="addclient">+ Add client</button>' : ''}</div></div>
    <div class="grid clients-overview">
      <div class="card clients-metric"><span class="clients-metric-icon">◉</span><div><span>Clients</span><b>${cl.clients.length}</b><small>Managed workspaces</small></div></div>
      <div class="card clients-metric"><span class="clients-metric-icon accounts">▣</span><div><span>Assigned accounts</span><b>${assignedAccounts}</b><small>${unassigned} unassigned</small></div></div>
      <div class="card clients-metric"><span class="clients-metric-icon reports">↗</span><div><span>Shared reports</span><b>${links.length}</b><small>Active client links</small></div></div>
      <div class="card clients-metric"><span class="clients-metric-icon approvals">◷</span><div><span>Pending approvals</span><b>${pendingCount}</b><small>Waiting for client review</small></div></div>
    </div>
    <div class="clients-section-heading"><h2>Client accounts</h2><span class="muted small">${cl.clients.length} client${cl.clients.length === 1 ? '' : 's'}</span></div>
    ${limitNote ? `<div class="notice" style="margin-bottom:12px">${esc(limitNote)}</div>` : ''}
    ${cl.clients.length ? `<div class="clients-grid">${cl.clients.map(c => {
      const mine = links.filter(l => l.client_id === c.id);
      const pending = (approvalsByClient[c.name] || []).filter(a => a.status === 'pending');
      const recent = (approvalsByClient[c.name] || []).filter(a => a.status !== 'pending').slice(0, 3);
      return `<div class="card client-card"><div class="row client-card-head" style="margin-bottom:6px"><b style="font-size:16px">${esc(c.name)}</b>
        <span class="muted">${c.accounts} account${c.accounts === 1 ? '' : 's'}</span>
        ${c.requires_approval ? '<span class="tag">approves posts first</span>' : ''}<span class="spacer"></span>
        ${admin ? `<button class="btn sm" data-ca="${c.id}">Accounts</button><button class="btn sm" data-ce="${c.id}">Edit</button>` : ''}</div>
        ${c.notes ? `<p class="muted small" style="margin:0 0 6px">${esc(c.notes)}</p>` : ''}
        <p class="small" style="margin:0 0 10px">${c.handles.length ? c.handles.map(h => `<span class="pill">${esc(h)}</span>`).join(' ') : '<span class="muted">No accounts yet. Use Accounts to add some.</span>'}</p>
        <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn sm" data-prev="${c.id}" ${c.accounts ? '' : 'disabled'}>Preview report</button>
          ${admin ? `<button class="btn sm primary" data-share="${c.id}" ${c.accounts ? '' : 'disabled'}>Create share link</button>` : ''}</div>
        ${mine.length ? `<div style="margin-top:10px">${mine.map(l => `<div class="row small" style="gap:8px;padding:6px 0;border-top:1px solid var(--line,#e5e7eb);flex-wrap:wrap">
          <span style="flex:1;min-width:180px"><b>${esc(l.title || 'Report')}</b> <span class="muted">· last ${l.days} days · ${l.view_count} view${l.view_count === 1 ? '' : 's'}${l.last_viewed_at ? ', last ' + esc(String(l.last_viewed_at).slice(0, 10)) : ''}</span></span>
          <button class="btn sm" data-copy="${esc(l.path)}">Copy link</button><a class="btn sm" href="${esc(l.path)}" target="_blank" rel="noopener">Open</a>
          ${admin ? `<button class="btn sm danger" data-revoke="${l.id}">Revoke</button>` : ''}</div>`).join('')}</div>` : ''}
        ${pending.length || recent.length ? `<div style="margin-top:10px"><div class="small muted" style="margin-bottom:4px">Approvals</div>
          ${pending.map(a => `<div class="row small" style="gap:8px;padding:6px 0;border-top:1px solid var(--line,#e5e7eb);flex-wrap:wrap">
            <span style="flex:1;min-width:180px"><span class="tag">pending</span> ${esc(short(a.hook || a.caption || 'Untitled', 60))} <span class="muted">· ${esc(a.handle || '')} · goes out ${esc(fmtWhen(a.schedule_at))}</span></span>
            <button class="btn sm" data-ap-copy="${esc(a.link)}">Copy link</button>
            ${admin ? `<button class="btn sm danger" data-ap-cancel="${a.id}">Cancel</button>` : ''}</div>`).join('')}
          ${recent.map(a => `<div class="row small" style="gap:8px;padding:6px 0;border-top:1px solid var(--line,#e5e7eb);flex-wrap:wrap">
            <span style="flex:1;min-width:180px">${a.status === 'approved' && a.publish_error ? '<span class="tag warn">approved, didn\'t post</span>' : a.status === 'approved' ? '<span class="tag good">approved</span>' : a.status === 'rejected' ? '<span class="tag warn">changes requested</span>' : '<span class="tag">cancelled</span>'}
              ${esc(short(a.hook || a.caption || 'Untitled', 60))}${a.status === 'rejected' && a.client_note ? ` <span class="muted">· "${esc(short(a.client_note, 80))}"</span>` : ''}${a.status === 'approved' && a.publish_error ? ` <span class="muted">· ${esc(short(a.publish_error, 100))}</span>` : ''}</span></div>`).join('')}</div>` : ''}</div>`;
    }).join('')}</div>` : `<div class="empty card clients-empty"><span class="clients-empty-icon">◉</span><h2>No clients yet</h2><p>Add a client for each brand you manage, put its accounts under it, then share a report.</p></div>`}
    ${unassigned && cl.clients.length ? `<p class="muted small clients-unassigned">${unassigned} account${unassigned === 1 ? ' is' : 's are'} not assigned to a client.</p>` : ''}
  </div>`;
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
  $$('[data-ap-copy]', app).forEach(b => (b.onclick = guard(async () => {
    await navigator.clipboard.writeText(fullLink(b.dataset.apCopy)); toast('Link copied');
  })));
  $$('[data-ap-cancel]', app).forEach(b => (b.onclick = guard(async () => {
    if (!await askConfirm('Cancel this approval request? The link will stop working.')) return;
    await api('DELETE', `/api/approvals/${b.dataset.apCancel}`); route();
  })));
};

/* ---------------- inbox: comments and DMs on a connected account (yours or a client's) ---------------- */
pages.inbox = async app => {
  const view = state.inboxView === 'messages' ? 'messages' : 'comments';
  const accounts = await api('GET', `/api/inbox/accounts?capability=${view}`);
  const toggle = `<div class="seg" role="tablist">
      <button type="button" class="seg-btn ${view === 'comments' ? 'on' : ''}" data-view="comments">Comments</button>
      <button type="button" class="seg-btn ${view === 'messages' ? 'on' : ''}" data-view="messages">Messages</button>
    </div>`;
  const hasSelectedAccount = accounts.some(a => String(a.id) === String(state.inboxAccount || ''));
  const acctFilter = hasSelectedAccount ? String(state.inboxAccount) : '';
  if (!hasSelectedAccount) state.inboxAccount = '';
  if (!accounts.length) {
    const who = view === 'messages' ? 'TikTok, Instagram, Facebook or X' : 'TikTok, Instagram, YouTube, Facebook or LinkedIn';
    const why = view === 'messages'
      ? 'YouTube and LinkedIn have no direct-message inbox on Zernio — only X, TikTok, Instagram and Facebook do.'
      : 'X has no comment inbox on Zernio — it only supports direct messages there.';
    app.innerHTML = `<div class="inbox-page">
      <div class="page-head inbox-head"><div><div class="eyebrow">COMMUNITY</div><h1>Inbox</h1><div class="sub">Keep up with comments and messages across your connected accounts.</div></div>${toggle}</div>
      <div class="empty card inbox-empty"><span class="inbox-empty-icon">✉</span><h2>No accounts connected for ${view} yet</h2><p>Connect a ${who} account under <a href="#/settings">Settings</a> or <a href="#/clients">Clients</a> to see them here. ${why}</p></div>
    </div>`;
    $$('[data-view]', app).forEach(b => (b.onclick = () => { state.inboxView = b.dataset.view; route(); }));
    return;
  }
  const acctSelect = `<select id="ia" style="width:auto"><option value="">All connected accounts</option>${accounts.map(a =>
    `<option value="${a.id}" ${String(acctFilter) === String(a.id) ? 'selected' : ''}>${esc(a.handle)}</option>`).join('')}</select>`;
  const inboxSearch = `<input id="inbox-search" type="search" value="${esc(state.inboxQuery || '')}" placeholder="Search ${view === 'messages' ? 'messages' : 'comments'}…" aria-label="Search inbox">`;
  const inboxStatusValue = view === 'messages' ? (state.inboxMessageFilter || 'all') : (state.inboxCommentFilter || 'all');
  const inboxStatus = view === 'messages'
    ? `<select id="inbox-status" aria-label="Filter conversations"><option value="all" ${inboxStatusValue === 'all' ? 'selected' : ''}>All conversations</option><option value="unread" ${inboxStatusValue === 'unread' ? 'selected' : ''}>Unread</option></select>`
    : `<select id="inbox-status" aria-label="Filter comments"><option value="all" ${inboxStatusValue === 'all' ? 'selected' : ''}>All comments</option><option value="needs-reply" ${inboxStatusValue === 'needs-reply' ? 'selected' : ''}>Needs reply</option><option value="replied" ${inboxStatusValue === 'replied' ? 'selected' : ''}>Replied</option></select>`;

  const dmNote = `<div class="notice small" style="margin-bottom:12px">
      <b>Platform rules for DMs, not a HookTrack limit:</b>
      YouTube and LinkedIn have no direct-message inbox at all.
      TikTok DMs only work on a Business account outside the EEA, Switzerland and the UK, and are reply-only — you can never send the first message, only reply to one someone sent you.
      Instagram allows a free reply within 24 hours of their last message to you (a limited message type works up to 7 days after that).
    </div>`;
  const commentsNote = `<div class="notice small" style="margin-bottom:12px">
      <b>Platform rule for comments, not a HookTrack limit:</b> X has no comment inbox at all — only direct messages (under Messages).
    </div>`;
  if (view === 'messages') {
    const convos = await api('GET', `/api/inbox/conversations${acctFilter ? '?account_id=' + acctFilter : ''}`);
    const unread = convos.filter(c => c.unread).length;
    app.innerHTML = `<div class="inbox-page">
      <div class="page-head inbox-head"><div><div class="eyebrow">COMMUNITY</div><h1>Messages</h1>
        <div class="sub">Respond to direct conversations across your connected accounts.</div></div></div>
      <div class="grid inbox-overview">
        <div class="card inbox-metric"><span class="inbox-metric-icon">✉</span><div><span>Conversations</span><b>${convos.length}</b><small>For the selected accounts</small></div></div>
        <div class="card inbox-metric"><span class="inbox-metric-icon unread">●</span><div><span>Unread</span><b>${unread}</b><small>Waiting for your attention</small></div></div>
        <div class="card inbox-metric"><span class="inbox-metric-icon accounts">◉</span><div><span>Connected accounts</span><b>${accounts.length}</b><small>Available for messages</small></div></div>
      </div>
      <div class="inbox-toolbar"><div class="inbox-toolbar-note"><b>Direct messages</b><span>Choose an account or open a conversation</span></div>
        <div class="row inbox-actions">${toggle}${acctSelect}${inboxStatus}${inboxSearch}<button class="btn sm" id="irefresh">Refresh</button></div></div>
      ${dmNote}
      ${convos.length ? `<ul class="list inbox-list">${convos.map(c => `<li data-inbox-row data-inbox-status="${c.unread ? 'unread' : 'read'}" data-inbox-search="${esc([c.participant_name, c.handle, c.platform, c.last_message_preview].filter(Boolean).join(' ').toLowerCase())}" data-open-convo="${c.id}" style="cursor:pointer">
          <div style="flex:1;min-width:0"><b>${esc(c.participant_name || 'Someone')}</b> <span class="muted small">· ${esc(c.handle)} · ${window.socialPlatformIdentity(c.platform)}</span>
            ${c.unread ? '<span class="pill hot" style="margin-left:6px">New</span>' : ''}
            <div class="small muted">${esc(short(c.last_message_preview || '', 100))}</div></div>
          <span class="small muted">${c.last_message_at ? esc(String(c.last_message_at).slice(0, 10)) : ''}</span></li>`).join('')}</ul>`
        : `<div class="empty card inbox-empty"><span class="inbox-empty-icon">✉</span><h2>No conversations yet</h2><p>Press Refresh to check for new DMs on your connected accounts.</p></div>`}
      ${convos.length ? '<div class="empty card inbox-empty inbox-filter-empty" id="inbox-filter-empty" hidden><h2>No matches</h2><p>Try a different search or filter.</p></div>' : ''}
    </div>`;
    wireInboxFilters(app, 'inboxMessageFilter');
    $('#irefresh').onclick = guard(async () => { await api('GET', `/api/inbox/conversations?refresh=true${acctFilter ? '&account_id=' + acctFilter : ''}`); route(); });
    $$('[data-open-convo]', app).forEach(li => (li.onclick = () => conversationModal(+li.dataset.openConvo)));
  } else {
    const comments = await api('GET', `/api/inbox/comments${acctFilter ? '?account_id=' + acctFilter : ''}`);
    const needsReply = comments.filter(c => !c.replied_at && !c.hidden_at).length;
    app.innerHTML = `<div class="inbox-page">
      <div class="page-head inbox-head"><div><div class="eyebrow">COMMUNITY</div><h1>Comments</h1>
        <div class="sub">Follow up on the conversations happening around your posts.</div></div></div>
      <div class="grid inbox-overview">
        <div class="card inbox-metric"><span class="inbox-metric-icon">☷</span><div><span>Comments</span><b>${comments.length}</b><small>For the selected accounts</small></div></div>
        <div class="card inbox-metric"><span class="inbox-metric-icon unread">↩</span><div><span>Need a reply</span><b>${needsReply}</b><small>Comments without a response</small></div></div>
        <div class="card inbox-metric"><span class="inbox-metric-icon accounts">◉</span><div><span>Connected accounts</span><b>${accounts.length}</b><small>Available for comments</small></div></div>
      </div>
      <div class="inbox-toolbar"><div class="inbox-toolbar-note"><b>Recent comments</b><span>Reply directly from your inbox</span></div>
        <div class="row inbox-actions">${toggle}${acctSelect}${inboxStatus}${inboxSearch}<button class="btn sm" id="irefresh">Refresh</button></div></div>
      ${commentsNote}
      ${comments.length ? `<ul class="list inbox-list">${comments.map(c => `<li data-inbox-row data-inbox-status="${c.replied_at ? 'replied' : c.hidden_at ? 'hidden' : 'needs-reply'}" data-inbox-search="${esc([c.author_name, c.post_title, c.handle, c.platform, c.body].filter(Boolean).join(' ').toLowerCase())}" style="align-items:flex-start;flex-direction:column;gap:6px${c.parent_comment_id ? ';margin-left:28px;border-left:2px solid var(--border);padding-left:10px' : ''}">
          <div class="row" style="width:100%">${c.parent_comment_id ? '<span class="muted small">↳ reply ·</span>' : ''}<b>${esc(c.author_name || 'Someone')}</b> <span class="muted small">on "${esc(short(c.post_title || 'your post', 60))}" · ${esc(c.handle)}</span>
            <span class="spacer"></span><span class="small muted">${c.posted_at ? esc(String(c.posted_at).slice(0, 10)) : ''}</span></div>
          <div>${esc(c.body)}</div>
          ${c.replied_at ? `<div class="small muted">You replied: ${esc(short(c.reply_body, 140))}</div>`
            : `<div class="row" style="width:100%;gap:8px"><input type="text" class="reply-in" data-comment="${c.id}" placeholder="Write a reply…" style="flex:1">
               <button class="btn sm primary" data-reply="${c.id}">Reply</button></div>
               <div class="small" id="reply-err-${c.id}" style="color:var(--bad);display:none"></div>`}
        </li>`).join('')}</ul>`
        : `<div class="empty card inbox-empty"><span class="inbox-empty-icon">☷</span><h2>No comments yet</h2><p>Press Refresh to check for new comments on your connected accounts' posts.</p></div>`}
      ${comments.length ? '<div class="empty card inbox-empty inbox-filter-empty" id="inbox-filter-empty" hidden><h2>No matches</h2><p>Try a different search or filter.</p></div>' : ''}
    </div>`;
    wireInboxFilters(app, 'inboxCommentFilter');
    $('#irefresh').onclick = guard(async () => { await api('GET', `/api/inbox/comments?refresh=true${acctFilter ? '&account_id=' + acctFilter : ''}`); route(); });
    $$('[data-reply]', app).forEach(b => (b.onclick = async () => {
      const input = $(`.reply-in[data-comment="${b.dataset.reply}"]`, app);
      const errBox = $(`#reply-err-${b.dataset.reply}`, app);
      errBox.style.display = 'none';
      const body = input.value.trim();
      if (!body) { errBox.textContent = 'Write a reply first'; errBox.style.display = ''; return; }
      busy(b, true, 'Sending…');
      try { await api('POST', `/api/inbox/comments/${b.dataset.reply}/reply`, { body }); toast('Reply sent'); route(); }
      catch (e) { errBox.textContent = e.message; errBox.style.display = ''; }
      finally { busy(b, false); }
    }));
  }
  $('#ia', app).onchange = e => { state.inboxAccount = e.target.value; route(); };
  $$('[data-view]', app).forEach(b => (b.onclick = () => { state.inboxView = b.dataset.view; route(); }));
};

function wireInboxFilters(app, filterKey) {
  const search = $('#inbox-search', app), status = $('#inbox-status', app);
  const empty = $('#inbox-filter-empty', app);
  const filter = () => {
    const query = search.value.trim().toLowerCase();
    let shown = 0;
    $$('[data-inbox-row]', app).forEach(row => {
      const matches = (!query || row.dataset.inboxSearch.includes(query)) &&
        (status.value === 'all' || row.dataset.inboxStatus === status.value);
      row.hidden = !matches;
      if (matches) shown++;
    });
    empty.hidden = shown > 0;
  };
  search.oninput = () => {
    state.inboxQuery = search.value;
    filter();
  };
  status.onchange = () => {
    state[filterKey] = status.value;
    filter();
  };
  filter();
}

function conversationModal(conversationId) {
  const m = openModal(`<h2>Conversation</h2><div id="cv-msgs" class="muted small">Loading…</div>
    <div class="row" style="gap:8px;margin-top:14px"><input type="text" id="cv-send" placeholder="Write a message…" style="flex:1"><button class="btn primary" id="cv-go">Send</button></div>
    <div class="small" id="cv-err" style="color:var(--bad);display:none;margin-top:6px"></div>
    <div class="actions"><button class="btn" data-close>Close</button></div>`, true);
  const load = async () => {
    const msgs = await api('GET', `/api/inbox/conversations/${conversationId}/messages`);
    $('#cv-msgs', m).className = '';
    $('#cv-msgs', m).innerHTML = msgs.length ? msgs.map(x => `<div style="margin:6px 0;text-align:${x.direction === 'out' ? 'right' : 'left'}">
        <span class="pill ${x.direction === 'out' ? 'win' : 'avg'}">${esc(x.body)}</span></div>`).join('')
      : '<p class="muted">No messages yet.</p>';
  };
  load();
  $('#cv-go', m).onclick = async () => {
    const errBox = $('#cv-err', m);
    errBox.style.display = 'none';
    const body = $('#cv-send', m).value.trim();
    if (!body) { errBox.textContent = 'Write a message first'; errBox.style.display = ''; return; }
    const btn = $('#cv-go', m); busy(btn, true, 'Sending…');
    try { await api('POST', `/api/inbox/conversations/${conversationId}/messages`, { body }); $('#cv-send', m).value = ''; await load(); }
    catch (e) { errBox.textContent = e.message; errBox.style.display = ''; }
    finally { busy(btn, false); }
  };
}

function clientModal(c) {
  const requiresApproval = c ? c.requires_approval !== false : true;
  const m = openModal(`<h2>${c ? 'Edit client' : 'New client'}</h2><div class="fields">
    <div><label class="f">Name</label><input id="cn" value="${esc(c ? c.name : '')}" maxlength="80" placeholder="Brand or client name"></div>
    <div><label class="f">Contact email (optional)</label><input id="cem" type="email" value="${esc(c ? c.contact_email || '' : '')}" placeholder="for approval links and reports" maxlength="200"></div>
    <div style="grid-column:1/-1"><label class="f">Notes (only you see these)</label><input id="cno" value="${esc(c ? c.notes : '')}" maxlength="500"></div>
    <div style="grid-column:1/-1"><label class="check"><input type="checkbox" id="creq" ${requiresApproval ? 'checked' : ''}> This client approves a post before it goes out to their account</label></div></div>
    <div class="actions">${c ? '<button class="btn danger" id="cdel">Delete</button>' : ''}<span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="csave">Save</button></div>`);
  $('#csave', m).onclick = guard(async () => {
    const b = { name: $('#cn', m).value.trim(), notes: $('#cno', m).value, contact_email: $('#cem', m).value.trim(), requires_approval: $('#creq', m).checked };
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
      <input type="checkbox" value="${a.id}" ${a.client_id === c.id ? 'checked' : ''}> <span>${esc(a.handle)} <span class="muted small">${window.socialPlatformIdentity(a.platform)}${taken(a) ? ' · currently ' + esc((state.clients.find(x => x.id === a.client_id) || {}).name || 'another client') + ' (moves here)' : ''}</span></span></label>`).join('') || '<p class="muted">Add accounts first.</p>'}</div>
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
  const PLATFORMS = ['TikTok', 'Instagram', 'YouTube', 'X', 'LinkedIn', 'Facebook'];
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
        <div><label class="f">Niche</label><input id="n" value="${esc(x.niche)}" list="niche-list" placeholder="e.g. fitness, food, comedy" title="Also used to compare a new account with similar ones until it has posts of its own"><datalist id="niche-list">${['general', 'comedy', 'fitness', 'beauty', 'food', 'tech', 'education', 'gaming', 'lifestyle', 'music'].map(n => `<option value="${n}">`).join('')}</datalist></div>
        <div><label class="f">Status</label><select id="st">${['active', 'warming', 'paused', 'killed'].map(s => `<option ${x.status === s ? 'selected' : ''}>${s}</option>`).join('')}</select></div>
        <div><label class="f">Posts per day</label><input id="t" type="number" min="0" max="50" value="${x.times_per_day}"></div>
        <div><label class="f">Content hook style</label><input id="ch" value="${esc(x.content_hook)}"></div>
        <div><label class="f">AI model / tool</label><input id="md" value="${esc(x.model)}"></div>
        <div style="grid-column:1/-1"><label class="f" style="display:flex;align-items:center;gap:8px;font-weight:500">
          <input type="checkbox" id="sy" ${x.sync_enabled ? 'checked' : ''}> Auto-sync (include in Sync all)</label>
          <div class="muted small">Pulls public posts &amp; view counts automatically.</div></div></div></details>
    <div class="actions">${editing ? '<button class="btn danger" id="del">Delete</button>' : ''}<span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="save">${editing ? 'Save' : 'Add account'}</button></div>`);

  const guessPlatform = v => (/tiktok\.com/i.test(v) ? 'TikTok' : /instagram\.com/i.test(v) ? 'Instagram' : /youtu(\.be|be\.com)/i.test(v) ? 'YouTube'
    : /(x\.com|twitter\.com)/i.test(v) ? 'X' : /linkedin\.com/i.test(v) ? 'LinkedIn' : /facebook\.com|fb\.com/i.test(v) ? 'Facebook' : '');
  const looksLikeLink = v => /(tiktok|instagram|youtube|youtu|twitter|linkedin|facebook|x)\.(com|be)/i.test(v);
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
        <span class="muted">· ${window.socialPlatformIdentity(r.platform)}${r.followers != null ? ' · ' + compact(r.followers) + ' followers' : ''}${r.video_count != null ? ' · ' + compact(r.video_count) + ' videos' : ''}${r.niche ? ' · niche: ' + esc(r.niche) : ''}</span>`);
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

  app.innerHTML = `<div class="settings-page">
  <div class="page-head settings-head"><div><div class="eyebrow">WORKSPACE CONTROL</div><h1>Settings</h1>
    <div class="sub">Configure the workspace, publishing, and your account preferences.</div></div></div>
  <div class="grid settings-overview">
    <div class="card settings-metric"><span class="settings-metric-icon">▣</span><div><span>Workspace</span><b title="${esc(me.workspace.name)}">${esc(short(me.workspace.name, 28))}</b><small>Current workspace</small></div></div>
    <div class="card settings-metric"><span class="settings-metric-icon role">◉</span><div><span>Your role</span><b>${esc(me.workspace.role)}</b><small>Workspace permissions</small></div></div>
    <div class="card settings-metric"><span class="settings-metric-icon team">♧</span><div><span>Team members</span><b>${members.length}</b><small>People with access</small></div></div>
    <div class="card settings-metric"><span class="settings-metric-icon invites">✉</span><div><span>Pending invites</span><b>${admin ? invites.length : '—'}</b><small>${admin ? 'Awaiting acceptance' : 'Managed by admins'}</small></div></div>
  </div>
  <div class="grid settings-content">

  <div class="settings-section" id="settings-start"><h2 class="settings-h">Getting started</h2>
  <p class="muted small" style="margin:-4px 0 12px">Core setup for this workspace. Everything else is optional.</p>

  <div class="card"><h2>Decision rules</h2>
    <p class="muted small">When to Scale, Tweak, or Stop an account based on weekly views and hook scores.</p>
    <div class="fields">
    ${[['scale_weekly', 'Weekly views to Scale'], ['scale_vsc', 'Hook score to Scale (of 30)'], ['mutate_vsc', 'Hook score to Tweak (of 30)'], ['weekly_target', 'Posts to make per week (0 = no target)'], ['credit_budget', 'Monthly budget for syncing and reading content, in credits (0 = no budget)'], ['hold_low_pct', 'Hold starts at (% of normal, 50-99)'], ['hold_high_pct', 'Hold ends at (% of normal, from 100)'], ['kill_after_posts', 'Posts before a stall = Stop'], ['stall_views', 'Views that count as a breakout'], ['winner_pct', 'Winner = % of an account\'s normal views (200 = 2×)']]
      .map(([k, l]) => `<div><label class="f">${l}</label><input data-s="${k}" type="number" min="0" value="${S[k]}" ${admin ? '' : 'disabled'}></div>`).join('')}</div>
    <div class="small muted" id="calib" style="margin-top:10px">Checking how reliable the calls have been…</div>
    <div class="check" style="margin-top:10px"><input type="checkbox" id="read-on-send" data-s="read_on_send" ${S.read_on_send ? 'checked' : ''} ${admin ? '' : 'disabled'}>
      <label for="read-on-send">Read a Scale hook's video when I send it to Posts <span class="muted">(about 1 credit each; its first words and structure then arrive in the card's brief)</span></label></div>
    ${admin ? '<div class="actions"><button class="btn primary" id="saverules">Save rules</button></div>' : ''}</div>

  <div class="card"><h2>Export CSV</h2>
    <p class="muted small">Download views and posts for spreadsheets or backups.</p>
    <div class="row" style="gap:8px;flex-wrap:wrap">
      <a class="btn" href="/api/export/views.csv" download>Views CSV</a>
      <a class="btn" href="/api/export/posts.csv" download>Posts CSV</a>
    </div>
  </div>
  </div><!-- /getting started -->

  <div class="settings-section" id="settings-publishing"><h2 class="settings-h">Publishing</h2>
  <div class="card" id="publish-card"><h2>📤 Connect your accounts</h2>
    ${pub.managed_available && pub.held ? `<div class="notice" style="margin-bottom:10px"><b>Publishing starts when you choose a plan.</b>
      Your trial includes tracking, decisions, cards and briefs. <a href="#/billing">Choose a plan</a> to connect accounts and publish.</div>` : ''}
    ${pub.managed_available ? `
    <p class="muted">Connect the TikTok, Instagram, YouTube, X, LinkedIn or Facebook accounts you publish to. Press <b>Publish</b> on a Filmed card and HookTrack posts it for you.
      You sign in on each platform's own page: HookTrack never sees your password. <b>Nothing is ever posted unless you press Publish.</b></p>
    <div id="pub-managed-body" class="small muted">Loading…</div>`
    : `<p class="muted">Publishing is not set up on this server yet. Ask the platform admin to add the publishing key.</p>`}
  </div>
  </div><!-- /publishing section -->

  <div class="settings-section" id="settings-team"><h2 class="settings-h">Team &amp; account</h2>

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
    <label class="check" style="margin-top:14px"><input type="checkbox" id="digest-on" ${me.user.digest === false ? '' : 'checked'}> Email me a weekly summary (winners, matching posts that beat their original, what is waiting)</label>
    <label class="check"><input type="checkbox" id="hot-on" ${me.user.hot_alerts === false ? '' : 'checked'}> Email me when a post is breaking out (a very strong first day)</label>
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
  </div></div></div>`;



  // Publishing: the accounts this workspace has connected, and buttons to connect more
  const mbody = $('#pub-managed-body');
  if (mbody && pub.managed_available) {
    (async () => {
      let r;
      try { r = await api('GET', '/api/publish/managed'); } catch (e) { mbody.textContent = e.message; return; }
      const names = { tiktok: 'TikTok', instagram: 'Instagram', youtube: 'YouTube', twitter: 'X', linkedin: 'LinkedIn', facebook: 'Facebook' };
      mbody.className = 'small';
      mbody.innerHTML = `${(r.accounts || []).length ? `<ul class="list">${r.accounts.map(a => `<li><span class="badge">${window.socialPlatformIdentity(a.platform, names[a.platform] || a.platform)}</span>
          <span style="flex:1">${esc(a.display_name || a.username)}${a.username ? ` <span class="muted">@${esc(String(a.username).replace(/^@/, ''))}</span>` : ''}</span><span class="pill SCALE">connected</span>
          ${admin ? `<button class="btn sm danger" type="button" data-disconnect="${esc(a.id)}" data-name="${esc(a.display_name || a.username || '')}">Disconnect</button>` : ''}</li>`).join('')}</ul>`
        : '<div class="muted" style="margin-bottom:6px">No account connected yet.</div>'}
        ${r.error ? `<div class="muted" style="margin-top:4px">${esc(r.error)}</div>` : ''}
        ${admin ? `<div class="row" style="gap:8px;margin-top:10px;flex-wrap:wrap">${['tiktok', 'instagram', 'youtube', 'twitter', 'linkedin', 'facebook'].map(p =>
          `<button class="btn social-brand-btn" type="button" data-connect="${p}" ${pub.held ? 'disabled title="Unlocks with a paid plan"' : ''}>Connect ${window.socialPlatformIdentity(p, names[p])}</button>`).join('')}</div>`
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

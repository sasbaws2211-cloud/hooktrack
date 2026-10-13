/* HookTrack - vanilla JS single page app */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = n => (n == null ? '' : Number(n).toLocaleString());
const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const parseISO = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const mondayOf = d => addDays(d, -((d.getDay() + 6) % 7));
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const short = (s, n = 28) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s || '');

const state = {
  me: null, accounts: [], phones: [], settings: {}, ai: { available: false },
  weekStart: iso(mondayOf(new Date())),
  calMonth: iso(new Date(new Date().getFullYear(), new Date().getMonth(), 1)),
  calAccount: '', hookStatus: '', hookAccount: '',
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
const pill = (t, cls) => (t ? `<span class="pill ${esc(cls || t)}">${esc(t)}</span>` : '');
const accColor = id => (state.accounts.find(a => a.id === id) || {}).phone_color || 'var(--accent)';

async function loadBase() {
  [state.accounts, state.phones, state.settings, state.ai] = await Promise.all([
    api('GET', '/api/accounts'), api('GET', '/api/phones'), api('GET', '/api/settings'), api('GET', '/api/ai/status')]);
  const b = $('#ai-badge');
  b.textContent = state.ai.available ? 'AI on' : 'AI off';
  b.className = 'badge' + (state.ai.available ? ' on' : '');
  b.title = state.ai.available ? `Model: ${state.ai.model}` : 'AI is not configured for this workspace (Settings → AI)';
}
const isAdmin = () => state.me && ['owner', 'admin'].includes(state.me.workspace.role);
const aiNotice = () => (state.ai.available ? '' :
  `<div class="notice">AI is off for this workspace. ${isAdmin() ? 'Add an API key in <a href="#/settings">Settings → AI</a>' : 'Ask a workspace admin to add an API key in Settings → AI'} to turn it on. Manual entry works without it.</div>`);

/* ---------------- router ---------------- */
const pages = {};
async function route() {
  const hash = location.hash.replace('#/', '');
  if (hash.startsWith('invite/')) return renderInvite(hash.slice(7));
  if (!state.me) {
    try { state.me = await api('GET', '/api/auth/me'); } catch { state.me = null; }
    if (!state.me) return renderAuth();
  }
  document.body.classList.remove('anon'); renderChrome();
  const tab = (hash || 'dashboard').split('?')[0];
  const page = pages[tab] || pages.dashboard;
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.tab === (pages[tab] ? tab : 'dashboard')));
  try { await loadBase(); await page($('#app')); } catch (e) {
    if (state.me) $('#app').innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p></div>`;
  }
}
window.addEventListener('hashchange', route);

/* ---------------- chrome: workspace switcher, sign out ---------------- */
function renderChrome() {
  const sw = $('#ws-switch');
  sw.innerHTML = state.me.workspaces.map(w => `<option value="${w.id}" ${w.id === state.me.workspace.id ? 'selected' : ''}>${esc(w.name)}</option>`).join('')
    + '<option value="new">＋ New workspace…</option>';
  $('#user-email').textContent = state.me.user.email;
}
function resetViewState() { state.calAccount = ''; state.hookAccount = ''; state.hookStatus = ''; }
$('#ws-switch').addEventListener('change', guard(async e => {
  if (e.target.value === 'new') {
    const name = prompt('Name for the new workspace?');
    if (!name || !name.trim()) { renderChrome(); return; }
    state.me = await api('POST', '/api/workspaces', { name: name.trim() });
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
    <p class="small muted" style="margin-top:14px">${mode === 'login' ? 'New here? <a href="#" id="swap">Create a workspace</a>' : 'Already have an account? <a href="#" id="swap">Sign in</a>'}</p></div>`;
  $('#swap').onclick = e => { e.preventDefault(); renderAuth(mode === 'login' ? 'signup' : 'login'); };
  $('#auth-form').onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    try {
      state.me = await api('POST', mode === 'login' ? '/api/auth/login' : '/api/auth/signup', body);
      location.hash = '#/dashboard'; route();
    } catch (err) { $('#auth-err').textContent = err.message; }
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
  if (!d.accounts) {
    app.innerHTML = `<div class="empty card"><h2>Welcome to HookTrack</h2>
      <p>Track daily views per account, score your hooks, and let AI tell you what to scale or kill.</p>
      <div class="row" style="justify-content:center"><a class="btn primary" href="#/accounts">Add accounts or import your spreadsheet</a></div></div>`;
    return;
  }
  const delta = d.prev_week_views ? Math.round((d.week_views - d.prev_week_views) / d.prev_week_views * 100) : null;
  const maxT = Math.max(1, ...d.trend.map(t => t.views));
  const maxP = Math.max(1, ...d.by_phone.map(p => p.views));
  app.innerHTML = `
  <div class="page-head"><div><h1>Dashboard</h1><div class="sub">Week of ${esc(d.week_start)}</div></div></div>
  <div class="grid kpis">
    <div class="card kpi"><div class="label">Views this week</div><div class="value">${fmt(d.week_views)}</div>
      <div class="delta ${delta > 0 ? 'up' : delta < 0 ? 'down' : ''}">${delta == null ? 'No prior week' : `${delta > 0 ? '▲' : delta < 0 ? '▼' : ''} ${Math.abs(delta)}% vs last week`}</div></div>
    <div class="card kpi"><div class="label">Avg / account</div><div class="value">${fmt(d.avg_per_account)}</div><div class="delta">${d.accounts} accounts</div></div>
    <div class="card kpi"><div class="label">Scale</div><div class="value up">${d.decisions.SCALE}</div><div class="delta">${fmt(d.settings.scale_weekly)}+ weekly &amp; top hook</div></div>
    <div class="card kpi"><div class="label">Kill</div><div class="value down">${d.decisions.KILL}</div><div class="delta">stalled ${d.settings.kill_after_posts}+ posts</div></div>
  </div>
  <div class="card" style="margin-bottom:14px"><h2>Ask your data</h2>${aiNotice()}
    <form class="ask" id="ask-form"><input id="ask-q" placeholder="e.g. Which accounts should I kill this week, and why?" maxlength="1000" ${state.ai.available ? '' : 'disabled'}>
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
  <div class="grid cols-2">
    <div class="card"><h2>Act on this</h2>${d.act_on.length ? `<ul class="list">${d.act_on.map(a =>
      `<li>${pill(a.decision)}<div><b>${esc(a.handle)}</b><div class="muted small">${esc(a.reason)}</div></div></li>`).join('')}</ul>`
      : '<p class="muted">Nothing to scale or kill right now.</p>'}
      ${d.unscored_posts ? `<p class="small muted">${d.unscored_posts} posted hooks aren't scored yet — <a href="#/hooks">score them</a> so SCALE decisions are accurate.</p>` : ''}</div>
    <div class="card"><h2>Top hooks</h2>${d.top_hooks.length ? `<ul class="list">${d.top_hooks.map(h =>
      `<li><div style="flex:1"><div>${esc(short(h.hook, 60))}</div><div class="muted small">${esc(h.handle)} · ${fmt(h.views)} views</div></div>${h.vsc != null ? `<b>${h.vsc}/30</b>` : ''}</li>`).join('')}</ul>`
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
};

/* ---------------- daily views ---------------- */
pages.views = async app => {
  const g = await api('GET', `/api/views?start=${state.weekStart}`);
  const start = parseISO(g.start);
  app.innerHTML = `
  <div class="page-head"><div><h1>Daily views</h1><div class="sub">Type numbers directly (85k works), or let AI read them from a screenshot.</div></div>
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
    <p class="muted small">Decision rules: SCALE = ${fmt(g.settings.scale_weekly)}+ weekly with a hook scored ${g.settings.scale_vsc}+. KILL = no post above ${fmt(g.settings.stall_views)} views in the last ${g.settings.kill_after_posts} posts. Change them in Accounts → Rules.</p>`
    : '<div class="empty card"><h2>No accounts yet</h2><p><a href="#/accounts">Add accounts or import your spreadsheet</a> to start tracking.</p></div>'}`;
  const go = n => { state.weekStart = iso(addDays(start, n)); route(); };
  $('#prev').onclick = () => go(-7); $('#next').onclick = () => go(7);
  $('#today').onclick = () => { state.weekStart = iso(mondayOf(new Date())); route(); };
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
  const p = post || { account_id: '', post_date: iso(new Date()), status: 'planned', ai_prompt: '', hook: '', body: '', caption: '', views: 0, comments: 0, intent: false, v: null, s: null, c: null, ...defaults };
  const m = openModal(`<h2>${post ? 'Edit post' : 'New post'}</h2><div class="fields">
    <div><label class="f">Account</label><select id="account">${accountOptions(p.account_id, 'Choose…')}</select></div>
    <div><label class="f">Date</label><input type="date" id="date" value="${esc(p.post_date)}"></div>
    <div><label class="f">Status</label><select id="status">${['idea', 'planned', 'posted'].map(s => `<option ${p.status === s ? 'selected' : ''}>${s}</option>`).join('')}</select></div>
    <div><label class="f">AI prompt / format</label><input id="prompt" value="${esc(p.ai_prompt)}" placeholder="Slideshow 5-pages, urgent tone"></div>
    <div class="full"><label class="f">Hook (overlay 1)</label><input id="hook" value="${esc(p.hook)}"></div>
    <div class="full"><label class="f">Body (overlay 2)</label><input id="body" value="${esc(p.body)}"></div>
    <div class="full"><label class="f">Caption + hashtags</label><textarea id="caption">${esc(p.caption)}</textarea></div>
    <div><label class="f">Views</label><input id="views" inputmode="numeric" value="${p.views || 0}"></div>
    <div><label class="f">Comments</label><input id="comments" inputmode="numeric" value="${p.comments || 0}"></div>
    <div class="full check"><input type="checkbox" id="intent" ${p.intent ? 'checked' : ''}><label for="intent">Intent — comments ask "what's the app?" / "where can I get this?"</label></div>
    <div class="full"><div class="score-box"><label class="f">VSC score (each /10)</label>
      <div class="score-row">${['v', 's', 'c'].map(k => `<div><label class="f">${{ v: 'V · hook pull', s: 'S · specificity', c: 'C · conversion' }[k]}</label><input id="${k}" type="number" min="0" max="10" value="${p[k] ?? ''}"></div>`).join('')}
      <div class="total"><span id="tot">–</span><div id="dec"></div></div></div>
      <div class="muted small" id="notes" style="margin-top:8px">${esc(p.score_notes)}</div></div></div></div>
    <div class="actions">${post ? '<button class="btn danger" id="del">Delete</button>' : ''}<span class="spacer"></span>
      <button class="btn ai" id="aiscore" ${state.ai.available ? '' : 'disabled'} title="${state.ai.available ? '' : 'AI is off'}">✨ AI score</button>
      <button class="btn" data-close>Cancel</button><button class="btn primary" id="save">Save</button></div>`);
  const calc = () => {
    const vals = ['v', 's', 'c'].map(k => $('#' + k, m).value);
    const t = vals.every(x => x !== '') ? vals.reduce((a, b) => a + +b, 0) : null;
    $('#tot', m).textContent = t == null ? '–' : `${t}/30`; $('#dec', m).innerHTML = pill(vscDecision(t));
  };
  ['v', 's', 'c'].forEach(k => $('#' + k, m).addEventListener('input', calc)); calc();

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
  $('#aiscore', m).onclick = guard(async () => {
    const btn = $('#aiscore', m);
    if (!$('#hook', m).value.trim()) throw new Error('Write a hook first');
    busy(btn, true, 'Scoring…');
    try {
      await save(); const r = await api('POST', `/api/ai/score/${post.id}`); post = r;
      ['v', 's', 'c'].forEach(k => ($('#' + k, m).value = r[k])); calc();
      $('#notes', m).textContent = r.score_notes; toast('Scored by AI');
    } finally { busy(btn, false); }
  });
  if (post) $('#del', m).onclick = guard(async () => {
    if (!confirm('Delete this post?')) return;
    await api('DELETE', `/api/posts/${post.id}`); m.close(); onDone && onDone();
  });
  return m;
}

/* ---------------- calendar ---------------- */
pages.calendar = async app => {
  const first = parseISO(state.calMonth);
  const gridStart = mondayOf(first);
  const gridEnd = addDays(gridStart, 41);
  const q = `start=${iso(gridStart)}&end=${iso(gridEnd)}` + (state.calAccount ? `&account_id=${state.calAccount}` : '');
  const posts = await api('GET', `/api/posts?${q}`);
  const by = {}; posts.forEach(p => (by[p.post_date] = by[p.post_date] || []).push(p));
  const today = iso(new Date());
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = addDays(gridStart, i), k = iso(d), list = (by[k] || []).slice().reverse();
    cells.push(`<div class="day ${d.getMonth() !== first.getMonth() ? 'out' : ''} ${k === today ? 'today' : ''}" data-day="${k}">
      <div class="dn"><span>${d.getDate()}</span>${list.length ? `<span>${list.length}</span>` : ''}</div>
      ${list.slice(0, 3).map(p => `<button class="chip ${p.status}" data-id="${p.id}" style="--c:${esc(p.phone_color || 'var(--accent)')}" title="${esc((p.handle || '') + ': ' + p.hook)}">${esc(short(p.hook, 24) || p.handle || '…')}</button>`).join('')}
      ${list.length > 3 ? `<div class="more">+${list.length - 3} more</div>` : ''}</div>`);
  }
  app.innerHTML = `<div class="page-head"><div><h1>Calendar</h1><div class="sub">Dashed = planned, solid = posted, faded = idea. Click a day to add a post.</div></div>
    <div class="row"><select id="cf" style="width:auto">${accountOptions(state.calAccount, 'All accounts')}</select>
      <button class="btn" id="prev">‹</button><b style="min-width:130px;text-align:center">${first.toLocaleString('default', { month: 'long', year: 'numeric' })}</b>
      <button class="btn" id="next">›</button><button class="btn" id="today">Today</button>
      <button class="btn ai" id="plan">✨ AI plan week</button></div></div>
    <div class="cal">${DOW.map(d => `<div class="dow">${d}</div>`).join('')}${cells.join('')}</div>`;
  const shift = n => { state.calMonth = iso(new Date(first.getFullYear(), first.getMonth() + n, 1)); route(); };
  $('#prev').onclick = () => shift(-1); $('#next').onclick = () => shift(1);
  $('#today').onclick = () => { state.calMonth = iso(new Date(new Date().getFullYear(), new Date().getMonth(), 1)); route(); };
  $('#cf').onchange = e => { state.calAccount = e.target.value; route(); };
  $('#plan').onclick = () => planModal();
  $$('.day', app).forEach(el => el.addEventListener('click', e => {
    const chip = e.target.closest('.chip');
    if (chip) { postModal(posts.find(p => p.id === +chip.dataset.id), null, route); return; }
    postModal(null, { post_date: el.dataset.day, account_id: state.calAccount || '' }, route);
  }));
};

function planModal() {
  const m = openModal(`<h2>✨ AI plan week</h2>${aiNotice()}
    <p class="muted">Drafts posts for every active account using your winning hooks. You choose which to add.</p>
    <div class="fields"><div><label class="f">Week starting (Monday)</label><input type="date" id="ps" value="${iso(mondayOf(new Date()))}"></div></div>
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
      await api('POST', '/api/posts/bulk', { posts: chosen });
      m.close(); toast(`Added ${chosen.length} posts`); state.calMonth = iso(parseISO(chosen[0].post_date.slice(0, 8) + '01')); route();
    });
  });
}

/* ---------------- hooks ---------------- */
pages.hooks = async app => {
  const q = [state.hookStatus && `status=${state.hookStatus}`, state.hookAccount && `account_id=${state.hookAccount}`].filter(Boolean).join('&');
  const posts = await api('GET', `/api/posts?${q}`);
  app.innerHTML = `<div class="page-head"><div><h1>Hooks</h1><div class="sub">Log every post, score it on VSC, and let the thresholds decide: SCALE ${state.settings.scale_vsc}+, MUTATE ${state.settings.mutate_vsc}–${state.settings.scale_vsc - 1}, KILL below.</div></div>
    <div class="row"><select id="fs" style="width:auto"><option value="">All statuses</option>${['idea', 'planned', 'posted'].map(s => `<option ${state.hookStatus === s ? 'selected' : ''}>${s}</option>`).join('')}</select>
      <select id="fa" style="width:auto">${accountOptions(state.hookAccount, 'All accounts')}</select>
      <button class="btn ai" id="ideas">✨ AI ideas</button><button class="btn primary" id="new">+ New post</button></div></div>
    ${posts.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Account</th><th>Hook</th><th>Status</th><th class="num">Views</th><th class="num">Comments</th><th>Intent</th><th class="num">VSC</th><th>Decision</th><th></th></tr></thead><tbody>
    ${posts.map(p => `<tr class="band" style="--band:${esc(p.phone_color || 'transparent')};cursor:pointer" data-id="${p.id}"><td>${esc(p.post_date)}</td><td class="handle">${esc(p.handle || '—')}</td>
      <td class="wrap">${esc(p.hook || '—')}<div class="muted small">${esc(short(p.ai_prompt, 60))}</div></td><td>${pill(p.status)}</td>
      <td class="num">${fmt(p.views)}</td><td class="num">${fmt(p.comments)}</td><td>${p.intent ? '✓' : ''}</td>
      <td class="num">${p.vsc == null ? '<span class="muted">—</span>' : p.vsc + '/30'}${p.score_by === 'ai' ? ' <span title="Scored by AI">✨</span>' : ''}</td><td>${pill(p.decision)}</td>
      <td><button class="btn sm" data-score="${p.id}" ${state.ai.available && p.hook ? '' : 'disabled'} title="AI score">✨</button></td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty card"><h2>No posts yet</h2><p>Add your first post, or generate ideas with AI.</p></div>'}`;
  $('#fs').onchange = e => { state.hookStatus = e.target.value; route(); };
  $('#fa').onchange = e => { state.hookAccount = e.target.value; route(); };
  $('#new').onclick = () => postModal(null, { status: 'posted', account_id: state.hookAccount || '' }, route);
  $('#ideas').onclick = () => ideasModal();
  $$('tr[data-id]', app).forEach(tr => tr.addEventListener('click', e => {
    if (e.target.closest('[data-score]')) return;
    postModal(posts.find(p => p.id === +tr.dataset.id), null, route);
  }));
  $$('[data-score]', app).forEach(b => b.addEventListener('click', guard(async () => {
    busy(b, true); try { await api('POST', `/api/ai/score/${b.dataset.score}`); } finally { busy(b, false); }
    toast('Scored by AI'); route();
  })));
};

function ideasModal() {
  const m = openModal(`<h2>✨ AI hook ideas</h2>${aiNotice()}
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
      <div class="row" style="margin-top:8px"><input type="date" data-d="${i}" value="${iso(new Date())}" style="width:auto"><button class="btn sm primary" data-add="${i}">Add to calendar</button></div></div>`).join('');
    $$('[data-add]', m).forEach(b => (b.onclick = guard(async () => {
      const x = r.ideas[+b.dataset.add];
      await api('POST', '/api/posts', { account_id: +acc, post_date: $(`[data-d="${b.dataset.add}"]`, m).value, status: 'planned',
        ai_prompt: x.ai_prompt || '', hook: x.hook, body: x.body || '', caption: x.caption || '' });
      b.textContent = 'Added ✓'; b.disabled = true;
    })));
  });
}

/* ---------------- accounts ---------------- */
pages.accounts = async app => {
  const groups = state.phones.map(p => ({ p, rows: state.accounts.filter(a => a.phone_id === p.id) }));
  const loose = state.accounts.filter(a => !a.phone_id);
  if (loose.length) groups.push({ p: { id: null, label: 'No phone', color: '#94a3b8' }, rows: loose });
  app.innerHTML = `<div class="page-head"><div><h1>Accounts</h1><div class="sub">${state.accounts.length} accounts across ${state.phones.length} phones</div></div>
    <div class="row"><button class="btn" id="addphone">+ Phone</button><button class="btn primary" id="addacc">+ Account</button></div></div>
    ${groups.map(({ p, rows }) => `<div class="card" style="margin-bottom:14px"><div class="row" style="margin-bottom:8px">
      <span class="dot" style="background:${esc(p.color)}"></span><b>${esc(p.label)}</b><span class="muted">${rows.length} accounts</span><span class="spacer"></span>
      ${p.id ? `<button class="btn sm" data-ep="${p.id}">Edit</button>` : ''}</div>
      ${rows.length ? `<div class="table-wrap" style="box-shadow:none"><table><thead><tr><th>Handle</th><th>Platform</th><th>Niche</th><th>Status</th><th class="num">Posts/day</th><th>Content hook</th><th>Model</th><th></th></tr></thead><tbody>
      ${rows.map(a => `<tr><td class="handle">${esc(a.handle)}</td><td>${esc(a.platform)}</td><td>${esc(a.niche)}</td><td>${pill(a.status, a.status === 'active' ? 'posted' : a.status === 'killed' ? 'KILL' : 'HOLD')}</td>
        <td class="num">${a.times_per_day}</td><td class="muted">${esc(a.content_hook)}</td><td class="muted">${esc(a.model)}</td><td><button class="btn sm" data-ea="${a.id}">Edit</button></td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">No accounts on this phone.</p>'}</div>`).join('')}
    ${state.accounts.length ? '' : '<div class="empty card"><h2>No accounts yet</h2><p>Add a phone and some accounts, or import your spreadsheet below.</p></div>'}
    <div class="grid cols-2">    <div class="card"><h2>Import from spreadsheet</h2><p class="muted">Upload "The Ugly Tracker" workbook (.xlsx). Accounts, daily views and the hook log are imported; re-importing never duplicates. The workbook has no dates for Day 1–7, so choose the Monday they belong to.</p>
      <div class="fields"><div><label class="f">Week starting (Monday)</label><input type="date" id="iw" value="${iso(mondayOf(new Date()))}"></div>
      <div><label class="f">File</label><input type="file" id="ifile" accept=".xlsx"></div></div>
      <div class="actions"><button class="btn primary" id="import">Import</button></div></div></div>`;

  $('#addphone').onclick = () => phoneModal();
  $('#addacc').onclick = () => accountModal();
  $$('[data-ep]', app).forEach(b => (b.onclick = () => phoneModal(state.phones.find(p => p.id === +b.dataset.ep))));
  $$('[data-ea]', app).forEach(b => (b.onclick = () => accountModal(state.accounts.find(a => a.id === +b.dataset.ea))));
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
  const x = a || { handle: '', phone_id: state.phones[0] ? state.phones[0].id : '', platform: 'TikTok', niche: '', status: 'active', times_per_day: 1, content_hook: '', model: '' };
  const m = openModal(`<h2>${a ? 'Edit account' : 'New account'}</h2><div class="fields">
    <div><label class="f">Handle</label><input id="h" value="${esc(x.handle)}" placeholder="@account_1a"></div>
    <div><label class="f">Phone</label><select id="ph"><option value="">None</option>${state.phones.map(p => `<option value="${p.id}" ${p.id === x.phone_id ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></div>
    <div><label class="f">Platform</label><input id="pf" value="${esc(x.platform)}"></div>
    <div><label class="f">Niche</label><input id="n" value="${esc(x.niche)}"></div>
    <div><label class="f">Status</label><select id="st">${['active', 'warming', 'paused', 'killed'].map(s => `<option ${x.status === s ? 'selected' : ''}>${s}</option>`).join('')}</select></div>
    <div><label class="f">Posts per day</label><input id="t" type="number" min="0" max="50" value="${x.times_per_day}"></div>
    <div><label class="f">Content hook style</label><input id="ch" value="${esc(x.content_hook)}"></div>
    <div><label class="f">AI model / tool</label><input id="md" value="${esc(x.model)}"></div></div>
    <div class="actions">${a ? '<button class="btn danger" id="del">Delete</button>' : ''}<span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="save">Save</button></div>`);
  $('#save', m).onclick = guard(async () => {
    const b = { handle: $('#h', m).value.trim(), phone_id: $('#ph', m).value ? +$('#ph', m).value : null, platform: $('#pf', m).value, niche: $('#n', m).value,
      status: $('#st', m).value, times_per_day: +$('#t', m).value || 0, content_hook: $('#ch', m).value, model: $('#md', m).value };
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
  const [members, cfg, invites] = await Promise.all([
    api('GET', '/api/workspace/members'),
    admin ? api('GET', '/api/ai/config') : null,
    admin ? api('GET', '/api/workspace/invites') : [],
  ]);
  const S = state.settings, me = state.me;
  const roleSel = (m) => `<select data-role="${m.id}" style="width:auto" ${admin ? '' : 'disabled'}>${['owner', 'admin', 'member'].map(r => `<option ${m.role === r ? 'selected' : ''}>${r}</option>`).join('')}</select>`;

  app.innerHTML = `<div class="page-head"><div><h1>Settings</h1><div class="sub">${esc(me.workspace.name)} · you are ${esc(me.workspace.role)}</div></div></div>
  <div class="grid" style="max-width:860px">

  <div class="card" id="ai-card"><h2>✨ AI</h2>
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

  <div class="card"><h2>Decision rules</h2><div class="fields">
    ${[['scale_weekly', 'Weekly views to SCALE'], ['scale_vsc', 'VSC to SCALE (of 30)'], ['mutate_vsc', 'VSC to MUTATE (of 30)'], ['kill_after_posts', 'Posts before a stall = KILL'], ['stall_views', 'Views that count as a breakout']]
      .map(([k, l]) => `<div><label class="f">${l}</label><input data-s="${k}" type="number" min="0" value="${S[k]}" ${admin ? '' : 'disabled'}></div>`).join('')}</div>
    ${admin ? '<div class="actions"><button class="btn primary" id="saverules">Save rules</button></div>' : ''}</div>

  <div class="card"><h2>Workspace</h2>
    ${admin ? `<div class="row" style="margin-bottom:12px"><input id="ws-name" value="${esc(me.workspace.name)}" maxlength="100" style="max-width:320px"><button class="btn" id="ws-rename">Rename</button></div>` : ''}
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
  </div>`;

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
  $('#pw-go').onclick = guard(async () => {
    await api('POST', '/api/auth/password', { current: $('#pw-cur').value, new: $('#pw-new').value });
    $('#pw-cur').value = ''; $('#pw-new').value = ''; toast('Password changed');
  });
};

route();

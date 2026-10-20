/* HookTrack Platform Admin: standalone console */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = n => (n == null ? '—' : Number(n).toLocaleString());
const date = d => (d ? new Date(d).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—');
const money = (minor, cur) => {
  try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur || 'USD' }).format((minor || 0) / 100); }
  catch { return `${(minor || 0) / 100} ${cur || ''}`; }
};
const lim = n => (n === -1 ? 'Unlimited' : fmt(n));
let me = null;

async function api(method, path, body) {
  const opt = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch('/api' + path, opt);
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== '/auth/login') { showGate(); throw new Error('Signed out'); }
  if (!r.ok) {
    const d = data.detail;
    throw new Error(typeof d === 'string' ? d : (Array.isArray(d) ? d.map(x => x.msg).join('; ') : r.statusText));
  }
  return data;
}
let toastT;
function toast(msg, err = false) {
  const t = $('#toast'); t.textContent = msg; t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toastT); toastT = setTimeout(() => (t.className = ''), 3500);
}
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };
function openModal(html) {
  const m = $('#modal'); m.innerHTML = html;
  $$('[data-close]', m).forEach(b => (b.onclick = () => m.close()));
  if (!m.open) m.showModal();
  return m;
}

/* In-app confirm / text prompts (native dialogs are suppressed in some embedded browsers). Own <dialog>, so it can
   sit on top of the shared #modal without replacing its content. */
function askConfirm(message, opts = {}) {
  return new Promise(resolve => {
    const d = document.createElement('dialog');
    d.className = 'modal';
    d.innerHTML = `<h2 style="margin-top:0">${esc(opts.title || 'Please confirm')}</h2>
      <p style="white-space:pre-wrap">${esc(message)}</p>
      <div class="actions"><span class="spacer"></span><button class="btn" type="button" id="a-no">Cancel</button>
        <button class="btn ${opts.danger === false ? 'primary' : 'danger'}" type="button" id="a-yes">${esc(opts.ok || 'Confirm')}</button></div>`;
    document.body.appendChild(d);
    let out = false;
    d.querySelector('#a-yes').onclick = () => { out = true; d.close(); };
    d.querySelector('#a-no').onclick = () => d.close();
    d.addEventListener('close', () => { d.remove(); resolve(out); });
    d.showModal();
  });
}
function askText(message, opts = {}) {
  return new Promise(resolve => {
    const d = document.createElement('dialog');
    d.className = 'modal';
    d.innerHTML = `<h2 style="margin-top:0">${esc(opts.title || 'Enter a value')}</h2>
      <p style="white-space:pre-wrap">${esc(message)}</p>
      <input id="a-in" type="${opts.password ? 'password' : 'text'}" value="${esc(opts.value || '')}" style="width:100%" autocomplete="off">
      <div class="actions"><span class="spacer"></span><button class="btn" type="button" id="a-no">Cancel</button>
        <button class="btn primary" type="button" id="a-ok">${esc(opts.ok || 'OK')}</button></div>`;
    document.body.appendChild(d);
    let out = null;
    const go = () => { out = d.querySelector('#a-in').value; d.close(); };
    d.querySelector('#a-ok').onclick = go;
    d.querySelector('#a-no').onclick = () => d.close();
    d.querySelector('#a-in').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
    d.addEventListener('close', () => { d.remove(); resolve(out); });
    d.showModal();
  });
}
const planOptions = (plans, sel) => plans.map(p => `<option value="${esc(p.code)}" ${p.code === sel ? 'selected' : ''}>${esc(p.name)} (${esc(p.code)})</option>`).join('');

/* ---------- gate (sign in) ---------- */
function showGate() {
  $('#shell').hidden = true;
  const g = $('#gate'); g.hidden = false;
  g.innerHTML = `<form class="card" id="loginf"><h1 style="margin:0 0 4px">Platform Admin</h1>
    <p class="muted" style="margin:0 0 8px">Sign in with your operator account.</p>
    <label class="f">Email</label><input id="em" type="email" autocomplete="username" required>
    <label class="f">Password</label><input id="pw" type="password" autocomplete="current-password" required>
    <div class="actions"><button class="btn primary" style="width:100%">Sign in</button></div>
    <p id="gerr" class="small" style="color:var(--bad);min-height:1em"></p></form>`;
  $('#loginf').onsubmit = async e => {
    e.preventDefault();
    try { me = (await api('POST', '/auth/login', { email: $('#em').value, password: $('#pw').value })); boot(); }
    catch (err) { $('#gerr').textContent = err.message; }
  };
}

/* ---------- pages ---------- */
const pages = {};

function vaultBanner(v) {
  if (!v) return '';
  const out = [];
  const left = v.credits != null ? `${fmt(v.credits)} credits left${v.percent_left != null ? ` (${v.percent_left}% of the last top-up)` : ''}` : '';
  if (v.status === 'exhausted') out.push(['bad', `<b>SociaVault credits are used up.</b> Syncing is down for every customer. Top up at sociavault.com.`]);
  else if (v.status === 'paused') out.push(['bad', `<b>SociaVault credits nearly gone: scheduled syncs are paused.</b> ${esc(left)}. Top up to resume.`]);
  else if (v.status === 'low') out.push(['warn', `<b>SociaVault credits are running low.</b> ${esc(left)}.`]);
  else if (v.status === 'unknown' && v.error) out.push(['warn', `Could not read the SociaVault balance: ${esc(v.error)}`]);
  if (v.failing && v.failing.alerting) out.push(['bad', `<b>Syncing is failing for ${fmt(v.failing.count)} of ${fmt(v.failing.total)} accounts.</b> ${esc((v.failing.samples[0] || {}).sync_error || '')}`]);
  return out.map(([k, h]) => `<div class="card" style="margin-bottom:12px;border-color:var(--${k === 'bad' ? 'bad' : 'warn'})">${h} <a href="#/settings">Details</a></div>`).join('');
}

pages.overview = async app => {
  const o = await api('GET', '/overview');
  const c = o.counts;
  const mrr = Object.entries(o.mrr_minor).map(([cur, v]) => money(v, cur)).join(' + ') || money(0, 'USD');
  const rev = o.revenue_30d.map(r => money(r.minor, r.currency)).join(' + ') || '—';
  app.innerHTML = `<div class="page-head"><div><h1>Overview</h1><div class="sub">Whole-platform numbers</div></div></div>
    ${vaultBanner(o.vault)}
    ${o.legal_configured ? '' : `<div class="card" style="margin-bottom:12px;border-color:var(--warn)">Your public <b>Terms and Privacy</b> pages are live but your company name and contact email are not filled in. Add them under <a href="#/settings">Settings</a>.</div>`}
    ${o.billing.enabled ? '' : `<div class="card" style="margin-bottom:12px;border-color:var(--warn)">Billing is <b>off</b>: nobody is limited and plans are not shown to customers. Turn it on under <a href="#/settings">Settings</a>.</div>`}
    ${o.billing.enabled && !o.billing.paystack.configured ? `<div class="card" style="margin-bottom:12px;border-color:var(--warn)">Billing is on but no Paystack key is set, so paid plans cannot be bought yet. <a href="#/settings">Add it</a>.</div>` : ''}
    <div class="grid kpis">
      <div class="card kpi"><div class="label">Monthly recurring</div><div class="value">${esc(mrr)}</div></div>
      <div class="card kpi"><div class="label">Revenue, 30 days</div><div class="value">${esc(rev)}</div></div>
      <div class="card kpi"><div class="label">Paying workspaces</div><div class="value">${fmt(c.paid_workspaces)}</div></div>
      <div class="card kpi"><div class="label">Workspaces</div><div class="value">${fmt(c.workspaces)}</div></div>
      <div class="card kpi"><div class="label">Users</div><div class="value">${fmt(c.users)}</div></div>
      <div class="card kpi"><div class="label">Accounts tracked</div><div class="value">${fmt(c.accounts)}</div></div>
      <div class="card kpi"><div class="label">AI calls this month</div><div class="value">${fmt(c.ai_calls_this_month)}</div></div>
    </div>
    <div class="grid two" style="margin-top:12px">
      <div class="card"><h2>Workspaces by plan / state</h2>${o.plan_distribution.map(d => `<div class="row"><span style="flex:1">${esc(d.plan)}</span><b>${fmt(d.n)}</b></div>`).join('') || '<span class="muted">None yet</span>'}</div>
      <div class="card"><h2>Newest workspaces</h2>${o.recent_workspaces.map(w => `<div class="row"><a href="#/workspaces/${w.id}" style="flex:1">${esc(w.name)}</a><span class="muted small">${date(w.created_at)}</span></div>`).join('') || '<span class="muted">None yet</span>'}</div>
    </div>`;
};

pages.funnel = async app => {
  const days = +(pages._fd ?? 30);
  const f = await api('GET', '/funnel?days=' + days);
  const max = Math.max(1, f.total);
  app.innerHTML = `<div class="page-head"><div><h1>Activation funnel</h1><div class="sub">Where new workspaces stop. Each step is the first time a workspace did it.</div></div>
      <select id="fd" style="width:auto">${[[7, 'Signed up in the last 7 days'], [30, 'Last 30 days'], [90, 'Last 90 days'], [0, 'All time']]
        .map(([v, t]) => `<option value="${v}" ${v === days ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
    ${f.total ? `<div class="card">${f.steps.map((s, i) => `<div style="margin:0 0 12px">
        <div class="row" style="gap:8px"><b style="flex:1">${esc(s.label)}</b><span><b>${fmt(s.reached)}</b> <span class="muted small">${s.pct_of_signups}% of signups${i ? ` · ${s.pct_of_previous}% of the step before` : ''}${s.median_hours != null ? ` · median ${s.median_hours < 48 ? s.median_hours + ' h' : Math.round(s.median_hours / 24) + ' days'} after signup` : ''}</span></span></div>
        <div style="height:10px;border-radius:6px;background:var(--line,#e5e7eb);margin-top:4px"><div style="height:10px;border-radius:6px;background:var(--accent,#6366f1);width:${Math.round(100 * s.reached / max)}%"></div></div></div>`).join('')}</div>
      <div class="card" style="margin-top:12px"><h2>Stuck workspaces <span class="muted small">${fmt(f.stuck_total)} not finished, oldest first</span></h2>
        ${f.stuck.map(w => `<div class="row"><a href="#/workspaces/${w.id}" style="flex:1">${esc(w.name)}</a><span class="muted small">waiting to: ${esc(w.waiting_label.toLowerCase())} · ${w.age_days} days old</span></div>`).join('') || '<span class="muted">Nobody is stuck</span>'}</div>`
      : '<div class="card muted">No workspaces in this period yet.</div>'}`;
  $('#fd').onchange = e => { pages._fd = e.target.value; route(); };
};

pages.workspaces = async app => {
  const sub = (location.hash.split('/')[2] || '');
  const q = pages._wq || '';
  const r = await api('GET', '/workspaces?q=' + encodeURIComponent(q));
  app.innerHTML = `<div class="page-head"><div><h1>Workspaces</h1><div class="sub">Every customer workspace and its plan</div></div>
    <input id="q" placeholder="Search by name…" value="${esc(q)}" style="max-width:260px"></div>
    <div class="table-wrap"><table><thead><tr><th>Name</th><th>Plan</th><th>Until</th><th class="num">Accounts</th><th class="num">Members</th><th class="num">Posts</th><th>Created</th></tr></thead><tbody>
    ${r.workspaces.map(w => `<tr data-id="${w.id}"><td><b>${esc(w.name)}</b></td>
      <td><span class="badge ${w.plan_code === 'expired' ? 'bad' : w.plan_code === 'trial' ? 'warn' : 'accent'}">${esc(w.plan_code)}</span>${w.sub_status === 'comp' ? ' <span class="badge warn">comp</span>' : ''}</td>
      <td>${['trial', 'expired'].includes(w.plan_code) ? '—' : date(w.current_period_end)}</td><td class="num">${fmt(w.accounts)}</td><td class="num">${fmt(w.members)}</td><td class="num">${fmt(w.posts)}</td><td>${date(w.created_at)}</td></tr>`).join('')
      || '<tr><td colspan="7" class="empty">No workspaces</td></tr>'}</tbody></table></div>`;
  let t; $('#q').oninput = e => { clearTimeout(t); t = setTimeout(() => { pages._wq = e.target.value; route(); }, 300); };
  $$('tr[data-id]', app).forEach(tr => (tr.onclick = () => workspaceModal(+tr.dataset.id)));
  if (sub && /^\d+$/.test(sub)) { history.replaceState(null, '', '#/workspaces'); workspaceModal(+sub); }
};

async function workspaceModal(id) {
  const d = await api('GET', `/workspaces/${id}`);
  const b = d.billing, w = d.workspace;
  const meter = (used, limit, label) => `<div style="margin-top:8px"><div class="row"><span class="small muted" style="flex:1">${label}</span><span class="small">${fmt(used)} / ${lim(limit)}</span></div>
    <div class="bar ${limit !== -1 && used >= limit ? 'full' : ''}"><i style="width:${limit === -1 || !limit ? 0 : Math.min(100, used / limit * 100)}%"></i></div></div>`;
  const m = openModal(`<h2 style="margin:0">${esc(w.name)}</h2><div class="muted small">Workspace #${w.id} · created ${date(w.created_at)} · ${esc(w.timezone || '')}</div>
    <div class="card" style="margin-top:12px"><h2>Plan</h2>
      <div>${b.state === 'trial' ? `<span class="badge warn">Free trial</span> <span class="muted small">${b.trial.days_left} day${b.trial.days_left === 1 ? '' : 's'} left, ends ${date(b.trial.ends_at)}</span>`
        : b.state === 'expired' ? `<span class="badge bad">Expired</span> <span class="muted small">${b.subscription.lapsed ? 'paid period ended ' + date(b.subscription.current_period_end) : b.trial.reason === 'trial_used' ? 'its owner already used a free trial on another workspace' : 'trial ended ' + date(b.trial.ends_at)}: read-only until they pay</span>`
        : b.state === 'off' ? '<span class="badge">Billing is off</span>'
        : `<span class="badge accent">${esc(b.plan.name)}</span> ${b.state === 'comp' ? '<span class="badge warn">comp</span>' : ''} <span class="muted small">${b.subscription.current_period_end ? 'until ' + date(b.subscription.current_period_end) : 'no end date'}</span>`}</div>
      ${meter(b.usage.accounts, b.plan.max_accounts, 'Accounts')}${meter(b.usage.members, b.plan.max_members, 'Members')}${meter(b.usage.ai_calls, b.plan.max_ai_calls, 'AI requests this month')}
      <div class="fields" style="margin-top:6px">
        <div><label class="f">Set plan</label><select id="sp">${planOptions(d.plans, b.subscription.plan_code)}</select></div>
        <div><label class="f">Type</label><select id="ss"><option value="comp">Complimentary (comp)</option><option value="active">Paid / manual payment</option></select></div>
        <div><label class="f">Days (blank = a full interval, or no end for comp)</label><input id="sd" type="number" min="1" placeholder="e.g. 30"></div>
        <div><label class="f">Note</label><input id="sn" maxlength="300" value="${esc(b.subscription.note || '')}"></div>
      </div>
      <div class="actions"><button class="btn primary" id="savesub">Apply plan</button></div>
      ${['trial', 'expired'].includes(b.state) && !b.subscription.lapsed ? `<div class="row" style="margin-top:10px;gap:8px"><span class="small muted">Trial:</span>
        <input id="td" type="number" min="0" max="365" value="7" style="max-width:90px"><button class="btn sm" id="settrial">Set trial to end in N days</button></div>` : ''}
      ${b.card && b.card.has_card ? `<div class="small" style="margin-top:10px">Saved card: <b>${esc(b.card.brand)} •••• ${esc(b.card.last4)}</b> ${b.card.exp ? '(exp ' + esc(b.card.exp) + ')' : ''} ·
        ${b.card.auto_renew ? 'auto-renew <span class="badge good">on</span>' + (b.card.next_charge_at ? ' · next charge ' + date(b.card.next_charge_at) : '') : 'auto-renew <span class="badge">off</span>'}
        ${b.card.renew_error ? `<div style="color:var(--bad)">Last renewal error: ${esc(b.card.renew_error)}</div>` : ''}
        <button class="btn sm" id="stopcard" style="margin-left:8px">Remove card</button></div>` : ''}</div>
    <div class="grid two" style="margin-top:12px">
      <div class="card"><h2>Members (${d.members.length})</h2>${d.members.map(u => `<div class="row small"><span style="flex:1">${esc(u.email)}</span><span class="badge">${esc(u.role)}</span></div>`).join('')}</div>
      <div class="card"><h2>Payments</h2>${d.payments.map(p => `<div class="row small"><span style="flex:1">${esc(p.plan_code)} · ${money(p.amount_minor, p.currency)}</span><span class="badge ${p.status === 'success' ? 'good' : p.status === 'failed' ? 'bad' : ''}">${esc(p.status)}</span></div>`).join('') || '<span class="muted small">None</span>'}</div>
    </div>
    <div class="actions"><button class="btn" id="rename">Rename</button><button class="btn danger" id="del">Delete workspace…</button><span class="spacer"></span><button class="btn" data-close>Close</button></div>`);
  $('#savesub', m).onclick = guard(async () => {
    const days = $('#sd', m).value;
    await api('PUT', `/workspaces/${id}/subscription`, { plan_code: $('#sp', m).value, status: $('#ss', m).value,
      days: days ? +days : null, note: $('#sn', m).value });
    toast('Plan updated'); m.close(); route();
  });
  if ($('#settrial', m)) $('#settrial', m).onclick = guard(async () => {
    await api('PUT', `/workspaces/${id}/trial`, { days: +$('#td', m).value }); toast('Trial updated'); m.close(); route();
  });
  if ($('#stopcard', m)) $('#stopcard', m).onclick = guard(async () => {
    await api('DELETE', `/workspaces/${id}/card`); toast('Card removed'); m.close(); route();
  });
  $('#rename', m).onclick = guard(async () => {
    const n = await askText('New name', {title: 'Rename workspace', value: w.name}); if (!n) return;
    await api('PATCH', `/workspaces/${id}`, { name: n }); toast('Renamed'); m.close(); route();
  });
  $('#del', m).onclick = guard(async () => {
    if ((await askText(`This permanently deletes "${w.name}" and ALL its data. Type DELETE to confirm.`, {title: 'Delete workspace', ok: 'Delete'})) !== 'DELETE') return;
    await api('DELETE', `/workspaces/${id}`); toast('Workspace deleted'); m.close(); route();
  });
}

pages.plans = async app => {
  const r = await api('GET', '/plans');
  app.innerHTML = `<div class="page-head"><div><h1>Plans</h1><div class="sub">Price changes apply at each customer's next payment. Existing paid periods are never changed. -1 means unlimited.</div></div>
    <button class="btn primary" id="newplan">+ New plan</button></div>
    <div class="table-wrap"><table><thead><tr><th>Plan</th><th class="num">Price</th><th class="num">Accounts</th><th class="num">Members</th><th class="num">AI / month</th><th class="num">Connected</th><th class="num">Credits / month</th><th class="num">Subscribers</th><th>Status</th></tr></thead><tbody>
    ${r.plans.map(p => `<tr data-id="${esc(p.code)}"><td><b>${esc(p.name)}</b><div class="muted small">${esc(p.code)}${p.description ? ' · ' + esc(p.description) : ''}</div></td>
      <td class="num">${money(p.price_minor, p.currency)} / ${esc(p.interval)}</td><td class="num">${lim(p.max_accounts)}</td><td class="num">${lim(p.max_members)}</td><td class="num">${lim(p.max_ai_calls)}</td><td class="num">${lim(p.max_connected)}</td><td class="num">${lim(p.max_credits)}</td>
      <td class="num">${fmt(p.subscribers)}</td><td><span class="badge ${p.active ? 'good' : ''}">${p.active ? 'on sale' : 'hidden'}</span></td></tr>`).join('')}</tbody></table></div>`;
  $$('tr[data-id]', app).forEach(tr => (tr.onclick = () => planModal(r.plans.find(p => p.code === tr.dataset.id))));
  $('#newplan').onclick = () => planModal(null);
};

function planModal(p) {
  const isNew = !p; p = p || { code: '', name: '', description: '', price_minor: 0, currency: 'USD', interval: 'month', max_accounts: 3, max_members: 2, max_ai_calls: 50, max_connected: 1, max_credits: 150, max_reports: 0, active: true, position: 10 };
  const m = openModal(`<h2 style="margin:0 0 6px">${isNew ? 'New plan' : 'Edit ' + esc(p.name)}</h2>
    <div class="fields">
      <div><label class="f">Code (permanent)</label><input id="code" value="${esc(p.code)}" ${isNew ? '' : 'disabled'} placeholder="team"></div>
      <div><label class="f">Name</label><input id="name" value="${esc(p.name)}"></div>
      <div><label class="f">Price (${esc(p.currency)})</label><input id="price" type="number" min="0" step="0.01" value="${(p.price_minor / 100).toFixed(2)}"></div>
      <div><label class="f">Currency (3 letters)</label><input id="cur" maxlength="3" value="${esc(p.currency)}"></div>
      <div><label class="f">Billed every</label><select id="int"><option value="month" ${p.interval === 'month' ? 'selected' : ''}>month</option><option value="year" ${p.interval === 'year' ? 'selected' : ''}>year</option></select></div>
      <div><label class="f">Sort order</label><input id="pos" type="number" value="${p.position}"></div>
      <div><label class="f">Accounts (-1 = unlimited)</label><input id="ma" type="number" min="-1" value="${p.max_accounts}"></div>
      <div><label class="f">Team members (-1 = unlimited)</label><input id="mm" type="number" min="-1" value="${p.max_members}"></div>
      <div><label class="f">AI requests / month (-1 = unlimited)</label><input id="mi" type="number" min="-1" value="${p.max_ai_calls}"></div>
      <div><label class="f">Connected publishing accounts (-1 = unlimited)</label><input id="mc" type="number" min="-1" value="${p.max_connected}"></div>
      <div><label class="f">Shareable client reports (0 = none, -1 = unlimited)</label><input id="mrp" type="number" min="-1" value="${p.max_reports ?? 0}"></div>
      <div><label class="f">Data credits / month (-1 = unlimited)</label><input id="mcr" type="number" min="-1" value="${p.max_credits}"></div>
    </div>
    <label class="f">Description (shown to customers)</label><input id="desc" maxlength="300" value="${esc(p.description)}">
    <label class="check"><input type="checkbox" id="act" ${p.active ? 'checked' : ''}> On sale (customers can choose it)</label>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="saveplan">Save</button></div>`);
  $('#saveplan', m).onclick = guard(async () => {
    const body = { name: $('#name', m).value, description: $('#desc', m).value, price_minor: Math.round(parseFloat($('#price', m).value || '0') * 100),
      currency: $('#cur', m).value.toUpperCase(), interval: $('#int', m).value, max_accounts: +$('#ma', m).value, max_members: +$('#mm', m).value,
      max_ai_calls: +$('#mi', m).value, max_connected: +$('#mc', m).value, max_credits: +$('#mcr', m).value, max_reports: +$('#mrp', m).value,
      active: $('#act', m).checked, position: +$('#pos', m).value };
    if (isNew) await api('POST', '/plans', { ...body, code: $('#code', m).value });
    else await api('PUT', `/plans/${p.code}`, body);
    toast('Plan saved'); m.close(); route();
  });
}

pages.payments = async app => {
  const f = pages._pf || '';
  const r = await api('GET', '/payments' + (f ? '?status=' + f : ''));
  app.innerHTML = `<div class="page-head"><div><h1>Payments</h1><div class="sub">Every checkout attempt</div></div>
    <select id="pf" style="max-width:180px"><option value="">All</option><option value="success">Paid</option><option value="pending">Pending</option><option value="failed">Failed</option></select></div>
    <div class="table-wrap"><table><thead><tr><th>Date</th><th>Workspace</th><th>Plan</th><th>Type</th><th class="num">Amount</th><th>Payer</th><th>Status</th><th>Reference</th></tr></thead><tbody>
    ${r.payments.map(p => `<tr><td>${date(p.created_at)}</td><td>${esc(p.workspace_name || '—')}</td><td>${esc(p.plan_code)}</td><td>${p.kind === 'renewal' ? '<span class="badge accent">auto-renewal</span>' : 'checkout'}</td><td class="num">${money(p.amount_minor, p.currency)}</td>
      <td>${esc(p.payer_email)}</td><td><span class="badge ${p.status === 'success' ? 'good' : p.status === 'failed' ? 'bad' : 'warn'}">${esc(p.status)}</span></td><td><code>${esc(p.reference)}</code></td></tr>`).join('')
      || '<tr><td colspan="8" class="empty">No payments yet</td></tr>'}</tbody></table></div>`;
  $('#pf').value = f; $('#pf').onchange = e => { pages._pf = e.target.value; route(); };
};

pages.users = async app => {
  const q = pages._uq || '';
  const r = await api('GET', '/users?q=' + encodeURIComponent(q));
  app.innerHTML = `<div class="page-head"><div><h1>Users</h1><div class="sub">Customer accounts across all workspaces</div></div>
    <input id="q" placeholder="Search email or name…" value="${esc(q)}" style="max-width:260px"></div>
    <div class="table-wrap"><table><thead><tr><th>Email</th><th>Name</th><th class="num">Workspaces</th><th>Joined</th></tr></thead><tbody>
    ${r.users.map(u => `<tr data-id="${u.id}"><td>${esc(u.email)}</td><td>${esc(u.name)}</td><td class="num">${fmt(u.workspaces)}</td><td>${date(u.created_at)}</td></tr>`).join('')
      || '<tr><td colspan="4" class="empty">No users</td></tr>'}</tbody></table></div>`;
  let t; $('#q').oninput = e => { clearTimeout(t); t = setTimeout(() => { pages._uq = e.target.value; route(); }, 300); };
  $$('tr[data-id]', app).forEach(tr => (tr.onclick = guard(async () => {
    const d = await api('GET', `/users/${tr.dataset.id}`);
    const m = openModal(`<h2 style="margin:0">${esc(d.user.email)}</h2><div class="muted small">${esc(d.user.name)} · joined ${date(d.user.created_at)}</div>
      <div class="card" style="margin-top:12px"><h2>Workspaces</h2>${d.memberships.map(x => `<div class="row"><a href="#/workspaces/${x.id}" data-close style="flex:1">${esc(x.name)}</a><span class="badge">${esc(x.role)}</span></div>`).join('') || '<span class="muted">None</span>'}</div>
      <div class="actions"><button class="btn danger" id="deluser">Delete this account…</button><span class="spacer"></span><button class="btn" data-close>Close</button></div>`);
    $('#deluser', m).onclick = guard(async () => {
      const plan = await api('GET', `/users/${d.user.id}/deletion-preview`);
      const lines = plan.workspaces.map(w => `${w.action === 'delete' ? 'DELETES' : w.action === 'blocked' ? 'BLOCKED (sole owner, others use it)' : 'leaves'}: ${w.name}`).join('\n');
      let shared = false;
      if (plan.blocked.length) {
        shared = await askConfirm(`${d.user.email} is the only owner of workspaces other people use:\n\n${plan.blocked.map(w => w.name).join('\n')}\n\nDelete those workspaces for everyone too? (Cancel to stop.)`);
        if (!shared) return;
      }
      if ((await askText(`Permanently delete ${d.user.email}?\n\n${lines}\n\nType DELETE to confirm.`, {title: 'Delete account', ok: 'Delete'})) !== 'DELETE') return;
      await api('DELETE', `/users/${d.user.id}`, { delete_shared_workspaces: shared });
      toast('Account deleted'); m.close(); route();
    });
  })));
};

pages.settings = async app => {
  const s = await api('GET', '/settings');
  const b = s.billing, sv = s.sociavault;
  app.innerHTML = `<div class="page-head"><div><h1>Settings</h1><div class="sub">Platform-wide configuration</div></div></div>
    <div class="card"><h2>Billing</h2>
      <label class="check" style="margin-top:0"><input type="checkbox" id="ben" ${b.enabled ? 'checked' : ''}> Enforce plan limits and show plans to customers</label>
      <p class="small muted" style="margin:6px 0 0">Off: nobody is limited and the Billing page says so. Existing workspaces above a limit are never cut off; they simply cannot add more.</p>
      <label class="f">Paystack secret key ${b.paystack.configured ? `<span class="badge good">set: ${esc(b.paystack.key_hint)} (${esc(b.paystack.source)})</span>` : '<span class="badge warn">not set</span>'}</label>
      <input id="psk" type="password" autocomplete="off" placeholder="sk_live_… or sk_test_… (leave blank to keep)">
      <p class="small muted" style="margin:6px 0 0">Webhook URL to add in the Paystack dashboard (Settings → API Keys &amp; Webhooks): <code>https://YOUR-APP-DOMAIN${esc(b.webhook_path)}</code></p>
      <div class="actions"><button class="btn" id="pstest" ${b.paystack.configured ? '' : 'disabled'}>Test key</button>
        ${b.paystack.source === 'platform' ? '<button class="btn danger" id="psclear">Remove key</button>' : ''}<button class="btn primary" id="savebill">Save billing</button></div></div>
    <div class="card" style="margin-top:12px"><h2>AI</h2>
      <p class="small muted" style="margin:0 0 6px">One AI connection for every workspace: screenshot reading, hook ideas, weekly plans, "ask your data" and video structure. Customers do not enter keys. ${s.ai.available ? '<span class="badge good">on</span>' : '<span class="badge warn">off</span>'}</p>
      <div class="fields">
        <div><label class="f">Provider</label><select id="aip">${Object.entries(s.ai.providers).map(([id, p]) => `<option value="${esc(id)}" ${id === s.ai.provider ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></div>
        <div><label class="f">Model (blank = default: ${esc(s.ai.providers[s.ai.provider].default_model)})</label><input id="aim" value="${esc(s.ai.model)}"></div>
        <div class="full" id="aiurlrow"><label class="f">Endpoint URL (OpenAI-compatible servers only)</label><input id="aiu" value="${esc(s.ai.base_url)}" placeholder="https://…/v1"></div>
        <div class="full"><label class="f">API key ${s.ai.has_key ? `<span class="badge good">set: ${esc(s.ai.key_hint)}</span>` : '<span class="badge warn">not set</span>'}</label>
          <input id="aik" type="password" autocomplete="off" placeholder="paste the key (leave blank to keep)"></div>
      </div>
      <div class="actions"><button class="btn" id="aitest" ${s.ai.available ? '' : 'disabled'}>Test</button>
        ${s.ai.has_key ? '<button class="btn danger" id="aiclear">Remove key</button>' : ''}<button class="btn primary" id="aisave">Save</button></div></div>
    <div class="card" style="margin-top:12px"><h2>Publishing (Late)</h2>
      <p class="small muted" style="margin:0 0 6px">One Late account that publishes for customers who do not bring their own key. Each workspace gets its own profile, so a customer can only ever post to accounts they connected themselves.</p>
      <label class="f">Late API key ${s.late.configured ? `<span class="badge good">set: ${esc(s.late.key_hint)} (${esc(s.late.source)})</span>` : '<span class="badge warn">not set</span>'}</label>
      <input id="latek" type="password" autocomplete="off" placeholder="paste the key (leave blank to keep)">
      <div class="actions"><button class="btn" id="latetest" ${s.late.configured ? '' : 'disabled'}>Test key</button>
        ${s.late.source === 'platform' ? '<button class="btn danger" id="lateclear">Remove key</button>' : ''}<button class="btn primary" id="latesave">Save</button></div></div>
    <div class="card" style="margin-top:12px"><h2>Free trial</h2>
      <p class="small muted" style="margin:0 0 6px">There is no free plan. A new workspace gets this trial, then can only view until it subscribes. The clock starts when the workspace is created, or when billing is first switched on, whichever is later.</p>
      <div class="fields"><div><label class="f">Trial length (days)</label><input id="trd" type="number" min="0" max="365" value="${esc(s.trial.days)}"></div>
        <div><label class="f">Limits during the trial</label><select id="trp"><option value="">Cheapest plan (default)</option>${s.trial.plans.map(c => `<option value="${esc(c)}" ${s.trial.plan === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div></div>
      <div class="actions"><button class="btn primary" id="savetrial">Save trial</button></div></div>
    <div class="card" style="margin-top:12px"><h2>Legal details (Terms and Privacy pages)</h2>
      <p class="small muted" style="margin:0 0 6px">Shown on your public /terms and /privacy pages. Fill these in before you take real payments. The pages are a plain-language template: have a lawyer review them for your business and country.</p>
      <div class="fields">
        <div><label class="f">Company / legal name</label><input id="lgco" maxlength="120" value="${esc(s.legal.company)}" placeholder="Acme Media Ltd"></div>
        <div><label class="f">Contact email</label><input id="lgem" type="email" maxlength="200" value="${esc(s.legal.email)}" placeholder="support@acme.example"></div>
        <div><label class="f">Postal address (optional)</label><input id="lgad" maxlength="300" value="${esc(s.legal.address)}"></div>
        <div><label class="f">Governing law / country</label><input id="lgjr" maxlength="160" value="${esc(s.legal.jurisdiction)}" placeholder="the laws of Ghana"></div>
      </div>
      <label class="f">Refund policy (replaces the default sentence in the Terms)</label>
      <textarea id="lgrf" rows="2" maxlength="600" placeholder="e.g. You can ask for a refund within 7 days of a payment if you have not used the plan.">${esc(s.legal.refund_policy)}</textarea>
      <div class="actions"><button class="btn primary" id="savelegal">Save legal details</button></div></div>
    <div class="card" style="margin-top:12px"><h2>Disposable email addresses</h2>
      <label class="check" style="margin-top:0"><input type="checkbox" id="egon" ${s.email_guard.enabled ? 'checked' : ''}> Refuse sign-ups and invitations from temporary mailboxes</label>
      <p class="small muted" style="margin:6px 0 0">Stops the cheapest way to get a second free trial. ${esc(s.email_guard.builtin_count)} well-known providers are built in; the list can never be complete, so add any you see below. Existing accounts are never affected.</p>
      <label class="f">Also block these domains (one per line)</label>
      <textarea id="egextra" rows="3" placeholder="tempmail.example">${esc(s.email_guard.extra.join('\n'))}</textarea>
      <label class="f">Always allow these domains (fixes a wrong block)</label>
      <textarea id="egallow" rows="2" placeholder="mycompany.example">${esc(s.email_guard.allow.join('\n'))}</textarea>
      <div class="row" style="margin-top:10px;gap:8px"><input id="egtest" type="email" placeholder="Check an address, e.g. x@mailinator.com" style="flex:1;min-width:200px">
        <button class="btn" id="egcheck">Check</button><button class="btn primary" id="egsave">Save</button></div>
      <div id="egres" class="small" style="margin-top:6px"></div></div>
    <div class="card" style="margin-top:12px"><h2>Display exchange rate (USD)</h2>
      <p class="small muted" style="margin:0 0 6px">Customers see plan prices as an approximate dollar amount with the real charge below it. The rate is also used to convert the price at checkout when you pick a currency under "Charge customers in" below.</p>
      ${s.fx.currencies.map(c => `<div class="row" style="margin-top:8px"><span style="min-width:90px">1 USD =</span>
        <input data-fx="${esc(c)}" type="number" min="0" step="0.01" value="${esc(s.fx.rates[c] ?? '')}" placeholder="e.g. 15.50" style="max-width:160px"><b>${esc(c)}</b></div>`).join('')}
      <div class="row" style="margin-top:8px"><span style="min-width:90px">1 USD =</span>
        <input id="fxnewrate" type="number" min="0" step="0.01" placeholder="rate" style="max-width:160px">
        <input id="fxnewcode" maxlength="3" placeholder="GHS" style="max-width:80px;text-transform:uppercase"><span class="muted small">add a currency</span></div>
      <div class="actions"><button class="btn primary" id="savefx">Save rate</button></div>
      ${!s.fx.charge_currency && s.fx.rate_currencies.length
        ? `<div class="notice" style="margin-top:12px;border-color:var(--bad, #ef4444)"><b>Customers are still being charged in USD.</b> You have set a ${esc(s.fx.rate_currencies.join(', '))} rate, but the box below is still on USD. If your Paystack account cannot take USD, payments will fail until you choose ${esc(s.fx.rate_currencies[0])} below and press Save, then press <b>Test key</b> under Billing.</div>` : ''}
      <label class="f" style="margin-top:12px">Charge customers in</label>
      <div class="row" style="gap:8px"><select id="chcur" style="max-width:220px"><option value="">USD: no conversion (plans are priced in USD)</option>
        ${s.fx.rate_currencies.map(c => `<option value="${esc(c)}" ${s.fx.charge_currency === c ? 'selected' : ''}>${esc(c)}: convert at 1 USD = ${esc(s.fx.rates[c])} ${esc(c)}</option>`).join('')}</select>
        <button class="btn" id="savechcur">Save</button></div>
      <p class="small muted" style="margin:6px 0 0">Pick GHS here when your Paystack account cannot take USD (for example a GHS-only account), then press Save. If GHS is not in the list, save its rate above first. Plans stay priced in USD; at checkout the customer is charged the converted amount in this currency at today's rate.</p></div>
    <div class="card" style="margin-top:12px"><h2>Sign-up protection</h2>
      <p class="small muted" style="margin:0 0 6px">A free trial spends your SociaVault credits, so people will try to farm it.</p>
      <label class="check" style="margin-top:0"><input type="checkbox" id="spver" ${s.signup_protection.require_verification ? 'checked' : ''}> Require a confirmed email before the first sync</label>
      <p class="small muted" style="margin:6px 0 0">${s.signup_protection.enforced
        ? '<span class="badge good">enforced</span> New workspaces cannot sync until the owner clicks the link we email. People who signed up before this existed are treated as confirmed.'
        : (s.signup_protection.email_configured
          ? '<span class="badge warn">off</span> Ticked is needed to enforce it.'
          : '<span class="badge warn">not enforced</span> This server cannot send email yet (set RESEND_API_KEY, HOOKTRACK_EMAIL_FROM and HOOKTRACK_PUBLIC_URL), so nobody could confirm. It switches on by itself once email works.')}
        ${s.signup_protection.unverified_users ? `${fmt(s.signup_protection.unverified_users)} user(s) have not confirmed.` : ''}</p>
      <label class="f">New accounts allowed per network address per day (0 = no limit)</label>
      <input id="spip" type="number" min="0" max="1000" value="${esc(s.signup_protection.ip_daily_limit)}" style="max-width:160px">
      <p class="small muted" style="margin:6px 0 0">Offices, campuses and mobile carriers share one address, so keep this above 3. Behind a proxy or load balancer, set HOOKTRACK_TRUSTED_PROXIES or every visitor will look like the same address.</p>
      <div class="actions"><button class="btn primary" id="spsave">Save</button></div></div>
    <div class="card" style="margin-top:12px"><h2>Database backups</h2>
      ${(() => { const k = s.backups; const ago = t => { if (!t) return 'never'; const h = (Date.now() - new Date(t)) / 3600000; return h < 1 ? 'less than an hour ago' : h < 48 ? Math.round(h) + ' hours ago' : Math.round(h / 24) + ' days ago'; };
        return `<p style="margin:0 0 6px">${k.enabled ? '<span class="badge good">scheduled</span>' : '<span class="badge warn">schedule off</span>'}
          Last backup: <b>${ago(k.last_backup_at)}</b>${k.last_backup_bytes ? ` <span class="muted small">(${esc(k.last_backup_file)}, ${(k.last_backup_bytes / 1048576).toFixed(1)} MB, ${fmt(k.last_backup_rows)} rows)</span>` : ''}</p>
          ${k.last_error ? `<p class="small" style="color:var(--bad);margin:0 0 6px">Last backup failed: ${esc(k.last_error)}</p>` : ''}
          <p style="margin:0 0 6px">Restore test: ${k.last_drill_at ? (k.drill_ok ? '<span class="badge good">passed</span>' : '<span class="badge bad">FAILED</span>') + ` ${ago(k.last_drill_at)}` : '<span class="badge warn">never run</span>'}
            ${k.drill_ok && k.drill_report ? `<span class="muted small">restored ${fmt(k.drill_report.rows)} rows in ${k.drill_seconds}s and every table matched${k.drill_report.secret_matches ? '' : ' (the encryption secret differs: stored keys would need re-entering)'}</span>` : ''}
            ${k.drill_error ? `<span class="small" style="color:var(--bad)">${esc(k.drill_error)}</span>` : ''}</p>
          <p class="small muted" style="margin:0 0 6px">Saved to <code>${esc(k.dir)}</code>, newest ${fmt(k.keep)} kept${k.copy_to ? `; also copied to <code>${esc(k.copy_to)}</code>` : '. <b>This is the same machine as the database.</b> Set HOOKTRACK_BACKUP_COPY_TO to a mounted or synced folder so a copy survives losing it'}.
            Backups hold customer data, so keep that folder private, and store HOOKTRACK_SECRET separately: without it a restore works but every saved API key must be re-entered.</p>`; })()}
      <div class="actions"><button class="btn" id="bknow">Back up now</button><button class="btn primary" id="bkdrill">Run restore test now</button></div></div>
    <div class="card" style="margin-top:12px"><h2>SociaVault balance watch</h2>
      ${(() => { const v = s.vault; const badge = { ok: 'good', low: 'warn', paused: 'bad', exhausted: 'bad', unknown: 'warn', no_key: 'warn' }[v.status] || 'warn';
        return `<p style="margin:0 0 6px"><span class="badge ${badge}">${esc(v.status.replace('_', ' '))}</span>
          ${v.credits != null ? `<b>${fmt(v.credits)}</b> credits left${v.percent_left != null ? ` (${v.percent_left}% of ${fmt(v.peak)})` : ''}` : '<span class="muted">no reading yet</span>'}
          ${v.checked_at ? `<span class="muted small"> · checked ${esc(new Date(v.checked_at).toLocaleString())}</span>` : ''}</p>
          ${v.error ? `<p class="small" style="color:var(--bad);margin:0 0 6px">${esc(v.error)}</p>` : ''}
          <p class="small muted" style="margin:0 0 6px">Sync health: ${v.failing.count ? `<b style="color:var(--bad)">${fmt(v.failing.count)}</b> of ${fmt(v.failing.total)} accounts failing` : `all ${fmt(v.failing.total)} accounts syncing`}.</p>`; })()}
      <div class="fields">
        <div><label class="f">Warn at (% of last top-up left)</label><input id="vwwarn" type="number" min="1" max="90" value="${esc(s.vault.config.warn_pct)}"></div>
        <div><label class="f">Pause scheduled syncs at (%)</label><input id="vwpause" type="number" min="0" max="50" value="${esc(s.vault.config.pause_pct)}"></div>
        <div><label class="f">Size of your last credit pack (optional)</label><input id="vwref" type="number" min="0" value="${esc(s.vault.config.reference || '')}" placeholder="e.g. 10000"></div>
        <div><label class="f">Email alerts to</label><input id="vwmail" type="email" value="${esc(s.vault.config.alert_email)}" placeholder="defaults to your legal contact email"></div>
      </div>
      <p class="small muted" style="margin:6px 0 0">When paused, only the automatic sync stops: customers can still press Sync, and "Run sync now" below always runs. You are emailed once a day per problem.</p>
      <div class="actions"><button class="btn" id="vwcheck">Check balance now</button><button class="btn primary" id="vwsave">Save</button></div></div>
    <div class="card" style="margin-top:12px"><h2>SociaVault (TikTok / Instagram / YouTube data)</h2>
      <div class="muted small">Key: ${sv.key.configured ? `<span class="badge good">set</span> ${esc(sv.key.key_hint)} (${esc(sv.key.source)})` : '<span class="badge warn">not set</span>'}</div>
      <label class="f">API key</label><input id="svk" type="password" autocomplete="off" placeholder="leave blank to keep">
      <div class="fields"><div><label class="f">Scheduled sync</label><select id="sve"><option value="false">Off</option><option value="true" ${sv.schedule.enabled ? 'selected' : ''}>On</option></select></div>
        <div><label class="f">Every (hours)</label><input id="svi" type="number" min="0" step="0.5" value="${esc(sv.schedule.interval_hours ?? '')}"></div>
        <div><label class="f">Mode</label><select id="svm"><option value="light">light</option><option value="full" ${sv.schedule.mode === 'full' ? 'selected' : ''}>full</option></select></div></div>
      <div class="actions"><button class="btn" id="svtest">Test key</button><button class="btn" id="svrun">Run sync now</button><button class="btn" id="svsch">Process due posts</button><button class="btn" id="runren">Run renewals now</button><button class="btn" id="digestnow">Send weekly digests now</button><button class="btn primary" id="savesv">Save</button></div></div>
    <div class="card" style="margin-top:12px"><h2>Your password</h2>
      <div class="fields"><div><label class="f">Current</label><input id="pcur" type="password" autocomplete="current-password"></div>
      <div><label class="f">New (10+ characters)</label><input id="pnew" type="password" autocomplete="new-password"></div></div>
      <div class="actions"><button class="btn" id="savepw">Change password</button></div></div>`;
  $('#savebill').onclick = guard(async () => {
    const r = await api('PUT', '/settings/billing', { enabled: $('#ben').checked, paystack_key: $('#psk').value || null });
    if (r.currency_check && !r.currency_check.ok) { toast('Saved, but customers cannot pay yet: ' + r.currency_check.message, true); return route(); }
    toast('Billing settings saved'); route();
  });
  if ($('#psclear')) $('#psclear').onclick = guard(async () => { await api('PUT', '/settings/billing', { clear_paystack_key: true }); toast('Key removed'); route(); });
  $('#pstest').onclick = guard(async () => { const r = await api('POST', '/settings/paystack/test'); toast(r.message, !r.ok); });
  $('#runren').onclick = guard(async () => {
    const r = await api('POST', '/actions/run-renewals'); const o = r.result;
    toast(`Renewals: ${o.renewed} renewed, ${o.pending} pending, ${o.failed} failed, ${o.skipped} skipped`);
  });
  $('#savelegal').onclick = guard(async () => {
    await api('PUT', '/settings/legal', { company: $('#lgco').value, email: $('#lgem').value, address: $('#lgad').value,
      jurisdiction: $('#lgjr').value, refund_policy: $('#lgrf').value });
    toast('Legal details saved'); route();
  });
  const lines = id => $(id).value.split(/[\n,]+/).map(x => x.trim()).filter(Boolean);
  $('#egsave').onclick = guard(async () => {
    await api('PUT', '/settings/email-guard', { enabled: $('#egon').checked, extra: lines('#egextra'), allow: lines('#egallow') });
    toast('Email rules saved'); route();
  });
  $('#egcheck').onclick = guard(async () => {
    const r = await api('POST', '/settings/email-guard/check', { email: $('#egtest').value });
    $('#egres').innerHTML = r.blocked ? `<span style="color:var(--bad)">Blocked</span> (${r.reason === 'operator' ? 'on your list' : 'built-in list'}): ${esc(r.domain)}`
      : `<span style="color:var(--good)">Allowed</span>${r.reason === 'allowed' ? ' (on your allow list)' : r.reason === 'off' ? ' (the check is switched off)' : ''}: ${esc(r.domain)}`;
  });
  if ($('#savechcur')) $('#savechcur').onclick = guard(async () => {
    await api('PUT', '/settings/charge-currency', { currency: $('#chcur').value }); toast('Charge currency saved'); route();
  });
  $('#savetrial').onclick = guard(async () => {
    await api('PUT', '/settings/trial', { days: +$('#trd').value, plan: $('#trp').value }); toast('Trial saved'); route();
  });
  if ($('#savefx')) $('#savefx').onclick = guard(async () => {
    const rates = {}; $$('[data-fx]').forEach(i => (rates[i.dataset.fx] = i.value));
    const nc = $('#fxnewcode').value.trim().toUpperCase();
    if (nc && $('#fxnewrate').value) rates[nc] = $('#fxnewrate').value;
    await api('PUT', '/settings/fx', { rates }); toast('Exchange rate saved'); route();
  });
  const aiBody = () => ({ provider: $('#aip').value, model: $('#aim').value, base_url: $('#aiu').value, api_key: $('#aik').value || null });
  const aiUrl = () => { $('#aiurlrow').style.display = $('#aip').value === 'compatible' ? '' : 'none'; };
  $('#aip').onchange = aiUrl; aiUrl();
  $('#aisave').onclick = guard(async () => { await api('PUT', '/settings/ai', aiBody()); toast('Saved'); route(); });
  $('#aitest').onclick = guard(async () => { const r = await api('POST', '/settings/ai/test'); toast(`Connected: ${r.model} replied`); });
  const aic = $('#aiclear');
  if (aic) aic.onclick = guard(async () => { if (await askConfirm('Remove the AI key? AI features turn off for everyone.')) { await api('PUT', '/settings/ai', { provider: $('#aip').value, clear_key: true }); toast('Removed'); route(); } });
  $('#latesave').onclick = guard(async () => {
    await api('PUT', '/settings/late', { api_key: $('#latek').value || null }); toast('Saved'); route();
  });
  $('#latetest').onclick = guard(async () => { const r = await api('POST', '/settings/late/test'); toast(r.message || 'Key works'); });
  const lc = $('#lateclear');
  if (lc) lc.onclick = guard(async () => { if (await askConfirm('Remove the platform Late key? Customers using it will not be able to publish.')) { await api('PUT', '/settings/late', { clear_key: true }); toast('Removed'); route(); } });
  $('#savesv').onclick = guard(async () => {
    await api('PUT', '/settings/sociavault', { api_key: $('#svk').value || null, enabled: $('#sve').value === 'true',
      interval_hours: $('#svi').value === '' ? null : +$('#svi').value, mode: $('#svm').value });
    toast('Saved'); route();
  });
  $('#spsave').onclick = guard(async () => {
    await api('PUT', '/settings/signup-protection', { require_verification: $('#spver').checked, ip_daily_limit: +$('#spip').value || 0 });
    toast('Saved'); route();
  });
  $('#vwsave').onclick = guard(async () => {
    await api('PUT', '/settings/vault-watch', { warn_pct: +$('#vwwarn').value, pause_pct: +$('#vwpause').value,
      reference: +$('#vwref').value || 0, alert_email: $('#vwmail').value });
    toast('Saved'); route();
  });
  $('#vwcheck').onclick = guard(async () => { const r = await api('POST', '/actions/check-balance');
    toast(r.credits != null ? `${r.credits} credits left` : (r.error || 'No reading')); route(); });
  $('#bknow').onclick = guard(async () => { const r = await api('POST', '/actions/backup-now');
    toast(`Backed up ${r.rows} rows (${(r.bytes / 1048576).toFixed(1)} MB)`); route(); });
  $('#bkdrill').onclick = guard(async () => { toast('Restoring into a scratch database…'); const r = await api('POST', '/actions/restore-drill');
    toast(`Restore test passed: ${r.rows} rows, every table matched (${r.seconds}s)`); route(); });
  $('#digestnow').onclick = guard(async () => {
    if (!await askConfirm("Send this week's digest to every workspace that has something to report?")) return;
    const r = await api('POST', '/actions/send-digests');
    toast(r.email_configured ? `Sent ${r.sent} email(s) for ${r.workspaces} workspace(s); ${r.skipped_empty} had nothing to report` : 'Email is not set up on this server', !r.email_configured);
  });
  $('#svtest').onclick = guard(async () => { const r = await api('POST', '/settings/sociavault/test'); toast(r.message || 'Key works'); });
  $('#svrun').onclick = guard(async () => { await api('POST', '/actions/run-sync?force=true'); toast('Sync started'); });
  $('#svsch').onclick = guard(async () => { const r = await api('POST', '/actions/process-schedules'); toast(`Published ${r.published}, polled ${r.video_polled} video jobs`); });
  $('#savepw').onclick = guard(async () => {
    await api('POST', '/auth/password', { current: $('#pcur').value, new: $('#pnew').value });
    $('#pcur').value = $('#pnew').value = ''; toast('Password changed');
  });
};

pages.admins = async app => {
  const r = await api('GET', '/admins');
  app.innerHTML = `<div class="page-head"><div><h1>Admins</h1><div class="sub">People who can sign in to this console. Separate from customer accounts.</div></div>
    <button class="btn primary" id="newadmin">+ Add admin</button></div>
    <div class="table-wrap"><table><thead><tr><th>Email</th><th>Name</th><th>Added</th><th>Status</th><th></th></tr></thead><tbody>
    ${r.admins.map(a => `<tr><td>${esc(a.email)}${a.id === me.id ? ' <span class="muted small">(you)</span>' : ''}</td><td>${esc(a.name)}</td><td>${date(a.created_at)}</td>
      <td><span class="badge ${a.disabled ? 'bad' : 'good'}">${a.disabled ? 'disabled' : 'active'}</span></td>
      <td class="num">${a.id === me.id ? '' : `<button class="btn sm" data-toggle="${a.id}" data-dis="${a.disabled ? 0 : 1}">${a.disabled ? 'Enable' : 'Disable'}</button> <button class="btn sm" data-pw="${a.id}">Reset password</button>`}</td></tr>`).join('')}</tbody></table></div>`;
  $$('[data-toggle]', app).forEach(b => (b.onclick = guard(async () => { await api('PATCH', `/admins/${b.dataset.toggle}`, { disabled: b.dataset.dis === '1' }); route(); })));
  $$('[data-pw]', app).forEach(b => (b.onclick = guard(async () => {
    const pw = await askText('New password for this admin (10+ characters)', {title: 'New admin password', password: true, ok: 'Set password'}); if (!pw) return;
    await api('PATCH', `/admins/${b.dataset.pw}`, { password: pw }); toast('Password reset');
  })));
  $('#newadmin').onclick = () => {
    const m = openModal(`<h2 style="margin:0 0 6px">Add admin</h2><div class="fields">
      <div><label class="f">Email</label><input id="ae" type="email"></div><div><label class="f">Name</label><input id="an"></div>
      <div><label class="f">Password (10+ characters)</label><input id="ap" type="password" autocomplete="new-password"></div></div>
      <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="mk">Create</button></div>`);
    $('#mk', m).onclick = guard(async () => { await api('POST', '/admins', { email: $('#ae', m).value, name: $('#an', m).value, password: $('#ap', m).value }); m.close(); toast('Admin added'); route(); });
  };
};

/* ---------- shell ---------- */
async function route() {
  const tab = (location.hash.replace('#/', '') || 'overview').split('/')[0];
  const key = pages[tab] ? tab : 'overview';
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.tab === key));
  try { await pages[key]($('#app')); }
  catch (e) { if (e.message !== 'Signed out') $('#app').innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p></div>`; }
}
window.addEventListener('hashchange', route);

async function boot() {
  const s = await api('GET', '/auth/me');
  if (!s.authenticated) return showGate();
  me = s.admin;
  $('#gate').hidden = true; $('#shell').hidden = false;
  $('#who').textContent = me.email;
  route();
}
$('#logout').onclick = guard(async () => { await api('POST', '/auth/logout'); me = null; showGate(); });
boot();

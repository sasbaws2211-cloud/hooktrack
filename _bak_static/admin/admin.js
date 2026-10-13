/* HookTrack Platform Admin — standalone UI */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = n => (n == null ? '—' : Number(n).toLocaleString());

let me = null;

async function api(method, path, body) {
  const opt = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    throw new Error(typeof d === 'string' ? d : (Array.isArray(d) ? d.map(x => x.msg).join('; ') : r.statusText));
  }
  return data;
}

let toastT;
function toast(msg, err = false) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toastT); toastT = setTimeout(() => (t.className = ''), 3200);
}
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };

const pages = {};

async function route() {
  const tab = (location.hash.replace('#/', '') || 'overview').split('/')[0];
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.tab === tab));
  const page = pages[tab] || pages.overview;
  try {
    await page($('#app'));
  } catch (e) {
    $('#app').innerHTML = `<div class="empty"><h2>Error</h2><p>${esc(e.message)}</p></div>`;
  }
}
window.addEventListener('hashchange', route);

async function boot() {
  me = await api('GET', '/api/platform/me');
  if (!me.authenticated) {
    document.body.innerHTML = `<div class="gate card" style="padding:28px">
      <h1>Platform Admin</h1>
      <p class="muted">Sign in to HookTrack first, then open this page.</p>
      <p><a class="btn primary" href="/">Go to HookTrack</a></p>
    </div>`;
    return;
  }
  if (!me.platform_admin) {
    document.body.innerHTML = `<div class="gate card" style="padding:28px">
      <h1>Access denied</h1>
      <p class="muted">Signed in as <b>${esc(me.user.email)}</b>, but this account is not a platform admin.</p>
      <p class="small muted">Set <code>HOOKTRACK_PLATFORM_ADMINS</code> to a comma-separated list of emails (including yours) and restart the server.</p>
      ${!me.admins_configured ? '<p class="small" style="color:var(--bad)">No platform admins are configured yet.</p>' : ''}
      <p><a class="btn" href="/">← Back to app</a></p>
    </div>`;
    return;
  }
  $('#who').textContent = me.user.email;
  route();
}

pages.overview = async app => {
  const d = await api('GET', '/api/platform/overview');
  const c = d.counts;
  app.innerHTML = `<div class="page-head"><div><h1>Overview</h1><div class="sub">Platform-wide totals</div></div></div>
    <div class="grid kpis" style="margin-bottom:16px">
      ${[['Users', c.users], ['Workspaces', c.workspaces], ['Accounts', c.accounts], ['Posts', c.posts], ['View rows', c.daily_view_rows], ['Memberships', c.memberships]]
        .map(([l, v]) => `<div class="card kpi"><div class="label">${l}</div><div class="value">${fmt(v)}</div></div>`).join('')}
    </div>
    <div class="grid" style="grid-template-columns:1fr 1fr;gap:12px">
      <div class="card"><h2>Recent users</h2>
        <ul class="list">${d.recent_users.map(u => `<li><div style="flex:1"><b>${esc(u.email)}</b><div class="muted small">${esc(u.name || '')} · ${esc((u.created_at || '').slice(0, 10))}</div></div></li>`).join('') || '<li class="muted">None</li>'}</ul>
      </div>
      <div class="card"><h2>Recent workspaces</h2>
        <ul class="list">${d.recent_workspaces.map(w => `<li><div style="flex:1"><b>${esc(w.name)}</b><div class="muted small">${esc((w.created_at || '').slice(0, 10))}</div></div>
          <a class="btn sm" href="#/workspaces/${w.id}">Open</a></li>`).join('') || '<li class="muted">None</li>'}</ul>
      </div>
    </div>`;
};

pages.workspaces = async app => {
  const parts = location.hash.replace('#/', '').split('/');
  const wid = parts[1] ? +parts[1] : null;
  if (wid) return workspaceDetail(app, wid);

  const q = parts[0].includes('?') ? '' : '';
  const data = await api('GET', '/api/platform/workspaces');
  app.innerHTML = `<div class="page-head"><div><h1>Workspaces</h1><div class="sub">${data.workspaces.length} shown</div></div>
    <div class="row"><input id="q" placeholder="Search name…" style="max-width:220px"><button class="btn" id="go">Search</button></div></div>
    <div class="table-wrap"><table><thead><tr><th>Name</th><th>Members</th><th>Accounts</th><th>Posts</th><th>Created</th><th></th></tr></thead>
    <tbody>${data.workspaces.map(w => `<tr>
      <td><b>${esc(w.name)}</b> <span class="muted small">#${w.id}</span></td>
      <td>${w.members}</td><td>${w.accounts}</td><td>${w.posts}</td>
      <td class="muted">${esc((w.created_at || '').slice(0, 10))}</td>
      <td><a class="btn sm" href="#/workspaces/${w.id}">View</a></td>
    </tr>`).join('')}</tbody></table></div>`;
  const search = guard(async () => {
    const qq = $('#q').value.trim();
    const d = await api('GET', `/api/platform/workspaces?q=${encodeURIComponent(qq)}`);
    location.hash = '#/workspaces';
    // re-render with results
    data.workspaces = d.workspaces;
    route();
  });
  $('#go').onclick = async () => {
    const qq = $('#q').value.trim();
    const d = await api('GET', `/api/platform/workspaces?q=${encodeURIComponent(qq)}`);
    app.querySelector('tbody').innerHTML = d.workspaces.map(w => `<tr>
      <td><b>${esc(w.name)}</b> <span class="muted small">#${w.id}</span></td>
      <td>${w.members}</td><td>${w.accounts}</td><td>${w.posts}</td>
      <td class="muted">${esc((w.created_at || '').slice(0, 10))}</td>
      <td><a class="btn sm" href="#/workspaces/${w.id}">View</a></td>
    </tr>`).join('') || '<tr><td colspan="6" class="muted">No matches</td></tr>';
  };
};

async function workspaceDetail(app, wid) {
  const d = await api('GET', `/api/platform/workspaces/${wid}`);
  const w = d.workspace;
  app.innerHTML = `<div class="page-head"><div><h1>${esc(w.name)}</h1>
    <div class="sub">Workspace #${w.id} · created ${(w.created_at || '').slice(0, 10)}</div></div>
    <div class="row"><a class="btn sm" href="#/workspaces">← All</a>
      <button class="btn sm danger" id="del">Delete workspace</button></div></div>
    <div class="grid kpis" style="margin-bottom:14px">
      ${Object.entries(d.counts).map(([k, v]) => `<div class="card kpi"><div class="label">${esc(k)}</div><div class="value">${fmt(v)}</div></div>`).join('')}
    </div>
    <div class="card"><h2>Members</h2>
      <div class="table-wrap" style="border:none;box-shadow:none"><table><thead><tr><th>Email</th><th>Name</th><th>Role</th></tr></thead>
      <tbody>${d.members.map(m => `<tr><td>${esc(m.email)}</td><td>${esc(m.name || '')}</td><td><span class="pill">${esc(m.role)}</span></td></tr>`).join('')}</tbody></table></div>
    </div>`;
  $('#del').onclick = guard(async () => {
    if (!confirm(`Permanently delete workspace "${w.name}" and all its data?`)) return;
    if (!confirm('This cannot be undone. Continue?')) return;
    await api('DELETE', `/api/platform/workspaces/${wid}`);
    toast('Workspace deleted');
    location.hash = '#/workspaces';
  });
}

pages.users = async app => {
  const parts = location.hash.replace('#/', '').split('/');
  const uid = parts[1] ? +parts[1] : null;
  if (uid) return userDetail(app, uid);

  const data = await api('GET', '/api/platform/users');
  app.innerHTML = `<div class="page-head"><div><h1>Users</h1><div class="sub">${data.users.length} shown</div></div>
    <div class="row"><input id="q" placeholder="Search email or name…" style="max-width:240px"><button class="btn" id="go">Search</button></div></div>
    <div class="table-wrap"><table><thead><tr><th>Email</th><th>Name</th><th>Workspaces</th><th>Joined</th><th></th></tr></thead>
    <tbody id="ubody">${data.users.map(u => userRow(u)).join('')}</tbody></table></div>`;
  $('#go').onclick = guard(async () => {
    const d = await api('GET', `/api/platform/users?q=${encodeURIComponent($('#q').value.trim())}`);
    $('#ubody').innerHTML = d.users.map(u => userRow(u)).join('') || '<tr><td colspan="5" class="muted">No matches</td></tr>';
  });
};

function userRow(u) {
  return `<tr>
    <td>${esc(u.email)} ${u.platform_admin ? '<span class="pill admin">platform admin</span>' : ''}</td>
    <td>${esc(u.name || '')}</td>
    <td>${u.workspaces}</td>
    <td class="muted">${esc((u.created_at || '').slice(0, 10))}</td>
    <td><a class="btn sm" href="#/users/${u.id}">View</a></td>
  </tr>`;
}

async function userDetail(app, uid) {
  const d = await api('GET', `/api/platform/users/${uid}`);
  const u = d.user;
  app.innerHTML = `<div class="page-head"><div><h1>${esc(u.email)}</h1>
    <div class="sub">${esc(u.name || '—')} · joined ${(u.created_at || '').slice(0, 10)}
      ${d.platform_admin ? ' · <span class="pill admin">platform admin</span>' : ''}</div></div>
    <a class="btn sm" href="#/users">← All</a></div>
    <div class="card"><h2>Memberships</h2>
      ${d.memberships.length ? `<div class="table-wrap" style="border:none"><table><thead><tr><th>Workspace</th><th>Role</th><th></th></tr></thead>
      <tbody>${d.memberships.map(m => `<tr><td>${esc(m.name)}</td><td>${esc(m.role)}</td>
        <td><a class="btn sm" href="#/workspaces/${m.id}">Open</a></td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted">No workspaces</p>'}
    </div>`;
}

pages.system = async app => {
  const [d, sv] = await Promise.all([
    api('GET', '/api/platform/overview'),
    api('GET', '/api/platform/sociavault'),
  ]);
  const s = d.system;
  const key = sv.key || {};
  const sch = sv.schedule || {};
  const last = sv.last_run || {};
  const keyLabel = key.configured
    ? (key.source === 'platform' ? `Saved (${esc(key.key_hint || '••••')})` : 'From environment')
    : 'Not configured';
  app.innerHTML = `<div class="page-head"><div><h1>System</h1><div class="sub">Server configuration (secrets never shown again)</div></div></div>

    <div class="card" style="margin-bottom:14px">
      <h2>SociaVault (system-wide)</h2>
      <p class="muted small">Used for scheduled sync across all workspaces that do not set their own key.
        Get a key at <a href="https://sociavault.com" target="_blank" rel="noopener">sociavault.com</a>.</p>
      <div class="row" style="margin:10px 0;gap:12px;flex-wrap:wrap">
        <span class="pill ${key.configured ? 'admin' : ''}">${keyLabel}</span>
        ${key.has_env_fallback && key.source === 'platform' ? '<span class="muted small">Env fallback also present</span>' : ''}
      </div>
      <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px">
        <input id="sv-key" type="password" placeholder="${key.key_hint ? '•••• leave blank to keep' : 'sk_live_…'}" style="max-width:320px;flex:1" autocomplete="off" spellcheck="false">
        <button class="btn primary" id="sv-save-key">Save key</button>
        <button class="btn" id="sv-test">Test</button>
        ${key.source === 'platform' ? '<button class="btn danger" id="sv-clear">Remove key</button>' : ''}
      </div>
      <p id="sv-key-msg" class="small muted" style="margin-top:8px"></p>

      <h2 style="margin-top:20px">Auto-sync schedule</h2>
      <p class="muted small">Light sync pulls recent posts/views for every sync-enabled account (uses system or workspace key).</p>
      <div class="row" style="gap:12px;flex-wrap:wrap;margin-top:10px">
        <label class="row" style="gap:6px"><input type="checkbox" id="sv-enabled" ${sch.enabled ? 'checked' : ''}> Enabled</label>
        <label class="muted small">Every <input id="sv-hours" type="number" min="0.25" max="168" step="0.25" value="${esc(sch.interval_hours || 24)}" style="width:80px"> hours</label>
        <label class="muted small">Days back <input id="sv-days" type="number" min="1" max="30" value="${esc(sch.days_back || 7)}" style="width:64px"></label>
        <label class="muted small">Mode
          <select id="sv-mode" style="width:auto">
            <option value="light" ${sch.mode === 'light' ? 'selected' : ''}>Light (cheaper)</option>
            <option value="full" ${sch.mode === 'full' ? 'selected' : ''}>Full</option>
          </select>
        </label>
        <button class="btn primary" id="sv-save-sched">Save schedule</button>
      </div>
      <p class="muted small" style="margin-top:10px">Last run:
        ${last.running ? 'Running…' : (last.finished_at ? esc(String(last.finished_at).slice(0, 19).replace('T', ' ')) : 'never')}
        ${last.ok === false ? ' · failed' : last.ok ? ' · ok' : ''}
      </p>
    </div>

    <div class="card" style="margin-bottom:12px">
      <h2>Environment</h2>
      <ul class="list">
        <li><span style="flex:1">Platform admins</span><span>${(s.platform_admins || []).map(e => esc(e)).join(', ') || '<span class="muted">none configured</span>'}</span></li>
        <li><span style="flex:1">Server AI key</span><span>${s.has_server_ai_key ? 'Yes' : 'No'}</span></li>
        <li><span style="flex:1">SociaVault key</span><span>${keyLabel}</span></li>
      </ul>
    </div>
    <div class="card">
      <h2>Actions</h2>
      <p class="muted small">Run background jobs on demand (uses the system-wide key when workspaces have none).</p>
      <div class="row" style="margin-top:10px">
        <button class="btn primary" id="sync">Run SociaVault sync now</button>
        <button class="btn" id="sched">Process due publishes / video jobs</button>
      </div>
      <pre id="out" class="muted small" style="margin-top:12px;white-space:pre-wrap"></pre>
    </div>`;

  $('#sv-save-key').onclick = guard(async () => {
    const k = $('#sv-key').value;
    if (!k.trim()) { toast('Enter an API key', true); return; }
    const r = await api('PUT', '/api/platform/sociavault', { api_key: k });
    $('#sv-key').value = '';
    $('#sv-key-msg').textContent = 'Key saved (' + (r.key && r.key.key_hint || '••••') + ')';
    toast('SociaVault key saved');
    route();
  });
  const clr = $('#sv-clear');
  if (clr) clr.onclick = guard(async () => {
    if (!confirm('Remove the system-wide SociaVault key?')) return;
    await api('PUT', '/api/platform/sociavault', { clear_key: true });
    toast('Key removed');
    route();
  });
  $('#sv-test').onclick = guard(async () => {
    $('#sv-key-msg').textContent = 'Testing…';
    const r = await api('POST', '/api/platform/sociavault/test');
    $('#sv-key-msg').textContent = r.message || 'OK';
    toast('Connection OK');
  });
  $('#sv-save-sched').onclick = guard(async () => {
    const body = {
      enabled: $('#sv-enabled').checked,
      interval_hours: +$('#sv-hours').value || 24,
      days_back: +$('#sv-days').value || 7,
      mode: $('#sv-mode').value,
    };
    await api('PUT', '/api/platform/sociavault', body);
    toast('Schedule saved');
    route();
  });
  $('#sync').onclick = guard(async () => {
    $('#out').textContent = 'Running…';
    const r = await api('POST', '/api/platform/actions/run-sync');
    $('#out').textContent = JSON.stringify(r.summary || r, null, 2);
    toast('Sync finished');
  });
  $('#sched').onclick = guard(async () => {
    $('#out').textContent = 'Running…';
    const r = await api('POST', '/api/platform/actions/process-schedules');
    $('#out').textContent = JSON.stringify(r, null, 2);
    toast('Done');
  });
};

boot();

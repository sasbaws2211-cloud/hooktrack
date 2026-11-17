/* Renders the client-facing approval page (the JSON from approvals.public_get) into an element. No login: the
   token in the URL is the whole credential. Everything is escaped. */
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const PLATFORM_LABEL = { tiktok: 'TikTok', instagram: 'Instagram', youtube: 'YouTube', x: 'X', twitter: 'X', facebook: 'Facebook', linkedin: 'LinkedIn', threads: 'Threads' };
  const when = iso => { try { return new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }); } catch { return iso; } };

  function decisionBanner(d) {
    if (d.status === 'approved') return `<div class="rp-decision approved">You approved this${d.decided_at ? ' on ' + esc(when(d.decided_at)) : ''}. It will go out as scheduled.</div>`;
    if (d.status === 'rejected') return `<div class="rp-decision rejected">You asked for changes${d.decided_at ? ' on ' + esc(when(d.decided_at)) : ''}.${d.client_note ? '<br>Your note: "' + esc(d.client_note) + '"' : ''}</div>`;
    if (d.status === 'cancelled') return `<div class="rp-decision cancelled">This request was withdrawn, so there is nothing to review here any more.</div>`;
    return '';
  }

  window.renderApproval = function (el, d, token, reload) {
    const decided = d.status !== 'pending';
    el.innerHTML = `<article class="rp">
      <header class="rp-head"><div><div class="rp-kicker">${esc(d.prepared_by || 'Review requested')}</div>
        <h1>A post is ready for your review</h1>
        <div class="rp-sub">${esc(d.handle || 'Your account')} · ${(d.platforms || []).map(p => window.socialPlatformIdentity(p, PLATFORM_LABEL[p] || p)).join(' ')}${d.schedule_at ? ' · goes out ' + esc(when(d.schedule_at)) : ''}</div></div></header>
      ${decisionBanner(d)}
      <section class="rp-card">
        <h2>The post</h2>
        ${d.hook ? `<p style="font-size:17px;font-weight:600;margin:0 0 8px">&ldquo;${esc(d.hook)}&rdquo;</p>` : ''}
        ${d.caption ? `<p style="margin:0 0 8px">${esc(d.caption)}</p>` : ''}
        ${d.body && d.body !== d.caption ? `<p class="rp-muted" style="white-space:pre-wrap">${esc(d.body)}</p>` : ''}
        ${d.video_url ? `<video class="rp-video" controls preload="metadata" src="${esc(d.video_url)}">Your browser can't preview this video.</video>
          <p class="rp-muted"><a href="${esc(d.video_url)}" target="_blank" rel="noopener">Open the video directly ↗</a></p>` : ''}
        ${(d.images || []).length ? `<div class="rp-gallery">${d.images.map(u => `<a href="${esc(u)}" target="_blank" rel="noopener"><img class="rp-img" src="${esc(u)}" alt="" loading="lazy"></a>`).join('')}</div>` : ''}
        ${!d.hook && !d.caption && !d.video_url && !(d.images || []).length ? '<p class="rp-muted">No preview content was attached to this post.</p>' : ''}
      </section>
      ${!decided ? `<section class="rp-card">
        <h2>Your decision</h2>
        <div class="row" style="display:flex;gap:10px;flex-wrap:wrap">
          <button class="rp-btn primary" id="ap-go">Approve — let it go out</button>
          <button class="rp-btn danger" id="ap-reject-open">Request changes</button>
        </div>
        <div id="ap-reject-box" hidden style="margin-top:12px">
          <label for="ap-note" style="font-size:13.5px;color:var(--rp-mute)">What needs to change?</label>
          <textarea class="rp-textarea" id="ap-note" maxlength="1000" placeholder="e.g. Please use a different clip for the first 3 seconds"></textarea>
          <button class="rp-btn danger" id="ap-reject-go">Send</button>
        </div>
        <div class="rp-err" id="ap-err" hidden></div>
      </section>` : ''}
      <footer class="rp-foot">This link is private to you. Approving schedules the post exactly as shown here; nothing else on this account is visible or changed.</footer>
    </article>`;
    if (decided) return;
    const err = el.querySelector('#ap-err');
    const showErr = msg => { err.textContent = msg; err.hidden = false; };
    const busy = (btn, on, label) => { btn.disabled = on; if (on) { btn.dataset.label = btn.textContent; btn.textContent = label; } else if (btn.dataset.label) btn.textContent = btn.dataset.label; };
    const post = async (action, note) => {
      const r = await fetch(`/api/public/approval/${encodeURIComponent(token)}/decide`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, note: note || '' }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || 'Something went wrong. Try again.');
      return body;
    };
    el.querySelector('#ap-go').onclick = async () => {
      const btn = el.querySelector('#ap-go'); err.hidden = true;
      busy(btn, true, 'Approving…');
      try { await post('approve'); await reload(); }
      catch (e) { showErr(e.message); busy(btn, false); }
    };
    el.querySelector('#ap-reject-open').onclick = () => { el.querySelector('#ap-reject-box').hidden = false; el.querySelector('#ap-note').focus(); };
    el.querySelector('#ap-reject-go').onclick = async () => {
      const note = el.querySelector('#ap-note').value.trim();
      err.hidden = true;
      if (!note) { showErr('Say what needs to change first.'); return; }
      const btn = el.querySelector('#ap-reject-go');
      busy(btn, true, 'Sending…');
      try { await post('reject', note); await reload(); }
      catch (e) { showErr(e.message); busy(btn, false); }
    };
  };
})();

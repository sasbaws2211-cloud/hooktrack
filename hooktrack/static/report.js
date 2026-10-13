/* Renders a client report (the JSON from reports.build) into an element. Used by the public /r/<token> page and by the
   in-app preview, so what an agency previews is exactly what their client sees. Everything is escaped. */
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => Number(v || 0).toLocaleString();
  const compact = v => {
    v = Number(v || 0);
    return v >= 1e6 ? (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + 'M' : v >= 1e4 ? Math.round(v / 1e3) + 'K' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'K' : String(v);
  };
  const mult = v => (typeof v === 'number' ? v.toFixed(1) + 'x' : '–');
  const date = s => { try { return new Date(s + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); } catch { return s; } };
  const change = p => (p == null ? '<span class="rp-flat">no earlier period to compare</span>'
    : `<span class="${p > 0 ? 'rp-up' : p < 0 ? 'rp-down' : 'rp-flat'}">${p > 0 ? '▲' : p < 0 ? '▼' : '■'} ${Math.abs(p)}% vs the period before</span>`);

  function bars(series) {
    const max = Math.max(1, ...series.map(s => s.views));
    const w = 100 / series.length;
    return `<svg class="rp-chart" viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label="Views per day">` +
      series.map((s, i) => { const h = Math.max(0.6, (s.views / max) * 38); return `<rect x="${(i * w + w * 0.12).toFixed(2)}" y="${(40 - h).toFixed(2)}" width="${(w * 0.76).toFixed(2)}" height="${h.toFixed(2)}" rx="0.4"><title>${esc(s.day)}: ${num(s.views)} views</title></rect>`; }).join('') + '</svg>';
  }

  window.renderReport = function (el, d) {
    const h = d.headline, m = d.made, p = d.period;
    const verdict = m.judged
      ? `${m.beat} of ${m.judged} beat the original they were made from${m.matched ? `, ${m.matched} matched it` : ''}${m.fell_short ? `, ${m.fell_short} fell short` : ''}.`
      : (m.posted ? 'Results are still coming in, so none has a verdict yet.' : 'No matching videos have posted in this period yet.');
    el.innerHTML = `<article class="rp">
      <header class="rp-head"><div><div class="rp-kicker">${esc(d.prepared_by)}</div><h1>${esc(d.title)}</h1>
        <div class="rp-sub">${esc(date(p.start))} to ${esc(date(p.end))} · last ${p.days} days</div></div>
        <button class="rp-print" onclick="window.print()">Save as PDF / Print</button></header>
      <section class="rp-kpis">
        <div class="rp-kpi"><div class="rp-k">Views</div><div class="rp-v">${compact(h.views)}</div><div class="rp-d">${change(h.change_pct)}</div></div>
        <div class="rp-kpi"><div class="rp-k">Videos posted</div><div class="rp-v">${num(h.posts)}</div><div class="rp-d">${h.accounts} account${h.accounts === 1 ? '' : 's'}</div></div>
        <div class="rp-kpi"><div class="rp-k">Winners</div><div class="rp-v">${num(h.winners)}</div><div class="rp-d">2x or more of the account's usual views</div></div>
        <div class="rp-kpi"><div class="rp-k">Made from winners</div><div class="rp-v">${num(m.posted)}</div><div class="rp-d">${esc(m.judged ? m.beat + ' beat their original' : 'posted this period')}</div></div>
      </section>
      <section class="rp-card"><h2>Views per day</h2>${h.views || d.series.some(s => s.views) ? bars(d.series) : '<p class="rp-muted">No views recorded in this period.</p>'}</section>
      ${d.accounts.length ? `<section class="rp-card"><h2>Accounts</h2><div class="rp-scroll"><table><thead><tr><th>Account</th><th>Platform</th><th class="n">Views</th><th class="n">Change</th><th class="n">Videos</th><th class="n">Winners</th></tr></thead><tbody>
        ${d.accounts.map(a => `<tr><td>${esc(a.handle)}</td><td>${esc(a.platform)}</td><td class="n">${num(a.views)}</td>
          <td class="n ${a.change_pct > 0 ? 'rp-up' : a.change_pct < 0 ? 'rp-down' : ''}">${a.change_pct == null ? '–' : (a.change_pct > 0 ? '+' : '') + a.change_pct + '%'}</td><td class="n">${num(a.posts)}</td><td class="n">${num(a.winners)}</td></tr>`).join('')}
        </tbody></table></div></section>` : ''}
      <section class="rp-card"><h2>Winning videos</h2>
        ${d.winners.length ? `<ol class="rp-list">${d.winners.map(w => `<li><div class="rp-main">${w.hook ? `&ldquo;${esc(w.hook)}&rdquo;` : 'A winning video'}
            <div class="rp-meta">${esc(w.handle)} · ${esc(date(w.date))} · ${num(w.views)} views</div></div><div class="rp-badge">${mult(w.multiple)} usual</div></li>`).join('')}</ol>
          ${d.winner_count > d.winners.length ? `<p class="rp-muted">and ${d.winner_count - d.winners.length} more</p>` : ''}`
          : '<p class="rp-muted">No video beat its account\'s usual views by 2x or more in this period.</p>'}</section>
      <section class="rp-card"><h2>New videos made from the winners</h2>
        <p>${m.made ? `${num(m.made)} new video${m.made === 1 ? ' was' : 's were'} planned from winning hooks in this period. ` : ''}${esc(verdict)}</p>
        ${m.examples.length ? `<ol class="rp-list">${m.examples.map(x => `<li><div class="rp-main">${x.hook ? `&ldquo;${esc(x.hook)}&rdquo;` : 'A matching video'}
            <div class="rp-meta">${esc(x.handle)}</div></div><div class="rp-badge good">${mult(x.made)} vs ${mult(x.original)}</div></li>`).join('')}</ol>` : ''}</section>
      <footer class="rp-foot">${esc(d.note)}</footer></article>`;
    document.title = d.title;
  };
})();

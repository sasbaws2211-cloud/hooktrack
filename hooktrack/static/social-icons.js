/* Locally hosted social brand marks used throughout the app, reports, and approval pages. */
(function () {
  const brands = {
    tiktok: { name: 'TikTok', icon: 'tiktok' },
    instagram: { name: 'Instagram', icon: 'instagram' },
    youtube: { name: 'YouTube', icon: 'youtube' },
    facebook: { name: 'Facebook', icon: 'facebook' },
    linkedin: { name: 'LinkedIn', icon: 'linkedin' },
    twitter: { name: 'X', icon: 'x' },
    x: { name: 'X', icon: 'x' },
    threads: { name: 'Threads', icon: 'threads' },
  };
  const escapeHtml = value => String(value == null ? '' : value).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const brandFor = platform => brands[String(platform || '').trim().toLowerCase()];

  window.socialPlatformIcon = platform => {
    const brand = brandFor(platform);
    if (!brand) return '';
    return `<span class="social-logo social-logo-${brand.icon}" aria-hidden="true"><img src="/social-icons/${brand.icon}.svg" alt=""></span>`;
  };
  window.socialPlatformIdentity = (platform, label) => {
    const brand = brandFor(platform);
    if (!brand) return escapeHtml(label || platform || '');
    return `<span class="social-identity">${window.socialPlatformIcon(platform)}<span>${escapeHtml(label || brand.name)}</span></span>`;
  };
})();

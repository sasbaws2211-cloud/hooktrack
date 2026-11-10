/* The call to action shown under every free tool's result: a tagged link to pricing and a link to the demo video.
   The ?ref= tag is the page the visitor was on, so a signup can be traced back to the tool that brought it. */
(function (root) {
  'use strict';
  var DEMO = 'https://youtu.be/SAprelI0bJ8';

  function ref() {
    var path = (root.location && root.location.pathname) || '';
    var slug = path.replace(/^\/+|\/+$/g, '').replace(/\//g, '-').toLowerCase().replace(/[^a-z0-9-]/g, '');
    return slug || 'tools';
  }

  function html(label) {
    return '<a class="btn primary" href="/?ref=' + ref() + '#pricing">' + (label || 'Track every post across all your accounts') + '</a> ' +
      '<a class="btn" href="' + DEMO + '" target="_blank" rel="noopener">Watch the demo</a>';
  }

  var api = { ref: ref, html: html, DEMO: DEMO };
  root.HTCta = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);

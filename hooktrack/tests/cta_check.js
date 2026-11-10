// Run with: node tests/cta_check.js  (checks the call-to-action links under the free tools)
const assert = require('assert');
let n = 0; const ok = (name, c) => { assert(c, name); console.log('PASS ' + name); n++; };
const load = (pathname) => { delete require.cache[require.resolve('../static/tool-cta.js')]; globalThis.location = { pathname }; return require('../static/tool-cta.js'); };

ok('tag is the page path', load('/tools/tiktok-analytics').ref() === 'tools-tiktok-analytics');
ok('trailing slash and case are normalised', load('/Tools/Best-Time/').ref() === 'tools-best-time');
ok('hub and unknown paths get a safe tag', load('/tools').ref() === 'tools' && load('/').ref() === 'tools');
ok('odd characters are stripped from the tag', load('/tools/a b<script>').ref() === 'tools-abscript');
const h = load('/tools/best-video-length-for-tiktok').html('Track it');
ok('primary link goes to tagged pricing', h.includes('href="/?ref=tools-best-video-length-for-tiktok#pricing"') && h.includes('>Track it</a>'));
ok('demo link opens the YouTube demo safely', h.includes('href="https://youtu.be/SAprelI0bJ8"') && h.includes('target="_blank"') && h.includes('rel="noopener"'));
ok('default label is used when none is given', load('/tools/x').html().includes('Track every post across all your accounts'));
console.log('ALL OK (' + n + ')');

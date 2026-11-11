// Run with: node tests/aigen_check.js  (checks how the free AI tools show what the server sends back: escaping above all)
const assert = require('assert');
global.HTHook = require('../static/tool-hook.js');
const A = require('../static/tool-aigen.js');
let n = 0; const ok = (name, c) => { assert(c, name); console.log('PASS ' + name); n++; };
const evil = '<img src=x onerror=alert(1)>"\'&';

const script = A.RENDER.script({ script: { hook: evil, beats: [{ say: evil, show: evil }, { say: 'Beat two', show: '' }], cta: evil, caption: evil, hashtags: ['#a', evil], seconds: 30, platform: 'tiktok' } });
ok('script output never contains a live tag from the AI text', !/<img/i.test(script) && !/onerror=alert/i.test(script.replace(/&lt;img[^>]*&gt;/g, '')));
ok('script output shows the hook, the beats and the call to action', script.indexOf('Beat two') > 0 && script.indexOf('Call to action') > 0 && script.indexOf('about 30 seconds') > 0);
ok('script output carries a hook score from the free scorer', /\d+\/100/.test(script));
ok('the copy button text is attribute-escaped', !/data-copy="[^"]*"[^>]*onerror/i.test(script) && script.indexOf('data-copy="') > 0);

const bio = A.RENDER.bio({ items: [{ text: evil, chars: 12, limit: 80 }] });
ok('bios are escaped and show their character count', !/<img/i.test(bio) && bio.indexOf('12 of 80 characters') > 0);

const cap = A.RENDER.caption({ items: [{ text: evil, hashtags: ['#a', '#b'] }, { text: 'Plain', hashtags: [] }] });
ok('captions are escaped and two are shown', !/<img/i.test(cap) && (cap.match(/ai-card/g) || []).length === 2);

const ideas = A.RENDER.ideas({ items: [{ title: evil, format: evil, why: evil }, { title: 'Stop doing this at the gym', format: '', why: '' }] });
ok('ideas are escaped, numbered and scored', !/<img/i.test(ideas) && ideas.indexOf('1. ') > 0 && ideas.indexOf('2. ') > 0 && /pill/.test(ideas));

ok('every renderer returns text for an empty list without throwing', ['bio', 'caption', 'ideas'].every(k => A.RENDER[k]({ items: [] }) === ''));

const form = (v) => ({ topic: { value: v.topic || '' }, platform: { value: 'tiktok' }, seconds: { value: '30' }, tone: { value: '' }, about: { value: v.about || '' }, audience: { value: '' }, cta: { value: '' }, niche: { value: v.niche || '' } });
ok('the script form needs a topic', A.CHECK.script(form({})) !== '' && A.CHECK.script(form({ topic: 'gym tips' })) === '');
ok('the bio and caption forms need a description', A.CHECK.bio(form({})) !== '' && A.CHECK.caption(form({})) !== '' && A.CHECK.bio(form({ about: 'home workouts' })) === '');
ok('the ideas form needs a niche', A.CHECK.ideas(form({})) !== '' && A.CHECK.ideas(form({ niche: 'fitness' })) === '');
ok('the request body carries the right fields', JSON.stringify(A.BODY.script(form({ topic: 'x' }))) === '{"topic":"x","platform":"tiktok","seconds":30,"tone":""}');
ok('esc covers all five dangerous characters', A.esc('<>"\'&') === '&lt;&gt;&quot;&#39;&amp;');
console.log('ALL OK (' + n + ')');

// Run with: node tests/length_check.js  (checks the best-video-length analyzer)
const assert = require('assert');
const L = require('../static/tool-length.js');
let n = 0; const ok = (name, c) => { assert(c, name); console.log('PASS ' + name); n++; };

ok('parses lengths', L.parseLength('45') === 45 && L.parseLength('45s') === 45 && L.parseLength('0:45') === 45 && L.parseLength('1:30') === 90 && L.parseLength('1m 30s') === 90 && L.parseLength('1.5m') === 90);
ok('rejects junk lengths', isNaN(L.parseLength('abc')) && isNaN(L.parseLength('')));
ok('parses "length, views"', (() => { const r = L.parse('0:34, 4200'); return r.rows.length === 1 && r.rows[0].seconds === 34 && r.rows[0].views === 4200; })());
ok('parses "title, length, views"', (() => { const r = L.parse('Morning routine, 0:28, 18.4k'); return r.rows[0].title === 'Morning routine' && r.rows[0].seconds === 28 && r.rows[0].views === 18400; })());
ok('parses space separated and pipes', L.parse('0:34 4200').rows.length === 1 && L.parse('Q and A | 1:10 | 3,900').rows[0].seconds === 70);
ok('skips unreadable lines', (() => { const r = L.parse('hello\n0:30\n\n0:40, 100'); return r.rows.length === 1 && r.skipped === 2; })());
ok('needs at least 10 videos', L.analyze('0:30, 100\n0:40, 100').ok === false);

// 20-30 s videos do 3x better; everything else ~1000
const lines = [];
for (let i = 0; i < 8; i++) lines.push(`0:${20 + (i % 9)}, ${3000 + i * 10}`);           // 20-27 s
for (let i = 0; i < 8; i++) lines.push(`0:${40 + (i % 6)}, ${1000 + i * 10}`);            // 40-45 s
for (let i = 0; i < 8; i++) lines.push(`1:${10 + (i % 6)}, ${900 + i * 10}`);             // 70-75 s
const r = L.analyze(lines.join('\n'));
ok('analysis runs', r.ok === true && r.count === 24);
ok('best range is 15-30 s', r.best.label === '15–30 s' && r.best.n === 8);
ok('best range is well above normal', r.best.m > 2);
ok('top videos are shorter than weakest', r.topLength < r.bottomLength);
ok('enough data is not low confidence', r.lowConfidence === false);
ok('ranked buckets are in order', r.ranked.every((b, i) => i === 0 || r.ranked[i - 1].m >= b.m));
ok('bucket counts add up', r.buckets.reduce((a, b) => a + b.n, 0) === 24);

const up = [];
for (let i = 0; i < 20; i++) up.push(`0:${10 + i * 2}, ${500 + i * 200}`);                  // longer is better
ok('detects "longer is better"', L.analyze(up.join('\n')).trend === 'longer');
const down = [];
for (let i = 0; i < 20; i++) down.push(`0:${10 + i * 2}, ${4500 - i * 200}`);
ok('detects "shorter is better"', L.analyze(down.join('\n')).trend === 'shorter');
const flat = [];
for (let i = 0; i < 20; i++) flat.push(`0:${10 + i * 2}, ${i % 2 ? 1000 : 1100}`);
ok('detects "length does not matter"', L.analyze(flat.join('\n')).trend === 'flat');
ok('spearman is 0 when views never vary', L.spearman([1, 2, 3, 4], [5, 5, 5, 5]) === 0);

const same = [];
for (let i = 0; i < 12; i++) same.push(`0:30, ${1000 + i}`);
ok('all one length is flagged', L.analyze(same.join('\n')).onlyOneBucket === true);
ok('few videos is low confidence', L.analyze(lines.slice(0, 11).join('\n')).lowConfidence === true);
ok('zero median refused', L.analyze(Array(12).fill('0:30, 0').join('\n')).ok === false);
console.log('ALL OK (' + n + ')');

// Run with: node tests/analyzer_check.js  (checks the free video checker's maths against the app's own rules)
const assert = require('assert');
const A = require('../static/tool-analyzer.js');
let n = 0; const ok = (name, c) => { assert(c, name); console.log('PASS ' + name); n++; };

ok('parses plain, comma, k and m numbers', JSON.stringify(A.parse('100\n1,200\n12.4k\n1.2M').map(v => v.views)) === '[100,1200,12400,1200000]');
ok('parses titles with separators', (() => { const v = A.parse('Morning routine, 18400\nBTS - 9.6k views\nQ&A | 3,900')[0]; return v.title === 'Morning routine' && v.views === 18400; })());
ok('ignores lines without a number', A.parse('hello\n\n42').length === 1);
ok('median odd and even', A.median([1, 9, 5]) === 5 && A.median([2, 4, 6, 8]) === 5);
ok('needs at least 5 videos', A.analyze('1\n2\n3\n4').ok === false);
ok('zero median is refused', A.analyze('0\n0\n0\n0\n0\n5').ok === false);

const r = A.analyze('1000\n1000\n1000\n1000\n2000\n500\n3000', true);   // median 1000
ok('normal is the median', r.normal === 1000);
const by = Object.fromEntries(r.videos.map(v => [v.views, v]));
ok('2x median is a WINNER / Scale', by[2000].label === 'WINNER' && by[2000].verdict === 'Scale');
ok('0.5x median is a DUD / Tweak', by[500].label === 'DUD' && by[500].verdict === 'Tweak');
ok('1x median is AVERAGE / Hold', by[1000].label === 'AVERAGE' && by[1000].verdict === 'Hold');
ok('counts winners and duds', r.winners === 2 && r.duds === 1);

const s = A.analyze('100\n100\n100\n1000\n1000\n1000\n1000', true);       // median 1000, the 3 newest are 0.1x
ok('three newest duds flagged as stop', s.stop === true);
ok('oldest-first reverses the stop check', A.analyze('100\n100\n100\n1000\n1000\n1000\n1000', false).stop === false);
const sk = A.analyze('100\n100\n100\n100\n100\n100\n5000', true);
ok('skewed average is detected', sk.skewed === true && sk.normal === 100);
ok('caps at 100 videos', A.analyze(Array(150).fill('500').join('\n')).count === 100);
console.log('ALL OK (' + n + ')');

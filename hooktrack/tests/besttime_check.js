// Run with: node tests/besttime_check.js  (checks the best-time-to-post analyzer)
const assert = require('assert');
const B = require('../static/tool-besttime.js');
let n = 0; const ok = (name, c) => { assert(c, name); console.log('PASS ' + name); n++; };
const pad = v => (v < 10 ? '0' : '') + v;
const iso = (d, h, m) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(h)}:${pad(m || 0)}`;

ok('parses ISO with comma views', (() => { const r = B.parse('2026-10-04 18:30, 4200'); return r.rows.length === 1 && r.rows[0].views === 4200 && r.rows[0].when.getHours() === 18 && r.rows[0].when.getMinutes() === 30; })());
ok('parses space separated and k suffix', (() => { const r = B.parse('2026-10-04 18:30 4.2k'); return r.rows[0].views === 4200; })());
ok('parses "Oct 4, 2026 6:30 PM"', (() => { const r = B.parse('Oct 4, 2026 6:30 PM, 3,100'); return r.rows.length === 1 && r.rows[0].when.getHours() === 18 && r.rows[0].views === 3100; })());
ok('parses US month-first dates', (() => { const r = B.parse('10/04/2026 18:30, 900'); return r.rows.length === 1 && r.rows[0].when.getMonth() === 9 && r.rows[0].when.getDate() === 4; })());
ok('skips lines with no time, no year or no views', (() => { const r = B.parse('2026-10-04, 4200\nOct 4 6:30 PM, 100\n2026-10-04 18:30\nhello'); return r.rows.length === 0 && r.skipped === 4; })());
ok('too few videos is refused', B.analyze('2026-10-04 18:30, 100\n2026-10-05 18:30, 100').ok === false);

// a clear pattern: Fridays 6-9 pm do 4x better, everything else is ~1000, across 24 weeks
const lines = [];
const start = new Date(2026, 0, 5);                         // a Monday
for (let w = 0; w < 24; w++) for (const [off, h, v] of [[0, 9, 1000], [2, 12, 1100], [4, 19, 4000], [5, 10, 900]]) {
  const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + w * 7 + off);
  lines.push(`${iso(d, h, 5)}, ${v + (w % 3) * 20}`);
}
const r = B.analyze(lines.join('\n'));
ok('analysis runs', r.ok === true && r.count === 96);
ok('best day is Friday', B.DAYS[r.bestDay.day] === 'Friday');
ok('best time of day is 6-9 pm', B.BLOCKS[r.bestBlock.block] === '6–9 pm');
ok('best slot is Friday 6-9 pm with 24 videos', B.DAYS[r.best.day] === 'Friday' && B.BLOCKS[r.best.block] === '6–9 pm' && r.best.n === 24);
ok('multiple is well above normal', r.best.m > 2);
ok('plenty of data is not low confidence', r.lowConfidence === false);
ok('three ranked slots, best first', r.slots.length === 3 && r.slots[0].m >= r.slots[1].m && r.slots[1].m >= r.slots[2].m);

const few = B.analyze(lines.slice(0, 10).join('\n'));
ok('under 20 videos is flagged low confidence', few.ok && few.lowConfidence === true);
ok('Monday maps to day 0 and Sunday to 6', (() => { const mon = B.analyze(Array(8).fill('2026-01-05 09:00, 500').join('\n')); const sun = B.analyze(Array(8).fill('2026-01-11 09:00, 500').join('\n')); return mon.grid[0][3].n === 8 && sun.grid[6][3].n === 8; })());
ok('zero median refused', B.analyze(Array(9).fill('2026-01-05 09:00, 0').join('\n')).ok === false);
console.log('ALL OK (' + n + ')');

// Run with: node tests/repurpose_check.js  (checks the repurposing checklist's scoring)
const assert = require('assert');
const R = require('../static/tool-repurpose.js');
let n = 0; const ok = (name, c) => { assert(c, name); console.log('PASS ' + name); n++; };

const items = R.build('tiktok', 'shorts');
ok('builds the full checklist', items.length === R.ITEMS.length && items.length >= 15);
ok('every item has text, a reason and a group', items.every(i => i.text && i.why && i.group));
ok('item ids are unique', new Set(items.map(i => i.id)).size === items.length);
ok('text names both platforms', items.some(i => i.text.includes('TikTok')) && items.some(i => i.text.includes('YouTube Shorts')));
ok('wording follows the direction', R.build('reels', 'tiktok').some(i => i.text.includes('Instagram Reels')) && !R.build('reels', 'tiktok').some(i => i.text.includes('YouTube Shorts')));
ok('there are must-do items, but not all items', items.filter(i => i.critical).length >= 5 && items.filter(i => i.critical).length < items.length);

const none = R.score('tiktok', 'shorts', {});
ok('nothing done is "fix" with 0%', none.verdict === 'fix' && none.done === 0 && none.pct === 0 && none.missingCritical.length === items.filter(i => i.critical).length);

const all = {}; items.forEach(i => { all[i.id] = true; });
const full = R.score('tiktok', 'shorts', all);
ok('everything done is "ready" at 100%', full.verdict === 'ready' && full.pct === 100 && full.missing.length === 0);

const crit = {}; items.filter(i => i.critical).forEach(i => { crit[i.id] = true; });
const c = R.score('tiktok', 'shorts', crit);
ok('only must-dos done is "almost" when under 80%', c.missingCritical.length === 0 && (c.pct >= 80 ? c.verdict === 'ready' : c.verdict === 'almost'));

const most = Object.assign({}, all); delete most.audio;
const m = R.score('tiktok', 'shorts', most);
ok('one missing must-do forces "fix" even at a high score', m.verdict === 'fix' && m.pct >= 90 && m.missingCritical.length === 1 && m.missingCritical[0].id === 'audio');

const small = Object.assign({}, all); delete small.cover; delete small.tags;
const s = R.score('tiktok', 'shorts', small);
ok('only small items missing and 80%+ is "ready"', s.verdict === 'ready' && s.missing.length === 2);
console.log('ALL OK (' + n + ')');

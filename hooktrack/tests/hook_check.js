// Run with: node tests/hook_check.js  (checks the free hook scorer's logic)
const assert = require('assert');
const H = require('../static/tool-hook.js');
let n = 0; const ok = (name, c) => { assert(c, name); console.log('PASS ' + name); n++; };
const chk = (r, k) => r.checks.find(c => c.key === k);

ok('empty input scores nothing', H.score('') === null && H.score('   ') === null && H.score(null) === null);

const strong = H.score('3 mistakes ruining your sleep (and the one fix nobody tells you)');
ok('a hook with a number, a warning, "you" and an open loop scores strong', strong.score >= 90 && strong.label === 'strong');
ok('the maximum is 100 and the points add up', H.score('x').checks.reduce((s, c) => s + c.max, 0) === 100 && strong.score === strong.checks.reduce((s, c) => s + c.points, 0));

const weak = H.score('Hey guys, welcome back to my channel, today I am going to talk about some tips');
ok('a greeting opener loses the opener points', chk(weak, 'opener').points === 0 && chk(weak, 'opener').pass === false);
ok('a rambling greeting scores weak', weak.label === 'weak' && weak.score < 50);
ok('a long line loses length points', chk(weak, 'length').points < 20);

const bare = H.score('My morning routine');
ok('a bare topic scores weak and is told what is missing', bare.label === 'weak' && bare.tryThese.length > 0 && bare.tryThese.length <= 3);
ok('three words is too short to earn full length points', chk(bare, 'length').points === 5);

const stop = H.score('Stop doing this at the gym if you want to build muscle');
ok('a command that speaks to the viewer is okay', stop.label === 'okay' && chk(stop, 'viewer').pass && chk(stop, 'pattern').pass);
ok('it says which pattern it found', stop.patterns.includes('warning'));

const result = H.score('I lost 12 pounds in 30 days');
ok('a number and a timeframe count as specifics and a result', chk(result, 'specific').pass && result.patterns.includes('result') && result.patterns.includes('number'));

ok('a question counts as a pattern and an open loop', chk(H.score('Why does your hook never work?'), 'pattern').pass && chk(H.score('Why does your hook never work?'), 'loop').pass);
ok('full length points need 6 to 14 words', chk(H.score('one two three four five six'), 'length').points === 20 && chk(H.score('one two three four five'), 'length').points === 12 && chk(H.score(Array(15).fill('word').join(' ')), 'length').points === 12 && chk(H.score(Array(25).fill('word').join(' ')), 'length').points === 4);
ok('long words lose the plain-words point', chk(H.score('Comprehensive entrepreneurial infrastructure'), 'plain').points === 0 && chk(H.score('Stop doing this now'), 'plain').points === 5);

const ranked = H.rank(H.SAMPLE);
ok('several hooks are ranked from best to worst', ranked.length === 4 && ranked.every((r, i) => i === 0 || ranked[i - 1].score >= r.score));
ok('the strongest sample hook ranks first and the greeting last or near last', ranked[0].text.indexOf('3 mistakes') === 0 && ranked[ranked.length - 1].score <= ranked[0].score - 40);
ok('blank lines are ignored when ranking', H.rank(['', 'Stop doing this', '  ']).length === 1);

ok('odd input never throws', [ '🔥🔥🔥', '12345', '???', 'a'.repeat(5000), '<script>alert(1)</script>', 'Stop. Don\'t. Never.' ].every(t => { const r = H.score(t); return r && r.score >= 0 && r.score <= 100; }));
ok('the same hook always gets the same score', H.score('Stop doing this at the gym').score === H.score('Stop doing this at the gym').score);
ok('every failed check carries a fix and every passed one a reason', strong.checks.concat(weak.checks).every(c => typeof c.text === 'string' && c.text.length > 10));
console.log('ALL OK (' + n + ')');

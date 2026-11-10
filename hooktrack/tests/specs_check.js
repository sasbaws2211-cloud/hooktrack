// Run with: node tests/specs_check.js  (checks the short-form video spec checker's logic)
const assert = require('assert');
const S = require('../static/tool-specs.js');
let n = 0; const ok = (name, c) => { assert(c, name); console.log('PASS ' + name); n++; };
const by = (r, k) => r.platforms.find(p => p.key === k);
const kinds = (p) => Object.fromEntries(p.checks.map(c => [c.kind, c.status]));

ok('parses seconds, mm:ss and "1m 30s"', S.parseDuration('45') === 45 && S.parseDuration('0:45') === 45 && S.parseDuration('1:30') === 90 && S.parseDuration('1m 30s') === 90 && S.parseDuration('90s') === 90);
ok('rejects junk durations', isNaN(S.parseDuration('abc')) && isNaN(S.parseDuration('')));
ok('classifies shapes', S.classify(1080, 1920) === 'vertical916' && S.classify(1080, 1350) === 'vertical' && S.classify(1080, 1080) === 'square' && S.classify(1920, 1080) === 'horizontal' && S.classify(0, 0) === null);

ok('only YouTube Shorts figures are marked confirmed', S.SPECS.shorts.limitConfirmed === true && S.SPECS.reels.limitConfirmed === false && S.SPECS.tiktok.limitConfirmed === false);
ok('Shorts limit is 3 minutes', S.SPECS.shorts.limit === 180);

let r = S.analyze({ seconds: 45, width: 1080, height: 1920 });
ok('45s 9:16 fits Shorts firmly', by(r, 'shorts').overall === 'fits');
ok('45s 9:16 is "check" (not a firm pass) on unconfirmed platforms', by(r, 'reels').overall === 'check' || by(r, 'reels').overall === 'fits');
ok('unconfirmed length is never a firm "fits"', kinds(by(r, 'reels')).Length === 'check' && kinds(by(r, 'tiktok')).Length === 'check');

r = S.analyze({ seconds: 181, width: 1080, height: 1920 });
ok('181s is over the Shorts limit', by(r, 'shorts').overall === 'over' && kinds(by(r, 'shorts')).Length === 'over');
ok('181s is over the reported Reels limit', kinds(by(r, 'reels')).Length === 'over');
ok('181s is still within the reported TikTok limit (check)', kinds(by(r, 'tiktok')).Length === 'check');

r = S.analyze({ seconds: 601, width: 1080, height: 1920 });
ok('601s is over the reported TikTok limit', kinds(by(r, 'tiktok')).Length === 'over');

r = S.analyze({ seconds: 120, width: 1080, height: 1920 });
ok('120s mentions the older 90 second Reels limit', /older/.test(by(r, 'reels').checks[0].text));

r = S.analyze({ seconds: 30, width: 1920, height: 1080 });
ok('horizontal is not a Short', kinds(by(r, 'shorts')).Shape === 'over' && by(r, 'shorts').overall === 'over');
ok('horizontal gets bars or crop on Reels and TikTok', kinds(by(r, 'reels')).Shape === 'over' && kinds(by(r, 'tiktok')).Shape === 'over');

r = S.analyze({ seconds: 30, width: 1080, height: 1080 });
ok('square is fine for Shorts', kinds(by(r, 'shorts')).Shape === 'fits');
ok('square is accepted but may be cropped on Reels', kinds(by(r, 'reels')).Shape === 'check');

r = S.analyze({ seconds: 30, width: 2160, height: 3840 });
ok('4K is flagged against the Shorts 1080p maximum', /1080p/.test(by(r, 'shorts').checks.find(c => c.kind === 'Resolution').text));
r = S.analyze({ seconds: 30, width: 360, height: 640 });
ok('low resolution is flagged', /soft/.test(by(r, 'tiktok').checks.find(c => c.kind === 'Resolution').text));

ok('length only still works', S.analyze({ seconds: 40, width: NaN, height: NaN }).ok === true && by(S.analyze({ seconds: 40 }), 'shorts').checks.length === 1);
ok('nothing entered gives no result', S.analyze({ seconds: NaN, width: NaN, height: NaN }).ok === false);
console.log('ALL OK (' + n + ')');

// Tracking map: every vessel type has its own shape, replay positions follow the clock, trails stop at the replay time.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { renderMap, vesselShape } from '../../web/js/map.js';
import { trackView, trackState, totalMin } from '../../web/js/track-view.js';
import { VESSEL_TYPES, addMinutes } from '../../web/js/util.js';

const load = (n) => JSON.parse(fs.readFileSync(`web/data/${n}.json`, 'utf8'));
const s = {
  geo: load('geo'), ais: load('ais'), sar: load('sar'), assets: load('assets'), assumptions: load('assumptions'), weather: load('weather'),
  captured: [], assetAdjust: {}, server: null,
  ui: { view: 'track', entity: null, flash: [], layers: {}, mapf: { q: '', type: '', risk: '' }, vb: null },
};
const shapes = Object.keys(VESSEL_TYPES).map(vesselShape);
assert.equal(new Set(shapes).size, shapes.length, 'each vessel type needs a distinct shape');

const types = new Set(s.ais.vessels.map((v) => v.type));
for (const t of types) assert.ok(VESSEL_TYPES[t], `type ${t} missing from VESSEL_TYPES`);

const now = renderMap(s, {});
assert.equal((now.match(/class="m-vessel/g) || []).length, s.ais.vessels.length, 'every vessel drawn on the latest picture');
assert.ok(now.includes('m-country'), 'country borders drawn');

const start = s.ais.meta.track_start;
const early = renderMap(s, { at: addMinutes(start, 60), trailMin: 120 });
const late = renderMap(s, { at: addMinutes(start, 600), trailMin: 120 });
assert.notEqual(early, late, 'replay changes the picture');
const pos = (html, id) => html.match(new RegExp(`data-entity="${id}"[^>]*?\\s+transform="translate\\(([\\d.]+) ([\\d.]+)\\)`))?.slice(1).map(Number);
const moving = s.ais.vessels.find((v) => v.pts[3] && v.pts[30] && v.pts[3][2] > 8);
assert.notDeepEqual(pos(early, moving.id), pos(late, moving.id), 'a moving vessel is somewhere else later');
assert.ok(!early.includes('m-gapline'), 'radar-pass links hidden before the radar pass');

trackState(s).min = 0;
const html = trackView(s);
assert.ok(html.includes('tv-slider') && html.includes(`max="${totalMin(s)}"`));
for (const t of types) assert.ok(html.includes(`data-type="${t}"`), `legend chip for ${t}`);
console.log(`tracking map ok · ${types.size} vessel shapes · replay ${totalMin(s) / 60} h`);

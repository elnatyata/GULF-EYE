import fs from 'node:fs';
import { fusion, alerts, passSummary, triage } from '../../web/js/model.js';
import { responseRecs, fleetRecs } from '../../web/js/rules.js';
import * as V from '../../web/js/views.js';
const load = (n) => JSON.parse(fs.readFileSync(`tests/fixtures/original_data/${n}.json`, 'utf8'));
const s = { geo: load('geo'), ais: load('ais'), sar: load('sar'), assets: load('assets'), assumptions: load('assumptions'), weather: load('weather'), captured: [], assetAdjust: {},
  ui: { view: 'home', entity: null, flash: [], logFilter: 'all', logDay: null, event: null, layers: { tracks: true, zones: true, footprint: true, assets: true, platforms: true } } };
const ps = passSummary(s);
console.log(JSON.stringify({ ps, tri: triage(s), alerts: alerts(s).map((a) => `${a.id}:${a.type}:${a.risk}`), recs: responseRecs(s).length, fleet: fleetRecs(s).length }));
for (const v of ['homeView','weatherView','activitiesView','sarView','lastView','recsView','fleetView']) { const h = V[v](s); if (!h || h.length < 200) throw new Error(v); }
console.log('views ok');

// Prints the browser fusion result for a data folder (default web/data) as JSON, for the Python cross-check.
import fs from 'node:fs';
import { fusion } from '../../web/js/model.js';
const dir = process.argv[2] || 'web/data';
const load = (n) => JSON.parse(fs.readFileSync(`${dir}/${n}.json`, 'utf8'));
const s = { geo: load('geo'), ais: load('ais'), sar: load('sar'), assets: load('assets'), assumptions: load('assumptions'), captured: [], assetAdjust: {} };
const f = fusion(s);
console.log(JSON.stringify({
  matched: f.matches.length, infrastructure: f.infrastructure.length,
  dark: f.alerts.filter((a) => a.type !== 'ais_gap').map((a) => a.detId).sort(),
  gaps: f.gaps.filter((g) => g.status === 'matched').map((g) => g.vessel.id).sort(),
}));

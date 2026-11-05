// Regression test: the original AIS/SAR fusion and all original screens must behave exactly as before the upgrade.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
const out = execFileSync('node', ['tests/js/baseline.mjs'], { encoding: 'utf8' }).split('\n');
const expected = fs.readFileSync('tests/js/baseline.expected.json', 'utf8').trim();
if (out[0].trim() !== expected) { console.error('Fusion output changed!\n', out[0], '\n', expected); process.exit(1); }
if (!out.includes('views ok')) { console.error('A view failed to render'); process.exit(1); }
console.log('baseline unchanged · all 7 original views render');

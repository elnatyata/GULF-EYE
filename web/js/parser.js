// Rule-based parser: turns a typed or spoken radio-style note into a draft record.
// It is the offline fallback. When the server has an API key, serve.py exposes /api/interpret and a
// language model reads the note first (see capture.js); its answer goes through the same checks.
import { addDays, parseDate, isoDate, fmtCoord } from './util.js';
import { today, nowIso, alerts, openAlerts, alertTitle, zone, zoneAt, asset } from './model.js';

const UNITS = { zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19 };
const TENS = { twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 };

// "one hundred and fifty" -> "150", "a thousand" -> "1000", "one and a half" -> "1.5"
export function wordsToNumbers(text) {
  const words = text.split(/(\s+)/);
  const out = [];
  let i = 0;
  const isNum = (w) => w in UNITS || w in TENS || w === 'hundred' || w === 'thousand';
  while (i < words.length) {
    const w = words[i].toLowerCase();
    const next = (words[i + 2] || '').toLowerCase();
    const startsNumber = isNum(w) || (w === 'a' && (next === 'hundred' || next === 'thousand' || next === 'half'));
    if (!startsNumber || /^\s+$/.test(w)) { out.push(words[i]); i += 1; continue; }
    let total = 0;
    let current = 0;
    let j = i;
    let last = i;
    while (j < words.length) {
      const x = words[j].toLowerCase();
      if (/^\s+$/.test(x)) { j += 1; continue; }
      const after = (words[j + 2] || '').toLowerCase();
      if (x in UNITS) current += UNITS[x];
      else if (x in TENS) current += TENS[x];
      else if (x === 'hundred') current = (current || 1) * 100;
      else if (x === 'thousand') { total += (current || 1) * 1000; current = 0; }
      else if (x === 'a' && (after === 'hundred' || after === 'thousand' || after === 'half')) current += after === 'half' ? 0 : 1;
      else if (x === 'half') current += 0.5;
      else if (x === 'and' && (isNum(after) || after === 'a')) { /* "hundred and fifty" */ }
      else break;
      last = j;
      j += 1;
    }
    out.push(String(total + current));
    i = last + 1;
  }
  return out.join('');
}

export const REPORT_KINDS = [
  ['attack', 'Attack or shots fired'], ['boarding', 'Boarding'], ['kidnap', 'Kidnapping'],
  ['skiff', 'Suspicious skiff'], ['lights', 'Vessel without lights / AIS'], ['fishing', 'Illegal fishing'], ['transfer', 'Ship-to-ship transfer'],
];
const KIND_WORDS = [
  ['attack', /\b(attack(ed|ing)?|shots?|fired|gunfire|hijack(ed)?)\b/],
  ['boarding', /\b(board(ed|ing)?|climbed)\b/],
  ['kidnap', /\b(kidnap(ped)?|abduct(ed)?|hostage)\b/],
  ['transfer', /\b(transfer(ring)?|alongside|bunkering|sts)\b/],
  ['fishing', /\b(trawl(er|ing)?|illegal fishing|fishing without|nets?)\b/],
  ['lights', /\b(no lights|without lights|lights off|no ais|ais off|dark)\b/],
  ['skiff', /\b(skiffs?|speedboats?|small boats?|fast boats?|suspicious|approach(ed|ing)?|following)\b/],
];

const OP_WORDS = [
  ['refuel', /\b(refuel(l)?ed|refuel(l)?ing|refuel|bunkered|topped up|top up|fuelled|fueled)\b/],
  ['resolve', /\b(resolved|resolve|false alarm|cleared|closed|verified|confirmed|stand down|stood down)\b/],
  ['dispatch', /\b(send|sent|dispatch(ed)?|assign(ed)?|deploy(ed)?|task(ed)?|intercept(ing)?|launch(ed)?|scramble(d)?)\b/],
  ['report', /\b(report(ed|s)?|spotted|sighted|saw|seen|observed|heard|call(ed)?)\b/],
];
export const ASSET_ROLES_FOR_OP = { dispatch: ['OPV', 'PATROL', 'RHIB', 'AIR'], refuel: ['OPV', 'PATROL', 'RHIB', 'AIR'] };

const PLACES = {
  'bonny approaches': [3.9, 6.9, 'the Bonny approaches'], 'fairway buoy': [3.9, 6.9, 'the Bonny fairway buoy'], 'bonny fairway': [3.9, 6.9, 'the Bonny fairway'],
  bonny: [4.40, 7.15, 'Bonny'], 'niger delta': [4.0, 6.6, 'the Niger Delta'], lagos: [6.30, 3.40, 'Lagos'], lekki: [6.20, 4.0, 'Lekki'],
  douala: [3.85, 9.50, 'Douala'], cameroon: [3.6, 9.3, 'the Cameroon coast'], bioko: [3.9, 8.7, 'Bioko'], malabo: [3.9, 8.7, 'Malabo'],
  calabar: [4.40, 8.40, 'Calabar'], forcados: [5.2, 5.3, 'Forcados'], cotonou: [6.15, 2.45, 'Cotonou'], benin: [5.0, 2.7, 'the Benin offshore'],
  lome: [6.0, 1.25, 'Lomé'], togo: [5.9, 1.3, 'Togo'], tema: [5.55, 0.65, 'Tema'], ghana: [5.4, 0.9, 'Ghana'],
};

// ---- helpers
function findAssets(s, text) {
  const hits = [];
  for (const a of s.assets.assets) {
    const names = [a.name.toLowerCase(), ...a.aliases];
    const hit = names.filter((n) => new RegExp(`\\b${n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(text)).sort((x, y) => y.length - x.length)[0];
    if (hit) hits.push({ asset: a, len: hit.length });
  }
  if (hits.length) {
    const best = Math.max(...hits.map((h) => h.len));
    const top = hits.filter((h) => h.len === best).map((h) => h.asset);
    return { matched: top.length === 1 ? top[0] : null, candidates: top.length > 1 ? top : [] };
  }
  // "interceptor" or "patrol boat" alone -> several candidates, the assistant asks which one.
  const generic = [['interceptor', 'RHIB'], ['rhib', 'RHIB'], ['patrol boat', 'PATROL'], ['patrol vessel', 'OPV'], ['helicopter', 'AIR']];
  for (const [word, type] of generic) {
    if (text.includes(word)) {
      const c = s.assets.assets.filter((a) => a.type === type);
      return { matched: c.length === 1 ? c[0] : null, candidates: c.length > 1 ? c : [] };
    }
  }
  return { matched: null, candidates: [] };
}

function findAlertIds(s, text) {
  const ids = new Set();
  const all = alerts(s);
  const re = /\b([dgr])\s*-?\s*0*(\d{1,4})\b/g;
  let m;
  while ((m = re.exec(text))) {
    const letter = m[1].toUpperCase();
    const n = m[2];
    const hit = all.find((a) => a.id.startsWith(`${letter}-`) && a.id.slice(2).replace(/^0+/, '') === n.replace(/^0+/, ''))
      || (letter === 'G' ? all.find((a) => a.id.startsWith('G-') && a.id.endsWith(n)) : null);
    if (hit) ids.add(hit.id);
  }
  const named = /\b(alert|contact|detection|target)s?\s+((?:\d{1,4}(?:\s*(?:,|and|&)\s*)?)+)/g;
  while ((m = named.exec(text))) {
    for (const n of m[2].match(/\d{1,4}/g)) {
      const hit = all.find((a) => a.id.startsWith('D-') && Number(a.id.slice(2)) === Number(n));
      if (hit) ids.add(hit.id);
    }
  }
  return [...ids];
}

function findPlace(text) {
  const keys = Object.keys(PLACES).sort((a, b) => b.length - a.length);
  const key = keys.find((k) => new RegExp(`\\b${k}\\b`).test(text));
  if (!key) return null;
  const [lat, lon, name] = PLACES[key];
  return { lat, lon, name };
}

function findAmount(text) {
  const m = text.match(/(\d+(?:[.,]\d+)?)\s*(kilo\s*litres?|kilolitres?|kl|k\s*litres?|thousand litres?|litres?|liters?|ltrs?|l)\b/);
  if (!m) return null;
  let v = Number(m[1].replace(',', '.'));
  if (/^(kilo|kl|k\s|thousand)/.test(m[2])) v *= 1000;
  return v;
}

function findTime(s, text) {
  const t = today(s);
  const hm = text.match(/\bat\s+(\d{1,2})[:h.]?(\d{2})?\b/);
  let time = null;
  if (hm && Number(hm[1]) < 24) time = `${String(hm[1]).padStart(2, '0')}:${hm[2] || '00'}`;
  if (/\byesterday\b/.test(text)) return { date: addDays(t, -1), time, assumed: false };
  if (/\b(today|this morning|this afternoon|this evening|tonight|now|just now)\b/.test(text)) return { date: t, time, assumed: false };
  const days = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  const m = text.match(/\b(?:on\s+)?(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b/);
  if (m) {
    const d = parseDate(t);
    const back = (d.getUTCDay() - days.indexOf(m[1]) + 7) % 7;
    return { date: isoDate(new Date(d.getTime() - back * 86400000)), time, assumed: false };
  }
  return { date: t, time, assumed: true };
}

export function parseNote(s, raw) {
  const text = wordsToNumbers(raw.toLowerCase()).replace(/[’']/g, "'");
  const draft = {
    raw, type: null, assetId: null, assetCandidates: [], alertIds: [], alertsAmbiguous: null, place: null, kind: null,
    outcome: null, amount: null, date: null, time: null, dateAssumed: false, notes: [],
  };
  draft.type = OP_WORDS.find(([, re]) => re.test(text))?.[0] ?? null;

  const { matched, candidates } = findAssets(s, text);
  if (matched) draft.assetId = matched.id;
  draft.assetCandidates = candidates.map((a) => a.id);

  draft.alertIds = findAlertIds(s, text);
  draft.place = findPlace(text);
  draft.kind = KIND_WORDS.find(([, re]) => re.test(text))?.[0] ?? null;
  if (!draft.type && draft.kind && !draft.assetId && !draft.alertIds.length) draft.type = 'report';
  if (!draft.type && (draft.assetId || draft.assetCandidates.length) && draft.alertIds.length) draft.type = 'dispatch';
  if (draft.type === 'resolve') draft.outcome = /\bfalse alarm|nothing|harmless|stand down|stood down\b/.test(text) ? 'false_alarm' : 'confirmed';
  if (draft.type === 'refuel') draft.amount = findAmount(text);

  // "all alerts" is ambiguous; "all alerts near Bonny" is not.
  const all = /\b(all|every)\b.*\b(alerts?|contacts?|detections?|targets?)\b/.test(text);
  if (all && !draft.alertIds.length) {
    const open = openAlerts(s);
    const zoned = draft.place ? open.filter((a) => a.zoneId && zoneAt(s, draft.place.lon, draft.place.lat)?.id === a.zoneId) : [];
    if (zoned.length) draft.alertIds = zoned.map((a) => a.id);
    else draft.alertsAmbiguous = true;
  }
  const t = findTime(s, text);
  draft.date = t.date;
  draft.time = t.time;
  draft.dateAssumed = t.assumed;
  return draft;
}

// Next question the assistant asks, or null when the draft is complete.
export function nextQuestion(s, draft) {
  if (!draft.type) {
    return {
      key: 'type', text: 'What do you want to log?',
      options: [['dispatch', 'Send a patrol asset'], ['report', 'A sighting or incident'], ['resolve', 'Close an alert'], ['refuel', 'A refuelling']]
        .map(([value, label]) => ({ label, value })),
    };
  }
  if (draft.assetCandidates.length && !draft.assetId) {
    return {
      key: 'assetId', text: 'Which one exactly?',
      options: draft.assetCandidates.map((id) => {
        const a = asset(s, id);
        return { label: `${a.name} (${a.base})`, value: id };
      }),
    };
  }
  if (draft.alertsAmbiguous) {
    const open = openAlerts(s);
    const high = open.filter((a) => ['high', 'critical'].includes(a.risk));
    return {
      key: 'alertIds', text: `"All alerts": all ${open.length} open alerts, or only the ${high.length} high-risk ones?`,
      options: [{ label: `All ${open.length} open alerts`, value: 'all' }, { label: `The ${high.length} high-risk alerts`, value: 'high' }],
    };
  }
  if (draft.type === 'dispatch' && !draft.assetId) {
    return {
      key: 'assetId', text: 'Which patrol asset?',
      options: s.assets.assets.slice(0, 7).map((a) => ({ label: a.name, value: a.id })),
    };
  }
  if ((draft.type === 'dispatch' || draft.type === 'resolve') && !draft.alertIds.length) {
    const list = (draft.type === 'dispatch' ? openAlerts(s) : alerts(s).filter((a) => a.state.code !== 'resolved')).slice(0, 5);
    return {
      key: 'alertIds', text: draft.type === 'dispatch' ? 'To which alert?' : 'Which alert?',
      options: list.map((a) => ({ label: `${a.id} · ${alertTitle(s, a).replace(/ · .*/, '')}`, value: [a.id] })),
    };
  }
  if (draft.type === 'report') {
    if (!draft.place) {
      return {
        key: 'place', text: 'Where was it?',
        options: [['Bonny', 'Bonny'], ['Douala', 'Douala'], ['Lagos', 'Lagos'], ['Benin offshore', 'benin'], ['Calabar', 'Calabar']]
          .map(([label, k]) => ({ label, value: findPlace(k.toLowerCase()) })),
      };
    }
    if (!draft.kind) {
      return { key: 'kind', text: 'What was observed?', options: REPORT_KINDS.slice(0, 6).map(([value, label]) => ({ label, value })) };
    }
  }
  if (draft.type === 'resolve' && !draft.outcome) {
    return { key: 'outcome', text: 'What was the outcome?', options: [{ label: 'False alarm', value: 'false_alarm' }, { label: 'Threat confirmed and handled', value: 'confirmed' }] };
  }
  if (draft.type === 'refuel') {
    if (!draft.assetId) {
      return { key: 'assetId', text: 'Which asset was refuelled?', options: s.assets.assets.slice(0, 7).map((a) => ({ label: a.name, value: a.id })) };
    }
    if (draft.amount == null) {
      const a = asset(s, draft.assetId);
      return { key: 'amount', text: 'How many litres?', options: [0.25, 0.5, 1].map((f) => ({ label: `${Math.round(a.capacity * f).toLocaleString('en-GB')} L`, value: Math.round(a.capacity * f) })) };
    }
  }
  return null;
}

export function applyAnswer(s, draft, key, value) {
  const d = { ...draft };
  if (key === 'type') d.type = value;
  if (key === 'assetId') { d.assetId = value; d.assetCandidates = []; }
  if (key === 'alertIds') {
    d.alertsAmbiguous = null;
    if (value === 'all') d.alertIds = openAlerts(s).map((a) => a.id);
    else if (value === 'high') d.alertIds = openAlerts(s).filter((a) => ['high', 'critical'].includes(a.risk)).map((a) => a.id);
    else d.alertIds = value;
  }
  if (key === 'place') d.place = value;
  if (key === 'kind') d.kind = value;
  if (key === 'outcome') d.outcome = value;
  if (key === 'amount') d.amount = value;
  return d;
}

// Accept the JSON a language model returned (see serve.py) and merge it into a draft.
// Every id is checked against the real data; anything unknown is dropped so the assistant asks instead.
export function draftFromLlm(s, raw, j) {
  const d = parseNote(s, raw);
  const clean = { ...d };
  if (['dispatch', 'report', 'resolve', 'refuel'].includes(j.type)) clean.type = j.type;
  if (j.asset && asset(s, j.asset)) { clean.assetId = j.asset; clean.assetCandidates = []; }
  if (Array.isArray(j.alerts)) {
    const known = alerts(s).map((a) => a.id);
    const ids = j.alerts.filter((id) => known.includes(id));
    if (ids.length) { clean.alertIds = ids; clean.alertsAmbiguous = null; }
  }
  if (j.place && typeof j.place.lat === 'number' && typeof j.place.lon === 'number') clean.place = { lat: j.place.lat, lon: j.place.lon, name: j.place.name || null };
  if (REPORT_KINDS.some(([k]) => k === j.kind)) clean.kind = j.kind;
  if (j.outcome === 'false_alarm' || j.outcome === 'confirmed') clean.outcome = j.outcome;
  if (typeof j.litres === 'number' && j.litres > 0) clean.amount = j.litres;
  return clean;
}

// Short summary of what the parser understood, shown back to the operator.
export function understood(s, d) {
  const parts = [];
  const types = { dispatch: 'dispatch', report: 'field report', resolve: 'close alert', refuel: 'refuelling' };
  if (d.type) parts.push(types[d.type]);
  if (d.assetId) parts.push(asset(s, d.assetId).name);
  if (d.alertIds.length) parts.push(d.alertIds.length > 2 ? `${d.alertIds.length} alerts` : d.alertIds.join(', '));
  if (d.kind) parts.push(REPORT_KINDS.find(([k]) => k === d.kind)?.[1].toLowerCase());
  if (d.place) parts.push(d.place.name || fmtCoord(d.place.lat, d.place.lon));
  if (d.amount != null) parts.push(`${Math.round(d.amount).toLocaleString('en-GB')} L`);
  return parts.filter(Boolean).join(' · ');
}
export { zone, nowIso };

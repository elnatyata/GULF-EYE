// Derived views of the maritime data: vessel positions, the AIS/SAR fusion, alerts, patrol assets and the log.
import {
  sum, minutesBetween, addMinutes, haversineKm, KM_PER_NM, moveBy, pointInPoly, nmBetween, fmtDate,
} from './util.js';

export const nowIso = (s) => s.ais.meta.now;
export const today = (s) => nowIso(s).slice(0, 10);
export const sarTime = (s) => s.sar.scene.time;
export const vessel = (s, id) => s.ais.vessels.find((v) => v.id === id);
export const asset = (s, id) => s.assets.assets.find((a) => a.id === id);
export const zone = (s, id) => s.geo.zones.find((z) => z.id === id);
export const zoneAt = (s, lon, lat) => s.geo.zones.find((z) => pointInPoly(lon, lat, z.poly)) || null;
export const port = (s, name) => s.geo.ports.find((p) => p.name === name);
export const allEvents = (s) => [...staticEvents(s), ...s.captured];
export const capturedOf = (s, type) => s.captured.filter((e) => e.type === type);

// ---------------------------------------------------------------- AIS positions
const stepMin = (s) => s.ais.meta.step_min;
const idxAt = (s, tIso) => minutesBetween(s.ais.meta.track_start, tIso) / stepMin(s);

export function lastReport(s, v) {
  for (let i = v.pts.length - 1; i >= 0; i -= 1) {
    if (v.pts[i]) {
      const [lat, lon, sog, cog] = v.pts[i];
      return { i, t: addMinutes(s.ais.meta.track_start, i * stepMin(s)), lat, lon, sog, cog };
    }
  }
  return null;
}

// Position at time t: interpolated between reports, or dead-reckoned from the last report
// when it is recent enough. Returns null when the vessel is silent (AIS gap).
export function positionAt(s, v, tIso, maxExtrapolateMin = s.assumptions.fusion.max_extrapolate_min) {
  const f = idxAt(s, tIso);
  const lo = Math.floor(f);
  const hi = Math.ceil(f);
  const a = v.pts[lo];
  const b = v.pts[hi];
  if (a && b) {
    const w = f - lo;
    return {
      lat: a[0] + (b[0] - a[0]) * w, lon: a[1] + (b[1] - a[1]) * w, sog: a[2], cog: a[3], ageMin: 0, mode: 'reported',
    };
  }
  let i = lo;
  while (i >= 0 && !v.pts[i]) i -= 1;
  if (i < 0) return null;
  const ageMin = (f - i) * stepMin(s);
  if (ageMin > maxExtrapolateMin) return null;
  const [lat, lon, sog, cog] = v.pts[i];
  const p = moveBy(lat, lon, cog, sog * KM_PER_NM * (ageMin / 60));
  return { ...p, sog, cog, ageMin, mode: 'extrapolated' };
}

export const currentPosition = (s, v) => positionAt(s, v, nowIso(s), 24 * 60) || lastReport(s, v);

export function trackPoints(v) {
  return v.pts.filter(Boolean).map(([lat, lon]) => ({ lat, lon }));
}

// ---------------------------------------------------------------- AIS + SAR fusion
const fusionCache = new WeakMap();

export function fusion(s) {
  if (fusionCache.has(s.sar)) return fusionCache.get(s.sar);
  const f = s.assumptions.fusion;
  const T = sarTime(s);
  const dets = s.sar.detections.map((d) => ({ ...d }));
  const footprint = s.sar.scene.footprint;
  const inFootprint = (lat, lon) => pointInPoly(lon, lat, footprint);

  const live = [];
  const silent = [];
  for (const v of s.ais.vessels) {
    const p = positionAt(s, v, T);
    if (p) live.push({ v, p });
    else {
      const last = lastReport(s, v);
      if (last) silent.push({ v, last, gapMin: minutesBetween(last.t, T) });
    }
  }

  const lengthOk = (det, v) => Math.abs(det.length_m - v.length_m) / v.length_m <= f.max_length_mismatch;

  // 1. one-to-one matching, nearest first: "match found" = normal vessel
  const pairs = [];
  for (const det of dets) {
    for (const { v, p } of live) {
      const d = haversineKm(det.lat, det.lon, p.lat, p.lon);
      if (d <= f.match_radius_km && lengthOk(det, v)) pairs.push({ det, v, p, distKm: d });
    }
  }
  pairs.sort((a, b) => a.distKm - b.distKm);
  const matches = [];
  const usedDet = new Set();
  const usedVessel = new Set();
  for (const pr of pairs) {
    if (usedDet.has(pr.det.id) || usedVessel.has(pr.v.id)) continue;
    usedDet.add(pr.det.id);
    usedVessel.add(pr.v.id);
    matches.push(pr);
  }

  // 2. known infrastructure (platforms are bright static radar returns, not threats)
  const infrastructure = [];
  for (const det of dets.filter((d) => !usedDet.has(d.id))) {
    const pf = s.geo.platforms.find((x) => haversineKm(det.lat, det.lon, x.lat, x.lon) <= f.platform_radius_km);
    if (pf) { usedDet.add(det.id); infrastructure.push({ det, platform: pf }); }
  }

  // 3. vessels that went silent: look for a detection near the dead-reckoned position
  const gaps = [];
  const alerts = [];
  for (const g of silent.filter((x) => x.gapMin >= f.gap_minutes)) {
    const hours = g.gapMin / 60;
    const travelKm = g.last.sog * KM_PER_NM * hours;
    const predicted = moveBy(g.last.lat, g.last.lon, g.last.cog, travelKm);
    const tol = Math.max(f.gap_min_tolerance_km, f.match_radius_km + f.gap_drift_pct * travelKm);
    const entry = { vessel: g.v, last: g.last, gapMin: g.gapMin, predicted, tolKm: tol, status: 'outside' };
    if (inFootprint(predicted.lat, predicted.lon)) {
      const cands = dets.filter((d) => !usedDet.has(d.id) && lengthOk(d, g.v))
        .map((d) => ({ d, dist: haversineKm(d.lat, d.lon, predicted.lat, predicted.lon) }))
        .filter((c) => c.dist <= tol).sort((a, b) => a.dist - b.dist);
      if (cands.length) {
        const det = cands[0].d;
        usedDet.add(det.id);
        entry.status = 'matched';
        entry.det = det;
        const z = zoneAt(s, det.lon, det.lat);
        alerts.push({
          id: `G-${g.v.id.slice(-4)}`, type: 'ais_gap', detId: det.id, vesselId: g.v.id, lat: det.lat, lon: det.lon,
          lengthM: det.length_m, confidence: det.confidence, zoneId: z?.id || null, gapMin: g.gapMin,
          offsetKm: cands[0].dist, lastKnown: g.last, time: T, baseRisk: 'high',
          score: 70 + det.confidence * 20 + (z?.weight || 0),
        });
      } else entry.status = 'unseen';
    }
    gaps.push(entry);
  }

  // 4. everything left is a dark vessel candidate
  const dark = dets.filter((d) => !usedDet.has(d.id)).map((det) => {
    const z = zoneAt(s, det.lon, det.lat);
    const near = matches.find((m) => ['tanker', 'cargo'].includes(m.v.type)
      && haversineKm(det.lat, det.lon, m.p.lat, m.p.lon) <= f.sts_radius_km);
    return { det, z, near };
  });
  const flagged = dark.filter((x) => x.det.confidence >= f.low_confidence);
  const clusterOf = new Map();
  const seen = new Set();
  for (const a of flagged) {
    if (seen.has(a.det.id)) continue;
    const group = [];
    const queue = [a];
    seen.add(a.det.id);
    while (queue.length) {
      const cur = queue.pop();
      group.push(cur);
      for (const b of flagged) {
        if (!seen.has(b.det.id) && haversineKm(cur.det.lat, cur.det.lon, b.det.lat, b.det.lon) <= f.cluster_radius_km) {
          seen.add(b.det.id);
          queue.push(b);
        }
      }
    }
    if (group.length >= 2) group.forEach((x) => clusterOf.set(x.det.id, group.map((y) => y.det.id)));
  }
  for (const { det, z, near } of dark) {
    const low = det.confidence < f.low_confidence;
    const cluster = clusterOf.get(det.id) || null;
    const piracy = z?.kind === 'piracy';
    const isSts = Boolean(near);
    let baseRisk = 'medium';
    if (low) baseRisk = 'low';
    else if (piracy || (cluster && cluster.length >= 3)) baseRisk = 'high';
    const score = low ? det.confidence * 20
      : 35 + det.confidence * 30 + (z?.weight || 0) + (cluster ? 15 : 0) + (isSts ? 10 : 0);
    alerts.push({
      id: det.id, type: isSts ? 'sts' : 'dark_vessel', detId: det.id, lat: det.lat, lon: det.lon,
      lengthM: det.length_m, confidence: det.confidence, zoneId: z?.id || null, lowConf: low,
      cluster, nearVesselId: near ? near.v.id : null, time: T, baseRisk, score,
    });
  }

  const result = {
    time: T, matches, infrastructure, gaps, alerts, detections: dets.length,
    live: live.length, silent: silent.length,
  };
  fusionCache.set(s.sar, result);
  return result;
}

// ---------------------------------------------------------------- alerts (fusion + field reports + status)
export function reportAlerts(s) {
  const f = s.assumptions.fusion;
  return capturedOf(s, 'report').map((e) => {
    const z = zoneAt(s, e.lon, e.lat);
    const grave = ['attack', 'boarding', 'kidnap'].includes(e.kind);
    return {
      id: e.entity, type: 'report', lat: e.lat, lon: e.lon, zoneId: z?.id || null, kind: e.kind, place: e.place,
      time: e.time, text: e.raw, baseRisk: grave ? 'critical' : (z?.kind === 'piracy' ? 'high' : 'medium'),
      score: grave ? 120 : 60 + (z?.weight || 0), source: 'captured', corroboratesWindowH: f.corroboration_hours,
    };
  });
}

export function alertState(s, id) {
  let state = { code: 'new', assetId: null, at: null };
  for (const e of s.captured) {
    if (e.entity !== id) continue;
    if (e.type === 'dispatch') state = { code: 'assigned', assetId: e.assetId, at: e.time };
    if (e.type === 'resolve') state = { code: e.outcome === 'false_alarm' ? 'false_alarm' : 'resolved', assetId: state.assetId, at: e.time };
  }
  return state;
}

export function alerts(s) {
  const f = s.assumptions.fusion;
  const reports = reportAlerts(s);
  const list = [...fusion(s).alerts, ...reports].map((a) => ({ ...a, risk: a.baseRisk, corroboratedBy: [] }));
  for (const r of reports) {
    const target = list.find((x) => x.id === r.id);
    for (const a of list.filter((x) => x.type !== 'report' && !x.lowConf)) {
      const close = haversineKm(a.lat, a.lon, r.lat, r.lon) <= f.corroboration_radius_km;
      if (close) {
        a.corroboratedBy.push(r.id);
        a.risk = 'critical';
        a.score += 25;
        target.corroborates = [...(target.corroborates || []), a.id];
      }
    }
  }
  for (const a of list) a.state = alertState(s, a.id);
  return list.sort((x, y) => y.score - x.score);
}

export const alertById = (s, id) => alerts(s).find((a) => a.id === id);
export const openAlerts = (s) => alerts(s).filter((a) => a.state.code === 'new' && !a.lowConf);
export const alertTitle = (s, a) => {
  const z = a.zoneId ? zone(s, a.zoneId).name : nearestPortName(s, a);
  if (a.type === 'ais_gap') return `${vessel(s, a.vesselId).name} (AIS off)`;
  if (a.type === 'report') return `Field report · ${a.place || z}`;
  if (a.type === 'sts') return `Possible transfer · ${z}`;
  return `${a.lowConf ? 'Low-confidence contact' : 'Dark vessel'} · ${z}`;
};

export function nearestPortName(s, a) {
  const best = s.geo.ports.map((p) => ({ p, d: haversineKm(a.lat, a.lon, p.lat, p.lon) })).sort((x, y) => x.d - y.d)[0];
  return `near ${best.p.name}`;
}

export function passSummary(s) {
  const fu = fusion(s);
  const flagged = fu.alerts.filter((a) => !a.lowConf);
  return {
    detections: fu.detections, matched: fu.matches.length, infrastructure: fu.infrastructure.length,
    dark: flagged.filter((a) => a.type !== 'ais_gap').length, gaps: flagged.filter((a) => a.type === 'ais_gap').length,
    lowConf: fu.alerts.filter((a) => a.lowConf).length,
    unmatchedShare: flagged.length / Math.max(1, fu.detections - fu.infrastructure.length),
  };
}

export function triage(s) {
  const list = alerts(s).filter((a) => !a.lowConf);
  const count = (code) => list.filter((a) => a.state.code === code).length;
  return { total: list.length, new: count('new'), assigned: count('assigned'), resolved: count('resolved') + count('false_alarm') };
}

export function zoneStats(s) {
  return s.geo.zones.map((z) => ({
    zone: z, current: alerts(s).filter((a) => a.zoneId === z.id && a.type !== 'report' && !a.lowConf).length,
  }));
}

// ---------------------------------------------------------------- patrol assets
export const assetFuel = (s, id) => asset(s, id).fuel + (s.assetAdjust[id] || 0);

export function assetTask(s, a) {
  const mine = s.captured.filter((e) => e.type === 'dispatch' && e.assetId === a.id)
    .map((e) => e.entity).filter((id) => alertState(s, id).code === 'assigned' && alertState(s, id).assetId === a.id);
  return mine.length ? mine : null;
}

export function assetPosition(s, a) {
  const task = assetTask(s, a);
  if (task) {
    const al = alertById(s, task[0]);
    if (al) return { lat: al.lat, lon: al.lon, onTask: true };
  }
  return { lat: a.lat, lon: a.lon, onTask: false };
}

// Plan: nearest-neighbour order, then back to base. Fuel in litres, times in hours.
export function missionPlan(s, a, targets) {
  const reserve = s.assumptions.response.reserve_pct;
  const onScene = s.assumptions.response.on_scene_h;
  let here = assetPosition(s, a);
  const todo = [...targets];
  const legs = [];
  let t = 0;
  let nm = 0;
  while (todo.length) {
    todo.sort((x, y) => nmBetween(here, x) - nmBetween(here, y));
    const next = todo.shift();
    const d = nmBetween(here, next);
    nm += d;
    t += d / a.speed_kn;
    legs.push({ id: next.id, nm: d, etaH: t });
    t += onScene;
    here = next;
  }
  const back = nmBetween(here, { lat: a.lat, lon: a.lon });
  nm += back;
  const fuel = nm * a.burn_l_per_nm;
  const level = assetFuel(s, a.id);
  return {
    legs, totalNm: nm, etaFirstH: legs[0]?.etaH ?? 0, etaLastH: legs.length ? legs[legs.length - 1].etaH : 0,
    fuelL: fuel, level, feasible: fuel <= level * (1 - reserve), returnNm: back,
  };
}

export function bestAssets(s, alert, role = 'intercept') {
  return s.assets.assets
    .filter((a) => a.role === role && !assetTask(s, a))
    .map((a) => ({ asset: a, plan: missionPlan(s, a, [alert]) }))
    .sort((x, y) => (Number(y.plan.feasible) - Number(x.plan.feasible)) || (x.plan.etaFirstH - y.plan.etaFirstH));
}

export function assetUsage(s, a) {
  const tasked = s.captured.filter((e) => e.type === 'dispatch' && e.assetId === a.id);
  const refuel = s.captured.filter((e) => e.type === 'refuel' && e.assetId === a.id);
  return {
    taskedCount: tasked.length, usedFuel: sum(tasked.map((e) => e.fuel_l || 0)), refuelled: sum(refuel.map((e) => e.litres || 0)),
    last: [...tasked, ...refuel].sort((x, y) => y.time.localeCompare(x.time))[0] || null,
  };
}

export function recentAssetUse(s, limit = 4) {
  const out = [];
  const seen = new Set();
  for (const e of [...s.captured].filter((x) => x.assetId).sort((a, b) => b.time.localeCompare(a.time))) {
    if (seen.has(e.assetId)) continue;
    seen.add(e.assetId);
    out.push({ name: asset(s, e.assetId).name, time: e.time, type: e.type, entity: e.entity });
    if (out.length === limit) break;
  }
  return out;
}

// ---------------------------------------------------------------- event log
function staticEvents(s) {
  const fu = fusion(s);
  const out = [];
  for (const a of fu.alerts) {
    out.push({
      id: `ev-${a.id}`, type: a.type, entity: a.id, time: a.time, status: 'done', source: a.type === 'ais_gap' ? 'ais' : 'sar',
      campaign: a.time.slice(0, 10),
    });
  }
  for (const g of fu.gaps) {
    out.push({
      id: `ev-gap-${g.vessel.id}`, type: 'ais_gap', entity: g.vessel.id, time: addMinutes(g.last.t, s.ais.meta.step_min), status: 'done',
      source: 'ais', note: 'AIS reports stop', campaign: g.last.t.slice(0, 10), gapOnset: true,
    });
  }
  const next = addMinutes(sarTime(s), 6 * 24 * 60);
  out.push({ id: 'ev-next-pass', type: 'pass', entity: null, time: next, status: 'planned', source: 'simulated', campaign: next.slice(0, 10) });
  return out;
}

export function lastActivities(s, limit = 18) {
  const t = nowIso(s);
  const evs = allEvents(s).filter((e) => e.status === 'done' && e.time <= t).map((e) => ({ ...e, kind: 'event' }));
  const notes = s.assumptions.simulated_notes.map((n, i) => ({
    ...n, noteKind: n.kind, id: `note-${i}`, kind: 'note', source: 'simulated', time: `${n.date}T${n.time}:00Z`,
  }));
  return [...evs, ...notes].sort((a, b) => b.time.localeCompare(a.time) || (b.source === 'captured') - (a.source === 'captured')).slice(0, limit);
}

export const dayLabel = (iso) => fmtDate(iso);

// Closest AIS vessel to a point at the time of the radar pass (shows why a contact is "dark").
export function nearestVessel(s, lat, lon) {
  const T = sarTime(s);
  let best = null;
  for (const v of s.ais.vessels) {
    const p = positionAt(s, v, T);
    if (!p) continue;
    const d = haversineKm(lat, lon, p.lat, p.lon);
    if (!best || d < best.distKm) best = { v, distKm: d };
  }
  return best;
}

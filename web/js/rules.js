// The assistant's rules. Every number comes from the data; thresholds come from assumptions.json.
import {
  fmt, fmtDate, fmtDay, fmtDateRuns, fmtDuration, fmtQty, fmtTime, mean, sum, minutesBetween, OP_LABELS, typeName,
} from './util.js';
import {
  today, fusion, alerts, openAlerts, alertTitle, zone, vessel, asset, assetFuel, assetTask, bestAssets, missionPlan,
  zoneStats, passSummary, nowIso, sarTime, lastReport, currentPosition,
} from './model.js';

export const forecastDays = (s) => s.weather.forecast.days.filter((d) => d.date >= today(s));

export function evaluateDay(s, op, idx) {
  const d = forecastDays(s)[idx];
  const t = s.assumptions.thresholds[op];
  const reasons = [];
  if (t.max_wave_m != null && d.wave_height_max_m > t.max_wave_m) reasons.push(`waves up to ${fmt(d.wave_height_max_m)} m`);
  if (t.max_wind_kmh != null && d.wind_max_kmh > t.max_wind_kmh) reasons.push(`wind up to ${Math.round(d.wind_max_kmh)} km/h`);
  if (t.max_rain_prob != null && d.precip_prob > t.max_rain_prob) reasons.push(`${d.precip_prob}% chance of rain`);
  return { date: d.date, ok: reasons.length === 0, reasons };
}

// Good days for an operation, and when the window closes: the first run of two unsuitable days in a row.
export function opWindow(s, op) {
  const days = forecastDays(s);
  const evals = days.map((_, i) => evaluateDay(s, op, i));
  let closeIdx = null;
  for (let i = 0; i < evals.length - 1; i += 1) {
    if (!evals[i].ok && !evals[i + 1].ok) { closeIdx = i; break; }
  }
  const usable = closeIdx === null ? evals : evals.slice(0, closeIdx);
  let blocker = null;
  if (closeIdx !== null) {
    let end = closeIdx;
    while (end + 1 < evals.length && !evals[end + 1].ok) end += 1;
    const run = days.slice(closeIdx, end + 1);
    blocker = {
      from: run[0].date, to: run[run.length - 1].date,
      waveMax: Math.max(...run.map((d) => d.wave_height_max_m)), windMax: Math.max(...run.map((d) => d.wind_max_kmh)),
      reasons: [...new Set(evals.slice(closeIdx, end + 1).flatMap((e) => e.reasons))],
    };
  }
  return {
    evals, good: usable.filter((e) => e.ok).map((e) => e.date),
    closesAfter: closeIdx === null ? null : (closeIdx > 0 ? days[closeIdx - 1].date : today(s)),
    blocker, horizonEnd: days[days.length - 1].date,
  };
}

function blockerText(w) {
  const b = w.blocker;
  const span = b.from === b.to ? fmtDate(b.from) : `${fmtDate(b.from)}–${fmtDate(b.to)}`;
  return `${span}: waves up to ${fmt(b.waveMax)} m, wind up to ${Math.round(b.windMax)} km/h`;
}

export const goodDayFor = (s) => Object.keys(OP_LABELS).map((op) => ({ op, ...evaluateDay(s, op, 0) }));

// ---------------------------------------------------------------- 1 · response (which alert first, with which asset)
const dist = (a, b) => fmt(a, b);

function recommendedAssets(s, list) {
  // Greedy: highest-priority alerts pick first; each asset is recommended once.
  const taken = new Set();
  const out = new Map();
  for (const a of list) {
    const options = bestAssets(s, a);
    const pick = options.find((o) => o.plan.feasible && !taken.has(o.asset.id));
    if (pick) taken.add(pick.asset.id);
    out.set(a.id, { pick: pick || null, options });
  }
  return out;
}

export function responseRecs(s) {
  const list = openAlerts(s);
  const assign = recommendedAssets(s, list);
  const air = s.assets.assets.find((a) => a.role === 'verify' && !assetTask(s, a));
  const recs = [];
  for (const a of list) {
    const z = a.zoneId ? zone(s, a.zoneId) : null;
    const { pick, options } = assign.get(a.id);
    const lines = [];
    let title;
    if (a.type === 'ais_gap') {
      const v = vessel(s, a.vesselId);
      title = `Intercept ${v.name} (${v.flag}, ${a.lengthM} m): AIS off for ${fmtDuration(a.gapMin / 60)}`;
      lines.push(`Last AIS report ${fmtTime(a.lastKnown.t)} UTC. The SAR detection at ${fmtTime(a.time)} sits ${fmt(a.offsetKm)} km from where it should be: same vessel, transponder switched off.`);
    } else if (a.type === 'sts') {
      const v = vessel(s, a.nearVesselId);
      title = `Inspect possible transfer next to ${v.name} (${a.id})`;
      lines.push(`A ${a.lengthM} m contact with no AIS lies alongside ${v.name}, which does report AIS. Pattern of a ship-to-ship transfer.`);
    } else if (a.type === 'report') {
      title = `Respond to field report: ${a.kind} ${a.place ? `near ${a.place}` : ''}`.trim();
      lines.push(`Reported ${fmtTime(a.time)} UTC: "${a.text}"`);
    } else {
      title = `Check dark vessel ${a.id} (${a.lengthM} m, ${Math.round(a.confidence * 100)}% SAR confidence)${z ? ` in ${z.name}` : ''}`;
      lines.push(`No AIS vessel within ${fmt(s.assumptions.fusion.match_radius_km)} km of the radar detection, so it is flagged as a dark vessel.`);
    }
    if (z) lines.push(`Inside the ${z.name} ${z.kind === 'piracy' ? 'high-risk piracy zone' : `${z.kind} watch zone`}.`);
    if (a.cluster) lines.push(`${a.cluster.length} unmatched contacts within ${s.assumptions.fusion.cluster_radius_km} km of each other (${a.cluster.join(', ')}): possible mothership with skiffs.`);
    if (a.corroboratedBy?.length) lines.push(`Corroborated by field report ${a.corroboratedBy.join(', ')} nearby: risk raised to critical.`);
    if (pick) {
      const op = pick.asset.op;
      const w = opWindow(s, op);
      lines.push(`Recommend ${pick.asset.name} (${pick.asset.base}) to intercept: ${fmt(pick.plan.totalNm - pick.plan.returnNm, 0)} nm, arrival in ${fmtDuration(pick.plan.etaFirstH)}, ${fmtQty(pick.plan.fuelL, 'L')} for the round trip (${fmt((pick.plan.fuelL / assetFuel(s, pick.asset.id)) * 100, 0)}% of its fuel).`);
      if (!w.evals[0].ok) lines.push(`Sea state today is above the ${OP_LABELS[op].toLowerCase()} limit (${w.evals[0].reasons.join(', ')}).`);
      else if (w.blocker) lines.push(`${OP_LABELS[op]} window closes after ${fmtDate(w.closesAfter)} (${blockerText(w)}).`);
    } else {
      const closest = options[0];
      lines.push(closest
        ? `No free patrol asset can reach this contact and return on its fuel. Closest: ${closest.asset.name}, ${fmt(closest.plan.totalNm - closest.plan.returnNm, 0)} nm, needs ${fmtQty(closest.plan.fuelL, 'L')}, has ${fmtQty(assetFuel(s, closest.asset.id), 'L')}.`
        : 'All patrol assets are on task.');
    }
    if (air && !(pick && pick.asset.role === 'verify')) {
      const p = missionPlan(s, air, [a]);
      if (p.feasible && a.type !== 'report') lines.push(`Optional: ${air.name} can verify by overflight in ${fmtDuration(p.etaFirstH)}.`);
    }
    recs.push({
      id: `response-${a.id}`, kind: 'response', alertId: a.id, risk: a.risk, entity: a.id, assetId: pick?.asset.id || null,
      priority: a.score, title, lines, sources: a.type === 'report' ? ['captured', 'forecast', 'rule'] : ['sar', 'ais', 'forecast', 'rule'],
      goodDays: pick ? opWindow(s, pick.asset.op).good : [], parcels: [a.id],
    });
  }
  return recs.sort((x, y) => y.priority - x.priority);
}

export function workload(s) {
  const recs = responseRecs(s);
  const r = s.assumptions.response;
  let neededH = 0;
  for (const rec of recs) {
    const a = rec.assetId ? asset(s, rec.assetId) : null;
    if (!a) continue;
    const al = alerts(s).find((x) => x.id === rec.alertId);
    const p = missionPlan(s, a, [al]);
    neededH += p.etaFirstH * 2 + r.on_scene_h;
  }
  const ready = s.assets.assets.filter((a) => a.role === 'intercept' && !assetTask(s, a));
  return { neededH, availableH: ready.length * r.hours_per_asset_per_day, ready: ready.length, open: recs.length, unassigned: recs.filter((x) => !x.assetId).length };
}

// ---------------------------------------------------------------- 2 · fleet (fuel and readiness)
export function fleetStatus(s) {
  const recs = responseRecs(s);
  const r = s.assumptions.response;
  return s.assets.assets.map((a) => {
    const level = assetFuel(s, a.id);
    const needs = recs.filter((x) => x.assetId === a.id).map((x) => {
      const al = alerts(s).find((y) => y.id === x.alertId);
      return { rec: x, alert: al, plan: missionPlan(s, a, [al]) };
    });
    const total = sum(needs.map((n) => n.plan.fuelL));
    const task = assetTask(s, a);
    let status = 'none';
    if (needs.length) status = total > level * (1 - r.reserve_pct) ? 'critical' : level - total < 0.15 * a.capacity ? 'low' : 'ok';
    else if (level < 0.15 * a.capacity) status = 'critical';
    else if (level < 0.25 * a.capacity) status = 'low';
    const target = Math.max(total / (1 - r.reserve_pct), 0.5 * a.capacity);
    return { asset: a, level, needs, total, status, task, shortfall: Math.max(0, target - level), cap: a.capacity };
  });
}

export function fleetRecs(s) {
  return fleetStatus(s).filter((x) => !x.task && (x.status === 'low' || x.status === 'critical') && x.shortfall > 0).map((x) => {
    const lines = [`${fmtQty(x.level, 'L')} on board (${fmt((x.level / x.cap) * 100, 0)}% of ${fmtQty(x.cap, 'L')}).`];
    if (x.needs.length) {
      lines.push(`Recommended for ${x.needs.map((n) => `${n.alert.id} (${fmtQty(n.plan.fuelL, 'L')})`).join(', ')}: round trip leaves ${fmtQty(Math.max(0, x.level - x.total), 'L')}.`);
    } else lines.push('No mission planned, but the tank is low for a rapid response.');
    return {
      id: `fleet-${x.asset.id}`, kind: 'fleet', status: x.status, priority: x.status === 'critical' ? 80 : 60,
      title: `${x.status === 'critical' ? 'Critical' : 'Low'}: refuel ${fmtQty(x.shortfall, 'L')} for ${x.asset.name}`,
      lines, sources: ['simulated', 'rule'], assetId: x.asset.id,
    };
  });
}

// ---------------------------------------------------------------- 3 · patterns (this pass vs the baseline)
export function patternSummary(s, z) {
  const h = s.sar.history[z.id];
  const current = alerts(s).filter((a) => a.zoneId === z.id && a.type !== 'report' && !a.lowConf).length;
  const avg = mean(h.passes);
  const lower = h.passes.filter((v) => v < current).length;
  const ties = h.passes.filter((v) => v === current).length;
  return {
    zone: z, current, avg, ref: h.reference, passes: h.passes, rank: lower + 1, ties, total: h.passes.length + 1,
    diffPct: avg ? ((current - avg) / avg) * 100 : null,
  };
}

export function patternRecs(s) {
  const band = s.assumptions.pattern_band_pct;
  return s.geo.zones.map((z) => {
    const p = patternSummary(s, z);
    const dir = p.diffPct == null ? 'in line with' : p.diffPct > band ? 'above' : p.diffPct < -band ? 'below' : 'in line with';
    const lines = [`${p.current} dark or silent contact${p.current === 1 ? '' : 's'} in this pass, against ${fmt(p.avg)} on average over the last ${p.passes.length} passes (regional reference ${fmt(p.ref)}).`];
    if (p.rank === p.total) lines.push('The highest of the last 13 passes.');
    else if (p.rank === 1 && p.ties === 0) lines.push('The lowest of the last 13 passes.');
    if (dir === 'above') lines.push(z.kind === 'piracy' ? 'Consider more patrol presence here and an extra SAR pass.' : 'Consider tasking the next SAR pass over this zone.');
    return {
      id: `pattern-${z.id}`, kind: 'pattern', dir, zoneId: z.id,
      priority: (dir === 'above' ? 40 : 10) + p.current * 3 + (z.kind === 'piracy' ? 5 : 0),
      title: `${z.name}: dark contacts ${dir} the recent average`, lines, sources: ['sar', 'reference'],
    };
  }).sort((a, b) => b.priority - a.priority);
}

// ---------------------------------------------------------------- watch-outs and notes
export function watchOuts(s) {
  const out = [];
  const days = forecastDays(s);
  const calm = days.filter((d) => d.wave_height_max_m <= s.assumptions.calm_wave_m).map((d) => d.date);
  if (calm.length) {
    out.push({ text: `Calm seas (waves below ${fmt(s.assumptions.calm_wave_m)} m) on ${fmtDateRuns(calm)}: also favourable for small-boat attacks in the risk zones. Keep patrols out on those days.`, sources: ['forecast', 'rule'] });
  }
  const rough = days.filter((d) => d.wave_height_max_m > s.assumptions.thresholds.rhib.max_wave_m);
  if (rough.length) out.push({ text: `Rough sea on ${fmtDateRuns(rough.map((d) => d.date))} (up to ${fmt(Math.max(...rough.map((d) => d.wave_height_max_m)))} m): RHIB interceptions are not possible.`, sources: ['forecast', 'rule'] });
  const windy = days.filter((d) => d.wind_max_kmh > s.assumptions.thresholds.sar.max_wind_kmh).map((d) => d.date);
  if (windy.length) out.push({ text: `Wind above ${s.assumptions.thresholds.sar.max_wind_kmh} km/h on ${fmtDateRuns(windy)}: sea clutter hides small boats in radar images. A dark skiff may not show up.`, sources: ['forecast', 'rule'] });
  const hist = s.weather.history.years;
  const rougher = hist.filter((y) => y.calm_days <= s.assumptions.rough_season_calm_days);
  if (rougher.length) out.push({ text: `In ${rougher.length} of the last ${hist.length} years there were ${s.assumptions.rough_season_calm_days} calm days or fewer between 4 Nov and 3 Dec. Use the calm days you have.`, sources: ['simulated'] });
  const fu = fusion(s);
  const blind = fu.gaps.filter((g) => g.status !== 'matched');
  if (blind.length) {
    out.push({ text: `${blind.length} vessel${blind.length > 1 ? 's' : ''} went silent on AIS without a radar match (${blind.map((g) => g.vessel.name).join(', ')}): ${blind.some((g) => g.status === 'outside') ? 'some are outside this scene, ' : ''}task the next pass.`, sources: ['ais', 'sar'] });
  }
  return out;
}

export function entityNotes(s, id) {
  const notes = [];
  const v = vessel(s, id);
  if (v) {
    const last = lastReport(s, v);
    const gap = minutesBetween(last.t, nowIso(s));
    if (gap > s.assumptions.fusion.gap_minutes) {
      notes.push({ text: `No AIS report for ${fmtDuration(gap / 60)} (last at ${fmtTime(last.t)} UTC, ${fmt(last.sog)} kn).`, sources: ['ais'] });
    } else {
      notes.push({ text: `Reporting normally: ${fmt(last.sog)} kn on course ${last.cog}°, last report ${fmtTime(last.t)} UTC.`, sources: ['ais'] });
    }
    const match = fusion(s).matches.find((m) => m.v.id === v.id);
    if (match) notes.push({ text: `Seen by Sentinel-1 at ${fmtTime(sarTime(s))} UTC, ${fmt(match.distKm * 1000, 0)} m from its AIS position: radar and AIS agree.`, sources: ['sar', 'ais'] });
    const g = fusion(s).gaps.find((x) => x.vessel.id === v.id);
    if (g) notes.push({ text: g.status === 'matched' ? 'Found by radar near its dead-reckoned position: see the alert.' : g.status === 'unseen' ? 'Inside the radar scene but not detected: it may have changed course or is hidden by sea clutter.' : 'Predicted position is outside the radar scene.', sources: ['sar'] });
    return notes;
  }
  const a = alerts(s).find((x) => x.id === id);
  if (a) {
    const best = bestAssets(s, a)[0];
    if (best) notes.push({ text: `Closest free asset: ${best.asset.name}, ${fmt(best.plan.totalNm - best.plan.returnNm, 0)} nm, arrival in ${fmtDuration(best.plan.etaFirstH)}${best.plan.feasible ? '' : ' (not enough fuel for the round trip)'}.`, sources: ['rule'] });
    if (a.cluster) notes.push({ text: `Part of a cluster of ${a.cluster.length} unmatched contacts.`, sources: ['sar'] });
    if (a.lowConf) notes.push({ text: 'Low radar confidence: likely sea clutter. Verify on the next pass before sending anyone.', sources: ['sar', 'rule'] });
  }
  return notes;
}

export function todaysActions(s) {
  const response = responseRecs(s);
  const fleet = fleetRecs(s).sort((a, b) => b.priority - a.priority);
  const third = fleet[0] || patternRecs(s)[0];
  return [...response.slice(0, 2), third].filter(Boolean);
}

export const allRecommendations = (s) => ({ response: responseRecs(s), fleet: fleetRecs(s), pattern: patternRecs(s), watch: watchOuts(s) });
export { dist as _dist, fmtDay, typeName, alertTitle, zoneStats, passSummary, currentPosition };

// HTML for every screen. Pure functions of the state.
import {
  OP_LABELS, TYPE_LABELS, RISK, esc, fmt, fmtQty, fmtDate, fmtDay, fmtTime, fmtDateTime, fmtLongDate, fmtDuration, fmtCoord,
  badges, typeName, typeColor, weatherIcon, monthName, mean, sum, shortMmsi, VESSEL_TYPES,
} from './util.js';
import {
  today, nowIso, sarTime, vessel, asset, zone, alerts, alertTitle, openAlerts, passSummary, triage, zoneStats, fusion, allEvents,
  assetFuel, assetTask, assetUsage, recentAssetUse, lastActivities, lastReport, bestAssets, nearestVessel, currentPosition, alertById,
} from './model.js';
import {
  forecastDays, evaluateDay, opWindow, responseRecs, workload, fleetStatus, fleetRecs, patternRecs, patternSummary, watchOuts,
  goodDayFor, entityNotes, todaysActions,
} from './rules.js';
import { renderMap, MAP_LEGEND, shapeIcon } from './map.js';
import { barChart, progressBar, stackedBar } from './charts.js';
import {
  kpiStrip, homeExtras, behaviourAlertsSection, serverAlertDrawer, incidentDrawer, vesselIntel,
} from './intel-views.js';
import { observationDrawer, submissionDrawer } from './intake-views.js';
import { fusedDrawer } from './merge-views.js';

const ICONS = {
  track: '<path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14M15 6v14"/>',
  home: '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.4"/>',
  weather: '<path d="M3 15c2 0 2-2 4-2s2 2 4 2 2-2 4-2 2 2 4 2"/><path d="M3 19c2 0 2-2 4-2s2 2 4 2 2-2 4-2 2 2 4 2"/><circle cx="16" cy="6" r="2.6"/><path d="M16 1.5v1M20.5 6h-1M11.5 6h1"/>',
  activities: '<path d="M12 3 2.5 20h19z"/><path d="M12 10v4.5M12 17.2v.1"/>',
  sar: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><path d="M12 12 18 6"/><circle cx="15.5" cy="15" r=".9"/>',
  last: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  recs: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  fleet: '<path d="M3 15 5.5 19h13L21 15z"/><path d="M12 15V5l5 6h-5M7 11.5l5-1"/>',
  incidents: '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>',
  intel: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8.3 6.8l7.4.3M7.3 8.2l3.6 7.6M16.8 9.3l-3.6 6.6"/>',
  analytics: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  agencies: '<circle cx="8" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M2.5 20c.6-3.4 2.8-5.5 5.5-5.5s4.9 2.1 5.5 5.5M14 15.2c.9-.7 1.9-1 3-1 2.3 0 4 1.7 4.5 4.8"/>',
  intake: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 17v3h16v-3"/>',
  impact: '<path d="M12 21c-4-2.5-8-6-8-10.5A4.5 4.5 0 0 1 12 8a4.5 4.5 0 0 1 8 2.5C20 15 16 18.5 12 21z"/>',
};
export const icon = (name, cls = 'ico') => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

const KIND = { response: 'Response', fleet: 'Fleet', pattern: 'Pattern' };
const riskChip = (r) => `<span class="risk risk-${r}">${RISK[r].label}</span>`;
const ZONE_CHIP = (z) => (z ? `<span class="crop-chip"><i style="background:${z.color}"></i>${esc(z.name)}</span>` : '');
const dayChips = (dates) => (dates.length ? `<div class="day-chips">${dates.map((d) => `<span class="day-chip">${esc(fmtDay(d))}</span>`).join('')}</div>` : '');

export function recCard(s, r, { index = null, compact = false } = {}) {
  const a = r.alertId ? alertById(s, r.alertId) : null;
  const best = r.assetId ? asset(s, r.assetId) : null;
  return `<article class="rec rec-${r.kind}${r.status ? ` rec-${r.status}` : ''}${r.risk ? ` rec-r-${r.risk}` : ''}" data-rec="${esc(r.id)}">
    <header>
      ${index != null ? `<span class="rec-num">${index}</span>` : ''}
      <span class="kind kind-${r.kind}">${KIND[r.kind]}</span>
      ${r.risk ? riskChip(r.risk) : ''}${a?.zoneId ? ZONE_CHIP(zone(s, a.zoneId)) : ''}
    </header>
    <h4>${esc(r.title)}</h4>
    <ul class="rec-lines">${r.lines.slice(0, compact ? 3 : 9).map((l) => `<li>${esc(l)}</li>`).join('')}</ul>
    ${r.goodDays && !compact ? dayChips(r.goodDays) : ''}
    ${a ? `<div class="rec-actions"><button class="chip" data-entity="${a.id}">Show on map</button>${best ? `<button class="chip chip-go" data-log="Send ${esc(best.name)} to ${esc(a.id)}">Dispatch ${esc(best.name)}…</button>` : ''}</div>` : ''}
    ${r.assetId && r.kind === 'fleet' ? `<div class="rec-actions"><button class="chip chip-go" data-log="Refuelled ${esc(asset(s, r.assetId).name)} with ${Math.round(Number((r.title.match(/refuel ([\d.,]+) (kL|L)/) || [])[1]?.replace(',', '') * (/kL/.test(r.title) ? 1000 : 1)) || 1000)} litres">Log refuelling…</button></div>` : ''}
    <footer>${badges(r.sources)}</footer>
  </article>`;
}

// ---------- sidebar ----------
export function sidebar(s) {
  const view = s.ui.view;
  const t = today(s);
  const d0 = forecastDays(s)[0];
  const w = weatherIcon(d0.weather_code);
  const sum_ = passSummary(s);
  const tri = triage(s);
  const top = todaysActions(s)[0];
  const fl = fleetStatus(s);
  const alertFleet = fl.filter((x) => ['low', 'critical'].includes(x.status) && !x.task);
  const last = lastActivities(s, 6).find((e) => e.kind === 'event');
  const lastTitle = last ? (last.entity ? alertTitle(s, alertById(s, last.entity) || { type: last.type, lat: 0, lon: 0 }) : TYPE_LABELS[last.type] || last.type) : '';

  const tiles = [
    {
      view: 'home', title: s.server ? 'Dashboard' : 'Sea map', summary: `${s.ais.vessels.length} vessels · ${sum_.dark} dark · ${sum_.gaps} AIS off`,
      pop: `<h5>Alerts by zone</h5>${zoneStats(s).map(({ zone: z, current }) => `<div class="pop-row">${ZONE_CHIP(z)}<span>${current}</span></div>`).join('')}
        <p class="pop-note">AIS tracks (blue) and radar alerts (red) on one map.</p>`,
    },
    {
      view: 'track', title: 'Live tracking map', summary: `Full map · ${s.ais.vessels.length} vessels · 12 h replay`,
      pop: '<p>The whole region on one screen: vessel shapes by type, trails, a time slider to replay the last 12 hours, and follow-a-vessel.</p><p class="pop-note">Simulated AIS, not live intelligence.</p>',
    },
    {
      view: 'weather', title: 'Sea state', summary: `${w.icon} waves ${fmt(d0.wave_height_max_m)} m · ${fmt(d0.wind_max_kmh, 0)} km/h`,
      pop: `<h5>${esc(fmtLongDate(t))}</h5>
        <div class="pop-row"><span>${w.icon} ${esc(w.text)}</span><span>${fmt(d0.tmax, 0)}° / ${fmt(d0.tmin, 0)}°</span></div>
        <div class="pop-row"><span>Waves</span><span>up to ${fmt(d0.wave_height_max_m)} m</span></div>
        <div class="pop-row"><span>Wind</span><span>up to ${fmt(d0.wind_max_kmh, 0)} km/h</span></div>
        <h5>Good day for</h5>${goodDayFor(s).map((g) => `<div class="pop-row"><span>${esc(OP_LABELS[g.op])}</span><span class="${g.ok ? 'ok' : 'no'}">${g.ok ? '✓ yes' : `✗ ${esc(g.reasons[0])}`}</span></div>`).join('')}`,
    },
    {
      view: 'activities', title: 'Alerts', summary: `${tri.new} open · ${tri.assigned} assigned · ${tri.resolved} closed`,
      pop: `<h5>Triage</h5>${[['new', 'Open'], ['assigned', 'Assigned'], ['resolved', 'Closed']].map(([k, l]) => `<div class="pop-row"><span>${l}</span><span>${tri[k]} of ${tri.total}</span></div>${progressBar(tri[k], tri.total)}`).join('')}`,
    },
    {
      view: 'sar', title: 'Radar (SAR)', summary: `${sum_.detections} detections · ${sum_.matched} matched`,
      pop: `<h5>Sentinel-1 · ${esc(fmtDateTime(sarTime(s)))}</h5>
        <div class="pop-row"><span>Matched to AIS</span><span>${sum_.matched}</span></div><div class="pop-row"><span>Known platforms</span><span>${sum_.infrastructure}</span></div>
        <div class="pop-row"><span>Dark vessels</span><span class="no">${sum_.dark}</span></div><div class="pop-row"><span>AIS switched off</span><span class="no">${sum_.gaps}</span></div>
        <p class="pop-note">Simulated detections: same format as a YOLOv8 run on real scenes.</p>`,
    },
    {
      view: 'last', title: 'Last reports', summary: last ? `${fmtDate(last.time)} ${fmtTime(last.time)} · ${lastTitle}` : 'None yet',
      pop: `<h5>Latest</h5>${lastActivities(s, 4).map((e) => `<div class="pop-row"><span>${esc(e.kind === 'note' ? e.noteKind : TYPE_LABELS[e.type] || e.type)}</span><span>${esc(fmtTime(e.time))}</span></div>`).join('')}`,
    },
    {
      view: 'recs', title: 'Recommendations', summary: top ? top.title : 'Nothing urgent',
      pop: top ? `<h5>Top action now</h5><p class="pop-strong">${esc(top.title)}</p><p>${esc(top.lines.find((l) => l.startsWith('Recommend')) || top.lines[0])}</p><p class="pop-note">Rule-based, with a voice language model for logging.</p>` : '',
    },
    {
      view: 'fleet', title: 'Fleet', summary: alertFleet.length ? `${alertFleet.length} alert${alertFleet.length > 1 ? 's' : ''} · ${alertFleet[0].asset.name} ${alertFleet[0].status}` : 'Fuel covers the plan',
      pop: `<h5>Recently tasked</h5>${recentAssetUse(s).map((u) => `<div class="pop-row"><span>${esc(u.name)}</span><span>${esc(fmtTime(u.time))}${u.entity ? ` · ${esc(u.entity)}` : ''}</span></div>`).join('') || '<p class="pop-note">No dispatches logged yet.</p>'}`,
    },
  ];
  const sv = s.server;
  if (sv) {
    const sm = sv.summary;
    const openInc = sm ? sm.active_incidents : 0;
    const flagged = (sv.vessels || []).filter((v) => v.level !== 'low');
    tiles.splice(3, 0, {
      view: 'incidents', title: 'Incidents', summary: `${openInc} active · ${sm?.incidents_30d ?? 0} in 30 days`,
      pop: `<h5>Last 30 days by type</h5>${Object.entries(sm?.incidents_30d_by_type || {}).map(([k, n]) => `<div class="pop-row"><span>${esc(k.replace(/_/g, ' '))}</span><span>${n}</span></div>`).join('') || '<p class="pop-note">None.</p>'}`,
    });
    tiles.push(
      { group: 'Intelligence' },
      {
        view: 'intel', title: 'Intelligence', summary: `${flagged.length} vessels flagged by AI`,
        pop: `<h5>Highest risk now</h5>${flagged.slice(0, 5).map((v) => `<div class="pop-row"><span>${esc(v.name)}</span><span class="risk risk-${v.level}">${RISK[v.level].label}</span></div>`).join('') || '<p class="pop-note">Nothing above Low.</p>'}<p class="pop-note">Rules + Isolation Forest. Indicators for investigation, not proof.</p>`,
      },
      {
        view: 'intake', title: 'Data intake', summary: `Agency submissions · ${(sv.observationsRecent || []).length} reports in 7 days`,
        pop: '<p>Sightings, patrol reports, radar contacts, watchlists and incident batches from partner agencies (CSV or API key).</p><p class="pop-note">Prototype of the phase-2 agency integration.</p>',
      },
      { view: 'analytics', title: 'Analytics', summary: 'Trends, hotspots, exports', pop: '<p>Incident trends, high-risk locations, alert statistics, CSV/JSON exports.</p>' },
      {
        view: 'agencies', title: 'Agencies', summary: `${sv.me.agency} · ${sv.me.role_label}${sm?.inbox_unread ? ` · ${sm.inbox_unread} new` : ''}`,
        pop: `<h5>Signed in</h5><p>${esc(sv.me.name)} · ${esc(sv.me.agency)}</p><p class="pop-note">Sharing, roles and the audit log.</p>`,
      },
      { view: 'impact', title: 'About & impact', summary: 'Objectives and SDGs', pop: '<p>Environmental and social objectives, SDG 8, 9, 11, 14, 16, responsible use.</p>' },
    );
    tiles.splice(0, 0, { group: 'Operations' });
  }
  return tiles.map((tile) => (tile.group ? `<div class="side-group">${esc(tile.group)}</div>` : `
    <div class="tile-wrap">
      <button class="tile${view === tile.view ? ' is-active' : ''}" data-view="${tile.view}">
        <span class="tile-ico">${icon(tile.view)}</span>
        <span class="tile-text"><span class="tile-title">${esc(tile.title)}</span><span class="tile-sum">${esc(tile.summary)}</span></span>
      </button>
      <div class="pop" role="tooltip">${tile.pop}</div>
    </div>`)).join('');
}

// ---------- home ----------
function layerChips(s) {
  const L = s.ui.layers;
  const list = [['tracks', 'AIS tracks'], ['zones', 'Risk zones'], ['footprint', 'Radar scene'], ['assets', 'Patrol assets'], ['platforms', 'Platforms'], ['lanes', 'Shipping lanes']];
  if (s.server) list.push(['incidents', 'Incidents'], ['agency', 'Agency reports'], ['fused', 'Merged contacts']);
  return list
    .map(([k, l]) => `<button class="chip chip-sm${L[k] ? ' is-on' : ''}" data-layer="${k}">${l}</button>`).join('');
}

function legend(s) {
  const marks = {
    dark: '<i class="lg lg-dark"></i>', gap: '<i class="lg lg-gap">×</i>', sts: '<i class="lg lg-sts"></i>', ais: '<i class="lg lg-ais"></i>',
    asset: '<i class="lg lg-asset"></i>', platform: '<i class="lg lg-plat"></i>', risk: '<i class="lg lg-risk"></i>', inc: '<i class="lg lg-inc"></i>', agency: '<i class="lg lg-agency"></i>', fused: '<i class="lg lg-fused"></i>',
  };
  return MAP_LEGEND.filter(([k]) => s.server || !['risk', 'inc', 'agency', 'fused'].includes(k)).map(([k, l]) => `<span>${marks[k]} ${l}</span>`).join('');
}

function shapeKey() {
  return Object.keys(VESSEL_TYPES).map((t) => `<span>${shapeIcon(t, 15)} ${esc(typeName(t))}</span>`).join('');
}

function triageProgress(s) {
  const tri = triage(s);
  const ps = passSummary(s);
  const wl = workload(s);
  const rows = [
    ['Radar contacts explained by AIS', ps.matched + ps.infrastructure, ps.detections - ps.lowConf, `${ps.matched + ps.infrastructure} / ${ps.detections - ps.lowConf}`],
    ['Alerts assigned to a patrol asset', tri.assigned + tri.resolved, tri.total, `${tri.assigned + tri.resolved} / ${tri.total}`],
    ['Alerts closed', tri.resolved, tri.total, `${tri.resolved} / ${tri.total}`],
    ['Patrol assets free', wl.ready, s.assets.assets.filter((a) => a.role === 'intercept').length, `${wl.ready} / ${s.assets.assets.filter((a) => a.role === 'intercept').length}`],
  ];
  return `<div class="progress-grid">${rows.map(([l, d, t, txt]) => `<div class="progress-item"><div class="progress-head"><span>${l}</span><span>${txt}</span></div>${progressBar(d, t)}</div>`).join('')}</div>`;
}

function workloadBox(s) {
  const wl = workload(s);
  const tight = wl.neededH > wl.availableH || wl.unassigned > 0;
  return `<div class="workload ${tight ? 'is-tight' : ''}">
    <div><strong>${wl.open} open alerts</strong> need about <strong>${fmt(wl.neededH, 0)} asset-hours</strong>; ${wl.ready} free assets offer ${fmt(wl.availableH, 0)} today.</div>
    <div class="workload-bar"><div style="width:${Math.min(100, (wl.availableH / Math.max(wl.neededH, wl.availableH, 1)) * 100)}%"></div></div>
    <div class="workload-note">${wl.unassigned ? `${wl.unassigned} alert${wl.unassigned > 1 ? 's have' : ' has'} no asset in range: take the highest risk first.` : tight ? 'More work than free hours: start with the highest risk.' : 'Free assets cover every open alert.'} ${badges(['rule'])}</div>
  </div>`;
}

function mapTools(s) {
  if (!s.server) return '';
  const f = s.ui.mapf;
  const types = Object.keys(VESSEL_TYPES);
  return `<form class="map-tools" data-form="map-filter" role="search">
    <input name="q" type="search" value="${esc(f.q)}" placeholder="Search vessel, MMSI, IMO…" list="vessel-names" autocomplete="off" aria-label="Search vessels">
    <datalist id="vessel-names">${(s.server.vessels || []).map((v) => `<option value="${esc(v.name)}">${esc(v.id)}</option>`).join('')}</datalist>
    <select name="type" aria-label="Vessel type"><option value="">All types</option>${types.map((t) => `<option value="${t}" ${f.type === t ? 'selected' : ''}>${esc(typeName(t))}</option>`).join('')}</select>
    <select name="risk" aria-label="Minimum risk"><option value="">Any risk</option>${['medium', 'high', 'critical'].map((r) => `<option value="${r}" ${f.risk === r ? 'selected' : ''}>${RISK[r].label}+</option>`).join('')}</select>
    ${f.q || f.type || f.risk ? '<button type="button" class="chip chip-sm" data-act="map-clear">Clear</button>' : ''}
    <span class="zoom"><button type="button" class="icon-btn" data-act="zoom" data-z="in" aria-label="Zoom in">+</button><button type="button" class="icon-btn" data-act="zoom" data-z="out" aria-label="Zoom out">−</button><button type="button" class="icon-btn" data-act="zoom" data-z="reset" aria-label="Reset view">⤢</button></span>
  </form>${searchHits(s)}`;
}

function searchHits(s) {
  const q = s.ui.mapf.q.trim().toLowerCase();
  if (!q) return '';
  const hits = (s.server.vessels || []).filter((v) => `${v.name} ${v.id} ${v.imo || ''} ${v.call_sign || ''}`.toLowerCase().includes(q)).slice(0, 6);
  return `<div class="search-hits">${hits.map((v) => `<button class="chip" data-entity="${esc(v.id)}">${esc(v.name)} · ${esc(v.id)} <span class="risk risk-${v.level}">${RISK[v.level].label}</span></button>`).join('') || '<span class="muted small">No vessel matches.</span>'}</div>`;
}

export function homeView(s) {
  const actions = todaysActions(s);
  return `${kpiStrip(s)}
  <section class="home-grid">
    <div class="card map-card">
      <div class="card-head">
        <div><h2>Gulf of Guinea · surveillance picture</h2><p class="sub">${s.ais.vessels.length} vessels with AIS · radar pass ${esc(fmtTime(sarTime(s)))} UTC · click a marker for details${s.server ? ' · scroll or drag to zoom and pan' : ''}</p></div>
        <div class="chips tight">${layerChips(s)}<button class="btn primary small-btn" data-view="track" title="Open the full-screen tracking map with 12-hour replay">⛶ Full map &amp; replay</button></div>
      </div>
      ${mapTools(s)}
      <div class="map-wrap" data-zoomable="1">${renderMap(s, { selected: s.ui.entity, flash: s.ui.flash, layers: s.ui.layers, filter: s.server ? s.ui.mapf : {}, vb: s.ui.vb })}</div>
      <div class="map-foot">
        <div class="legend status-legend">${shapeKey()}${legend(s)}</div>
        <span class="muted">Schematic coastline. AIS and radar data are simulated in this demo.</span>
      </div>
      <h3 class="section-title">Response status</h3>
      ${triageProgress(s)}
    </div>
    <div class="card assistant-card">
      <div class="card-head"><div><h2>Assistant · now</h2><p class="sub">${esc(fmtLongDate(today(s)))} · ${esc(fmtTime(nowIso(s)))} UTC</p></div>
        <button class="btn ghost small-btn" data-action="brief" title="Read the top actions aloud">🔊 Brief me</button></div>
      ${workloadBox(s)}
      <div class="rec-list">${actions.map((r, i) => recCard(s, r, { index: i + 1, compact: true })).join('')}</div>
      <button class="link-btn" data-view="recs">All recommendations →</button>
    </div>
  </section>
  ${homeExtras(s)}`;
}

// ---------- sea state ----------
export function weatherView(s) {
  const days = forecastDays(s);
  const d0 = days[0];
  const w0 = weatherIcon(d0.weather_code);
  const ops = Object.keys(OP_LABELS);
  const hist = s.weather.history.years;
  const roughIdx = hist.map((y, i) => (y.calm_days <= s.assumptions.rough_season_calm_days ? i : -1)).filter((i) => i >= 0);
  const season = s.weather.season_monthly.months;
  const f = s.weather.forecast;
  return `
  <section class="view-head"><h2>Sea state</h2><p class="sub">Wind and wave forecast for the Gulf of Guinea ${badges(['forecast', 'simulated'])}</p></section>
  <section class="grid-2">
    <div class="card today-card">
      <div class="today-main"><span class="today-ico">${w0.icon}</span>
        <div><div class="today-temp">${fmt(d0.wave_height_max_m)} m <span>waves</span></div><div>${esc(w0.text)} · wind up to ${fmt(d0.wind_max_kmh, 0)} km/h · rain ${fmt(d0.precip_mm)} mm (${d0.precip_prob}%)</div></div>
      </div>
      <h3 class="section-title">Good day for</h3>
      <div class="good-for">${goodDayFor(s).map((g) => `<div class="good ${g.ok ? 'ok' : 'no'}"><b>${g.ok ? '✓' : '✗'}</b> ${esc(OP_LABELS[g.op])}${g.ok ? '' : `<small>${esc(g.reasons.join(', '))}</small>`}</div>`).join('')}</div>
    </div>
    <div class="card">
      <h3 class="section-title">Things to consider</h3>
      <ul class="watch">${watchOuts(s).map((x) => `<li>${esc(x.text)} ${badges(x.sources)}</li>`).join('')}</ul>
    </div>
  </section>
  <section class="card">
    <div class="card-head"><h3>Next ${days.length} days</h3><span class="muted">Snapshot ${esc(f.fetched_at.replace('T', ' '))} · ${esc(f.source)}</span></div>
    <div class="forecast-row">${days.map((d) => {
      const wi = weatherIcon(d.weather_code);
      return `<div class="fday${d.wave_height_max_m > s.assumptions.thresholds.rhib.max_wave_m ? ' is-wet' : ''}"><div class="fday-name">${esc(fmtDay(d.date))}</div><div class="fday-ico" title="${esc(wi.text)}">${wi.icon}</div>
        <div class="fday-t">${fmt(d.wave_height_max_m)} m</div>
        <div class="fday-r">${fmt(d.wind_max_kmh, 0)} km/h</div><div class="fday-w">${fmt(d.precip_mm)} mm</div></div>`;
    }).join('')}</div>
    <h3 class="section-title">Work windows ${badges(['rule'])}</h3>
    <div class="table-scroll"><table class="windows">
      <thead><tr><th></th>${days.map((d) => `<th>${esc(fmtDay(d.date))}</th>`).join('')}</tr></thead>
      <tbody>${ops.map((op) => `<tr><th>${esc(OP_LABELS[op])}</th>${days.map((_, i) => {
        const e = evaluateDay(s, op, i);
        return `<td class="${e.ok ? 'ok' : 'no'}" title="${esc(e.ok ? 'Suitable' : e.reasons.join(', '))}">${e.ok ? '✓' : '✗'}</td>`;
      }).join('')}</tr>`).join('')}</tbody>
    </table></div>
  </section>
  <section class="grid-2">
    <div class="card">
      <h3>Calm days, 4 Nov–3 Dec, last 10 years ${badges(['simulated'])}</h3>
      <p class="sub">Days with waves under ${fmt(s.assumptions.calm_wave_m)} m. Orange: years with ${s.assumptions.rough_season_calm_days} calm days or fewer.</p>
      ${barChart({ labels: hist.map((y) => String(y.year)), values: hist.map((y) => y.calm_days), highlight: roughIdx, unit: 'calm days', digits: 0 })}
    </div>
    <div class="card">
      <h3>${s.weather.season_monthly.year} mean wave height by month ${badges(['simulated'])}</h3>
      <p class="sub">Metres. Higher swell in the rainy months limits small-boat work on both sides.</p>
      ${barChart({ labels: season.map((m) => monthName(m.month)), values: season.map((m) => m.wave_mean_m), unit: 'm', digits: 1, color: '#4F8FC0' })}
      <p class="muted">Mean wind: ${season.map((m) => `${monthName(m.month)} ${fmt(m.wind_mean_kmh, 0)}`).join(' · ')} km/h</p>
    </div>
  </section>`;
}

// ---------- alerts and log ----------
const TL_ICON = { dark_vessel: '🛰️', ais_gap: '📡', sts: '⛴️', report: '📻', dispatch: '🚤', resolve: '✅', refuel: '⛽', pass: '🛰️' };

function eventSummary(s, e) {
  if (e.type === 'dispatch') return `${asset(s, e.assetId).name} · arrival ${fmtDuration(e.eta_h)} · ${fmtQty(e.fuel_l, 'L')}`;
  if (e.type === 'resolve') return e.outcome === 'false_alarm' ? 'false alarm' : 'threat handled';
  if (e.type === 'refuel') return `${asset(s, e.assetId).name} +${fmtQty(e.litres, 'L')}`;
  if (e.type === 'pass') return 'next Sentinel-1 pass (planned)';
  if (e.gapOnset) return `${vessel(s, e.entity).name}: AIS reports stop`;
  const a = alertById(s, e.entity);
  if (!a) return '';
  if (e.type === 'report') return `${a.kind} · ${a.place || ''}`;
  return `${a.lengthM} m · ${Math.round((a.confidence || 0) * 100)}% conf. · ${a.zoneId ? zone(s, a.zoneId).name : 'open sea'}`;
}

function eventDetail(s, e) {
  const rows = [['Time', fmtDateTime(e.time)], ['Source', e.source]];
  const a = e.entity ? alertById(s, e.entity) : null;
  if (a) rows.push(['Position', fmtCoord(a.lat, a.lon)], ['Risk', RISK[a.risk].label]);
  if (e.type === 'dispatch') rows.push(['Asset', asset(s, e.assetId).name], ['Distance', `${fmt(e.dist_nm, 0)} nm incl. return`], ['Fuel for this alert', fmtQty(e.fuel_l, 'L')]);
  if (e.type === 'report' && a) rows.push(['Observed', a.kind], ['Place', a.place || '–']);
  if (e.raw) rows.push(['Logged as', `"${e.raw}" (${e.channel})`]);
  return `<div class="event-detail"><dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div>`;
}

export function activitiesView(s) {
  const tri = triage(s);
  const ps = passSummary(s);
  const list = alerts(s).filter((a) => !a.lowConf);
  const filter = s.ui.logFilter;
  const evsAll = allEvents(s);
  const days = [...new Set(evsAll.filter((e) => e.status === 'done').map((e) => e.time.slice(0, 10)))].sort().reverse().slice(0, 4);
  const day = s.ui.logDay && days.includes(s.ui.logDay) ? s.ui.logDay : days[0];
  const evs = evsAll.filter((e) => e.time.slice(0, 10) === day && (filter === 'all' || e.type === filter)).sort((a, b) => b.time.localeCompare(a.time));
  const byRisk = ['critical', 'high', 'medium'].map((r) => [r, list.filter((a) => a.risk === r)]);
  return `
  <section class="view-head"><h2>Alerts & log</h2><p class="sub">What the system found, what has been sent, and what is left ${badges(['sar', 'ais', 'captured'])}</p></section>
  ${behaviourAlertsSection(s)}
  ${s.server ? '<h3 class="section-title">Radar alerts (AIS/SAR fusion)</h3>' : ''}
  <section class="grid-2">
    <div class="card">
      <h3>Open alerts by risk</h3>
      <p class="sub">${tri.new} open, ${tri.assigned} assigned, ${tri.resolved} closed.</p>
      ${byRisk.map(([r, items]) => {
        const done = items.filter((a) => a.state.code !== 'new').length;
        return `<div class="progress-item"><div class="progress-head"><span>${riskChip(r)} ${items.length ? `${items.length - done} open of ${items.length}` : 'none'}</span><span class="muted small">${items.filter((a) => a.state.code === 'new').map((a) => a.id).slice(0, 6).join(', ') || (items.length ? 'All handled ✓' : '')}</span></div>${progressBar(done, items.length || 1, RISK[r].color)}</div>`;
      }).join('')}
      <button class="link-btn" data-view="recs">What to do next →</button>
    </div>
    <div class="card">
      <h3>This radar pass in numbers ${badges(['sar'])}</h3>
      <div class="kpis">
        <div class="kpi"><span class="kpi-v">${ps.detections}</span><span class="kpi-l">radar detections</span></div>
        <div class="kpi"><span class="kpi-v">${ps.matched}</span><span class="kpi-l">matched to an AIS vessel</span></div>
        <div class="kpi"><span class="kpi-v">${ps.dark + ps.gaps}</span><span class="kpi-l">dark or AIS-off vessels</span></div>
        <div class="kpi"><span class="kpi-v">${fmt(ps.unmatchedShare * 100, 0)}%</span><span class="kpi-l">of ships with no AIS match</span></div>
      </div>
      <p class="muted small">Platforms (${ps.infrastructure}) and ${ps.lowConf} low-confidence contacts are not counted as alerts. Studies of the Indian Ocean found a much higher share of unmatched radar vessels (66%): AIS alone misses most traffic.</p>
    </div>
  </section>
  <section class="card">
    <div class="card-head"><h3>Incident log</h3>
      <div class="chips">${days.map((d) => `<button class="chip${day === d ? ' is-on' : ''}" data-day="${d}">${esc(fmtDate(d))}</button>`).join('')}</div>
    </div>
    <div class="chips">${['all', 'dark_vessel', 'ais_gap', 'sts', 'report', 'dispatch', 'resolve', 'refuel'].map((f) => `<button class="chip${filter === f ? ' is-on' : ''}" data-filter="${f}">${f === 'all' ? 'All' : TYPE_LABELS[f]}</button>`).join('')}</div>
    ${evs.length ? `<div class="table-scroll"><table class="log">
      <thead><tr><th>Time</th><th>Entity</th><th>Type</th><th>Details</th><th>Source</th></tr></thead>
      <tbody>${evs.map((e) => {
        const open = s.ui.event === e.id;
        const a = e.entity ? alertById(s, e.entity) : null;
        return `<tr class="log-row${open ? ' is-open' : ''}${e.status === 'planned' ? ' is-planned' : ''}" data-event="${esc(e.id)}">
          <td>${esc(fmtTime(e.time))}</td><td>${esc(e.entity ? (a ? a.id : shortMmsi(e.entity)) : '–')} ${a ? riskChip(a.risk) : ''}</td>
          <td>${TL_ICON[e.type] || ''} ${esc(TYPE_LABELS[e.type] || 'Radar pass')}${e.status === 'planned' ? ' <span class="tag">planned</span>' : ''}</td>
          <td>${esc(eventSummary(s, e))}</td><td>${badges([e.source])}</td></tr>
          ${open ? `<tr class="detail-row"><td colspan="5">${eventDetail(s, e)}</td></tr>` : ''}`;
      }).join('')}</tbody></table></div>` : '<p class="empty">Nothing logged for this day and filter. Use <b>Log report</b> to add a record.</p>'}
  </section>`;
}

// ---------- radar view ----------
export function sarView(s) {
  const fu = fusion(s);
  const ps = passSummary(s);
  const list = alerts(s).filter((a) => a.type !== 'report').sort((a, b) => b.confidence - a.confidence);
  return `
  <section class="view-head"><h2>Radar (SAR)</h2><p class="sub">Sentinel-1 detections compared with AIS ${badges(['sar', 'ais', 'simulated'])}</p></section>
  <div class="banner">Simulated design screen. In production, detections come from YOLOv8 on Sentinel-1 bursts (scripts/detect_sar.py) and AIS from Neptune-AIS (scripts/fetch_ais.py). Matching radius ${s.assumptions.fusion.match_radius_km} km, one vessel per detection.</div>
  <section class="home-grid">
    <div class="card map-card">
      <div class="card-head"><div><h2>${esc(s.sar.scene.id)}</h2><p class="sub">${esc(s.sar.scene.polarisation)} · ${esc(s.sar.scene.preprocessing)} · ${esc(s.sar.scene.detector)}</p></div></div>
      <div class="map-wrap">${renderMap(s, { mode: 'sar', selected: s.ui.entity, flash: s.ui.flash, layers: { ...s.ui.layers, footprint: true, tracks: false } })}</div>
      <div class="map-foot"><div class="legend status-legend"><span><i class="lg lg-okdet"></i> matched to AIS (${ps.matched})</span><span><i class="lg lg-plat"></i> known platform (${ps.infrastructure})</span><span><i class="lg lg-dark"></i> dark vessel</span><span><i class="lg lg-gap">×</i> AIS off</span></div></div>
    </div>
    <div class="card">
      <h3>Unmatched contacts, by radar confidence</h3>
      <div class="ndvi-list">${list.map((a) => `<button class="ndvi-row" data-entity="${a.id}">
          <span class="ndvi-id">${esc(a.id)} ${riskChip(a.risk)}</span>
          <span class="ndvi-v" style="color:${RISK[a.risk].color}">${Math.round(a.confidence * 100)}%</span>
          ${stackedBar([[a.confidence, RISK[a.risk].color, 'confidence'], [1 - a.confidence, '#E3E8EE', 'remaining']])}
          <span class="muted small">${esc(alertTitle(s, a))} · ${a.lengthM} m${a.lowConf ? ' · likely sea clutter' : ''}</span></button>`).join('')}</div>
      <h3 class="section-title">What to look for</h3>
      <ul class="watch"><li>Several small unmatched contacts around a larger one point to a mothership with skiffs.</li>
      <li>An unmatched contact next to a tanker that reports AIS can be a ship-to-ship transfer.</li>
      <li>${fu.gaps.length} vessel${fu.gaps.length === 1 ? '' : 's'} stopped sending AIS: radar looks for them at the dead-reckoned position.</li></ul>
    </div>
  </section>`;
}

// ---------- last reports ----------
export function lastView(s) {
  const groups = [];
  for (const it of lastActivities(s, 24)) {
    const day = it.time.slice(0, 10);
    const g = groups[groups.length - 1];
    if (g && g.date === day) g.items.push(it); else groups.push({ date: day, items: [it] });
  }
  const row = (it) => {
    if (it.kind === 'note') {
      return `<div class="tl-item"><span class="tl-ico">📝</span>
        <div class="tl-body"><b>${esc(it.noteKind)} · ${esc(fmtTime(it.time))}</b><span>${esc(it.text)}</span></div>${badges(['simulated'])}</div>`;
    }
    const target = it.entity && (alertById(s, it.entity) || vessel(s, it.entity)) ? ` data-entity="${it.entity}"` : '';
    return `<div class="tl-item"${target}><span class="tl-ico">${TL_ICON[it.type] || '•'}</span>
      <div class="tl-body"><b>${esc(TYPE_LABELS[it.type] || it.type)}${it.entity ? ` · ${esc(it.entity.length > 7 ? shortMmsi(it.entity) : it.entity)}` : ''} · ${esc(fmtTime(it.time))}</b><span>${esc(eventSummary(s, it))}</span></div>${badges([it.source])}</div>`;
  };
  return `
  <section class="view-head"><h2>Last reports</h2><p class="sub">Radar and AIS events, your logs, and radio or port notes ${badges(['sar', 'ais', 'captured', 'simulated'])}</p></section>
  <section class="card timeline">${groups.map((g) => `
    <div class="tl-day"><div class="tl-date">${esc(fmtLongDate(g.date))}</div>${g.items.map(row).join('')}</div>`).join('')}
  </section>`;
}

// ---------- recommendations ----------
export function recsView(s) {
  const response = responseRecs(s);
  const fleet = fleetRecs(s);
  const pattern = patternRecs(s);
  return `
  <section class="view-head"><h2>Recommendations</h2><p class="sub">Rule-based assistant: every advice line shows where its numbers come from. A voice language model reads your logs.</p></section>
  <section class="card">
    <div class="card-head"><h3>1 · Response: which alert first, with which asset</h3><span>${badges(['sar', 'ais', 'forecast', 'rule'])}</span></div>
    ${workloadBox(s)}
    <div class="rec-grid">${response.map((r, i) => recCard(s, r, { index: i + 1 })).join('') || '<p class="empty">No open alerts ✓</p>'}</div>
  </section>
  <section class="grid-2">
    <div class="card">
      <h3>2 · Fleet</h3>
      <div class="rec-list">${fleet.map((r) => recCard(s, r)).join('') || '<p class="empty">Fuel covers every planned mission ✓</p>'}</div>
      <button class="link-btn" data-view="fleet">Open fleet →</button>
    </div>
    <div class="card">
      <h3>3 · Dark contacts against the recent baseline</h3>
      <div class="rec-list">${pattern.map((r) => recCard(s, r, { compact: true })).join('')}</div>
    </div>
  </section>
  <section class="card">
    <h3>Watch-outs</h3>
    <ul class="watch">${watchOuts(s).map((x) => `<li>${esc(x.text)} ${badges(x.sources)}</li>`).join('')}</ul>
  </section>
  <section class="card">
    <h3>By alert</h3>
    <div class="field-grid">${alerts(s).filter((a) => !a.lowConf).map((a) => {
      const notes = entityNotes(s, a.id).slice(0, 2);
      return `<button class="field-card" data-entity="${a.id}">
        <span class="fc-head"><b>${esc(a.id)}</b>${riskChip(a.risk)}<span class="muted">${a.state.code === 'new' ? 'open' : esc(a.state.code.replace('_', ' '))}</span></span>
        <span class="fc-status">${esc(alertTitle(s, a))}</span>
        ${notes.map((n) => `<span class="fc-note">${esc(n.text)}</span>`).join('')}
      </button>`;
    }).join('')}</div>
  </section>`;
}

// ---------- fleet ----------
const STATUS_TEXT = { ok: 'Covers plan', low: 'Low', critical: 'Critical', none: 'No planned use' };

export function fleetView(s) {
  const rows = fleetStatus(s);
  const alertsList = fleetRecs(s);
  return `
  <section class="view-head"><h2>Fleet</h2><p class="sub">Patrol assets, fuel on board and what the response plan needs ${badges(['simulated', 'captured'])}</p></section>
  ${alertsList.length ? `<div class="rec-list">${alertsList.map((r) => recCard(s, r)).join('')}</div>` : ''}
  <section class="card">
    <div class="table-scroll"><table class="inv">
      <thead><tr><th>Asset</th><th class="num">Fuel</th><th>Planned mission</th><th>Status</th><th class="num">Fuel used (logged)</th><th class="num">Refuelled (logged)</th><th>Last logged</th></tr></thead>
      <tbody>${rows.map((r) => {
        const u = assetUsage(s, r.asset);
        const need = r.task ? `<b>On task:</b> ${r.task.join(', ')}` : r.needs.length ? r.needs.map((n) => `${esc(n.alert.id)} · ${fmt(n.plan.totalNm - n.plan.returnNm, 0)} nm · ${fmtDuration(n.plan.etaFirstH)} · ${fmtQty(n.plan.fuelL, 'L')}`).join('<br>') : '–';
        const st = r.task ? 'ok' : r.status;
        return `<tr class="inv-${st}" data-entity="${r.asset.id}">
          <td><b>${esc(r.asset.name)}</b><div class="muted small">${esc(r.asset.type)} · base ${esc(r.asset.base)} · ${r.asset.speed_kn} kn</div></td>
          <td class="num"><b>${esc(fmtQty(r.level, 'L'))}</b><div class="cover"><div style="width:${Math.min(100, (r.level / r.cap) * 100)}%"></div></div></td>
          <td class="small">${need}</td>
          <td><span class="status status-${r.task ? 'ok' : r.status}">${r.task ? 'On task' : STATUS_TEXT[r.status]}</span></td>
          <td class="num">${u.usedFuel ? esc(fmtQty(u.usedFuel, 'L')) : '–'}</td><td class="num">${u.refuelled ? esc(fmtQty(u.refuelled, 'L')) : '–'}</td>
          <td class="small">${u.last ? `${esc(fmtTime(u.last.time))} · ${esc(u.last.type)}` : '–'}</td></tr>`;
      }).join('')}</tbody></table></div>
    <p class="muted small">Fuel, bases and readiness are simulated. Missions assume a round trip with a ${Math.round(s.assumptions.response.reserve_pct * 100)}% reserve and ${s.assumptions.response.on_scene_h} h on scene.</p>
  </section>`;
}

// ---------- drawer (alert, vessel or asset) ----------
function alertDrawer(s, a) {
  const z = a.zoneId ? zone(s, a.zoneId) : null;
  const near = nearestVessel(s, a.lat, a.lon);
  const options = bestAssets(s, a).slice(0, 4);
  const evs = allEvents(s).filter((e) => e.entity === a.id).sort((x, y) => y.time.localeCompare(x.time));
  const stText = { new: 'Open: no asset assigned yet', assigned: `Assigned to ${a.state.assetId ? asset(s, a.state.assetId).name : ''}`, resolved: 'Closed: threat handled', false_alarm: 'Closed: false alarm' }[a.state.code];
  return `
  <div class="drawer-head">
    <div><div class="drawer-title">${esc(a.id)} ${riskChip(a.risk)}</div>
      <div class="sub">${esc(TYPE_LABELS[a.type])} · ${esc(fmtCoord(a.lat, a.lon))} ${z ? `· ${esc(z.name)}` : ''}</div></div>
    <button class="icon-btn" data-action="close-drawer" aria-label="Close">✕</button>
  </div>
  <div class="drawer-body">
    <div class="status-box status-${a.state.code}"><b>${esc(stText)}</b><span>${esc(alertTitle(s, a))}</span></div>
    ${s.server?.fusedByMember?.[a.detId || a.id] ? (() => { const f = s.server.fusedByMember[a.detId || a.id]; return `<div class="banner corroborated">⬡ Corroborated: <b>${f.independent_sources} independent sources</b> (${esc(f.sources.join(', '))}) reported this contact. <button class="link-inline" data-entity="${esc(f.id)}">Open merged contact ${esc(f.id)}</button></div>`; })() : ''}
    <div class="rec-actions"><button class="chip chip-go" data-log="Send ${esc(options[0]?.asset.name || 'Patrol Vessel Alpha')} to ${esc(a.id)}">Dispatch…</button>
      <button class="chip" data-log="Close alert ${esc(a.id)} as a false alarm">Close as false alarm…</button></div>
    <h4>What to watch</h4>
    <ul class="watch">${entityNotes(s, a.id).map((n) => `<li>${esc(n.text)} ${badges(n.sources)}</li>`).join('')}</ul>
    <h4>Evidence ${badges(['sar', 'ais'])}</h4>
    <dl class="soil">
      ${a.type !== 'report' ? `<dt>Radar confidence</dt><dd>${Math.round(a.confidence * 100)}%</dd><dt>Estimated length</dt><dd>${a.lengthM} m</dd>` : `<dt>Observed</dt><dd>${esc(a.kind)}</dd>`}
      ${near ? `<dt>Nearest AIS vessel</dt><dd>${esc(near.v.name)} · ${fmt(near.distKm)} km</dd>` : ''}
      ${a.type === 'ais_gap' ? `<dt>Last AIS report</dt><dd>${esc(fmtTime(a.lastKnown.t))} UTC · ${fmt(a.lastKnown.sog)} kn</dd><dt>Silent for</dt><dd>${fmtDuration(a.gapMin / 60)}</dd>` : ''}
      ${a.cluster ? `<dt>Cluster</dt><dd>${a.cluster.join(', ')}</dd>` : ''}
      ${a.corroboratedBy?.length ? `<dt>Corroborated by</dt><dd>${a.corroboratedBy.join(', ')}</dd>` : ''}
    </dl>
    <h4>Closest free assets ${badges(['rule'])}</h4>
    <div class="mini-tl">${options.map((o) => `<div class="mini-tl-row asset-row"><span class="mini-date">${esc(o.asset.id)}</span><span>${fmt(o.plan.totalNm - o.plan.returnNm, 0)} nm · ${fmtDuration(o.plan.etaFirstH)}</span><span class="${o.plan.feasible ? 'muted' : 'neg'}">${o.plan.feasible ? `${fmtQty(o.plan.fuelL, 'L')}` : 'not enough fuel'}</span></div>`).join('') || '<p class="empty">All assets are on task.</p>'}</div>
    <h4>Timeline</h4>
    <div class="mini-tl">${evs.map((e) => `<div class="mini-tl-row${e.source === 'captured' ? ' is-captured' : ''}"><span class="mini-date">${esc(fmtTime(e.time))}</span><span>${TL_ICON[e.type] || ''} ${esc(TYPE_LABELS[e.type])}</span><span class="muted">${esc(eventSummary(s, e))}</span></div>`).join('')}</div>
  </div>`;
}

function vesselDrawer(s, v) {
  const last = lastReport(s, v);
  const n = v.pts.length;
  const reported = v.pts.filter(Boolean).length;
  const hourly = [];
  for (let i = 0; i < n; i += 3) hourly.push(v.pts[i] ? v.pts[i][2] : null);
  const labels = hourly.map((_, i) => `${String((20 + i) % 24).padStart(2, '0')}`);
  const fu = fusion(s);
  const gap = fu.gaps.find((g) => g.vessel.id === v.id);
  const match = fu.matches.find((m) => m.v.id === v.id);
  const al = gap && gap.status === 'matched' ? alerts(s).find((a) => a.vesselId === v.id) : null;
  return `
  <div class="drawer-head">
    <div><div class="drawer-title">${esc(v.name)} <span class="crop-chip"><i style="background:${typeColor(v.type)}"></i>${esc(typeName(v.type))}</span></div>
      <div class="sub">MMSI ${esc(v.id)} · ${esc(v.flag)} · ${v.length_m} m · to ${esc(v.destination || '–')}</div></div>
    <button class="icon-btn" data-action="close-drawer" aria-label="Close">✕</button>
  </div>
  <div class="drawer-body">
    <div class="status-box ${gap ? 'status-new' : 'status-ok'}"><b>${gap ? 'AIS switched off' : 'Reporting AIS'}</b><span>${esc(fmtCoord(last.lat, last.lon))} at ${esc(fmtTime(last.t))} UTC · ${fmt(last.sog)} kn</span></div>
    ${al ? `<div class="rec-actions"><button class="chip" data-entity="${al.id}">Open alert ${esc(al.id)}</button></div>` : ''}
    <h4>What to watch</h4>
    <ul class="watch">${entityNotes(s, v.id).map((x) => `<li>${esc(x.text)} ${badges(x.sources)}</li>`).join('')}</ul>
    <h4>Speed over the last 12 hours (kn, every hour) ${badges(['ais'])}</h4>
    ${barChart({ labels, values: hourly, color: typeColor(v.type), height: 130, unit: 'kn', refValue: mean(hourly), refLabel: 'avg' })}
    <h4>AIS reporting ${badges(['ais'])}</h4>
    <dl class="soil"><dt>Reports received</dt><dd>${reported} of ${n} expected</dd><dt>Report interval</dt><dd>${s.ais.meta.step_min} min</dd>
      <dt>Seen by radar</dt><dd>${match ? `yes, ${fmt(match.distKm * 1000, 0)} m from AIS position` : gap ? (gap.status === 'matched' ? 'yes, near its expected position' : 'no') : 'not in the scene'}</dd></dl>
    ${vesselIntel(s, v.id)}
  </div>`;
}

function assetDrawer(s, a) {
  const level = assetFuel(s, a.id);
  const fl = fleetStatus(s).find((x) => x.asset.id === a.id);
  const u = assetUsage(s, a);
  return `
  <div class="drawer-head">
    <div><div class="drawer-title">${esc(a.name)} <span class="tag">${esc(a.type)}</span></div><div class="sub">Base ${esc(a.base)} · ${a.speed_kn} kn · ${a.burn_l_per_nm} L/nm</div></div>
    <button class="icon-btn" data-action="close-drawer" aria-label="Close">✕</button>
  </div>
  <div class="drawer-body">
    <div class="status-box status-${fl.task ? 'assigned' : 'ok'}"><b>${fl.task ? `On task: ${esc(fl.task.join(', '))}` : 'Available'}</b><span>Fuel ${esc(fmtQty(level, 'L'))} of ${esc(fmtQty(a.capacity, 'L'))}</span></div>
    ${stackedBar([[level / a.capacity, 'var(--ok)', 'fuel'], [1 - level / a.capacity, '#E3E8EE', 'empty']])}
    <h4>Planned mission ${badges(['rule'])}</h4>
    <ul class="watch">${fl.needs.length ? fl.needs.map((n) => `<li>${esc(n.alert.id)}: ${fmt(n.plan.totalNm - n.plan.returnNm, 0)} nm, arrival ${fmtDuration(n.plan.etaFirstH)}, ${fmtQty(n.plan.fuelL, 'L')} for the round trip.</li>`).join('') : '<li>No mission recommended for this asset right now.</li>'}</ul>
    <h4>Logged today ${badges(['captured'])}</h4>
    <dl class="soil"><dt>Dispatches</dt><dd>${u.taskedCount}</dd><dt>Fuel used</dt><dd>${u.usedFuel ? esc(fmtQty(u.usedFuel, 'L')) : '–'}</dd><dt>Refuelled</dt><dd>${u.refuelled ? esc(fmtQty(u.refuelled, 'L')) : '–'}</dd></dl>
    <div class="rec-actions"><button class="chip chip-go" data-log="Refuelled ${esc(a.name)} with ${Math.round(a.capacity * 0.5)} litres">Log refuelling…</button></div>
  </div>`;
}

export function entityDrawer(s) {
  const id = s.ui.entity;
  if (s.server && /^A-\d+$/.test(id)) return serverAlertDrawer(s, id);
  if (s.server && /^INC-/.test(id)) return incidentDrawer(s, id);
  if (s.server && /^OBS-\d+$/.test(id)) return observationDrawer(s, id);
  if (s.server && /^FC-[0-9a-f]+$/.test(id)) return fusedDrawer(s, id);
  if (s.server && /^SUB-\d+$/.test(id)) return submissionDrawer(s, id);
  const a = alertById(s, id);
  if (a) return alertDrawer(s, a);
  const v = vessel(s, id);
  if (v) return vesselDrawer(s, v);
  const as = asset(s, id);
  if (as) return assetDrawer(s, as);
  return '';
}
export { openAlerts, recentAssetUse, currentPosition, sum };

// Geographic map drawn as SVG (no map library, works offline). Equirectangular projection of the
// Gulf of Guinea with a simplified coastline from data/geo.json.
import { RISK, esc, fmt, typeColor, shortMmsi, fmtTime } from './util.js';
import {
  fusion, alerts, currentPosition, trackPoints, lastReport, assetPosition, assetTask, sarTime, alertTitle, positionAt,
} from './model.js';
import { minutesBetween, VESSEL_TYPES } from './util.js';

// Each vessel type has its own shape and colour. Shapes point to the bow and are rotated to the heading.
const SHAPES = {
  cargo: '<path d="M0,-8 L5,6 L0,3 L-5,6 Z"/>',                                   // arrowhead
  tanker: '<path d="M0,-10 L4.8,-4.5 L4.8,7.5 L-4.8,7.5 L-4.8,-4.5 Z"/>',              // long hull
  fishing: '<circle r="4.6"/><rect x="-1.1" y="-10.5" width="2.2" height="6.4" rx="1"/>',              // circle with bow line
  supply: '<path d="M0,-8 L4.6,-3.4 L4.6,5.5 L-4.6,5.5 L-4.6,-3.4 Z"/>',            // pentagon ("house")
  tug: '<path d="M0,-6 L5.2,-3 L5.2,3 L0,6 L-5.2,3 L-5.2,-3 Z"/>',                  // hexagon
  passenger: '<path d="M0,-8.5 L2.2,-2.8 L8,-2.6 L3.4,1.2 L5,7.2 L0,3.8 L-5,7.2 L-3.4,1.2 L-8,-2.6 L-2.2,-2.8 Z"/>', // star
};
export function vesselShape(type) { return SHAPES[type] || SHAPES.cargo; }
export function shapeIcon(type, size = 16) {
  return `<svg class="shape-ico" width="${size}" height="${size}" viewBox="-10 -10 20 20" aria-hidden="true"><g fill="${typeColor(type)}" stroke="#071C2E" stroke-width="1">${vesselShape(type)}</g></svg>`;
}
export const SHAPE_TYPES = Object.keys(VESSEL_TYPES);

const W = 1000;

export function projector(geo) {
  const { lon0, lon1, lat0, lat1 } = geo.bbox;
  const mid = ((lat0 + lat1) / 2) * (Math.PI / 180);
  const H = (W * (lat1 - lat0)) / ((lon1 - lon0) * Math.cos(mid));
  return {
    H,
    x: (lon) => ((lon - lon0) / (lon1 - lon0)) * W,
    y: (lat) => ((lat1 - lat) / (lat1 - lat0)) * H,
  };
}

const poly = (pr, pts) => pts.map(([lon, lat]) => `${pr.x(lon).toFixed(1)},${pr.y(lat).toFixed(1)}`).join(' ');

export function ndviLike(conf) { // confidence colour: low = grey, high = red
  const t = Math.max(0, Math.min(1, (conf - 0.3) / 0.65));
  const a = [138, 151, 166];
  const b = [255, 77, 90];
  return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(',')})`;
}

// Which vessels pass the map search/filters (q: name, MMSI, IMO or call sign; type; minimum risk level).
export function vesselFilter(s, f = {}) {
  const risk = s.server?.vesselMap || {};
  const order = { low: 0, medium: 1, high: 2, critical: 3 };
  const q = (f.q || '').trim().toLowerCase();
  const active = Boolean(q || f.type || f.risk);
  return {
    active,
    keep: (v) => (!f.type || v.type === f.type) && (!f.risk || order[risk[v.id]?.level || 'low'] >= order[f.risk]),
    hit: (v) => Boolean(q) && `${v.name} ${v.id} ${risk[v.id]?.imo || ''} ${risk[v.id]?.call_sign || ''}`.toLowerCase().includes(q),
  };
}

const INC_COLOR = { piracy_attack: '#FF4D5A', kidnapping: '#FF4D5A', boarding: '#F0556A', armed_robbery: '#F5A524', oil_theft: '#B58CE0',
  sts_transfer: '#A78BFA', illegal_fishing: '#3FBF9A', suspicious_approach: '#FFD166', smuggling: '#E8B04A' };

export function renderMap(s, { mode = 'sea', selected = null, flash = [], layers = {}, filter = {}, vb = null, at = null, trailMin = null, id = null, symbol = 1 } = {}) {
  // `at` (ISO time) replays the picture at that moment: positions interpolated, trails cut at that time.
  const geo = s.geo;
  const pr = projector(geo);
  const L = { tracks: true, footprint: true, zones: true, assets: true, platforms: true, lanes: true, incidents: true, agency: true, fused: true, ...layers };
  const vf = vesselFilter(s, filter);
  const riskOf = s.server?.vesselMap || {};
  const fu = fusion(s);
  const list = alerts(s);
  const bb = geo.bbox;
  const k = vb ? Math.max(0.12, vb[2] / W) : 1;     // keep symbols the same size on screen when zoomed
  const ks = k * symbol;
  const sc = ks === 1 ? '' : ` scale(${ks.toFixed(3)})`;

  const graticule = [];
  for (let lon = Math.ceil(bb.lon0); lon <= bb.lon1; lon += 1) {
    graticule.push(`<line x1="${pr.x(lon)}" x2="${pr.x(lon)}" y1="0" y2="${pr.H}" class="m-grid"/><text x="${pr.x(lon) + 4}" y="${pr.H - 6}" class="m-grid-lab">${lon}°E</text>`);
  }
  for (let lat = Math.ceil(bb.lat0); lat <= bb.lat1; lat += 1) {
    graticule.push(`<line y1="${pr.y(lat)}" y2="${pr.y(lat)}" x1="0" x2="${W}" class="m-grid"/><text x="6" y="${pr.y(lat) - 4}" class="m-grid-lab">${lat}°N</text>`);
  }

  const land = `<polygon points="${poly(pr, geo.land)}" class="m-land"/>${geo.islands.map((i) => `<polygon points="${poly(pr, i.poly)}" class="m-land"/>`).join('')}`;
  const zones = L.zones ? geo.zones.map((z) => {
    const [lx, ly] = z.poly[0];
    return `<polygon points="${poly(pr, z.poly)}" class="m-zone" style="--zc:${z.color}"><title>${esc(z.name)} · ${esc(z.kind)} watch zone</title></polygon>
      <text x="${pr.x(lx) + 8}" y="${pr.y(ly) + 15}" class="m-zone-lab" style="fill:${z.color}">${esc(z.name)}</text>`;
  }).join('') : '';
  const foot = L.footprint ? `<polygon points="${poly(pr, s.sar.scene.footprint)}" class="m-foot${mode === 'sar' ? ' is-strong' : ''}"/>
    <text x="${pr.x(s.sar.scene.footprint[0][0]) + 10}" y="${pr.y(s.sar.scene.footprint[0][1]) + 18}" class="m-foot-lab">Sentinel-1 · ${fmtTime(sarTime(s))} UTC</text>` : '';
  const platforms = L.platforms ? geo.platforms.map((p) => `<rect x="-4" y="-4" width="8" height="8" class="m-platform" transform="translate(${pr.x(p.lon).toFixed(1)} ${pr.y(p.lat).toFixed(1)})${sc}"><title>${esc(p.name)} (known infrastructure)</title></rect>`).join('') : '';
  const ports = geo.ports.map((p) => `<g class="m-port" transform="translate(${pr.x(p.lon).toFixed(1)} ${pr.y(p.lat).toFixed(1)})${sc}"><circle r="3.2"/><text x="6" y="-5">${esc(p.name)}</text></g>`).join('');

  const dim = mode === 'sar' ? ' is-dim' : '';
  const lanes = L.lanes && geo.lanes ? geo.lanes.map((l) => `<polyline class="m-lane" points="${poly(pr, l.pts)}"><title>${esc(l.name)} (usual shipping lane)</title></polyline>`).join('') : '';
  const incidents = L.incidents && s.server?.incidentsRecent ? s.server.incidentsRecent.map((i) => {
    const c = INC_COLOR[i.type] || '#7DD3FC';
    return `<g class="m-inc${selected === i.id ? ' is-selected' : ''}" data-entity="${esc(i.id)}" tabindex="0" role="button" aria-label="${esc(i.title)}" transform="translate(${pr.x(i.lon).toFixed(1)} ${pr.y(i.lat).toFixed(1)})${sc}">
      <title>${esc(i.id)} · ${esc(i.type_label)} · ${esc(i.occurred_at.slice(0, 10))} · ${esc(i.title)}</title><path d="M0,-6.5 L6,5 L-6,5 Z" fill="${c}"/><circle r="10" class="m-hit"/></g>`;
  }).join('') : '';
  const AG = { NN: '#2FBF8A', NIMASA: '#7DD3FC', MPOL: '#F5A524', NPA: '#B58CE0', GULFEYE: '#E4EEF6', NAF: '#60A5FA', NCS: '#F472B6', MMCC: '#FACC15' };
  const agencyObs = L.agency && s.server?.observationsRecent ? s.server.observationsRecent.map((o) => (
    `<g class="m-obs${selected === `OBS-${o.id}` ? ' is-selected' : ''}" data-entity="OBS-${o.id}" tabindex="0" role="button" aria-label="${esc(o.agency)} ${esc(o.kind)}" transform="translate(${pr.x(o.lon).toFixed(1)} ${pr.y(o.lat).toFixed(1)})${sc}">
      <title>OBS-${o.id} · ${esc(o.agency)} ${esc(o.kind.replace('_', ' '))} · ${esc(o.observed_at.slice(0, 16).replace('T', ' '))} UTC${o.vessel_name ? ` · ${esc(o.vessel_name)}` : ' · unidentified'}${o.description ? ` · ${esc(o.description)}` : ''}</title>
      <rect x="-5" y="-5" width="10" height="10" rx="2" transform="rotate(45)" fill="${AG[o.agency] || '#fff'}"/><text y="3.2">${o.kind === 'radar_contact' ? 'r' : o.kind === 'patrol_report' ? 'p' : 's'}</text><circle r="10" class="m-hit"/></g>`)).join('') : '';
  const fusedMarks = L.fused && s.server?.fused ? s.server.fused.map((f) => {
    const r = Math.max(13, (f.spread_km / ((geo.bbox.lon1 - geo.bbox.lon0) * 111)) * W + 9);
    return `<g class="m-fused${selected === f.id ? ' is-selected' : ''}" data-entity="${esc(f.id)}" tabindex="0" role="button" aria-label="Merged contact, ${f.independent_sources} sources" transform="translate(${pr.x(f.lon).toFixed(1)} ${pr.y(f.lat).toFixed(1)})${sc}">
      <title>${esc(f.id)} · merged contact · ${f.independent_sources} independent sources (${esc(f.sources.join(', '))}) · ${Math.round(f.confidence * 100)}% combined confidence</title>
      <circle r="${r.toFixed(1)}" class="m-fused-ring"/><text x="${(r * 0.72).toFixed(1)}" y="${(-r * 0.72).toFixed(1)}" class="m-fused-n">×${f.independent_sources}</text></g>`;
  }).join('') : '';
  const t0 = s.ais.meta.track_start;
  const step = s.ais.meta.step_min;
  const atIdx = at ? minutesBetween(t0, at) / step : null;
  const trailFrom = at && trailMin ? atIdx - trailMin / step : -1;
  const tracks = L.tracks ? s.ais.vessels.filter((v) => !vf.active || vf.keep(v)).map((v) => {
    let pts = trackPoints(v);
    if (at) {
      pts = v.pts.map((p, i) => (p && i <= atIdx && i >= trailFrom ? { lat: p[0], lon: p[1] } : null)).filter(Boolean);
      const cur = positionAt(s, v, at, 60);
      if (cur) pts.push(cur);
      if (pts.length < 2) return '';
    }
    return `<polyline class="m-track${dim}${selected === v.id ? ' is-sel' : ''}" points="${pts.map((p) => `${pr.x(p.lon).toFixed(1)},${pr.y(p.lat).toFixed(1)}`).join(' ')}"/>`;
  }).join('') : '';

  const gapIds = new Set(fu.gaps.map((g) => g.vessel.id));
  const vessels = s.ais.vessels.map((v) => {
    let silent = gapIds.has(v.id);
    let p = silent ? lastReport(s, v) : currentPosition(s, v);
    if (at) {                                   // replay: where was it at that moment?
      p = positionAt(s, v, at, 60);
      if (!p) {
        const before = v.pts.map((q, i) => (q && i <= atIdx ? i : -1)).filter((i) => i >= 0);
        if (!before.length) return '';
        const q = v.pts[before[before.length - 1]];
        p = { lat: q[0], lon: q[1], sog: q[2], cog: q[3] };
        silent = true;
      } else silent = false;
    }
    if (!p || (vf.active && !vf.keep(v))) return '';
    const sel = selected === v.id ? ' is-selected' : '';
    const lvl = riskOf[v.id]?.level;
    const ring = lvl && lvl !== 'low' ? `<circle r="11.5" class="m-risk" style="stroke:${RISK[lvl].color}"/>` : '';
    const hit = vf.hit(v) ? ' is-hit' : '';
    return `<g class="m-vessel${dim}${sel}${hit}${silent ? ' is-silent' : ''}" data-entity="${v.id}" tabindex="0" role="button" aria-label="${esc(v.name)}"
      transform="translate(${pr.x(p.lon).toFixed(1)} ${pr.y(p.lat).toFixed(1)}) rotate(${p.cog || 0})${sc}">
      <title>${esc(v.name)} · ${esc(v.flag)} · ${v.length_m} m · ${fmt(p.sog)} kn${silent ? ' · AIS off' : ''}${lvl ? ` · AI risk ${RISK[lvl].label}` : ''}</title>
      ${ring}<g class="shape s-${v.type}" fill="${typeColor(v.type)}">${vesselShape(v.type)}</g><circle r="11" class="m-hit"/></g>`;
  }).join('');

  const radarVisible = !at || at >= sarTime(s);
  const gapLines = fu.gaps.filter((g) => radarVisible && g.status === 'matched').map((g) => {
    const a = list.find((x) => x.vesselId === g.vessel.id);
    return `<line class="m-gapline" x1="${pr.x(g.last.lon)}" y1="${pr.y(g.last.lat)}" x2="${pr.x(a.lon)}" y2="${pr.y(a.lat)}"/>`;
  }).join('');
  const gapPred = fu.gaps.filter((g) => radarVisible && g.status !== 'matched').map((g) => (
    `<circle class="m-gapghost" cx="${pr.x(g.predicted.lon)}" cy="${pr.y(g.predicted.lat)}" r="9"><title>${esc(g.vessel.name)}: expected position, ${g.status === 'unseen' ? 'not detected' : 'outside the radar scene'}</title></circle>`
  )).join('');

  const detDots = mode === 'sar' ? fu.matches.map((m) => `<circle cx="${pr.x(m.det.lon)}" cy="${pr.y(m.det.lat)}" r="3.4" class="m-det-ok"><title>${esc(m.det.id)} matched to ${esc(m.v.name)}</title></circle>`).join('')
    + fu.infrastructure.map((i) => `<circle cx="${pr.x(i.det.lon)}" cy="${pr.y(i.det.lat)}" r="3.4" class="m-det-infra"><title>${esc(i.det.id)} = ${esc(i.platform.name)}</title></circle>`).join('') : '';

  const alertMarks = list.filter((a) => radarVisible || a.type === 'report').map((a) => {
    const x = pr.x(a.lon);
    const y = pr.y(a.lat);
    const st = a.state.code;
    const done = st === 'resolved' || st === 'false_alarm';
    const color = done ? '#6B7A89' : st === 'assigned' ? '#F5A524' : a.type === 'sts' ? '#A78BFA' : RISK[a.risk].color;
    const cls = `m-alert m-${a.type}${a.lowConf ? ' is-low' : ''}${done ? ' is-done' : ''}${selected === a.id ? ' is-selected' : ''}${flash.includes(a.id) ? ' is-flash' : ''}`;
    const shape = a.type === 'report'
      ? `<path d="M0,-10 L10,0 L0,10 L-10,0 Z" fill="${color}" class="m-core"/><text y="4" class="m-mark">!</text>`
      : a.lowConf ? `<circle r="6" class="m-core m-hollow" style="stroke:${color}"/>`
        : `<circle r="${a.type === 'ais_gap' ? 8 : 8.5}" class="m-core${a.type === 'ais_gap' ? ' m-hollow' : ''}" fill="${a.type === 'ais_gap' ? 'rgba(10,20,30,.6)' : color}" style="stroke:${color}"/>${a.type === 'ais_gap' ? `<text y="4" class="m-mark" style="fill:${color}">×</text>` : ''}`;
    return `<g class="${cls}" data-entity="${a.id}" tabindex="0" role="button" aria-label="${esc(alertTitle(s, a))}" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})${sc}" style="--ac:${color}">
      <title>${esc(a.id)} · ${esc(alertTitle(s, a))} · ${esc(RISK[a.risk].label)}${st !== 'new' ? ` · ${st.replace('_', ' ')}` : ''}</title>
      ${a.lowConf || done ? '' : '<circle r="8.5" class="m-pulse"/>'}${shape}
      ${a.lowConf ? '' : `<text x="13" y="-9" class="m-alert-lab">${esc(a.id)}</text>`}<circle r="14" class="m-hit"/></g>`;
  }).join('');

  const assetMarks = L.assets ? s.assets.assets.map((a) => {
    const p = assetPosition(s, a);
    const task = assetTask(s, a);
    return `<g class="m-asset${task ? ' is-task' : ''}${selected === a.id ? ' is-selected' : ''}" data-entity="${a.id}" transform="translate(${pr.x(p.lon).toFixed(1)} ${pr.y(p.lat).toFixed(1)})${sc}" tabindex="0" role="button" aria-label="${esc(a.name)}">
      <title>${esc(a.name)} · ${esc(a.base)}${task ? ` · on task ${esc(task.join(', '))}` : ''}</title>
      <path d="M0,-8 L8,0 L0,8 L-8,0 Z"/><text y="3.4">${a.type === 'AIR' ? '✈' : a.type === 'RHIB' ? 'R' : 'P'}</text></g>`;
  }).join('') : '';
  const taskLines = L.assets ? s.assets.assets.map((a) => {
    const task = assetTask(s, a);
    if (!task) return '';
    const al = list.find((x) => x.id === task[0]);
    return al ? `<line class="m-taskline" x1="${pr.x(a.lon)}" y1="${pr.y(a.lat)}" x2="${pr.x(al.lon)}" y2="${pr.y(al.lat)}"/>` : '';
  }).join('') : '';

  const box = vb || [0, 0, W, Number(pr.H.toFixed(1))];
  const countries = geo.countries ? geo.countries.map((c) => c.polys.map((pp) => `<polygon points="${poly(pr, pp)}" class="m-country"><title>${esc(c.name)}</title></polygon>`).join('')).join('') : '';
  const countryLabels = geo.countries ? geo.countries.filter((c) => c.label[0] > bb.lon0 + 0.4 && c.label[0] < bb.lon1 - 0.4 && c.label[1] < bb.lat1 - 0.2 && c.label[1] > bb.lat0 + 0.2).map((c) => `<text x="${pr.x(c.label[0])}" y="${pr.y(c.label[1])}" class="m-country-lab">${esc(c.name)}</text>`).join('') : '';
  return `<svg class="sea-map"${id ? ` id="${id}"` : ''} style="--k:${k.toFixed(3)}" data-full="0 0 ${W} ${pr.H.toFixed(1)}" viewBox="${box.join(' ')}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Map of the Gulf of Guinea with AIS tracks and radar alerts">
    <defs>
      <linearGradient id="ocean" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0E3550"/><stop offset="1" stop-color="#071C2E"/></linearGradient>
      <radialGradient id="vignette" cx="50%" cy="50%" r="75%"><stop offset="60%" stop-color="#000" stop-opacity="0"/><stop offset="100%" stop-color="#000" stop-opacity=".35"/></radialGradient>
    </defs>
    <rect class="m-ocean" x="0" y="0" width="${W}" height="${pr.H}"/>
    ${graticule.join('')}${zones}${foot}${land}${countries}${countryLabels}${lanes}${tracks}${platforms}${gapLines}${taskLines}${gapPred}${detDots}${fusedMarks}${incidents}${vessels}${ports}${assetMarks}${agencyObs}${alertMarks}
    <rect x="0" y="0" width="${W}" height="${pr.H}" fill="url(#vignette)" pointer-events="none"/>
  </svg>`;
}

export const MAP_LEGEND = [
  ['dark', 'Dark vessel (radar, no AIS)'], ['gap', 'AIS switched off'], ['sts', 'Possible transfer'], ['risk', 'AI risk ring (Medium+)'], ['inc', 'Incident (90 days)'], ['agency', 'Agency report (7 days)'], ['fused', 'Merged contact (several sources)'], ['asset', 'Patrol asset'], ['platform', 'Known platform'],
];
export { shortMmsi };

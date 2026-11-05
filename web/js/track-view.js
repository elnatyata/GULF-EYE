// Live tracking map: the whole Gulf of Guinea picture filling the screen, with a time slider to replay the
// last 12 hours of AIS movement, vessel trails, follow-a-vessel, and a searchable vessel list.
// Works in static mode and with the backend (adds risk rings, incidents, agency reports, merged contacts).
import { esc, fmt, fmtTime, fmtDate, addMinutes, minutesBetween, typeName, RISK, VESSEL_TYPES } from './util.js';
import { renderMap, projector, shapeIcon, vesselFilter } from './map.js';
import { positionAt, nowIso, sarTime } from './model.js';

const TRAILS = [[30, '30 min'], [120, '2 h'], [360, '6 h'], [0, 'Full 12 h']];
const SPEEDS = [[5, '1×'], [10, '2×'], [20, '4×'], [40, '8×']];   // simulated minutes advanced per tick
const TICK_MS = 250;
const LIST_MAX = 400;

export const totalMin = (s) => minutesBetween(s.ais.meta.track_start, nowIso(s));

export function trackState(s) {
  if (!s.ui.track) s.ui.track = { min: totalMin(s), playing: false, speed: 10, trail: 120, follow: false };
  return s.ui.track;
}

export const trackTime = (s) => addMinutes(s.ais.meta.track_start, trackState(s).min);

// Map options for the tracking view (used by the view and by the redraw on every playback tick).
export function trackMapOpts(s) {
  const t = trackState(s);
  const live = t.min >= totalMin(s);
  return {
    selected: s.ui.entity, flash: s.ui.flash, layers: s.ui.layers, filter: s.ui.mapf, vb: s.ui.vb,
    at: live ? null : trackTime(s), trailMin: t.trail || null, id: 'track-map', symbol: 1.35,
  };
}

function typeCounts(s) {
  const c = {};
  for (const v of s.ais.vessels) c[v.type] = (c[v.type] || 0) + 1;
  return c;
}

function riskOf(s, v) { return s.server?.vesselMap?.[v.id]?.level || null; }

export function vesselList(s) {
  const vf = vesselFilter(s, s.ui.mapf);
  const q = s.ui.mapf.q.trim().toLowerCase();
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  const rows = s.ais.vessels
    .filter((v) => (!vf.active || vf.keep(v)) && (!q || vf.hit(v)))
    .sort((a, b) => (order[riskOf(s, a) || 'low'] - order[riskOf(s, b) || 'low']) || a.name.localeCompare(b.name));
  const at = trackTime(s);
  const html = rows.slice(0, LIST_MAX).map((v) => {
    const p = positionAt(s, v, at, 60);
    const lvl = riskOf(s, v);
    return `<button class="tv-row${s.ui.entity === v.id ? ' is-sel' : ''}" data-act="track-pick" data-id="${esc(v.id)}">
      ${shapeIcon(v.type, 18)}
      <span class="tv-name"><b>${esc(v.name)}</b><span>${esc(typeName(v.type))} · ${esc(v.flag)} · ${esc(v.id)}</span></span>
      <span class="tv-meta">${p ? `${fmt(p.sog)} kn` : '<span class="tv-off">AIS off</span>'}${lvl && lvl !== 'low' ? `<span class="risk risk-${lvl}">${RISK[lvl].label}</span>` : ''}</span>
    </button>`;
  }).join('');
  return `<div class="tv-count muted small">${rows.length} of ${s.ais.vessels.length} vessels${rows.length > LIST_MAX ? ` · first ${LIST_MAX} shown` : ''}</div>${html || '<p class="muted small">No vessel matches.</p>'}`;
}

function shapeLegend(s) {
  const c = typeCounts(s);
  const f = s.ui.mapf.type;
  return Object.keys(VESSEL_TYPES).map((t) => `<button type="button" class="chip chip-sm tv-type${f === t ? ' is-on' : ''}" data-act="track-type" data-type="${t}" title="Show only ${esc(typeName(t))}">
    ${shapeIcon(t, 16)} ${esc(typeName(t))} <span class="muted">${c[t] || 0}</span></button>`).join('');
}

export function clockLabel(s) {
  const t = trackState(s);
  const live = t.min >= totalMin(s);
  const at = trackTime(s);
  const radar = at >= sarTime(s) ? ' · radar pass shown' : ` · before radar pass (${fmtTime(sarTime(s))})`;
  return `<b>${esc(fmtDate(at))} ${esc(fmtTime(at))} UTC</b> <span class="tag ${live ? 'tag-live' : 'tag-replay'}">${live ? 'Latest picture' : 'Replay'}</span><span class="muted small">${radar}</span>`;
}

export function trackView(s) {
  const t = trackState(s);
  const total = totalMin(s);
  const f = s.ui.mapf;
  const layerList = [['tracks', 'Trails'], ['zones', 'Risk zones'], ['lanes', 'Shipping lanes'], ['platforms', 'Platforms'], ['assets', 'Patrol assets'], ['footprint', 'Radar scene']];
  if (s.server) layerList.push(['incidents', 'Incidents'], ['agency', 'Agency reports'], ['fused', 'Merged contacts']);
  const L = s.ui.layers;
  return `<section class="track-card" id="track-card">
    <div class="tv-bar">
      <div class="tv-title"><h2>Live tracking map</h2><p class="sub">${s.ais.vessels.length} vessels · whole Gulf of Guinea · <span class="tag tag-synthetic">Simulated AIS</span> not live intelligence</p></div>
      <div class="tv-play" data-form="track-ctl">
        <button class="icon-btn" data-act="track-start" title="Go to start of the 12 h window" aria-label="Start">⏮</button>
        <button class="icon-btn tv-pp" data-act="track-toggle" aria-label="${t.playing ? 'Pause' : 'Play'}" title="${t.playing ? 'Pause' : 'Play the last 12 hours'}">${t.playing ? '⏸' : '▶'}</button>
        <button class="icon-btn" data-act="track-end" title="Jump to the latest picture" aria-label="Latest">⏭</button>
        <input type="range" class="tv-slider" name="track-t" min="0" max="${total}" step="5" value="${t.min}" aria-label="Time">
        <span class="tv-clock" id="tv-clock">${clockLabel(s)}</span>
        <label class="tv-sel">Speed <select name="track-speed">${SPEEDS.map(([v, l]) => `<option value="${v}"${t.speed === v ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
        <label class="tv-sel">Trail <select name="track-trail">${TRAILS.map(([v, l]) => `<option value="${v}"${t.trail === v ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
        <label class="tv-sel"><input type="checkbox" name="track-follow"${t.follow ? ' checked' : ''}> Follow selected</label>
        <button class="btn ghost small-btn" data-act="track-full" title="Use the whole screen">⛶ Full screen</button>
      </div>
    </div>
    <div class="tv-body">
      <div class="tv-mapcol">
        <div class="chips tight tv-layers">${layerList.map(([k, l]) => `<button class="chip chip-sm${L[k] ? ' is-on' : ''}" data-layer="${k}">${l}</button>`).join('')}
          <span class="zoom"><button type="button" class="icon-btn" data-act="zoom" data-z="in" aria-label="Zoom in">+</button><button type="button" class="icon-btn" data-act="zoom" data-z="out" aria-label="Zoom out">−</button><button type="button" class="icon-btn" data-act="zoom" data-z="reset" aria-label="Whole region">⤢</button></span>
        </div>
        <div class="map-wrap tv-map" data-zoomable="1">${renderMap(s, trackMapOpts(s))}</div>
        <div class="tv-legend">${shapeLegend(s)}<span class="tv-note muted small">Shape and colour = vessel type · points to heading · dashed outline = AIS off${s.server ? ' · ring = AI risk Medium+' : ''}</span></div>
      </div>
      <aside class="tv-side">
        <form class="map-tools tv-search" data-form="map-filter" role="search">
          <input name="q" type="search" value="${esc(f.q)}" placeholder="Search name, MMSI, IMO…" autocomplete="off" aria-label="Search vessels">
          <select name="type" aria-label="Vessel type"><option value="">All types</option>${Object.keys(VESSEL_TYPES).map((ty) => `<option value="${ty}" ${f.type === ty ? 'selected' : ''}>${esc(typeName(ty))}</option>`).join('')}</select>
          ${s.server ? `<select name="risk" aria-label="Minimum risk"><option value="">Any risk</option>${['medium', 'high', 'critical'].map((r) => `<option value="${r}" ${f.risk === r ? 'selected' : ''}>${RISK[r].label}+</option>`).join('')}</select>` : ''}
          ${f.q || f.type || f.risk ? '<button type="button" class="chip chip-sm" data-act="map-clear">Clear</button>' : ''}
        </form>
        <div class="tv-list" id="track-list">${vesselList(s)}</div>
      </aside>
    </div>
  </section>`;
}

// ------------------------------------------------------------------ playback (DOM updates without a full re-render)
let timer = null;

export function redrawTrack(app, { list = false } = {}) {
  const s = app.state;
  const t = trackState(s);
  if (t.follow && s.ui.entity) centreOn(s, s.ui.entity);
  const wrap = document.querySelector('.tv-map');
  if (!wrap) return;
  wrap.innerHTML = renderMap(s, trackMapOpts(s));
  const clock = document.getElementById('tv-clock');
  if (clock) clock.innerHTML = clockLabel(s);
  const slider = document.querySelector('.tv-slider');
  if (slider && Number(slider.value) !== t.min) slider.value = t.min;
  const pp = document.querySelector('.tv-pp');
  if (pp) { pp.textContent = t.playing ? '⏸' : '▶'; pp.setAttribute('aria-label', t.playing ? 'Pause' : 'Play'); }
  if (list) { const el = document.getElementById('track-list'); if (el) el.innerHTML = vesselList(s); }
}

function centreOn(s, id) {
  const v = s.ais.vessels.find((x) => x.id === id);
  if (!v) return;
  const p = positionAt(s, v, trackTime(s), 24 * 60);
  if (!p) return;
  const pr = projector(s.geo);
  const fullW = 1000;
  const fullH = Number(pr.H.toFixed(1));
  const w = s.ui.vb ? s.ui.vb[2] : fullW / 3;
  const h = w * (fullH / fullW);
  const x = Math.min(fullW - w, Math.max(0, pr.x(p.lon) - w / 2));
  const y = Math.min(fullH - h, Math.max(0, pr.y(p.lat) - h / 2));
  s.ui.vb = [x, y, w, h].map((n) => Number(n.toFixed(1)));
}

function stop(app) {
  clearInterval(timer);
  timer = null;
  trackState(app.state).playing = false;
}

function play(app) {
  const s = app.state;
  const t = trackState(s);
  if (t.min >= totalMin(s)) t.min = 0;           // replay from the start
  t.playing = true;
  clearInterval(timer);
  let n = 0;
  timer = setInterval(() => {
    if (app.state.ui.view !== 'track') { stop(app); return; }
    t.min = Math.min(totalMin(s), t.min + t.speed);
    if (t.min >= totalMin(s)) stop(app);
    n += 1;
    redrawTrack(app, { list: !t.playing || n % 8 === 0 });
  }, TICK_MS);
}

// data-act handlers (called from ops.handleAct). Returns true when handled.
export function trackAct(app, el) {
  const s = app.state;
  const t = trackState(s);
  const act = el.dataset.act;
  if (act === 'track-toggle') { if (t.playing) stop(app); else play(app); redrawTrack(app, { list: true }); return true; }
  if (act === 'track-start') { t.min = 0; redrawTrack(app, { list: true }); return true; }
  if (act === 'track-end') { stop(app); t.min = totalMin(s); redrawTrack(app, { list: true }); return true; }
  if (act === 'track-type') { s.ui.mapf = { ...s.ui.mapf, type: s.ui.mapf.type === el.dataset.type ? '' : el.dataset.type }; app.render(); return true; }
  if (act === 'track-pick') {
    s.ui.entity = el.dataset.id;
    t.follow = true;
    if (!s.ui.vb) s.ui.vb = [0, 0, 1000 / 3, 0];   // zoom in on the vessel; centreOn fixes the box
    centreOn(s, el.dataset.id);
    app.openEntity ? app.openEntity(el.dataset.id) : app.render();
    return true;
  }
  if (act === 'track-full') {
    const card = document.getElementById('track-card');
    if (document.fullscreenElement) document.exitFullscreen?.();
    else if (card?.requestFullscreen) card.requestFullscreen().catch(() => app.toast('Full screen is not allowed here; the map already fills the window.'));
    else app.toast('Full screen is not supported by this browser.');
    return true;
  }
  return false;
}

// input/change on the playback controls
export function trackControl(app, el) {
  const t = trackState(app.state);
  if (el.name === 'track-t') { if (t.playing) stop(app); t.min = Number(el.value); redrawTrack(app, { list: true }); return true; }
  if (el.name === 'track-speed') { t.speed = Number(el.value); return true; }
  if (el.name === 'track-trail') { t.trail = Number(el.value); redrawTrack(app); return true; }
  if (el.name === 'track-follow') { t.follow = el.checked; if (!el.checked) app.state.ui.vb = null; redrawTrack(app); return true; }
  return false;
}

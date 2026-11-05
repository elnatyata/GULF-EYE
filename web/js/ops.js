// Everything that talks to the backend from the UI: loading data, buttons (data-act), forms (data-form), map search,
// zoom and pan, and realtime events. main.js calls these; views stay pure functions of the state.
import { api } from './api.js';
import { esc, addDays } from './util.js';
import { renderMap } from './map.js';
import { incidentForm } from './intel-views.js';
import { trackAct, trackControl, redrawTrack } from './track-view.js';

const demoToday = (s) => s.ais.meta.now.slice(0, 10);

// ------------------------------------------------------------------ loading
export async function loadBase(app) {
  const s = app.state;
  const from = addDays(demoToday(s), -90);
  const since = `${addDays(demoToday(s), -7)}T00:00:00Z`;
  const [me, summary, vessels, alerts, recent, obs] = await Promise.all([
    api.get('/api/me'), api.get('/api/summary'), api.get('/api/vessels'), api.get('/api/alerts'),
    api.get(`/api/incidents?from=${from}`), api.get(`/api/observations?date_from=${since}`),
  ]);
  const [fused, ags] = await Promise.all([api.get('/api/fused'), api.get('/api/agencies')]);
  const sv = s.server || { detail: {} };
  Object.assign(sv, {
    me, summary, vessels: vessels.vessels, alerts: alerts.alerts, disclaimer: alerts.disclaimer,
    incidentsRecent: recent.incidents, incidentTypes: recent.types, observationsRecent: obs.observations,
    agencyCodes: ags.agencies.map((a) => a.code),
    fused: fused.fused, fusedByMember: Object.fromEntries(fused.fused.flatMap((f) => f.members.map((m) => [m.id, f]))),
    vesselMap: Object.fromEntries(vessels.vessels.map((v) => [v.id, v])),
  });
  s.server = sv;
}

export async function loadView(app, view = app.state.ui.view) {
  const s = app.state;
  const sv = s.server;
  if (!sv) return;
  if (view === 'incidents') {
    const f = s.ui.inf;
    const qs = new URLSearchParams(Object.entries({ q: f.q, type: f.type, status: f.status, from: f.from, to: f.to }).filter(([, v]) => v));
    sv.incidents = await api.get(`/api/incidents?${qs}`);
  } else if (view === 'intel') {
    [sv.intel, sv.sources, sv.duplicates, sv.merges] = await Promise.all([api.get('/api/intel'), api.get('/api/sources'),
      api.get('/api/incidents/duplicates'), api.get('/api/incidents/merges')]);
  } else if (view === 'analytics') {
    const r = s.ui.range;
    sv.analytics = await api.get(`/api/analytics?from=${r.from}&to=${r.to}`);
  } else if (view === 'intake') {
    const [kinds, submissions, watchlist, observations, keys] = await Promise.all([
      api.get('/api/intake/kinds'), api.get('/api/intake/submissions'), api.get('/api/watchlist'), api.get('/api/observations?limit=60'),
      sv.me.permissions.includes('keys:manage') ? api.get('/api/apikeys') : Promise.resolve(null)]);
    sv.intake = { kinds, submissions, watchlist, observations, keys };
  } else if (view === 'agencies') {
    [sv.agencies, sv.roles, sv.users, sv.audit, sv.inbox] = await Promise.all([
      api.get('/api/agencies'), api.get('/api/roles'), api.get('/api/users'),
      api.get(`/api/audit?action=${encodeURIComponent(s.ui.auditFilter || '')}`), api.get('/api/shares/inbox')]);
  }
}

export async function loadDetail(app, id) {
  const sv = app.state.server;
  if (!sv || !id) return false;
  if (/^A-\d+$/.test(id)) sv.detail[`a:${id}`] = await api.get(`/api/alerts/${id}`);
  else if (/^INC-/.test(id)) sv.detail[`i:${id}`] = await api.get(`/api/incidents/${id}`);
  else if (/^\d{9}$/.test(id)) sv.detail[`v:${id}`] = await api.get(`/api/vessels/${id}`);
  else if (/^OBS-\d+$/.test(id)) sv.detail[`o:${id}`] = await api.get(`/api/observations/${id.slice(4)}`);
  else if (/^FC-[0-9a-f]+$/.test(id)) sv.detail[`f:${id}`] = await api.get(`/api/fused/${id}`).catch(() => null);
  else if (/^SUB-\d+$/.test(id)) { if (!sv.intake) await loadView(app, 'intake'); }
  else return false;
  return true;
}

export async function refreshAll(app) {
  await loadBase(app);
  await loadView(app);
  if (app.state.ui.entity) await loadDetail(app, app.state.ui.entity).catch(() => {});
  app.render();
}

// ------------------------------------------------------------------ panel (incident form)
function openPanel(app, preset) {
  const root = document.getElementById('panel');
  root.innerHTML = incidentForm(app.state, preset);
  root.hidden = false;
  root.querySelector('select[name=type]').focus();
}
export function closePanel() {
  const root = document.getElementById('panel');
  root.hidden = true;
  root.innerHTML = '';
}

// ------------------------------------------------------------------ buttons
export async function handleAct(app, el) {
  const s = app.state;
  const act = el.dataset.act;
  if (act.startsWith('track-')) return trackAct(app, el);
  try {
    if (act === 'af') { s.ui.af[el.dataset.k] = el.dataset.v; app.render(); }
    else if (act === 'run-engine') {
      el.disabled = true;
      const r = await api.post('/api/engine/run');
      await refreshAll(app);
      app.toast(`Engine re-scored ${r.vessels} vessels: ${r.created.length} new alert(s), ${r.updated.length} updated.`);
    } else if (act === 'incident-new') {
      const v = el.dataset.vessel ? s.server.vesselMap[el.dataset.vessel] : null;
      const lat = el.dataset.lat && el.dataset.lat !== 'null' ? Number(el.dataset.lat) : v ? null : null;
      const p = v ? s.server.detail[`v:${v.id}`]?.last : null;
      openPanel(app, { vessel: el.dataset.vessel || '', lat: lat ?? p?.lat ?? '', lon: el.dataset.lon && el.dataset.lon !== 'null' ? Number(el.dataset.lon) : p?.lon ?? '' });
    } else if (act === 'close-panel') closePanel();
    else if (act === 'download') await api.download(`/api/attachments/${el.dataset.id}`, el.dataset.name);
    else if (act === 'export') {
      const r = s.ui.range;
      await api.download(`/api/export/${el.dataset.kind}?from=${r.from}&to=${r.to}`, el.dataset.kind);
      app.toast('Export downloaded. It is recorded in the audit log.');
    } else if (act === 'print') window.print();
    else if (act === 'range') {
      const today = demoToday(s);
      s.ui.range = { from: addDays(today, -Number(el.dataset.days)), to: today, preset: el.dataset.days };
      await loadView(app); app.render();
    } else if (act === 'audit-filter') { s.ui.auditFilter = el.dataset.v; await loadView(app); app.render(); }
    else if (act === 'map-clear') { s.ui.mapf = { q: '', type: '', risk: '' }; app.render(); }
    else if (act === 'zoom') zoomButton(app, el.dataset.z);
    else if (act === 'intake-template') await api.download(`/api/intake/templates/${el.dataset.kind}.csv`, `gulfeye_${el.dataset.kind}_template.csv`);
    else if (act === 'key-dismiss') { s.ui.newKey = null; app.render(); }
    else if (act === 'merge') {
      const note = window.prompt(`Merge ${el.dataset.duplicate} into ${el.dataset.primary}? Both reports are kept and the event is counted once. You can undo this later.\n\nOptional note:`, '');
      if (note === null) return true;
      await api.post('/api/incidents/merge', { primary: el.dataset.primary, duplicate: el.dataset.duplicate, note });
      if (s.ui.entity === el.dataset.duplicate) s.ui.entity = el.dataset.primary;
      await refreshAll(app);
      app.toast(`${el.dataset.duplicate} merged into ${el.dataset.primary}. Recorded in the audit log.`);
    } else if (act === 'not-dup') {
      const note = window.prompt('Why are these different events? (optional)', '');
      if (note === null) return true;
      await api.post('/api/incidents/not-duplicate', { a: el.dataset.a, b: el.dataset.b, note });
      await refreshAll(app);
      app.toast('Marked as different events; this pair will not be suggested again.');
    } else if (act === 'unmerge') {
      if (!el.dataset.mid || !window.confirm('Undo this merge? The report becomes a separate incident again.')) return true;
      await api.post(`/api/incidents/merge/${el.dataset.mid}/undo`);
      await refreshAll(app);
      app.toast('Merge undone. Recorded in the audit log.');
    }
    else if (act === 'key-revoke') {
      if (!window.confirm('Revoke this key? The agency system using it will be refused from now on.')) return true;
      await api.request('DELETE', `/api/apikeys/${el.dataset.id}`);
      await loadView(app); app.render(); app.toast('Key revoked and logged.');
    }
    else return false;
  } catch (e) {
    app.toast(`Could not complete: ${e.message}`);
    el.disabled = false;
  }
  return true;
}

export async function handleChange(app, el) {
  const s = app.state;
  if (el.name?.startsWith('track-')) return trackControl(app, el);
  try {
    if (el.dataset.actChange === 'af-behaviour') { s.ui.af.behaviour = el.value; app.render(); return true; }
    if (el.dataset.actChange === 'intake-kind') { s.ui.intakeKind = el.value; s.ui.intakeResult = null; app.render(); return true; }
    if (el.dataset.actChange === 'intake-format') { s.ui.intakeFormat = el.value; app.render(); return true; }
    if (el.dataset.wlActive) {
      await api.patch(`/api/watchlist/${el.dataset.wlActive}`, { active: el.checked });
      app.toast(el.checked ? 'Watchlist entry active again; scores updated.' : 'Watchlist entry deactivated; scores updated.');
      await refreshAll(app); return true;
    }
    if (el.dataset.userRole) {
      await api.patch(`/api/users/${el.dataset.userRole}`, { role: el.value });
      app.toast('Role updated and logged.'); await loadView(app); app.render(); return true;
    }
    if (el.dataset.userActive) {
      await api.patch(`/api/users/${el.dataset.userActive}`, { active: el.checked });
      app.toast(el.checked ? 'Account enabled.' : 'Account disabled.'); await loadView(app); app.render(); return true;
    }
    if (el.name === 'preset' && el.closest('[data-form="incident-create"]')) {
      const form = el.form;
      if (el.value) {
        const [la, lo] = el.value.split(',');
        form.lat.value = la;
        form.lon.value = lo;
        if (!form.place.value) form.place.value = el.selectedOptions[0].dataset.name;
      }
      return true;
    }
    if (el.closest('[data-form="map-filter"]') && el.tagName === 'SELECT') { mapFilterFrom(app, el.form); return true; }
  } catch (e) {
    app.toast(`Could not complete: ${e.message}`);
    await loadView(app).catch(() => {}); app.render();
    return true;
  }
  return false;
}

// ------------------------------------------------------------------ forms
export async function handleSubmit(app, form) {
  const s = app.state;
  const kind = form.dataset.form;
  const fd = new FormData(form);
  const btn = form.querySelector('button:not([type=button])');
  if (btn) btn.disabled = true;
  try {
    if (kind === 'comment') {
      await api.post('/api/comments', { target_type: form.dataset.type, target_id: form.dataset.id, body: fd.get('body') });
      await loadDetail(app, form.dataset.id); app.render(); app.toast('Note added.');
    } else if (kind === 'share') {
      const to = fd.getAll('to');
      if (!to.length) throw new Error('choose at least one agency');
      await api.post('/api/shares', { target_type: form.dataset.type, target_id: form.dataset.id, to_agencies: to, message: fd.get('message') || '' });
      await loadDetail(app, form.dataset.id); app.render(); app.toast(`Shared with ${to.join(', ')}.`);
    } else if (kind === 'alert-status') {
      const body = { status: fd.get('status'), note: fd.get('note') || '' };
      if (fd.get('outcome')) body.outcome = fd.get('outcome');
      await api.post(`/api/alerts/${form.dataset.id}/status`, body);
      await refreshAll(app); app.toast(`${form.dataset.id}: status saved.`);
    } else if (kind === 'incident-status') {
      await api.post(`/api/incidents/${form.dataset.id}/status`, { status: fd.get('status'), note: fd.get('note') || '' });
      await refreshAll(app); app.toast(`${form.dataset.id}: status saved.`);
    } else if (kind === 'attach') {
      const up = new FormData();
      up.append('file', fd.get('file'));
      await api.request('POST', `/api/incidents/${form.dataset.id}/attachments`, up);
      await loadDetail(app, form.dataset.id); app.render(); app.toast('File uploaded.');
    } else if (kind === 'incident-filter') {
      s.ui.inf = { q: fd.get('q') || '', type: fd.get('type') || '', status: fd.get('status') || '', from: fd.get('from') || '', to: fd.get('to') || '' };
      await loadView(app); app.render();
    } else if (kind === 'range') {
      s.ui.range = { from: fd.get('from'), to: fd.get('to'), preset: null };
      await loadView(app); app.render();
    } else if (kind === 'map-filter') {
      mapFilterFrom(app, form);
    } else if (kind === 'agency-create') {
      await api.post('/api/agencies', Object.fromEntries(fd));
      await refreshAll(app); app.toast(`Agency ${fd.get('code')} onboarded.`);
    } else if (kind === 'user-create') {
      await api.post('/api/users', Object.fromEntries(fd));
      await loadView(app); app.render(); app.toast('User created.');
    } else if (kind === 'incident-create') {
      await createIncident(app, form, fd);
    } else if (kind === 'intake-submit') {
      const k = fd.get('kind');
      let res;
      if (fd.get('format') === 'json') {
        let parsed;
        try { parsed = JSON.parse(fd.get('json')); } catch (e) { throw new Error(`the JSON is not valid (${e.message})`); }
        const records = Array.isArray(parsed) ? parsed : parsed.records;
        if (!Array.isArray(records)) throw new Error('send a list of records, or {"records": [...]}');
        res = await api.post(`/api/intake/${k}`, { records });
      } else {
        const file = fd.get('file');
        if (!file || !file.size) throw new Error('choose a CSV file');
        const up = new FormData();
        up.append('file', file);
        res = await api.request('POST', `/api/intake/${k}/csv`, up);
      }
      s.ui.intakeResult = res;
      await refreshAll(app);
      app.toast(`Submission ${res.submission}: ${res.accepted} accepted, ${res.rejected} rejected.`);
    } else if (kind === 'key-create') {
      const body = { name: fd.get('name'), scopes: fd.getAll('scopes') };
      if (fd.get('agency')) body.agency = fd.get('agency');
      if (!body.scopes.length) throw new Error('tick at least one data type');
      s.ui.newKey = await api.post('/api/apikeys', body);
      await loadView(app); app.render();
    }
  } catch (e) {
    const err = form.querySelector('.form-error');
    if (err) err.textContent = e.message; else app.toast(`Could not save: ${e.message}`);
  } finally {
    if (btn && document.contains(btn)) btn.disabled = false;
  }
}

async function createIncident(app, form, fd) {
  const vessels = [0, 1, 2].map((i) => ({ vessel_id: fd.get(`vessel${i}`), role: fd.get(`role${i}`) })).filter((v) => v.vessel_id);
  const body = {
    type: fd.get('type'), title: fd.get('title').trim(), description: fd.get('description').trim(),
    lat: Number(fd.get('lat')), lon: Number(fd.get('lon')), place: fd.get('place') || null,
    occurred_at: `${fd.get('date')}T${fd.get('time')}:00Z`, visibility: fd.get('visibility'), vessels,
  };
  if (Number.isNaN(body.lat) || Number.isNaN(body.lon) || fd.get('lat') === '' || fd.get('lon') === '') throw new Error('give a position (pick a known place or enter latitude and longitude)');
  const files = fd.getAll('files').filter((f) => f && f.size);
  for (const f of files) if (f.size > 5 * 1024 * 1024) throw new Error(`${f.name} is larger than 5 MB`);
  const inc = await api.post('/api/incidents', body);
  const failed = [];
  for (const f of files) {
    const up = new FormData();
    up.append('file', f);
    try { await api.request('POST', `/api/incidents/${inc.id}/attachments`, up); } catch (e) { failed.push(`${f.name} (${e.message})`); }
  }
  closePanel();
  app.state.ui.entity = inc.id;
  await refreshAll(app);
  app.toast(`Incident ${inc.id} reported${files.length ? ` with ${files.length - failed.length} file(s)` : ''}.${failed.length ? ` Not uploaded: ${failed.join(', ')}` : ''}`);
}

// Field reports logged through the existing voice/text dialog also go to the shared incident register.
const KIND_TO_TYPE = { attack: 'piracy_attack', boarding: 'boarding', kidnap: 'kidnapping', skiff: 'suspicious_approach',
  lights: 'suspicious_approach', fishing: 'illegal_fishing', transfer: 'sts_transfer' };
export async function mirrorFieldReport(app, ev) {
  if (!app.state.server?.me?.permissions.includes('incidents:create')) return;
  try {
    const inc = await api.post('/api/incidents', {
      type: KIND_TO_TYPE[ev.kind] || 'other', title: `Field report ${ev.entity}: ${ev.kind}${ev.place ? ` near ${ev.place}` : ''}`,
      description: ev.raw || 'Logged through the voice/text log.', lat: ev.lat, lon: ev.lon, place: ev.place || null,
      occurred_at: ev.time, visibility: 'shared', vessels: [],
    });
    await refreshAll(app);
    app.toast(`Field report also filed as incident ${inc.id}.`);
  } catch (e) { app.toast(`Field report saved locally; incident register said: ${e.message}`); }
}

// ------------------------------------------------------------------ map search, filter, zoom, pan
function redrawMap(app) {
  const s = app.state;
  if (s.ui.view === 'track') { redrawTrack(app, { list: true }); return; }
  const wrap = document.querySelector('.map-wrap[data-zoomable]');
  if (!wrap) return;
  wrap.innerHTML = renderMap(s, { selected: s.ui.entity, flash: s.ui.flash, layers: s.ui.layers, filter: s.ui.mapf, vb: s.ui.vb });
  const old = document.querySelector('.search-hits');
  const q = s.ui.mapf.q.trim().toLowerCase();
  const hits = q ? (s.server?.vessels || []).filter((v) => `${v.name} ${v.id} ${v.imo || ''} ${v.call_sign || ''}`.toLowerCase().includes(q)).slice(0, 6) : [];
  const html = q ? `<div class="search-hits">${hits.map((v) => `<button class="chip" data-entity="${esc(v.id)}">${esc(v.name)} · ${esc(v.id)} <span class="risk risk-${v.level}">${esc(v.level)}</span></button>`).join('') || '<span class="muted small">No vessel matches.</span>'}</div>` : '';
  if (old) old.outerHTML = html || '<span hidden class="search-hits"></span>';
  else document.querySelector('.map-tools')?.insertAdjacentHTML('afterend', html);
}

function mapFilterFrom(app, form) {
  const fd = new FormData(form);
  app.state.ui.mapf = { q: fd.get('q') || '', type: fd.get('type') || '', risk: fd.get('risk') || '' };
  redrawMap(app);
}

let typing = null;
export function handleInput(app, el) {
  if (el.name === 'track-t') return trackControl(app, el);
  const form = el.closest('[data-form="map-filter"]');
  if (!form || el.name !== 'q') return false;
  clearTimeout(typing);
  typing = setTimeout(() => mapFilterFrom(app, form), 120);
  return true;
}

function svgEl() { return document.querySelector('.map-wrap[data-zoomable] svg.sea-map'); }
function fullBox(svg) { return svg.dataset.full.split(' ').map(Number); }
function setBox(app, svg, box) {
  const full = fullBox(svg);
  const w = Math.min(full[2], Math.max(full[2] / 8, box[2]));
  const h = w * (full[3] / full[2]);
  const x = Math.min(full[2] - w, Math.max(0, box[0]));
  const y = Math.min(full[3] - h, Math.max(0, box[1]));
  const vb = [x, y, w, h].map((n) => Number(n.toFixed(1)));
  svg.setAttribute('viewBox', vb.join(' '));
  app.state.ui.vb = vb[2] >= full[2] ? null : vb;
  // redraw shortly after the zoom settles so symbols and labels get their on-screen size back
  clearTimeout(setBox.t);
  setBox.t = setTimeout(() => { if (!dragging) redrawMap(app); }, 160);
}
let dragging = false;

function zoomAt(app, svg, factor, cx = 0.5, cy = 0.5) {
  const [x, y, w, h] = svg.getAttribute('viewBox').split(' ').map(Number);
  const nw = w * factor;
  const nh = h * factor;
  setBox(app, svg, [x + (w - nw) * cx, y + (h - nh) * cy, nw, nh]);
}

function zoomButton(app, z) {
  const svg = svgEl();
  if (!svg) return;
  if (z === 'reset') setBox(app, svg, fullBox(svg));
  else zoomAt(app, svg, z === 'in' ? 0.7 : 1 / 0.7);
}

export function attachMapGestures(app) {
  let drag = null;
  app.dragged = false;
  document.addEventListener('wheel', (ev) => {
    const svg = ev.target.closest?.('.map-wrap[data-zoomable] svg.sea-map');
    if (!svg) return;
    ev.preventDefault();
    const r = svg.getBoundingClientRect();
    zoomAt(app, svg, ev.deltaY < 0 ? 0.85 : 1 / 0.85, (ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height);
  }, { passive: false });
  document.addEventListener('pointerdown', (ev) => {
    const svg = ev.target.closest?.('.map-wrap[data-zoomable] svg.sea-map');
    if (!svg || ev.button !== 0) return;
    dragging = true;
    drag = { svg, x: ev.clientX, y: ev.clientY, box: svg.getAttribute('viewBox').split(' ').map(Number), moved: false };
  });
  document.addEventListener('pointermove', (ev) => {
    if (!drag) return;
    const dx = ev.clientX - drag.x;
    const dy = ev.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    drag.moved = true;
    const r = drag.svg.getBoundingClientRect();
    const [x, y, w, h] = drag.box;
    setBox(app, drag.svg, [x - (dx * w) / r.width, y - (dy * h) / r.height, w, h]);
  });
  document.addEventListener('pointerup', () => {
    dragging = false;
    if (drag?.moved) { redrawMap(app); app.dragged = true; setTimeout(() => { app.dragged = false; }, 0); }
    drag = null;
  });
}

// ------------------------------------------------------------------ realtime
export function onServerEvent(app, ev) {
  const s = app.state;
  if (ev.type === 'connected' || ev.type === 'disconnected') {
    s.ui.live = ev.type === 'connected';
    const el = document.getElementById('live');
    if (el) { el.textContent = s.ui.live ? '● Live' : '○ Offline'; el.className = `live ${s.ui.live ? 'is-on' : ''}`; }
    return;
  }
  if (['hello', 'pong'].includes(ev.type)) return;
  const text = {
    'alert.created': `New AI alert ${ev.id}`, 'alert.updated': ev.id ? `${ev.id} → ${ev.status || 'updated'}${ev.by ? ` by ${ev.by}` : ''}` : 'Alerts re-scored',
    'incident.created': `New incident ${ev.id}${ev.by ? ` from ${ev.by}` : ''}`, 'incident.updated': `${ev.id} updated`,
    'share.received': `${ev.from} shared ${ev.target} with your agency`,
    'intake.received': `${ev.by} submitted ${ev.accepted} ${String(ev.kind).replace('_', ' ')}`, 'comment.created': null, 'data.updated': 'New AIS data received',
  }[ev.type];
  clearTimeout(onServerEvent.t);
  onServerEvent.t = setTimeout(() => {
    refreshAll(app).then(() => { if (text) app.toast(`Live: ${text}`); }).catch(() => {});
  }, 250);
}

import { fmtLongDate, fmtTime, esc } from './util.js';
import {
  sidebar, homeView, weatherView, activitiesView, sarView, lastView, recsView, fleetView, entityDrawer,
} from './views.js';
import { incidentsView, intelView, analyticsView, agenciesView, impactView } from './intel-views.js';
import { intakeView } from './intake-views.js';
import { trackView } from './track-view.js';
import { createCapture } from './capture.js';
import { nowIso, openAlerts } from './model.js';
import { todaysActions } from './rules.js';
import { api, backendAvailable } from './api.js';
import { showLogin } from './auth.js';
import {
  loadBase, loadView, loadDetail, handleAct, handleChange, handleSubmit, handleInput, attachMapGestures, onServerEvent,
  mirrorFieldReport, closePanel,
} from './ops.js';
import { addDays } from './util.js';

const STORE = 'gulf-watch-demo-v1';
const VIEWS = {
  home: homeView, track: trackView, weather: weatherView, activities: activitiesView, sar: sarView,
  last: lastView, recs: recsView, fleet: fleetView,
  // need the GulfEye backend
  incidents: incidentsView, intel: intelView, analytics: analyticsView, agencies: agenciesView, impact: impactView, intake: intakeView,
};
const SERVER_VIEWS = new Set(['incidents', 'intel', 'analytics', 'agencies', 'impact', 'intake']);

const app = {
  state: null,
  commit,
  render: () => render(),
  openEntity: (id) => openEntity(id),
  toast: (m) => toast(m),
  backend: false,
  socket: null,
};

async function loadJson(name) {
  const res = await fetch(`data/${name}.json`, { cache: 'no-store', headers: app.backend ? api.headers() : {} });
  if (!res.ok) throw new Error(`data/${name}.json: ${res.status}. Run scripts/build_data.py first.`);
  return res.json();
}

function restore(state) {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || 'null');
    if (saved) {
      state.captured = saved.captured || [];
      state.assetAdjust = saved.assetAdjust || {};
    }
  } catch { /* storage unavailable: start from the seed data */ }
}

function persist() {
  try {
    localStorage.setItem(STORE, JSON.stringify({ captured: app.state.captured, assetAdjust: app.state.assetAdjust }));
  } catch { /* the demo still works for this session */ }
}

function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.add('is-on');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('is-on'), 3800);
}

let previousTitles = null;

function render() {
  const s = app.state;
  if (!s) return;
  if (SERVER_VIEWS.has(s.ui.view) && !s.server) s.ui.view = 'home';
  s.radarHigh = openAlerts(s).filter((a) => a.risk === 'high' || a.risk === 'critical').length;
  // keep the cursor in a text field that is being re-rendered (map search, filters)
  const active = document.activeElement;
  const keep = active && active.name && document.getElementById('main').contains(active)
    ? { form: active.form?.dataset.form, name: active.name, pos: active.selectionStart } : null;
  document.getElementById('sidebar').innerHTML = sidebar(s);
  const main = document.getElementById('main');
  main.innerHTML = VIEWS[s.ui.view](s);
  const drawer = document.getElementById('drawer');
  if (s.ui.entity) {
    drawer.innerHTML = entityDrawer(s);
    drawer.classList.add('is-open');
  } else {
    drawer.classList.remove('is-open');
  }
  document.getElementById('log-count').textContent = s.captured.length ? `${s.captured.length} logged` : '';
  document.getElementById('reset').hidden = !s.captured.length;
  if (keep?.form) {
    const el = main.querySelector(`[data-form="${keep.form}"] [name="${keep.name}"]`);
    if (el) { el.focus(); try { el.setSelectionRange(keep.pos, keep.pos); } catch { /* not a text input */ } }
  }
  // After a new record, mark the advice that changed.
  if (previousTitles) {
    document.querySelectorAll('.rec').forEach((el) => {
      if (!previousTitles.has(el.querySelector('h4').textContent)) el.classList.add('is-changed');
    });
  }
}

function currentTitles() {
  const holder = document.createElement('div');
  holder.innerHTML = VIEWS[app.state.ui.view](app.state);
  return new Set([...holder.querySelectorAll('.rec h4')].map((h) => h.textContent));
}

function commit({ events, adjust, flash, message }) {
  const s = app.state;
  previousTitles = currentTitles();
  s.captured.push(...events);
  for (const [id, delta] of Object.entries(adjust)) s.assetAdjust[id] = (s.assetAdjust[id] || 0) + delta;
  s.ui.flash = flash;
  persist();
  render();
  toast(message);
  setTimeout(() => { s.ui.flash = []; previousTitles = null; }, 4000);
  for (const e of events) if (e.type === 'report' && app.backend) mirrorFieldReport(app, e);
}

async function go(view) {
  app.state.ui.view = view;
  app.state.ui.event = null;
  previousTitles = null;
  render();
  window.scrollTo(0, 0);
  if (app.state.server && SERVER_VIEWS.has(view)) {
    try { await loadView(app, view); render(); } catch (e) { toast(`Could not load: ${e.message}`); }
  }
}

async function openEntity(id) {
  const s = app.state;
  s.ui.entity = id;
  render();
  if (s.server) {
    try { if (await loadDetail(app, id)) render(); } catch (e) { toast(`Could not load ${id}: ${e.message}`); }
  }
}

async function signOut() {
  try { await api.post('/api/auth/logout'); } catch { /* token may already be invalid */ }
  api.setToken(null);
  app.socket?.close();
  location.reload();
}

function topbarUser(me) {
  const el = document.getElementById('user');
  if (!me) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<span id="live" class="live">○ Connecting</span>
    <span class="user-chip" title="${esc(me.email)}"><b>${esc(me.name)}</b><span>${esc(me.agency)} · ${esc(me.role_label)}</span></span>
    <button class="btn ghost small-btn" data-action="logout">Sign out</button>`;
}

async function boot() {
  const [geo, ais, sar, assets, assumptions, weather] = await Promise.all(
    ['geo', 'ais', 'sar', 'assets', 'assumptions', 'weather'].map(loadJson),
  );
  const today = ais.meta.now.slice(0, 10);
  const state = {
    geo, ais, sar, assets, assumptions, weather, captured: [], assetAdjust: {}, server: null,
    ui: {
      view: 'home', entity: null, flash: [], logFilter: 'all', logDay: null, event: null,
      layers: { tracks: true, zones: true, footprint: true, assets: true, platforms: true, lanes: true, incidents: true, agency: true, fused: true },
      mapf: { q: '', type: '', risk: '' }, vb: null,
      af: { status: 'open', level: '', behaviour: '' },
      inf: { q: '', type: '', status: '', from: '', to: '' },
      range: { from: addDays(today, -365), to: today, preset: '365' },
      auditFilter: '', intakeKind: 'sightings', intakeFormat: 'csv', intakeResult: null, newKey: null,
    },
  };
  restore(state);
  app.state = state;
  if (app.backend) {
    await loadBase(app);
    topbarUser(state.server.me);
    app.socket = api.connect((ev) => onServerEvent(app, ev));
    document.querySelector('.farm-chip .tag').textContent = 'Simulated data';
  }
  document.getElementById('farm-line').textContent = `${ais.meta.area} · ${ais.vessels.length} vessels with AIS · radar pass ${fmtTime(sar.scene.time)} UTC`;
  document.getElementById('today').textContent = `${fmtLongDate(nowIso(state))} · ${fmtTime(nowIso(state))} UTC`;
  const capture = createCapture(app);

  document.addEventListener('click', (ev) => {
    if (app.dragged) return;            // end of a map pan, not a click
    const el = ev.target.closest('[data-view],[data-entity],[data-action],[data-act],[data-filter],[data-day],[data-event],[data-layer],[data-log]');
    if (!el || el.closest('#capture')) return;
    const s = app.state;
    if (el.closest('#panel') && ev.target === document.getElementById('panel')) { closePanel(); return; }
    if (el.dataset.act) { ev.preventDefault(); handleAct(app, el); return; }
    if (el.dataset.read) api.post(`/api/shares/${el.dataset.read}/read`).catch(() => {});
    if (el.dataset.log) capture.open(el.dataset.log);
    else if (el.dataset.action === 'log') capture.open();
    else if (el.dataset.action === 'logout') signOut();
    else if (el.dataset.action === 'brief') {
      const top = todaysActions(s).slice(0, 3).map((r, i) => `${i + 1}. ${r.title}. ${r.lines.find((l) => l.startsWith('Recommend')) || r.lines[0]}`).join(' ');
      capture.speak(`Situation briefing. ${top}`);
      toast('Reading the top actions aloud.');
    } else if (el.dataset.action === 'close-drawer') { s.ui.entity = null; render(); }
    else if (el.dataset.action === 'reset') {
      if (window.confirm('Remove the reports logged in this demo and restore the starting fuel?')) {
        s.captured = [];
        s.assetAdjust = {};
        persist();
        render();
        toast('Demo reset to the starting picture.');
      }
    } else if (el.dataset.view) go(el.dataset.view);
    else if (el.dataset.entity) { closePanel(); openEntity(el.dataset.entity); }
    else if (el.dataset.layer) { s.ui.layers[el.dataset.layer] = !s.ui.layers[el.dataset.layer]; render(); }
    else if (el.dataset.filter) { s.ui.logFilter = el.dataset.filter; render(); }
    else if (el.dataset.day) { s.ui.logDay = el.dataset.day; render(); }
    else if (el.dataset.event) { s.ui.event = s.ui.event === el.dataset.event ? null : el.dataset.event; render(); }
  });

  document.addEventListener('submit', (ev) => {
    const form = ev.target.closest('[data-form]');
    if (!form || form.closest('#capture')) return;
    ev.preventDefault();
    handleSubmit(app, form);
  });
  document.addEventListener('change', (ev) => {
    if (ev.target.closest('#capture')) return;
    handleChange(app, ev.target);
  });
  document.addEventListener('input', (ev) => { if (!ev.target.closest('#capture')) handleInput(app, ev.target); });
  attachMapGestures(app);

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !document.getElementById('panel').hidden) { closePanel(); return; }
    if (ev.key === 'Escape' && !capture.isOpen() && app.state.ui.entity) { app.state.ui.entity = null; render(); }
    if (ev.key === 'Enter' && ev.target.matches?.('.m-vessel,.m-alert,.m-asset,.m-inc,.m-obs,.m-fused,.net-node')) openEntity(ev.target.dataset.entity);
  });
  window.addEventListener('gulfeye:signed-out', () => {
    if (app.signingOut) return;
    app.signingOut = true;
    api.setToken(null);
    app.socket?.close();
    toast('Your session ended. Please sign in again.');
    setTimeout(() => location.reload(), 1200);
  });

  render();
}

async function start() {
  app.backend = await backendAvailable();
  if (!app.backend) {
    document.getElementById('mode-note').textContent = 'Static mode: start the GulfEye server (python -m backend) for sign-in, AI alerts, incidents and sharing.';
    return boot();
  }
  if (api.token) {
    try { await api.get('/api/me'); return boot(); } catch { api.setToken(null); }
  }
  return new Promise((resolve, reject) => showLogin(() => boot().then(resolve, reject)));
}

start().catch((err) => {
  document.getElementById('main').innerHTML = `<div class="card error"><h2>Could not load the maritime data</h2><p>${esc(String(err.message))}</p></div>`;
  console.error(err);
});

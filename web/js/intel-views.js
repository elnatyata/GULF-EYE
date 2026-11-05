// GulfEye screens that use the backend: dashboard KPIs, AI alerts, incidents, intelligence, analytics, agencies, impact.
// Pure functions of the state, like views.js. Data is fetched by main.js/ops.js into s.server.
import { RISK, esc, fmt, fmtCoord, fmtDate, fmtDateTime, fmtTime, typeName, typeColor, badges, monthName } from './util.js';
import { barChart, hbarChart } from './charts.js';
import { projector } from './map.js';
import { agencyRecordsBlock } from './intake-views.js';
import { mergeSection, incidentMergeBlock, consolidatedBlock } from './merge-views.js';

export const can = (s, perm) => Boolean(s.server?.me?.permissions?.includes(perm));
export const riskChip = (r) => (r && RISK[r] ? `<span class="risk risk-${r}">${RISK[r].label}</span>` : '');
const STATUS_LABEL = {
  new: 'New', acknowledged: 'Acknowledged', investigating: 'Investigating', closed: 'Closed',
  reported: 'Reported', verified: 'Verified', under_investigation: 'Under investigation',
};
export const statusChip = (st) => `<span class="st st-${esc(st)}">${esc(STATUS_LABEL[st] || st)}</span>`;
const ALERT_NEXT = { new: ['acknowledged', 'investigating', 'closed'], acknowledged: ['investigating', 'closed'], investigating: ['closed'], closed: ['investigating'] };
const INC_NEXT = { reported: ['verified', 'closed'], verified: ['under_investigation', 'closed'], under_investigation: ['closed'], closed: ['under_investigation'] };
const BEHAVIOUR = {
  ais_gap: 'AIS disruption', high_risk_area: 'High-risk zone', loitering: 'Loitering', unexpected_stop: 'Unexpected stop',
  speed_change: 'Speed change', erratic_course: 'Erratic course', route_deviation: 'Unusual route', rendezvous: 'Rendezvous',
  dark_alongside: 'Dark vessel alongside', prior_incident: 'Prior incident', watchlist: 'Watchlist',
  identity_mismatch: 'Identity mismatch', flag_hopping: 'Flag changes', owner_network: 'Owner network', ml_outlier: 'ML outlier',
  agency_watchlist: 'Agency watchlist', agency_report: 'Agency report', agency_contact: 'Agency radar contact',
};
const GROUP = { ais: 'AIS signal', behaviour: 'Movement behaviour', location: 'Location', record: 'Records and history', agency: 'Agency reports', model: 'Statistical outlier (ML)' };
const agencyCodes = (s) => s.server?.agencyCodes || ['GULFEYE', 'NIMASA', 'NN', 'MPOL', 'NPA'];
export const behaviourLabel = (c) => BEHAVIOUR[c] || c;
const sim = (on = true) => (on ? badges(['simulated']) : '');
const dt = (iso) => (iso ? `${fmtDate(iso, true)} ${fmtTime(iso)}` : '–');
const levelOrder = { critical: 0, high: 1, medium: 2, low: 3 };

function loading(text = 'Loading…') { return `<div class="card"><p class="muted">${esc(text)}</p></div>`; }

// ------------------------------------------------------------------ dashboard
export function kpiStrip(s) {
  const sm = s.server?.summary;
  if (!sm) return '';
  const radarHigh = s.radarHigh ?? 0;
  const trend = sm.incidents_30d - sm.incidents_prev_30d;
  const items = [
    ['Vessels monitored', sm.vessels_monitored, 'AIS baseline + radar pass', 'home', ''],
    ['Suspicious vessels', sm.suspicious_vessels, `${sm.risk_levels.critical || 0} critical · ${sm.risk_levels.high || 0} high · ${sm.risk_levels.medium || 0} medium`, 'intel', sm.suspicious_vessels ? 'warn' : ''],
    ['High-risk alerts', sm.high_risk_alerts + radarHigh, `${sm.high_risk_alerts} AI · ${radarHigh} radar · ${sm.alerts_unacknowledged} unacknowledged`, 'activities', sm.high_risk_alerts + radarHigh ? 'crit' : ''],
    ['Active incidents', sm.active_incidents, 'reported, verified or under investigation', 'incidents', ''],
    ['Incidents, last 30 days', sm.incidents_30d, `${trend > 0 ? '▲' : trend < 0 ? '▼' : '='} ${Math.abs(trend)} vs previous 30 days`, 'analytics', trend > 0 ? 'warn' : ''],
  ];
  return `<section class="kpi-strip" aria-label="Maritime security statistics">${items.map(([l, v, sub, view, tone]) => `
    <button class="kpi-card ${tone}" data-view="${view}"><span class="kpi-l">${esc(l)}</span><span class="kpi-v">${esc(v)}</span><span class="kpi-sub">${esc(sub)}</span></button>`).join('')}
    <div class="kpi-note">${sim()} As of ${esc(dt(sm.as_of))} UTC (demo clock). ${sm.engine ? `Engine run ${esc(dt(sm.engine.at))}.` : ''}</div>
  </section>`;
}

export function homeExtras(s) {
  const sv = s.server;
  if (!sv?.summary) return '';
  const top = (sv.alerts || []).filter((a) => a.status !== 'closed').sort((a, b) => levelOrder[a.level] - levelOrder[b.level] || b.score - a.score).slice(0, 5);
  const recentInc = (sv.incidentsRecent || []).slice(0, 5);
  return `<section class="grid-2">
    <div class="card"><div class="card-head"><div><h3>Priority alerts · AI threat detection</h3><p class="sub">Vessel behaviour scored by rules + Isolation Forest. For investigation, not proof.</p></div>
      <button class="link-btn" data-view="activities">All alerts →</button></div>
      <div class="alert-list">${top.map((a) => alertRow(a)).join('') || '<p class="empty">No open AI alerts.</p>'}</div></div>
    <div class="card"><div class="card-head"><div><h3>Recent maritime activity</h3><p class="sub">Incident reports and actions by all agencies</p></div>
      <button class="link-btn" data-view="incidents">Incidents →</button></div>
      <div class="activity">${recentInc.map((i) => `<button class="act-row" data-entity="${esc(i.id)}"><span class="act-ico">${incidentIcon(i.type)}</span>
        <span class="act-body"><b>${esc(i.title)}</b><span class="muted small">${esc(dt(i.occurred_at))} · ${esc(i.place || fmtCoord(i.lat, i.lon))} · ${esc(i.agency)}</span></span>${statusChip(i.status)}</button>`).join('')}
      ${sv.summary.recent.slice(0, 5).map((r) => `<div class="act-row is-log"><span class="act-ico">•</span><span class="act-body"><span>${esc(actionText(r))}</span><span class="muted small">${esc(dt(r.at))} · ${esc(r.who)}</span></span></div>`).join('')}</div></div>
  </section>`;
}

function actionText(r) {
  const map = { 'incident.create': 'Reported incident', 'incident.status': 'Changed incident status', 'alert.status': 'Updated alert',
    'engine.run': 'Threat-detection engine ran', 'share.create': 'Shared', 'ingest.ais': 'AIS positions received' };
  return `${map[r.action] || r.action}${r.target ? ` ${r.target}` : ''}`;
}

export function incidentIcon(type) {
  return { piracy_attack: '⚠️', armed_robbery: '🔓', boarding: '🪝', kidnapping: '🚨', suspicious_approach: '👁️', illegal_fishing: '🎣',
    oil_theft: '🛢️', sts_transfer: '⛴️', smuggling: '📦', pollution: '🌊', search_rescue: '🛟', other: '📌' }[type] || '📌';
}

function alertRow(a) {
  return `<button class="alert-row lv-${a.level}" data-entity="${esc(a.id)}">
    <span class="ar-id">${esc(a.id)}</span>${riskChip(a.level)}<span class="ar-score">${fmt(a.score, 0)}</span>
    <span class="ar-title">${esc(a.title)}</span>${statusChip(a.status)}
    <span class="ar-tags">${a.behaviours.slice(0, 4).map((b) => `<i>${esc(behaviourLabel(b))}</i>`).join('')}</span></button>`;
}

// ------------------------------------------------------------------ AI alerts (inside the existing Alerts & log screen)
export function behaviourAlertsSection(s) {
  const sv = s.server;
  if (!sv) return '';
  const f = s.ui.af;
  const all = sv.alerts || [];
  const list = all.filter((a) => (f.status === 'open' ? a.status !== 'closed' : !f.status || a.status === f.status)
    && (!f.level || a.level === f.level) && (!f.behaviour || a.behaviours.includes(f.behaviour)));
  const behaviours = [...new Set(all.flatMap((a) => a.behaviours))];
  const chip = (key, value, label) => `<button class="chip chip-sm${f[key] === value ? ' is-on' : ''}" data-act="af" data-k="${key}" data-v="${value}">${esc(label)}</button>`;
  return `<section class="card">
    <div class="card-head"><div><h3>Early-warning alerts · AI threat detection ${sim()}</h3>
      <p class="sub">Raised when several behaviour indicators combine. Acknowledge, investigate and close with an outcome; every step is logged.</p></div>
      ${can(s, 'engine:run') ? '<button class="btn small-btn" data-act="run-engine">↻ Re-run engine</button>' : ''}</div>
    <div class="filter-row"><span class="muted small">Status</span>${chip('status', 'open', 'Open')}${chip('status', 'new', 'New')}${chip('status', 'acknowledged', 'Acknowledged')}${chip('status', 'investigating', 'Investigating')}${chip('status', 'closed', 'Closed')}${chip('status', '', 'All')}</div>
    <div class="filter-row"><span class="muted small">Risk</span>${chip('level', '', 'Any')}${['critical', 'high', 'medium'].map((l) => chip('level', l, RISK[l].label)).join('')}
      <span class="muted small">Behaviour</span><select data-act-change="af-behaviour"><option value="">Any</option>${behaviours.map((b) => `<option value="${b}" ${f.behaviour === b ? 'selected' : ''}>${esc(behaviourLabel(b))}</option>`).join('')}</select></div>
    <div class="table-scroll"><table class="data">
      <thead><tr><th>Alert</th><th>Vessel</th><th>Risk</th><th>Behaviour</th><th>Position</th><th>Observed</th><th>Status</th></tr></thead>
      <tbody>${list.map((a) => `<tr class="click" data-entity="${esc(a.id)}">
        <td><b>${esc(a.id)}</b></td><td>${esc(a.vessel?.name || '–')}<div class="muted small">${esc(a.vessel_id || '')} · ${esc(a.vessel?.flag || '')}</div></td>
        <td>${riskChip(a.level)} <span class="muted small">${fmt(a.score, 0)}/100</span></td>
        <td class="small">${a.behaviours.slice(0, 3).map(behaviourLabel).map(esc).join(', ')}</td>
        <td class="small">${a.lat != null ? esc(fmtCoord(a.lat, a.lon)) : '–'}${a.zone ? `<div class="muted">${esc(a.zone)}</div>` : ''}</td>
        <td class="small">${esc(dt(a.observed_at))}</td><td>${statusChip(a.status)}</td></tr>`).join('')
        || '<tr><td colspan="7" class="empty">No alerts match these filters.</td></tr>'}</tbody></table></div>
    <p class="muted small">${esc(sv.disclaimer || '')}</p>
  </section>`;
}

// ------------------------------------------------------------------ drawers
function commentsBlock(s, type, id, comments = []) {
  return `<h4>Notes between agencies</h4>
    <div class="comments">${comments.map((c) => `<div class="comment"><b>${esc(c.who)}</b> <span class="muted small">${esc(dt(c.at))}</span><p>${esc(c.body)}</p></div>`).join('') || '<p class="muted small">No notes yet.</p>'}</div>
    ${can(s, 'comment') ? `<form class="inline-form" data-form="comment" data-type="${type}" data-id="${esc(id)}"><textarea name="body" rows="2" maxlength="2000" required placeholder="Add a note visible to every agency that can see this item"></textarea><button class="btn small-btn">Add note</button></form>` : ''}`;
}

function shareBlock(s, type, id, shares = []) {
  const me = s.server.me.agency;
  return `<h4>Shared with</h4>
    ${shares.length ? `<ul class="watch">${shares.map((x) => `<li>${esc(x.from)} → <b>${esc(x.to)}</b> · ${esc(dt(x.at))}${x.message ? `: “${esc(x.message)}”` : ''}</li>`).join('')}</ul>` : '<p class="muted small">Not explicitly shared yet.</p>'}
    ${can(s, 'share') ? `<form class="inline-form" data-form="share" data-type="${type}" data-id="${esc(id)}">
      <div class="checks">${agencyCodes(s).filter((a) => a !== me).map((a) => `<label><input type="checkbox" name="to" value="${a}"> ${a}</label>`).join('')}</div>
      <input name="message" maxlength="1000" placeholder="Message (optional)"><button class="btn small-btn">Share</button></form>` : ''}`;
}

function drawerHead(title, sub) {
  return `<div class="drawer-head"><div><div class="drawer-title">${title}</div><div class="sub">${sub}</div></div>
    <button class="icon-btn" data-action="close-drawer" aria-label="Close">✕</button></div>`;
}

export function serverAlertDrawer(s, id) {
  const a = s.server.detail[`a:${id}`];
  if (!a) return drawerHead(esc(id), 'Loading…') + '<div class="drawer-body"><p class="muted">Loading the alert…</p></div>';
  const next = ALERT_NEXT[a.status] || [];
  return `${drawerHead(`${esc(a.id)} ${riskChip(a.level)}`, `${esc(a.vessel?.name || '')} · ${a.lat != null ? esc(fmtCoord(a.lat, a.lon)) : ''} ${a.zone ? `· ${esc(a.zone)}` : ''}`)}
  <div class="drawer-body">
    <div class="status-box status-${a.status === 'closed' ? 'resolved' : a.status === 'new' ? 'new' : 'assigned'}"><b>${statusChip(a.status)} ${a.outcome ? `· ${esc(a.outcome.replace('_', ' '))}` : ''}</b>
      <span>${esc(a.title)}</span><span class="muted small">Risk score ${fmt(a.score, 0)}/100 · observed ${esc(dt(a.observed_at))} UTC · raised ${esc(dt(a.created_at))}</span></div>
    <div class="disclaimer">${esc(a.disclaimer)}</div>
    <div class="rec-actions">${a.vessel_id ? `<button class="chip" data-entity="${esc(a.vessel_id)}">Open vessel profile</button>` : ''}
      ${can(s, 'incidents:create') ? `<button class="chip" data-act="incident-new" data-vessel="${esc(a.vessel_id || '')}" data-lat="${a.lat}" data-lon="${a.lon}">Report incident…</button>` : ''}</div>
    ${can(s, 'alerts:update') ? `<h4>Update status</h4><form class="inline-form status-form" data-form="alert-status" data-id="${esc(a.id)}">
      <select name="status">${next.map((n) => `<option value="${n}">${esc(STATUS_LABEL[n])}</option>`).join('')}</select>
      <select name="outcome"><option value="">Outcome (when closing)</option><option value="confirmed">Threat confirmed</option><option value="false_alarm">False alarm</option><option value="no_action">No action needed</option></select>
      <textarea name="note" rows="2" maxlength="1000" placeholder="Note (required to close)"></textarea><button class="btn primary small-btn">Save</button></form>` : ''}
    <h4>Detected behaviour and evidence ${badges(['ais', 'sar', 'simulated'])}</h4>
    ${a.evidence.map((e) => `<div class="evidence"><div class="ev-head"><b>${esc(e.indicator)}</b><span class="pts">+${fmt(e.points, 0)}</span></div>
      <ul>${e.items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul><p class="muted small">${esc(e.reason)}</p></div>`).join('')}
    <h4>Recommended actions</h4><ol class="steps">${a.actions.map((x) => `<li>${esc(x)}</li>`).join('')}</ol>
    <h4>History</h4><div class="mini-tl">${a.history.map((h) => `<div class="mini-tl-row"><span class="mini-date">${esc(fmtTime(h.at))}</span><span>${esc(h.from_status ? `${STATUS_LABEL[h.from_status]} → ${STATUS_LABEL[h.to_status]}` : STATUS_LABEL[h.to_status])}</span><span class="muted">${esc(h.who)}${h.note ? `: ${esc(h.note)}` : ''}</span></div>`).join('')}</div>
    ${shareBlock(s, 'alert', a.id, a.shares)}
    ${commentsBlock(s, 'alert', a.id, a.comments)}
  </div>`;
}

export function incidentDrawer(s, id) {
  const i = s.server.detail[`i:${id}`];
  if (!i) return drawerHead(esc(id), 'Loading…') + '<div class="drawer-body"><p class="muted">Loading the incident…</p></div>';
  const next = INC_NEXT[i.status] || [];
  return `${drawerHead(`${incidentIcon(i.type)} ${esc(i.id)}`, `${esc(i.type_label)} · ${esc(fmtCoord(i.lat, i.lon))}${i.zone ? ` · ${esc(i.zone)}` : ''}`)}
  <div class="drawer-body">
    <div class="status-box status-${i.status === 'closed' ? 'resolved' : i.status === 'reported' ? 'new' : 'assigned'}"><b>${statusChip(i.status)} ${i.visibility === 'agency' ? '<span class="tag">Restricted to ' + esc(i.agency) + '</span>' : '<span class="tag">Shared with all agencies</span>'}</b>
      <span><b>${esc(i.title)}</b></span><span class="muted small">${esc(dt(i.occurred_at))} UTC · ${esc(i.place || '')} · reported by ${esc(i.reporter)}</span></div>
    ${i.synthetic ? `<div class="banner">${i.source?.startsWith('agency_intake') ? `Simulated ${esc(i.agency)} submission received through the agency intake (demo).` : 'Simulated record from the demo incident history.'} ${badges(['simulated'])}</div>` : ''}
    <p>${esc(i.description)}</p>
    ${incidentMergeBlock(s, i)}
    <h4>Associated vessels</h4>
    ${i.vessels.length ? `<div class="chips">${i.vessels.map((v) => `<button class="chip" data-entity="${esc(v.vessel_id)}">${esc(v.name || v.vessel_id)} · ${esc(v.role)}</button>`).join('')}</div>` : '<p class="muted small">None named.</p>'}
    ${can(s, 'incidents:update') ? `<h4>Update status</h4><form class="inline-form status-form" data-form="incident-status" data-id="${esc(i.id)}">
      <select name="status">${next.map((n) => `<option value="${n}">${esc(STATUS_LABEL[n])}</option>`).join('')}</select>
      <textarea name="note" rows="2" maxlength="1000" placeholder="Note"></textarea><button class="btn primary small-btn">Save</button></form>` : ''}
    <h4>Evidence files</h4>
    <div class="files">${i.attachments.map((f) => `<button class="chip" data-act="download" data-id="${esc(f.id)}" data-name="${esc(f.filename)}">📎 ${esc(f.filename)} <span class="muted">${fmt(f.size / 1024, 0)} kB</span></button>`).join('') || '<p class="muted small">No files.</p>'}</div>
    ${can(s, 'incidents:create') && (i.agency === s.server.me.agency || s.server.me.role === 'admin') ? `<form class="inline-form" data-form="attach" data-id="${esc(i.id)}"><input type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,.txt,.csv" required><button class="btn small-btn">Upload</button><span class="muted small">PDF, image, text or CSV · max 5 MB</span></form>` : ''}
    <h4>Correlated intelligence</h4>
    <ul class="watch">${i.related_alerts.map((a) => `<li><button class="link-inline" data-entity="${esc(a.id)}">${esc(a.id)}</button> ${riskChip(a.level)} ${esc(a.title)} · ${fmt(a.distance_km)} km away</li>`).join('')}
      ${i.related.map((r) => `<li><button class="link-inline" data-entity="${esc(r.id)}">${esc(r.id)}</button> ${esc(r.title)} · ${esc(fmtDate(r.occurred_at))} · ${fmt(r.distance_km)} km away</li>`).join('') || (i.related_alerts.length ? '' : '<li class="muted">No other incident or alert within 25 km and 30 days.</li>')}</ul>
    <h4>History</h4><div class="mini-tl">${i.history.map((h) => `<div class="mini-tl-row"><span class="mini-date">${esc(fmtDate(h.at))}</span><span>${esc(STATUS_LABEL[h.to_status] || h.to_status)}</span><span class="muted">${esc(h.who)}${h.note ? `: ${esc(h.note)}` : ''}</span></div>`).join('')}</div>
    ${shareBlock(s, 'incident', i.id, i.shares)}
    ${commentsBlock(s, 'incident', i.id, i.comments)}
  </div>`;
}

export function vesselIntel(s, vid) {
  if (!s.server) return '';
  const p = s.server.detail[`v:${vid}`];
  if (!p) return '<h4>Intelligence</h4><p class="muted small">Loading registry, risk assessment and incidents…</p>';
  const r = p.risk;
  const reg = p.registry;
  const ind = r ? [...r.indicators].sort((a, b) => b.points - a.points) : [];
  return `
    <div class="intel-head">
      <div class="score-ring lv-${r?.level || 'low'}"><b>${fmt(r?.score ?? 0, 0)}</b><span>/100</span></div>
      <div><div>${riskChip(r?.level || 'low')} <b>AI risk assessment</b></div>
        <div class="muted small">${esc(r?.model_version || '')} · ${esc(dt(r?.computed_at))}</div>
        <div class="muted small">IMO ${esc(p.vessel.imo || 'not available')} · MMSI ${esc(p.vessel.mmsi)} · call sign ${esc(p.vessel.call_sign || '–')}</div></div>
    </div>
    <div class="disclaimer">${esc(p.disclaimer)}</div>
    <div class="rec-actions">${can(s, 'incidents:create') ? `<button class="chip" data-act="incident-new" data-vessel="${esc(vid)}">Report incident…</button>` : ''}
      ${p.alerts.map((a) => `<button class="chip" data-entity="${esc(a.id)}">${esc(a.id)} ${esc(STATUS_LABEL[a.status])}</button>`).join('')}</div>
    <h4>Why this score</h4>
    ${r && r.reasons.length ? `<ul class="watch">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="muted small">No indicator fired: behaviour consistent with its vessel type.</p>'}
    ${ind.map((i) => `<div class="evidence"><div class="ev-head"><b>${esc(i.title)}</b><span class="muted small">${esc(GROUP[i.group])}</span><span class="pts">+${fmt(i.points, 0)}</span></div>
      <ul>${i.evidence.map((x) => `<li>${esc(x)}</li>`).join('')}</ul><p class="muted small">${esc(i.reason)}</p></div>`).join('')}
    ${r?.steps.length ? `<h4>Recommended investigation steps</h4><ol class="steps">${r.steps.map((x) => `<li>${esc(x)}</li>`).join('')}</ol>` : ''}
    <h4>Registration ${badges(['simulated'])}</h4>
    ${reg ? `<dl class="soil"><dt>Registered name</dt><dd>${esc(reg.name)}${reg.former_names?.length ? ` <span class="muted">(ex ${esc(reg.former_names.join(', '))})</span>` : ''}</dd>
      <dt>Flag</dt><dd>${esc(reg.flag)}${reg.flag_history?.length > 1 ? ` <span class="muted">· ${esc(reg.flag_history.map((h) => `${h.flag} ${h.from}`).join(' → '))}</span>` : ''}</dd>
      <dt>Owner / manager</dt><dd>${esc(reg.owner || '–')}${reg.manager && reg.manager !== reg.owner ? ` / ${esc(reg.manager)}` : ''}</dd>
      <dt>Built · tonnage</dt><dd>${esc(reg.built || '–')} · ${reg.gross_tonnage ? `${fmt(reg.gross_tonnage, 0)} GT` : '–'}</dd>
      <dt>Class</dt><dd>${esc(reg.class_society || 'none recorded')}</dd>
      <dt>Last inspection</dt><dd>${reg.last_psc ? `${esc(reg.last_psc.date)} · ${esc(reg.last_psc.port)} · ${reg.last_psc.deficiencies} deficiencies` : '–'}</dd>
      ${reg.watchlist ? `<dt>Watchlist</dt><dd class="neg">${esc(reg.watchlist)}</dd>` : ''}${reg.notes ? `<dt>Notes</dt><dd>${esc(reg.notes)}</dd>` : ''}</dl>` : '<p class="muted small">No registration record.</p>'}
    ${consolidatedBlock(p)}
    ${p.identity_issues.length ? `<div class="warn">${p.identity_issues.map(esc).join(' ')}</div>` : ''}
    <h4>Movement history ${badges(['ais'])}</h4>
    <div class="table-scroll"><table class="data compact"><thead><tr><th>Time (UTC)</th><th>Position</th><th class="num">Speed</th><th class="num">Heading</th></tr></thead>
      <tbody>${p.history.slice(-8).reverse().map((h) => `<tr><td>${esc(dt(h.t))}</td><td>${esc(fmtCoord(h.lat, h.lon))}</td><td class="num">${fmt(h.sog)} kn</td><td class="num">${fmt(h.cog, 0)}°</td></tr>`).join('')}</tbody></table></div>
    <p class="muted small">${p.history.length} reports in the window. Last known: ${p.last ? `${esc(fmtCoord(p.last.lat, p.last.lon))}, ${fmt(p.last.sog)} kn, ${fmt(p.last.cog, 0)}°, ${p.last.age_min} min before the demo clock` : '–'}.</p>
    <h4>Associated incidents</h4>
    ${p.incidents.length ? `<ul class="watch">${p.incidents.map((i) => `<li><button class="link-inline" data-entity="${esc(i.id)}">${esc(i.id)}</button> <b>${esc(i.role)}</b> · ${esc(fmtDate(i.occurred_at, true))} · ${esc(i.title)} ${statusChip(i.status)}</li>`).join('')}</ul>` : '<p class="muted small">Not named in any incident report.</p>'}
    ${p.nearby_incidents.length ? `<h4>Incidents near its track (12 months)</h4><ul class="watch">${p.nearby_incidents.map((i) => `<li><button class="link-inline" data-entity="${esc(i.id)}">${esc(i.id)}</button> ${esc(i.type_label)} · ${esc(fmtDate(i.occurred_at, true))} · ${fmt(i.distance_km)} km from the track</li>`).join('')}</ul><p class="muted small">Proximity only: these do not involve the vessel.</p>` : ''}
    ${agencyRecordsBlock(p)}
    ${p.network.length ? `<h4>Same owner or manager</h4><ul class="watch">${p.network.map((n) => `<li><button class="link-inline" data-entity="${esc(n.vessel_id)}">${esc(n.name)}</button> · ${esc(n.link)}${n.adverse ? ' · <span class="neg">has adverse records</span>' : ''}</li>`).join('')}</ul>` : ''}
    ${shareBlock(s, 'vessel', vid, [])}
    ${commentsBlock(s, 'vessel', vid, p.comments)}`;
}

// ------------------------------------------------------------------ incidents screen + form
export function incidentsView(s) {
  const sv = s.server;
  const list = sv.incidents;
  const f = s.ui.inf;
  const types = sv.incidentTypes || {};
  return `
  <section class="view-head"><h2>Incidents</h2><p class="sub">Piracy, sea robbery, suspicious movements and other reports from every agency ${badges(['captured', 'simulated'])}</p></section>
  <section class="card">
    <form class="filter-form" data-form="incident-filter">
      <label>Search<input name="q" value="${esc(f.q)}" placeholder="id, title, place…"></label>
      <label>Type<select name="type"><option value="">All types</option>${Object.entries(types).map(([k, l]) => `<option value="${k}" ${f.type === k ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></label>
      <label>Status<select name="status">${[['', 'Any'], ['open', 'Open (not closed)'], ['reported', 'Reported'], ['verified', 'Verified'], ['under_investigation', 'Under investigation'], ['closed', 'Closed']].map(([v, l]) => `<option value="${v}" ${f.status === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label>From<input type="date" name="from" value="${esc(f.from)}"></label><label>To<input type="date" name="to" value="${esc(f.to)}"></label>
      <button class="btn small-btn">Apply</button>
      ${can(s, 'incidents:create') ? '<button type="button" class="btn primary small-btn" data-act="incident-new">+ Report incident</button>' : ''}
    </form>
    ${list ? `<p class="muted small">${list.total} incident${list.total === 1 ? '' : 's'} visible to ${esc(sv.me.agency)}. Restricted reports from other agencies are hidden unless shared with you.</p>
    <div class="table-scroll"><table class="data">
      <thead><tr><th>Incident</th><th>When (UTC)</th><th>Type</th><th>Where</th><th>Vessels</th><th>Agency</th><th>Status</th></tr></thead>
      <tbody>${list.incidents.map((i) => `<tr class="click" data-entity="${esc(i.id)}">
        <td><b>${esc(i.id)}</b><div class="small">${esc(i.title)}</div></td><td class="small">${esc(dt(i.occurred_at))}</td>
        <td class="small">${incidentIcon(i.type)} ${esc(i.type_label)}</td><td class="small">${esc(i.place || fmtCoord(i.lat, i.lon))}${i.zone ? `<div class="muted">${esc(i.zone)}</div>` : ''}</td>
        <td class="small">${i.vessels.map((v) => `${esc(v.name || v.vessel_id)} (${esc(v.role)})`).join(', ') || '–'}</td>
        <td class="small">${esc((i.agencies || [i.agency]).join(' + '))}${i.merged_reports?.length ? `<div class="muted">${i.merged_reports.length + 1} reports merged</div>` : ''}${i.visibility === 'agency' ? ' 🔒' : ''}</td><td>${statusChip(i.status)} ${i.synthetic ? '<span class="tag tag-synthetic">sim</span>' : ''}</td></tr>`).join('') || '<tr><td colspan="7" class="empty">No incidents match.</td></tr>'}</tbody></table></div>` : '<p class="muted">Loading…</p>'}
  </section>`;
}

export function incidentForm(s, preset = {}) {
  const types = s.server.incidentTypes || {};
  const vessels = [...(s.server.vessels || [])].sort((a, b) => a.name.localeCompare(b.name));
  const now = s.ais.meta.now;
  const vesselSel = (i) => `<div class="vrow"><select name="vessel${i}"><option value="">— vessel —</option>${vessels.map((v) => `<option value="${v.id}" ${preset.vessel === v.id && i === 0 ? 'selected' : ''}>${esc(v.name)} · ${esc(v.id)}</option>`).join('')}</select>
    <select name="role${i}">${['involved', 'suspect', 'victim', 'witness'].map((r) => `<option ${preset.vessel && i === 0 && r === 'involved' ? 'selected' : ''}>${r}</option>`).join('')}</select></div>`;
  const places = [...s.geo.ports.map((p) => [p.name, p.lat, p.lon]), ['Bonny fairway buoy', 3.9, 6.9], ['Bioko channel', 3.5, 8.9], ['Benin offshore', 5.0, 2.7]];
  return `<div class="modal-box wide" role="dialog" aria-modal="true" aria-label="Report a maritime incident">
    <div class="modal-head"><div><h2>Report a maritime incident</h2><p class="sub">Saved to the shared incident register, visible to agencies according to the sharing choice.</p></div>
      <button class="icon-btn" data-act="close-panel" aria-label="Close">✕</button></div>
    <form class="incident-form" data-form="incident-create">
      <div class="confirm-grid">
        <label>Type<select name="type" required>${Object.entries(types).map(([k, l]) => `<option value="${k}" ${preset.type === k ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></label>
        <label>Title<input name="title" required minlength="3" maxlength="140" value="${esc(preset.title || '')}" placeholder="Short summary"></label>
        <label>Date (UTC)<input type="date" name="date" required value="${now.slice(0, 10)}"></label>
        <label>Time (UTC)<input type="time" name="time" required value="${now.slice(11, 16)}"></label>
        <label>Known place<select name="preset"><option value="">— or enter a position —</option>${places.map(([n, la, lo]) => `<option value="${la},${lo}" data-name="${esc(n)}">${esc(n)}</option>`).join('')}</select></label>
        <label>Place description<input name="place" maxlength="120" placeholder="e.g. 12 nm SW of Bonny"></label>
        <label>Latitude (°N)<input name="lat" type="number" step="0.0001" min="-2" max="8" required value="${preset.lat ?? ''}"></label>
        <label>Longitude (°E)<input name="lon" type="number" step="0.0001" min="-5" max="12" required value="${preset.lon ?? ''}"></label>
      </div>
      <label class="full">Description<textarea name="description" required minlength="3" maxlength="4000" rows="4" placeholder="What happened, who reported it, persons and vessels involved, actions taken"></textarea></label>
      <div class="full"><div class="label">Associated vessels</div>${[0, 1, 2].map(vesselSel).join('')}</div>
      <div class="confirm-grid">
        <label>Sharing<select name="visibility"><option value="shared">Share with all agencies</option><option value="agency">Restricted to ${esc(s.server.me.agency)}</option></select></label>
        <label>Supporting files<input type="file" name="files" multiple accept=".pdf,.png,.jpg,.jpeg,.txt,.csv"><small class="muted">PDF, image, text, CSV · max 5 MB each</small></label>
      </div>
      <p class="form-error" role="alert"></p>
      <div class="confirm-actions"><button type="button" class="btn ghost" data-act="close-panel">Cancel</button><button class="btn primary">Submit report</button></div>
    </form></div>`;
}

// ------------------------------------------------------------------ intelligence
export function intelView(s) {
  const sv = s.server;
  const it = sv.intel;
  const src = sv.sources;
  if (!it || !src) return loading('Loading the consolidated intelligence picture…');
  return `
  <section class="view-head"><h2>Intelligence</h2><p class="sub">Vessel identities, tracks, records and incidents cross-referenced into one view ${sim()}</p></section>
  <div class="disclaimer">${esc(it.disclaimer)}</div>
  <section class="home-grid">
    <div class="card"><h3>Flagged vessels and their connections</h3>
      <div class="intel-cards">${it.cards.map((c) => `<article class="intel-card lv-${c.level}">
        <header><button class="link-inline" data-entity="${esc(c.vessel_id)}"><b>${esc(c.name)}</b></button> ${riskChip(c.level)} <span class="muted small">${fmt(c.score, 0)}/100 · ${esc(c.flag)} · ${esc(typeName(c.type))}</span></header>
        <ul class="watch">${c.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
        ${c.links.length ? `<div class="links">${c.links.map((l) => `<div class="link-row"><span class="lk lk-${l.kind}">${esc(l.kind)}</span>${['incident', 'nearby', 'vessel', 'agency'].includes(l.kind) ? `<button class="link-inline" data-entity="${esc(l.id)}">${esc(l.text)}</button>` : `<span>${esc(l.text)}</span>`}</div>`).join('')}</div>` : ''}
      </article>`).join('') || '<p class="empty">No vessel above Low risk.</p>'}</div></div>
    <div class="card"><h3>Link chart</h3><p class="sub">Vessels, owners, incidents and radar contacts that connect the flagged vessels. Click a node.</p>
      ${networkSvg(it.graph)}
      ${it.agency_unmatched?.length ? `<h3 class="section-title">Agency records not linked to a known vessel (7 days)</h3>
      <div class="links">${it.agency_unmatched.map((u) => `<div class="link-row"><span class="lk lk-agency">${esc(u.agency)}</span>${u.id.startsWith('OBS-') ? `<button class="link-inline" data-entity="${esc(u.id)}">${esc(u.kind.replace('_', ' '))}${u.zone ? ` in ${esc(u.zone)}` : ''}: ${esc(u.text)}</button>` : `<span>${esc(u.kind)}: ${esc(u.text)}</span>`}</div>`).join('')}</div>
      <p class="muted small">Unidentified contacts and listings stay visible so analysts can match them by hand.</p>` : ''}
      <h3 class="section-title">Incident hotspots · last 90 days</h3>
      <div class="table-scroll"><table class="data compact"><thead><tr><th>Cell (0.5°)</th><th>Zone</th><th class="num">Incidents</th><th>Main types</th></tr></thead>
        <tbody>${it.hotspots.map((h) => `<tr><td>${esc(fmtCoord(h.lat, h.lon))}</td><td>${esc(h.zone || '–')}</td><td class="num"><b>${h.count}</b></td><td class="small">${Object.entries(h.types).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([t, n]) => `${esc(t.replace('_', ' '))} ${n}`).join(', ')}</td></tr>`).join('')}</tbody></table></div>
    </div>
  </section>
  ${mergeSection(s)}
  <section class="card"><div class="card-head"><div><h3>Data sources and adapters</h3><p class="sub">Every source goes through an adapter (backend/adapters). Restricted agency feeds stay disconnected until an authorised integration exists.</p></div>
    ${can(s, 'engine:run') ? '<button class="btn small-btn" data-act="run-engine">↻ Re-run engine</button>' : ''}</div>
    <div class="table-scroll"><table class="data"><thead><tr><th>Source</th><th>Kind</th><th>State</th><th class="num">Records</th><th>As of</th><th>Notes</th></tr></thead>
      <tbody>${src.sources.map((x) => `<tr><td><b>${esc(x.name)}</b><div class="muted small">${esc(x.key)}</div></td><td>${esc(x.kind)}</td>
        <td><span class="src src-${x.restricted && !x.configured ? 'locked' : x.synthetic ? 'sim' : x.configured ? 'ok' : 'off'}">${esc(x.state)}</span></td>
        <td class="num">${x.records ?? '–'}</td><td class="small">${esc(x.as_of ? dt(x.as_of) : '–')}</td><td class="small">${esc(x.description)}${x.env ? ` <code>${esc(x.env)}</code>` : ''}</td></tr>`).join('')}</tbody></table></div>
    ${src.engine ? `<p class="muted small">Engine ${esc(src.engine.model)} last ran ${esc(dt(src.engine.at))} on ${src.engine.vessels} vessels.</p>` : ''}
  </section>`;
}

function networkSvg(g) {
  if (!g.nodes.length) return '<p class="empty">No links.</p>';
  const cols = { vessel: 0, owner: 1, radar: 1, incident: 2 };
  const W = 600;
  const groups = [[], [], []];
  g.nodes.forEach((n) => groups[cols[n.kind] ?? 1].push(n));
  const rowH = 34;
  const H = Math.max(...groups.map((x) => x.length)) * rowH + 30;
  const pos = {};
  groups.forEach((list, ci) => list.forEach((n, i) => {
    pos[n.id] = { x: [90, 300, 510][ci], y: 24 + i * rowH + (H - 30 - list.length * rowH) / 2 };
  }));
  const color = (n) => (n.kind === 'vessel' ? (RISK[n.level]?.color || '#5B9BD5') : n.kind === 'incident' ? '#E08A1E' : n.kind === 'radar' ? '#7DD3FC' : '#A78BFA');
  const edges = g.edges.filter((e) => pos[e.a] && pos[e.b]).map((e) => `<line x1="${pos[e.a].x}" y1="${pos[e.a].y}" x2="${pos[e.b].x}" y2="${pos[e.b].y}" class="net-edge"><title>${esc(e.kind)}</title></line>`).join('');
  const nodes = g.nodes.map((n) => {
    const p = pos[n.id];
    const clickable = n.kind === 'vessel' || n.kind === 'incident';
    const label = n.label.length > 26 ? `${n.label.slice(0, 25)}…` : n.label;
    return `<g class="net-node${clickable ? ' click' : ''}" ${clickable ? `data-entity="${esc(n.id)}" tabindex="0" role="button"` : ''} transform="translate(${p.x} ${p.y})">
      <circle r="7" fill="${color(n)}"/><text x="${p.x > 400 ? -11 : 11}" y="4" text-anchor="${p.x > 400 ? 'end' : 'start'}">${esc(label)}</text><title>${esc(n.label)}</title></g>`;
  }).join('');
  return `<svg class="net" viewBox="0 0 ${W} ${H}" role="img" aria-label="Link chart"><text x="90" y="12" class="net-h">Vessels</text><text x="300" y="12" class="net-h">Owners · radar</text><text x="510" y="12" class="net-h">Incidents</text>${edges}${nodes}</svg>
    <div class="legend status-legend"><span><i class="lg" style="background:#FF4D5A"></i> vessel (colour = risk)</span><span><i class="lg" style="background:#A78BFA"></i> owner</span><span><i class="lg" style="background:#7DD3FC"></i> radar contact</span><span><i class="lg" style="background:#E08A1E"></i> incident</span></div>`;
}

// ------------------------------------------------------------------ analytics
export function analyticsView(s) {
  const a = s.server.analytics;
  const r = s.ui.range;
  const presets = [['30', '30 days'], ['90', '90 days'], ['180', '6 months'], ['365', '12 months']];
  const head = `
  <section class="view-head"><h2>Analytics and reporting</h2><p class="sub">Incident trends, high-risk locations, alert statistics and vessel patterns ${sim()}</p></section>
  <section class="card no-print"><form class="filter-form" data-form="range">
    <span class="chips tight">${presets.map(([d, l]) => `<button type="button" class="chip chip-sm${r.preset === d ? ' is-on' : ''}" data-act="range" data-days="${d}">${l}</button>`).join('')}</span>
    <label>From<input type="date" name="from" value="${esc(r.from)}"></label><label>To<input type="date" name="to" value="${esc(r.to)}"></label><button class="btn small-btn">Apply</button>
    <span class="spacer"></span>
    ${can(s, 'export') ? `<button type="button" class="btn small-btn" data-act="export" data-kind="incidents.csv">⬇ Incidents CSV</button>
      <button type="button" class="btn small-btn" data-act="export" data-kind="alerts.csv">⬇ Alerts CSV</button>
      <button type="button" class="btn small-btn" data-act="export" data-kind="vessels.csv">⬇ Vessel risk CSV</button>
      <button type="button" class="btn small-btn" data-act="export" data-kind="report.json">⬇ Report JSON</button>` : '<span class="muted small">Exports need the export permission.</span>'}
    <button type="button" class="btn small-btn" data-act="print">🖨 Print / PDF</button></form></section>`;
  if (!a) return head + loading();
  const months = a.incidents_by_month;
  const lv = ['critical', 'high', 'medium', 'low'];
  const hours = ['00', '03', '06', '09', '12', '15', '18', '21'];
  return `${head}
  <div class="print-only"><h1>GulfEye maritime security report</h1><p>${esc(a.from)} to ${esc(a.to)} · simulated demo data</p></div>
  <section class="kpis wide">
    <div class="kpi"><span class="kpi-v">${a.incidents_total}</span><span class="kpi-l">incidents in range</span></div>
    <div class="kpi"><span class="kpi-v">${(a.incidents_by_type.find((x) => x.type === 'piracy_attack')?.count || 0) + (a.incidents_by_type.find((x) => x.type === 'kidnapping')?.count || 0)}</span><span class="kpi-l">attacks and kidnappings</span></div>
    <div class="kpi"><span class="kpi-v">${Object.values(a.alerts_by_level).reduce((x, y) => x + y, 0)}</span><span class="kpi-l">AI alerts (open or raised in range)</span></div>
    <div class="kpi"><span class="kpi-v">${(a.risk_distribution.high || 0) + (a.risk_distribution.critical || 0)}</span><span class="kpi-l">vessels at High or Critical now</span></div>
  </section>
  <section class="grid-2">
    <div class="card"><h3>Incident trend by month</h3>
      ${barChart({ labels: months.map((m) => monthName(Number(m.month.slice(5)))), values: months.map((m) => m.count), unit: 'incidents', digits: 0, color: '#0B5C7A' })}</div>
    <div class="card"><h3>Incidents by type</h3>${hbarChart(a.incidents_by_type.map((x) => [x.label, x.count]), { color: '#E08A1E' })}</div>
  </section>
  <section class="grid-2">
    <div class="card"><h3>High-risk locations</h3><p class="sub">Each dot is an incident in the range; zones as on the sea map.</p>${incidentMap(s, a.points)}
      ${hbarChart(a.incidents_by_zone.map((x) => [x.zone, x.count]), { color: '#E5484D' })}</div>
    <div class="card"><h3>Alert statistics</h3>
      <div class="split"><div><h4>By risk level</h4>${hbarChart(lv.filter((l) => a.alerts_by_level[l]).map((l) => [RISK[l].label, a.alerts_by_level[l]]), { colors: lv.filter((l) => a.alerts_by_level[l]).map((l) => RISK[l].color) })}</div>
        <div><h4>By status</h4>${hbarChart(Object.entries(a.alerts_by_status).map(([k, v]) => [STATUS_LABEL[k] || k, v]), { color: '#3B7DB5' })}</div></div>
      <h4>Behaviour behind alerts</h4>${hbarChart(a.alerts_by_behaviour.map((x) => [behaviourLabel(x.code), x.count]), { color: '#6A54A0' })}
      <h4>Time of day of incidents (UTC, 3-hour blocks)</h4>${barChart({ labels: hours, values: a.incidents_by_hour, digits: 0, unit: 'incidents', height: 120, color: '#4F8FC0' })}</div>
  </section>
  <section class="card"><h3>Vessel movement patterns</h3><p class="sub">Current AIS window: count, mean speed and how many are flagged by the engine, per vessel type.</p>
    <div class="table-scroll"><table class="data"><thead><tr><th>Type</th><th class="num">Vessels</th><th class="num">Mean speed</th><th class="num">Flagged (≥ Medium)</th><th>Risk now</th></tr></thead>
      <tbody>${a.vessel_types.map((t) => `<tr><td><span class="crop-chip"><i style="background:${typeColor(t.type)}"></i>${esc(typeName(t.type))}</span></td><td class="num">${t.count}</td><td class="num">${fmt(t.mean_sog)} kn</td><td class="num">${t.flagged}</td><td>${t.flagged ? '<span class="neg">needs review</span>' : '<span class="muted">normal</span>'}</td></tr>`).join('')}</tbody></table></div>
    <p class="muted small">Risk distribution of all vessels: ${lv.map((l) => `${RISK[l].label} ${a.risk_distribution[l] || 0}`).join(' · ')}.</p></section>`;
}

function incidentMap(s, points) {
  const pr = projector(s.geo);
  const poly = (pts) => pts.map(([lo, la]) => `${pr.x(lo).toFixed(1)},${pr.y(la).toFixed(1)}`).join(' ');
  const col = { piracy_attack: '#FF4D5A', kidnapping: '#FF4D5A', boarding: '#F0556A', armed_robbery: '#F5A524', oil_theft: '#B58CE0', sts_transfer: '#A78BFA', illegal_fishing: '#3FBF9A' };
  return `<svg class="sea-map mini" viewBox="0 0 1000 ${pr.H.toFixed(0)}" role="img" aria-label="Incident locations">
    <rect width="1000" height="${pr.H}" fill="#0B2A44"/>${s.geo.zones.map((z) => `<polygon points="${poly(z.poly)}" class="m-zone" style="--zc:${z.color}"/>`).join('')}
    <polygon points="${poly(s.geo.land)}" class="m-land"/>${s.geo.islands.map((i) => `<polygon points="${poly(i.poly)}" class="m-land"/>`).join('')}
    ${points.map((p) => `<circle cx="${pr.x(p.lon).toFixed(1)}" cy="${pr.y(p.lat).toFixed(1)}" r="5" fill="${col[p.type] || '#7DD3FC'}" fill-opacity=".75" stroke="#fff" stroke-width=".8" class="click" data-entity="${esc(p.id)}"><title>${esc(p.id)} · ${esc(p.type)}</title></circle>`).join('')}
    ${s.geo.ports.map((p) => `<g class="m-port"><circle cx="${pr.x(p.lon)}" cy="${pr.y(p.lat)}" r="3"/><text x="${pr.x(p.lon) + 6}" y="${pr.y(p.lat) - 5}">${esc(p.name)}</text></g>`).join('')}</svg>`;
}

// ------------------------------------------------------------------ agencies (sharing, roles, audit)
export function agenciesView(s) {
  const sv = s.server;
  const ag = sv.agencies;
  if (!ag || !sv.roles || !sv.audit || !sv.inbox || !sv.users) return loading();
  const admin = can(s, 'users:manage');
  return `
  <section class="view-head"><h2>Agencies and sharing</h2><p class="sub">Role-based access for maritime stakeholders, shared items and the audit trail</p></section>
  <div class="banner">Agency names show how the sharing model works. GulfEye has no connection to these organisations' systems; demo accounts are fictional.</div>
  <section class="grid-2">
    <div class="card"><h3>Shared with ${esc(sv.me.agency)}</h3>
      <div class="mini-tl">${sv.inbox.shares.filter((x) => x.direction === 'in').map((x) => `<div class="mini-tl-row share-row${x.read_at ? '' : ' is-unread'}">
        <span class="mini-date">${esc(fmtDate(x.at))}</span><span>from <b>${esc(x.from)}</b> · <button class="link-inline" data-entity="${esc(x.target_type === 'vessel' ? x.target_id : x.target_id)}" data-read="${x.id}">${esc(x.target_type)} ${esc(x.target_id)}</button></span>
        <span class="muted">${esc(x.who)}${x.message ? `: “${esc(x.message)}”` : ''}</span></div>`).join('') || '<p class="muted small">Nothing shared with your agency yet.</p>'}</div>
      <h3 class="section-title">Sent by ${esc(sv.me.agency)}</h3>
      <div class="mini-tl">${sv.inbox.shares.filter((x) => x.direction === 'out').slice(0, 10).map((x) => `<div class="mini-tl-row"><span class="mini-date">${esc(fmtDate(x.at))}</span><span>to <b>${esc(x.to)}</b> · ${esc(x.target_type)} ${esc(x.target_id)}</span><span class="muted">${esc(x.message || '')}</span></div>`).join('') || '<p class="muted small">Nothing sent yet. Use “Share” in any alert, incident or vessel profile.</p>'}</div>
    </div>
    <div class="card"><h3>Agencies</h3>
      <table class="data compact"><thead><tr><th>Code</th><th>Agency</th><th class="num">Users</th></tr></thead>
        <tbody>${ag.agencies.map((a) => `<tr${a.code === sv.me.agency ? ' class="is-me"' : ''}><td><b>${esc(a.code)}</b></td><td>${esc(a.name)}</td><td class="num">${a.users}</td></tr>`).join('')}</tbody></table>
      <h3 class="section-title">Roles and permissions</h3>
      <div class="table-scroll"><table class="data compact matrix"><thead><tr><th>Permission</th>${sv.roles.roles.map((r) => `<th>${esc(r.label)}</th>`).join('')}</tr></thead>
        <tbody>${Object.entries(sv.roles.permissions).map(([p, l]) => `<tr><td>${esc(l)}</td>${sv.roles.roles.map((r) => `<td class="c">${r.permissions.includes(p) ? '✓' : ''}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
      <p class="muted small">Restricted incidents are visible only to the reporting agency, administrators, and agencies they were explicitly shared with.</p>
      ${admin ? `<h3 class="section-title">Onboard a partner agency</h3>
      <form class="filter-form" data-form="agency-create"><label>Code<input name="code" required pattern="[A-Z][A-Z0-9]{1,11}" placeholder="e.g. GHN" maxlength="12"></label>
        <label>Name<input name="name" required minlength="3" placeholder="e.g. Ghana Navy"></label>
        <label>Kind<input name="kind" required minlength="2" placeholder="navy, customs, port…"></label><button class="btn small-btn">Add agency</button></form>
      <p class="muted small">Then add its users below and give its systems an API key in Data intake. Its data is merged with everyone else's from the first submission.</p>` : ''}
    </div>
  </section>
  <section class="card"><h3>${admin ? 'Users' : `Users in ${esc(sv.me.agency)}`}</h3>
    <div class="table-scroll"><table class="data"><thead><tr><th>Name</th><th>Email</th><th>Agency</th><th>Role</th><th>Active</th></tr></thead>
      <tbody>${sv.users.users.map((u) => `<tr><td>${esc(u.name)}</td><td>${esc(u.email)}</td><td>${esc(u.agency)}</td>
        <td>${admin ? `<select data-user-role="${u.id}">${['admin', 'analyst', 'operator', 'viewer'].map((r) => `<option ${u.role === r ? 'selected' : ''}>${r}</option>`).join('')}</select>` : esc(u.role)}</td>
        <td>${admin ? `<input type="checkbox" data-user-active="${u.id}" ${u.active ? 'checked' : ''}>` : (u.active ? 'yes' : 'no')}</td></tr>`).join('')}</tbody></table></div>
    ${admin ? `<form class="filter-form" data-form="user-create"><label>Name<input name="name" required minlength="2"></label><label>Email<input name="email" type="email" required></label>
      <label>Agency<select name="agency">${ag.agencies.map((a) => `<option>${esc(a.code)}</option>`).join('')}</select></label>
      <label>Role<select name="role"><option>viewer</option><option>operator</option><option>analyst</option><option>admin</option></select></label>
      <label>Initial password<input name="password" type="password" minlength="10" required></label><button class="btn small-btn">Add user</button></form>` : ''}
  </section>
  <section class="card"><div class="card-head"><div><h3>Audit log</h3><p class="sub">${sv.audit.full ? 'All actions by all users.' : 'Your own actions (administrators see everything).'} Logins, views, changes, shares and exports.</p></div>
    <span class="chips tight">${['', 'auth', 'alert', 'incident', 'share', 'export', 'vessel', 'denied'].map((a) => `<button class="chip chip-sm${(s.ui.auditFilter || '') === a ? ' is-on' : ''}" data-act="audit-filter" data-v="${a}">${a || 'all'}</button>`).join('')}</span></div>
    <div class="table-scroll"><table class="data compact"><thead><tr><th>Time (UTC)</th><th>User</th><th>Agency</th><th>Action</th><th>Target</th><th>Detail</th><th>IP</th></tr></thead>
      <tbody>${sv.audit.entries.map((e) => `<tr class="${e.ok ? '' : 'is-fail'}"><td class="small">${esc(dt(e.at))}</td><td class="small">${esc(e.email || '–')}</td><td>${esc(e.agency || '')}</td><td><code>${esc(e.action)}</code>${e.ok ? '' : ' ✗'}</td><td class="small">${esc(e.target || '')}</td><td class="small mono">${esc((e.detail || '').slice(0, 120))}</td><td class="small">${esc(e.ip || '')}</td></tr>`).join('')}</tbody></table></div>
  </section>`;
}

// ------------------------------------------------------------------ impact and SDGs
export function impactView(s) {
  const sm = s.server?.summary;
  const fishing = s.ais.vessels.filter((v) => v.type === 'fishing').length;
  const objectives = [
    ['🐟', 'Protect marine ecosystems', 'Flags illegal fishing, unauthorised ship-to-ship transfers and illegal bunkering, which drive overfishing and oil pollution in the Niger Delta and coastal waters.'],
    ['⚓', 'Support sustainable maritime activity', 'Separates normal fishing and offshore supply patterns from suspicious behaviour, so legitimate operators are not burdened by blanket suspicion.'],
    ['🛟', 'Protect fishermen, seafarers, passengers and coastal communities', `Early warning on attack patterns near fishing grounds and approaches. ${fishing} fishing vessels are in the current AIS window; many artisanal canoes carry no AIS at all, which is why radar is fused in.`],
    ['🧭', 'Improve maritime safety', 'Sea-state windows, response planning and incident reporting help assets reach people in distress faster and safely.'],
    ['🤝', 'Strengthen collaboration between security agencies', 'One shared picture, explicit sharing, notes between agencies and a full audit trail replace fragmented phone and email exchanges.'],
    ['📦', 'Protect maritime trade and coastal livelihoods', 'Fewer successful attacks lower insurance and freight costs and keep ports and fisheries working.'],
  ];
  const sdgs = [
    ['14', 'Life Below Water', '#0A97D9', 'Detection of illegal fishing and spill risks (targets 14.1, 14.4, 14.6).'],
    ['16', 'Peace, Justice and Strong Institutions', '#00689D', 'Evidence-based, auditable support to law-enforcement agencies; reduction of organised maritime crime (16.1, 16.4, 16.6).'],
    ['8', 'Decent Work and Economic Growth', '#A21942', 'Safer working conditions for seafarers and fishers (8.8) and protection of maritime trade.'],
    ['9', 'Industry, Innovation and Infrastructure', '#FD6925', 'Open, modular data adapters and AI applied to regional maritime infrastructure (9.1, 9.5).'],
    ['11', 'Sustainable Cities and Communities', '#FD9D24', 'Resilience of coastal communities and ports exposed to maritime crime and disasters (11.5).'],
  ];
  return `
  <section class="view-head"><h2>About and impact</h2><p class="sub">Why GulfEye exists and how it should be used</p></section>
  <section class="card impact-hero"><h3>GulfEye</h3>
    <p>An AI-assisted maritime intelligence and anti-piracy platform for Nigerian waters and the Gulf of Guinea. It fuses AIS, satellite radar, registration records and incident reports into one picture so agencies can spot suspicious activity early, share what they know, and protect lives at sea.</p>
    ${sm ? `<div class="kpis wide"><div class="kpi"><span class="kpi-v">${sm.vessels_monitored}</span><span class="kpi-l">vessels in view</span></div><div class="kpi"><span class="kpi-v">${sm.suspicious_vessels}</span><span class="kpi-l">flagged for review</span></div><div class="kpi"><span class="kpi-v">${sm.active_incidents}</span><span class="kpi-l">incidents being handled</span></div><div class="kpi"><span class="kpi-v">5</span><span class="kpi-l">agencies on one picture</span></div></div><p class="muted small">Demo figures from simulated data.</p>` : ''}
  </section>
  <section class="card"><h3>Environmental and social objectives</h3>
    <div class="objectives">${objectives.map(([i, t, d]) => `<div class="objective"><span class="obj-ico">${i}</span><div><b>${esc(t)}</b><p>${esc(d)}</p></div></div>`).join('')}</div></section>
  <section class="card"><h3>Sustainable Development Goals</h3>
    <div class="sdgs">${sdgs.map(([n, t, c, d]) => `<div class="sdg"><span class="sdg-n" style="background:${c}">${n}</span><div><b>SDG ${n}: ${esc(t)}</b><p>${esc(d)}</p></div></div>`).join('')}</div></section>
  <section class="card"><h3>Responsible use</h3>
    <ul class="watch">
      <li><b>Human decision.</b> Risk scores and alerts point analysts to vessels worth checking. They never declare a vessel guilty and never trigger enforcement on their own.</li>
      <li><b>AIS loss is not proof.</b> Signal gaps also come from coverage holes and equipment faults. A gap alone can never rate a vessel above Medium.</li>
      <li><b>Explainable.</b> Every score lists its indicators, evidence and reasons; the machine-learning model is one capped indicator among several.</li>
      <li><b>Honest data.</b> Simulated data is labelled everywhere. Agency feeds (NIMASA, Navy, coastal radar) are shown as “requires authorised integration” until a real agreement exists.</li>
      <li><b>Accountable.</b> Role-based access, restricted incidents and an audit log of every sign-in, view, change, share and export.</li>
    </ul></section>`;
}

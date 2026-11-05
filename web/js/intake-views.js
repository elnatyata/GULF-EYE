// Agency data intake screen: upload/paste agency data, see what was accepted or rejected, manage API keys for
// agency systems, and browse what agencies have contributed (watchlist, sightings, patrol reports, radar contacts).
import { esc, fmt, fmtCoord, fmtDate, fmtTime, badges } from './util.js';
import { can, riskChip } from './intel-views.js';

const KIND_LABEL = { sighting: 'Sighting', patrol_report: 'Patrol report', radar_contact: 'Radar contact' };
const KIND_ICON = { sighting: '👁️', patrol_report: '🚤', radar_contact: '📡' };
export const AGENCY_COLOR = { NN: '#2FBF8A', NIMASA: '#7DD3FC', MPOL: '#F5A524', NPA: '#B58CE0', GULFEYE: '#E4EEF6', NAF: '#60A5FA', NCS: '#F472B6', MMCC: '#FACC15' };
const dt = (iso) => (iso ? `${fmtDate(iso, true)} ${fmtTime(iso)}` : '–');
const CHANNEL = { api: 'API (agency system)', csv: 'CSV upload', json: 'JSON (signed-in user)', seed: 'demo seed' };

export const obsIcon = (k) => KIND_ICON[k] || '•';

export function intakeView(s) {
  const sv = s.server;
  const d = sv.intake;
  if (!d) return '<div class="card"><p class="muted">Loading the agency intake…</p></div>';
  const kind = s.ui.intakeKind;
  const k = d.kinds.kinds.find((x) => x.kind === kind) || d.kinds.kinds[0];
  const res = s.ui.intakeResult;
  return `
  <section class="view-head"><h2>Agency data intake</h2><p class="sub">How NIMASA, the Navy, Marine Police, the Ports Authority and other partners feed their own data into the shared picture</p></section>
  <div class="banner"><b>Prototype.</b> Agencies push data today through CSV upload or the JSON intake API with an agency key. In phase 2, direct connectors to their systems
    (NIMASA, Navy, coastal radar) will call this same intake, so the record formats below are the integration contract. Demo submissions are simulated ${badges(['simulated'])}</div>
  <section class="home-grid">
    <div class="card">
      <h3>Submit data for ${esc(sv.me.agency)}</h3>
      ${can(s, 'intake:submit') ? `
      <form class="intake-form" data-form="intake-submit">
        <div class="confirm-grid">
          <label>What are you sending?<select name="kind" data-act-change="intake-kind">${d.kinds.kinds.map((x) => `<option value="${x.kind}" ${x.kind === k.kind ? 'selected' : ''}>${esc(x.label)}</option>`).join('')}</select></label>
          <label>Format<select name="format" data-act-change="intake-format"><option value="csv" ${s.ui.intakeFormat === 'csv' ? 'selected' : ''}>CSV file</option><option value="json" ${s.ui.intakeFormat === 'json' ? 'selected' : ''}>JSON records (paste)</option></select></label>
        </div>
        ${s.ui.intakeFormat === 'json'
          ? `<label class="full">Records (JSON list)<textarea name="json" rows="8" class="mono" spellcheck="false">${esc(JSON.stringify([k.example], null, 1))}</textarea></label>`
          : '<label class="full">CSV file<input type="file" name="file" accept=".csv,text/csv" required></label>'}
        <p class="muted small">Required: <b>${esc(k.required.join(', ') || 'see fields')}</b>. All fields: ${esc(k.fields.join(', '))}. Times in UTC (ISO 8601). Use <code>ref</code> for your own record id: resubmitting the same ref is rejected as a duplicate. <code>visibility</code>: <i>shared</i> (default) or <i>agency</i> (only your agency; not used by the shared risk engine).</p>
        <div class="confirm-actions"><button type="button" class="btn small-btn" data-act="intake-template" data-kind="${k.kind}">⬇ CSV template</button><button class="btn primary small-btn">Submit</button></div>
        <p class="form-error" role="alert"></p>
      </form>
      ${res ? resultBox(res) : ''}` : '<p class="muted">Your role can read agency data but not submit it.</p>'}
    </div>
    <div class="card"><h3>Contributions by agency</h3>
      <table class="data compact"><thead><tr><th>Agency</th><th class="num">Submissions</th><th class="num">Accepted</th><th class="num">Rejected</th><th>Last</th></tr></thead>
        <tbody>${d.submissions.by_agency.map((a) => `<tr><td><span class="ag-dot" style="background:${AGENCY_COLOR[a.agency] || '#999'}"></span><b>${esc(a.agency)}</b></td><td class="num">${a.submissions}</td><td class="num">${a.accepted}</td><td class="num">${a.rejected ? `<span class="neg">${a.rejected}</span>` : 0}</td><td class="small">${esc(dt(a.last))}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">Nothing received yet.</td></tr>'}</tbody></table>
      <h3 class="section-title">What happens to the data</h3>
      <ul class="watch small">
        <li>Each record is validated (types, time, position inside the Gulf of Guinea area, IMO check digit). Bad records are rejected one by one with the reason; good ones are kept.</li>
        <li>Vessels are matched by MMSI, then IMO, then name. Unmatched records stay visible as unidentified.</li>
        <li>Shared records feed the risk engine: watchlist entries, sightings and patrol reports, and unidentified radar contacts found alongside a vessel. Scores are recalculated at once and alerts update live.</li>
        <li>Every submission is in the audit log with the agency, the user or API key, and the counts.</li>
      </ul>
    </div>
  </section>
  <section class="card"><h3>Recent submissions ${d.submissions.scope === 'all agencies' ? '(all agencies)' : `(${esc(d.submissions.scope)})`}</h3>
    <div class="table-scroll"><table class="data"><thead><tr><th>#</th><th>Received (UTC)</th><th>Agency</th><th>Data</th><th>Channel</th><th>By</th><th class="num">Records</th><th class="num">Accepted</th><th class="num">Rejected</th></tr></thead>
      <tbody>${d.submissions.submissions.map((x) => `<tr class="click" data-entity="SUB-${x.id}"><td>${x.id}</td><td class="small">${esc(dt(x.received_at))}</td><td><b>${esc(x.agency)}</b></td>
        <td>${esc(d.kinds.kinds.find((y) => y.kind === x.kind)?.label || x.kind)}${x.filename ? `<div class="muted small">${esc(x.filename)}</div>` : ''}</td><td class="small">${esc(CHANNEL[x.channel] || x.channel)}</td>
        <td class="small">${esc(x.by)}</td><td class="num">${x.records}</td><td class="num">${x.accepted}</td><td class="num">${x.rejected ? `<span class="neg">${x.rejected}</span>` : 0}</td></tr>`).join('') || '<tr><td colspan="9" class="empty">No submissions.</td></tr>'}</tbody></table></div>
  </section>
  <section class="grid-2">
    <div class="card"><h3>Agency watchlist</h3>
      <div class="table-scroll"><table class="data compact"><thead><tr><th>Vessel</th><th>Level</th><th>Reason</th><th>From</th><th>Active</th></tr></thead>
        <tbody>${d.watchlist.entries.map((w) => `<tr><td>${w.vessel_id ? `<button class="link-inline" data-entity="${esc(w.vessel_id)}">${esc(w.vessel_name || w.name || w.vessel_id)}</button>` : `${esc(w.name || w.mmsi || w.imo)} <span class="tag">unidentified</span>`}</td>
          <td><span class="wl wl-${w.level}">${esc(w.level)}</span></td><td class="small">${esc(w.reason)}${w.valid_until ? `<div class="muted">until ${esc(w.valid_until)}</div>` : ''}</td>
          <td>${esc(w.agency)}${w.visibility === 'agency' ? ' 🔒' : ''}</td>
          <td>${(w.agency === sv.me.agency && can(s, 'intake:submit')) || sv.me.role === 'admin' ? `<input type="checkbox" data-wl-active="${w.id}" ${w.active ? 'checked' : ''} aria-label="Active">` : (w.active ? 'yes' : 'no')}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">Empty.</td></tr>'}</tbody></table></div></div>
    <div class="card"><h3>Sightings, patrol reports and radar contacts</h3>
      <div class="activity">${d.observations.observations.slice(0, 12).map((o) => `<button class="act-row" data-entity="OBS-${o.id}"><span class="act-ico">${obsIcon(o.kind)}</span>
        <span class="act-body"><b>${esc(KIND_LABEL[o.kind])} · ${esc(o.agency)} · ${esc(o.vessel_name || (o.reported_name ? `${o.reported_name} (unmatched)` : 'unidentified'))}</b>
        <span class="muted small">${esc(dt(o.observed_at))} · ${esc(fmtCoord(o.lat, o.lon))}${o.description ? ` · ${esc(o.description.slice(0, 90))}` : ''}</span></span>${o.visibility === 'agency' ? '🔒' : ''}</button>`).join('') || '<p class="empty">None.</p>'}</div></div>
  </section>
  ${can(s, 'keys:manage') ? keysCard(s, d) : ''}
  <section class="card"><h3>Integration contract (for agency IT teams)</h3>
    <p class="sub">The same endpoint serves today's prototype and phase-2 connectors. Interactive reference: <a href="/api/docs" target="_blank" rel="noopener">/api/docs</a>.</p>
    <pre class="code">POST /api/intake/${esc(k.kind)}
X-API-Key: ge_xxxxxxxx_…            (created below, scoped to the data types it may send)
Content-Type: application/json

{"records": ${esc(JSON.stringify([k.example], null, 2))}}

→ {"submission": 12, "accepted": 1, "rejected": 0, "created": [...], "errors": [{"record": 2, "ref": "...", "error": "..."}]}

CSV: POST /api/intake/${esc(k.kind)}/csv  (multipart field "file", columns as in the template)</pre>
  </section>`;
}

function resultBox(r) {
  return `<div class="intake-result ${r.rejected ? 'has-errors' : ''}">
    <b>Submission ${r.submission}: ${r.accepted} accepted, ${r.rejected} rejected</b> of ${r.received} record${r.received === 1 ? '' : 's'}.
    ${r.engine ? `<span class="muted small">Risk engine re-ran: ${r.engine.created.length} new alert(s), ${r.engine.updated.length} updated.</span>` : ''}
    ${r.errors.length ? `<table class="data compact"><thead><tr><th>Record</th><th>Ref</th><th>Why it was rejected</th></tr></thead><tbody>${r.errors.map((e) => `<tr><td>${e.record}</td><td>${esc(e.ref || '')}</td><td class="small">${esc(e.error)}</td></tr>`).join('')}</tbody></table>` : ''}
  </div>`;
}

function keysCard(s, d) {
  const sv = s.server;
  const admin = sv.me.role === 'admin';
  const kinds = d.kinds.kinds.map((x) => x.kind);
  const nk = s.ui.newKey;
  return `<section class="card"><h3>API keys for agency systems</h3>
    <p class="sub">An agency system authenticates with its key instead of a user. Each key belongs to one agency, is limited to the data types ticked, and can be revoked at any time.</p>
    ${nk ? `<div class="new-key"><b>New key, shown once.</b> Copy it into the agency system's secret store now; GulfEye only keeps a hash.
      <pre class="code">${esc(nk.key)}</pre><pre class="code">curl -X POST ${esc(location.origin)}/api/intake/sightings -H "X-API-Key: ${esc(nk.key)}" -H "Content-Type: application/json" -d '{"records":[...]}'</pre>
      <button class="btn small-btn" data-act="key-dismiss">I have stored it</button></div>` : ''}
    <form class="filter-form" data-form="key-create"><label>Key name<input name="name" required minlength="3" maxlength="60" placeholder="e.g. NN operations centre feed"></label>
      ${admin ? `<label>Agency<select name="agency">${(sv.agencyCodes || []).map((a) => `<option ${a === sv.me.agency ? 'selected' : ''}>${a}</option>`).join('')}</select></label>` : ''}
      <span class="checks">${kinds.map((x) => `<label><input type="checkbox" name="scopes" value="${x}" ${['sightings', 'patrol_reports', 'radar_contacts'].includes(x) ? 'checked' : ''}> ${esc(x)}</label>`).join('')}</span>
      <button class="btn small-btn">Create key</button></form>
    <div class="table-scroll"><table class="data compact"><thead><tr><th>Name</th><th>Agency</th><th>Key</th><th>May send</th><th>Created</th><th>Last used</th><th></th></tr></thead>
      <tbody>${(d.keys?.keys || []).map((k) => `<tr class="${k.revoked_at ? 'is-revoked' : ''}"><td>${esc(k.name)}</td><td>${esc(k.agency)}</td><td><code>ge_${esc(k.prefix)}_…</code></td>
        <td class="small">${esc(k.scopes.join(', '))}</td><td class="small">${esc(dt(k.created_at))}</td><td class="small">${esc(k.last_used_at ? dt(k.last_used_at) : 'never')}</td>
        <td>${k.revoked_at ? `<span class="muted small">revoked ${esc(fmtDate(k.revoked_at))}</span>` : `<button class="btn small-btn" data-act="key-revoke" data-id="${k.id}">Revoke</button>`}</td></tr>`).join('') || '<tr><td colspan="7" class="empty">No keys yet.</td></tr>'}</tbody></table></div>
  </section>`;
}

function drawerHead(title, sub) {
  return `<div class="drawer-head"><div><div class="drawer-title">${title}</div><div class="sub">${sub}</div></div>
    <button class="icon-btn" data-action="close-drawer" aria-label="Close">✕</button></div>`;
}

export function observationDrawer(s, id) {
  const o = s.server.detail[`o:${id}`];
  if (!o) return `${drawerHead(esc(id), 'Loading…')}<div class="drawer-body"><p class="muted">Loading…</p></div>`;
  return `${drawerHead(`${obsIcon(o.kind)} ${esc(KIND_LABEL[o.kind])} · ${esc(id)}`, `${esc(o.agency)} · ${esc(fmtCoord(o.lat, o.lon))} · ${esc(dt(o.observed_at))} UTC`)}
  <div class="drawer-body">
    <div class="status-box"><b>Submitted by ${esc(o.agency)}</b><span class="muted small">Submission ${o.submission.id} · ${esc(CHANNEL[o.submission.channel] || o.submission.channel)} · received ${esc(dt(o.submission.received_at))}${o.ref ? ` · agency ref ${esc(o.ref)}` : ''}</span>
      ${o.visibility === 'agency' ? `<span class="tag">Restricted to ${esc(o.agency)}: not used by the shared risk engine</span>` : ''}</div>
    ${o.synthetic ? `<div class="banner">Simulated demo record. ${badges(['simulated'])}</div>` : ''}
    ${o.description ? `<p>${esc(o.description)}</p>` : ''}
    <dl class="soil"><dt>Vessel</dt><dd>${o.vessel_id ? `<button class="link-inline" data-entity="${esc(o.vessel_id)}">${esc(o.vessel_name || o.vessel_id)}</button> (matched by ${esc(o.match)})` : `unidentified${o.reported_mmsi ? ` · reported MMSI ${esc(o.reported_mmsi)}` : ''}${o.reported_name ? ` · reported name ${esc(o.reported_name)}` : ''}`}</dd>
      ${o.length_m ? `<dt>Length</dt><dd>${fmt(o.length_m, 0)} m</dd>` : ''}${o.speed_kn != null ? `<dt>Speed · course</dt><dd>${fmt(o.speed_kn)} kn · ${o.course != null ? `${fmt(o.course, 0)}°` : '–'}</dd>` : ''}
      ${o.confidence != null ? `<dt>Sensor confidence</dt><dd>${fmt(o.confidence * 100, 0)}%</dd>` : ''}</dl>
    <h4>Open alerts within 25 km</h4>
    <ul class="watch">${o.nearby_alerts.map((a) => `<li><button class="link-inline" data-entity="${esc(a.id)}">${esc(a.id)}</button> ${riskChip(a.level)} ${esc(a.title)} · ${fmt(a.distance_km)} km</li>`).join('') || '<li class="muted">None.</li>'}</ul>
    ${can(s, 'incidents:create') ? `<div class="rec-actions"><button class="chip" data-act="incident-new" data-vessel="${esc(o.vessel_id || '')}" data-lat="${o.lat}" data-lon="${o.lon}">Report incident from this…</button></div>` : ''}
  </div>`;
}

export function submissionDrawer(s, id) {
  const n = Number(id.slice(4));
  const x = s.server.intake?.submissions.submissions.find((y) => y.id === n);
  if (!x) return `${drawerHead(esc(id), '')}<div class="drawer-body"><p class="muted">Open the Data intake screen to see this submission.</p></div>`;
  return `${drawerHead(`Submission ${x.id}`, `${esc(x.agency)} · ${esc(x.kind)} · ${esc(dt(x.received_at))} UTC`)}
  <div class="drawer-body">
    <div class="status-box ${x.rejected ? 'status-assigned' : 'status-ok'}"><b>${x.accepted} accepted · ${x.rejected} rejected</b><span class="muted small">${x.records} records · ${esc(CHANNEL[x.channel] || x.channel)} · by ${esc(x.by)}${x.filename ? ` · ${esc(x.filename)}` : ''}</span></div>
    ${x.synthetic ? `<div class="banner">Simulated demo submission. ${badges(['simulated'])}</div>` : ''}
    <h4>Rejected records</h4>
    ${x.errors.length ? `<table class="data compact"><thead><tr><th>Record</th><th>Ref</th><th>Reason</th></tr></thead><tbody>${x.errors.map((e) => `<tr><td>${e.record}</td><td>${esc(e.ref || '')}</td><td class="small">${esc(e.error)}</td></tr>`).join('')}</tbody></table>
      <p class="muted small">Fix these rows and submit them again; accepted rows do not need to be resent.</p>` : '<p class="muted small">None: every record was accepted.</p>'}
  </div>`;
}

export function agencyRecordsBlock(p) {
  const a = p.agency;
  if (!a || (!a.watchlist.length && !a.observations.length)) return '';
  return `<h4>Agency reports ${badges(['captured'])}</h4>
    ${a.watchlist.map((w) => `<div class="evidence"><div class="ev-head"><b>${esc(w.agency)} watchlist</b><span class="wl wl-${w.level}">${esc(w.level)}</span>${w.active ? '' : '<span class="muted small">inactive</span>'}</div>
      <p class="small">${esc(w.reason)}</p><p class="muted small">Listed ${esc(fmtDate(w.listed_at, true))}${w.valid_until ? ` · until ${esc(w.valid_until)}` : ''}${w.visibility === 'agency' ? ' · restricted' : ''}</p></div>`).join('')}
    <ul class="watch">${a.observations.map((o) => `<li><button class="link-inline" data-entity="OBS-${o.id}">${obsIcon(o.kind)} ${esc(o.agency)} ${esc(KIND_LABEL[o.kind].toLowerCase())}</button> · ${esc(dt(o.observed_at))} · ${esc(o.description || '')}</li>`).join('')}</ul>`;
}

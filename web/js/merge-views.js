// Merged picture across agencies: fused contacts, duplicate incidents (merge / not a duplicate / undo),
// and the consolidated vessel record with disagreements between sources.
import { esc, fmt, fmtCoord, fmtDate, fmtTime } from './util.js';
import { can } from './intel-views.js';
import { AGENCY_COLOR } from './intake-views.js';

const dt = (iso) => (iso ? `${fmtDate(iso, true)} ${fmtTime(iso)}` : '–');
const TYPE = { sar: 'Sentinel-1 dark contact', radar_contact: 'Radar contact', sighting: 'Sighting', patrol_report: 'Patrol report', incident: 'Incident report' };
const srcChip = (src) => `<span class="src-chip" style="--sc:${AGENCY_COLOR[src] || (src === 'Sentinel-1' ? '#FF4D5A' : '#9AA7B5')}">${esc(src)}</span>`;
const pct = (x) => `${fmt(Math.min(99, x * 100), 0)}%`;

export function fusedLabel(f) {
  return `${f.independent_sources} sources · ${f.sources.join(' + ')}`;
}

export function fusedDrawer(s, id) {
  const f = s.server.detail[`f:${id}`] || (s.server.fused || []).find((x) => x.id === id);
  const head = (t, sub) => `<div class="drawer-head"><div><div class="drawer-title">${t}</div><div class="sub">${sub}</div></div>
    <button class="icon-btn" data-action="close-drawer" aria-label="Close">✕</button></div>`;
  if (!f) return `${head(esc(id), '')}<div class="drawer-body"><p class="muted">This merged contact no longer exists: new data regrouped the reports.</p></div>`;
  return `${head(`⬡ Merged contact ${esc(f.id)}`, `${esc(fmtCoord(f.lat, f.lon))}${f.zone ? ` · ${esc(f.zone)}` : ''} · ${esc(dt(f.first))}${f.last !== f.first ? ` – ${esc(fmtTime(f.last))}` : ''} UTC`)}
  <div class="drawer-body">
    <div class="status-box ${f.independent_sources >= 3 ? 'status-new' : 'status-assigned'}"><b>${f.independent_sources} independent source${f.independent_sources > 1 ? 's' : ''} agree</b>
      <span>${f.sources.map(srcChip).join(' ')}</span>
      <span class="muted small">Combined confidence ${pct(f.confidence)} (best single source ${pct(f.single_best)}) · reports within ${fmt(f.spread_km)} km</span></div>
    <div class="disclaimer">Grouped automatically because the reports are within 5 km and 90 minutes of each other. The original reports are unchanged; check them before treating this as one craft or group.</div>
    ${f.vessels.length ? `<p>Linked vessel${f.vessels.length > 1 ? 's' : ''}: ${f.vessels.map((v) => `<button class="link-inline" data-entity="${esc(v.vessel_id)}">${esc(v.name || v.vessel_id)}</button>`).join(', ')}</p>` : '<p class="muted small">No AIS vessel identified: the craft were not transmitting.</p>'}
    <h4>Reports merged here</h4>
    <div class="mini-tl">${f.members.map((m) => `<div class="mini-tl-row fused-row"><span class="mini-date">${esc(fmtTime(m.t))}</span>
      <span>${srcChip(m.source)} <button class="link-inline" data-entity="${esc(m.id)}">${esc(TYPE[m.type] || m.type)} ${esc(m.id)}</button></span>
      <span class="muted">${esc(m.text || '')}${m.conf ? ` · ${pct(m.conf)}` : ''}</span></div>`).join('')}</div>
    <h4>Why the confidence goes up</h4>
    <p class="small">Each independent source counts once, with its most confident report. The chance that all of them are wrong together is ${f.sources.length} small numbers multiplied, so agreement between agencies and sensors makes the contact more credible than any single report. Several reports from the same agency count as one source.</p>
    ${can(s, 'incidents:create') ? `<div class="rec-actions"><button class="chip" data-act="incident-new" data-lat="${f.lat}" data-lon="${f.lon}" data-vessel="${esc(f.vessels[0]?.vessel_id || '')}">Report incident from this…</button></div>` : ''}
  </div>`;
}

export function mergeSection(s) {
  const sv = s.server;
  const fused = sv.fused || [];
  const dups = sv.duplicates?.candidates;
  const merges = sv.merges?.merges;
  const canMerge = can(s, 'incidents:update');
  return `<section class="card"><div class="card-head"><div><h3>Merged picture across agencies</h3>
      <p class="sub">Reports from different agencies and sensors about the same thing, brought together. Contacts merge automatically; incidents merge only when a person confirms.</p></div></div>
    <h3 class="section-title">Merged contacts · last 7 days</h3>
    <div class="table-scroll"><table class="data"><thead><tr><th>Contact</th><th>Where</th><th>When (UTC)</th><th>Sources</th><th class="num">Confidence</th><th>Reports</th></tr></thead>
      <tbody>${fused.map((f) => `<tr class="click" data-entity="${esc(f.id)}"><td><b>${esc(f.id)}</b></td><td class="small">${esc(f.zone || fmtCoord(f.lat, f.lon))}</td>
        <td class="small">${esc(dt(f.first))}</td><td>${f.sources.map(srcChip).join(' ')}</td>
        <td class="num"><b>${pct(f.confidence)}</b><div class="muted small">best single ${pct(f.single_best)}</div></td>
        <td class="small">${f.members.map((m) => esc(m.id)).join(', ')}</td></tr>`).join('') || '<tr><td colspan="6" class="empty">No reports close enough to merge.</td></tr>'}</tbody></table></div>
    <h3 class="section-title">Possible duplicate incidents</h3>
    ${dups ? (dups.length ? `<div class="dup-list">${dups.map((d) => dupCard(d, canMerge)).join('')}</div>` : '<p class="empty">No likely duplicates. Pairs you marked “not a duplicate” are not suggested again.</p>') : '<p class="muted">Loading…</p>'}
    <h3 class="section-title">Merge decisions</h3>
    ${merges ? `<div class="table-scroll"><table class="data compact"><thead><tr><th>When</th><th>Decision</th><th>Kept</th><th>Merged / compared</th><th>By</th><th>Note</th><th></th></tr></thead>
      <tbody>${merges.map((m) => `<tr class="${m.status === 'undone' ? 'is-revoked' : ''}"><td class="small">${esc(dt(m.decided_at))}</td>
        <td><span class="st st-${m.status === 'merged' ? 'verified' : m.status === 'rejected' ? 'closed' : 'reported'}">${esc(m.status === 'rejected' ? 'not a duplicate' : m.status)}</span></td>
        <td><button class="link-inline" data-entity="${esc(m.primary_id)}">${esc(m.primary_id)}</button></td><td><button class="link-inline" data-entity="${esc(m.duplicate_id)}">${esc(m.duplicate_id)}</button></td>
        <td class="small">${esc(m.by || '')}${m.undone_by_name ? ` · undone by ${esc(m.undone_by_name)}` : ''}</td><td class="small">${esc(m.note || '')}</td>
        <td>${m.status === 'merged' && canMerge ? `<button class="btn small-btn" data-act="unmerge" data-mid="${m.id}">Undo</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="7" class="empty">No decisions yet.</td></tr>'}</tbody></table></div>` : ''}
  </section>`;
}

export function dupCard(d, canMerge, compact = false) {
  const side = (x, label) => `<div class="dup-side"><span class="muted small">${label}</span>
    <button class="link-inline" data-entity="${esc(x.id)}"><b>${esc(x.id)}</b></button> ${srcChip(x.agency)}
    <div class="small">${esc(x.title)}</div><div class="muted small">${esc(dt(x.occurred_at))} · ${esc(x.place || fmtCoord(x.lat, x.lon))} · ${esc(x.status.replace('_', ' '))}</div></div>`;
  return `<article class="dup-card"><div class="dup-head"><b>${fmt(d.score * 100, 0)}% match</b><span class="muted small">${esc(d.reasons.join(' · '))}</span></div>
    ${compact ? '' : `<div class="dup-sides">${side(d.primary, 'Keep (reported first)')}${side(d.duplicate, 'Merge into it')}</div>`}
    ${canMerge ? `<div class="rec-actions"><button class="chip chip-go" data-act="merge" data-primary="${esc(d.primary.id)}" data-duplicate="${esc(d.duplicate.id)}">Merge ${esc(d.duplicate.id)} into ${esc(d.primary.id)}</button>
      <button class="chip" data-act="not-dup" data-a="${esc(d.primary.id)}" data-b="${esc(d.duplicate.id)}">Not a duplicate</button></div>` : '<p class="muted small">Your role can see but not merge incidents.</p>'}
  </article>`;
}

// inside the incident drawer
export function incidentMergeBlock(s, i) {
  const canMerge = can(s, 'incidents:update');
  const parts = [];
  if (i.merged_into) {
    parts.push(`<div class="banner merged-banner">This report was merged into <button class="link-inline" data-entity="${esc(i.merged_into)}"><b>${esc(i.merged_into)}</b></button>, which now represents the event in lists and statistics.
      ${i.merge && canMerge ? `<button class="btn small-btn" data-act="unmerge" data-mid="${i.merge.id}">Undo merge</button>` : ''}</div>`);
  }
  if (i.reports && i.reports.length > 1) {
    parts.push(`<h4>Reports of this event (${i.reports.length} · ${esc(i.agencies?.join(', ') || '')})</h4>
      ${i.reports.map((r) => `<div class="evidence${r.id === i.id ? ' is-current' : ''}"><div class="ev-head"><button class="link-inline" data-entity="${esc(r.id)}"><b>${esc(r.id)}</b></button>${srcChip(r.agency)}
        <span class="muted small">${r.is_primary ? 'primary' : 'merged'} · ${esc(dt(r.occurred_at))}</span>
        ${!r.is_primary && canMerge ? `<button class="btn small-btn ev-right" data-act="unmerge" data-mid="${(i.merges_in || []).find((m) => m.duplicate_id === r.id)?.id ?? ''}">Undo</button>` : ''}</div>
        <p class="small"><b>${esc(r.title)}</b> ${esc(r.description)}</p></div>`).join('')}`);
  }
  if (i.duplicate_candidates?.length) {
    parts.push(`<h4>Possible duplicates from other reports</h4>${i.duplicate_candidates.map((d) => dupCard(d, canMerge)).join('')}`);
  }
  return parts.join('');
}

// inside the vessel profile
export function consolidatedBlock(p) {
  const c = p.consolidated;
  if (!c) return '';
  const label = { name: 'Name', mmsi: 'MMSI', imo: 'IMO', flag: 'Flag', type: 'Type', length_m: 'Length (m)', call_sign: 'Call sign', destination: 'Destination' };
  return `<h4>Consolidated record ${c.conflicts ? `<span class="risk risk-medium">${c.conflicts} disagreement${c.conflicts > 1 ? 's' : ''}</span>` : '<span class="muted small">all sources agree</span>'}</h4>
    <table class="data compact consolidated"><thead><tr><th>Field</th><th>Value · according to</th></tr></thead>
      <tbody>${c.fields.map((f) => `<tr class="${f.conflict ? 'is-conflict' : ''}"><td>${esc(label[f.field] || f.field)}${f.conflict ? ' ⚠' : ''}</td>
        <td>${f.values.map((v) => `<div><b>${esc(v.value)}</b> <span class="muted small">${esc(v.sources.join(', '))}</span></div>`).join('')}</td></tr>`).join('')}</tbody></table>
    ${c.conflicts ? '<p class="muted small">Sources disagree: nothing is overwritten. Confirm the right value with the flag state or the reporting agency.</p>' : ''}`;
}

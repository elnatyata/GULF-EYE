// "Log a report" dialog: speak or type a note, the assistant (language model when available, rules otherwise)
// asks for anything missing, the operator confirms, then the record updates alerts, assets and advice.
import { esc, fmt, fmtQty, fmtDuration, fmtCoord, fmtTime, addMinutes } from './util.js';
import {
  parseNote, nextQuestion, applyAnswer, understood, draftFromLlm, REPORT_KINDS,
} from './parser.js';
import {
  asset, assetFuel, assetTask, missionPlan, alerts, openAlerts, alertById, alertTitle, nowIso, today, zone, capturedOf,
} from './model.js';
import { opWindow } from './rules.js';
import { api } from './api.js';

const OPS = [['dispatch', 'Send a patrol asset'], ['report', 'Field report'], ['resolve', 'Close an alert'], ['refuel', 'Refuelling']];

export function createCapture(app) {
  const root = document.getElementById('capture');
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let draft = null;
  let messages = [];
  let question = null;
  let channel = 'text';
  let recognition = null;
  let listening = false;
  let speakReplies = false;
  let llm = { available: false, model: null };
  let thinking = false;

  const s = () => app.state;

  function examples() {
    const top = openAlerts(s())[0];
    const list = [
      `Send Patrol Vessel Bravo to ${top ? top.id : 'D-041'}`,
      'Fishing vessel reports a fast skiff with no lights near Bonny, five men on board',
      'Refuelled the interceptor with 400 litres',
      'Close alert D-050 as a false alarm',
    ];
    return list;
  }

  async function probeLlm() {
    try {
      const res = await fetch('/api/status', { cache: 'no-store' });
      if (res.ok) llm = await res.json();
    } catch { llm = { available: false }; }
    if (!root.hidden) render();
  }

  function speak(text) {
    if (!speakReplies || !('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text.replace(/<[^>]+>/g, ''));
    u.lang = 'en-GB';
    u.rate = 1.02;
    window.speechSynthesis.speak(u);
  }

  function open(initialText = null) {
    draft = null;
    messages = [{ from: 'assistant', html: 'What happened at sea? Say it or type it, like a radio call. I will ask if something is missing, and nothing is saved until you confirm.' }];
    question = null;
    channel = 'text';
    thinking = false;
    root.hidden = false;
    render();
    root.querySelector('textarea')?.focus();
    probeLlm();
    if (initialText) { channel = 'text'; send(initialText); }
  }

  function close() {
    if (recognition && listening) recognition.abort();
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    root.hidden = true;
    root.innerHTML = '';
  }

  function say(html) { messages.push({ from: 'assistant', html }); }

  function advance() {
    question = nextQuestion(s(), draft);
    const got = understood(s(), draft);
    const lead = got ? `<span class="got">Understood: ${esc(got)}</span>` : '';
    if (question) {
      say(`${lead}${esc(question.text)}`);
      speak(question.text);
    } else {
      say(`${lead}Please check the record below and confirm.`);
      speak('Please check the record and confirm.');
    }
    render();
  }

  async function interpret(value) {
    if (!llm.available) return parseNote(s(), value);
    try {
      const st = s();
      const context = {
        alerts: alerts(st).filter((a) => a.state.code === 'new').slice(0, 12).map((a) => ({ id: a.id, title: alertTitle(st, a), risk: a.risk, lat: a.lat, lon: a.lon })),
        assets: st.assets.assets.map((a) => ({ id: a.id, name: a.name, type: a.type })),
        kinds: REPORT_KINDS.map(([k]) => k),
      };
      const res = await fetch('/api/interpret', { method: 'POST', headers: api.headers({ 'Content-Type': 'application/json' }), body: JSON.stringify({ text: value, context }) });
      if (!res.ok) throw new Error(String(res.status));
      const j = await res.json();
      return draftFromLlm(st, value, j);
    } catch {
      llm = { available: false };
      return parseNote(s(), value);
    }
  }

  async function send(text) {
    const value = text.trim();
    if (!value || thinking) return;
    messages.push({ from: 'farmer', html: esc(value), channel });
    thinking = true;
    render();
    draft = await interpret(value);
    thinking = false;
    draft.channel = channel;
    advance();
  }

  function answer(i) {
    const opt = question.options[i];
    messages.push({ from: 'farmer', html: esc(opt.label), channel: 'tap' });
    draft = applyAnswer(s(), draft, question.key, opt.value);
    advance();
  }

  function stamp() {
    const t = draft.time || fmtTime(nowIso(s()));
    return `${draft.date}T${t}:00Z`;
  }

  function targets() { return draft.alertIds.map((id) => alertById(s(), id)).filter(Boolean); }

  function summary() {
    const st = s();
    const rows = [];
    let warn = null;
    if (draft.type === 'dispatch' && draft.assetId && draft.alertIds.length) {
      const a = asset(st, draft.assetId);
      const plan = missionPlan(st, a, targets());
      rows.push(['Targets', draft.alertIds.join(', ')], ['Asset', `${a.name} · ${a.base}`],
        ['Distance', `${fmt(plan.totalNm, 0)} nm incl. return`], ['Arrival', `${fmtDuration(plan.etaFirstH)} at the first target`],
        ['Fuel', `${fmtQty(plan.level, 'L')} → <b class="${plan.feasible ? '' : 'neg'}">${fmtQty(plan.level - plan.fuelL, 'L')}</b>`]);
      if (assetTask(st, a)) warn = `${a.name} is already on task. Sending it again will replace its current job.`;
      else if (!plan.feasible) warn = `Not enough fuel for the round trip (needs ${fmtQty(plan.fuelL, 'L')} plus reserve, has ${fmtQty(plan.level, 'L')}). Choose another asset or refuel first.`;
      else if (!opWindow(st, a.op).evals[0].ok) warn = `Sea state today is above the limit for this asset (${opWindow(st, a.op).evals[0].reasons.join(', ')}).`;
    }
    if (draft.type === 'report') {
      rows.push(['Where', draft.place ? `${draft.place.name || 'Position'} · ${fmtCoord(draft.place.lat, draft.place.lon)}` : '–']);
      const z = draft.place ? st.geo.zones.find((x) => draft.place && inZone(st, x, draft.place)) : null;
      if (z) rows.push(['Zone', z.name]);
    }
    if (draft.type === 'resolve') {
      rows.push(['Alerts', draft.alertIds.join(', ') || '–'], ['Outcome', draft.outcome === 'false_alarm' ? 'False alarm' : 'Threat confirmed and handled']);
    }
    if (draft.type === 'refuel' && draft.assetId) {
      const a = asset(st, draft.assetId);
      const level = assetFuel(st, a.id);
      const after = level + (draft.amount || 0);
      rows.push(['Asset', a.name], ['Fuel', `${fmtQty(level, 'L')} → <b class="${after > a.capacity ? 'neg' : ''}">${fmtQty(after, 'L')}</b> of ${fmtQty(a.capacity, 'L')}`]);
      if (after > a.capacity) warn = `That is more than the ${fmtQty(a.capacity, 'L')} tank. Check the amount.`;
    }
    return { rows, warn };
  }

  function inZone(st, z, p) {
    // tiny point-in-polygon, same rule as the map
    let inside = false;
    const poly = z.poly;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
      const [xi, yi] = poly[i];
      const [xj, yj] = poly[j];
      if ((yi > p.lat) !== (yj > p.lat) && p.lon < ((xj - xi) * (p.lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function complete() {
    if (!draft) return false;
    if (draft.type === 'dispatch') return Boolean(draft.assetId && draft.alertIds.length);
    if (draft.type === 'report') return Boolean(draft.place && draft.kind);
    if (draft.type === 'resolve') return Boolean(draft.alertIds.length && draft.outcome);
    if (draft.type === 'refuel') return Boolean(draft.assetId && draft.amount > 0);
    return false;
  }

  function confirmCard() {
    const st = s();
    const { rows, warn } = summary();
    const assetOptions = st.assets.assets.map((a) => `<option value="${a.id}" ${draft.assetId === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('');
    const pool = draft.type === 'resolve' ? alerts(st).filter((a) => a.state.code !== 'resolved' && a.state.code !== 'false_alarm') : openAlerts(st);
    const groups = [...new Set(pool.map((a) => a.zoneId || 'none'))].map((zid) => {
      const z = zid === 'none' ? null : zone(st, zid);
      return `<div class="fg"><span class="fg-crop"><i style="background:${z ? z.color : '#8A97A6'}"></i>${esc(z ? z.name : 'Open sea')}</span>
        ${pool.filter((a) => (a.zoneId || 'none') === zid).map((a) => `<label class="fg-field"><input type="checkbox" data-alert="${a.id}" ${draft.alertIds.includes(a.id) ? 'checked' : ''}> ${esc(a.id)} <span>${esc(a.risk)}</span></label>`).join('')}</div>`;
    }).join('');
    const placeOptions = st.geo.ports.map((p) => `<option value="${p.id}" ${draft.place?.name === p.name ? 'selected' : ''}>${esc(p.name)}</option>`).join('')
      + (draft.place && !st.geo.ports.some((p) => p.name === draft.place.name) ? `<option value="custom" selected>${esc(draft.place.name || 'Position')}</option>` : '');
    const isAssetOp = draft.type === 'dispatch' || draft.type === 'refuel';
    return `<div class="confirm">
      <div class="confirm-grid">
        <label>Operation<select data-k="type">${OPS.map(([v, l]) => `<option value="${v}" ${draft.type === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
        ${isAssetOp ? `<label>Asset<select data-k="assetId"><option value="">Choose…</option>${assetOptions}</select></label>` : ''}
        ${draft.type === 'refuel' ? `<label>Litres added<span class="amount"><input type="number" step="any" min="0" data-k="amount" value="${draft.amount ?? ''}"><span>L</span></span></label>` : ''}
        ${draft.type === 'report' ? `<label>Place<select data-k="place"><option value="">Choose…</option>${placeOptions}</select></label>
          <label>What was seen<select data-k="kind"><option value="">Choose…</option>${REPORT_KINDS.map(([v, l]) => `<option value="${v}" ${draft.kind === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>` : ''}
        ${draft.type === 'resolve' ? `<label>Outcome<select data-k="outcome"><option value="false_alarm" ${draft.outcome === 'false_alarm' ? 'selected' : ''}>False alarm</option><option value="confirmed" ${draft.outcome === 'confirmed' ? 'selected' : ''}>Threat confirmed and handled</option></select></label>` : ''}
        <label>Date<input type="date" data-k="date" value="${draft.date}" max="${today(st)}">${draft.dateAssumed ? '<small class="hint">No date given: today assumed.</small>' : ''}</label>
        <label>Time (UTC)<input type="time" data-k="time" value="${draft.time || fmtTime(nowIso(st))}"></label>
      </div>
      ${draft.type === 'dispatch' || draft.type === 'resolve' ? `<div class="fields-pick"><div class="label">Alerts</div>${groups || '<span class="muted small">No alerts to choose.</span>'}</div>` : ''}
      <dl class="confirm-sum">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>
      ${warn ? `<div class="warn">${esc(warn)}</div>` : ''}
      <div class="confirm-note">Saving adds this to the incident log, updates the fleet and refreshes the advice.</div>
      <div class="confirm-actions">
        <button class="btn ghost" data-cap="restart">Start over</button>
        <button class="btn primary" data-cap="save" ${complete() ? '' : 'disabled'}>Confirm and save</button>
      </div>
    </div>`;
  }

  function render() {
    const typed = root.querySelector('textarea')?.value || '';   // keep what the operator is typing across re-renders
    const hadFocus = document.activeElement?.tagName === 'TEXTAREA' && root.contains(document.activeElement);
    const done = draft && !question && !thinking;
    root.innerHTML = `<div class="modal-box" role="dialog" aria-modal="true" aria-label="Log a report">
      <div class="modal-head"><div><h2>Log a report</h2><p class="sub">${llm.available ? `🎙 Voice assistant · language model reads your note (${esc(llm.model || '')})` : '🎙 Voice assistant · rule-based reader (works offline)'}</p></div>
        <div class="head-actions"><button class="icon-btn${speakReplies ? ' is-on-soft' : ''}" data-cap="speak" aria-label="Read answers aloud" title="Read the assistant's questions aloud">🔊</button>
        <button class="icon-btn" data-cap="close" aria-label="Close">✕</button></div></div>
      <div class="convo">${messages.map((m) => `<div class="msg msg-${m.from}">${m.from === 'farmer' && m.channel === 'voice' ? '<span class="via">🎙 voice</span>' : ''}${m.html}</div>`).join('')}
        ${thinking ? '<div class="msg msg-assistant"><span class="dots"><i></i><i></i><i></i></span></div>' : ''}
        ${question ? `<div class="answers">${question.options.map((o, i) => `<button class="chip" data-answer="${i}">${esc(o.label)}</button>`).join('')}</div>` : ''}
      </div>
      ${done ? confirmCard() : `
      <div class="composer">
        <textarea rows="2" placeholder="e.g. Send Patrol Vessel Bravo to the dark vessel near Bonny"></textarea>
        <button class="icon-btn mic${listening ? ' is-on' : ''}" data-cap="mic" ${SR ? '' : 'disabled title="Voice input needs Chrome"'} aria-label="Speak">🎙</button>
        <button class="btn primary" data-cap="send">Send</button>
      </div>
      ${draft ? '' : `<div class="examples"><span class="muted small">Try:</span>${examples().map((e, i) => `<button class="chip" data-example="${i}">${esc(e)}</button>`).join('')}</div>`}`}
    </div>`;
    const convo = root.querySelector('.convo');
    convo.scrollTop = convo.scrollHeight;
    const box = root.querySelector('textarea');
    if (box && typed) box.value = typed;
    if (box && hadFocus) requestAnimationFrame(() => box.focus());
  }

  function toggleMic() {
    if (!SR) return;
    if (listening) { recognition.stop(); return; }
    recognition = new SR();
    recognition.lang = 'en-GB';
    recognition.interimResults = true;
    let heard = '';
    recognition.onresult = (e) => {
      heard = Array.from(e.results).map((r) => r[0].transcript).join('');
      const box = root.querySelector('textarea');
      if (box) box.value = heard;
    };
    recognition.onerror = (e) => {
      listening = false;
      say(`Voice input stopped (${esc(e.error)}). You can type the note instead.`);
      render();
    };
    recognition.onend = () => {
      listening = false;
      if (heard.trim()) { channel = 'voice'; send(heard); } else render();
    };
    listening = true;
    recognition.start();
    render();
  }

  function save() {
    const st = s();
    const time = stamp();
    const base = { time, campaign: draft.date, status: 'done', source: 'captured', channel: draft.channel || 'text', raw: draft.raw };
    const stampId = Date.now();
    let events = [];
    let adjust = {};
    let flash = [];
    let message = '';
    if (draft.type === 'dispatch') {
      const a = asset(st, draft.assetId);
      const list = targets();
      const plan = missionPlan(st, a, list);
      events = list.map((al) => ({
        ...base, id: `cap-${stampId}-${al.id}`, type: 'dispatch', entity: al.id, assetId: a.id,
        eta_h: plan.legs.find((l) => l.id === al.id)?.etaH ?? plan.etaFirstH, dist_nm: plan.totalNm, fuel_l: plan.fuelL / list.length,
      }));
      adjust = { [a.id]: -plan.fuelL };
      flash = list.map((al) => al.id);
      message = `${a.name} sent to ${list.map((x) => x.id).join(', ')}. Fleet and advice updated.`;
    } else if (draft.type === 'report') {
      const n = capturedOf(st, 'report').length + 1;
      const id = `R-${String(n).padStart(3, '0')}`;
      events = [{ ...base, id: `cap-${stampId}-${id}`, type: 'report', entity: id, lat: draft.place.lat, lon: draft.place.lon, place: draft.place.name, kind: draft.kind }];
      flash = [id];
      message = `Field report ${id} saved. Nearby alerts re-checked.`;
    } else if (draft.type === 'resolve') {
      events = draft.alertIds.map((id) => ({ ...base, id: `cap-${stampId}-${id}`, type: 'resolve', entity: id, outcome: draft.outcome }));
      flash = [...draft.alertIds];
      message = `${draft.alertIds.join(', ')} closed (${draft.outcome === 'false_alarm' ? 'false alarm' : 'handled'}). Assets are free again.`;
    } else if (draft.type === 'refuel') {
      const a = asset(st, draft.assetId);
      events = [{ ...base, id: `cap-${stampId}-${a.id}`, type: 'refuel', entity: null, assetId: a.id, litres: draft.amount }];
      adjust = { [a.id]: draft.amount };
      message = `${a.name} refuelled: ${fmtQty(draft.amount, 'L')} added.`;
    }
    app.commit({ events, adjust, flash, message });
    close();
  }

  root.addEventListener('click', (ev) => {
    const t = ev.target.closest('button');
    if (ev.target === root) { close(); return; }
    if (!t) return;
    if (t.dataset.example != null) { channel = 'text'; send(examples()[Number(t.dataset.example)]); return; }
    if (t.dataset.answer != null) { answer(Number(t.dataset.answer)); return; }
    const action = t.dataset.cap;
    if (action === 'close') close();
    if (action === 'send') { channel = 'text'; send(root.querySelector('textarea').value); }
    if (action === 'mic') toggleMic();
    if (action === 'restart') open();
    if (action === 'save') save();
    if (action === 'speak') { speakReplies = !speakReplies; if (!speakReplies && 'speechSynthesis' in window) window.speechSynthesis.cancel(); render(); }
  });

  root.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey && ev.target.tagName === 'TEXTAREA') {
      ev.preventDefault();
      channel = 'text';
      send(ev.target.value);
    }
    if (ev.key === 'Escape') close();
  });

  root.addEventListener('change', (ev) => {
    const el = ev.target;
    const st = s();
    if (!el.dataset.alert && !el.dataset.k) return;   // e.g. the note box losing focus: nothing to update
    if (el.dataset.alert) {
      draft.alertIds = el.checked ? [...new Set([...draft.alertIds, el.dataset.alert])] : draft.alertIds.filter((id) => id !== el.dataset.alert);
    } else if (el.dataset.k) {
      const k = el.dataset.k;
      if (k === 'amount') draft.amount = el.value === '' ? null : Number(el.value);
      else if (k === 'place') {
        const p = st.geo.ports.find((x) => x.id === el.value);
        if (p) draft.place = { name: p.name, lat: p.lat, lon: p.lon };
        else if (el.value === '') draft.place = null;
      } else if (k === 'kind') draft.kind = el.value || null;
      else if (k === 'assetId') draft.assetId = el.value || null;
      else draft[k] = el.value;
      if (k === 'date') draft.dateAssumed = false;
      if (k === 'type') { draft.alertIds = []; draft.outcome = draft.outcome || 'false_alarm'; }
    }
    render();
  });

  return { open, close, isOpen: () => !root.hidden, speak: (text) => { const was = speakReplies; speakReplies = true; speak(text); speakReplies = was; } };
}

'use strict';
/* SideQuest front-end (single file; the FastAPI app serves only this page).
   Talks to: POST /api/plans, GET /api/runs/{id}, POST /api/runs/{id}/answer,
   POST /api/trips/{id}/replan, POST /api/trips/{id}/decision. In ?replay=1 it plays
   fixtures/demo/replay-run.json instead. All dynamic text is set with textContent / text nodes. */
const $ = id => document.getElementById(id);
const REPLAY = new URLSearchParams(location.search).get('replay') === '1';
const REPLAY_PATHS = ['fixtures/demo/replay-run.json', '../fixtures/demo/replay-run.json'];
const S = { pos: null, tripId: null, current: null, view: null, runId: null, busy: false, replay: false,
            events: [], lastSeq: 0, pollTimer: null, nextIdx: 0, watchId: null, focus: -1 };
let map = null, layer = null, markers = [], meMarker = null;

/* ---------- icons: constant markup only, never data ---------- */
const ICONS = {
  compass: '<circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/>',
  pin: '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  unlock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
  swap: '<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
  map: '<path d="M1 6v16l7-4 8 4 7-4V2l-7 4-8-4-7 4z"/><path d="M8 2v16"/><path d="M16 6v16"/>',
  list: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
  chat: '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
  chev: '<path d="M6 9l6 6 6-6"/>',
  sun: '<circle cx="12" cy="12" r="5"/><path d="M12 1v2"/><path d="M12 21v2"/><path d="M4.22 4.22l1.42 1.42"/><path d="M18.36 18.36l1.42 1.42"/><path d="M1 12h2"/><path d="M21 12h2"/><path d="M4.22 19.78l1.42-1.42"/><path d="M18.36 5.64l1.42-1.42"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  refresh: '<path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/><path d="M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  play: '<polygon points="5 3 19 12 5 21 5 3"/>',
  nav: '<polygon points="3 11 22 2 13 21 11 13 3 11"/>',
  wallet: '<path d="M12 1v22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  rain: '<path d="M16 13v8"/><path d="M8 13v8"/><path d="M12 15v8"/><path d="M20 16.58A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
  route: '<circle cx="6" cy="19" r="3"/><path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"/><circle cx="18" cy="5" r="3"/>'
};
function ic(name) {
  const doc = new DOMParser().parseFromString(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || '') + '</svg>', 'image/svg+xml');
  const s = document.importNode(doc.documentElement, true);
  s.setAttribute('class', 'ic');
  return s;
}
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c);
  return e;
}
const show = id => $(id).classList.remove('hide');
const hide = id => $(id).classList.add('hide');
const clear = id => { $(id).replaceChildren(); return $(id); };
function put(id, ...kids) { const n = clear(id); n.append(...kids.flat().filter(Boolean)); return n; }
function iconText(name, text) { return [ic(name), h('span', null, text)]; }

/* ---------- theme ---------- */
function applyTheme(t) {
  if (t) document.documentElement.setAttribute('data-theme', t); else document.documentElement.removeAttribute('data-theme');
  const dark = t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  put('themeBtn', ic(dark ? 'sun' : 'moon'));
}
function initTheme() {
  let t = null; try { t = localStorage.getItem('sq-theme'); } catch (e) {}
  applyTheme(t === 'dark' || t === 'light' ? t : null);
  $('themeBtn').onclick = () => {
    const cur = document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    applyTheme(next); try { localStorage.setItem('sq-theme', next); } catch (e) {}
  };
}

/* ---------- location (required) ---------- */
function setLoc(title, small, cls) {
  const box = clear('loc'); box.className = 'loc ' + cls;
  box.append(h('span', { class: 'dot' }), h('div', null, h('strong', null, title), h('small', null, small)));
}
function getLocation() {
  if (!navigator.geolocation) { setLoc('Location unavailable.', 'This browser cannot share location.', 'bad'); return; }
  setLoc('Asking for your location…', 'Your browser will ask for permission.', '');
  navigator.geolocation.getCurrentPosition(p => {
    S.pos = p.coords;
    setLoc('Location ready.', p.coords.latitude.toFixed(4) + ', ' + p.coords.longitude.toFixed(4) + ' · accurate to ±' + Math.round(p.coords.accuracy) + ' m', 'ok');
    $('go').disabled = false;
  }, e => {
    S.pos = null; $('go').disabled = true;
    setLoc('Location is required to plan.', e.code === 1 ? 'Permission was denied. Allow location for this site, then try again.' : (e.message || 'Could not determine your position.'), 'bad');
  }, { enableHighAccuracy: true, timeout: 15000 });
}

/* ---------- API helper ---------- */
async function api(path, body, method) {
  let r;
  const headers = Object.assign({ 'Content-Type': 'application/json' }, S.ownerToken ? { Authorization: 'Bearer ' + S.ownerToken } : {});
  try {
    r = await fetch(path, { method: method || (body === undefined ? 'GET' : 'POST'), headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) { throw new Error("Couldn't reach the server. Make sure it is running, then try again."); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? 'Check the form values.' : 'The server rejected the request.';
    const e = new Error(r.status === 429 ? 'The planner is busy with another run. Wait for it to finish, then try again.'
      : r.status === 503 ? 'The AI planner is not connected (no model configured on the server).' : d);
    e.status = r.status; e.body = j; throw e;
  }
  return j;
}

/* ---------- planning flow ---------- */
function readForm() {
  const rain = $('rain').value;
  return { request: $('req').value.trim(), lat: S.pos.latitude, lon: S.pos.longitude,
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone, minutes: +$('mins').value || 120,
    budget_dollars: $('budget').value === '' ? null : +$('budget').value, no_budget_limit: $('nolimit').checked,
    mode: $('mode').value, data: $('data').value, avoid_rain_pct: rain === '' ? null : +rain };
}
function formMsg(text, cls) { const m = $('msg'); m.textContent = text || ''; m.className = cls || ''; }
$('formCard').addEventListener('submit', ev => { ev.preventDefault(); startPlan(); });

async function startPlan() {
  if (!S.pos) { getLocation(); return; }
  const len = $('req').value.trim().length;
  const err = $('reqErr');
  if (len < 3) { err.textContent = 'Describe your outing in a few words first.'; err.classList.remove('hide'); $('req').focus(); return; }
  err.classList.add('hide'); formMsg('');
  if (S.mode === 'group') { createGroup(); return; }
  $('go').disabled = true;
  S.current = null; S.view = null; S.tripId = null; stopQuest();
  beginRun('The agent is working');
  try {
    const j = await api('/api/plans', readForm());
    S.tripId = j.trip_id; S.ownerToken = j.owner_token;
    lsSet(soloKey(j.trip_id), j.owner_token); lsSet('sq-last-trip', j.trip_id);
    track(j.run_id);
  } catch (e) { endRun(); showCompose(); formMsg(e.message, 'field-err'); $('go').disabled = !S.pos; }
}

function beginRun(title) {
  S.busy = true; S.events = []; S.lastSeq = 0; clear('events');
  $('workTitle').textContent = title; show('working'); show('skeleton'); show('spinner');
  hide('compose'); if (S.view || S.current) show('workspace'); else hide('workspace');
  if (S.current || S.view) hide('skeleton');
  $('working').scrollIntoView({ block: 'nearest' });
  refreshControls();
}
function endRun() { S.busy = false; hide('working'); refreshControls(); }

function track(runId) {
  S.runId = runId;
  streamRun(runId).then(ok => { if (!ok) pollRun(runId); });
}
// Live progress over server-sent events. fetch() is used (not EventSource) so the auth header works.
async function streamRun(runId) {
  try {
    const r = await fetch('/api/runs/' + runId + '/stream', { headers: S.ownerToken ? { Authorization: 'Bearer ' + S.ownerToken } : {} });
    if (!r.ok || !r.body) return false;
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = '', finished = false;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = (block.match(/^event: (.*)$/m) || [])[1], data = (block.match(/^data: (.*)$/m) || [])[1];
        if (ev === 'progress' && data) appendEvents([JSON.parse(data)]);
        if (ev === 'done') finished = true;
      }
    }
    if (!finished) return false;
    pollRun(runId);  // the run is done: fetch its result once
    return true;
  } catch (e) { return false; }
}
function pollRun(runId) {
  api('/api/runs/' + runId).then(j => {
    appendEvents(j.events || []);
    if (!j.done) { S.pollTimer = setTimeout(() => pollRun(runId), 1500); return; }
    endRun();
    if (j.error) { runFailed('The run failed: ' + j.error); return; }
    if (!j.result) { runFailed('The run finished without a result. Try again.'); return; }
    showResult(j.result, {});
  }).catch(e => { endRun(); runFailed(e.message); });
}

function runFailed(text) {
  if (S.current || S.view) { show('workspace'); put('notice', notice('err', 'alert', text)); }
  else { showCompose(); formMsg(text, 'field-err'); $('go').disabled = !S.pos; }
}

const KIND = { tool_call: 'Searching and checking', tool_result: '', final: 'Done', limit: 'Stopped', error: 'Problem', cancelled: 'Cancelled' };
function appendEvents(events) {
  for (const e of events) {
    if (e.seq <= S.lastSeq) continue;
    S.lastSeq = e.seq; S.events.push(e);
    if (e.kind === 'tool_call') continue;  // the result line says what happened
    $('events').append(h('li', { class: e.kind || '' }, h('span', null, e.summary || '')));
  }
  const l = $('events'); l.scrollTop = l.scrollHeight;
}

/* ---------- results ---------- */
function notice(kind, icon, text) { return h('div', { class: 'notice ' + kind, role: kind === 'err' ? 'alert' : 'status' }, ic(icon), h('span', null, text)); }
function showCompose() { hide('workspace'); hide('working'); hide('groupView'); hide('joinView'); show('compose'); hide('newBtn'); document.body.classList.remove('has-ws'); }

function showResult(res, opts) {
  S.view = res; if (res.trip_id) S.tripId = res.trip_id;
  if (res.proposal && !res.awaiting_decision) S.current = res;
  S.replay = !!opts.replay; S.nextIdx = 0; S.focus = -1;
  hide('compose'); hide('working'); show('workspace'); show('newBtn');
  document.body.classList.add('has-ws');
  const shown = res.proposal ? res : (S.current || res);
  renderTripBar(res, shown); clear('notice'); renderClarify(res); renderDiff(res);
  renderPlan(res, shown); renderMap(shown); renderQuest(shown, res); refreshControls();
  const hist = clear('feedHist');
  for (const e of S.events) if (e.kind !== 'tool_call') hist.append(h('li', { class: e.kind || '' }, h('span', null, e.summary || '')));
  if (!res.proposal) {
    const msgs = { limit_reached: 'The planner ran out of time or steps before saving a plan. Try a simpler request.',
      cancelled: 'The run was cancelled.', model_error: 'The AI planner had an error. Try again in a moment.' };
    if (!res.clarification) put('notice', notice('warn', 'alert', res.message || msgs[res.status] || 'The planner did not produce a plan.'));
  }
  if (!S.replay) refreshSaveBox();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderTripBar(res, shown) {
  const c = res.constraints || shown.constraints, bar = clear('tripbar');
  const chip = (cls, icon, text) => h('span', { class: 'chip ' + cls }, ic(icon), text);
  if (c) {
    const hrs = c.minutes >= 60 ? (c.minutes / 60).toFixed(c.minutes % 60 ? 1 : 0) + ' h' : c.minutes + ' min';
    bar.append(chip('', 'clock', c.window + ' · ' + hrs), chip('', 'route', c.mode),
      chip('', 'wallet', c.no_budget_limit ? 'No budget limit' : c.budget_dollars == null ? 'Budget not set' : '$' + c.budget_dollars + ' budget'),
      chip('', 'rain', c.avoid_rain_pct == null ? "Rain: don't mind" : 'Avoid rain over ' + c.avoid_rain_pct + '%'));
  }
  const w = res.weather || shown.weather;
  if (w) bar.append(w.available ? chip('brand', 'rain', 'Forecast rain chance ' + (w.min_pct === w.max_pct ? w.max_pct + '%' : w.min_pct + '–' + w.max_pct + '%'))
                                : chip('warn', 'rain', 'Forecast unavailable for this time'));
  const d = (shown.data || res.data);
  if (d) bar.append(d.synthetic ? chip('warn', 'alert', 'DEMO DATA: invented venues') : chip('ok', 'shield', 'Live data'));
  const mc = $('modeChip');
  if (S.replay) { mc.textContent = 'Replay'; mc.className = 'chip warn'; }
  else if (d) { mc.textContent = d.synthetic ? 'Demo data' : 'Live'; mc.className = 'chip ' + (d.synthetic ? 'warn' : 'ok'); }
}

function renderClarify(res) {
  const box = clear('clarify');
  const q = res.clarification;
  if (!q) return;
  const err = h('p', { class: 'field-err hide', role: 'alert', id: 'askErr' });
  const input = h('input', { type: 'text', id: 'askText', maxlength: '300', placeholder: 'Type a different answer…', 'aria-label': 'Your own answer' });
  const buttons = [];
  const send = async (payload, btn) => {
    buttons.forEach(b => b.disabled = true); input.disabled = true; err.classList.add('hide');
    try {
      const j = await api('/api/runs/' + S.runId + '/answer', payload);
      beginRun('Applying your answer'); track(j.run_id);
    } catch (e) { buttons.forEach(b => b.disabled = false); input.disabled = false; err.textContent = e.message; err.classList.remove('hide'); }
  };
  const list = h('div', { class: 'choices' });
  for (const c of q.choices || []) {
    const b = h('button', { type: 'button', onclick: () => send({ choice: c.index }, b) }, c.kind === 'text' ? ic('chat') : ic('check'), h('span', null, c.label));
    buttons.push(b); list.append(b);
  }
  const sendBtn = h('button', { type: 'button', class: 'secondary', onclick: () => { const t = input.value.trim(); if (!t) { err.textContent = 'Type an answer or pick an option.'; err.classList.remove('hide'); input.focus(); return; } send({ text: t }, sendBtn); } }, 'Send');
  buttons.push(sendBtn);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); sendBtn.click(); } });
  const card = h('section', { class: 'card ask', 'aria-labelledby': 'askTitle' },
    h('h2', { id: 'askTitle' }, ic('help'), 'One question first'), h('p', { class: 'expl' }, q.question || 'The planner needs a bit more to go on.'),
    list, h('label', { class: 'lbl', for: 'askText' }, 'Or answer in your own words'), h('div', { class: 'answer-row' }, input, sendBtn), err);
  if (res.conflict && res.conflict.message) card.append(h('div', { class: 'conflict' }, 'Why: ' + res.conflict.message));
  box.append(card);
}

function renderDiff(res) {
  const box = clear('diffCard');
  if (!res.awaiting_decision || !res.diff) return;
  const d = res.diff;
  const li = (cls, label, items, fmt) => items.length ? items.map(i => h('li', null, h('span', { class: 'mini ' + cls }, label), h('span', null, fmt(i)))) : [];
  const err = h('p', { class: 'field-err hide', role: 'alert' });
  const decide = async which => {
    box.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
      const j = await api('/api/trips/' + S.tripId + '/decision', { decision: which });
      S.current = null; showResult(j.result, {});
    } catch (e) { box.querySelectorAll('button').forEach(b => b.disabled = false); err.textContent = e.message; err.classList.remove('hide'); }
  };
  box.append(h('section', { class: 'card diff', 'aria-labelledby': 'diffTitle' },
    h('h2', { id: 'diffTitle' }, ic('refresh'), 'Proposed change'), h('p', { class: 'sub' }, 'Review before it replaces your saved plan: ' + d.summary + '.'),
    h('ul', { class: 'difflist' },
      li('new', 'Added', d.added, i => i.name + ' · ' + i.when), li('moved', 'Moved', d.moved, i => i.name + ': ' + i.from + ' → ' + i.to),
      li('warn', 'Removed', d.removed, i => i.name + ' · ' + i.when),
      d.unchanged.length ? h('li', null, h('span', { class: 'mini' }, 'Unchanged'), h('span', null, d.unchanged.length + ' stop' + (d.unchanged.length === 1 ? '' : 's'))) : null),
    h('div', { class: 'decide' }, h('button', { type: 'button', onclick: () => decide('accept') }, ic('check'), 'Accept change'),
      h('button', { type: 'button', class: 'ghost', onclick: () => decide('reject') }, 'Keep my current plan')), err));
}

function legsFor(blocks, legs) {
  const ids = blocks.length && blocks.every(b => b.place_id);
  const into = blocks.map((b, i) => ids ? legs.find(l => l.to === b.place_id) : legs[i]);
  const last = ids ? legs.find(l => !blocks.some(b => b.place_id === l.to)) : legs[blocks.length];
  return { into, last };
}
function legNode(leg, label) {
  if (!leg) return null;
  return h('li', { class: 'leg' }, ic('route'), h('span', null, (leg.mode || 'travel') + (label ? ' ' + label : '') + ' · leave ' + leg.depart + ', there by ' + leg.latest_arrival), h('span', { class: 'line' }));
}
function factChip(f) {
  const names = { opening_hours: 'Hours', price: 'Price' };
  if (!names[f.field]) return null;
  const txt = { verified: 'verified', unverified: 'from map data', unknown: 'unknown' }[f.status] || f.status;
  return h('span', { class: 'mini ' + (f.status === 'unknown' ? 'warn' : f.status === 'verified' ? 'ok' : '') }, names[f.field] + ': ' + txt);
}

const CONF_SUB = { community_data: 'against community map data', unverified: 'opening hours and prices are unknown', mixed: 'some facts rest on community map data' };
function confLabel(overall, conf) {
  const label = { checked: 'Checked', provisional: 'Provisional', failed: 'Not valid' }[overall] || overall;
  const sub = (overall === 'checked' && CONF_SUB[conf]) || { checked: 'all checks passed', provisional: 'some facts are unknown', failed: 'breaks a constraint' }[overall] || '';
  return label + ' · ' + sub;
}
function verifyBlock(items) {
  if (!items || !items.length) return null;
  const fl = { opening_hours: 'opening hours', price: 'price', step_free: 'step-free access' };
  return h('div', { class: 'verify' }, h('div', { class: 'top' }, ic('shield'), h('strong', null, 'Verify before you go')),
    h('p', { class: 'help' }, 'These facts come from community map data. Confirm them with the venue.'),
    h('ul', null, items.map(v => h('li', null, v.name + ': ' + (fl[v.field] || v.field) + ' ',
      (v.url && /^https:\/\//.test(v.url)) ? h('a', { href: v.url, target: '_blank', rel: 'noopener noreferrer' }, 'check the source') : h('span', { class: 'help' }, '(no source link)')))));
}

function renderPlan(res, shown) {
  const p = shown.proposal;
  const cand = !!res.awaiting_decision;
  clear('pBadges'); clear('timeline'); clear('issues'); clear('notes'); clear('spend');
  if (!p) {
    $('pTitle').textContent = 'No plan yet'; $('pExpl').textContent = res.clarification ? 'Answer the question above and the planner will continue.' : 'Nothing to show.';
    $('pMeta').textContent = ''; return;
  }
  $('pTitle').textContent = S.replay ? 'Your plan (replay)' : cand ? 'Proposed plan' : 'Your plan';
  put('pBadges', h('span', { class: 'badge b-' + p.overall }, ic(p.overall === 'checked' ? 'check' : p.overall === 'failed' ? 'alert' : 'help'), confLabel(p.overall, p.confidence)),
    h('span', { class: 'badge b-info' }, p.checks_passed + ' checks passed'));
  $('pExpl').textContent = p.explanation || '';
  const bits = [];
  if (typeof shown.tool_calls === 'number') bits.push(shown.tool_calls + ' tool calls');
  if (typeof shown.tokens === 'number') bits.push(shown.tokens.toLocaleString() + ' tokens');
  $('pMeta').textContent = bits.join(' · ');

  const t = p.totals;
  if (t) {
    const range = t.low === t.high ? '$' + t.low.toFixed(0) : '$' + t.low.toFixed(0) + '–$' + t.high.toFixed(0);
    const box = h('div', { class: 'spend' }, h('strong', null, 'Estimated spend: ' + range + (t.cap != null ? ' of $' + t.cap.toFixed(0) : '')),
      t.has_unknown ? h('div', { class: 'help' }, 'Some prices are unknown, so the real total could be higher.') : null);
    if (t.cap) {
      const pct = Math.min(100, Math.round(t.high / t.cap * 100));
      box.append(h('div', { class: 'bar', role: 'img', 'aria-label': pct + ' percent of budget at the high estimate' }, h('i', { class: t.high > t.cap ? 'over' : '', style: 'width:' + pct + '%' })));
    }
    put('spend', box);
  }

  const diff = res.diff || {}, newIds = new Set((diff.added || []).map(x => x.id)), movedIds = new Set((diff.moved || []).map(x => x.id));
  const { into, last } = legsFor(p.blocks, p.legs || []);
  const canEdit = canEditNow(res);
  const tl = $('timeline');
  p.blocks.forEach((b, i) => {
    const lg = legNode(into[i], i === 0 ? 'from your start' : '');
    if (lg) tl.append(lg);
    const facts = (b.facts || []).map(factChip).filter(Boolean);
    const chips = h('div', { class: 'chips' }, h('span', { class: 'mini' }, ic('wallet'), b.cost || ''), facts,
      b.step_free === true ? h('span', { class: 'mini ok' }, 'Step-free') : b.step_free === false ? h('span', { class: 'mini warn' }, 'Not step-free') : null,
      b.shortened ? h('span', { class: 'mini warn' }, ic('clock'), 'Shortened to fit hours') : null,
      b.locked ? h('span', { class: 'mini moved' }, ic('lock'), 'Locked') : null,
      newIds.has(b.id) ? h('span', { class: 'mini new' }, 'New') : null, movedIds.has(b.id) ? h('span', { class: 'mini moved' }, 'Moved') : null);
    const acts = h('div', { class: 'actions' },
      h('button', { class: 'icon', type: 'button', 'aria-pressed': b.locked ? 'true' : 'false', 'aria-label': (b.locked ? 'Unlock ' : 'Lock ') + b.name,
        title: b.locked ? 'Unlock this stop' : 'Lock this stop in place', disabled: !canEdit, onclick: () => replan([{ type: b.locked ? 'unlock' : 'lock', block_id: b.id }]) }, ic(b.locked ? 'lock' : 'unlock')),
      h('button', { class: 'small ghost', type: 'button', disabled: !canEdit, 'aria-label': 'Swap ' + b.name, onclick: () => replan([{ type: 'swap_stop', block_id: b.id }]) }, ic('swap'), 'Swap'),
      h('button', { class: 'small danger', type: 'button', disabled: !canEdit, 'aria-label': 'Remove ' + b.name, onclick: () => replan([{ type: 'remove_stop', block_id: b.id }]) }, ic('trash'), 'Remove'),
      b.lat != null ? h('button', { class: 'small ghost', type: 'button', 'aria-label': 'Show ' + b.name + ' on the map', onclick: () => focusStop(i, true) }, ic('pin'), 'Map') : null);
    const dur = durationMin(b.start, b.end);
    const card = h('li', { class: 'stop' + (b.locked ? ' locked' : ''), id: 'stop' + i, 'data-i': i },
      h('div', { class: 'when' }, b.start, h('small', null, 'to ' + b.end + (dur ? ' · ' + dur + ' min' : ''))),
      h('div', null, h('h3', { 'aria-level': '2' }, h('span', { class: 'num' }, String(i + 1)), b.name), chips, S.replay ? null : acts));
    card.addEventListener('click', ev => { if (!ev.target.closest('button')) focusStop(i, false); });
    tl.append(card);
  });
  const fin = legNode(last, 'back to your start');
  if (fin) tl.append(fin);

  for (const i of p.issues || []) $('issues').append(h('div', { class: 'issue ' + i.status }, ic(i.status === 'fail' ? 'alert' : 'help'), h('span', null, i.message)));
  const vb = verifyBlock(p.verify); if (vb) $('issues').append(vb);
  for (const n of p.notes || []) $('notes').append(h('div', { class: 'note' }, ic('shield'), h('span', null, n)));
  if (S.replay) { $('ctlHint').textContent = 'Editing is off in replay mode.'; }
}
function durationMin(a, b) {
  const [h1, m1] = a.split(':').map(Number), [h2, m2] = b.split(':').map(Number);
  const d = (h2 * 60 + m2) - (h1 * 60 + m1); return d > 0 ? d : 0;
}

/* ---------- assistant controls (structured changes only) ---------- */
function canEditNow(res) {
  res = res || S.view;
  return !!(S.tripId && !S.replay && !S.busy && S.current && res && !res.awaiting_decision && !res.clarification);
}
function refreshControls() {
  const ok = canEditNow();
  for (const id of ['lateSel', 'lateBtn', 'timeSel', 'timeBtn', 'budSel', 'budNo', 'budBtn']) $(id).disabled = !ok;
  let hint = 'Change something and the planner re-plans around it. Locked stops stay put.';
  if (S.replay) hint = 'Editing is off in replay mode.';
  else if (!S.current) hint = 'Save a plan first, then adjust it here.';
  else if (S.busy) hint = 'Working…';
  else if (S.view && S.view.awaiting_decision) hint = 'Accept or keep your plan before changing anything else.';
  else if (S.view && S.view.clarification) hint = 'Answer the question first.';
  $('ctlHint').textContent = hint;
  document.querySelectorAll('.stop .actions button[aria-label]').forEach(b => { if (!b.getAttribute('aria-label').startsWith('Show')) b.disabled = !ok; });
}
async function replan(changes) {
  if (!canEditNow()) return;
  try {
    S.busy = true; refreshControls();
    const j = await api('/api/trips/' + S.tripId + '/replan', { changes });
    if (j.immediate) { S.busy = false; S.current = null; showResult(j.result, {}); return; }
    S.busy = false; beginRun('Re-planning around your change'); track(j.run_id);
  } catch (e) { S.busy = false; refreshControls(); put('notice', notice('err', 'alert', e.message)); }
}
$('lateBtn').onclick = () => replan([{ type: 'running_late', minutes: +$('lateSel').value }]);
$('timeBtn').onclick = () => replan([{ type: 'set_minutes', minutes: +$('timeSel').value }]);
$('budBtn').onclick = () => {
  const no = $('budNo').checked, v = $('budSel').value;
  if (!no && v === '') { put('notice', notice('warn', 'help', 'Enter a budget, or tick “No budget limit”.')); $('budSel').focus(); return; }
  replan([{ type: 'set_budget', budget_dollars: no ? null : +v, no_budget_limit: no }]);
};

/* ---------- map (never allowed to break the plan) ---------- */
function pinIcon(label, cls) { return L.divIcon({ className: '', html: '<div class="pin ' + (cls || '') + '">' + label + '</div>', iconSize: [30, 30], iconAnchor: [15, 15] }); }
function renderMap(shown) {
  const origin = shown.origin, blocks = (shown.proposal && shown.proposal.blocks) || [];
  try {
    if (typeof L === 'undefined') throw new Error('map library failed to load');
    if (!map) { map = L.map('map'); L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' }).addTo(map); }
    if (layer) layer.remove();
    layer = L.layerGroup().addTo(map); markers = []; meMarker = null;
    setTimeout(() => { try { map.getContainer().querySelectorAll('svg').forEach(s => s.setAttribute('aria-hidden', 'true')); } catch (e) {} }, 100);
    const pts = [];
    if (origin && origin.lat != null) { pts.push([origin.lat, origin.lon]); L.marker([origin.lat, origin.lon], { icon: pinIcon('S', 'home'), keyboard: false }).bindTooltip('Start / end').addTo(layer); }
    blocks.forEach((b, i) => {
      if (b.lat == null || b.lon == null) { markers[i] = null; return; }
      pts.push([b.lat, b.lon]);
      const m = L.marker([b.lat, b.lon], { icon: pinIcon(String(i + 1)), keyboard: false }).bindTooltip(b.name).addTo(layer);
      m.on('click', () => focusStop(i, false)); markers[i] = m;
    });
    for (let i = 1; i < pts.length; i++) L.polyline([pts[i - 1], pts[i]], { color: '#2459e6', weight: 2, opacity: .5, dashArray: '6 6' }).addTo(layer);
    if (pts.length) map.fitBounds(pts, { padding: [34, 34], maxZoom: 16 });
    setTimeout(() => { try { map.invalidateSize(); } catch (e) {} }, 80);
    hide('mapNote');
  } catch (e) { show('mapNote'); $('mapNote').textContent = 'Map could not load (' + e.message + '). Your plan is unaffected.'; }
}
function focusStop(i, pan) {
  S.focus = i;
  document.querySelectorAll('.stop').forEach((s, k) => s.classList.toggle('focus', k === i));
  markers.forEach((m, k) => { if (m && m._icon) { const d = m._icon.firstChild; if (d) d.classList.toggle('focus', k === i); } });
  const b = S.view && S.current && (S.view.proposal ? S.view : S.current).proposal.blocks[i];
  if (pan && b && b.lat != null && map) {
    if (matchMedia('(max-width:999px)').matches) setTab('map');
    try { map.flyTo([b.lat, b.lon], Math.max(map.getZoom(), 16)); markers[i] && markers[i].openTooltip(); } catch (e) {}
  } else { const el = $('stop' + i); if (el && !pan) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
}

/* ---------- quest (browser-only tracking) ---------- */
function stopQuest() { if (S.watchId != null && navigator.geolocation) navigator.geolocation.clearWatch(S.watchId); S.watchId = null; }
function haversine(a, b, c, d) { const R = 6371000, r = x => x * Math.PI / 180, dp = r(c - a), dl = r(d - b);
  const k = Math.sin(dp / 2) ** 2 + Math.cos(r(a)) * Math.cos(r(c)) * Math.sin(dl / 2) ** 2; return 2 * R * Math.asin(Math.sqrt(k)); }
const fmtDist = m => m < 1000 ? Math.round(m) + ' m' : (m / 1000).toFixed(1) + ' km';
function questPlan() { const v = S.current || S.view; return v && v.proposal ? v.proposal : null; }
function renderQuest(shown, res) {
  stopQuest(); S.nextIdx = 0;
  const q = $('quest'); q.classList.remove('active');
  const p = shown.proposal;
  if (!p || (res && res.awaiting_decision)) { q.classList.add('hide'); return; }
  q.classList.remove('hide');
  put('questBtn', ic('play'), 'Start Quest'); $('questBtn').classList.remove('hide');
  put('arrivedBtn', ic('check'), "I've arrived"); $('arrivedBtn').classList.add('hide');
  put('privacy', ic('shield'), h('span', null, 'Live tracking runs only in your browser to measure the distance to your next stop. Your location is not sent anywhere.'));
  questHeadline(); $('questInfo').textContent = '';
}
function questHeadline() {
  const p = questPlan(); if (!p) return;
  document.querySelectorAll('.stop').forEach((s, i) => s.classList.toggle('next', i === S.nextIdx && $('quest').classList.contains('active')));
  const b = p.blocks[S.nextIdx];
  $('questNext').textContent = b ? 'Next: ' + b.name + ' · ' + b.start : 'Quest complete';
}
$('questBtn').onclick = () => {
  const p = questPlan(); if (!p || !navigator.geolocation) return;
  $('questBtn').classList.add('hide'); $('arrivedBtn').classList.remove('hide'); $('quest').classList.add('active');
  $('questInfo').textContent = 'Getting your position…'; questHeadline();
  S.watchId = navigator.geolocation.watchPosition(pos => {
    const c = pos.coords;
    try { if (map && layer) { if (!meMarker) meMarker = L.circleMarker([c.latitude, c.longitude], { radius: 8, color: '#c2410c', fillOpacity: .85 }).addTo(layer); else meMarker.setLatLng([c.latitude, c.longitude]); } } catch (e) {}
    const b = p.blocks[S.nextIdx];
    if (!b) { $('questInfo').textContent = 'All stops done. Head home.'; return; }
    if (b.lat == null) { $('questInfo').textContent = 'Next: ' + b.name + ' at ' + b.start + ' (no map pin for this stop)'; return; }
    const m = haversine(c.latitude, c.longitude, b.lat, b.lon);
    $('questInfo').textContent = m < 60 ? "You're at " + b.name + '. Enjoy until ' + b.end + '.' : fmtDist(m) + ' to ' + b.name + ' (' + b.start + ')';
  }, e => { $('questInfo').textContent = 'Lost your location: ' + (e.message || 'unknown error'); }, { enableHighAccuracy: true, maximumAge: 5000 });
};
$('arrivedBtn').onclick = () => {
  const p = questPlan(); S.nextIdx++; questHeadline();
  if (!p || S.nextIdx >= p.blocks.length) { $('questInfo').textContent = 'All stops done. Head back to your start.'; $('arrivedBtn').classList.add('hide'); stopQuest(); }
};

/* ---------- tabs (mobile) ---------- */
function setTab(t) {
  document.body.setAttribute('data-tab', t);
  document.querySelectorAll('#tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === t)));
  if (t === 'map' && map) setTimeout(() => { try { map.invalidateSize(); } catch (e) {} }, 60);
}
document.querySelectorAll('#tabs button').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));

/* ---------- replay (recorded run; no server, no model) ---------- */
async function loadReplayFixture() {
  let last = null;
  for (const path of REPLAY_PATHS) {
    try { const r = await fetch(path); if (!r.ok) { last = path + ' → HTTP ' + r.status; continue; } return await r.json(); }
    catch (e) { last = path + ' → ' + e.message; }
  }
  throw new Error('Replay fixture not found. Tried: ' + REPLAY_PATHS.join(', ') + ' (last error: ' + last + ').');
}
function initReplay() {
  document.body.classList.add('replay'); S.replay = true; document.title = 'SideQuest — replay';
  $('modeChip').textContent = 'Replay'; $('modeChip').className = 'chip warn';
  hide('compose'); show('working'); $('workTitle').textContent = 'Replaying a recorded run';
  loadReplayFixture().then(fx => {
    const evs = fx.events || []; let i = 0;
    const step = () => {
      if (i < evs.length) { appendEvents([evs[i]]); i++; setTimeout(step, 700); return; }
      hide('working');
      if (!fx.result) { put('notice', notice('err', 'alert', 'Replay fixture has no result to render.')); show('workspace'); return; }
      showResult(fx.result, { replay: true });
      put('notice', notice('warn', 'play', 'Replay complete. Everything above came from the recorded file: ' + ((fx.meta && fx.meta.label) || 'recorded run') + '.'));
    };
    step();
  }).catch(e => { hide('working'); show('workspace'); put('notice', notice('err', 'alert', e.message)); });
}

/* =====================================================================
   GROUP TRIPS. Roles come from the member token on the server; the client only stores its own
   token. Others' budgets/needs are never sent to this browser. Polling every ~3 s (no websockets).
   ===================================================================== */
const G = { gid: null, token: null, timer: null, state: null, built: false, sig: {} };
const gTokenKey = gid => 'sq-g-' + gid;
function gLoadToken(gid) { try { return localStorage.getItem(gTokenKey(gid)); } catch (e) { return null; } }
function gSaveToken(gid, t) { try { localStorage.setItem(gTokenKey(gid), t); } catch (e) {} }
const pad2 = n => String(n).padStart(2, '0');
function toLocalInput(iso) { const d = new Date(iso); return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + 'T' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()); }
function fromLocalInput(v) { return new Date(v).toISOString(); }
function fmtRange(a, b) {
  const A = new Date(a), B = new Date(b), t = d => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return A.toLocaleDateString([], { weekday: 'short' }) + ' ' + t(A) + '–' + t(B);
}
async function gapi(path, method, body) {
  let r;
  try {
    r = await fetch(path, { method: method || 'GET', headers: Object.assign({ 'Content-Type': 'application/json' }, G.token ? { Authorization: 'Bearer ' + G.token } : {}),
      body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) { throw new Error("Couldn't reach the server. Make sure it is running."); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? 'Please check the values you entered.' : 'The server rejected the request.';
    const e = new Error(r.status === 429 ? 'The planner is busy with another run. Try again in a moment.' : r.status === 503 ? 'The AI planner is not connected on the server.' : d);
    e.status = r.status; throw e;
  }
  return j;
}
function gChanged(key, value) { const s = JSON.stringify(value); if (G.sig[key] === s) return false; G.sig[key] = s; return true; }

/* ---- create (from the compose form) ---- */
function setMode(m) {
  S.mode = m;
  $('formCard').classList.toggle('group-mode', m === 'group');
  $('modeSolo').setAttribute('aria-pressed', String(m === 'solo')); $('modeGroup').setAttribute('aria-pressed', String(m === 'group'));
  $('formTitle').textContent = m === 'group' ? 'Plan with friends' : 'Plan an outing';
  $('go').textContent = m === 'group' ? 'Create group trip' : 'Plan my outing';
}
async function createGroup() {
  const name = $('gNameIn').value.trim(), title = $('gTitleIn').value.trim();
  if (!name || !title) { formMsg('Give the trip a name and enter your own name.', 'field-err'); return; }
  $('go').disabled = true; formMsg('');
  try {
    const j = await gapi('/api/groups', 'POST', { title, request: $('req').value.trim(), lat: S.pos.latitude, lon: S.pos.longitude,
      tz: Intl.DateTimeFormat().resolvedOptions().timeZone, data: $('data').value, display_name: name });
    gSaveToken(j.group_id, j.member_token); history.replaceState(null, '', '?g=' + j.group_id); enterGroup(j.group_id);
  } catch (e) { formMsg(e.message, 'field-err'); }
  $('go').disabled = !S.pos;
}

/* ---- join ---- */
async function showJoin(token) {
  ['compose', 'working', 'workspace', 'groupView'].forEach(hide); show('joinView');
  try {
    const info = await gapi('/api/invites/' + encodeURIComponent(token));
    $('joinTitle').textContent = 'You are invited to “' + info.title + '” (' + info.members + ' already in).';
    $('joinBtn').disabled = false;
    $('joinBtn').onclick = async () => {
      const name = $('joinName').value.trim();
      if (!name) { $('joinMsg').textContent = 'Enter your name first.'; show('joinMsg'); return; }
      $('joinBtn').disabled = true; hide('joinMsg');
      try {
        const j = await gapi('/api/invites/' + encodeURIComponent(token) + '/join', 'POST', { name });
        gSaveToken(j.group_id, j.member_token); history.replaceState(null, '', '?g=' + j.group_id); enterGroup(j.group_id);
      } catch (e) { $('joinMsg').textContent = e.message; show('joinMsg'); $('joinBtn').disabled = false; }
    };
  } catch (e) { $('joinTitle').textContent = e.status === 404 ? 'This invite link is invalid or was revoked. Ask the organizer for a new one.' : e.message; }
}

/* ---- group view ---- */
function enterGroup(gid) {
  G.gid = gid; G.token = gLoadToken(gid); G.built = false; G.state = null; G.sig = {};
  ['compose', 'working', 'workspace', 'joinView'].forEach(hide); show('groupView'); show('newBtn');
  $('modeChip').textContent = 'Group trip'; $('modeChip').className = 'chip brand';
  if (!G.token) { $('gTitle').textContent = 'Group trip'; $('gRequest').textContent = 'You are not signed in to this group on this device. Open your invite link to join.'; clear('gMembers'); return; }
  gPoll();
}
async function gPoll() {
  clearTimeout(G.timer);
  try { renderGroup(await gapi('/api/groups/' + G.gid)); }
  catch (e) {
    if (e.status === 401 || e.status === 404) { $('gRequest').textContent = e.message; put('gPlan', notice('err', 'alert', e.message)); return; }
  }
  G.timer = setTimeout(gPoll, document.hidden ? 8000 : 3000);
}
function renderGroup(st) {
  G.state = st;
  const org = st.me.role === 'organizer';
  $('gTitle').textContent = st.group.title; $('gRequest').textContent = '“' + st.group.request + '”';
  $('gRole').textContent = org ? 'You are the organizer' : 'Member';
  document.title = 'SideQuest — ' + st.group.title;
  // invite + edit (organizer only; the server only sends the token to the organizer)
  $('gInvite').classList.toggle('hide', !org); $('gEdit').classList.toggle('hide', !org);
  if (org) {
    const url = st.group.invite_token ? location.origin + location.pathname + '?join=' + st.group.invite_token : '';
    $('gInviteUrl').value = url || '(link revoked)'; $('gCopy').disabled = !url;
    $('gInviteToggle').textContent = url ? 'Revoke link' : 'Create a new link';
    if (document.activeElement !== $('gReqIn') && !$('gEdit').open) $('gReqIn').value = st.group.request;
  }
  if (gChanged('members', st.members)) renderMembers(st);
  if (!G.built) { buildGroupForm(st.me.inputs); G.built = true; }
  if (gChanged('questions', st.questions)) renderQuestions(st);
  if (gChanged('plan', [st.run, st.candidate, st.accepted, st.needs_replan, st.group.version])) renderGroupPlan(st);
  if (gChanged('save', [st.accepted && st.accepted.seq, st.candidate && st.candidate.seq, st.share_token, st.me.role])) renderGroupSave(st);
}

function renderMembers(st) {
  const org = st.me.role === 'organizer';
  put('gMembers', st.members.map(m => {
    const wins = (m.windows || []).map(w => fmtRange(w.start, w.end)).join(' · ');
    const li = h('li', { class: 'mem' },
      h('div', { class: 'top' }, h('strong', null, m.name + (m.is_me ? ' (you)' : '')), h('span', { class: 'mini ' + (m.role === 'organizer' ? 'moved' : '') }, m.role),
        m.submitted ? h('span', { class: 'mini ok' }, ic('check'), 'Availability shared') : h('span', { class: 'mini warn' }, 'Waiting for availability'),
        m.budget_status ? h('span', { class: 'mini ' + (m.budget_status === 'within budget' ? 'ok' : 'warn') }, m.budget_status) : null),
      wins ? h('div', { class: 'help' }, ic('clock'), ' ' + wins) : null,
      (m.interests && m.interests.length) ? h('div', { class: 'chips' }, m.interests.map(i => h('span', { class: 'mini' }, i))) : null,
      (org && !m.is_me) ? h('div', { class: 'actions' }, h('button', { class: 'small danger', type: 'button', 'aria-label': 'Remove ' + m.name, onclick: async () => {
        if (!confirm('Remove ' + m.name + ' from the group? Their access ends immediately.')) return;
        try { await gapi('/api/groups/' + G.gid + '/members/' + m.id, 'DELETE'); G.sig = {}; gPoll(); } catch (e) { put('gPlan', notice('err', 'alert', e.message)); }
      } }, ic('trash'), 'Remove')) : null);
    return li;
  }));
}

/* ---- my inputs form (built once, so polling never overwrites what you are typing) ---- */
function winRow(start, end) {
  const row = h('div', { class: 'winrow' },
    h('div', null, h('label', { class: 'lbl', style: 'margin-top:0' }, 'From'), h('input', { type: 'datetime-local', value: start || '', 'data-k': 'start' })),
    h('div', null, h('label', { class: 'lbl', style: 'margin-top:0' }, 'Until'), h('input', { type: 'datetime-local', value: end || '', 'data-k': 'end' })),
    h('button', { class: 'icon', type: 'button', 'aria-label': 'Remove this time', onclick: () => row.remove() }, ic('trash')));
  return row;
}
function buildGroupForm(inp) {
  const box = clear('gWindows');
  const wins = (inp && inp.windows && inp.windows.length) ? inp.windows : [];
  if (wins.length) wins.forEach(w => box.append(winRow(toLocalInput(w.start), toLocalInput(w.end))));
  else { const a = new Date(Date.now() + 3600e3); a.setMinutes(0, 0, 0); box.append(winRow(toLocalInput(a), toLocalInput(new Date(a.getTime() + 3 * 3600e3)))); }
  $('gAddWin').onclick = () => { if (box.children.length < 5) box.append(winRow('', '')); };
  if (inp) {
    $('gBudget').value = inp.budget_dollars == null ? '' : inp.budget_dollars; $('gNoLimit').checked = !!inp.no_budget_limit;
    document.querySelectorAll('input[name="gmode"]').forEach(c => { c.checked = (inp.transport || []).includes(c.value); });
    $('gInterests').value = (inp.interests || []).join(', '); $('gDiet').value = (inp.dietary || []).join(', ');
    $('gStepFree').checked = !!inp.step_free; $('gRain').value = inp.avoid_rain_pct == null ? '' : String(inp.avoid_rain_pct);
  }
  $('gNoLimit').onchange = () => { $('gBudget').disabled = $('gNoLimit').checked; }; $('gBudget').disabled = $('gNoLimit').checked;
}
const splitList = v => v.split(',').map(x => x.trim()).filter(Boolean);
$('gForm').addEventListener('submit', async ev => {
  ev.preventDefault();
  const msg = $('gSaveMsg'); msg.className = 'help'; msg.textContent = 'Saving…';
  const windows = [];
  for (const row of $('gWindows').children) {
    const s = row.querySelector('[data-k="start"]').value, e = row.querySelector('[data-k="end"]').value;
    if (!s && !e) continue;
    if (!s || !e || new Date(e) <= new Date(s)) { msg.className = 'field-err'; msg.textContent = 'Each time needs a start and a later end.'; return; }
    windows.push({ start: fromLocalInput(s), end: fromLocalInput(e) });
  }
  const modes = [...document.querySelectorAll('input[name="gmode"]:checked')].map(c => c.value);
  if (!modes.length) { msg.className = 'field-err'; msg.textContent = 'Pick at least one way of getting around.'; return; }
  try {
    await gapi('/api/groups/' + G.gid + '/me/inputs', 'PUT', { windows, budget_dollars: $('gBudget').value === '' || $('gNoLimit').checked ? null : +$('gBudget').value,
      no_budget_limit: $('gNoLimit').checked, transport: modes, car_seats: null, interests: splitList($('gInterests').value), dietary: splitList($('gDiet').value),
      step_free: $('gStepFree').checked, avoid_rain_pct: $('gRain').value === '' ? null : +$('gRain').value });
    msg.textContent = 'Saved. Everyone sees your availability within a few seconds.'; G.sig = {}; gPoll();
  } catch (e) { msg.className = 'field-err'; msg.textContent = e.message; }
});
$('gCopy').onclick = async () => { try { await navigator.clipboard.writeText($('gInviteUrl').value); $('gInviteMsg').textContent = 'Copied.'; } catch (e) { $('gInviteUrl').select(); $('gInviteMsg').textContent = 'Press Ctrl+C to copy.'; } };
$('gInviteToggle').onclick = async () => {
  const enable = $('gInviteToggle').textContent.startsWith('Create');
  try { await gapi('/api/groups/' + G.gid + '/invite', 'POST', { enabled: enable }); G.sig = {}; gPoll(); } catch (e) { $('gInviteMsg').textContent = e.message; }
};
$('gReqSave').onclick = async () => {
  try { await gapi('/api/groups/' + G.gid, 'PATCH', { request: $('gReqIn').value.trim() }); G.sig = {}; gPoll(); } catch (e) { put('gPlan', notice('err', 'alert', e.message)); }
};

/* ---- questions: yes / no / suggest another time ---- */
function renderQuestions(st) {
  const box = clear('gQuestions');
  for (const q of st.questions.filter(q => q.mine && q.status === 'pending')) {
    const err = h('p', { class: 'field-err hide', role: 'alert' });
    const send = async payload => { try { const r = await gapi('/api/groups/' + G.gid + '/questions/' + q.id + '/answer', 'POST', payload);
        G.sig = {}; gPoll(); } catch (e) { err.textContent = e.message; err.classList.remove('hide'); } };
    const a = new Date(q.start), b = new Date(q.end);
    const s = h('input', { type: 'datetime-local', value: toLocalInput(a), 'aria-label': 'Alternative start' }), e = h('input', { type: 'datetime-local', value: toLocalInput(b), 'aria-label': 'Alternative end' });
    const alt = h('div', { class: 'hide' }, h('div', { class: 'row2' }, h('div', null, h('label', { class: 'lbl' }, 'From'), s), h('div', null, h('label', { class: 'lbl' }, 'Until'), e)),
      h('button', { type: 'button', class: 'small', style: 'margin-top:10px', onclick: () => send({ answer: 'alt', alt_start: fromLocalInput(s.value), alt_end: fromLocalInput(e.value) }) }, 'Send my time'));
    box.append(h('section', { class: 'card ask' }, h('h2', null, ic('help'), 'A question for you'),
      h('p', { class: 'expl' }, q.text), h('p', { class: 'help' }, 'The window above fits everyone else. Your answer is shared with the organizer.'),
      h('div', { class: 'choices' }, h('button', { type: 'button', onclick: () => send({ answer: 'yes' }) }, ic('check'), 'Yes, I can make it'),
        h('button', { type: 'button', onclick: () => send({ answer: 'no' }) }, ic('trash'), "No, I can't"),
        h('button', { type: 'button', onclick: () => alt.classList.toggle('hide') }, ic('clock'), 'Suggest another time')), alt, err));
  }
  if (st.me.role === 'organizer') {
    const others = st.questions.filter(q => !q.mine);
    if (others.length) box.append(h('section', { class: 'card' }, h('h2', null, 'Questions sent'), h('ul', { class: 'mlist' }, others.map(q =>
      h('li', { class: 'mem' }, h('div', { class: 'top' }, h('strong', null, q.for), h('span', { class: 'mini ' + (q.status === 'yes' || q.status === 'alt' ? 'ok' : q.status === 'no' ? 'warn' : '') },
        { pending: 'waiting for an answer', yes: 'said yes', no: "can't make it", alt: 'suggested another time' }[q.status] || q.status)), h('div', { class: 'help' }, q.text))))));
  }
}

/* ---- plan: organizer controls, run progress, proposal, accepted plan ---- */
const QSTATUS = { pending: 'asked, waiting for an answer', yes: 'said yes', no: "can't make it", alt: 'suggested another time' };
function planBlock(plan, st, opts) {
  const org = st.me.role === 'organizer';
  const stops = h('div', null, plan.blocks.map(b => h('div', { class: 'gstop' }, h('div', { class: 'when' }, b.start + '–' + b.end),
    h('div', null, h('strong', null, b.name), h('div', { class: 'chips' }, h('span', { class: 'mini' }, ic('wallet'), b.cost || ''),
      (b.facts || []).map(factChip).filter(Boolean), b.step_free === true ? h('span', { class: 'mini ok' }, 'Step-free') : null)))));
  const people = h('div', { class: 'peoplebox' }, plan.people.map(p => {
    const mine = p.my_cost ? h('div', null, 'Your estimated cost: ' + (p.my_cost.low === p.my_cost.high ? '$' + p.my_cost.low.toFixed(0) : '$' + p.my_cost.low.toFixed(0) + '–$' + p.my_cost.high.toFixed(0))
      + (p.my_cost.cap != null ? ' of your $' + p.my_cost.cap.toFixed(0) : '') + (p.my_cost.has_unknown ? ' (some prices unknown)' : '')) : null;
    const fit = p.fit.total ? 'Interest match: ' + (p.fit.matched.length ? p.fit.matched.join(', ') : 'no listed interest matched') + ' (' + p.fit.matched.length + ' of ' + p.fit.total + ' interests, keyword match)' : 'Interest match: no interests listed';
    return h('div', { class: 'person' }, h('div', { class: 'top', style: 'display:flex;gap:8px;align-items:center;flex-wrap:wrap' }, h('strong', null, p.name + (p.id === st.me.id ? ' (you)' : '')),
      h('span', { class: 'mini ' + (p.budget_status === 'within budget' ? 'ok' : 'warn') }, p.budget_status)), mine, h('div', { class: 'help' }, fit));
  }));
  const left = plan.excluded.length ? h('div', { class: 'notice warn', style: 'margin-top:12px' }, ic('help'),
    h('span', null, 'Not in this plan: ' + plan.excluded.map(x => x.name + ' (' + (QSTATUS[x.question] || 'not asked yet') + ')').join(', ') + '.')) : null;
  const decide = (opts.decide && org) ? h('div', { class: 'decide' },
    h('button', { type: 'button', disabled: plan.stale, onclick: () => decideGroup('accept') }, ic('check'), 'Accept this plan'),
    h('button', { type: 'button', class: 'ghost', onclick: () => decideGroup('reject') }, 'Discard')) : null;
  return h('div', { class: plan.stale ? 'card stale' : '', style: plan.stale ? '' : 'margin-top:12px' },
    h('h2', null, opts.title), h('div', { class: 'badges' }, h('span', { class: 'badge b-' + plan.overall }, confLabel(plan.overall, plan.confidence) + ' · ' + plan.checks_passed + ' checks passed'),
      h('span', { class: 'badge b-info' }, ic('clock'), plan.window), plan.data.synthetic ? h('span', { class: 'badge b-demo' }, 'DEMO DATA') : null,
      plan.stale ? h('span', { class: 'badge b-provisional' }, 'Stale: inputs changed') : null),
    plan.stale ? h('div', { class: 'notice warn', style: 'margin-top:10px' }, ic('alert'), h('span', null, 'Someone changed their availability or preferences after this plan was made. ' + (org ? 'Plan again to include the latest.' : 'The organizer needs to plan again.'))) : null,
    h('p', { class: 'expl' }, plan.explanation), left, stops, h('h2', { style: 'margin-top:16px' }, 'Each person'), people,
    (plan.issues || []).length ? h('div', { class: 'issues' }, plan.issues.map(i => h('div', { class: 'issue ' + i.status }, ic(i.status === 'fail' ? 'alert' : 'help'), h('span', null, i.message)))) : null,
    verifyBlock(plan.verify), decide, h('div', { class: 'notes' }, (plan.notes || []).map(n => h('div', { class: 'note' }, ic('shield'), h('span', null, n)))));
}
function renderGroupPlan(st) {
  const org = st.me.role === 'organizer', run = st.run, kids = [];
  const running = run && !run.done;
  if (org) {
    kids.push(h('div', null, h('button', { id: 'gPlanBtn', type: 'button', disabled: running, onclick: planForEveryone }, ic('route'), running ? 'Planning…' : 'Plan for everyone'),
      h('p', { class: 'help' }, 'Picks the window that fits the most people and plans one outing for everyone in it. Splitting up is not supported.')));
    if (st.needs_replan) kids.push(notice('warn', 'refresh', 'Someone answered a question but a new plan could not start automatically. Press “Plan for everyone”.'));
  }
  if (running) {
    kids.push(h('div', { class: 'feedhead', style: 'margin-top:12px' }, h('div', { class: 'spinner' }), h('strong', null, 'The agent is working')),
      h('ol', { class: 'feed' }, (run.events || []).map(e => h('li', { class: e.kind || '' }, h('span', null, e.summary)))));
  } else if (run) {
    if (run.error) kids.push(notice('err', 'alert', 'The run failed: ' + run.error));
    else if (!st.candidate && run.message && run.status !== 'proposal_saved') kids.push(notice('warn', 'alert', run.message));
  }
  if (st.candidate) kids.push(planBlock(st.candidate, st, { title: org ? 'Proposed plan (not saved yet)' : 'Proposed plan (the organizer will review)', decide: true }));
  if (st.accepted) kids.push(planBlock(st.accepted, st, { title: 'Group plan', decide: false }));
  if (!st.candidate && !st.accepted && !running && !run) kids.push(h('p', { class: 'sub', style: 'margin-top:10px' }, org ? 'Once at least one person has shared availability, press “Plan for everyone”.' : 'The organizer will plan once everyone has shared their availability.'));
  put('gPlan', kids);
}
async function planForEveryone() {
  $('gPlanBtn').disabled = true;
  try { await gapi('/api/groups/' + G.gid + '/plan', 'POST', {}); G.sig = {}; gPoll(); }
  catch (e) { put('gPlan', notice('err', 'alert', e.message)); G.sig = {}; setTimeout(gPoll, 1500); }
}
async function decideGroup(which) {
  try { await gapi('/api/groups/' + G.gid + '/decision', 'POST', { decision: which }); G.sig = {}; gPoll(); }
  catch (e) { put('gPlan', notice('err', 'alert', e.message)); G.sig = {}; setTimeout(gPoll, 2500); }
}

/* =====================================================================
   SAVED PLANS: history, restore, share, export, delete (solo and group).
   Plans are saved on the server; this browser keeps only its own owner / member token.
   ===================================================================== */
const soloKey = id => 'sq-solo-' + id;
function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }
function safeLink(url, text) { return (url && /^https:\/\//.test(url)) ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, text) : null; }
async function downloadWithAuth(path, filename, token) {
  const r = await fetch(path, { headers: token ? { Authorization: 'Bearer ' + token } : {} });
  if (!r.ok) throw new Error('Could not download the file (' + r.status + ').');
  const url = URL.createObjectURL(await r.blob());
  const a = h('a', { href: url, download: filename }); document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
const KIND_LABEL = { plan: 'first plan', replan: 'change', proposal: 'proposal' };
function historyRows(versions, onRestore) {
  if (!versions.length) return [h('li', { class: 'help' }, 'No saved versions yet.')];
  return versions.map(v => h('li', { class: 'mem' },
    h('div', { class: 'top' }, h('strong', null, 'Version ' + v.seq), h('span', { class: 'mini ' + (v.current ? 'ok' : '') }, v.current ? 'current' : v.status),
      h('span', { class: 'mini' }, v.kind === 'restore' ? 'restored from v' + v.restored_from : (KIND_LABEL[v.kind] || v.kind))),
    h('div', { class: 'help' }, (v.stops || []).join(' → ') || 'no stops'), v.change ? h('div', { class: 'help' }, v.change) : null,
    (onRestore && !v.current && v.kind !== 'proposal' && v.status !== 'rejected')
      ? h('div', { class: 'actions' }, h('button', { class: 'small ghost', type: 'button', onclick: () => onRestore(v.seq) }, ic('refresh'), 'Restore this version')) : null));
}
function shareUrl(token) { return location.origin + location.pathname + '?share=' + token; }

/* ---- solo ---- */
function setShare(token) {
  $('shareUrl').value = token ? shareUrl(token) : ''; $('shareCopy').disabled = !token;
  $('shareToggle').textContent = token ? 'Revoke link' : 'Create link';
}
async function refreshSaveBox() {
  const live = !S.replay && S.tripId && S.ownerToken;
  $('saveBox').classList.toggle('hide', !live);
  if (!live) return;
  const p = questPlan(), links = (p && p.directions) || [], dl = clear('dirLinks');
  if (links.length) dl.append(h('ul', { class: 'dirlist' }, links.map(d => h('li', null, safeLink(d.url, d.label) || d.label))));
  else dl.append(h('span', { class: 'help' }, 'No directions links for this plan.'));
  try {
    const [hist, trip] = await Promise.all([api('/api/trips/' + S.tripId + '/history'), api('/api/trips/' + S.tripId)]);
    put('histList', historyRows(hist.versions, restoreVersion)); setShare(trip.share_token);
  } catch (e) { $('shareMsg').textContent = e.message; }
}
async function restoreVersion(seq) {
  if (!confirm('Restore version ' + seq + '? It is re-checked against your current time and budget, then saved as a new version.')) return;
  try { const j = await api('/api/trips/' + S.tripId + '/restore', { seq }); S.current = null; showResult(j.result, {}); }
  catch (e) {
    const more = e.body && e.body.issues ? ' ' + e.body.issues.map(i => i.message).join(' ') : '';
    put('notice', notice('err', 'alert', e.message + more));
  }
}
$('icsBtn').onclick = () => downloadWithAuth('/api/trips/' + S.tripId + '/export.ics', 'sidequest-plan.ics', S.ownerToken).catch(e => put('notice', notice('err', 'alert', e.message)));
$('shareToggle').onclick = async () => {
  const enable = $('shareToggle').textContent.startsWith('Create');
  try { const j = await api('/api/trips/' + S.tripId + '/share', { enabled: enable }); setShare(j.share_token); } catch (e) { $('shareMsg').textContent = e.message; }
};
$('shareCopy').onclick = async () => { try { await navigator.clipboard.writeText($('shareUrl').value); $('shareMsg').textContent = 'Link copied.'; } catch (e) { $('shareUrl').select(); } };
$('delTrip').onclick = async () => {
  if (!confirm('Delete this trip, its history and any share link? This cannot be undone.')) return;
  try {
    await api('/api/trips/' + S.tripId, undefined, 'DELETE');
    lsDel(soloKey(S.tripId)); lsDel('sq-last-trip'); stopQuest();
    S.current = S.view = S.tripId = S.ownerToken = null; showCompose(); $('go').disabled = !S.pos;
  } catch (e) { put('notice', notice('err', 'alert', e.message)); }
};
async function resumeLastTrip() {
  const id = lsGet('sq-last-trip'), tok = id && lsGet(soloKey(id));
  if (!id || !tok) return;
  S.ownerToken = tok;
  try { const j = await api('/api/trips/' + id); if (!j.result) throw new Error('empty'); S.current = null; showResult(j.result, {}); }
  catch (e) { S.ownerToken = null; lsDel('sq-last-trip'); lsDel(soloKey(id)); }
}

/* ---- read-only shared page ---- */
async function showShare(token) {
  ['compose', 'working', 'workspace', 'groupView', 'joinView'].forEach(hide); show('shareView'); hide('newBtn');
  $('modeChip').textContent = 'Shared'; $('modeChip').className = 'chip brand';
  try {
    const r = await fetch('/api/shared/' + encodeURIComponent(token));
    const v = await r.json();
    if (!r.ok) throw new Error(typeof v.detail === 'string' ? v.detail : 'This link is not available.');
    document.title = 'SideQuest — ' + v.title;
    $('shTitle').textContent = v.title; $('shMeta').textContent = [v.date, v.window].filter(Boolean).join(' · ');
    put('shBadges', h('span', { class: 'badge b-info' }, ic('shield'), 'Read-only view'), v.synthetic ? h('span', { class: 'badge b-demo' }, 'DEMO DATA') : null,
      h('span', { class: 'badge b-' + (v.confidence === 'verified' ? 'checked' : 'provisional') }, ({ verified: 'Facts verified', community_data: 'Based on community map data', mixed: 'Partly community map data', unverified: 'Facts unknown' }[v.confidence]) || 'Checked'));
    put('shStops', v.blocks.map(b => h('div', { class: 'stopline' }, h('strong', null, b.start + '–' + b.end),
      h('div', null, h('strong', null, b.name), h('div', { class: 'chips' }, h('span', { class: 'mini' }, ic('wallet'), b.cost || ''), (b.facts || []).map(factChip).filter(Boolean))))));
    put('shVerify', verifyBlock(v.verify));
    put('shDirs', (v.directions || []).length ? h('div', { class: 'ctl', style: 'margin-top:12px' }, h('span', { class: 'lbl' }, 'Directions (Google Maps)'),
      h('ul', { class: 'dirlist' }, v.directions.map(d => h('li', null, safeLink(d.url, d.label) || d.label)))) : null);
    put('shNotes', (v.notes || []).map(n => h('div', { class: 'note' }, ic('shield'), h('span', null, n))));
    $('shNote').textContent = v.shared_note;
    $('shIcs').href = '/api/shared/' + encodeURIComponent(token) + '/export.ics'; $('shIcs').setAttribute('download', 'sidequest-plan.ics'); show('shIcs');
  } catch (e) { $('shTitle').textContent = 'Link not available'; $('shMeta').textContent = e.message; }
}

/* ---- group: save / share / export / delete ---- */
async function renderGroupSave(st) {
  const org = st.me.role === 'organizer', kids = [];
  const fail = e => put('gPlan', notice('err', 'alert', e.message));
  if (st.accepted) kids.push(h('button', { class: 'small ghost', type: 'button', onclick: () => downloadWithAuth('/api/groups/' + G.gid + '/export.ics', 'sidequest-group-plan.ics', G.token).catch(fail) }, ic('clock'), 'Download calendar (.ics)'));
  if (org && st.accepted) {
    const url = h('input', { type: 'text', readonly: true, 'aria-label': 'Share link', value: st.share_token ? shareUrl(st.share_token) : '', placeholder: 'No link yet' });
    kids.push(h('div', { class: 'ctl', style: 'margin-top:12px' }, h('span', { class: 'lbl' }, 'Share a read-only link'),
      h('div', { class: 'shareurl' }, url, h('button', { class: 'small', type: 'button', disabled: !st.share_token, onclick: () => navigator.clipboard.writeText(url.value).catch(() => url.select()) }, 'Copy')),
      h('button', { class: 'small ghost', type: 'button', onclick: async () => { try { await gapi('/api/groups/' + G.gid + '/share', 'POST', { enabled: !st.share_token }); G.sig = {}; gPoll(); } catch (e) { fail(e); } } }, st.share_token ? 'Revoke link' : 'Create link'),
      h('p', { class: 'help' }, 'The shared page shows stops and times only: no names, budgets or locations.')));
  }
  try {
    const hist = await gapi('/api/groups/' + G.gid + '/history');
    kids.push(h('div', { class: 'ctl', style: 'margin-top:12px' }, h('span', { class: 'lbl' }, 'History'), h('ul', { class: 'mlist' }, historyRows(hist.versions, null))));
  } catch (e) { /* history is optional */ }
  if (org) kids.push(h('button', { class: 'small danger', type: 'button', style: 'margin-top:12px', onclick: async () => {
    if (!confirm('Delete this group, everyone\u2019s saved inputs, all plans and any share link? This cannot be undone.')) return;
    try { await gapi('/api/groups/' + G.gid, 'DELETE'); lsDel(gTokenKey(G.gid)); location.href = location.pathname; } catch (e) { fail(e); } } }, ic('trash'), 'Delete this group and its data'));
  put('gSaveBody', kids.length ? kids : [h('p', { class: 'sub' }, 'Accept a plan to export or share it.')]);
}

/* ---------- boot ---------- */
function boot() {
  put('markIc', ic('compass')); put('themeBtn', ic('moon')); put('newBtn', ic('refresh')); put('locBtn', ic('pin'), 'Share my location');
  put('assistIc', ic('chat')); put('chevIc', ic('chev'));
  put('tabPlan', ic('list'), 'Plan'); put('tabMap', ic('map'), 'Map'); put('tabAssist', ic('chat'), 'Assistant');
  const pts = [['shield', 'Every plan is checked in code, not just written by a model.'], ['pin', 'Uses your real location and real nearby places.'],
               ['help', 'Says “unknown” when it is not sure, instead of guessing.']];
  put('points', pts.map(([i, t]) => h('li', null, h('span', { class: 'dotic' }, ic(i)), h('span', null, t))));
  initTheme();
  $('req').addEventListener('input', () => { $('reqCount').textContent = $('req').value.length; $('reqErr').classList.add('hide'); });
  $('reqCount').textContent = $('req').value.length;
  $('locBtn').onclick = getLocation;
  $('nolimit').addEventListener('change', () => { $('budget').disabled = $('nolimit').checked; });
  $('budNo').addEventListener('change', () => { $('budSel').disabled = $('budNo').checked; });
  $('newBtn').onclick = () => { if (S.replay || G.gid) { location.href = location.pathname; return; } stopQuest(); S.current = S.view = S.tripId = null; showCompose(); $('go').disabled = !S.pos; };
  $('modeSolo').onclick = () => setMode('solo'); $('modeGroup').onclick = () => setMode('group'); setMode('solo');
  const q = new URLSearchParams(location.search);
  if (!REPLAY && q.get('join')) { showJoin(q.get('join')); return; }
  if (!REPLAY && q.get('g')) { enterGroup(q.get('g')); return; }
  if (!REPLAY && q.get('share')) { showShare(q.get('share')); return; }
  if (REPLAY) initReplay(); else { getLocation(); resumeLastTrip(); }
}
boot();

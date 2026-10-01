// Raw request/response inspector (builder C), shown in B's drawer: Request, Response, Timing
// waterfall, Snippets, a Canvas explainer of how OpenJev reads the request, and a Repro list
// of every logged request with the same body hash.

import { el, trunc, describe, isObj, deepClose } from '/js/jev/util.js';
import { waterfall, timingSegments, legend } from '/js/jev/charts.js';
import { mountJsonEditor } from '/js/jev/jsonedit.js';
import { buildSnippet, SNIPPET_KINDS, bodySize } from '/js/jev/snippets.js';
import { statsFor } from '/js/jev/renderers.js';
import { openDrawer } from '/js/core/drawer.js';
import { icon } from '/js/core/icons.js';
import { copyText, download } from '/js/core/dom.js';
import { fmtMs, fmtBytes, fmtRelTime, fmtInt } from '/js/core/format.js';
import { listRequests, getSettings } from '/js/core/store.js';
import { summarizeAnswers } from '/js/core/metrics.js';
import { getCachedConfig } from '/js/core/api.js';

const TABS = ['Request', 'Response', 'Timing', 'Snippets', 'Canvas', 'Repro'];
let lastTab = 'Request';

function imgPlaceholder(v) {
  if (typeof v === 'string' && v.startsWith('data:')) {
    const type = v.slice(5, v.indexOf(';')) || 'image';
    const kb = Math.round(((v.length - v.indexOf(',') - 1) * 3 / 4) / 1024);
    return `<${type} ${kb} KB — click to reveal>`;
  }
  if (isObj(v) && typeof v.base64 === 'string') return { content_type: v.content_type, base64: `<${Math.round(v.base64.length * 0.75 / 1024)} KB — click to reveal>` };
  return v;
}
export function redactImages(req) {
  if (!isObj(req)) return req;
  const out = { ...req };
  if (Array.isArray(out.images)) out.images = out.images.map(imgPlaceholder);
  if (Array.isArray(out.messages)) {
    out.messages = out.messages.map((m) => (Array.isArray(m?.content)
      ? { ...m, content: m.content.map((p) => (p?.image_url?.url ? { ...p, image_url: { ...p.image_url, url: imgPlaceholder(p.image_url.url) } } : p)) } : m));
  }
  return out;
}

function kv(rows) {
  return el('table', { class: 'table insp-kv' }, el('tbody', {}, rows.filter(Boolean).map(([k, v, copy]) => el('tr', {},
    el('th', {}, k),
    el('td', { class: 'mono' }, v ?? '—', copy && v ? el('button', { class: 'icon-btn insp-copy', type: 'button', title: 'copy', onclick: () => copyText(String(copy === true ? v : copy)) }, icon('copy', 13)) : null)))));
}

function settingsSafe() { try { return getSettings() || {}; } catch { return {}; } }

// ---------------------------------------------------------------- tabs

function requestTab(host, turn, cleanups) {
  let showImages = false;
  const hasImages = Array.isArray(turn.request?.images) && turn.request.images.length;
  const edHost = el('div', { class: 'insp-json' });
  const toggle = hasImages ? el('label', { class: 'row insp-toggle' },
    el('input', { type: 'checkbox', onchange: (e) => { showImages = e.target.checked; ed.set(showImages ? turn.request : redactImages(turn.request)); } }),
    ' show image data') : null;
  host.append(el('div', { class: 'row insp-bar' },
    el('span', { class: 'faint mono' }, `${turn.kind === 'chat' ? 'POST /v1/chat/completions' : 'POST /v1/systemone'} · ${fmtBytes(bodySize(turn))}`),
    el('span', { class: 'spacer' }), toggle,
    el('button', { class: 'btn sm ghost', type: 'button', onclick: () => copyText(JSON.stringify(turn.request, null, 2)) }, icon('copy', 14), ' Copy'),
    el('button', { class: 'btn sm ghost', type: 'button', onclick: () => download('request.json', turn.request || {}) }, icon('download', 14), ' request.json')), edHost);
  const ed = mountJsonEditor(edHost, { value: redactImages(turn.request || {}), mode: settingsSafe().jsonEditorMode || 'tree', readOnly: true });
  cleanups.push(() => ed.destroy());
}

function responseTab(host, turn, cleanups) {
  let value;
  if (turn.kind === 'chat') value = { assistant: turn.assistant, finishReason: turn.finishReason, usage: turn.usage };
  else value = turn.response;
  if (value) {
    const edHost = el('div', { class: 'insp-json' });
    host.append(el('div', { class: 'row insp-bar' },
      el('span', { class: 'faint mono' }, `status ${turn.http?.status ?? turn.error?.status ?? '—'}`), el('span', { class: 'spacer' }),
      el('button', { class: 'btn sm ghost', type: 'button', onclick: () => copyText(JSON.stringify(value, null, 2)) }, icon('copy', 14), ' Copy'),
      el('button', { class: 'btn sm ghost', type: 'button', onclick: () => download('response.json', value) }, icon('download', 14), ' response.json')), edHost);
    const ed = mountJsonEditor(edHost, { value, mode: settingsSafe().jsonEditorMode || 'tree', readOnly: true });
    cleanups.push(() => ed.destroy());
  }
  if (turn.error) {
    const e = turn.error;
    host.append(el('h4', { class: 'insp-h' }, 'Error'),
      kv([['status', String(e.status)], ['kind', e.kind], ['title', e.title], ['message', e.message], ['error_type', e.errorType], ['request id', e.requestId, true], ['retry-after', e.retryAfter != null ? `${e.retryAfter} s` : null], ['hint', e.hint]]));
    if (Array.isArray(e.details) && e.details.length) {
      host.append(el('div', { class: 'insp-details' }, e.details.map((d) => el('div', { class: 'mono' },
        el('span', { class: 'faint' }, (d.loc || []).join(' › ')), ' ', d.msg, d.type ? el('span', { class: 'badge' }, d.type) : null))));
    }
    if (e.raw) host.append(el('h4', { class: 'insp-h' }, 'Raw body'), el('pre', { class: 'mono insp-pre' }, e.raw));
  }
  if (!value && !turn.error) host.append(el('div', { class: 'muted' }, turn.status === 'pending' ? 'Waiting for the response…' : 'No response.'));
}

function timingTab(host, turn) {
  const http = turn.http;
  if (!http) { host.append(el('div', { class: 'muted' }, 'No HTTP info for this turn.')); return; }
  const { segments, modelNA } = timingSegments(http);
  host.append(el('h4', { class: 'insp-h' }, 'Waterfall'));
  if (segments.length) host.append(waterfall(segments, { width: 500 }), legend([
    { label: 'browser↔proxy', color: 'var(--fg-muted)' }, { label: 'proxy↔OpenJev', color: 'var(--accent-2)' },
    { label: 'server', color: 'var(--accent)' }, modelNA ? { label: 'model (not reported)', hatched: true } : { label: 'model', color: 'var(--type-score)' },
  ]));
  if (modelNA) host.append(el('p', { class: 'faint' }, 'MLX reports model;dur=0.0: the model time is inside "server" here. On vLLM, model is the time on the model summed over reads, and can exceed total when reads run in parallel.'));
  const st = http.serverTiming || {};
  host.append(el('h4', { class: 'insp-h' }, 'Raw'), kv([
    ['status', String(http.status ?? '—')],
    ['x-request-id', http.requestId, true],
    ['server-timing', st.raw, true],
    ['client', fmtMs(http.clientMs)],
    ['upstream (proxy)', Number.isFinite(st.upstream) ? `${st.upstream.toFixed(1)} ms` : null],
    ['total (server)', Number.isFinite(st.total) ? `${st.total.toFixed(1)} ms` : null],
    ['server', Number.isFinite(st.server) ? `${st.server.toFixed(1)} ms` : null],
    ['model', Number.isFinite(st.model) ? `${st.model.toFixed(1)} ms${modelNA ? ' (n/a)' : ''}` : null],
    ['overhead', Number.isFinite(http.clientMs) && Number.isFinite(st.total) ? `${(http.clientMs - st.total).toFixed(1)} ms (client − total)` : null],
    ['request bytes', Number.isFinite(http.requestBytes) ? `${fmtInt(http.requestBytes)} (${fmtBytes(http.requestBytes)})` : null],
    ['response bytes', Number.isFinite(http.responseBytes) ? `${fmtInt(http.responseBytes)} (${fmtBytes(http.responseBytes)})` : null],
    ['started', http.startedAt ? new Date(http.startedAt).toISOString() : null],
    http.retryAfter != null ? ['retry-after', `${http.retryAfter} s`] : null,
    Number.isFinite(http.ttftMs) ? ['ttft', fmtMs(http.ttftMs)] : null,
    Number.isFinite(http.streamMs) ? ['stream', fmtMs(http.streamMs)] : null,
    Number.isFinite(http.chunks) ? ['chunks', String(http.chunks)] : null,
  ]));
}

function snippetsTab(host, turn) {
  let kind = 'curl';
  const code = el('pre', { class: 'mono insp-code' });
  const note = el('div', { class: 'faint insp-note' });
  const sub = el('div', { class: 'tabs insp-subtabs' });
  let config = null;
  try { config = getCachedConfig(); } catch { /* defaults */ }
  const draw = () => {
    sub.textContent = '';
    for (const k of SNIPPET_KINDS) {
      if (k.id === 'openai' && turn.kind !== 'chat') continue;
      sub.appendChild(el('button', { class: ['tab', kind === k.id && 'active'], type: 'button', onclick: () => { kind = k.id; draw(); } },
        k.id === 'python' ? (turn.kind === 'chat' ? 'Python (openai)' : 'Python (typesafe_sdk)') : k.label));
    }
    const text = buildSnippet(kind, turn, config);
    code.textContent = text.length > 40000 ? `${text.slice(0, 40000)}\n… (${fmtBytes(text.length)} total; Copy gives the full text)` : text;
    note.textContent = `Targets ${config?.openjevUrl || 'http://127.0.0.1:8080'} directly (not the UI proxy).${config?.authConfigured ? ' Export OPENJEV_API_KEY first.' : ''}`;
    code.dataset.full = '1';
    code._full = text;
  };
  host.append(el('div', { class: 'row insp-bar' }, sub, el('span', { class: 'spacer' }),
    el('button', { class: 'btn sm', type: 'button', onclick: () => copyText(code._full || code.textContent) }, icon('copy', 14), ' Copy'),
    el('button', { class: 'btn sm ghost', type: 'button', onclick: () => download('request.json', turn.request || {}) }, icon('download', 14), ' Download request.json')),
  note, code);
  draw();
}

const LETTERS = (i) => (i < 26 ? String.fromCharCode(65 + i) : `#${i + 1}`);

function canvasTab(host, turn) {
  if (turn.kind === 'chat') { host.append(el('p', { class: 'muted' }, 'Chat turns are generated left to right by diffusiongemma-26b; there is no answer canvas.')); return; }
  const req = turn.request || {};
  const qs = req.questions || {};
  const ids = Object.keys(qs);
  const answers = turn.response?.answers || {};
  const stats = statsFor(answers, qs);
  host.append(el('p', { class: 'muted' }, 'Question ids never reach the model. It sees q1, q2, … and reads one masked slot per question. Each slot label is one token; the answer is that slot\'s probability distribution.'));
  const list = el('table', { class: 'table insp-slots' }, el('thead', {}, el('tr', {}, ['slot', 'your id', 'type', 'label tokens'].map((h) => el('th', {}, h)))));
  const tb = el('tbody');
  ids.forEach((qid, i) => {
    const q = qs[qid] || {};
    let labels = '';
    if (q.type === 'noul') labels = 'yes / no';
    else if (q.type === 'choice' && isObj(q.criteria)) labels = Object.keys(q.criteria).map((k, j) => `${LETTERS(j)}=${trunc(k, 14)}`).join('  ');
    else if (q.type === 'score' && Array.isArray(q.criteria)) labels = q.criteria.map((l, j) => `${j}=${trunc(describe(l), 12)}`).join('  ');
    tb.appendChild(el('tr', {}, el('td', { class: 'mono' }, `q${i + 1}`), el('td', { class: 'mono' }, qid), el('td', {}, q.type), el('td', { class: 'mono insp-labels' }, trunc(labels, 400))));
  });
  list.appendChild(tb);
  host.append(list);

  // ASCII canvas in the spirit of the README
  const L = [];
  const colA = 24, colB = 27;
  L.push('canvas in'.padEnd(colA) + 'one read-only pass'.padEnd(colB) + 'answer out');
  ids.slice(0, 30).forEach((qid, i) => {
    const q = qs[qid] || {};
    const st = stats[qid];
    const rows = [];
    if (st?.type === 'noul') rows.push([`P(yes) ${st.value.toFixed(3)}`, `noul  ${st.value.toFixed(3)}`]);
    else if (st?.type === 'choice') {
      const keys = isObj(q.criteria) ? Object.keys(q.criteria) : st.probs.map((p) => p.key);
      const byKey = Object.fromEntries(st.probs.map((p) => [p.key, p.p]));
      const shown = keys.slice(0, 6);
      shown.forEach((k, j) => rows.push([`P(${LETTERS(j)}) ${(byKey[k] ?? 0).toFixed(3)}`, j === 0 ? `choice "${trunc(String(st.value), 14)}"` : j === 1 ? `confidence ${st.confidence.toFixed(3)}` : '']));
      if (keys.length > 6) rows.push([`… ${keys.length - 6} more`, '']);
    } else if (st?.type === 'score') {
      st.probs.slice(0, 10).forEach((p, j) => rows.push([`P(${j}) ${p.p.toFixed(3)}`, j === 0 ? `score ${(+st.value).toFixed(2)}` : j === 1 ? `confidence ${st.confidence.toFixed(3)}` : '']));
    } else rows.push(['(no answer)', '']);
    rows.forEach((r, j) => {
      const left = j === 0 ? `  q${i + 1}: [?]` : '';
      const arrow1 = j === 0 ? '──►' : '   ';
      const arrow2 = j === 0 ? '──►' : '   ';
      L.push(`${left.padEnd(colA - 8)}${arrow1}     ${r[0].padEnd(colB - 7)}${arrow2}    ${r[1]}`);
    });
  });
  if (ids.length > 30) L.push(`  … ${ids.length - 30} more questions`);
  host.append(el('pre', { class: 'mono insp-canvas' }, L.join('\n')));

  // notes
  const notes = [];
  const reads = Math.ceil(ids.length / 12);
  if (ids.length > 12) notes.push(`${ids.length} questions are read in about ${reads} chunks of ~12 slots per canvas, ${req.sequential ? 'in order (sequential: each chunk sees the answers before it)' : 'in parallel (set sequential for dependent questions)'}.`);
  else notes.push(`${ids.length} question${ids.length === 1 ? '' : 's'} fit in one read (~12 slots per canvas).`);
  if (req.sequential && ids.length <= 12) notes.push('sequential is set, but with ≤12 questions there is only one chunk, so it changes nothing.');
  const bigChoices = ids.filter((q) => qs[q]?.type === 'choice' && isObj(qs[q].criteria) && Object.keys(qs[q].criteria).length > 12);
  if (bigChoices.length) notes.push(`Choice${bigChoices.length > 1 ? 's' : ''} with more than 12 options (${bigChoices.join(', ')}) take more canvas room and may be read in several passes.`);
  const uncertain = ids.filter((q) => stats[q] && stats[q].entropyBits * Math.LN2 > 0.1);
  if (req.samples) notes.push(`samples: ${req.samples} → read ${req.samples}× with different noise and averaged (replaces the automatic re-reads; costs ${req.samples}× input tokens).`);
  else if (uncertain.length) notes.push(`Uncertain slot${uncertain.length > 1 ? 's' : ''} (entropy > 0.1 nats): ${uncertain.slice(0, 8).join(', ')}${uncertain.length > 8 ? '…' : ''}. OpenJev then reads the whole request 3 more times with fresh noise and averages the 4 reads; these re-reads add no tokens.`);
  else if (ids.length) notes.push('Every slot is confident (entropy ≤ 0.1 nats), so no automatic re-read was needed.');
  if (req.steps > 1) notes.push(`steps: ${req.steps} denoise steps per read let answers settle against each other (same tokens, more GPU time).`);
  if (Number.isFinite(req.think)) notes.push(`think: ${req.think} → the model writes up to ${req.think} thought tokens first, then reads the answers after the thought (input tokens twice + ${turn.response?.usage?.output_tokens ?? '?'} thought tokens).`);
  if (Array.isArray(req.images) && req.images.length) notes.push(`${req.images.length} image${req.images.length > 1 ? 's' : ''} are placed before the state (~280 tokens each).`);
  notes.push('The seed is a hash of state + questions (+ images): the same body gives the same answers. See Repro.');
  host.append(el('ul', { class: 'insp-notes' }, notes.map((n) => el('li', {}, n))));
}

async function reproTab(host, turn, conversation) {
  if (!turn.bodyHash) { host.append(el('p', { class: 'muted' }, 'This turn has no body hash.')); return; }
  host.append(el('p', { class: 'muted' }, 'Every logged request whose body hashes to ', el('code', { class: 'mono' }, turn.bodyHash), '. OpenJev seeds its noise from the request, so identical bodies should give identical answers.'));
  const box = el('div', {}, el('div', { class: 'skeleton insp-skel' }));
  host.append(box);
  let recs = [];
  try { recs = (await listRequests({ since: 0, limit: 5000 })).filter((r) => r.bodyHash === turn.bodyHash); } catch (e) { box.textContent = `Could not read the request log: ${e.message || e}`; return; }
  box.textContent = '';
  let mine = null;
  try { if (turn.response?.answers) mine = summarizeAnswers(turn.response.answers, turn.request?.questions || {}); } catch { mine = null; }
  const same = (a) => {
    if (!a || !mine) return null;
    if (a.length !== mine.length) return false;
    return a.every((x, i) => x.qid === mine[i].qid && x.top === mine[i].top && deepClose(x.topP, mine[i].topP, 1e-9));
  };
  if (!recs.length) { box.append(el('p', { class: 'faint' }, 'No logged requests with this hash (the log may have been cleared).')); return; }
  const lat = recs.map((r) => r.clientMs).filter(Number.isFinite);
  box.append(el('div', { class: 'mono faint' }, `${recs.length} run${recs.length > 1 ? 's' : ''} · client ${lat.length ? `${fmtMs(Math.min(...lat))} – ${fmtMs(Math.max(...lat))}` : '—'}`));
  box.append(el('table', { class: 'table insp-repro' },
    el('thead', {}, el('tr', {}, ['when', 'source', 'status', 'client', 'server', 'answers'].map((h) => el('th', {}, h)))),
    el('tbody', {}, recs.slice().reverse().map((r) => {
      const eq = same(r.answers);
      return el('tr', { class: r.turnId === turn.id ? 'insp-this' : '' },
        el('td', { title: new Date(r.ts).toISOString() }, fmtRelTime(r.ts)),
        el('td', {}, r.source || '—', r.turnId === turn.id ? el('span', { class: 'badge' }, 'this') : null, r.convId && conversation && r.convId !== conversation.id ? el('span', { class: 'badge faint' }, 'other conv') : null),
        el('td', { class: 'mono' }, String(r.status)),
        el('td', { class: 'mono' }, fmtMs(r.clientMs)),
        el('td', { class: 'mono' }, Number.isFinite(r.serverTiming?.total) ? fmtMs(r.serverTiming.total) : '—'),
        el('td', {}, eq === null ? el('span', { class: 'faint' }, '—') : eq ? el('span', { class: 'insp-ok' }, icon('check', 13), ' identical') : el('span', { class: 'insp-warn' }, icon('alert', 13), ' drift')));
    }))));
}

/** Open the inspector drawer for a turn. */
export function openInspector({ conversation = null, turn } = {}) {
  if (!turn) return;
  const idx = conversation?.turns ? conversation.turns.findIndex((t) => t.id === turn.id) : -1;
  const title = `Inspect ${idx >= 0 ? `#${idx + 1} · ` : ''}${turn.kind === 'chat' ? 'chat' : 'systemone'} · ${turn.status}`;
  openDrawer({
    title,
    render(root) {
      const cleanups = [];
      const tabs = el('div', { class: 'tabs insp-tabs' });
      const body = el('div', { class: 'insp-body' });
      root.append(el('div', { class: 'insp' }, tabs, body));
      const show = (name) => {
        lastTab = name;
        while (cleanups.length) { try { cleanups.pop()(); } catch { /* ignore */ } }
        body.textContent = '';
        tabs.textContent = '';
        for (const t of TABS) tabs.appendChild(el('button', { class: ['tab', t === name && 'active'], type: 'button', onclick: () => show(t) }, t));
        if (name === 'Request') requestTab(body, turn, cleanups);
        else if (name === 'Response') responseTab(body, turn, cleanups);
        else if (name === 'Timing') timingTab(body, turn);
        else if (name === 'Snippets') snippetsTab(body, turn);
        else if (name === 'Canvas') canvasTab(body, turn);
        else if (name === 'Repro') reproTab(body, turn, conversation);
      };
      show(TABS.includes(lastTab) ? lastTab : 'Request');
      return () => { while (cleanups.length) { try { cleanups.pop()(); } catch { /* ignore */ } } };
    },
  });
}

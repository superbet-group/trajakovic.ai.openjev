// Batch view (builder C, #/batch): one question set against many states. States come as
// lines, blank-line blocks, JSONL or a CSV column; runs with concurrency 1–4, pause/resume/abort,
// live progress, a sortable/filterable results table with aggregates, and CSV/JSON/Markdown export.

import { el, trunc, isObj, loadLS, saveLS, mean, std, sleep, clamp } from '/js/jev/util.js';
import { confColor, tint, stackedBar, SERIES, TYPE_COLOR } from '/js/jev/charts.js';
import { mountQuestionEditor } from '/js/jev/questionEditor.js';
import { statsFor, answerText } from '/js/jev/renderers.js';
import { openInspector } from '/js/jev/inspector.js';
import { imageZone } from '/js/jev/images.js';
import { TEMPLATES, takeBatchHandoff, demoShapesImage, getTemplate } from '/js/jev/templates.js';
import { systemOne, listModels } from '/js/core/api.js';
import { getSettings, getConversation } from '/js/core/store.js';
import { estimateTokens, sha256Hex, costOf } from '/js/core/metrics.js';
import { renderError } from '/js/core/errors.js';
import { icon } from '/js/core/icons.js';
import { fmtMs, fmtInt, fmtTokens, fmtCost } from '/js/core/format.js';
import { download, copyText } from '/js/core/dom.js';
import { toast } from '/js/core/toast.js';
import { confirmDialog } from '/js/core/modal.js';

const LS_KEY = 'ojui.batch.last.v1';
const FORMATS = [
  { id: 'lines', label: 'lines', hint: 'one state per line' },
  { id: 'blocks', label: 'blank-line blocks', hint: 'states separated by an empty line' },
  { id: 'jsonl', label: 'JSONL', hint: 'each line is a JSON state (object, array or string)' },
  { id: 'csv', label: 'CSV', hint: 'pick a column, or * for the whole row as an object' },
];

function settingsSafe() { try { return getSettings() || {}; } catch { return {}; } }

// ---------------------------------------------------------------- parsing

export function parseCsv(text) {
  const rows = [];
  let row = [], field = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; } else field += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(field); rows.push(row); row = []; field = ''; }
    else field += c;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((x) => x.trim() !== ''));
}

/** → {states: [{state, isJson}], errors: [string], columns?: string[]} */
export function parseStates(text, format, column) {
  const t = String(text || '');
  if (format === 'lines') return { states: t.split('\n').map((x) => x.trim()).filter(Boolean).map((state) => ({ state, isJson: false })), errors: [] };
  if (format === 'blocks') return { states: t.split(/\n\s*\n/).map((x) => x.trim()).filter(Boolean).map((state) => ({ state, isJson: false })), errors: [] };
  if (format === 'jsonl') {
    const states = [], errors = [];
    t.split('\n').forEach((line, i) => {
      const l = line.trim();
      if (!l) return;
      try { const v = JSON.parse(l); states.push({ state: v, isJson: typeof v !== 'string' }); } catch (e) { errors.push(`line ${i + 1}: ${e.message}`); }
    });
    return { states, errors };
  }
  const rows = parseCsv(t);
  if (!rows.length) return { states: [], errors: [], columns: [] };
  const header = rows[0].map((h) => h.trim());
  const col = column === '*' ? '*' : (header.includes(column) ? column : header[0]);
  const idx = header.indexOf(col);
  const states = rows.slice(1).map((r) => {
    if (col === '*') return { state: Object.fromEntries(header.map((h, i) => [h, r[i] ?? ''])), isJson: true };
    return { state: (r[idx] ?? '').trim(), isJson: false };
  }).filter((x) => x.isJson || x.state !== '');
  return { states, errors: [], columns: header, column: col };
}

// ---------------------------------------------------------------- view

export function mountBatchView(root) {
  const saved = loadLS(LS_KEY, null);
  const job = {
    questions: saved?.questions || null,
    options: saved?.options || { model: settingsSafe().defaultModel || 'openjev-latest', steps: null, samples: null, think: null, sequential: false },
    format: saved?.format || 'lines',
    column: saved?.column || '',
    input: saved?.input || '',
    concurrency: saved?.concurrency || 1,
    rows: saved?.rows || [],
    startedAt: saved?.startedAt || null,
    finishedAt: saved?.finishedAt || null,
    title: saved?.title || '',
  };
  let images = [];
  let running = false, paused = false, abort = null;
  let sort = { key: null, dir: -1 }, sortBy = 'value', filterOn = false, filterX = 0.6;
  let alive = true;
  let models = [];
  let tick = null;

  const view = el('div', { class: 'batch-view' });
  root.appendChild(view);
  const left = el('div', { class: 'batch-left' });
  const right = el('div', { class: 'batch-right' });
  view.append(left, right);

  // ---------------- left pane
  const qHost = el('div', { class: 'batch-qe' });
  let qe = null;
  const optsRow = el('div', { class: 'row batch-opts' });
  const fmtSel = el('select', { class: 'select sm', onchange: (e) => { job.format = e.target.value; refreshInput(); persist(); } },
    FORMATS.map((f) => el('option', { value: f.id, selected: job.format === f.id, title: f.hint }, f.label)));
  const colSel = el('select', { class: 'select sm', hidden: true, onchange: (e) => { job.column = e.target.value; refreshInput(); persist(); } });
  const ta = el('textarea', { class: 'textarea mono batch-input', rows: 10, spellcheck: 'false', value: job.input, placeholder: 'One state per line…',
    oninput: () => { job.input = ta.value; refreshInput(); persistSoon(); } });
  const inputInfo = el('div', { class: 'mono faint batch-info' });
  const tplSel = el('select', { class: 'select sm', onchange: async (e) => { const t = getTemplate(e.target.value); e.target.value = ''; if (t) await loadTemplateJob(t); } },
    el('option', { value: '' }, 'load template…'), TEMPLATES.map((t) => el('option', { value: t.id }, `${t.title} (${t.batchStates?.length || 0})`)));
  const zone = imageZone({ onChange: (v) => { images = v; refreshInput(); } });

  left.append(
    el('div', { class: 'row batch-lh' }, el('h2', { class: 'batch-h' }, icon('batch', 18), ' Batch'), el('span', { class: 'spacer' }), tplSel),
    el('div', { class: 'batch-sec' }, el('div', { class: 'batch-label' }, 'Question set'), qHost),
    el('div', { class: 'batch-sec' }, el('div', { class: 'batch-label' }, 'Options'), optsRow),
    el('div', { class: 'batch-sec' }, el('div', { class: 'row batch-label' }, 'States', el('span', { class: 'spacer' }), fmtSel, colSel), ta, inputInfo),
    el('div', { class: 'batch-sec' }, zone.element));

  function drawOptions() {
    optsRow.textContent = '';
    const o = job.options;
    const modelSel = el('select', { class: 'select sm', title: 'model', onchange: (e) => { o.model = e.target.value; persist(); refreshInput(); } },
      (models.length ? models.filter((m) => m.name !== 'diffusiongemma-26b').map((m) => m.name) : [o.model]).map((n) => el('option', { value: n, selected: n === o.model }, n)));
    const num = (k, label, min, max) => el('label', { class: 'chip batch-num' }, label,
      el('input', { class: 'mono', type: 'number', min, max, value: o[k] ?? '', placeholder: 'def', oninput: (e) => { o[k] = e.target.value === '' ? null : clamp(Number(e.target.value), min, max); persist(); refreshInput(); } }));
    optsRow.append(modelSel, num('steps', 'steps', 1, 8), num('samples', 'samples', 1, 32), num('think', 'think', 0, 4096),
      el('label', { class: ['chip', o.sequential && 'active'] }, el('input', { type: 'checkbox', checked: !!o.sequential, onchange: (e) => { o.sequential = e.target.checked; persist(); drawOptions(); refreshInput(); } }), ' sequential'));
  }

  let parsed = { states: [], errors: [] };
  function refreshInput() {
    parsed = parseStates(job.input, job.format, job.column);
    colSel.hidden = job.format !== 'csv';
    if (job.format === 'csv') {
      colSel.textContent = '';
      for (const c of [...(parsed.columns || []), '*']) colSel.appendChild(el('option', { value: c, selected: c === (parsed.column || job.column) }, c === '*' ? '* (row as JSON)' : c));
    }
    ta.placeholder = FORMATS.find((f) => f.id === job.format)?.hint || '';
    const qs = qe ? qe.get() : job.questions || {};
    let est = 0;
    try { for (const st of parsed.states) est += estimateTokens({ state: st.state, questions: qs, images: images.map((i) => i.dataUrl) }); } catch { est = 0; }
    const mult = (job.options.samples || 1) * (Number.isFinite(job.options.think) && job.options.think > 0 ? 2 : 1);
    inputInfo.textContent = `${parsed.states.length} states · ≈ ${fmtTokens(est * mult)} input tok est${mult > 1 ? ` (×${mult} for samples/think)` : ''}${images.length ? ` · ${images.length} image${images.length > 1 ? 's' : ''} each` : ''}${parsed.errors.length ? ` · ${parsed.errors.length} parse errors: ${parsed.errors[0]}` : ''}`;
    inputInfo.classList.toggle('err', parsed.errors.length > 0);
    if (!running) drawControls();
  }

  async function loadTemplateJob(t, force = false) {
    if (!force && job.rows.length && !(await confirmDialog(`Replace the current batch with "${t.title}"? The current results are discarded.`))) return;
    job.questions = JSON.parse(JSON.stringify(t.questions));
    qe.set(job.questions);
    job.options = { model: job.options.model || 'openjev-latest', steps: null, samples: null, think: null, sequential: false, ...(t.options || {}) };
    const states = t.batchStates || [];
    const multiline = states.some((x) => x.includes('\n'));
    job.format = t.stateIsJson ? 'jsonl' : multiline ? 'blocks' : 'lines';
    job.input = states.join(multiline && !t.stateIsJson ? '\n\n' : '\n');
    job.rows = []; job.title = t.title; job.startedAt = null; job.finishedAt = null;
    ta.value = job.input; fmtSel.value = job.format;
    if (t.images === 'demo-shapes') { images = [await demoShapesImage()]; zone.set(images); } else { images = []; zone.set([]); }
    drawOptions(); refreshInput(); drawResults(); persist();
    toast(`Loaded ${t.title}: ${states.length} states`, { kind: 'ok' });
  }

  // ---------------- controls & progress
  const controls = el('div', { class: 'card batch-controls' });
  const progress = el('div', { class: 'batch-progress' });
  const results = el('div', { class: 'batch-results' });
  right.append(controls, results);

  function drawControls() {
    controls.textContent = '';
    const conc = el('select', { class: 'select sm', title: 'concurrency', disabled: running, onchange: (e) => { job.concurrency = Number(e.target.value); persist(); } },
      [1, 2, 3, 4].map((n) => el('option', { value: n, selected: n === job.concurrency }, `concurrency ${n}`)));
    const hasPending = job.rows.some((r) => r.status === 'queued' || r.status === 'aborted');
    controls.append(el('div', { class: 'row' },
      running
        ? [el('button', { class: 'btn', type: 'button', onclick: () => { paused = !paused; drawControls(); } }, icon(paused ? 'play' : 'clock', 14), paused ? ' Resume' : ' Pause'),
          el('button', { class: 'btn danger', type: 'button', onclick: () => { abort?.abort(); } }, icon('stop', 14), ' Abort')]
        : [el('button', { class: 'btn primary', type: 'button', disabled: !parsed.states.length, onclick: () => start(false) }, icon('play', 14), ` Run ${parsed.states.length}`),
          hasPending ? el('button', { class: 'btn', type: 'button', onclick: () => start(true) }, icon('refresh', 14), ' Continue') : null],
      conc,
      el('span', { class: 'spacer' }),
      el('button', { class: 'btn sm ghost', type: 'button', disabled: !job.rows.some((r) => r.status === 'ok'), onclick: exportCsv }, icon('download', 14), ' CSV'),
      el('button', { class: 'btn sm ghost', type: 'button', disabled: !job.rows.length, onclick: exportJson }, icon('json', 14), ' JSON'),
      el('button', { class: 'btn sm ghost', type: 'button', disabled: !job.rows.some((r) => r.status === 'ok'), onclick: copyMarkdown }, icon('copy', 14), ' Markdown')),
    progress);
    drawProgress();
  }

  function drawProgress() {
    progress.textContent = '';
    const total = job.rows.length;
    if (!total) { progress.append(el('div', { class: 'faint' }, 'Results appear here. Every request goes through the proxy with source "batch" and lands in Stats.')); return; }
    const done = job.rows.filter((r) => r.status === 'ok' || r.status === 'error').length;
    const errs = job.rows.filter((r) => r.status === 'error').length;
    const now = job.finishedAt || Date.now();
    const elapsed = job.startedAt ? now - job.startedAt : 0;
    const rate = elapsed > 0 ? done / (elapsed / 1000) : 0;
    const eta = rate > 0 && done < total ? ((total - done) / rate) * 1000 : null;
    const tokens = job.rows.reduce((a, r) => a + (r.usage?.input_tokens || 0) + (r.usage?.output_tokens || 0), 0);
    let cost = null;
    try { cost = costOf({ input_tokens: job.rows.reduce((a, r) => a + (r.usage?.input_tokens || 0), 0), output_tokens: job.rows.reduce((a, r) => a + (r.usage?.output_tokens || 0), 0) }, settingsSafe()); } catch { cost = null; }
    progress.append(
      el('div', { class: 'batch-bar' }, el('i', { class: 'ok', style: { width: `${((done - errs) / total) * 100}%` } }), el('i', { class: 'err', style: { width: `${(errs / total) * 100}%` } })),
      el('div', { class: 'row mono batch-stats' },
        el('span', {}, el('b', {}, `${done}/${total}`), ' done'),
        errs ? el('span', { class: 'err' }, `${errs} errors`) : null,
        el('span', {}, `elapsed ${fmtMs(elapsed)}`),
        eta !== null ? el('span', {}, `ETA ${fmtMs(eta)}`) : null,
        el('span', {}, `${rate.toFixed(2)} req/s`),
        el('span', {}, `${fmtTokens(tokens)} tok`),
        cost !== null ? el('span', {}, fmtCost(cost, settingsSafe().currency || '$')) : null,
        paused ? el('span', { class: 'badge' }, 'paused') : null,
        running ? el('span', { class: 'badge batch-live' }, 'running') : null));
  }

  function buildBody(st) {
    const o = job.options;
    const body = { model: o.model || 'openjev-latest', state: st.state, questions: job.questions };
    if (images.length) body.images = images.map((i) => i.dataUrl).filter(Boolean);
    if (o.steps != null) body.steps = o.steps;
    if (o.samples != null) body.samples = o.samples;
    if (o.think != null) body.think = o.think;
    if (o.sequential) body.sequential = true;
    return body;
  }

  async function start(resume) {
    const v = qe.validate();
    if (!v.valid && !(await confirmDialog(`The question set has ${v.errors.length} error${v.errors.length > 1 ? 's' : ''} (${v.errors[0]?.message}). Send anyway?`))) return;
    job.questions = qe.get();
    if (!resume) {
      if (job.rows.some((r) => r.status === 'ok') && !(await confirmDialog('Discard the current results and run again?'))) return;
      job.rows = parsed.states.map((s0, i) => ({ i: i + 1, state: s0.state, isJson: s0.isJson, status: 'queued' }));
      job.startedAt = Date.now(); job.finishedAt = null;
    } else {
      for (const r of job.rows) if (r.status === 'aborted') r.status = 'queued';
      job.finishedAt = null;
      if (!job.startedAt) job.startedAt = Date.now();
    }
    running = true; paused = false; abort = new AbortController();
    drawControls(); drawResults();
    tick = setInterval(() => { if (alive) drawProgress(); }, 500);
    const autoRetry = settingsSafe().autoRetryOverloaded !== false;
    const worker = async () => {
      while (alive && !abort.signal.aborted) {
        if (paused) { await sleep(200, abort.signal); continue; }
        const row = job.rows.find((r) => r.status === 'queued');
        if (!row) return;
        row.status = 'pending';
        drawRow(row);
        const body = buildBody(row);
        let res = await systemOne(body, { signal: abort.signal, source: 'batch', label: `${job.title || 'batch'} #${row.i}` });
        if (!res.ok && autoRetry && (res.status === 529 || res.status === 429) && !abort.signal.aborted) {
          await sleep(((res.error?.retryAfter ?? 1) * 1000) || 1000, abort.signal);
          if (!abort.signal.aborted) res = await systemOne(body, { signal: abort.signal, source: 'batch', label: `${job.title || 'batch'} #${row.i} retry` });
        }
        if (!alive) return;
        if (res.error?.kind === 'aborted') { row.status = 'aborted'; drawRow(row); return; }
        row.status = res.ok ? 'ok' : 'error';
        row.answers = res.data?.answers || null;
        row.model = res.data?.model || null;
        row.usage = res.data?.usage || null;
        row.error = res.error || null;
        row.http = res.http || null;
        row.clientMs = res.http?.clientMs ?? null;
        try { row.bodyHash = await sha256Hex(JSON.stringify(body)); } catch { row.bodyHash = null; }
        row.stats = row.answers ? statsFor(row.answers, job.questions) : null;
        drawRow(row);
        drawProgress();
        persistSoon();
      }
    };
    await Promise.all(Array.from({ length: job.concurrency }, worker));
    if (abort?.signal.aborted) for (const r of job.rows) if (r.status === 'queued' || r.status === 'pending') r.status = 'aborted';
    running = false; abort = null;
    job.finishedAt = Date.now();
    clearInterval(tick); tick = null;
    if (!alive) return;
    persist();
    drawControls(); drawResults();
    const errs = job.rows.filter((r) => r.status === 'error').length;
    toast(`Batch finished: ${job.rows.filter((r) => r.status === 'ok').length} ok${errs ? `, ${errs} errors` : ''}`, { kind: errs ? 'warn' : 'ok' });
  }

  // ---------------- results table
  let tbody = null;
  const rowEls = new Map();
  function qids() { return Object.keys(job.questions || {}); }

  function sortedRows() {
    let rows = job.rows.slice();
    if (filterOn) rows = rows.filter((r) => r.stats && Object.values(r.stats).some((st) => st && st.confidence < filterX));
    if (!sort.key) return rows;
    const val = (r) => {
      if (sort.key === '#') return r.i;
      if (sort.key === 'lat') return r.clientMs ?? Infinity;
      if (sort.key === 'tok') return r.usage?.input_tokens ?? -1;
      if (sort.key === 'minconf') return r.stats ? Math.min(...Object.values(r.stats).filter(Boolean).map((s0) => s0.confidence)) : Infinity;
      const st = r.stats?.[sort.key];
      if (!st) return sort.dir > 0 ? Infinity : -Infinity;
      if (sortBy === 'confidence') return st.confidence;
      return st.type === 'choice' ? st.topP : st.value;
    };
    return rows.sort((a, b) => (val(a) - val(b)) * sort.dir || a.i - b.i);
  }

  function cellFor(r, qid) {
    const st = r.stats?.[qid];
    if (!st) return el('td', { class: 'faint' }, r.status === 'pending' ? '…' : '—');
    const c = st.confidence;
    return el('td', { class: 'mono batch-cell', style: { background: tint(confColor(c), 0.16) }, title: `conf ${c?.toFixed(3)} · H ${st.entropyBits?.toFixed(2)} bits` }, answerText(st));
  }

  function rowEl(r) {
    const minC = r.stats ? Math.min(...Object.values(r.stats).filter(Boolean).map((s0) => s0.confidence)) : null;
    const stateText = typeof r.state === 'string' ? r.state : JSON.stringify(r.state);
    const tr = el('tr', { class: [`st-${r.status}`, 'batch-row'], onclick: () => inspectRow(r) },
      el('td', { class: 'mono faint' }, String(r.i)),
      el('td', { class: 'batch-state', title: trunc(stateText, 600) }, trunc(stateText, 70)),
      qids().map((q) => cellFor(r, q)),
      el('td', { class: 'mono' }, Number.isFinite(minC) ? el('span', { style: { color: confColor(minC) } }, minC.toFixed(2)) : '—'),
      el('td', { class: 'mono' }, r.status === 'error' ? el('span', { class: 'err', title: r.error?.message || '' }, `${r.error?.status || ''} ${r.error?.kind || 'error'}`)
        : r.status === 'pending' ? el('span', { class: 'batch-live' }, 'running') : r.status === 'queued' ? el('span', { class: 'faint' }, 'queued') : r.status === 'aborted' ? el('span', { class: 'faint' }, 'aborted') : fmtMs(r.clientMs)),
      el('td', { class: 'mono' }, r.usage ? fmtInt(r.usage.input_tokens + (r.usage.output_tokens || 0)) : '—'));
    return tr;
  }
  function drawRow(r) {
    const old = rowEls.get(r.i);
    const n = rowEl(r);
    rowEls.set(r.i, n);
    if (old && old.parentNode) old.replaceWith(n);
  }

  function inspectRow(r) {
    const body = buildBody(r);
    const turn = {
      id: `batch_${r.i}`, kind: 'systemone', createdAt: job.startedAt || Date.now(), status: r.status === 'ok' ? 'ok' : r.status === 'error' ? 'error' : 'pending',
      source: 'batch', label: `batch #${r.i}`, parentTurnId: null, request: body, imagesMeta: images.map(({ name, type, bytes, width, height }) => ({ name, type, bytes, width, height })),
      response: r.answers ? { model: r.model || body.model, answers: r.answers, usage: r.usage } : null, http: r.http || null, error: r.error || null, bodyHash: r.bodyHash || null, labels: {},
    };
    openInspector({ conversation: null, turn });
  }

  function aggregates() {
    const ok = job.rows.filter((r) => r.stats);
    const cells = qids().map((qid) => {
      const sts = ok.map((r) => r.stats[qid]).filter(Boolean);
      if (!sts.length) return el('td', {}, '—');
      const mc = mean(sts.map((s0) => s0.confidence));
      const t = sts[0].type;
      let main;
      if (t === 'noul') main = el('span', {}, `P̄ ${mean(sts.map((s0) => s0.value)).toFixed(3)}`);
      else if (t === 'score') main = el('span', {}, `E ${mean(sts.map((s0) => +s0.value)).toFixed(2)} ± ${std(sts.map((s0) => +s0.value)).toFixed(2)}`);
      else {
        const counts = {};
        for (const s0 of sts) counts[s0.top] = (counts[s0.top] || 0) + 1;
        const keys = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
        main = el('span', { class: 'col batch-dist' }, stackedBar(keys.map((k, i) => ({ label: k, value: counts[k], color: SERIES[i % SERIES.length] })), { width: 110, height: 10, fmt: (v) => `${v}×` }),
          el('span', { class: 'faint' }, trunc(keys.slice(0, 2).map((k) => `${k} ${counts[k]}`).join(' · '), 24)));
      }
      return el('td', { class: 'mono batch-agg' }, main, el('div', { class: 'faint' }, `conf ${mc.toFixed(2)}`));
    });
    const lat = ok.map((r) => r.clientMs).filter(Number.isFinite);
    const allConf = ok.flatMap((r) => Object.values(r.stats).filter(Boolean).map((s0) => s0.confidence));
    return el('tr', { class: 'batch-aggrow' },
      el('td', { class: 'faint' }, 'Σ'), el('td', { class: 'faint' }, `${ok.length} ok`), cells,
      el('td', { class: 'mono' }, allConf.length ? mean(allConf).toFixed(2) : '—'),
      el('td', { class: 'mono' }, lat.length ? fmtMs(mean(lat)) : '—'),
      el('td', { class: 'mono' }, fmtInt(ok.reduce((a, r) => a + (r.usage?.input_tokens || 0) + (r.usage?.output_tokens || 0), 0))));
  }

  function drawResults() {
    results.textContent = '';
    rowEls.clear();
    if (!job.rows.length) return;
    const th = (key, label, title) => el('th', {
      class: ['batch-th', sort.key === key && 'sorted'], title: title || 'click to sort',
      onclick: () => { sort = sort.key === key ? (sort.dir === -1 ? { key, dir: 1 } : { key: null, dir: -1 }) : { key, dir: -1 }; drawResults(); },
    }, label, sort.key === key ? (sort.dir === -1 ? ' ↓' : ' ↑') : '');
    const qs = job.questions || {};
    const head = el('tr', {}, th('#', '#'), el('th', {}, 'state'),
      qids().map((q) => th(q, el('span', { class: 'row batch-qh' }, el('i', { class: 'qb-swatch', style: { background: TYPE_COLOR[qs[q]?.type] } }), trunc(q, 16)), `${qs[q]?.type} · sort by ${sortBy}`)),
      th('minconf', 'min conf'), th('lat', 'latency'), th('tok', 'tokens'));
    tbody = el('tbody');
    for (const r of sortedRows()) { const n = rowEl(r); rowEls.set(r.i, n); tbody.appendChild(n); }
    const tools = el('div', { class: 'row batch-tools' },
      el('span', { class: 'faint' }, 'sort question columns by'),
      el('select', { class: 'select sm', onchange: (e) => { sortBy = e.target.value; drawResults(); } },
        el('option', { value: 'value', selected: sortBy === 'value' }, 'value'), el('option', { value: 'confidence', selected: sortBy === 'confidence' }, 'confidence')),
      el('label', { class: ['chip', filterOn && 'active'] }, el('input', { type: 'checkbox', checked: filterOn, onchange: (e) => { filterOn = e.target.checked; drawResults(); } }), ' low confidence <',
        el('input', { class: 'mono batch-fx', type: 'number', step: '0.05', min: 0, max: 1, value: filterX, oninput: (e) => { filterX = Number(e.target.value); if (filterOn) drawResults(); } })),
      el('span', { class: 'spacer' }), el('span', { class: 'faint' }, 'click a row to inspect'));
    results.append(tools, el('div', { class: 'batch-table-wrap scroll' }, el('table', { class: 'table batch-table' }, el('thead', {}, head), tbody, el('tfoot', {}, aggregates()))));
    const errRows = job.rows.filter((r) => r.status === 'error' && r.error).slice(0, 3);
    if (errRows.length) results.append(el('div', { class: 'batch-errs' }, el('div', { class: 'faint' }, `first errors (${job.rows.filter((r) => r.status === 'error').length} total)`),
      errRows.map((r) => el('div', {}, el('div', { class: 'mono faint' }, `#${r.i}`), renderError(r.error, { onRetry: () => { r.status = 'queued'; start(true); } })))));
  }

  // ---------------- export
  function flatColumns() {
    const cols = [];
    for (const [qid, q] of Object.entries(job.questions || {})) {
      if (q.type === 'noul') cols.push([`${qid}.noul`, (st) => st.value], [`${qid}.confidence`, (st) => st.confidence]);
      else if (q.type === 'choice') {
        cols.push([`${qid}.choice`, (st) => st.value], [`${qid}.confidence`, (st) => st.confidence]);
        for (const k of isObj(q.criteria) ? Object.keys(q.criteria) : []) cols.push([`${qid}.p_${k}`, (st) => st.probs.find((p) => p.key === k)?.p]);
      } else if (q.type === 'score') {
        cols.push([`${qid}.score`, (st) => st.value], [`${qid}.confidence`, (st) => st.confidence]);
        (Array.isArray(q.criteria) ? q.criteria : []).forEach((_, i) => cols.push([`${qid}.p_${i}`, (st) => st.probs[i]?.p]));
      }
    }
    return cols;
  }
  const csvq = (v) => { const t = v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v); return /[",\n\r]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
  function exportCsv() {
    const cols = flatColumns();
    const qidOf = (name) => name.slice(0, name.lastIndexOf('.'));
    const lines = [['index', 'state', 'status', ...cols.map((c) => c[0]), 'latency_ms', 'input_tokens', 'output_tokens', 'error'].map(csvq).join(',')];
    for (const r of job.rows) {
      lines.push([r.i, r.state, r.status, ...cols.map(([name, f]) => { const st = r.stats?.[qidOf(name)]; return st ? f(st) : ''; }),
        r.clientMs != null ? r.clientMs.toFixed(1) : '', r.usage?.input_tokens ?? '', r.usage?.output_tokens ?? '', r.error ? `${r.error.status} ${r.error.message}` : ''].map(csvq).join(','));
    }
    download(`batch-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`, lines.join('\n'), 'text/csv');
  }
  function exportJson() {
    download(`batch-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`, {
      format: 'ojui-batch', version: 1, exportedAt: Date.now(), title: job.title, questions: job.questions, options: job.options, imageCount: images.length,
      rows: job.rows.map((r) => ({ index: r.i, state: r.state, status: r.status, model: r.model, answers: r.answers, usage: r.usage, clientMs: r.clientMs, serverTiming: r.http?.serverTiming, requestId: r.http?.requestId, bodyHash: r.bodyHash, error: r.error })),
    });
  }
  function copyMarkdown() {
    const q = qids();
    const esc = (t) => String(t).replace(/\|/g, '\\|').replace(/\n/g, ' ');
    const lines = [`| # | state | ${q.join(' | ')} | latency | tokens |`, `|---|---|${q.map(() => '---').join('|')}|---|---|`];
    for (const r of sortedRows()) {
      lines.push(`| ${r.i} | ${esc(trunc(typeof r.state === 'string' ? r.state : JSON.stringify(r.state), 60))} | ${q.map((x) => esc(r.stats?.[x] ? answerText(r.stats[x]) : '—')).join(' | ')} | ${r.clientMs != null ? fmtMs(r.clientMs) : '—'} | ${r.usage?.input_tokens ?? '—'} |`);
    }
    copyText(lines.join('\n'));
  }

  // ---------------- persistence
  let persistT = null;
  function persist() {
    if (qe) job.questions = qe.get();
    const rows = job.rows.map(({ stats, http, ...r }) => ({ ...r, http: http ? { status: http.status, requestId: http.requestId, serverTiming: http.serverTiming, clientMs: http.clientMs, requestBytes: http.requestBytes, responseBytes: http.responseBytes } : null }));
    if (!saveLS(LS_KEY, { ...job, rows, imageCount: images.length })) saveLS(LS_KEY, { ...job, rows: [], imageCount: images.length });
  }
  function persistSoon() { clearTimeout(persistT); persistT = setTimeout(persist, 600); }

  // ---------------- boot
  (async () => {
    const handoff = takeBatchHandoff();
    let initialQs = job.questions;
    if (!handoff && !initialQs) {
      try {
        const last = localStorage.getItem('ojui.lastConv');
        const conv = last ? await getConversation(last) : null;
        const dq = conv?.draft?.questions;
        if (isObj(dq) && Object.keys(dq).length) initialQs = dq;
        else { const lt = [...(conv?.turns || [])].reverse().find((t) => t.kind === 'systemone' && t.request?.questions); if (lt) initialQs = lt.request.questions; }
      } catch { /* none */ }
    }
    if (!alive) return;
    job.questions = initialQs || getTemplate('sentiment').questions;
    qe = mountQuestionEditor(qHost, { questions: job.questions, emit: false, tab: 'builder', onChange: (qs) => { job.questions = qs; refreshInput(); persistSoon(); } });
    for (const r of job.rows) { if (r.answers) r.stats = statsFor(r.answers, job.questions); if (r.status === 'pending' || r.status === 'queued') r.status = 'aborted'; }
    if (!job.input && !handoff) {
      const t = getTemplate('sentiment');
      job.input = t.batchStates.join('\n'); ta.value = job.input; job.title = 'Sentiment';
      if (!initialQs) { job.questions = t.questions; qe.set(t.questions); }
      else { job.input = ''; ta.value = ''; job.title = ''; }
    }
    drawOptions(); refreshInput(); drawControls(); drawResults();
    if (handoff) await loadTemplateJob(handoff, true);
    try { const r = await listModels(); if (alive && r?.models?.length) { models = r.models; drawOptions(); } } catch { /* offline */ }
    if (saved?.imageCount && !images.length && !handoff) toast(`The last batch used ${saved.imageCount} image${saved.imageCount > 1 ? 's' : ''}; images are not saved, attach them again`, { kind: 'info', timeout: 4000 });
  })();

  return () => {
    alive = false;
    if (abort) abort.abort();
    clearInterval(tick);
    clearTimeout(persistT);
    try { persist(); } catch { /* ignore */ }
    if (qe) qe.destroy();
  };
}


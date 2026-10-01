// Batch view (builder C, #/batch): one question set against many states. States come as
// lines, blank-line blocks, JSONL (whole or one field) or a CSV/TSV column, typed, pasted or loaded
// from files (Upload or a drop anywhere on the view); runs with concurrency 1–4, pause/resume/abort,
// live progress, a sortable/filterable results table with aggregates, and CSV/JSON/Markdown export.

import { el, trunc, isObj, loadLS, saveLS, mean, std, sleep, clamp } from '/js/jev/util.js';
import { confColor, tint, stackedBar, SERIES, TYPE_COLOR } from '/js/jev/charts.js';
import { mountQuestionEditor } from '/js/jev/questionEditor.js';
import { statsFor, answerText, turnToMarkdown } from '/js/jev/renderers.js';
import { mountInspector } from '/js/jev/inspector.js';
import { renderTurnCard } from '/js/jev/turnCard.js';
import { buildSnippet } from '/js/jev/snippets.js';
import { imageZone } from '/js/jev/images.js';
import { allTemplates, isUserTemplate, takeBatchHandoff, demoShapesImage, getTemplate } from '/js/jev/templates.js';
import { systemOne, listModels, getCachedConfig } from '/js/core/api.js';
import { getSettings, getConversation } from '/js/core/store.js';
import { IMAGE_TOKENS_EST, sha256Hex, costOf } from '/js/core/metrics.js';
import { renderError } from '/js/core/errors.js';
import { icon } from '/js/core/icons.js';
import { fmtMs, fmtInt, fmtTokens, fmtCost, fmtBytes, statesToBatchInput } from '/js/core/format.js';
import { download, copyText } from '/js/core/dom.js';
import { toast } from '/js/core/toast.js';
import { confirmDialog, openModal } from '/js/core/modal.js';
import { on } from '/js/core/bus.js';
import { parseCsv, parseStates, parseUpload, readFileText, mergeInputs, capInput, isImageFile, ACCEPT, MAX_FILE_BYTES, MAX_STATES } from '/js/jev/batchImport.js';

// moved to batchImport.js (node-testable); templateEditor imports parseStates from here
export { parseCsv, parseStates };

const LS_KEY = 'ojui.batch.last.v1';
const UI_KEY = 'ojui.batch.layout.v1';  // per-browser view prefs, kept apart from the job
const FORMATS = [
  { id: 'lines', label: 'lines', hint: 'one state per line' },
  { id: 'blocks', label: 'blank-line blocks', hint: 'states separated by an empty line' },
  { id: 'jsonl', label: 'JSONL', hint: 'each line is a JSON state; objects can send one field' },
  { id: 'csv', label: 'CSV / TSV', hint: 'header row; comma, tab or ; separated. Pick a column, or * for the row as an object' },
];

function settingsSafe() { try { return getSettings() || {}; } catch { return {}; } }

// ---------------------------------------------------------------- view

export function mountBatchView(root) {
  const saved = loadLS(LS_KEY, null);
  const job = {
    questions: saved?.questions || null,
    options: saved?.options || { model: settingsSafe().defaultModel || 'openjev-latest', steps: null, samples: null, think: null, sequential: false },
    format: saved?.format || 'lines',
    column: saved?.column || '',  // the CSV column
    field: saved?.field || '*',   // the JSONL field ('*' = the whole value); apart from column so a stale CSV pick never applies
    input: saved?.input || '',
    concurrency: saved?.concurrency || 1,
    rows: saved?.rows || [],
    startedAt: saved?.startedAt || null,
    finishedAt: saved?.finishedAt || null,
    title: saved?.title || '',
    // what the current table was run with: {questions, options, imagesMeta, at[, approx]}. The table,
    // stats, exports and the state detail read this, so editing the set after a run changes nothing there
    sent: saved?.sent || null,
  };
  let images = [];
  let sentImages = null;  // the run's image data URLs; memory only, never persisted
  let detail = null;      // the open state-detail modal: { i, render, close }
  const runQs = () => job.sent?.questions || job.questions || {};
  let running = false, paused = false, abort = null;
  let sort = { key: null, dir: -1 }, sortBy = 'value', filterOn = false, filterX = 0.6;
  let alive = true;
  let models = [];
  let tick = null;
  const pick = () => (job.format === 'jsonl' ? job.field : job.column);
  const stem = (n) => String(n || '').replace(/\.[^.]+$/, '');

  const view = el('div', { class: 'batch-view' });
  root.appendChild(view);
  const left = el('div', { class: 'batch-left' });
  const right = el('div', { class: 'batch-right' });
  view.append(left, right);
  left.id = 'batch-left';

  // file drops anywhere on the view load states (images go to the tray). Capture phase, so the image
  // zone's own drop handler never sees a file drop; text drags carry no 'Files' and keep the textarea's native drop.
  // position: fixed, so neither the grid nor .batch-left's overflow clips it
  const dropOverlay = el('div', { class: 'drop-overlay', 'aria-hidden': 'true' }, el('div', { class: 'drop-inner' },
    icon('upload', 28), el('div', {}, 'Drop files to load states'),
    el('div', { class: 'faint mono' }, 'JSONL · TXT · CSV · TSV · JSON · batch export · up to 5 MB'),
    el('div', { class: 'faint' }, 'images go to the image tray (up to 8)')));
  view.append(dropOverlay);
  // dragover keeps firing while a drag hovers, so a watchdog ends the overlay when it stops; counting
  // dragenter/dragleave fails when a redraw replaces the node under the cursor
  let dragT = null;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  const endDrag = () => { clearTimeout(dragT); dragT = null; view.classList.remove('dropping'); };
  const onDragEnter = (e) => { if (!hasFiles(e)) return; e.preventDefault(); view.classList.add('dropping'); };
  const onDragOver = (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = 'copy';
    view.classList.add('dropping');
    clearTimeout(dragT); dragT = setTimeout(endDrag, 300);
  };
  // a drag that leaves the window ends at once; other leaves are left to the watchdog (Safari reports a null relatedTarget)
  const onDragLeave = (e) => { if (hasFiles(e) && e.relatedTarget && !view.contains(e.relatedTarget)) endDrag(); };
  const onDrop = (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.stopPropagation(); endDrag(); importFiles([...e.dataTransfer.files]); };
  const dnd = [['dragenter', onDragEnter], ['dragover', onDragOver], ['dragleave', onDragLeave], ['drop', onDrop]];
  for (const [t, f] of dnd) view.addEventListener(t, f, true);

  // collapsed, the header row becomes a 40px rail holding the same toggle (focus never lands on a hidden element)
  let layout = { leftCollapsed: false, ...loadLS(UI_KEY, {}) };
  const collapseBtn = el('button', { class: 'icon-btn batch-collapse', type: 'button', 'aria-controls': 'batch-left', onclick: () => setCollapsed(!layout.leftCollapsed) }, icon('sidebar', 16));
  function setCollapsed(v, save = true) {
    layout.leftCollapsed = !!v;
    view.classList.toggle('left-collapsed', layout.leftCollapsed);
    const label = layout.leftCollapsed ? 'Show the question pane' : 'Hide the question pane';
    collapseBtn.setAttribute('aria-expanded', String(!layout.leftCollapsed));
    collapseBtn.title = label; collapseBtn.setAttribute('aria-label', label);
    if (save) saveLS(UI_KEY, layout);
  }

  // ---------------- left pane
  const qHost = el('div', { class: 'batch-qe' });
  let qe = null;
  const optsRow = el('div', { class: 'row batch-opts' });
  const fmtSel = el('select', { class: 'select sm', 'aria-label': 'states format', onchange: (e) => { job.format = e.target.value; refreshInput(); persist(); } },
    FORMATS.map((f) => el('option', { value: f.id, selected: job.format === f.id, title: f.hint }, f.label)));
  const colSel = el('select', { class: 'select sm', hidden: true, 'aria-label': 'column or field',
    onchange: (e) => { if (job.format === 'jsonl') job.field = e.target.value; else job.column = e.target.value; refreshInput(); persist(); } });
  // a very large input re-parses off the keystroke
  let inputT = null;
  const flushInput = () => { if (inputT) { clearTimeout(inputT); inputT = null; refreshInput(); } };
  const ta = el('textarea', { class: 'textarea mono batch-input', rows: 10, spellcheck: 'false', value: job.input, placeholder: 'One state per line…',
    oninput: () => {
      job.input = ta.value; clearTimeout(inputT); inputT = null;
      if (ta.value.length > 200_000) inputT = setTimeout(() => { inputT = null; refreshInput(); }, 150); else refreshInput();
      persistSoon();
    },
    // a file copied in Finder pastes as files without text
    onpaste: (e) => {
      const files = [...(e.clipboardData?.files || [])];
      if (!files.length || e.clipboardData.getData('text')) return;
      e.preventDefault(); importFiles(files);
    } });
  const fileInput = el('input', { type: 'file', multiple: true, hidden: true, accept: ACCEPT,
    onchange: (e) => { const fs = [...e.target.files]; e.target.value = ''; importFiles(fs); } });
  const uploadBtn = el('button', { class: 'btn sm ghost', type: 'button', title: 'Load states from files: JSONL, TXT, CSV/TSV, JSON or a batch export. Read in this browser; or drop files anywhere here',
    onclick: () => fileInput.click() }, icon('upload', 14), ' Upload file');
  const inputInfo = el('div', { class: 'mono faint batch-info' });
  const tplSel = el('select', { class: 'select sm', onchange: async (e) => { const t = getTemplate(e.target.value); e.target.value = ''; if (t) await loadTemplateJob(t); } });
  function fillTplSel() {
    tplSel.textContent = '';
    tplSel.append(el('option', { value: '' }, 'load template…'),
      ...allTemplates().map((t) => el('option', { value: t.id }, `${t.title} (${t.batchStates?.length || 0})${isUserTemplate(t) ? ' · mine' : ''}`)));
  }
  fillTplSel();
  const offTpl = on('oj:templates-changed', fillTplSel);
  const zone = imageZone({ onChange: (v) => { images = v; refreshInput(); } });

  left.append(
    el('div', { class: 'row batch-lh' }, collapseBtn,
      el('h2', { class: 'batch-h', onclick: () => { if (layout.leftCollapsed) setCollapsed(false); } }, icon('batch', 18), ' Batch'),
      el('span', { class: 'spacer' }), tplSel),
    el('div', { class: 'batch-sec' }, el('div', { class: 'batch-label' }, 'Question set'), qHost),
    el('div', { class: 'batch-sec' }, el('div', { class: 'batch-label' }, 'Options'), optsRow),
    el('div', { class: 'batch-sec' }, el('div', { class: 'row batch-label batch-states-head' }, 'States', el('span', { class: 'spacer' }), uploadBtn, fmtSel, colSel), ta, inputInfo, fileInput),
    el('div', { class: 'batch-sec' }, zone.element));
  setCollapsed(layout.leftCollapsed, false);

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

  /** The column/field select's options: CSV columns then '*', or '*' then the JSONL keys → [value, label][] */
  const pickChoices = (format, columns) => (format === 'csv'
    ? [...columns.map((c) => [c, c]), ['*', '* (row as JSON)']]
    : [['*', '* (whole object)'], ...columns.map((c) => [c, c])]);
  const delimNote = (d) => (d === '\t' ? 'tab-separated' : d === ';' ? ';-separated' : '');

  let parsed = { states: [], errors: [], skipped: 0 };
  function refreshInput() {
    parsed = parseStates(job.input, job.format, pick());
    const cols = parsed.columns || [];
    colSel.hidden = !(job.format === 'csv' || (job.format === 'jsonl' && cols.length));
    if (!colSel.hidden) {
      colSel.textContent = '';
      const cur = job.format === 'csv' ? parsed.column || job.column : parsed.column;
      for (const [v, label] of pickChoices(job.format, cols)) colSel.appendChild(el('option', { value: v, selected: v === cur }, label));
    }
    ta.placeholder = FORMATS.find((f) => f.id === job.format)?.hint || '';
    const qs = qe ? qe.get() : job.questions || {};
    // estimateTokens summed per state, with the question set stringified once (5000 states on every keystroke)
    const n = parsed.states.length;
    let est = 0;
    try {
      const qChars = JSON.stringify(qs ?? {}).length;
      let chars = 0;
      for (const st of parsed.states) chars += JSON.stringify(st.state ?? '').length;
      est = n ? Math.ceil((chars + n * qChars) / 3.6) + IMAGE_TOKENS_EST * images.length * n : 0;
    } catch { est = 0; }
    const mult = (job.options.samples || 1) * (Number.isFinite(job.options.think) && job.options.think > 0 ? 2 : 1);
    if (!job.input.trim()) inputInfo.textContent = '0 states · type, paste, drop files or Upload file';
    else {
      const skip = parsed.skipped ? (job.format === 'jsonl' ? ` · ${parsed.skipped} skipped (no "${parsed.column}")` : job.format === 'csv' ? ` · ${parsed.skipped} empty rows` : '') : '';
      const delim = job.format === 'csv' && delimNote(parsed.delimiter) ? ` · ${delimNote(parsed.delimiter)}` : '';
      inputInfo.textContent = `${n} states · ≈ ${fmtTokens(est * mult)} input tok est${mult > 1 ? ` (×${mult} for samples/think)` : ''}${images.length ? ` · ${images.length} image${images.length > 1 ? 's' : ''} each` : ''}${skip}${delim}${parsed.errors.length ? ` · ${parsed.errors.length} parse errors: ${parsed.errors[0]}` : ''}`;
    }
    inputInfo.classList.toggle('err', parsed.errors.length > 0);
    if (!running) drawControls();
  }

  async function loadTemplateJob(t, force = false) {
    if (!force && job.rows.length && !(await confirmDialog(`Replace the current batch with "${t.title}"? The current results are discarded.`))) return;
    job.questions = JSON.parse(JSON.stringify(t.questions));
    qe.set(job.questions);
    job.options = { model: job.options.model || 'openjev-latest', steps: null, samples: null, think: null, sequential: false, ...(t.options || {}) };
    const states = t.batchStates || [];
    if (t.batchInput && typeof t.batchInput.input === 'string') {
      // a ready-made {format, input}, e.g. a conversation handoff: used as is
      job.format = FORMATS.some((f) => f.id === t.batchInput.format) ? t.batchInput.format : 'lines';
      job.input = t.batchInput.input;
    } else if (t.batchFormat === 'jsonl' && !t.stateIsJson) {
      ({ format: job.format, input: job.input } = statesToBatchInput(states));
      if (job.format !== 'jsonl') { job.format = 'jsonl'; job.input = states.map((x) => JSON.stringify(x)).join('\n'); }
    } else {
      const multiline = states.some((x) => x.includes('\n'));
      job.format = t.stateIsJson ? 'jsonl' : multiline ? 'blocks' : 'lines';
      job.input = states.join(multiline && !t.stateIsJson ? '\n\n' : '\n');
    }
    job.rows = []; job.field = '*'; job.sent = null; sentImages = null; job.title = t.title; job.startedAt = null; job.finishedAt = null;
    ta.value = job.input; fmtSel.value = job.format;
    if (Array.isArray(t.imageRefs) && t.imageRefs.length) { images = t.imageRefs.slice(0, 8); zone.set(images); }
    else if (t.images === 'demo-shapes') { images = [await demoShapesImage()]; zone.set(images); } else { images = []; zone.set([]); }
    drawOptions(); refreshInput(); drawResults(); persist();
    toast(`Loaded ${t.title}: ${parseStates(job.input, job.format, pick()).states.length} states`, { kind: 'ok' });
    return true;
  }

  // ---------------- states from files (parsed in batchImport.js; read here, never uploaded)
  let importing = false, importModal = null;
  async function importFiles(files) {
    if (!files.length) return;
    if (importing) { toast('Still reading the previous files', { kind: 'info' }); return; }
    importing = true;
    try {
      const imgs = files.filter(isImageFile), rest = files.filter((f) => !isImageFile(f));
      if (imgs.length) await zone.add(imgs);  // the zone toasts its own limits and errors; onChange updates `images`
      if (!rest.length || !alive) return;
      const results = [];
      for (const f of rest) {
        if (f.size > MAX_FILE_BYTES) { results.push({ kind: 'reject', name: f.name, message: `${f.name} is ${fmtBytes(f.size)}; files over ${fmtBytes(MAX_FILE_BYTES)} are not read. Split it or keep the first rows.` }); continue; }
        try {
          const r = await readFileText(f);
          results.push(r.binary ? { kind: 'reject', name: f.name, message: `${f.name} is not a text file` }
            : { ...parseUpload(r.text, f.name, f.type), bytes: f.size, encodingNote: r.note });
        } catch (e) { results.push({ kind: 'reject', name: f.name, message: `${f.name}: ${e.message || e}` }); }
      }
      if (!alive) return;
      if (!results.some((r) => r.kind !== 'reject')) { for (const r of results) toast(r.message, { kind: r.warn ? 'warn' : 'err', timeout: 5000 }); return; }
      openImportPreview(results, imgs.length);
    } finally { importing = false; }
  }

  const fmtLabel = (format, delimiter) => (format === 'csv' ? (delimiter === '\t' ? 'TSV' : 'CSV') : FORMATS.find((f) => f.id === format)?.label || format);

  function openImportPreview(results, imageCount) {
    importModal?.close();
    const ok = results.filter((r) => r.kind !== 'reject');
    const blank = !job.input.trim();
    const restorable = ok.length === 1 && ok[0].kind === 'batch';
    const pickOf = (r) => ({ format: r.format, input: r.input, column: r.column ?? '' });
    // files merge left to right, in the order the browser gives them; the Send select edits `incoming.column`
    const incoming = ok.slice(1).reduce((acc, r) => mergeInputs(acc, pickOf(r)), pickOf(ok[0]));
    const cur = { format: job.format, input: job.input, column: pick() };
    const parse = (x) => parseStates(x.input, x.format, x.format === 'jsonl' ? x.column || '*' : x.column);

    const fileRow = (r) => {
      if (r.kind === 'reject') return el('div', { class: 'batch-imp-file err' }, icon('alert', 14), el('div', { class: 'batch-imp-name' }, r.message));
      const ic = r.kind === 'batch' || r.format === 'jsonl' || /\.json$/i.test(r.name) ? 'json' : r.format === 'csv' ? 'code' : 'paperclip';
      const details = [r.errors?.[0], ...(r.notes || []), r.encodingNote, r.by === 'content' ? 'by content' : r.by === 'mime' ? 'by file type' : ''].filter(Boolean);
      return el('div', { class: 'batch-imp-file' }, icon(ic, 14), el('div', { class: 'batch-imp-name mono', title: r.name }, r.name),
        el('div', { class: 'batch-imp-meta' }, [r.bytes != null ? fmtBytes(r.bytes) : null, r.kind === 'batch' ? 'batch export' : fmtLabel(r.format, r.delimiter)].filter(Boolean).join(' · '), ' · ',
          el('span', { class: r.count ? '' : 'batch-imp-warn' }, `${r.count} state${r.count === 1 ? '' : 's'}`),
          r.skipped ? el('span', { class: 'batch-imp-warn' }, ` · ${r.skipped} skipped`) : null),
        details.length ? el('div', { class: 'batch-imp-meta' }, details.join(' · ')) : null);
    };

    const batchLine = () => {
      const d = ok[0];
      const opts = Object.entries(d.options || {}).filter(([k, v]) => k !== 'model' && v !== null && v !== false && v !== undefined).map(([k, v]) => `${k} ${v}`).join(' · ');
      const qn = Object.keys(d.questions || {}).length;
      return el('div', {}, `Batch export "${d.title || stem(d.name)}": ${qn} question${qn === 1 ? '' : 's'}, ${d.count} states${opts ? `, options ${opts}` : ''}`,
        d.imageCount ? `, the run used ${d.imageCount} image${d.imageCount > 1 ? 's' : ''}: attach ${d.imageCount > 1 ? 'them' : 'it'} again` : '');
    };

    // the Send select: which CSV column or JSONL field the incoming states send
    const inParsed = parse(incoming);
    const inCols = inParsed.columns || [];
    let sendSel = null;
    if (incoming.format === 'csv' || (incoming.format === 'jsonl' && inCols.length)) {
      const curPick = incoming.format === 'csv' ? inParsed.column : inParsed.column || '*';
      sendSel = el('select', { class: 'select sm', 'aria-label': 'column or field to send',
        onchange: (e) => { incoming.column = e.target.value; drawSummary(); } },
      pickChoices(incoming.format, inCols).map(([v, label]) => el('option', { value: v, selected: v === curPick }, label)));
      incoming.column = curPick;
    }

    const sum = el('div', { class: 'batch-imp-sum' });
    const cutNote = (r, a) => {
      if (!r && !a) return null;
      const what = r && a && r !== a ? `${fmtInt(r)} dropped on ${blank ? 'Load' : 'Replace'}, ${fmtInt(a)} on Append`
        : r && (!a || r === a) ? `${fmtInt(r)} dropped${a ? '' : blank ? '' : ` on Replace`}` : `${fmtInt(a)} dropped on Append`;
      return el('div', { class: 'batch-imp-warn' }, `Only the first ${fmtInt(MAX_STATES)} are kept: ${what}.`);
    };
    let btns = {};
    function drawSummary() {
      const rep = capInput(incoming), app = blank ? null : capInput(mergeInputs(cur, incoming));
      const nRep = parse(rep).states.length, nApp = app ? parse(app).states.length : 0;
      sum.textContent = '';
      sum.append(...[
        blank ? null : el('div', {}, `Now: ${parse(cur).states.length} states (${fmtLabel(cur.format, parse(cur).delimiter)}).`),
        el('div', {}, `${blank ? 'Load' : 'Replace'}: ${nRep} states (${fmtLabel(rep.format, parse(rep).delimiter)}).`),
        app ? el('div', {}, `Append: ${nApp} states (${app.format === cur.format ? '' : 'as '}${fmtLabel(app.format, parse(app).delimiter)}).`) : null,
        cutNote(rep.truncated, app?.truncated || 0),
        nRep ? null : el('div', { class: 'batch-imp-warn' }, 'No states to load.')].filter(Boolean));
      // nothing to add: only Cancel (and Restore, which also brings the question set) stay
      if (btns.replace) btns.replace.disabled = !nRep;
      if (btns.append) btns.append.disabled = !nRep;
    }

    function commit(mode) {
      const next = capInput(mode === 'append' ? mergeInputs(cur, incoming) : incoming);
      const n = parse(next).states.length;
      job.format = next.format; job.input = next.input;
      if (next.format === 'csv') job.column = next.column || ''; else if (next.format === 'jsonl') job.field = next.column || '*';
      if (mode === 'replace' && ok.length === 1) job.title = stem(ok[0].name);  // labels "<title> #i" in Stats
      ta.value = job.input; fmtSel.value = job.format;
      setCollapsed(false, false); refreshInput(); persist();
      const label = ok.length === 1 ? ok[0].name : `${ok.length} files`;
      toast(`${mode === 'append' ? 'Appended' : 'Loaded'} ${n} states from ${label} (${fmtLabel(next.format, parse(next).delimiter)})${next.truncated ? `; ${fmtInt(next.truncated)} over the ${fmtInt(MAX_STATES)} limit dropped` : ''}`,
        { kind: next.truncated ? 'warn' : 'ok', timeout: next.truncated ? 5000 : 2500 });
    }

    async function restore(_ev, handle) {
      // loadTemplateJob empties job.rows, which running workers still write to
      if (running) { toast('Abort or finish the run first', { kind: 'warn' }); return false; }
      const d = ok[0];
      const opts = { ...(d.options || {}) };
      if (models.length && !models.some((m) => m.name === opts.model)) delete opts.model;
      const keep = imageCount ? zone.get() : null;  // images dropped with the export stay attached
      handle.close();
      setCollapsed(false, false);
      const done = await loadTemplateJob({ title: d.title || stem(d.name), questions: d.questions, options: opts, batchInput: { format: d.format, input: d.input } });
      if (done && keep?.length && alive) { images = keep; zone.set(keep); refreshInput(); persist(); }
    }

    const body = el('div', { class: 'batch-imp' },
      results.map(fileRow),
      imageCount ? el('div', { class: 'faint' }, `${imageCount} image${imageCount > 1 ? 's' : ''} sent to the image tray`) : null,
      restorable ? batchLine() : null,
      sendSel ? el('label', { class: 'row batch-imp-send' }, el('span', {}, incoming.format === 'csv' ? 'Send column' : 'Send field'), sendSel) : null,
      sum,
      el('div', { class: 'faint' }, 'Read in this browser. Nothing is sent until you press Run. Results in the table stay until the next Run.'));
    const actions = [{ label: 'Cancel', kind: 'ghost' }];
    if (!blank) actions.push({ label: 'Append', onClick: () => commit('append') });
    actions.push({ label: blank ? 'Load' : 'Replace', kind: restorable ? undefined : 'primary', onClick: () => commit('replace') });
    if (restorable) actions.push({ label: 'Restore batch', kind: 'primary', onClick: restore });
    const m = openModal({ title: 'Load states', body, actions, className: 'batch-import-modal', onClose: () => { if (importModal === m) importModal = null; } });
    importModal = m;
    for (const b of body.closest('.modal')?.querySelectorAll('.modal-foot .btn') || []) {
      const t = b.textContent;
      if (t === 'Append') btns.append = b; else if (t === 'Replace' || t === 'Load') btns.replace = b;
    }
    drawSummary();
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

  // from the run's snapshot; the live editor and images only before the first run
  function buildBody(st, sent = job.sent) {
    const o = sent?.options || job.options;
    const body = { model: o.model || 'openjev-latest', state: st.state, questions: sent ? sent.questions : job.questions };
    const imgs = sent ? sentImages || [] : images.map((i) => i.dataUrl).filter(Boolean);
    if (imgs.length) body.images = imgs;
    if (o.steps != null) body.steps = o.steps;
    if (o.samples != null) body.samples = o.samples;
    if (o.think != null) body.think = o.think;
    if (o.sequential) body.sequential = true;
    return body;
  }

  async function start(resume, only = null) {
    flushInput();
    const v = qe.validate();
    if (!v.valid && !(await confirmDialog(`The question set has ${v.errors.length} error${v.errors.length > 1 ? 's' : ''} (${v.errors[0]?.message}). Send anyway?`))) return;
    job.questions = qe.get();
    const snapshot = () => {
      const imgs = images.filter((i) => i.dataUrl);
      job.sent = { questions: JSON.parse(JSON.stringify(job.questions)), options: JSON.parse(JSON.stringify(job.options)),
        imagesMeta: imgs.map(({ name, type, bytes, width, height }) => ({ name, type, bytes, width, height })), at: Date.now() };
      sentImages = imgs.map((i) => i.dataUrl);
    };
    if (!resume) {
      if (job.rows.some((r) => r.status === 'ok') && !(await confirmDialog('Discard the current results and run again?'))) return;
      snapshot();
      job.rows = parsed.states.map((s0, i) => ({ i: i + 1, state: s0.state, isJson: s0.isJson, status: 'queued' }));
      job.startedAt = Date.now(); job.finishedAt = null;
    } else {
      // one table, one question set: Continue sends the run's set, not the edited one
      if (!job.sent) snapshot();
      else {
        const changed = JSON.stringify(job.questions) !== JSON.stringify(job.sent.questions) || JSON.stringify(job.options) !== JSON.stringify(job.sent.options);
        if (changed && !(await confirmDialog('The question set or options changed since this run started. Continue with the run\'s original set? (Run starts over with the edited set.)'))) return;
        if (!sentImages) {
          // after a reload the run's images are gone: send what is attached now
          const imgs = images.filter((i) => i.dataUrl);
          sentImages = imgs.map((i) => i.dataUrl);
          job.sent.imagesMeta = imgs.map(({ name, type, bytes, width, height }) => ({ name, type, bytes, width, height }));
        }
      }
      // a single-row retry requeues just that row, and only now that both confirms have passed
      if (only) only.status = 'queued';
      else for (const r of job.rows) if (r.status === 'aborted') r.status = 'queued';
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
        const first = res;
        let retried = false;
        if (!res.ok && autoRetry && (res.status === 529 || res.status === 429) && !abort.signal.aborted) {
          await sleep(((res.error?.retryAfter ?? 1) * 1000) || 1000, abort.signal);
          if (!abort.signal.aborted) { res = await systemOne(body, { signal: abort.signal, source: 'batch', label: `${job.title || 'batch'} #${row.i} retry` }); retried = true; }
        }
        if (!alive) return;
        if (res.error?.kind === 'aborted') { row.status = 'aborted'; drawRow(row); return; }
        row.status = res.ok ? 'ok' : 'error';
        row.response = res.ok ? res.data || null : null;  // the whole parsed body (the detail's Response tab); errors keep theirs in error.raw
        row.answers = res.data?.answers || null;
        row.model = res.data?.model || null;
        row.usage = res.data?.usage || null;
        row.error = res.error || null;
        row.http = res.http || null;
        row.clientMs = res.http?.clientMs ?? null;
        row.attempts = retried ? 2 : 1;
        if (retried) row.retriedFrom = { status: first.status, kind: first.error?.kind }; else delete row.retriedFrom;
        try { row.bodyHash = await sha256Hex(JSON.stringify(body)); } catch { row.bodyHash = null; }
        row.stats = row.answers ? statsFor(row.answers, runQs()) : null;
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
  function qids() { return Object.keys(runQs()); }

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
    const tr = el('tr', { class: [`st-${r.status}`, 'batch-row'], title: 'Open details', onclick: () => openDetail(r) },
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
    if (detail && detail.i === r.i) scheduleDetail();  // live: pending → ok, retry → pending → ok
  }

  // the detail re-renders off the run loop (a view bug must not wedge the batch) and coalesced
  let detailRaf = 0;
  function scheduleDetail() {
    if (detailRaf) return;
    detailRaf = requestAnimationFrame(() => {
      detailRaf = 0;
      try { detail?.render(); } catch (e) { console.warn('[batch] detail render failed', e); }
    });
  }

  // ---------------- state detail (full-screen modal: the decision card + the inspector tabs)
  /** A row as a System One turn, request rebuilt from the run's snapshot. Pure. */
  function rowToTurn(r) {
    const sent = job.sent || { questions: runQs(), options: job.options, imagesMeta: [] };
    const request = buildBody(r, sent);
    const n = sent.imagesMeta?.length || 0;
    // null entries render as "missing" tiles (the data is not kept after a reload)
    if (n) request.images = sentImages && sentImages.length === n ? sentImages : sent.imagesMeta.map(() => null);
    const status = r.status === 'ok' || r.status === 'error' || r.status === 'aborted' ? r.status : 'pending';
    return {
      id: `batch_${r.i}`, kind: 'systemone', createdAt: r.http?.startedAt || job.startedAt || Date.now(), status, queued: r.status === 'queued',
      source: 'batch', label: `${job.title || 'batch'} #${r.i}`, parentTurnId: null, request, imagesMeta: sent.imagesMeta || [],
      response: r.response || (r.answers ? { model: r.model || request.model, answers: r.answers, usage: r.usage } : null),
      http: r.http || null, error: r.error || null, bodyHash: r.bodyHash || null, labels: r.labels || {},
    };
  }

  function openDetail(r) {
    detail?.close();
    // navigation follows the table as it was at open (sort + filter); a filtered-out row falls back to all rows
    let order = sortedRows().map((x) => x.i);
    if (!order.includes(r.i)) order = job.rows.map((x) => x.i);
    let pos = Math.max(0, order.indexOf(r.i));
    let inspCleanup = null;
    let detailTab = 'Request';  // not the module-global last tab: Repro would refetch the whole log on every step
    const titleEl = el('span', { class: 'bd-title' });
    const bar = el('div', { class: 'row bd-bar' });
    // built once: re-creating them in render() would drop keyboard focus on every step
    const prevBtn = el('button', { class: 'btn sm ghost', type: 'button', title: 'Previous state (Left / K)', 'aria-label': 'Previous state', onclick: () => go(-1) }, icon('chevron-up', 14));
    const nextBtn = el('button', { class: 'btn sm ghost', type: 'button', title: 'Next state (Right / J)', 'aria-label': 'Next state', onclick: () => go(1) }, icon('chevron-down', 14));
    const barExtra = el('span', { class: 'row bd-extra' });
    const main = el('div', { class: 'bd-main' });
    const side = el('div', { class: 'bd-side' });
    // focus lands here (modal.js picks [autofocus] first), not in the JSON editor, so the arrow keys navigate
    const host = el('div', { class: 'bd', tabindex: '-1', autofocus: '' }, bar, el('div', { class: 'bd-grid' }, main, side));
    const go = (d) => { const p = clamp(pos + d, 0, order.length - 1); if (p !== pos) { pos = p; render(); } };
    const copyBtn = (ic, label, fn) => el('button', { class: 'btn sm ghost', type: 'button', title: label, onclick: fn }, icon(ic, 14), ` ${label}`);

    function render() {
      const row = job.rows.find((x) => x.i === order[pos]);
      if (!row) return;
      const turn = rowToTurn(row);
      titleEl.textContent = '';
      titleEl.append(`State #${row.i}`, el('span', { class: 'faint mono bd-pos' }, `${pos + 1} of ${order.length}`), el('span', { class: 'badge' }, row.status));
      let config = null;
      try { config = getCachedConfig(); } catch { /* defaults */ }
      const imgsMissing = Array.isArray(turn.request.images) && turn.request.images.some((x) => x === null);
      prevBtn.disabled = pos === 0;
      nextBtn.disabled = pos >= order.length - 1;
      if (!bar.firstChild) bar.append(prevBtn, nextBtn, el('span', { class: 'spacer' }), barExtra);
      barExtra.textContent = '';
      barExtra.append(...[
        row.attempts > 1 ? el('span', { class: 'badge', title: 'the first attempt was answered with a retryable status and sent again once' }, `auto-retried after ${row.retriedFrom?.status || 'overload'}`) : null,
        job.sent?.approx ? el('span', { class: 'faint' }, 'request rebuilt from the current question set') : null,
        imgsMissing ? el('span', { class: 'faint' }, 'images not kept after reload') : null,
        turn.status === 'ok' ? copyBtn('copy', 'Copy as text', () => copyText(turnToMarkdown(turn, { turnIndex: row.i - 1 }))) : null,
        turn.response ? copyBtn('json', 'Copy JSON', () => copyText(JSON.stringify(turn.response, null, 2))) : null,
        copyBtn('terminal', 'Copy curl', () => copyText(buildSnippet('curl', turn, config))),
        copyBtn('code', 'Copy Python', () => copyText(buildSnippet('python', turn, config))),
      ].filter(Boolean));
      const ctx = { conversation: null, settings: settingsSafe(), turnIndex: row.i - 1, parentTurn: null, compact: false,
        onLabel: (_q, _v, labels) => { row.labels = labels; persistSoon(); } };
      main.textContent = '';
      main.append(renderTurnCard(turn, ctx, { onRetry: running ? undefined : () => start(true, row) }));
      inspCleanup?.();
      inspCleanup = null;
      side.textContent = '';
      inspCleanup = mountInspector(side, { turn, conversation: null, tab: detailTab, onTab: (t) => { detailTab = t; } });
      // a rebuilt or newly disabled control must not leave focus on <body>, outside the dialog
      const ae = document.activeElement;
      if (!host.contains(ae) || ae.disabled) host.focus({ preventScroll: true });
    }

    function onNav(ev) {
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (ev.target.closest?.('input, textarea, select, [contenteditable="true"], .jse-main, .cm-editor')) return;
      // only while this modal is on top (a confirm may be stacked over it)
      const bd = host.closest('.modal-backdrop');
      if (!host.isConnected || !bd || bd.parentNode.lastElementChild !== bd) return;
      const d = ev.key === 'ArrowLeft' || ev.key === 'k' ? -1 : ev.key === 'ArrowRight' || ev.key === 'j' ? 1 : 0;
      if (!d) return;
      ev.preventDefault();
      go(d);
    }

    const m = openModal({
      title: titleEl, body: host, className: 'batch-detail',
      onClose: () => { document.removeEventListener('keydown', onNav); inspCleanup?.(); inspCleanup = null; if (detail?.close === m.close) detail = null; },
    });
    titleEl.closest('.modal')?.setAttribute('aria-label', `State #${r.i}`);
    render();  // after open: the JSON editors mount into an attached node
    document.addEventListener('keydown', onNav);
    detail = { get i() { return order[pos]; }, render, close: m.close };
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
    const qs = runQs();
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
      el('span', { class: 'spacer' }), el('span', { class: 'faint' }, 'click a row for details'));
    results.append(tools, el('div', { class: 'batch-table-wrap scroll' }, el('table', { class: 'table batch-table' }, el('thead', {}, head), tbody, el('tfoot', {}, aggregates()))));
    const errRows = job.rows.filter((r) => r.status === 'error' && r.error).slice(0, 3);
    if (errRows.length) results.append(el('div', { class: 'batch-errs' }, el('div', { class: 'faint' }, `first errors (${job.rows.filter((r) => r.status === 'error').length} total)`),
      errRows.map((r) => el('div', {}, el('div', { class: 'mono faint' }, `#${r.i}`), renderError(r.error, { onRetry: () => start(true, r) })))));
    if (detail) scheduleDetail();  // run start / end changes statuses without drawRow
  }

  // ---------------- export
  function flatColumns() {
    const cols = [];
    for (const [qid, q] of Object.entries(runQs())) {
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
      format: 'ojui-batch', version: 1, exportedAt: Date.now(), title: job.title, questions: runQs(), options: job.sent?.options || job.options, imageCount: job.sent?.imagesMeta?.length ?? images.length,
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
  let persistWarnedAt = -1;  // the input length the "too large to keep" toast was shown for
  function persist() {
    if (qe) job.questions = qe.get();
    // per row: the parsed response (answers live inside it), slim http, an error capped at 4 KB
    const slimHttp = (h) => (h ? { status: h.status, requestId: h.requestId, serverTiming: h.serverTiming, clientMs: h.clientMs, startedAt: h.startedAt, retryAfter: h.retryAfter, requestBytes: h.requestBytes, responseBytes: h.responseBytes } : null);
    const slimErr = (e) => (e && typeof e.raw === 'string' && e.raw.length > 4096 ? { ...e, raw: `${e.raw.slice(0, 4096)}…` } : e);
    const rows = job.rows.map(({ stats, http, answers, response, error, ...r }) => ({ ...r,
      response: response || (answers ? { model: r.model, answers, usage: r.usage } : null), http: slimHttp(http), error: slimErr(error) }));
    if (saveLS(LS_KEY, { ...job, rows, imageCount: images.length }) || saveLS(LS_KEY, { ...job, rows: [], imageCount: images.length })) return;
    // the input alone is over the localStorage quota (about 5 MB per origin, shared)
    if (persistWarnedAt === job.input.length) return;
    persistWarnedAt = job.input.length;
    toast(`This batch is too large to keep across reloads (${fmtBytes(job.input.length)} of states). Export the results before leaving.`, { kind: 'warn', timeout: 6000 });
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
    qe = mountQuestionEditor(qHost, {
      questions: job.questions, emit: false, tab: 'builder', onChange: (qs) => { job.questions = qs; refreshInput(); persistSoon(); },
      templateContext: () => {
        const ps = parseStates(job.input, job.format, pick()).states;
        const json = ps.some((x) => x.isJson);
        const first = json ? ps.find((x) => x.isJson) : ps[0];
        return { title: job.title, stateIsJson: json,
          state: first ? (json ? JSON.stringify(first.state, null, 2) : first.state) : '',
          batchStates: ps.map((x) => (typeof x.state === 'string' && !json ? x.state : JSON.stringify(x.state))),
          options: Object.fromEntries(Object.entries(job.options).filter(([k, v]) => k !== 'model' && v !== null && v !== false)) };
      },
    });
    // saves from before the snapshot: rebuild it from the saved set (the detail notes that)
    if (!job.sent && job.rows.length) job.sent = { questions: JSON.parse(JSON.stringify(job.questions)), options: JSON.parse(JSON.stringify(job.options)), imagesMeta: Array.from({ length: saved?.imageCount || 0 }, () => ({})), approx: true };
    for (const r of job.rows) {
      r.answers = r.response?.answers ?? r.answers ?? null;  // new saves keep answers inside response
      if (r.answers) r.stats = statsFor(r.answers, runQs());
      if (r.status === 'pending' || r.status === 'queued') r.status = 'aborted';
    }
    if (!job.input && !handoff) {
      const t = getTemplate('sentiment');
      job.input = t.batchStates.join('\n'); ta.value = job.input; job.title = 'Sentiment';
      if (!initialQs) { job.questions = t.questions; qe.set(t.questions); }
      else { job.input = ''; ta.value = ''; job.title = ''; }
    }
    drawOptions(); refreshInput(); drawControls(); drawResults();
    if (handoff) {
      // a template handoff replaces the job as before; a conversation handoff asks before dropping finished rows
      // show the pane (without overwriting the saved preference) so the loaded states are visible and focusable
      setCollapsed(false, false);
      await loadTemplateJob(handoff, handoff.source !== 'conversation');
      if (alive && !layout.leftCollapsed) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
    }
    try { const r = await listModels(); if (alive && r?.models?.length) { models = r.models; drawOptions(); } } catch { /* offline */ }
    if (saved?.imageCount && !images.length && !handoff) toast(`The last batch used ${saved.imageCount} image${saved.imageCount > 1 ? 's' : ''}; images are not saved, attach them again`, { kind: 'info', timeout: 4000 });
  })();

  return () => {
    detail?.close();
    importModal?.close();
    clearTimeout(dragT);
    for (const [t, f] of dnd) view.removeEventListener(t, f, true);
    endDrag();
    clearTimeout(inputT);
    alive = false;
    offTpl();
    if (abort) abort.abort();
    clearInterval(tick);
    clearTimeout(persistT);
    try { persist(); } catch { /* ignore */ }
    if (qe) qe.destroy();
  };
}


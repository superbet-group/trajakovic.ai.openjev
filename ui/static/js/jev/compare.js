// Compare view (builder C, #/compare/<convId>/<turnId>[/<turnId2>]): side-by-side runs of one
// request under variants (steps, samples, think, models, choice-order permutations), with
// per-question dot plots / aligned bars / E markers, max JSD against the reference and flips.

import { el, s, trunc, hashParts, isObj, shuffle, mulberry32, clamp, deepClone } from '/js/jev/util.js';
import { confColor, tip, bars, TYPE_COLOR } from '/js/jev/charts.js';
import { statsFor, answerText } from '/js/jev/renderers.js';
import { jsd, sha256Hex } from '/js/core/metrics.js';
import { getConversation, appendTurn } from '/js/core/store.js';
import { systemOne, listModels } from '/js/core/api.js';
import { renderError } from '/js/core/errors.js';
import { navigate } from '/js/core/router.js';
import { icon } from '/js/core/icons.js';
import { uid, fmtMs, fmtInt } from '/js/core/format.js';
import { toast } from '/js/core/toast.js';
import { promptDialog } from '/js/core/modal.js';

const LS_PRESETS = 'ojui.compare.presets.v1';
let queuedPreset = null;
/** Ask the next compare view mount to apply a preset (used by /compare [preset]). */
export function queueComparePreset(name) { queuedPreset = name || null; }

export const PRESETS = [
  { id: 'steps', label: 'steps 1 vs 4', make: () => [{ label: 'steps 1', o: { steps: 1 } }, { label: 'steps 4', o: { steps: 4 } }] },
  { id: 'samples', label: 'samples 1 vs 8', make: () => [{ label: 'samples 1', o: { samples: 1 } }, { label: 'samples 8', o: { samples: 8 } }] },
  { id: 'think', label: 'think 0 vs 512', make: () => [{ label: 'think 0', o: { think: 0 } }, { label: 'think 512', o: { think: 512 } }] },
  { id: 'models', label: 'models', make: null },
  { id: 'order', label: 'order sensitivity', make: null },
];

function loadUserPresets() { try { return JSON.parse(localStorage.getItem(LS_PRESETS) || '[]'); } catch { return []; } }
function saveUserPresets(v) { try { localStorage.setItem(LS_PRESETS, JSON.stringify(v)); } catch { /* ignore */ } }

/** Apply overrides to a request body. o: {model, steps, samples, think, sequential, shuffle, seed} (null → remove field). */
export function applyOverrides(base, o = {}) {
  const b = deepClone(base || {});
  if (o.model) b.model = o.model;
  for (const k of ['steps', 'samples', 'think']) {
    if (!(k in o)) continue;
    if (o[k] === null || o[k] === '' || o[k] === 'default') delete b[k]; else b[k] = Number(o[k]);
  }
  if ('sequential' in o) { if (o.sequential) b.sequential = true; else delete b.sequential; }
  if (o.shuffle && isObj(b.questions)) {
    const rnd = mulberry32(o.seed ?? 7);
    for (const q of Object.values(b.questions)) {
      if (q?.type === 'choice' && isObj(q.criteria)) {
        const keys = Object.keys(q.criteria);
        let perm = shuffle(keys, rnd);
        if (keys.length > 1) for (let tries = 0; tries < 5 && perm.every((k, i) => k === keys[i]); tries++) perm = shuffle(keys, rnd);
        q.criteria = Object.fromEntries(perm.map((k) => [k, q.criteria[k]]));
      }
    }
  }
  return b;
}

function aliasesOf(models) {
  // "Alias for the newest OpenJev release. Currently openjev-0.1." → openjev-latest ≡ openjev-0.1
  const groups = new Map();
  for (const m of models) {
    const d = String(m.description || '');
    const hit = /alias/i.test(d) ? models.find((x) => x.name !== m.name && d.includes(x.name)) : null;
    groups.set(m.name, hit ? hit.name : m.name);
  }
  return groups;
}

function turnToRun(t, label) {
  return { id: t.id, label, body: t.request, status: t.status, data: t.response, error: t.error, http: t.http, fromTurn: t, imagesMeta: t.imagesMeta };
}

/** Mount the compare view. */
export function mountCompareView(root, params = {}) {
  const parts = hashParts();
  const convId = params.convId || params.conv || parts[1];
  const turnId = params.turnId || params.turn || parts[2];
  const turnId2 = params.turnId2 || params.turn2 || parts[3];
  const view = el('div', { class: 'cmp-view' });
  root.appendChild(view);
  let alive = true;
  let abort = null;
  let conv = null, base = null;
  let runs = [];
  let refIdx = 0;
  let models = [];

  view.appendChild(el('div', { class: 'skeleton cmp-skel' }));

  (async () => {
    conv = convId ? await getConversation(convId) : null;
    base = conv?.turns?.find((t) => t.id === turnId) || null;
    if (!alive) return;
    view.textContent = '';
    if (!conv || !base) {
      view.append(el('div', { class: 'card cmp-empty' }, el('h2', {}, 'Compare'),
        el('p', { class: 'muted' }, 'Open this view from a turn\'s toolbar ("Compare…") or with /compare. It needs a conversation and a System One turn.'),
        el('p', { class: 'mono faint' }, `conv ${convId || '—'} · turn ${turnId || '—'}`)));
      return;
    }
    const idx = (id) => conv.turns.findIndex((t) => t.id === id) + 1;
    runs.push({ ...turnToRun(base, `base #${idx(base.id)}`), isBase: true });
    if (turnId2) {
      const t2 = conv.turns.find((t) => t.id === turnId2);
      if (t2) runs.push({ ...turnToRun(t2, `#${idx(t2.id)}`), isBase: true });
    }
    try { const r = await listModels(); models = r?.models || []; } catch { models = []; }
    if (!alive) return;
    if (!turnId2 && queuedPreset) { applyPreset(queuedPreset); queuedPreset = null; }
    draw();
  })();

  function variantRun(label, o) {
    return { id: uid('v'), label, o, body: applyOverrides(base.request, o), status: 'idle', data: null, error: null, http: null, imagesMeta: base.imagesMeta || [] };
  }
  function addVariants(list) {
    const room = 4 - runs.filter((r) => !r.isBase).length;
    if (room <= 0) { toast('At most 4 variants', { kind: 'warn' }); return; }
    list.slice(0, room).forEach((v) => runs.push(variantRun(v.label, v.o)));
    if (list.length > room) toast(`Added ${room} of ${list.length} variants (max 4)`, { kind: 'warn' });
  }
  function applyPreset(id) {
    const p = PRESETS.find((x) => x.id === id || x.label === id) || loadUserPresets().find((x) => x.label === id);
    if (!p) { toast(`Unknown compare preset "${id}"`, { kind: 'warn' }); return; }
    runs = runs.filter((r) => r.isBase);
    if (p.variants) { addVariants(p.variants); return; }
    if (p.id === 'models') {
      const group = aliasesOf(models);
      const baseKey = group.get(base.request.model) || base.request.model;
      const seen = new Set([baseKey]);
      const others = [];
      for (const m of models) {
        if (m.name === 'diffusiongemma-26b') continue;
        const key = group.get(m.name) || m.name;
        if (seen.has(key)) continue;
        seen.add(key); others.push({ label: m.name, o: { model: m.name } });
      }
      if (!others.length) { toast('No other System One model in /v1/models', { kind: 'warn' }); return; }
      addVariants(others);
    } else if (p.id === 'order') {
      const hasChoice = Object.values(base.request.questions || {}).some((q) => q?.type === 'choice' && isObj(q.criteria) && Object.keys(q.criteria).length > 1);
      if (!hasChoice) { toast('No choice question with 2+ options to permute', { kind: 'warn' }); return; }
      addVariants([1, 2, 3].map((i) => ({ label: `perm ${i}`, o: { shuffle: true, seed: 1000 + i * 7919 } })));
    } else addVariants(p.make());
  }

  async function runAll() {
    const todo = runs.filter((r) => !r.isBase && r.status !== 'ok');
    if (!todo.length) { toast('Nothing to run: add variants first', { kind: 'info' }); return; }
    abort = new AbortController();
    draw();
    for (const r of todo) {
      if (!alive || abort.signal.aborted) break;
      r.status = 'pending'; r.startedAt = Date.now(); draw();
      const res = await systemOne(r.body, { signal: abort.signal, source: 'compare', convId: conv.id, label: r.label });
      if (!alive) return;
      r.status = res.ok ? 'ok' : res.error?.kind === 'aborted' ? 'aborted' : 'error';
      r.data = res.data; r.error = res.error; r.http = res.http;
      try { r.bodyHash = await sha256Hex(JSON.stringify(r.body)); } catch { r.bodyHash = null; }
      draw();
    }
    abort = null;
    draw();
  }

  async function saveRuns() {
    const done = runs.filter((r) => !r.isBase && (r.status === 'ok' || r.status === 'error') && !r.saved);
    if (!done.length) { toast('No finished runs to save', { kind: 'info' }); return; }
    for (const r of done) {
      const turn = {
        id: uid('t'), kind: 'systemone', createdAt: r.startedAt || Date.now(), status: r.status, source: 'compare', label: r.label,
        parentTurnId: base.id, request: r.body, imagesMeta: r.imagesMeta || [], response: r.data || null, http: r.http || null,
        error: r.error || null, bodyHash: r.bodyHash || (await sha256Hex(JSON.stringify(r.body))), labels: {},
      };
      try { await appendTurn(conv.id, turn); r.saved = true; } catch (e) { toast(`Save failed: ${e.message || e}`, { kind: 'err' }); return; }
    }
    toast(`Saved ${done.length} run${done.length > 1 ? 's' : ''} to the conversation`, { kind: 'ok' });
    draw();
  }

  // ------------------------------------------------ drawing
  function draw() {
    if (!alive || !base) return;
    view.textContent = '';
    const running = !!abort;
    const idxBase = conv.turns.findIndex((t) => t.id === base.id) + 1;
    view.append(el('div', { class: 'cmp-top' },
      el('div', {},
        el('h2', { class: 'cmp-h' }, icon('compare', 18), ' Compare'),
        el('div', { class: 'muted' }, el('a', { href: `#/c/${encodeURIComponent(conv.id)}` }, trunc(conv.title || 'conversation', 40)), ` · base #${idxBase} · `,
          el('span', { class: 'mono' }, `${Object.keys(base.request?.questions || {}).length} questions · ${base.request?.model}`))),
      el('span', { class: 'spacer' }),
      turnId2 ? null : el('div', { class: 'row' },
        running ? el('button', { class: 'btn danger', type: 'button', onclick: () => abort?.abort() }, icon('stop', 14), ' Stop')
          : el('button', { class: 'btn primary', type: 'button', onclick: runAll }, icon('play', 14), ' Run all'),
        el('button', { class: 'btn', type: 'button', disabled: running, onclick: saveRuns }, icon('download', 14), ' Save runs to conversation'))));

    if (!turnId2) view.append(variantPanel(running));
    view.append(grid());
    view.append(footer());
    const failed = runs.filter((r) => r.status === 'error' && r.error);
    if (failed.length) {
      view.append(el('div', { class: 'cmp-errors' }, failed.map((r) => el('div', {}, el('div', { class: 'mono faint' }, r.label),
        renderError(r.error, { onRetry: () => { r.status = 'idle'; runAll(); } })))));
    }
  }

  function variantPanel(running) {
    const user = loadUserPresets();
    const presetRow = el('div', { class: 'row cmp-presets' }, el('span', { class: 'faint' }, 'presets'),
      PRESETS.map((p) => el('button', { class: 'chip', type: 'button', disabled: running, onclick: () => { applyPreset(p.id); draw(); } }, p.label)),
      user.map((p) => el('span', { class: 'chip cmp-user' }, el('button', { class: 'cmp-chipbtn', type: 'button', onclick: () => { applyPreset(p.label); draw(); } }, p.label),
        el('button', { class: 'cmp-chipx', type: 'button', title: 'delete preset', onclick: () => { saveUserPresets(loadUserPresets().filter((x) => x.label !== p.label)); draw(); } }, icon('x', 11)))),
      runs.some((r) => !r.isBase) ? el('button', { class: 'chip', type: 'button', title: 'save the current variants as a preset', onclick: async () => {
        const label = await promptDialog('Preset name', runs.filter((r) => !r.isBase).map((r) => r.label).join(' vs '));
        if (!label) return;
        saveUserPresets([...loadUserPresets().filter((x) => x.label !== label), { label, variants: runs.filter((r) => !r.isBase).map((r) => ({ label: r.label, o: r.o })) }]);
        draw();
      } }, icon('plus', 11), ' save as preset') : null);

    const f = { model: '', steps: '', samples: '', think: '', sequential: '', shuffle: false, label: '' };
    const modelSel = el('select', { class: 'select sm', onchange: (e) => { f.model = e.target.value; } },
      el('option', { value: '' }, `model: ${base.request.model}`), models.filter((m) => m.name !== 'diffusiongemma-26b').map((m) => el('option', { value: m.name }, m.name)));
    const numIn = (k, ph, min, max) => el('input', { class: 'input sm mono cmp-num', type: 'number', min, max, placeholder: ph, oninput: (e) => { f[k] = e.target.value; } });
    const seqSel = el('select', { class: 'select sm', onchange: (e) => { f.sequential = e.target.value; } },
      el('option', { value: '' }, 'sequential: as base'), el('option', { value: 'on' }, 'sequential on'), el('option', { value: 'off' }, 'sequential off'));
    const shuf = el('label', { class: 'row cmp-check' }, el('input', { type: 'checkbox', onchange: (e) => { f.shuffle = e.target.checked; } }), 'shuffle choice order');
    const labelIn = el('input', { class: 'input sm', placeholder: 'label (optional)', oninput: (e) => { f.label = e.target.value; } });
    const add = el('button', { class: 'btn sm', type: 'button', disabled: running, onclick: () => {
      const o = {};
      if (f.model) o.model = f.model;
      for (const k of ['steps', 'samples', 'think']) if (f[k] !== '') o[k] = f[k] === 'default' ? null : Number(f[k]);
      if (f.sequential) o.sequential = f.sequential === 'on';
      if (f.shuffle) { o.shuffle = true; o.seed = Math.floor(Math.random() * 1e9); }
      const auto = [o.model, ...['steps', 'samples', 'think'].filter((k) => k in o).map((k) => `${k} ${o[k] ?? 'default'}`), 'sequential' in o ? `seq ${o.sequential ? 'on' : 'off'}` : null, o.shuffle ? 'shuffled' : null].filter(Boolean).join(' · ');
      addVariants([{ label: f.label || auto || 'same as base', o }]);
      draw();
    } }, icon('plus', 14), ' Add variant');
    return el('div', { class: 'card cmp-panel' }, presetRow,
      el('div', { class: 'row cmp-form' }, modelSel, numIn('steps', 'steps 1–8', 1, 8), numIn('samples', 'samples 1–32', 1, 32), numIn('think', 'think 0–4096', 0, 4096), seqSel, shuf, labelIn, add),
      el('div', { class: 'faint' }, 'Runs go one at a time (MLX reads one request at a time). Divergence is the maximum Jensen–Shannon divergence against the base, in bits.'));
  }

  function refRun() {
    const i = runs.findIndex((r) => r.status === 'ok' && r.data);
    refIdx = i < 0 ? 0 : i;
    return runs[refIdx];
  }

  function grid() {
    const ref = refRun();
    const qs = base.request?.questions || {};
    const qids = Object.keys(qs);
    const allStats = runs.map((r) => (r.status === 'ok' && r.data ? statsFor(r.data.answers, r.body?.questions || qs) : null));
    const refStats = allStats[refIdx];
    const cols = `minmax(120px, 170px) repeat(${runs.length}, minmax(150px, 1fr)) 110px`;
    const g = el('div', { class: 'cmp-grid', style: { gridTemplateColumns: cols } });
    // header
    g.appendChild(el('div', { class: 'cmp-hcell cmp-corner faint' }, 'question'));
    runs.forEach((r, i) => {
      const u = r.data?.usage;
      g.appendChild(el('div', { class: ['cmp-hcell', `st-${r.status}`, i === refIdx && 'is-ref'] },
        el('div', { class: 'row' }, el('strong', { class: 'mono' }, trunc(r.label, 22)), i === refIdx ? el('span', { class: 'badge' }, 'ref') : null, el('span', { class: 'spacer' }),
          !r.isBase && !abort ? el('button', { class: 'icon-btn', type: 'button', title: 'remove variant', onclick: () => { runs = runs.filter((x) => x !== r); draw(); } }, icon('x', 12)) : null),
        el('div', { class: 'mono faint cmp-hmeta' },
          r.status === 'pending' ? el('span', { class: 'cmp-pending' }, 'running…') : r.status === 'idle' ? 'not run' : r.status === 'ok' ? `${fmtMs(r.http?.clientMs)} · ${fmtInt(u?.input_tokens ?? 0)} tok${u?.output_tokens ? ` +${fmtInt(u.output_tokens)}` : ''}` : `${r.error?.status || ''} ${r.error?.kind || r.status}`),
        overrideLine(r)));
    });
    g.appendChild(el('div', { class: 'cmp-hcell faint' }, 'max JSD'));
    for (const qid of qids) {
      const q = qs[qid] || {};
      g.appendChild(el('div', { class: 'cmp-qcell' }, el('span', { class: 'mono' }, trunc(qid, 22)), el('span', { class: 'chip viz-typechip', style: { '--viz-c': TYPE_COLOR[q.type] } }, q.type)));
      let maxJ = null, flipped = false;
      runs.forEach((r, i) => {
        const st = allStats[i]?.[qid];
        g.appendChild(el('div', { class: ['cmp-cell', i === refIdx && 'is-ref'] }, st ? cellViz(st, q, refStats?.[qid]) : el('span', { class: 'faint' }, r.status === 'pending' ? '…' : '—')));
        if (st && refStats?.[qid] && i !== refIdx) {
          const d = divergence(st, refStats[qid], q);
          if (d !== null) maxJ = Math.max(maxJ ?? 0, d);
          if (st.top !== refStats[qid].top) flipped = true;
        }
      });
      g.appendChild(el('div', { class: 'cmp-div' },
        maxJ === null ? el('span', { class: 'faint' }, '—') : tip(el('span', { class: 'mono cmp-jsd', style: { color: confColor(1 - clamp(maxJ / 0.3, 0, 1)), background: `color-mix(in oklch, ${confColor(1 - clamp(maxJ / 0.3, 0, 1))} 14%, transparent)` } }, maxJ < 0.0005 ? '0.000' : maxJ.toFixed(3)), 'max Jensen–Shannon divergence against the reference, bits (0 = identical, 1 = disjoint)'),
        flipped ? el('span', { class: 'badge viz-flip' }, 'flipped') : null));
    }
    return el('div', { class: 'cmp-grid-wrap scroll' }, g);
  }

  function overrideLine(r) {
    if (r.isBase) {
      const b = r.body || {};
      const bits = [b.steps && `steps ${b.steps}`, b.samples && `samples ${b.samples}`, Number.isFinite(b.think) && `think ${b.think}`, b.sequential && 'seq'].filter(Boolean);
      return el('div', { class: 'faint cmp-o' }, bits.join(' · ') || 'server defaults');
    }
    const o = r.o || {};
    return el('div', { class: 'faint cmp-o' }, Object.entries(o).filter(([k]) => k !== 'seed').map(([k, v]) => (k === 'shuffle' ? 'shuffled' : `${k} ${v ?? 'default'}`)).join(' · ') || 'same body');
  }

  function probsVector(st, keys) {
    const m = Object.fromEntries(st.probs.map((p) => [p.key, p.p]));
    return keys.map((k) => m[k] ?? 0);
  }
  function divergence(st, ref, q) {
    try {
      if (st.type !== ref.type) return null;
      const keys = st.type === 'choice' && isObj(q.criteria) ? Object.keys(q.criteria) : ref.probs.map((p) => p.key);
      return jsd(probsVector(st, keys), probsVector(ref, keys));
    } catch { return null; }
  }

  function cellViz(st, q, ref) {
    const W = 150;
    if (st.type === 'noul') {
      const g = s('svg', { class: 'viz-svg cmp-dot', viewBox: `0 0 ${W} 26`, width: '100%' });
      g.appendChild(s('line', { x1: 6, x2: W - 6, y1: 13, y2: 13, class: 'viz-axis-line' }));
      g.appendChild(s('rect', { x: 6 + (W - 12) * 0.35, width: (W - 12) * 0.3, y: 8, height: 10, class: 'viz-gauge-band' }));
      for (const t of [0, 0.5, 1]) g.appendChild(s('line', { x1: 6 + (W - 12) * t, x2: 6 + (W - 12) * t, y1: 9, y2: 17, class: 'viz-tick' }));
      if (ref && ref !== st) g.appendChild(s('circle', { cx: 6 + (W - 12) * ref.value, cy: 13, r: 4, class: 'cmp-ghost' }));
      g.appendChild(tip(s('circle', { cx: 6 + (W - 12) * st.value, cy: 13, r: 5, style: { fill: TYPE_COLOR.noul } }), `P(yes) ${st.value.toFixed(4)}`));
      return el('div', {}, g, el('div', { class: 'mono num cmp-val' }, st.value.toFixed(3)));
    }
    if (st.type === 'choice') {
      const keys = isObj(q.criteria) ? Object.keys(q.criteria) : st.probs.map((p) => p.key);
      const m = Object.fromEntries(st.probs.map((p) => [p.key, p.p]));
      const shown = keys.length > 8 ? [...keys].sort((a, b) => (m[b] ?? 0) - (m[a] ?? 0)).slice(0, 6) : keys;
      return el('div', { class: 'cmp-bars' }, shown.map((k) => tip(el('div', { class: ['cmp-bar', k === st.top && 'top'] },
        el('span', { class: 'mono cmp-bk' }, trunc(k, 10)), el('span', { class: 'cmp-bt' }, el('i', { style: { width: `${((m[k] ?? 0) * 100).toFixed(1)}%` } })),
        el('span', { class: 'mono num cmp-bp' }, `${((m[k] ?? 0) * 100).toFixed(0)}`)), `${k}: ${((m[k] ?? 0) * 100).toFixed(2)}%`)),
      keys.length > shown.length ? el('div', { class: 'faint mono' }, `top 6 of ${keys.length}`) : null);
    }
    const K = st.probs.length;
    const g = s('svg', { class: 'viz-svg cmp-dot', viewBox: `0 0 ${W} 26`, width: '100%' });
    const x = (v) => 6 + (W - 12) * (K > 1 ? v / (K - 1) : 0.5);
    g.appendChild(s('line', { x1: 6, x2: W - 6, y1: 13, y2: 13, class: 'viz-axis-line' }));
    for (let i = 0; i < K; i++) g.appendChild(s('line', { x1: x(i), x2: x(i), y1: 9, y2: 17, class: 'viz-tick' }));
    if (ref && ref !== st) g.appendChild(s('path', { d: `M${x(ref.value) - 4},5 L${x(ref.value) + 4},5 L${x(ref.value)},12 Z`, class: 'cmp-ghost' }));
    g.appendChild(tip(s('path', { d: `M${x(st.value) - 5},4 L${x(st.value) + 5},4 L${x(st.value)},13 Z`, style: { fill: TYPE_COLOR.score } }), `E = ${(+st.value).toFixed(3)} · mode ${st.probs[st.mode]?.label}`));
    return el('div', {}, g, el('div', { class: 'mono num cmp-val' }, answerText(st, { long: true })));
  }

  function footer() {
    const done = runs.filter((r) => r.status === 'ok');
    if (!done.length) return el('div');
    const lat = bars(done.map((r) => ({ label: trunc(r.label, 10), value: r.http?.clientMs || 0, tip: `${r.label}: ${fmtMs(r.http?.clientMs)}` })), { height: 130, yFmt: (v) => fmtMs(v), color: 'var(--accent)' });
    const tok = bars(done.map((r) => ({ label: trunc(r.label, 10), value: (r.data?.usage?.input_tokens || 0) + (r.data?.usage?.output_tokens || 0), tip: `${r.label}: ${fmtInt(r.data?.usage?.input_tokens || 0)} in + ${fmtInt(r.data?.usage?.output_tokens || 0)} out` })), { height: 130, color: 'var(--accent-2)' });
    return el('div', { class: 'cmp-footer' },
      el('div', { class: 'card' }, el('div', { class: 'stats-ct' }, 'latency (client)'), lat),
      el('div', { class: 'card' }, el('div', { class: 'stats-ct' }, 'tokens (in + out)'), tok));
  }

  return () => { alive = false; if (abort) abort.abort(); };
}

export function openCompare(convId, turnId, preset) {
  if (preset) queueComparePreset(preset);
  navigate(`#/compare/${encodeURIComponent(convId)}/${encodeURIComponent(turnId)}`);
}

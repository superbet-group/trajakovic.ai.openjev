// app/jev.js — B's bridge to builder C's barrel /js/jev/index.js (B-private).
// Loads it with a dynamic import in try/catch; every function below calls C when present
// and falls back to a plain implementation when C is missing or throws, so B never breaks.

import { h, clear, debounce } from '/js/core/dom.js';
import { emit, on } from '/js/core/bus.js';
import { toast } from '/js/core/toast.js';
import { registerView, hasView, setViewFailure } from '/js/core/router.js';
import { openDrawer } from '/js/core/drawer.js';
import { getConversation } from '/js/core/store.js';
import { fmtInt, fmtMs, shortId } from '/js/core/format.js';

let mod = null;
let loadError = null;

export async function loadJev() {
  try {
    mod = await import('/js/jev/index.js');
  } catch (err) {
    loadError = err;
    console.warn('[jev] /js/jev/index.js failed to load; using fallbacks', err);
  }
  if (mod) {
    try { await mod.init?.(); } catch (err) {
      loadError = err;
      console.warn('[jev] init() failed', err);
    }
  }
  for (const name of ['templates', 'batch', 'stats', 'compare']) {
    if (!hasView(name)) {
      setViewFailure(name, loadError || new Error('module failed to load'));
      registerView(name, { title: name[0].toUpperCase() + name.slice(1), icon: 'alert', mount: (el) => fallbackView(el, name) });
    }
  }
  if (!mod || loadError) {
    toast(`Visualizers failed to load: ${loadError?.message || 'unknown error'}. Running with fallbacks.`, { kind: 'err', timeout: 6000 });
    on('oj:open-inspector', async ({ convId, turnId }) => {
      const conversation = await getConversation(convId);
      const turn = conversation?.turns.find((t) => t.id === turnId);
      if (turn) openInspector({ conversation, turn });
    });
  }
  return { ok: !!mod && !loadError, error: loadError };
}

export function jevLoaded() { return !!mod; }
export function jevError() { return loadError; }
export function jevModule() { return mod; }

function fallbackView(el, name) {
  el.appendChild(h('div', { class: 'view-placeholder' },
    h('h2', {}, `${name[0].toUpperCase() + name.slice(1)} is unavailable`),
    h('p', { class: 'muted' }, 'module failed to load'),
    h('pre', { class: 'mono ph-error' }, String(loadError?.stack || loadError?.message || loadError || 'The visualizer module did not register this view.')),
    h('a', { class: 'btn', href: '#/' }, 'Back')));
}

function call(name, fallback) {
  return (...args) => {
    const fn = mod?.[name];
    if (typeof fn === 'function') {
      try { return fn(...args); } catch (err) {
        console.warn(`[jev] ${name} threw; using fallback`, err);
      }
    }
    return fallback(...args);
  };
}

// ------------------------------------------------------------------ fallbacks
function basicValidate(questions) {
  const errors = [];
  if (!questions || typeof questions !== 'object' || Array.isArray(questions)) {
    return { valid: false, errors: [{ path: [], message: 'questions must be an object of {qid: question}' }] };
  }
  const ids = Object.keys(questions);
  if (ids.length < 1) errors.push({ path: [], message: 'add at least one question' });
  if (ids.length > 256) errors.push({ path: [], message: 'at most 256 questions per request' });
  for (const qid of ids) {
    const q = questions[qid];
    if (!q || typeof q !== 'object') { errors.push({ path: [qid], message: 'a question is an object' }); continue; }
    if (!['noul', 'choice', 'score'].includes(q.type)) { errors.push({ path: [qid, 'type'], message: 'type must be noul, choice or score' }); continue; }
    if (q.type === 'choice') {
      const c = q.criteria;
      if (!c || typeof c !== 'object' || Array.isArray(c)) errors.push({ path: [qid, 'criteria'], message: 'choice criteria is an object {option: description}' });
      else if (!Object.keys(c).length) errors.push({ path: [qid, 'criteria'], message: 'a choice needs at least one option' });
      else if (Object.keys(c).length > 255) errors.push({ path: [qid, 'criteria'], message: 'at most 255 options' });
    }
    if (q.type === 'score') {
      const c = q.criteria;
      if (!Array.isArray(c)) errors.push({ path: [qid, 'criteria'], message: 'score criteria is an array of levels' });
      else if (c.length < 1 || c.length > 10) errors.push({ path: [qid, 'criteria'], message: 'a score has 1 to 10 levels' });
    }
    if (q.type === 'noul' && q.criteria !== undefined && q.criteria !== null) {
      if (typeof q.criteria !== 'object' || Array.isArray(q.criteria)) errors.push({ path: [qid, 'criteria'], message: 'noul criteria is {true?, false?}' });
      else for (const k of Object.keys(q.criteria)) if (k !== 'true' && k !== 'false') errors.push({ path: [qid, 'criteria', k], message: 'noul criteria keys are only true and false' });
    }
  }
  return { valid: errors.length === 0, errors };
}

function lintMessage(text, err) {
  const m = /position (\d+)/.exec(err?.message || '');
  if (m) {
    const pos = Number(m[1]);
    const before = text.slice(0, pos);
    const line = before.split('\n').length;
    const col = pos - before.lastIndexOf('\n');
    return `${line}:${col} ${err.message}`;
  }
  const lc = /line (\d+) column (\d+)/.exec(err?.message || '');
  if (lc) return `${lc[1]}:${lc[2]} ${err.message}`;
  return err?.message || String(err);
}

function fallbackJsonEditor(el, { value, readOnly = false, onChange } = {}) {
  clear(el);
  const ta = h('textarea', { class: 'textarea mono json-fallback', spellcheck: 'false', readonly: readOnly ? true : null, rows: 8 });
  const lint = h('div', { class: 'json-lint mono' });
  const set = (v) => { ta.value = v === undefined ? '' : JSON.stringify(v, null, 2); lint.textContent = ''; };
  set(value);
  const check = () => {
    try {
      const v = JSON.parse(ta.value);
      lint.textContent = '';
      lint.classList.remove('bad');
      onChange?.(v, { valid: true, error: null });
    } catch (err) {
      const msg = lintMessage(ta.value, err);
      lint.textContent = msg;
      lint.classList.add('bad');
      onChange?.(undefined, { valid: false, error: msg });
    }
  };
  ta.addEventListener('input', debounce(check, 150));
  el.append(ta, lint);
  return {
    get() { return JSON.parse(ta.value); },
    set,
    setMode() {},
    isFallback: true,
    focus() { ta.focus(); },
    destroy() { clear(el); },
  };
}

function fallbackQuestionEditor(el, { questions, onChange } = {}) {
  clear(el);
  let current = questions || {};
  const wrap = h('div', { class: 'qe-fallback' },
    h('div', { class: 'faint qe-fallback-note' }, 'Question editor unavailable, editing the raw JSON question set.'));
  const host = h('div');
  wrap.appendChild(host);
  el.appendChild(wrap);
  const fire = debounce((v) => emit('oj:composer-questions-changed', { questions: current, valid: v.valid, errors: v.errors }), 150);
  const ed = fallbackJsonEditor(host, {
    value: current,
    onChange: (v, st) => {
      if (!st.valid) {
        const res = { valid: false, errors: [{ path: [], message: st.error }] };
        onChange?.(current, res);
        fire(res);
        return;
      }
      current = v;
      const res = basicValidate(v);
      onChange?.(current, res);
      fire(res);
    },
  });
  return {
    get() { try { current = ed.get(); } catch { /* keep last good */ } return current; },
    set(q) { current = q || {}; ed.set(current); },
    validate() { return basicValidate(current); },
    highlightErrors() {},
    setTab() {},
    focus() { ed.focus(); },
    destroy() { ed.destroy(); },
  };
}

function fallbackResult(turn) {
  return h('pre', { class: 'mono result-fallback' }, JSON.stringify(turn?.response?.answers ?? turn?.response ?? null, null, 2));
}

function fallbackMeta(turn, ctx = {}) {
  const parts = [];
  if (ctx.turnIndex !== undefined) parts.push(`#${ctx.turnIndex + 1}`);
  if (turn.kind === 'chat') {
    const u = turn.usage;
    parts.push(turn.request?.model || 'chat');
    if (u) parts.push(`${fmtInt(u.prompt_tokens)} / ${fmtInt(u.completion_tokens)} tok`);
    if (turn.http?.ttftMs != null) parts.push(`ttft ${fmtMs(turn.http.ttftMs)}`);
    if (turn.finishReason) parts.push(turn.finishReason);
  } else {
    parts.push(turn.response?.model || turn.request?.model || '');
    const u = turn.response?.usage;
    if (u) parts.push(`${fmtInt(u.input_tokens)} in / ${fmtInt(u.output_tokens)} out tok`);
  }
  if (turn.http?.clientMs != null) parts.push(`${fmtMs(turn.http.clientMs)} client`);
  if (turn.http?.requestId) parts.push(shortId(turn.http.requestId));
  return h('div', { class: 'turn-meta mono faint' }, parts.filter(Boolean).join(' · '));
}

function fallbackConvStats(conv) {
  const turns = conv?.turns || [];
  const ok = turns.filter((t) => t.status === 'ok').length;
  const inTok = turns.reduce((a, t) => a + (t.response?.usage?.input_tokens || t.usage?.prompt_tokens || 0), 0);
  return h('div', { class: 'mono faint' }, `${turns.length} turns · ${ok} ok · ${fmtInt(inTok)} in tok`);
}

function fallbackInspector({ conversation, turn }) {
  openDrawer({
    title: `Inspect turn ${shortId(turn.id)}`,
    render(el) {
      el.append(
        h('h4', {}, 'Request'), h('pre', { class: 'mono insp-fallback' }, JSON.stringify(turn.request, (k, v) => (typeof v === 'string' && v.startsWith('data:image') ? `<${v.slice(5, v.indexOf(';'))} ${Math.round(v.length * 0.75 / 1024)} KB>` : v), 2)),
        h('h4', {}, 'Response'), h('pre', { class: 'mono insp-fallback' }, JSON.stringify(turn.response ?? turn.error ?? null, null, 2)),
        h('h4', {}, 'HTTP'), h('pre', { class: 'mono insp-fallback' }, JSON.stringify(turn.http, null, 2)),
        h('p', { class: 'faint' }, `conversation ${conversation?.id || ''}`));
    },
  });
}

function fallbackSnippet(kind, turn, config) {
  const base = config?.openjevUrl || 'http://127.0.0.1:8080';
  const path = turn?.kind === 'chat' ? '/v1/chat/completions' : '/v1/systemone';
  const auth = config?.authConfigured ? ' \\\n  -H "Authorization: Bearer $OPENJEV_API_KEY"' : '';
  const body = JSON.stringify(turn?.request || {});
  return `curl ${base}${path}${auth} \\\n  -H "Content-Type: application/json" \\\n  -d '${body.replace(/'/g, "'\\''")}'`;
}

export const FALLBACK_RUBRIC = {
  correctness: { type: 'score', instructions: 'Is the answer factually and logically correct?', criteria: ['wrong', 'mostly wrong', 'partly right', 'mostly right', 'fully correct'] },
  completeness: { type: 'score', instructions: 'Does the answer cover everything that was asked?', criteria: ['misses the point', 'large gaps', 'some gaps', 'minor gaps', 'complete'] },
  clarity: { type: 'score', instructions: 'Is the answer clear and well organised?', criteria: ['confusing', 'hard to follow', 'acceptable', 'clear', 'very clear'] },
  concision: { type: 'score', instructions: 'Is the answer free of padding and repetition?', criteria: ['very padded', 'padded', 'acceptable', 'tight', 'as short as possible'] },
  verdict: { type: 'choice', instructions: 'Overall, should this answer be shipped to the user?', criteria: { ship: 'good as is', revise: 'useful but needs edits', reject: 'wrong or unhelpful' } },
};

// Try to find C's 'rubric' template without importing anything but the barrel.
export function rubricQuestions() {
  try {
    const cands = [mod?.templates, mod?.TEMPLATES, mod?.getTemplates?.()];
    for (const list of cands) {
      if (Array.isArray(list)) {
        const t = list.find((x) => x && x.id === 'rubric');
        if (t?.questions) return t.questions;
      }
    }
    const t = mod?.getTemplate?.('rubric');
    if (t?.questions) return t.questions;
  } catch { /* fall back */ }
  return FALLBACK_RUBRIC;
}

// ------------------------------------------------------------------ the facade
export const J = {
  mountQuestionEditor: call('mountQuestionEditor', fallbackQuestionEditor),
  mountJsonEditor: call('mountJsonEditor', fallbackJsonEditor),
  validateQuestions: call('validateQuestions', basicValidate),
  renderResult: call('renderResult', fallbackResult),
  renderTurnMeta: call('renderTurnMeta', fallbackMeta),
  renderConversationStats: call('renderConversationStats', fallbackConvStats),
  renderTemplateStrip: call('renderTemplateStrip', () => null),
  openInspector: call('openInspector', fallbackInspector),
  buildSnippet: call('buildSnippet', fallbackSnippet),
  get questionTypes() {
    return mod?.questionTypes || [
      { type: 'noul', label: 'noul', color: 'var(--type-noul)', description: 'yes / no → P(yes)' },
      { type: 'choice', label: 'choice', color: 'var(--type-choice)', description: 'one of named options' },
      { type: 'score', label: 'score', color: 'var(--type-score)', description: 'ordered levels → expected level' },
    ];
  },
};

function openInspector(args) { return J.openInspector(args); }

// Result visualizers (builder C): renderResult (summary strip, confidence heat row, per-question
// cards for noul / choice / score, deltas against the parent turn, compact table) and
// renderTurnMeta (the one-line mono meta bar with timing waterfall and seed-stable check).

import { el, s, trunc, describe, isObj, logit, deepClose, clamp } from '/js/jev/util.js';
import { confColor, tint, ring, gauge, tip, waterfall, timingSegments, miniBar, TYPE_COLOR } from '/js/jev/charts.js';
import { answerStats, costOf, IMAGE_TOKENS_EST, chatThroughput } from '/js/core/metrics.js';
import { fmtMs, fmtProb, fmtPct, fmtBytes, fmtCost, fmtInt } from '/js/core/format.js';
import { icon } from '/js/core/icons.js';
import { updateTurn, getSettings } from '/js/core/store.js';
import { copyText } from '/js/core/dom.js';
import { toast } from '/js/core/toast.js';

// ---------------------------------------------------------------- stats helpers

/** answerStats for every answered qid (in answers' key order). → {[qid]: stats|null} */
export function statsFor(answers, questions) {
  const out = {};
  for (const [qid, a] of Object.entries(answers || {})) {
    try { out[qid] = answerStats(a, (questions || {})[qid] || { type: a?.type }); } catch (e) { console.warn('[ojui] answerStats failed', qid, e); out[qid] = null; }
  }
  return out;
}

/** Scalar used for deltas: noul P(yes), choice top-probability, score E. */
function scalar(st) {
  if (!st) return null;
  if (st.type === 'noul') return st.value;
  if (st.type === 'score') return st.value;
  return st.topP;
}

/** Short answer text: noul `0.998`, choice `billing 97%`, score `E 1.03`. */
export function answerText(st, { long = false } = {}) {
  if (!st) return '—';
  if (st.type === 'noul') return long ? `P(yes) ${fmtProb(st.value, 4)}` : fmtProb(st.value, 3);
  if (st.type === 'choice') return `${st.value ?? st.top} ${(st.topP * 100).toFixed(0)}%`;
  if (st.type === 'score') {
    const lbl = st.probs?.[st.mode]?.label;
    return long && lbl !== undefined ? `E ${(+st.value).toFixed(2)} (${trunc(lbl, 18)})` : `E ${(+st.value).toFixed(2)}`;
  }
  return '—';
}

function typeChip(type) {
  return el('span', { class: 'chip viz-typechip', style: { '--viz-c': TYPE_COLOR[type] || 'var(--fg-muted)' } }, type || '?');
}

function settingsSafe() { try { return getSettings() || {}; } catch { return {}; } }

// ---------------------------------------------------------------- delta

function deltaInfo(qid, st, parentStats, parentQs) {
  if (!parentStats) return null;
  if (!(qid in parentStats)) return { kind: 'new' };
  const ps = parentStats[qid];
  if (!ps || !st) return null;
  if (ps.type !== st.type) return { kind: 'retyped' };
  let d;
  let flipped = false;
  if (st.type === 'choice') {
    const pp = ps.probs.find((p) => p.key === st.top);
    d = st.topP - (pp ? pp.p : 0);
    flipped = ps.top !== st.top;
  } else d = scalar(st) - scalar(ps);
  return { kind: 'delta', d, flipped, from: ps };
}

function deltaBadge(info) {
  if (!info) return null;
  if (info.kind === 'new') return el('span', { class: 'badge viz-new' }, 'new');
  if (info.kind === 'retyped') return el('span', { class: 'badge viz-flip' }, 'type changed');
  const small = Math.abs(info.d) < 0.01;
  return el('span', { class: 'row viz-delta-wrap' },
    el('span', { class: ['badge', 'mono', 'viz-delta', small ? 'muted' : info.d > 0 ? 'up' : 'down'], title: 'change against the parent turn' },
      `Δ ${info.d >= 0 ? '+' : '−'}${Math.abs(info.d).toFixed(2)}`),
    info.flipped ? el('span', { class: 'badge viz-flip', title: `was ${info.from.top}` }, `flipped from ${trunc(info.from.top, 16)}`) : null);
}

// ---------------------------------------------------------------- feedback

function feedback(turn, qid, st, question, ctx) {
  const wrap = el('span', { class: 'row viz-fb' });
  const draw = () => {
    wrap.textContent = '';
    const lab = turn.labels?.[qid];
    const good = el('button', { class: ['icon-btn', 'viz-fb-btn', lab?.correct === true && 'on-ok'], type: 'button', title: 'correct', 'aria-label': 'mark correct', onclick: () => setLabel(lab?.correct === true ? null : { correct: true }) }, icon('check', 14));
    const bad = el('button', { class: ['icon-btn', 'viz-fb-btn', lab?.correct === false && 'on-err'], type: 'button', title: 'wrong', 'aria-label': 'mark wrong', onclick: () => onWrong(lab) }, icon('x', 14));
    wrap.append(good, bad);
    if (lab && lab.correct === false && lab.expected !== undefined) wrap.appendChild(el('span', { class: 'faint mono viz-fb-exp' }, `expected ${trunc(String(lab.expected), 14)}`));
  };
  async function setLabel(v) {
    const labels = { ...(turn.labels || {}) };
    if (v === null) delete labels[qid]; else labels[qid] = v;
    turn.labels = labels;
    draw();
    if (typeof ctx.onLabel === 'function') { ctx.onLabel(qid, v, labels); return; }
    const convId = ctx.conversation?.id;
    if (!convId) return;
    try { await updateTurn(convId, turn.id, { labels }); } catch (e) { toast(`Could not save label: ${e.message || e}`, { kind: 'err' }); }
  }
  function onWrong(lab) {
    if (lab?.correct === false) return setLabel(null);
    if (st.type === 'noul') return setLabel({ correct: false, expected: st.top === 'yes' ? 'no' : 'yes' });
    // picker of the other options
    const opts = st.probs.filter((p) => p.key !== st.top);
    const pick = el('div', { class: 'viz-picker card' },
      el('div', { class: 'faint' }, 'expected answer'),
      opts.map((p) => el('button', { class: 'viz-pick', type: 'button', onclick: () => { pick.remove(); setLabel({ correct: false, expected: st.type === 'score' ? Number(p.key) : p.key }); } },
        el('span', { class: 'mono' }, st.type === 'score' ? `${p.key} · ${trunc(p.label, 24)}` : trunc(p.key, 28)), el('span', { class: 'faint mono' }, fmtPct(p.p))) ),
      el('button', { class: 'viz-pick faint', type: 'button', onclick: () => { pick.remove(); setLabel({ correct: false }); } }, 'just wrong'));
    wrap.appendChild(pick);
    const close = (ev) => { if (!pick.contains(ev.target)) { pick.remove(); document.removeEventListener('pointerdown', close, true); } };
    setTimeout(() => document.addEventListener('pointerdown', close, true), 0);
  }
  draw();
  return wrap;
}

// ---------------------------------------------------------------- per type

function noulViz(st, question) {
  const p = st.value;
  const verdict = p >= 0.65 ? 'YES' : p <= 0.35 ? 'NO' : 'UNSURE';
  const lg = logit(p);
  const lgText = lg === Infinity ? '+∞' : lg === -Infinity ? '−∞' : `${lg >= 0 ? '+' : '−'}${Math.abs(lg).toFixed(2)}`;
  const crit = isObj(question?.criteria) ? question.criteria : null;
  return el('div', { class: 'viz-noul' },
    el('div', { class: 'viz-gauge-wrap' }, gauge(p)),
    el('div', { class: 'viz-noul-side' },
      el('div', { class: 'viz-bignum mono' }, el('span', { class: 'faint viz-bignum-k' }, 'P(yes) '), fmtProb(p, 4)),
      el('div', { class: 'row' },
        el('span', { class: ['viz-verdict', `v-${verdict.toLowerCase()}`] }, verdict),
        tip(el('span', { class: 'mono faint' }, `logit ${lgText}`), 'log-odds ln(p / (1 − p)); ±∞ when p rounds to 0 or 1')),
      crit && (crit.true || crit.false) ? el('div', { class: 'viz-crit' },
        crit.true ? el('div', {}, el('span', { class: 'faint' }, 'yes means: '), describe(crit.true)) : null,
        crit.false ? el('div', {}, el('span', { class: 'faint' }, 'no means: '), describe(crit.false)) : null) : null));
}

function choiceViz(st, question) {
  const crit = isObj(question?.criteria) ? question.criteria : {};
  const sorted = [...st.probs].sort((a, b) => b.p - a.p);
  const K = sorted.length;
  const box = el('div', { class: 'viz-choice' });
  let showAll = K <= 12;
  const list = el('div', { class: 'viz-opts' });
  const draw = () => {
    list.textContent = '';
    const rows = showAll ? sorted : sorted.slice(0, 8);
    rows.forEach((o, i) => {
      const desc = crit[o.key];
      const row = el('div', { class: ['viz-opt', i === 0 && 'top'] },
        el('span', { class: 'viz-opt-name mono' }, trunc(o.key, 28)),
        el('span', { class: 'viz-opt-track' }, el('i', { style: { width: `${clamp(o.p * 100, 0, 100).toFixed(2)}%` } })),
        el('span', { class: 'viz-opt-pct mono num' }, `${(o.p * 100).toFixed(1)}%`));
      tip(row, () => `${o.key}${desc !== undefined && desc !== null ? ` — ${describe(desc)}` : ''} · p = ${o.p.toPrecision(4)}`);
      list.appendChild(row);
    });
    if (!showAll) list.appendChild(el('button', { class: 'btn ghost sm viz-showall', type: 'button', onclick: () => { showAll = true; draw(); } }, `show all ${K} options`));
  };
  draw();
  box.append(list, el('div', { class: 'faint mono viz-sub' }, `${K} options · top-2 margin ${num2(st.margin)}`));
  return box;
}
const num2 = (v) => (Number.isFinite(v) ? v.toFixed(2) : '—');

function scoreViz(st) {
  const K = st.probs.length;
  const W = 380, H = 156, ml = 8, mr = 8, mt = 26, mb = 34;
  const g = s('svg', { class: 'viz-svg viz-score-svg', viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img' });
  const bw = (W - ml - mr) / Math.max(1, K);
  const maxP = Math.max(0.0001, ...st.probs.map((p) => p.p));
  const yv = (p) => H - mb - (p / Math.max(maxP, 0.25)) * (H - mb - mt);
  const maxChars = Math.max(3, Math.floor(bw / 6.2));
  st.probs.forEach((p, i) => {
    const x0 = ml + i * bw;
    const top = yv(p.p);
    const bar = s('rect', { x: x0 + bw * 0.14, y: top, width: bw * 0.72, height: Math.max(0, H - mb - top), rx: 3, class: ['viz-bar', i === st.mode ? 'viz-score-mode' : 'viz-score-other'].join(' ') });
    tip(bar, `${i} · ${p.label}: ${(p.p * 100).toFixed(1)}%`);
    g.appendChild(bar);
    if (p.p >= 0.005) { const inside = top < mt + 4; g.appendChild(s('text', { x: x0 + bw / 2, y: inside ? top + 13 : top - 4, 'text-anchor': 'middle', class: inside ? 'viz-axis viz-val-in' : 'viz-axis viz-val' }, `${(p.p * 100).toFixed(p.p >= 0.1 ? 0 : 1)}%`)); }
    g.appendChild(s('text', { x: x0 + bw / 2, y: H - mb + 13, 'text-anchor': 'middle', class: 'viz-axis' }, trunc(p.label, maxChars)));
    if (String(p.label) !== String(i)) g.appendChild(s('text', { x: x0 + bw / 2, y: H - mb + 25, 'text-anchor': 'middle', class: 'viz-axis faint-axis' }, String(i)));
  });
  g.appendChild(s('line', { x1: ml, x2: W - mr, y1: H - mb, y2: H - mb, class: 'viz-axis-line' }));
  const E = +st.value;
  const ex = ml + (E + 0.5) * bw;
  g.appendChild(s('line', { x1: ex, x2: ex, y1: mt - 6, y2: H - mb, class: 'viz-eline' }));
  g.appendChild(s('path', { d: `M${ex - 5},${mt - 12} L${ex + 5},${mt - 12} L${ex},${mt - 5} Z`, class: 'viz-emarker' }));
  const anchor = ex > W - 60 ? 'end' : ex < 60 ? 'start' : 'middle';
  g.appendChild(s('text', { x: ex + (anchor === 'start' ? 7 : anchor === 'end' ? -7 : 0), y: mt - 14, 'text-anchor': anchor, class: 'viz-axis viz-elabel' }, `E = ${E.toFixed(2)}`));
  const norm = K > 1 ? E / (K - 1) : 0;
  const modeLbl = st.probs[st.mode]?.label ?? '';
  return el('div', { class: 'viz-score' }, g,
    el('div', { class: 'row viz-sub' },
      el('span', { class: 'mono faint' }, `mode = ${trunc(modeLbl, 24)} · σ = ${num2(st.std)}`),
      el('span', { class: 'spacer' }),
      tip(el('span', { class: 'row viz-norm' }, el('span', { class: 'faint mono' }, 'E/(K−1)'), miniBar(norm, { color: 'var(--type-score)', width: 90 }), el('span', { class: 'mono num' }, norm.toFixed(2))),
        'expected level normalised to 0..1')));
}

// ---------------------------------------------------------------- cards / table

function questionCard(qid, st, answer, question, ctx, turn, delta) {
  const instr = describe(question?.instructions);
  const instrEl = instr ? el('div', { class: 'viz-instr muted', title: 'click to expand', onclick: (e) => e.currentTarget.classList.toggle('open') }, instr) : null;
  let body;
  if (!st) body = el('pre', { class: 'mono faint' }, JSON.stringify(answer, null, 2));
  else if (st.type === 'noul') body = noulViz(st, question);
  else if (st.type === 'choice') body = choiceViz(st, question);
  else if (st.type === 'score') body = scoreViz(st);
  else body = el('pre', { class: 'mono' }, JSON.stringify(answer, null, 2));
  const c = st?.confidence;
  const foot = st ? el('div', { class: 'viz-foot' },
    tip(el('span', { class: 'row viz-conf' }, ring(c, { size: 28 }), el('span', { class: 'mono num' }, num2(c))),
      st.type === 'noul' ? 'confidence = 1 − H/ln 2 (computed in the UI; the server sends none for noul)' : 'confidence = 1 − H(p)/ln K, from the server'),
    tip(el('span', { class: 'mono faint' }, `H = ${num2(st.entropyBits)} bits`), 'Shannon entropy of the answer distribution'),
    tip(el('span', { class: 'mono faint' }, `ppl ${Number.isFinite(st.perplexity) ? st.perplexity.toFixed(1) : '—'}`), 'perplexity e^H: the effective number of options'),
    tip(el('span', { class: 'mono faint' }, `margin ${num2(st.margin)}`), 'top probability minus the runner-up'),
    el('span', { class: 'spacer' }),
    feedback(turn, qid, st, question, ctx)) : null;
  return el('section', { class: 'viz-card', dataset: { vizQid: qid, type: st?.type || answer?.type }, style: { '--viz-c': TYPE_COLOR[st?.type] || 'var(--fg-muted)' } },
    el('header', { class: 'viz-card-head' },
      el('span', { class: 'mono viz-qid' }, qid), typeChip(st?.type || answer?.type), deltaBadge(delta),
      el('span', { class: 'spacer' }),
      st?.type === 'choice' ? el('span', { class: 'mono viz-top' }, trunc(String(st.value), 24)) : null),
    instrEl, body, foot);
}

function compactTable(order, stats, answers, questions, deltas, turn, ctx) {
  const hasDelta = order.some((q) => deltas[q]);
  const tbody = el('tbody');
  for (const qid of order) {
    const st = stats[qid];
    const bar = !st ? null : st.type === 'noul' ? miniBar(st.value, { color: TYPE_COLOR.noul })
      : st.type === 'choice' ? miniBar(st.topP, { color: TYPE_COLOR.choice }) : miniBar(st.probs.length > 1 ? st.value / (st.probs.length - 1) : 0, { color: TYPE_COLOR.score });
    tbody.appendChild(el('tr', { dataset: { vizQid: qid } },
      el('td', { class: 'mono' }, qid),
      el('td', {}, typeChip(st?.type || answers[qid]?.type)),
      el('td', { class: 'mono' }, answerText(st, { long: true })),
      el('td', {}, st ? el('span', { class: 'row viz-conf' }, ring(st.confidence, { size: 18, stroke: 3 }), el('span', { class: 'mono num' }, num2(st.confidence))) : '—'),
      el('td', {}, bar),
      hasDelta ? el('td', {}, deltaBadge(deltas[qid])) : null,
      el('td', {}, st ? feedback(turn, qid, st, questions[qid], ctx) : null)));
  }
  return el('div', { class: 'viz-table-wrap scroll' }, el('table', { class: 'table viz-compact' },
    el('thead', {}, el('tr', {}, ['qid', 'type', 'answer', 'conf', ''].map((h) => el('th', {}, h)), hasDelta ? el('th', {}, 'Δ parent') : null, el('th', {}, 'label'))),
    tbody));
}

/** Confidence heat row: one square per question. */
export function heatRow(order, stats, onPick) {
  return el('div', { class: 'viz-heat', role: 'list' }, order.map((qid) => {
    const c = stats[qid]?.confidence;
    const sq = el('button', { class: 'viz-heat-sq', type: 'button', role: 'listitem', style: { background: confColor(c) }, 'aria-label': `${qid} confidence ${num2(c)}`, onclick: () => onPick && onPick(qid) });
    return tip(sq, `${qid} · conf ${num2(c)}`);
  }));
}

/**
 * renderResult(turn, ctx) → HTMLElement. turn.status === 'ok'.
 * ctx: {conversation, settings, turnIndex, parentTurn, compact, onLabel?}
 */
export function renderResult(turn, ctx = {}) {
  const answers = turn?.response?.answers || {};
  const questions = turn?.request?.questions || {};
  const order = Object.keys(answers);
  const stats = statsFor(answers, questions);
  const parent = ctx.parentTurn && ctx.parentTurn.response ? ctx.parentTurn : null;
  const parentStats = parent ? statsFor(parent.response.answers, parent.request?.questions) : null;
  const deltas = {};
  for (const qid of order) deltas[qid] = deltaInfo(qid, stats[qid], parentStats, parent?.request?.questions);
  const removed = parentStats ? Object.keys(parentStats).filter((q) => !(q in answers)) : [];

  const root = el('div', { class: 'viz-result' });
  // summary strip
  const confs = order.map((q) => [q, stats[q]?.confidence]).filter(([, c]) => Number.isFinite(c));
  const meanConf = confs.length ? confs.reduce((a, [, c]) => a + c, 0) / confs.length : null;
  const minC = confs.length ? confs.reduce((m, x) => (x[1] < m[1] ? x : m)) : null;
  const usage = turn.response?.usage || {};
  const lat = turn.http?.clientMs;
  root.appendChild(el('div', { class: 'viz-summary mono' },
    el('span', {}, `${order.length} answer${order.length === 1 ? '' : 's'}`),
    meanConf !== null ? el('span', {}, '· mean conf ', el('b', { style: { color: confColor(meanConf) } }, num2(meanConf))) : null,
    minC ? el('span', {}, '· min ', el('b', { style: { color: confColor(minC[1]) } }, num2(minC[1])), ` (${trunc(minC[0], 20)})`) : null,
    Number.isFinite(usage.input_tokens) ? el('span', {}, `· ${fmtInt(usage.input_tokens)} in tok`) : null,
    usage.output_tokens > 0 ? el('span', {}, `· ${fmtInt(usage.output_tokens)} thought tok`) : null,
    Number.isFinite(lat) ? el('span', {}, `· ${fmtMs(lat)}`) : null,
    parent ? el('span', { class: 'viz-vs' }, `· vs parent`) : null,
    removed.length ? el('span', {}, ' ', el('span', { class: 'badge viz-removed', title: removed.join(', ') }, `removed ${removed.length}: ${trunc(removed.join(', '), 40)}`)) : null));

  const compact = !!ctx.compact || order.length > 12;
  const scrollTo = (qid) => {
    const n = root.querySelector(`[data-viz-qid="${CSS.escape(qid)}"]`);
    if (n) { n.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); n.classList.add('flash'); setTimeout(() => n.classList.remove('flash'), 900); }
  };
  if (order.length > 1) root.appendChild(heatRow(order, stats, scrollTo));

  if (usage.output_tokens > 0) {
    root.appendChild(el('div', { class: 'viz-thought' }, icon('sparkles', 14),
      ` think: ${fmtInt(usage.output_tokens)} thought tokens (the server does not return the thought text)`));
  }

  if (compact) root.appendChild(compactTable(order, stats, answers, questions, deltas, turn, ctx));
  else {
    const grid = el('div', { class: 'viz-cards' });
    for (const qid of order) grid.appendChild(questionCard(qid, stats[qid], answers[qid], questions[qid], ctx, turn, deltas[qid]));
    root.appendChild(grid);
  }
  return root;
}

// ---------------------------------------------------------------- meta bar

function part(text, { title, cls, onClick, tipContent } = {}) {
  const n = el(onClick ? 'button' : 'span', { class: ['viz-meta-part', cls, onClick && 'click'], type: onClick ? 'button' : null, title, onclick: onClick }, text);
  if (tipContent) tip(n, tipContent);
  return n;
}

/** Is answers deep-equal within 1e-9? */
export function sameAnswers(a, b) { return deepClose(a || {}, b || {}, 1e-9); }

function seedStable(turn, ctx) {
  if (turn.status !== 'ok' || !turn.bodyHash) return null;
  const others = (ctx.conversation?.turns || []).filter((t) => t.id !== turn.id && t.kind === 'systemone' && t.status === 'ok' && t.bodyHash === turn.bodyHash && t.response);
  if (!others.length) return null;
  const same = others.every((t) => sameAnswers(t.response.answers, turn.response?.answers));
  return part(same ? ['seed-stable ', icon('check', 12)] : ['seed-stable ', icon('x', 12), ' drift'], {
    cls: same ? 'ok' : 'warn',
    tipContent: same
      ? `${others.length} other run${others.length > 1 ? 's' : ''} of this exact body gave identical answers (within 1e-9): the seed is derived from the request`
      : `${others.length} other run${others.length > 1 ? 's' : ''} of this exact body gave different answers`,
  });
}

function latencyTip(http) {
  return () => {
    const { segments, modelNA } = timingSegments(http);
    const box = el('div', { class: 'viz-tip-wf' });
    if (!segments.length) { box.textContent = 'no timing'; return box; }
    box.appendChild(waterfall(segments, { width: 360, rowH: 12, labelW: 100 }));
    if (modelNA) box.appendChild(el('div', { class: 'faint' }, 'model;dur=0.0 on MLX: the backend does not report model time'));
    return box;
  };
}

/** renderTurnMeta(turn, ctx) → one-line mono meta bar for any kind/status. */
export function renderTurnMeta(turn, ctx = {}) {
  const settings = ctx.settings || settingsSafe();
  const cur = settings.currency || '$';
  const bar = el('div', { class: 'viz-meta mono' });
  const parts = [];
  if (Number.isFinite(ctx.turnIndex)) parts.push(part(`#${ctx.turnIndex + 1}`, { cls: 'strong' }));
  const http = turn.http || null;
  const st = http?.serverTiming || {};

  if (turn.kind === 'chat') {
    parts.push(part(turn.request?.model || 'chat'));
    if (turn.status === 'pending') parts.push(part('streaming…', { cls: 'accent2' }));
    const u = turn.usage;
    if (u) {
      parts.push(part(`${fmtInt(u.prompt_tokens ?? 0)} prompt / ${fmtInt(u.completion_tokens ?? 0)} completion tok`));
      try { parts.push(part(fmtCost(costOf(u, settings), cur), { title: 'estimated cost at the current prices' })); } catch { /* no cost */ }
    }
    if (Number.isFinite(http?.ttftMs)) parts.push(part(`ttft ${fmtMs(http.ttftMs)}`, { title: 'time to first token' }));
    const ct = u?.completion_tokens;
    const thr = chatThroughput(ct, http?.ttftMs, http?.streamMs);
    if (thr) {
      parts.push(part(`${thr.tps.toFixed(1)} tok/s${thr.basis === 'e2e' ? ' e2e' : ''}`, {
        title: thr.basis === 'e2e'
          ? 'completion tokens / total stream time (the tokens arrived in one burst after ttft, so a decode-only rate would be meaningless)'
          : 'completion tokens / (stream time − ttft)',
      }));
    }
    if (turn.finishReason) parts.push(part(`finish ${turn.finishReason}`, { cls: turn.finishReason === 'length' ? 'warn' : '' }));
    if (Number.isFinite(http?.clientMs)) parts.push(part(fmtMs(http.clientMs)));
    if (Number.isFinite(http?.chunks)) parts.push(part(`${http.chunks} chunks`));
  } else {
    const model = turn.response?.model || turn.request?.model;
    if (model) parts.push(part(model, { title: turn.request?.model && turn.response?.model && turn.request.model !== turn.response.model ? `requested ${turn.request.model}` : null }));
    if (turn.status === 'pending') parts.push(part('pending', { cls: 'accent' }));
    if (turn.status === 'aborted') parts.push(part('stopped', { cls: 'muted' }));
    if (turn.status === 'error' && turn.error) parts.push(part(`${turn.error.status || ''} ${turn.error.kind || 'error'}`.trim(), { cls: 'err' }));
    const u = turn.response?.usage;
    if (u) parts.push(part(`${fmtInt(u.input_tokens ?? 0)} in / ${fmtInt(u.output_tokens ?? 0)} out tok`, { title: 'usage.input_tokens counts image tokens too; output tokens are the think thought' }));
    const nImg = turn.imagesMeta?.length || (Array.isArray(turn.request?.images) ? turn.request.images.length : 0);
    if (nImg) parts.push(part(`+${fmtInt(nImg * IMAGE_TOKENS_EST)} img est`, { title: `${nImg} image${nImg > 1 ? 's' : ''} × ~${IMAGE_TOKENS_EST} tokens (already inside input tokens)` }));
    if (u) { try { parts.push(part(fmtCost(costOf(u, settings), cur), { title: 'estimated cost at the current prices (Settings)' })); } catch { /* no cost */ } }
    if (Number.isFinite(http?.clientMs)) parts.push(part(`${fmtMs(http.clientMs)} client`, { cls: 'lat', tipContent: latencyTip(http) }));
    if (Number.isFinite(st.total)) {
      const modelNA = !Number.isFinite(st.model) || (st.model === 0 && st.total > 5);
      parts.push(part(`srv ${st.total.toFixed(1)} (model ${modelNA ? 'n/a' : st.model.toFixed(1)})`, {
        tipContent: modelNA ? 'Server-Timing total; the MLX backend reports model;dur=0.0, so model time is not available' : 'Server-Timing total (model = time on the model, summed over reads)',
      }));
    }
    if (Number.isFinite(st.upstream)) parts.push(part(`up ${st.upstream.toFixed(1)}`, { title: 'proxy: time until OpenJev sent response headers' }));
    const opts = turn.request || {};
    const optBits = [opts.steps && `steps ${opts.steps}`, opts.samples && `samples ${opts.samples}`, Number.isFinite(opts.think) && `think ${opts.think}`, opts.sequential && 'seq'].filter(Boolean);
    if (optBits.length && ctx.showOptions) parts.push(part(optBits.join(' ')));
  }
  if (http && Number.isFinite(http.requestBytes)) parts.push(part(`${fmtBytes(http.requestBytes)}→${fmtBytes(http.responseBytes || 0)}`, { title: 'request → response bytes' }));
  const rid = http?.requestId || turn.error?.requestId;
  if (rid) parts.push(part(trunc(rid, 9), { title: `${rid} (click to copy)`, onClick: () => copyText(rid) }));
  if (turn.kind !== 'chat') { const ss = seedStable(turn, ctx); if (ss) parts.push(ss); }

  parts.forEach((p, i) => { if (i) bar.appendChild(el('span', { class: 'viz-meta-sep' }, '·')); bar.appendChild(p); });
  return bar;
}

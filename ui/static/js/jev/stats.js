// Stats (builder C): the #/stats dashboard (KPI tiles with sparklines, latency / timing /
// rate / token / confidence / entropy charts, a reliability diagram from user labels, breakdown
// tables) and renderConversationStats, the per-conversation strip under the thread header.

import { el, trunc, mean, loadLS, saveLS } from '/js/jev/util.js';
import { sparkline, histogram, scatter, bars, stackedBars, stackedBar, reliability, legend, shortMs, confColor, TYPE_COLOR } from '/js/jev/charts.js';
import { statsFor } from '/js/jev/renderers.js';
import { percentile, costOf, IMAGE_TOKENS_EST, chatThroughput } from '/js/core/metrics.js';
import { listRequests, getTotals, resetTotals, clearRequests, listConversations, getConversation, getSettings } from '/js/core/store.js';
import { fmtInt, fmtMs, fmtTokens, fmtCost, fmtPct, fmtRelTime } from '/js/core/format.js';
import { icon } from '/js/core/icons.js';
import { on } from '/js/core/bus.js';
import { download } from '/js/core/dom.js';
import { confirmDialog } from '/js/core/modal.js';
import { toast } from '/js/core/toast.js';

const LS_RANGE = 'ojui.stats.range.v1';
const SESSION_START = (typeof performance !== 'undefined' && performance.timeOrigin) ? Math.floor(performance.timeOrigin) : Date.now();
export const RANGES = [
  { id: 'session', label: 'session', since: () => SESSION_START },
  { id: '1h', label: '1 h', since: () => Date.now() - 3600e3 },
  { id: '24h', label: '24 h', since: () => Date.now() - 86400e3 },
  { id: '7d', label: '7 d', since: () => Date.now() - 7 * 86400e3 },
  { id: 'all', label: 'all', since: () => 0 },
];

function settingsSafe() { try { return getSettings() || {}; } catch { return {}; } }
const statusColor = (r) => (r.errorKind === 'aborted' ? 'var(--fg-faint)' : r.ok ? 'var(--ok)' : r.status >= 400 && r.status < 500 ? 'var(--warn)' : 'var(--err)');
const pctl = (xs, p) => { const v = xs.filter(Number.isFinite); return v.length ? percentile(v, p) : null; };

// ---------------------------------------------------------------- math

/** Least squares y = a + Σ bᵢ xᵢ via normal equations (small k). → coefficients [a, b1, …] or null */
export function ols(X, y) {
  const n = y.length, k = X[0]?.length ?? 0;
  if (n < k + 2) return null;
  const A = Array.from({ length: k + 1 }, () => new Array(k + 1).fill(0));
  const B = new Array(k + 1).fill(0);
  for (let i = 0; i < n; i++) {
    const row = [1, ...X[i]];
    for (let r = 0; r <= k; r++) { B[r] += row[r] * y[i]; for (let c = 0; c <= k; c++) A[r][c] += row[r] * row[c]; }
  }
  // Gaussian elimination with partial pivoting
  for (let c = 0; c <= k; c++) {
    let p = c;
    for (let r = c + 1; r <= k; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    if (Math.abs(A[p][c]) < 1e-9) return null;
    [A[c], A[p]] = [A[p], A[c]]; [B[c], B[p]] = [B[p], B[c]];
    for (let r = 0; r <= k; r++) {
      if (r === c) continue;
      const f = A[r][c] / A[c][c];
      for (let cc = c; cc <= k; cc++) A[r][cc] -= f * A[c][cc];
      B[r] -= f * B[c];
    }
  }
  return B.map((b, i) => b / A[i][i]);
}

/** Empirical token model from ok systemone records without samples/think multipliers. */
export function tokenModel(recs) {
  const pts = recs.filter((r) => r.ok && r.endpoint === '/v1/systemone' && Number.isFinite(r.usage?.inputTokens) && !(r.options?.samples > 1) && !(r.options?.think > 0));
  const q = pts.map((r) => r.questionCount || 0), im = pts.map((r) => r.imageCount || 0), y = pts.map((r) => r.usage.inputTokens);
  const varies = (xs) => xs.length > 1 && Math.min(...xs) !== Math.max(...xs);
  let perQ = null, perImg = null, base = null;
  if (varies(q) && varies(im)) { const c = ols(pts.map((_, i) => [q[i], im[i]]), y); if (c) [base, perQ, perImg] = c; }
  else if (varies(q)) { const c = ols(pts.map((_, i) => [q[i]]), y); if (c) [base, perQ] = c; }
  else if (varies(im)) { const c = ols(pts.map((_, i) => [im[i]]), y); if (c) [base, perImg] = c; }
  return { n: pts.length, base, perQ, perImg, pts };
}

/** Reliability bins over labelled answers: [{lo, hi, conf, acc, count}] + brier + ece. */
export function calibration(labelled, lo = 0.5, hi = 1, nb = 10) {
  const bins = Array.from({ length: nb }, (_, i) => ({ lo: lo + ((hi - lo) * i) / nb, hi: lo + ((hi - lo) * (i + 1)) / nb, confSum: 0, correct: 0, count: 0 }));
  let below = 0;
  for (const x of labelled) {
    if (x.p < lo) { below++; continue; }
    const b = bins[Math.min(nb - 1, Math.floor(((x.p - lo) / (hi - lo)) * nb))];
    b.count++; b.confSum += x.p; b.correct += x.correct ? 1 : 0;
  }
  const N = labelled.length;
  const out = bins.map((b) => ({ lo: b.lo, hi: b.hi, count: b.count, conf: b.count ? b.confSum / b.count : (b.lo + b.hi) / 2, acc: b.count ? b.correct / b.count : 0 }));
  const inBins = out.reduce((a, b) => a + b.count, 0);
  const ece = inBins ? out.reduce((a, b) => a + (b.count / inBins) * Math.abs(b.acc - b.conf), 0) : null;
  const brier = N ? labelled.reduce((a, x) => a + (x.p - (x.correct ? 1 : 0)) ** 2, 0) / N : null;
  const acc = N ? labelled.filter((x) => x.correct).length / N : null;
  return { bins: out, ece, brier, acc, n: N, below };
}

function bucketize(recs, since, until, nb = 40) {
  const t0 = Math.max(since || 0, recs.length ? recs[0].ts : until - 3600e3);
  const span = Math.max(60e3, until - t0);
  const minute = 60e3;
  const nice = [minute, 5 * minute, 10 * minute, 30 * minute, 3600e3, 3 * 3600e3, 6 * 3600e3, 86400e3];
  const size = nice.find((s) => span / s <= nb) || Math.ceil(span / nb);
  const start = Math.floor(t0 / size) * size;
  const n = Math.max(1, Math.ceil((until - start) / size));
  const buckets = Array.from({ length: n }, (_, i) => ({ t: start + i * size, recs: [] }));
  for (const r of recs) { const i = Math.floor((r.ts - start) / size); if (i >= 0 && i < n) buckets[i].recs.push(r); }
  return { buckets, size };
}
const clock = (t, size, secs = false) => { const d = new Date(t); const p2 = (n) => String(n).padStart(2, '0'); return size >= 86400e3 ? `${d.getMonth() + 1}/${d.getDate()}` : `${p2(d.getHours())}:${p2(d.getMinutes())}${secs ? `:${p2(d.getSeconds())}` : ''}`; };
const sizeLabel = (size) => (size >= 3600e3 ? `${size / 3600e3} h` : `${size / 60e3} min`);

// ---------------------------------------------------------------- dashboard

function tile(label, value, sub, spark, { title, color } = {}) {
  return el('div', { class: 'card stats-tile', title },
    el('div', { class: 'stats-tl' }, label),
    el('div', { class: 'stats-tv mono num', style: color ? { color } : {} }, value),
    sub ? el('div', { class: 'stats-ts mono faint' }, sub) : null,
    spark ? el('div', { class: 'stats-spark' }, spark) : null);
}
function chartCard(title, chart, { sub, wide, extra } = {}) {
  return el('section', { class: ['card', 'stats-card', wide && 'wide'] },
    el('div', { class: 'row stats-ch' }, el('div', { class: 'stats-ct' }, title), el('span', { class: 'spacer' }), sub ? el('span', { class: 'faint mono stats-cs' }, sub) : null),
    chart, extra || null);
}
function table(headers, rows) {
  return el('div', { class: 'scroll stats-tw' }, el('table', { class: 'table stats-table' },
    el('thead', {}, el('tr', {}, headers.map((h) => el('th', {}, h)))),
    el('tbody', {}, rows.map((r) => el('tr', {}, r.map((c) => el('td', { class: typeof c === 'number' || /^[\d.,$%€£ ]+(ms|s)?$/.test(String(c)) ? 'mono' : '' }, c ?? '—')))))));
}

async function labelledAnswers(since) {
  const out = [];
  let convs = [];
  try { convs = await listConversations(); } catch { return out; }
  for (const c of convs) {
    if (c.mode === 'chat') continue;
    let conv;
    try { conv = await getConversation(c.id); } catch { continue; }
    for (const t of conv?.turns || []) {
      if (t.kind !== 'systemone' || t.status !== 'ok' || !t.response || (t.createdAt || 0) < since) continue;
      const labels = t.labels || {};
      if (!Object.keys(labels).length) continue;
      const st = statsFor(t.response.answers, t.request?.questions);
      for (const [qid, lab] of Object.entries(labels)) {
        if (!st[qid] || typeof lab?.correct !== 'boolean') continue;
        out.push({ p: st[qid].topP, correct: lab.correct, type: st[qid].type, qid, convId: conv.id });
      }
    }
  }
  return out;
}

function groupBy(recs, keyFn) {
  const m = new Map();
  for (const r of recs) { const k = keyFn(r); if (!m.has(k)) m.set(k, []); m.get(k).push(r); }
  return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
}
function breakdownRows(groups, settings) {
  return groups.map(([k, rs]) => {
    const lat = rs.map((r) => r.clientMs).filter(Number.isFinite);
    const tin = rs.map((r) => r.usage?.inputTokens).filter(Number.isFinite);
    const err = rs.filter((r) => !r.ok && r.errorKind !== 'aborted').length;
    let cost = 0;
    try { for (const r of rs) cost += costOf({ input_tokens: r.usage?.inputTokens || 0, output_tokens: r.usage?.outputTokens || 0 }, settings); } catch { cost = null; }
    return [String(k), fmtInt(rs.length), lat.length ? fmtMs(mean(lat)) : '—', lat.length ? fmtMs(pctl(lat, 95)) : '—', tin.length ? fmtInt(Math.round(mean(tin))) : '—',
      err ? fmtPct(err / rs.length) : '0%', cost === null ? '—' : fmtCost(cost, settings.currency || '$')];
  });
}

export function mountStatsView(root) {
  // default to 24 h: the log is persistent, and "session" (since this page load) is empty
  // right after a reload even though the sidebar totals show requests
  let range = loadLS(LS_RANGE, '24h');
  if (!RANGES.some((r) => r.id === range)) range = '24h';
  let alive = true;
  const view = el('div', { class: 'stats-view' });
  root.appendChild(view);
  const head = el('div', { class: 'stats-top' });
  const body = el('div', { class: 'stats-body' });
  view.append(head, body);

  function drawHead() {
    head.textContent = '';
    head.append(
      el('div', {}, el('h2', { class: 'stats-h' }, icon('stats', 18), ' Stats'), el('div', { class: 'muted' }, 'Every request the UI makes, from the local request log. Cost uses the prices in Settings.')),
      el('span', { class: 'spacer' }),
      el('div', { class: 'tabs stats-range' }, RANGES.map((r) => el('button', { class: ['tab', r.id === range && 'active'], type: 'button', onclick: () => { range = r.id; saveLS(LS_RANGE, range); drawHead(); load(); } }, r.label))),
      el('div', { class: 'row' },
        el('button', { class: 'btn sm ghost', type: 'button', onclick: () => exportLog('json') }, icon('download', 14), ' log JSON'),
        el('button', { class: 'btn sm ghost', type: 'button', onclick: () => exportLog('csv') }, icon('download', 14), ' log CSV'),
        el('button', { class: 'btn sm ghost', type: 'button', onclick: async () => { if (await confirmDialog('Reset the all-time totals? The request log stays.', { danger: true })) { resetTotals(); toast('Totals reset', { kind: 'ok' }); load(); } } }, icon('refresh', 14), ' Reset totals'),
        el('button', { class: 'btn sm danger', type: 'button', onclick: async () => { if (await confirmDialog('Clear the whole request log? Totals stay.', { danger: true })) { await clearRequests(); toast('Request log cleared', { kind: 'ok' }); load(); } } }, icon('trash', 14), ' Clear log')));
  }

  async function exportLog(kind) {
    const recs = await listRequests({ since: 0, limit: 5000 });
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    if (kind === 'json') { download(`openjev-requests-${stamp}.json`, recs); return; }
    const cols = ['id', 'ts', 'endpoint', 'source', 'convId', 'turnId', 'label', 'model', 'status', 'ok', 'errorKind', 'clientMs', 'model_ms', 'server_ms', 'total_ms', 'upstream_ms', 'requestBytes', 'responseBytes', 'inputTokens', 'outputTokens', 'imageCount', 'questionCount', 'steps', 'samples', 'think', 'sequential', 'ttftMs', 'tokensPerSec', 'bodyHash'];
    const q = (v) => { const t = v === null || v === undefined ? '' : String(v); return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
    const lines = [cols.join(',')].concat(recs.map((r) => [r.id, new Date(r.ts).toISOString(), r.endpoint, r.source, r.convId, r.turnId, r.label, r.model, r.status, r.ok, r.errorKind, r.clientMs,
      r.serverTiming?.model, r.serverTiming?.server, r.serverTiming?.total, r.serverTiming?.upstream, r.requestBytes, r.responseBytes, r.usage?.inputTokens, r.usage?.outputTokens,
      r.imageCount, r.questionCount, r.options?.steps, r.options?.samples, r.options?.think, r.options?.sequential, r.chat?.ttftMs, r.chat?.tokensPerSec, r.bodyHash].map(q).join(',')));
    download(`openjev-requests-${stamp}.csv`, lines.join('\n'), 'text/csv');
  }

  let loading = false, again = false;
  async function load() {
    if (loading) { again = true; return; }
    loading = true;
    const rg = RANGES.find((r) => r.id === range);
    const since = rg.since();
    let recs = [];
    try { recs = await listRequests({ since, limit: 5000 }); } catch (e) { console.warn('[ojui] listRequests failed', e); }
    recs = (recs || []).filter((r) => r.ts >= since).sort((a, b) => a.ts - b.ts);
    const labelled = await labelledAnswers(since);
    if (!alive) return;
    draw(recs, since, labelled);
    loading = false;
    if (again) { again = false; load(); }
  }

  function draw(recs, since, labelled) {
    body.textContent = '';
    const settings = settingsSafe();
    const cur = settings.currency || '$';
    let totals = null;
    try { totals = getTotals(); } catch { totals = null; }
    const now = Date.now();
    const { buckets, size } = bucketize(recs, range === 'all' || range === 'session' ? (recs[0]?.ts ?? since) : since, now);
    const perBucket = (f) => buckets.map((b) => f(b.recs));
    const s1 = recs.filter((r) => r.endpoint === '/v1/systemone');
    const chat = recs.filter((r) => r.endpoint === '/v1/chat/completions');
    const ok = recs.filter((r) => r.ok);
    const errs = recs.filter((r) => !r.ok && r.errorKind !== 'aborted');
    const aborted = recs.filter((r) => r.errorKind === 'aborted');
    const lat = recs.map((r) => r.clientMs).filter(Number.isFinite);
    const srv = ok.map((r) => r.serverTiming?.total).filter(Number.isFinite);
    const over = ok.filter((r) => Number.isFinite(r.clientMs) && Number.isFinite(r.serverTiming?.total)).map((r) => r.clientMs - r.serverTiming.total);
    // recompute from the raw timings so older records (burst streams) get the same e2e fallback
    const tps = chat.filter((r) => r.ok).map((r) => chatThroughput(r.usage?.outputTokens, r.chat?.ttftMs, r.chat?.streamMs)?.tps ?? r.chat?.tokensPerSec).filter(Number.isFinite);
    const tin = recs.reduce((a, r) => a + (r.usage?.inputTokens || 0), 0);
    const tout = recs.reduce((a, r) => a + (r.usage?.outputTokens || 0), 0);
    const imgTok = recs.reduce((a, r) => a + (r.estImageTokens ?? (r.imageCount || 0) * IMAGE_TOKENS_EST), 0);
    const qn = s1.reduce((a, r) => a + (r.questionCount || 0), 0);
    let cost = null;
    try { cost = costOf({ input_tokens: tin, output_tokens: tout }, settings); } catch { cost = null; }
    const allTime = range === 'all' && totals;
    const sparkReq = sparkline(perBucket((rs) => rs.length), { tipFmt: (v, i) => `${clock(buckets[i].t, size)} · ${v} req` });
    const sparkTok = sparkline(perBucket((rs) => rs.reduce((a, r) => a + (r.usage?.inputTokens || 0) + (r.usage?.outputTokens || 0), 0)), { color: 'var(--accent-2)', tipFmt: (v, i) => `${clock(buckets[i].t, size)} · ${fmtTokens(v)} tok` });
    const sparkLat = sparkline(perBucket((rs) => pctl(rs.map((r) => r.clientMs), 50)), { color: 'var(--type-score)', tipFmt: (v, i) => `${clock(buckets[i].t, size)} · p50 ${fmtMs(v)}` });
    const sparkErr = sparkline(perBucket((rs) => (rs.length ? rs.filter((r) => !r.ok && r.errorKind !== 'aborted').length / rs.length : null)), { color: 'var(--err)', min: 0, max: 1, tipFmt: (v, i) => `${clock(buckets[i].t, size)} · ${fmtPct(v)}` });
    const sparkQ = sparkline(perBucket((rs) => rs.reduce((a, r) => a + (r.questionCount || 0), 0)), { color: 'var(--type-choice)', tipFmt: (v, i) => `${clock(buckets[i].t, size)} · ${v} questions` });

    const reqN = allTime ? totals.requests : recs.length;
    const tiles = el('div', { class: 'stats-tiles' },
      tile('requests', fmtInt(reqN), allTime ? `${fmtInt(totals.ok)} ok · ${fmtInt(totals.errors)} err · since ${fmtRelTime(totals.since)}` : `${ok.length} ok · ${errs.length} err${aborted.length ? ` · ${aborted.length} stopped` : ''}`, sparkReq),
      tile('input tokens', fmtTokens(allTime ? totals.inputTokens : tin), `${fmtTokens(allTime ? totals.outputTokens : tout)} out (thought) · ~${fmtTokens(allTime ? totals.estImageTokens : imgTok)} img est`, sparkTok),
      tile('est. cost', cost === null ? '—' : fmtCost(allTime ? costOf({ input_tokens: totals.inputTokens, output_tokens: totals.outputTokens }, settings) : cost, cur),
        `${settings.pricePerMInput || 0} / ${settings.pricePerMOutput || 0} ${cur} per 1M in/out`, null, { title: 'derived at render time from tokens and the current prices' }),
      tile('latency p50', lat.length ? fmtMs(pctl(lat, 50)) : '—', lat.length ? `p95 ${fmtMs(pctl(lat, 95))} · p99 ${fmtMs(pctl(lat, 99))}` : 'client, fetch → body read', sparkLat),
      tile('server total p50', srv.length ? fmtMs(pctl(srv, 50)) : '—', srv.length ? `p95 ${fmtMs(pctl(srv, 95))}` : 'Server-Timing total'),
      tile('mean overhead', over.length ? fmtMs(mean(over)) : '—', 'client − server total (proxy + network + JSON)'),
      tile('chat tok/s p50', tps.length ? pctl(tps, 50).toFixed(1) : '—', chat.length ? `${chat.length} chats · ttft p50 ${fmtMs(pctl(chat.map((r) => r.chat?.ttftMs), 50))}` : 'no chat yet'),
      tile('error rate', recs.length ? fmtPct(errs.length / recs.length) : '—', errs.length ? groupBy(errs, (r) => r.status).slice(0, 3).map(([k, v]) => `${k}×${v.length}`).join(' ') : 'no errors', sparkErr, { color: errs.length ? 'var(--err)' : null }),
      tile('questions asked', fmtInt(allTime ? totals.questions : qn), s1.length ? `${(qn / s1.length).toFixed(1)} per request` : '', sparkQ),
      tile('tokens / question', qn ? (s1.reduce((a, r) => a + (r.usage?.inputTokens || 0), 0) / qn).toFixed(1) : '—', 'input tokens ÷ questions (System One)'));
    body.appendChild(tiles);

    if (!recs.length) {
      body.appendChild(el('div', { class: 'card stats-none' }, el('p', {}, 'No requests in this range yet.'), el('p', { class: 'muted' }, 'Send a decision from a conversation, run a template or a batch, and come back.')));
      return;
    }
    const grid = el('div', { class: 'stats-grid' });
    body.appendChild(grid);

    // 1. latency histogram by endpoint
    const byEp = {};
    for (const r of recs) if (Number.isFinite(r.clientMs)) (byEp[r.endpoint] ||= []).push(r.clientMs);
    const epColors = { '/v1/systemone': 'var(--accent)', '/v1/chat/completions': 'var(--accent-2)' };
    grid.appendChild(chartCard('Latency distribution', histogram({ series: byEp }, { logX: true, bins: 24, xFmt: shortMs, colors: epColors }),
      { sub: 'client ms, log x', extra: legend(Object.keys(byEp).map((k) => ({ label: k, color: epColors[k] || 'var(--fg-muted)' }))) }));

    // 2. latency over time with rolling p50
    const pts = recs.filter((r) => Number.isFinite(r.clientMs)).map((r) => ({ x: r.ts, y: Math.max(0.1, r.clientMs), color: statusColor(r), tip: `${new Date(r.ts).toLocaleTimeString()} · ${r.endpoint.replace('/v1/', '')} · ${r.status} · ${fmtMs(r.clientMs)}${r.label ? ` · ${r.label}` : ''}` }));
    const roll = pts.map((p, i) => ({ x: p.x, y: percentile(pts.slice(Math.max(0, i - 14), i + 1).map((q) => q.y), 50) }));
    grid.appendChild(chartCard('Latency over time', scatter(pts, { logY: true, yFmt: shortMs, xFmt: (t) => clock(t, size, (pts[pts.length - 1]?.x - pts[0]?.x) < 15 * 60e3), lines: [{ points: roll, color: 'var(--fg-muted)', label: 'p50 (15)' }] }),
      { sub: 'coloured by status', extra: legend([{ label: 'ok', color: 'var(--ok)' }, { label: '4xx', color: 'var(--warn)' }, { label: '5xx / network', color: 'var(--err)' }, { label: 'stopped', color: 'var(--fg-faint)' }]) }));

    // 3. timing breakdown by model
    const timed = ok.filter((r) => Number.isFinite(r.clientMs) && Number.isFinite(r.serverTiming?.total));
    const tb = groupBy(timed, (r) => r.model || '?').slice(0, 8).map(([model, rs]) => {
      const m = (f) => mean(rs.map(f).filter(Number.isFinite)) || 0;
      const total = m((r) => r.serverTiming.total), model_ = m((r) => r.serverTiming.model), server = m((r) => r.serverTiming.server);
      const up = m((r) => r.serverTiming.upstream), client = m((r) => r.clientMs);
      const modelNA = model_ === 0 && total > 5;
      return { model, n: rs.length, segs: [
        { label: modelNA ? 'model (n/a on MLX)' : 'model', value: modelNA ? 0 : model_, color: 'var(--type-score)' },
        { label: 'server', value: modelNA ? total : server, color: 'var(--accent)' },
        { label: 'proxy↔up', value: up ? Math.max(0, up - total) : 0, color: 'var(--accent-2)' },
        // a chat stream's headers arrive before generation, so the remainder is the stream itself, not network
        { label: rs.every((r) => r.endpoint === '/v1/chat/completions') ? 'stream body (generation, after headers)' : 'browser↔proxy', value: Math.max(0, client - (up || total)), color: 'var(--fg-muted)' },
      ], client, modelNA };
    });
    const maxClient = Math.max(1, ...tb.map((t) => t.client));
    grid.appendChild(chartCard('Timing breakdown (mean)', el('div', { class: 'stats-tbd' }, tb.map((t) => el('div', { class: 'stats-tbrow' },
      el('span', { class: 'mono stats-tbl' }, trunc(t.model, 18), el('span', { class: 'faint' }, ` n=${t.n}`)),
      el('span', { class: 'stats-tbbar' }, stackedBar(t.segs, { width: 300, height: 14, total: Math.max(maxClient, t.segs.reduce((a, sg) => a + sg.value, 0)), fmt: (v) => fmtMs(v) })), // shared axis: the track is the slowest model's mean
      el('span', { class: 'mono faint' }, fmtMs(t.client), t.modelNA ? ' · model n/a' : '')))),
    { sub: 'per model', extra: legend([{ label: 'model', color: 'var(--type-score)' }, { label: 'server', color: 'var(--accent)' }, { label: 'proxy↔OpenJev', color: 'var(--accent-2)' }, { label: 'browser↔proxy (chat: stream body)', color: 'var(--fg-muted)' }]) }));

    // 4. request rate
    const perMin = size / 60e3;
    grid.appendChild(chartCard('Request rate', bars(buckets.map((b) => ({ label: clock(b.t, size), value: b.recs.length / perMin, tip: `${clock(b.t, size)} · ${b.recs.length} req in ${sizeLabel(size)}` })), { yFmt: (v) => v.toFixed(v < 10 ? 1 : 0), color: 'var(--accent)' }),
      { sub: `req/min · ${sizeLabel(size)} buckets` }));

    // 5. tokens over time + empirical token model
    grid.appendChild(chartCard('Tokens over time', stackedBars(buckets.map((b) => ({ label: clock(b.t, size), parts: { in: b.recs.reduce((a, r) => a + (r.usage?.inputTokens || 0), 0), out: b.recs.reduce((a, r) => a + (r.usage?.outputTokens || 0), 0) } })),
      [{ key: 'in', label: 'input', color: 'var(--accent)' }, { key: 'out', label: 'output / thought', color: 'var(--type-noul)' }]),
    { sub: `${sizeLabel(size)} buckets`, extra: legend([{ label: 'input', color: 'var(--accent)' }, { label: 'output (thought / completion)', color: 'var(--type-noul)' }]) }));

    const tm = tokenModel(recs);
    const qLine = [], iLine = [];
    if (tm.perQ !== null && tm.pts.length) {
      const xs = tm.pts.map((r) => r.questionCount || 0);
      const avgImg = mean(tm.pts.map((r) => r.imageCount || 0)) || 0;
      for (const x of [Math.min(...xs), Math.max(...xs)]) qLine.push({ x, y: tm.base + tm.perQ * x + (tm.perImg || 0) * avgImg });
    }
    if (tm.perImg !== null && tm.pts.length) {
      const xs = tm.pts.map((r) => r.imageCount || 0);
      const avgQ = mean(tm.pts.map((r) => r.questionCount || 0)) || 0;
      for (const x of [Math.min(...xs), Math.max(...xs)]) iLine.push({ x, y: tm.base + tm.perImg * x + (tm.perQ || 0) * avgQ });
    }
    grid.appendChild(chartCard('Tokens vs questions', scatter(tm.pts.map((r) => ({ x: r.questionCount || 0, y: r.usage.inputTokens, color: r.imageCount ? 'var(--type-noul)' : 'var(--accent)', tip: `${r.questionCount} q · ${r.imageCount} img · ${fmtInt(r.usage.inputTokens)} tok` })),
      { xLabel: 'questions', yLabel: 'input tok', lines: qLine.length ? [{ points: qLine, color: 'var(--accent-2)', dash: '4 3', label: `≈ ${tm.perQ.toFixed(1)} tok/question` }] : [] }),
    { sub: tm.perQ !== null ? `≈ ${tm.perQ.toFixed(1)} tok/question · base ${Math.round(tm.base)}` : 'needs requests with different question counts' }));
    grid.appendChild(chartCard('Tokens vs images', scatter(tm.pts.map((r) => ({ x: r.imageCount || 0, y: r.usage.inputTokens, color: 'var(--type-noul)', tip: `${r.imageCount} img · ${r.questionCount} q · ${fmtInt(r.usage.inputTokens)} tok` })),
      { xLabel: 'images', yLabel: 'input tok', lines: iLine.length ? [{ points: iLine, color: 'var(--type-noul)', dash: '4 3', label: `≈ ${tm.perImg.toFixed(0)} tok/image` }] : [] }),
    { sub: tm.perImg !== null ? `≈ ${tm.perImg.toFixed(0)} tok/image (README: ~${IMAGE_TOKENS_EST}) · fit on ${tm.n} req` : 'run requests with and without images to estimate' }));

    // 6/7. confidence and entropy distributions
    const ans = s1.filter((r) => Array.isArray(r.answers)).flatMap((r) => r.answers);
    const byType = (f) => { const o = {}; for (const a of ans) if (Number.isFinite(f(a))) (o[a.type] ||= []).push(f(a)); return o; };
    grid.appendChild(chartCard('Confidence distribution', histogram({ series: byType((a) => a.confidence) }, { min: 0, max: 1, bins: 20, colors: TYPE_COLOR, xFmt: (v) => v.toFixed(1) }),
      { sub: `${ans.length} answers`, extra: legend(['noul', 'choice', 'score'].map((t) => ({ label: t, color: TYPE_COLOR[t] }))) }));
    const ent = byType((a) => a.entropyBits);
    const entMax = Math.max(1, ...Object.values(ent).flat());
    grid.appendChild(chartCard('Entropy distribution', histogram({ series: ent }, { min: 0, max: entMax, bins: 20, colors: TYPE_COLOR, xFmt: (v) => v.toFixed(1) }),
      { sub: 'bits · 0 = certain', extra: legend(['noul', 'choice', 'score'].map((t) => ({ label: t, color: TYPE_COLOR[t] }))) }));

    // 8. calibration
    if (labelled.length) {
      const cal = calibration(labelled);
      grid.appendChild(chartCard('Calibration (your labels)', reliability(cal.bins, { width: 360, height: 250 }),
        { sub: `${cal.n} labelled`, extra: el('div', { class: 'row mono stats-cal' },
          el('span', {}, `accuracy ${fmtPct(cal.acc)}`), el('span', {}, `Brier ${cal.brier.toFixed(3)}`), el('span', {}, `ECE ${cal.ece === null ? '—' : cal.ece.toFixed(3)}`),
          cal.below ? el('span', { class: 'faint' }, `${cal.below} with top-p < 0.5 not binned`) : null) }));
    } else {
      grid.appendChild(chartCard('Calibration', histogram(ans.map((a) => a.topP).filter(Number.isFinite), { min: 0, max: 1, bins: 20, xFmt: (v) => v.toFixed(1), colors: { all: 'var(--accent)' } }),
        { sub: 'top-p histogram', extra: el('div', { class: 'faint stats-hint' }, 'Label answers with correct/wrong (the check and x buttons on each answer card) to get a real reliability diagram, Brier score and ECE.') }));
    }

    // 9. breakdowns
    const H = ['', 'requests', 'mean latency', 'p95', 'mean in tok', 'errors', 'cost'];
    grid.appendChild(chartCard('By model', table(H, breakdownRows(groupBy(recs, (r) => r.model || '?'), settings)), { wide: true }));
    grid.appendChild(chartCard('By source', table(H, breakdownRows(groupBy(recs, (r) => r.source || '?'), settings)), { wide: true }));
    grid.appendChild(chartCard('By status', table(H, breakdownRows(groupBy(recs, (r) => `${r.status}${r.errorKind && !r.ok ? ` ${r.errorKind}` : ''}`), settings)), { wide: true }));
    grid.appendChild(chartCard('By options', table(H, breakdownRows(groupBy(s1, (r) => {
      const o = r.options || {};
      return [o.steps ? `steps ${o.steps}` : null, o.samples ? `samples ${o.samples}` : null, Number.isFinite(o.think) && o.think !== null ? `think ${o.think}` : null, o.sequential ? 'seq' : null, r.imageCount ? `${r.imageCount} img` : null].filter(Boolean).join(' · ') || 'defaults';
    }), settings)), { wide: true, sub: 'System One only' }));
  }

  drawHead();
  body.appendChild(el('div', { class: 'skeleton stats-skel' }));
  load();
  let t = null;
  const off = on('oj:request-done', () => { clearTimeout(t); t = setTimeout(() => { if (alive) load(); }, 800); });
  return () => { alive = false; clearTimeout(t); if (typeof off === 'function') off(); };
}

// ---------------------------------------------------------------- per-conversation strip

/** renderConversationStats(conversation, ctx) → HTMLElement for #conv-stats. */
export function renderConversationStats(conversation, ctx = {}) {
  const settings = ctx.settings || settingsSafe();
  const turns = conversation?.turns || [];
  const done = turns.filter((t) => t.status === 'ok' || t.status === 'error');
  const ok = turns.filter((t) => t.status === 'ok');
  const err = turns.filter((t) => t.status === 'error');
  let tin = 0, tout = 0;
  for (const t of ok) {
    if (t.kind === 'chat') { tin += t.usage?.prompt_tokens || 0; tout += t.usage?.completion_tokens || 0; }
    else { tin += t.response?.usage?.input_tokens || 0; tout += t.response?.usage?.output_tokens || 0; }
  }
  let cost = null;
  try { cost = costOf({ input_tokens: tin, output_tokens: tout }, settings); } catch { cost = null; }
  const lat = done.map((t) => t.http?.clientMs);
  const latF = lat.filter(Number.isFinite);
  const confPerTurn = turns.map((t) => {
    if (t.kind !== 'systemone' || t.status !== 'ok' || !t.response) return null;
    const st = statsFor(t.response.answers, t.request?.questions);
    return mean(Object.values(st).filter(Boolean).map((x) => x.confidence));
  });
  const confF = confPerTurn.filter(Number.isFinite);
  const labels = turns.reduce((a, t) => a + Object.keys(t.labels || {}).length, 0);
  const m = (label, value, t) => el('div', { class: 'stats-m', title: t || '' }, el('span', { class: 'faint' }, label), el('span', { class: 'mono num' }, value));
  return el('div', { class: 'stats-strip' },
    m('turns', fmtInt(turns.length)),
    m('ok / err', `${ok.length} / ${err.length}`),
    m('tokens in / out', `${fmtTokens(tin)} / ${fmtTokens(tout)}`),
    m('cost', cost === null ? '—' : fmtCost(cost, settings.currency || '$')),
    m('latency mean', latF.length ? fmtMs(mean(latF)) : '—'),
    m('p95', latF.length ? fmtMs(pctl(latF, 95)) : '—'),
    m('mean conf', confF.length ? el('span', { style: { color: confColor(mean(confF)) } }, mean(confF).toFixed(2)) : '—'),
    labels ? m('labelled', fmtInt(labels)) : null,
    el('div', { class: 'stats-m stats-m-spark' }, el('span', { class: 'faint' }, 'latency / turn'), sparkline(lat, { width: 110, height: 24, color: 'var(--type-score)', tipFmt: (v, i) => `#${i + 1} · ${fmtMs(v)}` })),
    el('div', { class: 'stats-m stats-m-spark' }, el('span', { class: 'faint' }, 'confidence / turn'), sparkline(confPerTurn, { width: 110, height: 24, min: 0, max: 1, color: 'var(--ok)', tipFmt: (v, i) => `#${i + 1} · conf ${v.toFixed(3)}` })));
}


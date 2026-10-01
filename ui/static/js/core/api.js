// core/api.js — the only module that fetches /v1/* and /ui/api/* (builder B).
// systemOne / chatStream measure timing and bytes, normalize errors, and record every
// call (RequestRecord → store log + totals + oj:request-done). Health polling lives here too.

import { emit } from '/js/core/bus.js';
import { getSettings, logRequest, addToTotals } from '/js/core/store.js';
import { uid } from '/js/core/format.js';
import { parseServerTiming, summarizeAnswers, sha256Hex, IMAGE_TOKENS_EST, chatThroughput } from '/js/core/metrics.js';

const DEFAULT_CONFIG = {
  uiVersion: '0.1.0',
  openjevUrl: 'http://127.0.0.1:8080',
  proxyBase: '',
  authConfigured: false,
  defaults: { model: 'openjev-latest', chatModel: 'diffusiongemma-26b' },
  limits: {
    maxImages: 8, maxImageBytes: 5242880, maxQuestions: 256, stepsMax: 8, samplesMax: 32, thinkMax: 4096,
    chatMaxTokensDefault: 1024, chatMaxTokensCap: 8192, choiceMaxOptions: 255, scoreMaxLevels: 10,
  },
  hints: { start: 'mise run startOpenJev', logs: 'mise run logsOpenJev', status: 'mise run statusOpenJev', stop: 'mise run stopOpenJev' },
};

let config = null;
let lastHealth = null;
let pollTimer = null;
let nextCheckAt = null;

const enc = new TextEncoder();
const byteLen = (s) => (s ? enc.encode(s).length : 0);

// Read from settings on every call: '' lets the proxy decide.
export const authOverride = {
  get value() { return getSettings().authOverride || ''; },
  toString() { return this.value; },
};

function headers(accept = 'application/json') {
  const hd = { 'content-type': 'application/json', accept };
  const a = authOverride.value;
  if (a) hd.authorization = a;
  return hd;
}

// ------------------------------------------------------------------- config
export async function getConfig() {
  if (config) return config;
  try {
    const r = await fetch('/ui/api/config', { cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const c = await r.json();
    config = { ...DEFAULT_CONFIG, ...c, defaults: { ...DEFAULT_CONFIG.defaults, ...(c.defaults || {}) }, limits: { ...DEFAULT_CONFIG.limits, ...(c.limits || {}) }, hints: { ...DEFAULT_CONFIG.hints, ...(c.hints || {}) } };
  } catch (err) {
    console.warn('[api] /ui/api/config unavailable, using defaults', err);
    config = { ...DEFAULT_CONFIG, _fallback: true };
  }
  return config;
}

export function getCachedConfig() { return config; }

// ------------------------------------------------------------------- health
export async function health() {
  let h;
  try {
    const r = await fetch('/ui/api/health', { cache: 'no-store' });
    h = await r.json();
    if (!h || typeof h !== 'object' || !('ok' in h)) throw new Error('bad health shape');
  } catch {
    h = { ok: false, proxyDown: true, checkedAt: Date.now() };
  }
  lastHealth = h;
  emit('oj:health', { health: h });
  return h;
}

function schedule() {
  clearTimeout(pollTimer);
  const ms = lastHealth && lastHealth.ok ? 10000 : 4000;
  nextCheckAt = Date.now() + ms;
  pollTimer = setTimeout(async () => { await health(); schedule(); }, ms);
}

export function startHealthPolling() {
  health().then(schedule);
  window.addEventListener('focus', () => { health().then(schedule); });
}

export function getLastHealth() { return lastHealth; }

// B-private: when the next poll happens (for the banner countdown)
export function nextHealthCheckAt() { return nextCheckAt; }
export async function checkHealthNow() { const h = await health(); schedule(); return h; }

// ------------------------------------------------------------------- errors
const TITLES = {
  validation: 'Request failed validation',
  bad_request: "The server can't ask this",
  auth: 'API key rejected',
  forbidden: 'API key required or origin not allowed',
  too_large: 'Request body too large',
  rate_limited: 'Rate limited',
  overloaded: 'OpenJev is overloaded',
  unavailable: 'Inference backend unavailable',
  upstream_down: 'OpenJev is not running',
  timeout: 'Upstream timed out',
  network: 'UI server unreachable',
  aborted: 'Stopped',
  client: 'Fix the request',
  unknown: 'Unexpected response',
};

function getHeader(headers, name) {
  if (!headers) return null;
  if (typeof headers.get === 'function') return headers.get(name);
  const k = Object.keys(headers).find((x) => x.toLowerCase() === name);
  return k ? headers[k] : null;
}

function deriveHint(kind, message, errorType) {
  const m = String(message || '').toLowerCase();
  if (kind === 'bad_request') {
    if (m.includes('unknown model')) return 'Pick a model from the models popover (health pill) or the model chip.';
    if (m.includes('image') && (m.includes('think') || m.includes('sequential'))) return 'Turn off think or sequential when sending images.';
    if (m.includes('option') || m.includes('level')) return 'Limits: 255 options per choice, 1–10 score levels.';
    if (errorType === 'api_usage_error') return 'Check the model name and question types (noul, choice, score).';
    return null;
  }
  if (kind === 'auth' || kind === 'forbidden') return 'The UI proxy sends OPENJEV_API_KEY. Start it with the server\'s key: OPENJEV_API_KEY=… mise run ui';
  if (kind === 'upstream_down') return 'mise run startOpenJev';
  if (kind === 'timeout') return 'No answer within 900 s.';
  if (kind === 'network') return 'Is `mise run ui` still running?';
  if (kind === 'unavailable') return 'The inference backend (or a routed model\'s container) is down.';
  return null;
}

export function normalizeError({ status = 0, bodyText = '', headers = null, endpoint = '', exception = null } = {}) {
  const requestId = getHeader(headers, 'x-request-id') || getHeader(headers, 'x-typesafe-request-id') || null;
  const ra = getHeader(headers, 'retry-after');
  let retryAfter = ra !== null && ra !== undefined && ra !== '' && Number.isFinite(Number(ra)) ? Number(ra) : null;
  const raw = String(bodyText || '').slice(0, 20000);

  if (exception) {
    const name = exception.name || '';
    let kind = 'client';
    if (name === 'AbortError') kind = 'aborted';
    else if (exception instanceof TypeError) kind = 'network';
    return {
      status: 0, kind, title: TITLES[kind],
      message: kind === 'aborted' ? 'The request was stopped before it finished.' :
        kind === 'network' ? 'The UI server itself is unreachable. Is `mise run ui` still running?' :
          String(exception.message || exception),
      errorType: null, details: null, requestId, retryAfter: null, hint: deriveHint(kind, '', null), raw: raw || String(exception.stack || exception), endpoint,
    };
  }

  let body = null;
  try { body = raw ? JSON.parse(raw) : null; } catch { body = null; }
  let message = '', errorType = null, details = null, hint = null;
  const detail = body && typeof body === 'object' ? body.detail : undefined;
  if (Array.isArray(detail)) {
    details = detail.map((d) => ({ loc: d.loc || [], msg: d.msg || '', type: d.type || '', input: d.input }));
    message = details.map((d) => `${(d.loc || []).filter((x) => x !== 'body').join(' › ')}: ${d.msg}`).join('\n');
  } else if (typeof detail === 'string') {
    message = detail;
  } else if (detail && typeof detail === 'object') {
    message = detail.message || JSON.stringify(detail);
    errorType = detail.error_type || null;
    hint = detail.hint || null;
  } else if (body && body.error && typeof body.error === 'object') {
    message = body.error.message || '';
    errorType = body.error.type || null;
  } else if (body && typeof body === 'object' && body.message) {
    message = body.message;
  } else {
    message = raw ? raw.slice(0, 500) : `HTTP ${status}`;
  }

  let kind;
  if (status === 422 && details) kind = 'validation';
  else if (status === 422) kind = 'bad_request';
  else if (status === 400) kind = 'bad_request';
  else if (status === 401) kind = 'auth';
  else if (status === 403) kind = 'forbidden';
  else if (status === 413) kind = 'too_large';
  else if (status === 429) kind = 'rate_limited';
  else if (status === 529) kind = 'overloaded';
  else if (status === 503) kind = 'unavailable';
  else if (status === 502 && errorType === 'upstream_unreachable') kind = 'upstream_down';
  else if (status === 504) kind = 'timeout';
  else kind = 'unknown';

  if ((kind === 'rate_limited' || kind === 'overloaded') && retryAfter === null) retryAfter = 1;
  let title = TITLES[kind];
  if (kind === 'unknown') title = status === 404 ? 'Not found' : status === 502 ? 'Bad gateway' : status >= 500 ? 'Server error' : status >= 200 && status < 300 ? 'Unexpected response' : `HTTP ${status}`;
  return {
    status, kind, title, message, errorType, details, requestId, retryAfter,
    hint: hint || deriveHint(kind, message, errorType), raw, endpoint,
  };
}

// ------------------------------------------------------------------- body building
export function buildSystemOneBody(draft, options) {
  const o = options || {};
  const body = {
    model: o.model || getSettings().defaultModel,
    state: draft.stateIsJson ? JSON.parse(draft.state) : draft.state,
    questions: draft.questions || {},
  };
  const imgs = (draft.images || []).map((i) => (typeof i === 'string' ? i : i?.dataUrl)).filter(Boolean);
  if (imgs.length) body.images = imgs;
  if (o.steps !== null && o.steps !== undefined && o.steps !== '') body.steps = Number(o.steps);
  if (o.samples !== null && o.samples !== undefined && o.samples !== '') body.samples = Number(o.samples);
  if (o.think !== null && o.think !== undefined && o.think !== '') body.think = Number(o.think);
  if (o.sequential === true) body.sequential = true;
  return body;
}

function httpInfo(res, extra) {
  const hd = res?.headers;
  const ra = getHeader(hd, 'retry-after');
  const st = parseServerTiming(getHeader(hd, 'server-timing'));
  const up = getHeader(hd, 'x-ojui-upstream-ms');
  if (st.upstream === null && up !== null && up !== undefined && Number.isFinite(Number(up))) st.upstream = Number(up);
  return {
    status: res ? res.status : 0,
    requestId: getHeader(hd, 'x-request-id') || getHeader(hd, 'x-typesafe-request-id') || null,
    serverTiming: st,
    retryAfter: ra !== null && ra !== undefined && Number.isFinite(Number(ra)) ? Number(ra) : null,
    ...extra,
  };
}

function countTypes(questions) {
  const out = { noul: 0, choice: 0, score: 0 };
  for (const q of Object.values(questions || {})) if (q && out[q.type] !== undefined) out[q.type]++;
  return out;
}

async function record(rec) {
  try {
    await logRequest(rec);
    addToTotals(rec);
  } catch (err) { console.warn('[api] could not record request', err); }
  emit('oj:request-done', { record: rec });
}

// ------------------------------------------------------------------- System One
export async function systemOne(body, meta = {}) {
  const endpoint = '/v1/systemone';
  let payload = '';
  let buildErr = null;
  try { payload = JSON.stringify(body); } catch (e) { buildErr = e; }
  const startedAt = Date.now();
  const t0 = performance.now();
  let res = null, bodyText = '', exception = buildErr;
  if (!exception) {
    try {
      res = await fetch(endpoint, { method: 'POST', headers: headers(), body: payload, signal: meta.signal, cache: 'no-store' });
      bodyText = await res.text();
    } catch (e) { exception = e; }
  }
  const clientMs = performance.now() - t0;
  const http = httpInfo(res, { clientMs, startedAt, requestBytes: byteLen(payload), responseBytes: byteLen(bodyText) });
  let data = null;
  if (res) { try { data = JSON.parse(bodyText); } catch { data = null; } }
  const ok = !!(res && res.ok && data && data.answers && typeof data.answers === 'object');
  let error = null;
  if (!ok) {
    error = normalizeError({ status: res ? res.status : 0, bodyText, headers: res?.headers, endpoint, exception });
    if (res && res.ok && !exception) { error.message = 'The response had no `answers` object.'; }
  }
  const bodyHash = meta.bodyHash || await sha256Hex(payload);
  const imageCount = Array.isArray(body?.images) ? body.images.length : 0;
  const rec = {
    id: uid('r'), ts: startedAt, endpoint,
    source: meta.source || 'other', convId: meta.convId || null, turnId: meta.turnId || null, label: meta.label ?? null,
    model: body?.model ?? null, status: http.status, ok, errorKind: error?.kind || null,
    clientMs, serverTiming: { model: http.serverTiming.model, server: http.serverTiming.server, total: http.serverTiming.total, upstream: http.serverTiming.upstream },
    requestBytes: http.requestBytes, responseBytes: http.responseBytes,
    usage: { inputTokens: data?.usage?.input_tokens ?? 0, outputTokens: data?.usage?.output_tokens ?? 0 },
    imageCount, estImageTokens: imageCount * IMAGE_TOKENS_EST,
    questionCount: Object.keys(body?.questions || {}).length, questionTypes: countTypes(body?.questions),
    options: { steps: body?.steps ?? null, samples: body?.samples ?? null, think: body?.think ?? null, sequential: !!body?.sequential },
    answers: ok ? summarizeAnswers(data.answers, body?.questions) : null,
    chat: null,
    bodyHash,
  };
  await record(rec);
  return { ok, status: http.status, data: data ?? null, error, http };
}

// ------------------------------------------------------------------- chat (SSE)
export async function chatStream(body, { signal, onDelta, onUsage } = {}, meta = {}) {
  const endpoint = '/v1/chat/completions';
  const payload = JSON.stringify(body);
  const startedAt = Date.now();
  const t0 = performance.now();
  let res = null, exception = null, text = '', finishReason = null, usage = null, streamError = null;
  let ttftMs = null, chunks = 0, responseBytes = 0, errBody = '';
  try {
    res = await fetch(endpoint, { method: 'POST', headers: headers('text/event-stream'), body: payload, signal, cache: 'no-store' });
    if (!res.ok) {
      errBody = await res.text();
      responseBytes = byteLen(errBody);
    } else if (!res.body) {
      errBody = await res.text();
      responseBytes = byteLen(errBody);
    } else {
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let done = false;
      const handleLine = (line) => {
        const l = line.trim();
        if (!l.startsWith('data:')) return;
        const data = l.slice(5).trim();
        if (!data) return;
        if (data === '[DONE]') { done = true; return; }
        let j;
        try { j = JSON.parse(data); } catch { return; }
        if (j.error) { streamError = j.error; return; }
        const ch = j.choices?.[0];
        const delta = ch?.delta?.content;
        if (delta) {
          if (ttftMs === null) ttftMs = performance.now() - t0;
          text += delta;
          chunks++;
          try { onDelta?.(delta, text); } catch (e) { console.warn('[api] onDelta failed', e); }
        }
        if (ch?.finish_reason) finishReason = ch.finish_reason;
        if (j.usage) {
          usage = j.usage;
          try { onUsage?.(usage); } catch (e) { console.warn('[api] onUsage failed', e); }
        }
      };
      while (!done) {
        const { value, done: rd } = await reader.read();
        if (rd) break;
        responseBytes += value.length;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, idx);
          buf = buf.slice(idx + 1);
          handleLine(line);
        }
      }
      if (buf) handleLine(buf);
      try { reader.releaseLock(); } catch { /* ignore */ }
    }
  } catch (e) { exception = e; }
  const streamMs = performance.now() - t0;
  const http = httpInfo(res, {
    clientMs: streamMs, startedAt, requestBytes: byteLen(payload), responseBytes, ttftMs, streamMs, chunks,
  });
  let error = null;
  if (exception) error = normalizeError({ status: res ? res.status : 0, headers: res?.headers, endpoint, exception });
  else if (res && !res.ok) error = normalizeError({ status: res.status, bodyText: errBody, headers: res.headers, endpoint });
  else if (streamError) error = normalizeError({ status: res?.status || 0, bodyText: JSON.stringify({ error: streamError }), headers: res?.headers, endpoint });
  else if (res && !res.body) error = normalizeError({ status: res.status, bodyText: errBody, headers: res.headers, endpoint });
  const ok = !error;
  const completion = usage?.completion_tokens ?? null;
  const thr = chatThroughput(completion ?? chunks, ttftMs, streamMs);
  const tokensPerSec = thr ? thr.tps : null;
  const tokensPerSecBasis = thr ? thr.basis : null;
  const rec = {
    id: uid('r'), ts: startedAt, endpoint,
    source: 'chat', convId: meta.convId || null, turnId: meta.turnId || null, label: meta.label ?? null,
    model: body?.model ?? null, status: http.status, ok, errorKind: error?.kind || null,
    clientMs: streamMs, serverTiming: { model: http.serverTiming.model, server: http.serverTiming.server, total: http.serverTiming.total, upstream: http.serverTiming.upstream },
    requestBytes: http.requestBytes, responseBytes,
    usage: { inputTokens: usage?.prompt_tokens ?? 0, outputTokens: usage?.completion_tokens ?? 0 },
    imageCount: 0, estImageTokens: 0, questionCount: 0, questionTypes: { noul: 0, choice: 0, score: 0 },
    options: { steps: null, samples: null, think: null, sequential: false },
    answers: null,
    chat: { ttftMs, streamMs, chunks, tokensPerSec, tokensPerSecBasis },
    bodyHash: await sha256Hex(payload),
  };
  await record(rec);
  return { ok, text, finishReason, usage, error, http };
}

// ------------------------------------------------------------------- models
export async function listModels() {
  try {
    const r = await fetch('/v1/models', { headers: headers(), cache: 'no-store' });
    const t = await r.text();
    if (!r.ok) return { ok: false, models: [], error: normalizeError({ status: r.status, bodyText: t, headers: r.headers, endpoint: '/v1/models' }) };
    const j = JSON.parse(t);
    return { ok: true, models: Array.isArray(j.models) ? j.models : [], error: null };
  } catch (e) {
    return { ok: false, models: [], error: normalizeError({ exception: e, endpoint: '/v1/models' }) };
  }
}

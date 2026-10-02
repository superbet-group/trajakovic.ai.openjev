// Dev harness only (builder C): replaces window.fetch for /v1/* and /ui/api/* with a fake
// OpenJev that answers deterministically from the request body (same body → same answers),
// sends Server-Timing like the MLX backend (model;dur=0.0) and reproduces 400/422 shapes.

const MODELS = [
  { name: 'openjev-latest', description: 'Alias for the newest OpenJev release. Currently openjev-0.1.', release_date: '2026-09-18' },
  { name: 'openjev-0.1', description: 'OpenJev 0.1: DiffusionGemma 26B-A4B on MLX.', release_date: '2026-09-18' },
  { name: 'diffusiongemma-26b', description: 'DiffusionGemma 26B-A4B text generation.', release_date: '2026-09-18' },
  { name: 'laya-1.0', description: 'Laya by Nandakishor M / Convai Innovations.', release_date: '2026-09-18' },
];

function hash32(str) { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
function rng(seed) { let t = seed >>> 0; return () => { t += 0x6D2B79F5; let r = Math.imul(t ^ (t >>> 15), 1 | t); r ^= r + Math.imul(r ^ (r >>> 7), 61 | r); return ((r ^ (r >>> 14)) >>> 0) / 4294967296; }; }
function normal(r) { return Math.sqrt(-2 * Math.log(r() + 1e-12)) * Math.cos(2 * Math.PI * r()); }
function softmax(xs) { const m = Math.max(...xs); const e = xs.map((x) => Math.exp(x - m)); const s = e.reduce((a, b) => a + b, 0); return e.map((v) => v / s); }
const conf = (ps) => (ps.length <= 1 ? 1 : 1 + ps.reduce((a, p) => a + (p > 0 ? p * Math.log(p) : 0), 0) / Math.log(ps.length));

function json(status, body, extra = {}) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', 'x-request-id': `req_${Math.random().toString(16).slice(2, 18)}${Math.random().toString(16).slice(2, 18)}`, ...extra } });
}

function answer(body) {
  const qs = body.questions || {};
  const seedKey = JSON.stringify([body.state, qs, body.images || []]) + (body.model || '') + (body.steps || '') + (body.samples || '') + (body.think ?? '');
  const out = {};
  for (const [qid, q] of Object.entries(qs)) {
    const r = rng(hash32(seedKey + qid));
    const sharp = body.steps > 1 ? 4.2 : 3.2;
    if (q.type === 'noul') out[qid] = { type: 'noul', noul: 1 / (1 + Math.exp(-normal(r) * sharp)) };
    else if (q.type === 'choice') {
      // order-independent: each option's logit depends on its name, so a permutation maps back
      const keys = Object.keys(q.criteria);
      const ps = softmax(keys.map((k) => normal(rng(hash32(seedKey + qid + k))) * sharp));
      const probabilities = Object.fromEntries(keys.map((k, i) => [k, ps[i]]));
      const top = keys[ps.indexOf(Math.max(...ps))];
      out[qid] = { type: 'choice', choice: top, probabilities, confidence: conf(ps) };
    } else if (q.type === 'score') {
      const ps = softmax(q.criteria.map(() => normal(r) * sharp));
      out[qid] = { type: 'score', score: ps.reduce((a, p, i) => a + i * p, 0), legend: Object.fromEntries(q.criteria.map((l, i) => [String(i), l])), probabilities: Object.fromEntries(ps.map((p, i) => [String(i), p])), confidence: conf(ps) };
    }
  }
  return out;
}

async function systemone(bodyText) {
  let body;
  try { body = JSON.parse(bodyText); } catch { return json(422, { detail: [{ type: 'json_invalid', loc: ['body', 0], msg: 'JSON decode error', input: {} }] }); }
  const qs = body.questions;
  if (!qs || typeof qs !== 'object' || !Object.keys(qs).length) return json(422, { detail: [{ type: 'too_short', loc: ['body', 'questions'], msg: 'Dictionary should have at least 1 item after validation, not 0', input: qs ?? null }] });
  const details = [];
  for (const [qid, q] of Object.entries(qs)) {
    if (!['noul', 'choice', 'score'].includes(q?.type)) return json(400, { detail: { error_type: 'api_usage_error', message: 'Invalid request.' } });
    if (q.type === 'score' && !Array.isArray(q.criteria)) details.push({ type: 'list_type', loc: ['body', 'questions', qid, 'score', 'criteria'], msg: 'Input should be a valid list', input: q.criteria });
    if (q.type === 'choice' && (typeof q.criteria !== 'object' || Array.isArray(q.criteria) || q.criteria === null)) details.push({ type: 'dict_type', loc: ['body', 'questions', qid, 'choice', 'criteria'], msg: 'Input should be a valid dictionary', input: q.criteria });
  }
  if (details.length) return json(422, { detail: details });
  if (!MODELS.some((m) => m.name === body.model) && !['jev-latest', 'jev-preview'].includes(body.model)) return json(400, { detail: { error_type: 'api_usage_error', message: `Unknown model: ${body.model}` } });
  for (const q of Object.values(qs)) {
    if (q.type === 'choice' && !Object.keys(q.criteria).length) return json(400, { detail: 'no options' });
    if (q.type === 'score' && q.criteria.length > 10) return json(400, { detail: 'too many score levels: at most 10' });
  }
  if (body.images?.length && (body.think != null || body.sequential)) return json(400, { detail: 'think and sequential need a text state; this request has images' });
  const nq = Object.keys(qs).length;
  const chars = JSON.stringify(body.state).length + JSON.stringify(qs).length;
  const input = Math.ceil(chars / 3.1) + 38 + nq * 6 + (body.images?.length || 0) * 268;
  const thought = body.think ? Math.min(body.think, 90 + (hash32(chars + '') % 200)) : 0;
  const reads = Math.ceil(nq / 12) * (body.samples || 1);
  const total = 120 + nq * 14 * (body.steps || 1) * (body.sequential ? 1.6 : 1) + reads * 40 + thought * 9 + (hash32(bodyText) % 60);
  await new Promise((res) => setTimeout(res, Math.min(1400, total)));
  const answers = answer(body);
  return json(200, { model: body.model?.startsWith('openjev') ? 'openjev-0.1' : body.model, answers, usage: { input_tokens: input * (body.samples || 1) * (body.think ? 2 : 1), output_tokens: thought } }, {
    'server-timing': `model;dur=0.0, server;dur=${total.toFixed(1)}, total;dur=${total.toFixed(1)}, upstream;dur=${(total + 1.7).toFixed(1)}`,
    'x-ojui-upstream-ms': (total + 1.7).toFixed(1),
  });
}

export function installMockOpenJev() {
  const real = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    const path = new URL(url, location.href).pathname;
    if (init.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    if (path === '/ui/api/config') {
      return json(200, { uiVersion: '0.5.0-dev', openjevUrl: 'http://127.0.0.1:8080', proxyBase: '', authConfigured: false, defaults: { model: 'openjev-latest', chatModel: 'diffusiongemma-26b' },
        limits: { maxImages: 8, maxImageBytes: 5242880, maxQuestions: 256, stepsMax: 8, samplesMax: 32, thinkMax: 4096, chatMaxTokensDefault: 1024, chatMaxTokensCap: 8192, choiceMaxOptions: 255, scoreMaxLevels: 10 },
        hints: { start: 'mise run start', logs: 'mise run logs', status: 'mise run status', stop: 'mise run stop' } });
    }
    if (path === '/ui/api/health') return json(200, { ok: true, checkedAt: Date.now(), upstream: { url: 'http://127.0.0.1:8080', reachable: true, status: 200, latencyMs: 2.1, requestId: 'req_mock', serverTiming: 'model;dur=0.0', error: null, errorType: null }, models: MODELS, authConfigured: false, proxy: { uptimeS: 1, requests: 1 } });
    if (path === '/v1/models') return json(200, { models: MODELS }, { 'server-timing': 'model;dur=0.0, server;dur=0.1, total;dur=0.1, upstream;dur=1.2' });
    if (path === '/v1/systemone' && (init.method || 'GET').toUpperCase() === 'POST') {
      const p = systemone(init.body);
      if (!init.signal) return p;
      return Promise.race([p, new Promise((_, rej) => init.signal.addEventListener('abort', () => rej(new DOMException('Aborted', 'AbortError')), { once: true }))]);
    }
    if (path.startsWith('/v1/')) return json(404, { detail: 'not mocked in the dev harness' });
    return real(input, init);
  };
}

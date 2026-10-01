// core/metrics.js — pure maths over OpenJev answers and timings (builder B).
// Entropy / confidence / JSD, Server-Timing parsing, per-answer stats, token and cost
// estimates, percentiles and a request-body hash. C depends on these exact semantics.

export const IMAGE_TOKENS_EST = 280;

export function parseServerTiming(header) {
  const out = { model: null, server: null, total: null, upstream: null, raw: header ?? null };
  if (!header) return out;
  for (const part of String(header).split(',')) {
    const bits = part.trim().split(';').map((s) => s.trim());
    const name = bits.shift();
    if (!name) continue;
    let dur = null;
    for (const b of bits) {
      const m = /^dur=([-\d.eE+]+)/.exec(b);
      if (m) dur = Number(m[1]);
    }
    if (dur !== null && Number.isFinite(dur)) out[name] = dur;
    else if (!(name in out)) out[name] = null;
  }
  return out;
}

export function entropy(probs) {
  let h = 0;
  for (const p of probs || []) if (p > 0) h -= p * Math.log(p);
  return h;
}

export function entropyBits(probs) {
  return entropy(probs) / Math.LN2;
}

export function normConfidence(probs) {
  const k = (probs || []).length;
  if (k <= 1) return 1;
  return 1 - entropy(probs) / Math.log(k);
}

export function noulProbs(p) {
  const v = Number(p);
  return [v, 1 - v];
}

export function perplexity(probs) {
  return Math.exp(entropy(probs));
}

export function jsd(p, q) {
  const n = Math.max(p?.length || 0, q?.length || 0);
  if (!n) return 0;
  let a = 0, b = 0;
  for (let i = 0; i < n; i++) {
    const pi = p[i] || 0, qi = q[i] || 0;
    const m = (pi + qi) / 2;
    if (pi > 0 && m > 0) a += pi * Math.log2(pi / m);
    if (qi > 0 && m > 0) b += qi * Math.log2(qi / m);
  }
  return Math.max(0, 0.5 * a + 0.5 * b);
}

function topTwo(list) {
  let top = null, second = null;
  for (const it of list) {
    if (!top || it.p > top.p) { second = top; top = it; }
    else if (!second || it.p > second.p) second = it;
  }
  return [top, second];
}

export function answerStats(answer, question) {
  const type = answer?.type || question?.type || 'unknown';
  if (!answer) return { type, probs: [], top: null, topP: null, secondP: null, margin: null, confidence: null, entropyBits: null, perplexity: null, value: null };
  if (type === 'noul') {
    const p = Number(answer.noul);
    const probs = [{ key: 'yes', label: 'yes', p }, { key: 'no', label: 'no', p: 1 - p }];
    const arr = [p, 1 - p];
    const topP = Math.max(p, 1 - p);
    const secondP = Math.min(p, 1 - p);
    return {
      type, probs, top: p >= 0.5 ? 'yes' : 'no', topP, secondP, margin: topP - secondP,
      confidence: normConfidence(arr), entropyBits: entropyBits(arr), perplexity: perplexity(arr), value: p,
    };
  }
  if (type === 'choice') {
    const probs = Object.entries(answer.probabilities || {}).map(([key, p]) => ({ key, label: key, p: Number(p) }));
    const arr = probs.map((x) => x.p);
    const [t, s] = topTwo(probs);
    const top = answer.choice ?? t?.key ?? null;
    const topP = probs.find((x) => x.key === top)?.p ?? t?.p ?? null;
    const secondP = (t && t.key === top ? s?.p : t?.p) ?? 0;
    return {
      type, probs, top, topP, secondP, margin: topP !== null ? topP - secondP : null,
      confidence: answer.confidence ?? normConfidence(arr), entropyBits: entropyBits(arr), perplexity: perplexity(arr),
      value: answer.choice ?? top,
    };
  }
  if (type === 'score') {
    const legend = answer.legend || {};
    const keys = Object.keys(answer.probabilities || {}).sort((a, b) => Number(a) - Number(b));
    const probs = keys.map((key) => {
      const l = legend[key];
      return { key, label: typeof l === 'string' ? l : (l === undefined ? key : JSON.stringify(l)), p: Number(answer.probabilities[key]) };
    });
    const arr = probs.map((x) => x.p);
    const mean = answer.score ?? probs.reduce((acc, x, i) => acc + Number(x.key ?? i) * x.p, 0);
    const variance = probs.reduce((acc, x) => acc + x.p * (Number(x.key) - mean) ** 2, 0);
    const [t, s] = topTwo(probs);
    const mode = t ? Number(t.key) : null;
    return {
      type, probs, top: t?.key ?? null, topP: t?.p ?? null, secondP: s?.p ?? 0,
      margin: t ? t.p - (s?.p ?? 0) : null,
      confidence: answer.confidence ?? normConfidence(arr), entropyBits: entropyBits(arr), perplexity: perplexity(arr),
      value: answer.score ?? mean, mean, std: Math.sqrt(Math.max(0, variance)), mode,
    };
  }
  return { type, probs: [], top: null, topP: null, secondP: null, margin: null, confidence: null, entropyBits: null, perplexity: null, value: null };
}

export function summarizeAnswers(answers, questions) {
  if (!answers || typeof answers !== 'object') return [];
  return Object.keys(answers).map((qid) => {
    const s = answerStats(answers[qid], questions?.[qid]);
    return { qid, type: s.type, top: s.top, topP: s.topP, confidence: s.confidence, entropyBits: s.entropyBits };
  });
}

export function estimateTokens(body) {
  if (!body) return 0;
  let chars = 0;
  try { chars += JSON.stringify(body.state ?? '').length; } catch { /* ignore */ }
  try { chars += JSON.stringify(body.questions ?? {}).length; } catch { /* ignore */ }
  const images = Array.isArray(body.images) ? body.images.length : 0;
  return Math.ceil(chars / 3.6) + IMAGE_TOKENS_EST * images;
}

export function costOf(usage, settings) {
  if (!usage) return 0;
  const inp = Number(usage.input_tokens ?? usage.prompt_tokens ?? usage.inputTokens ?? 0) || 0;
  const out = Number(usage.output_tokens ?? usage.completion_tokens ?? usage.outputTokens ?? 0) || 0;
  const pi = Number(settings?.pricePerMInput) || 0;
  const po = Number(settings?.pricePerMOutput) || 0;
  return (inp / 1e6) * pi + (out / 1e6) * po;
}

/** Chat throughput. The contract's figure is completion / (stream − ttft), but a diffusion model
 *  (and OpenJev on MLX) emits the whole block at once after ttft, so that window can be a few
 *  milliseconds and the rate absurd. When the decode window is under 250 ms or under 10% of the
 *  stream, fall back to end-to-end: completion / stream. → {tps, basis: 'decode'|'e2e'} | null */
export function chatThroughput(completionTokens, ttftMs, streamMs) {
  const n = Number(completionTokens);
  if (!Number.isFinite(n) || n <= 0 || !Number.isFinite(streamMs) || streamMs <= 0) return null;
  const gen = Number.isFinite(ttftMs) ? streamMs - ttftMs : null;
  if (gen !== null && gen >= 250 && gen >= 0.1 * streamMs) return { tps: n / (gen / 1000), basis: 'decode' };
  return { tps: n / (streamMs / 1000), basis: 'e2e' };
}

export function percentile(values, p) {
  const v = (values || []).filter((x) => x !== null && x !== undefined && Number.isFinite(Number(x))).map(Number).sort((a, b) => a - b);
  if (!v.length) return null;
  if (v.length === 1) return v[0];
  const rank = (Math.min(100, Math.max(0, p)) / 100) * (v.length - 1);
  const lo = Math.floor(rank), hi = Math.ceil(rank);
  return v[lo] + (v[hi] - v[lo]) * (rank - lo);
}

function fnv1a(text) {
  let h1 = 0x811c9dc5, h2 = 0x01000193 ^ 0x5bd1e995;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0;
    h2 = Math.imul(h2 ^ c, 0x5bd1e995) >>> 0;
  }
  return (h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0')).slice(0, 16);
}

export async function sha256Hex(text) {
  const s = String(text ?? '');
  try {
    if (globalThis.crypto?.subtle) {
      const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
      return Array.from(new Uint8Array(buf).slice(0, 8), (b) => b.toString(16).padStart(2, '0')).join('');
    }
  } catch { /* fall through */ }
  return fnv1a(s);
}

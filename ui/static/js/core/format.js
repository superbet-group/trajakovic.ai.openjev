// core/format.js — number, time, byte, token and cost formatting, plus uid() (builder B).
// Every formatter accepts null/undefined/NaN and returns an em dash for "unknown".

const DASH = '—';
const bad = (n) => n === null || n === undefined || Number.isNaN(Number(n));

export function uid(prefix = 'id') {
  const r = Math.random().toString(36).slice(2, 8).padEnd(6, '0');
  return `${prefix}_${Date.now().toString(36)}${r}`;
}

export function fmtInt(n) {
  if (bad(n)) return DASH;
  return Math.round(Number(n)).toLocaleString('en-US');
}

export function fmtMs(ms) {
  if (bad(ms)) return DASH;
  const v = Number(ms);
  if (v < 10) return `${v.toFixed(1)} ms`;
  if (v < 1000) return `${Math.round(v)} ms`;
  if (v < 60000) return `${(v / 1000).toFixed(2)} s`;
  const m = Math.floor(v / 60000);
  const s = Math.round((v % 60000) / 1000);
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

export function fmtProb(p, d = 3) {
  if (bad(p)) return DASH;
  return Number(p).toFixed(d);
}

export function fmtPct(p, d = 1) {
  if (bad(p)) return DASH;
  return `${(Number(p) * 100).toFixed(d)}%`;
}

export function fmtBytes(n) {
  if (bad(n)) return DASH;
  const v = Number(n);
  if (v < 1024) return `${Math.round(v)} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  if (v < 1024 ** 3) return `${(v / 1024 / 1024).toFixed(1)} MB`;
  return `${(v / 1024 ** 3).toFixed(2)} GB`;
}

export function fmtCost(amount, currency = '$') {
  if (bad(amount)) return DASH;
  const v = Number(amount);
  if (v === 0) return `${currency}0`;
  const abs = Math.abs(v);
  let digits;
  if (abs >= 1) digits = 2;
  else digits = Math.min(10, 2 - Math.floor(Math.log10(abs)));
  return `${v < 0 ? '-' : ''}${currency}${abs.toFixed(digits)}`;
}

export function fmtRelTime(ts) {
  if (bad(ts)) return DASH;
  const t = Number(ts);
  const now = Date.now();
  const diff = now - t;
  if (diff < 45_000) return 'just now';
  if (diff < 3_600_000) return `${Math.max(1, Math.round(diff / 60_000))} min ago`;
  const d = new Date(t);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  if (t >= today.getTime()) return `${Math.round(diff / 3_600_000)} h ago`;
  const yest = today.getTime() - 86_400_000;
  if (t >= yest) return 'yesterday';
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString('en-US', sameYear ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
}

export function fmtTokens(n) {
  if (bad(n)) return DASH;
  const v = Number(n);
  const abs = Math.abs(v);
  if (abs < 1000) return String(Math.round(v));
  if (abs < 1e6) return `${(v / 1e3).toFixed(abs < 1e4 ? 1 : 0).replace(/\.0$/, '')}k`;
  if (abs < 1e9) return `${(v / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  return `${(v / 1e9).toFixed(1).replace(/\.0$/, '')}B`;
}

// B-private helpers
export function fmtClock(ts) {
  if (bad(ts)) return DASH;
  return new Date(Number(ts)).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function fmtDateTime(ts) {
  if (bad(ts)) return DASH;
  return new Date(Number(ts)).toLocaleString('en-GB', { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function shortId(id, n = 8) {
  if (!id) return DASH;
  const s = String(id);
  return s.length > n + 4 ? `${s.slice(0, n + 4)}…` : s;
}

/** (string|object)[] → {format:'lines'|'blocks'|'jsonl', input, count}: lossless text for the batch textarea.
 *  Any object, or a string with a blank line inside → 'jsonl' (every entry JSON.stringify'd, strings too);
 *  else any string with '\n' → 'blocks' joined by '\n\n'; else 'lines'. Trims, drops empty strings, no dedupe. */
export function statesToBatchInput(states) {
  const list = (Array.isArray(states) ? states : [])
    .map((s) => (typeof s === 'string' ? s.trim() : s))
    .filter((s) => s !== null && s !== undefined && s !== '');
  // same split as parseStates' blocks branch, so a whitespace-only line also counts as blank
  const jsonl = list.some((s) => typeof s !== 'string' || /\n\s*\n/.test(s));
  if (jsonl) return { format: 'jsonl', input: list.map((s) => JSON.stringify(s)).join('\n'), count: list.length };
  if (list.some((s) => s.includes('\n'))) return { format: 'blocks', input: list.join('\n\n'), count: list.length };
  return { format: 'lines', input: list.join('\n'), count: list.length };
}

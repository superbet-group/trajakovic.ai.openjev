// Builder C private helpers: a small DOM builder with explicit property semantics,
// CDN loading with timeout, localStorage wrappers and numeric helpers.
// Only C's modules import this file.

const SVG_NS = 'http://www.w3.org/2000/svg';
const PROPS = new Set(['value', 'checked', 'disabled', 'selected', 'readOnly', 'hidden', 'indeterminate', 'multiple', 'open']);

function apply(node, attrs, isSvg) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false && !PROPS.has(k)) continue;
    if (k === 'class' || k === 'className') {
      const cls = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
      if (cls) node.setAttribute('class', cls);
    } else if (k === 'style') {
      if (typeof v === 'string') node.setAttribute('style', v);
      else for (const [sk, sv] of Object.entries(v)) {
        if (sv === undefined || sv === null) continue;
        if (sk.startsWith('--')) node.style.setProperty(sk, sv); else node.style[sk] = sv;
      }
    } else if (k === 'dataset') {
      for (const [dk, dv] of Object.entries(v)) if (dv !== undefined && dv !== null) node.dataset[dk] = dv;
    } else if (k.startsWith('on') && typeof v === 'function') {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (!isSvg && PROPS.has(k)) {
      node[k] = v;
    } else if (k === 'text') {
      node.textContent = v;
    } else if (k === 'ref' && typeof v === 'function') {
      v(node);
    } else {
      node.setAttribute(k, v === true ? '' : String(v));
    }
  }
}

function append(node, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(node, c);
    else if (c instanceof Node) node.appendChild(c);
    else node.appendChild(document.createTextNode(String(c)));
  }
}

/** HTML element. attrs: class (str|arr), style (obj|str), dataset, on<event>, text, ref, props. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  apply(node, attrs, false);
  append(node, children);
  return node;
}

/** SVG element in the SVG namespace. */
export function s(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  apply(node, attrs, true);
  append(node, children);
  return node;
}

export function empty(node) { while (node && node.firstChild) node.removeChild(node.firstChild); return node; }

export const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
export const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
export const round = (x, d = 3) => (x === null || x === undefined || Number.isNaN(x)) ? null : Math.round(x * 10 ** d) / 10 ** d;
export function trunc(str, n = 60) {
  const t = String(str ?? '');
  return t.length > n ? t.slice(0, Math.max(0, n - 1)) + '…' : t;
}
export function describe(v) {
  // Described = string | object | array | null → a display string
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v); } catch { return String(v); }
}
export function num(n, d = 2) {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return Number(n).toFixed(d);
}
export function mean(xs) { const v = xs.filter(Number.isFinite); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; }
export function std(xs) {
  const m = mean(xs); if (m === null) return null;
  const v = xs.filter(Number.isFinite);
  return Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / v.length);
}
export function sum(xs) { return xs.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0); }

export function loadLS(key, fallback) {
  try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; } catch { return fallback; }
}
export function saveLS(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (e) { console.warn('[ojui] localStorage write failed', key, e); return false; }
}

export function withTimeout(promise, ms, what = 'operation') {
  let t;
  return Promise.race([
    promise.finally(() => clearTimeout(t)),
    new Promise((_, rej) => { t = setTimeout(() => rej(new Error(`${what} timed out after ${ms} ms`)), ms); }),
  ]);
}

const cdnCache = new Map();
/** Dynamic import of a pinned CDN module with a 6 s timeout. Resolves to the module or null. */
export function loadCdn(url) {
  if (!cdnCache.has(url)) {
    cdnCache.set(url, withTimeout(import(/* @vite-ignore */ url), 6000, url).catch((e) => {
      console.warn('[ojui] CDN module unavailable, using fallback:', url, e?.message || e);
      return null;
    }));
  }
  return cdnCache.get(url);
}

const cssLoaded = new Set();
export function loadCss(href) {
  if (cssLoaded.has(href)) return;
  cssLoaded.add(href);
  const link = el('link', { rel: 'stylesheet', href });
  link.addEventListener('error', () => console.warn('[ojui] stylesheet unavailable:', href));
  document.head.appendChild(link);
}

export function cssVar(name, fallback = '') {
  try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback; } catch { return fallback; }
}

export function deepClone(v) {
  if (typeof structuredClone === 'function') { try { return structuredClone(v); } catch { /* fall through */ } }
  return JSON.parse(JSON.stringify(v));
}

/** Numbers equal within eps, recursing into arrays/objects. */
export function deepClose(a, b, eps = 1e-9) {
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) <= eps;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null || typeof a !== 'object') return a === b;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => deepClose(a[k], b[k], eps));
}

/** Parse position/line/col out of a JSON.parse error (V8, SpiderMonkey, JSC). */
export function jsonErrorPos(err, text) {
  const msg = String(err?.message || err);
  let line = null, col = null;
  let m = msg.match(/line (\d+) column (\d+)/i);
  if (m) { line = +m[1]; col = +m[2]; }
  else if ((m = msg.match(/position (\d+)/i))) {
    const pos = +m[1];
    const before = text.slice(0, pos).split('\n');
    line = before.length; col = before[before.length - 1].length + 1;
  } else if (/end of (JSON )?(input|data)/i.test(msg)) {
    const lines = text.split('\n'); line = lines.length; col = lines[lines.length - 1].length + 1;
  }
  const clean = msg.replace(/^JSON\.parse: /, '').replace(/ in JSON at position \d+.*$/, '').replace(/ at line \d+ column \d+ of the JSON data$/, '');
  return { line, col, message: clean };
}

export function uniqueKey(base, taken) {
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}_${i}`)) i++;
  return `${base}_${i}`;
}

/** Hash-route parts: '#/compare/a/b' → ['compare','a','b']. */
export function hashParts() {
  return (location.hash || '').replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
}

export function shuffle(arr, rnd = Math.random) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}

export function mulberry32(seed) {
  let t = seed >>> 0;
  return () => { t += 0x6D2B79F5; let r = Math.imul(t ^ (t >>> 15), 1 | t); r ^= r + Math.imul(r ^ (r >>> 7), 61 | r); return ((r ^ (r >>> 14)) >>> 0) / 4294967296; };
}

export function logit(p) {
  if (p <= 1e-12) return -Infinity;
  if (p >= 1 - 1e-12) return Infinity;
  return Math.log(p / (1 - p));
}

export function sleep(ms, signal) {
  return new Promise((res) => {
    const t = setTimeout(res, ms);
    signal?.addEventListener('abort', () => { clearTimeout(t); res(); }, { once: true });
  });
}

export function debounceLocal(fn, ms) {
  let t = null, lastArgs = null;
  const d = (...args) => { lastArgs = args; clearTimeout(t); t = setTimeout(() => { t = null; fn(...lastArgs); }, ms); };
  d.flush = () => { if (t) { clearTimeout(t); t = null; fn(...lastArgs); } };
  d.cancel = () => { clearTimeout(t); t = null; };
  return d;
}

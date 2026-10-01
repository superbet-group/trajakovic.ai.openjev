// SVG chart primitives for the OpenJev UI (builder C): confColor, tooltips, sparkline,
// histogram, bars, ring, gauge, stackedBar, scatter, reliability and waterfall.
// Everything is hand-written inline SVG coloured only through design-token CSS variables.

import { el, s, clamp, cssVar } from '/js/jev/util.js';

// ---------------------------------------------------------------- colour

let oklchOk = null;
function supportsMix() {
  if (oklchOk === null) {
    try { oklchOk = !!(window.CSS && CSS.supports('color', 'color-mix(in oklch, red 50%, blue)')); } catch { oklchOk = false; }
  }
  return oklchOk;
}

function parseRgb(str) {
  const m = String(str).trim();
  if (m.startsWith('#')) {
    let h = m.slice(1);
    if (h.length === 3) h = h.split('').map((c) => c + c).join('');
    const n = parseInt(h.slice(0, 6), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const r = m.match(/rgba?\(([^)]+)\)/);
  if (r) return r[1].split(/[ ,/]+/).slice(0, 3).map(Number);
  return [128, 128, 128];
}

/** Confidence (0..1) → CSS colour: --err at 0, --warn at 0.5, --ok at 1, interpolated in OKLCH (RGB fallback). */
export function confColor(c) {
  if (c === null || c === undefined || !Number.isFinite(c)) return 'var(--fg-faint)';
  const x = clamp(c, 0, 1);
  const [a, b, t] = x < 0.5 ? ['--err', '--warn', x / 0.5] : ['--warn', '--ok', (x - 0.5) / 0.5];
  if (supportsMix()) return `color-mix(in oklch, var(${b}) ${(t * 100).toFixed(1)}%, var(${a}))`;
  const ca = parseRgb(cssVar(a, '#888')), cb = parseRgb(cssVar(b, '#888'));
  const mix = ca.map((v, i) => Math.round(v + (cb[i] - v) * t));
  return `rgb(${mix.join(',')})`;
}

/** A CSS colour at reduced alpha (tints). */
export function tint(color, alpha = 0.16) {
  if (supportsMix()) return `color-mix(in oklch, ${color} ${(alpha * 100).toFixed(0)}%, transparent)`;
  if (color.startsWith('rgb(')) return color.replace('rgb(', 'rgba(').replace(')', `,${alpha})`);
  return color;
}

export const TYPE_COLOR = { noul: 'var(--type-noul)', choice: 'var(--type-choice)', score: 'var(--type-score)' };
export const SERIES = ['var(--accent)', 'var(--accent-2)', 'var(--type-noul)', 'var(--type-score)', 'var(--warn)', 'var(--ok)', 'var(--err)', 'var(--fg-muted)'];

// ---------------------------------------------------------------- tooltip

let tipEl = null;
function tipNode() {
  if (!tipEl) {
    tipEl = el('div', { class: 'viz-tip', role: 'tooltip' });
    document.body.appendChild(tipEl);
    // mouseleave never fires when the hovered node is re-rendered or the route changes,
    // so hide on anything that can detach it.
    window.addEventListener('hashchange', hideTip);
    window.addEventListener('scroll', hideTip, true);
    window.addEventListener('pointerdown', hideTip, true);
    window.addEventListener('keydown', hideTip, true);
  }
  return tipEl;
}
function moveTip(ev) {
  const t = tipNode();
  const pad = 14, w = t.offsetWidth, hgt = t.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
  if (y + hgt > window.innerHeight - 8) y = ev.clientY - hgt - pad;
  t.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, y)}px)`;
}
export function hideTip() { if (tipEl) tipEl.classList.remove('show'); }

/** Attach a hover tooltip. content: string | Node | () => string|Node. */
export function tip(node, content) {
  if (!content) return node;
  node.addEventListener('mouseenter', (ev) => {
    const t = tipNode();
    const c = typeof content === 'function' ? content() : content;
    t.textContent = '';
    if (c instanceof Node) t.appendChild(c); else t.textContent = String(c);
    t.classList.add('show');
    moveTip(ev);
  });
  node.addEventListener('mousemove', moveTip);
  node.addEventListener('mouseleave', hideTip);
  return node;
}

// ---------------------------------------------------------------- scales & ticks

export function linear(d0, d1, r0, r1) {
  const span = d1 - d0 || 1;
  const f = (v) => r0 + ((v - d0) / span) * (r1 - r0);
  f.domain = [d0, d1];
  return f;
}
export function logScale(d0, d1, r0, r1) {
  const l0 = Math.log10(Math.max(d0, 1e-9)), l1 = Math.log10(Math.max(d1, d0 * 1.0001, 1e-9));
  const f = (v) => r0 + ((Math.log10(Math.max(v, 1e-9)) - l0) / (l1 - l0 || 1)) * (r1 - r0);
  f.domain = [d0, d1];
  return f;
}
export function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  const span = max - min;
  const step0 = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(step0));
  const err = step0 / mag;
  const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(+v.toFixed(12));
  return out;
}
export function logTicks(min, max) {
  const out = [];
  for (let e = Math.floor(Math.log10(Math.max(min, 1e-9))); e <= Math.ceil(Math.log10(Math.max(max, 1e-9))); e++) {
    for (const m of [1, 2, 5]) { const v = m * 10 ** e; if (v >= min * 0.999 && v <= max * 1.001) out.push(v); }
  }
  return out;
}
export function shortNum(v) {
  const a = Math.abs(v);
  if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
  if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k';
  if (a >= 100 || Number.isInteger(v)) return String(Math.round(v));
  if (a >= 1) return v.toFixed(1);
  return v.toFixed(2);
}
export function shortMs(v) {
  if (v >= 60000) return (v / 60000).toFixed(1) + 'm';
  if (v >= 1000) return (v / 1000).toFixed(v >= 10000 ? 0 : 1) + 's';
  if (v >= 1) return Math.round(v) + 'ms';
  return v.toFixed(1) + 'ms';
}

function frame(width, height, cls) {
  return s('svg', { class: ['viz-svg', cls], viewBox: `0 0 ${width} ${height}`, width: '100%', role: 'img', preserveAspectRatio: 'xMidYMid meet' });
}
function axisText(x, y, text, anchor = 'middle', extra = {}) {
  return s('text', { x, y, 'text-anchor': anchor, class: 'viz-axis', ...extra }, text);
}
function emptyChart(width, height, msg = 'no data yet') {
  const g = frame(width, height, 'viz-empty');
  g.appendChild(s('rect', { x: 0.5, y: 0.5, width: width - 1, height: height - 1, rx: 8, class: 'viz-well' }));
  g.appendChild(axisText(width / 2, height / 2 + 4, msg));
  return g;
}

function xAxis(svgEl, x, ticks, y, fmt = shortNum) {
  for (const t of ticks) {
    const px = x(t);
    svgEl.appendChild(s('line', { x1: px, x2: px, y1: y, y2: y + 4, class: 'viz-tick' }));
    svgEl.appendChild(axisText(px, y + 14, fmt(t)));
  }
}
function yAxis(svgEl, y, ticks, x0, x1, fmt = shortNum) {
  for (const t of ticks) {
    const py = y(t);
    svgEl.appendChild(s('line', { x1: x0, x2: x1, y1: py, y2: py, class: 'viz-grid' }));
    svgEl.appendChild(axisText(x0 - 4, py + 3, fmt(t), 'end'));
  }
}

// ---------------------------------------------------------------- sparkline

/** Tiny line chart. values: number[] (nulls skipped). */
export function sparkline(values, { width = 120, height = 28, color = 'var(--accent)', fill = true, min, max, tipFmt, dots = false } = {}) {
  const vals = (values || []).map((v) => (Number.isFinite(v) ? v : null));
  const real = vals.filter((v) => v !== null);
  const g = frame(width, height, 'viz-spark');
  g.setAttribute('width', width); g.setAttribute('height', height);
  if (!real.length) { g.appendChild(s('line', { x1: 0, x2: width, y1: height - 2, y2: height - 2, class: 'viz-grid' })); return g; }
  const lo = min ?? Math.min(...real), hi = max ?? Math.max(...real);
  const x = linear(0, Math.max(1, vals.length - 1), 2, width - 2);
  const y = linear(lo, hi === lo ? lo + 1 : hi, height - 3, 3);
  let d = '', started = false;
  vals.forEach((v, i) => { if (v === null) { started = false; return; } d += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; started = true; });
  if (fill && real.length > 1 && real.length === vals.length) {
    const first = vals.findIndex((v) => v !== null), last = vals.length - 1 - [...vals].reverse().findIndex((v) => v !== null);
    g.appendChild(s('path', { d: `${d}L${x(last).toFixed(1)},${height}L${x(first).toFixed(1)},${height}Z`, style: { fill: color, opacity: 0.14 } }));
  }
  g.appendChild(s('path', { d, class: 'viz-line', style: { stroke: color } }));
  if (dots || real.length === 1) {
    vals.forEach((v, i) => { if (v !== null) g.appendChild(tip(s('circle', { cx: x(i), cy: y(v), r: 2, style: { fill: color } }), tipFmt ? tipFmt(v, i) : null)); });
  } else if (tipFmt) {
    const step = (width - 4) / Math.max(1, vals.length);
    vals.forEach((v, i) => { if (v !== null) g.appendChild(tip(s('rect', { x: x(i) - step / 2, y: 0, width: step, height, class: 'viz-hit' }), tipFmt(v, i))); });
  }
  return g;
}

// ---------------------------------------------------------------- ring / donut

/** Donut showing value in 0..1. */
export function ring(value, { size = 28, stroke = 4, color, label, title } = {}) {
  const v = Number.isFinite(value) ? clamp(value, 0, 1) : 0;
  const col = color || confColor(value);
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  const g = s('svg', { class: 'viz-ring', viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: 'img' });
  g.appendChild(s('circle', { cx: size / 2, cy: size / 2, r, class: 'viz-ring-bg', style: { strokeWidth: stroke } }));
  g.appendChild(s('circle', {
    cx: size / 2, cy: size / 2, r, class: 'viz-ring-fg',
    style: { stroke: col, strokeWidth: stroke, strokeDasharray: `${(v * c).toFixed(2)} ${c.toFixed(2)}` },
    transform: `rotate(-90 ${size / 2} ${size / 2})`,
  }));
  if (label !== undefined) g.appendChild(s('text', { x: size / 2, y: size / 2 + size * 0.11, 'text-anchor': 'middle', class: 'viz-ring-label', style: { fontSize: `${size * 0.3}px` } }, label));
  if (title) g.appendChild(s('title', {}, title));
  return g;
}

// ---------------------------------------------------------------- noul gauge

/** Semicircle 0..1 gauge with a needle at p and a shaded uncertainty band. */
export function gauge(p, { width = 220, band = [0.35, 0.65], color = 'var(--type-noul)' } = {}) {
  const hgt = width * 0.5 + 22, cx = width / 2, cy = width / 2 + 2, r = width / 2 - 18, sw = 12;
  const g = frame(width, hgt, 'viz-gauge');
  const pt = (t, rad = r) => { const a = Math.PI * (1 - t); return [cx + rad * Math.cos(a), cy - rad * Math.sin(a)]; };
  const arc = (t0, t1, rad = r) => { const [x0, y0] = pt(t0, rad), [x1, y1] = pt(t1, rad); return `M${x0},${y0} A${rad},${rad} 0 0 1 ${x1},${y1}`; };
  g.appendChild(s('path', { d: arc(0, 1), class: 'viz-gauge-track', style: { strokeWidth: sw } }));
  g.appendChild(tip(s('path', { d: arc(band[0], band[1]), class: 'viz-gauge-band', style: { strokeWidth: sw } }), `uncertain band ${band[0]}–${band[1]}`));
  const pv = Number.isFinite(p) ? clamp(p, 0, 1) : 0.5;
  if (pv > 0.001) g.appendChild(s('path', { d: arc(0, pv), class: 'viz-gauge-fill', style: { stroke: color, strokeWidth: sw } }));
  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    const [x0, y0] = pt(t, r - sw / 2 - 2), [x1, y1] = pt(t, r - sw / 2 - 7);
    g.appendChild(s('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: 'viz-tick' }));
    if (t === 0 || t === 1) { const [ex] = pt(t, r); g.appendChild(axisText(t === 0 ? ex - sw / 2 : ex + sw / 2, cy + 16, t === 0 ? 'no · 0' : 'yes · 1', t === 0 ? 'start' : 'end')); continue; }
    const [tx, ty] = pt(t, r + sw / 2 + 7);
    g.appendChild(axisText(tx, ty + 3, String(t)));
  }
  const [nx, ny] = pt(pv, r - 4);
  g.appendChild(s('line', { x1: cx, y1: cy, x2: nx, y2: ny, class: 'viz-needle' }));
  g.appendChild(s('circle', { cx, cy, r: 5, class: 'viz-needle-hub' }));
  return g;
}

// ---------------------------------------------------------------- bars (vertical, categorical or time)

/**
 * Vertical bars. items: [{label, value, color?, tip?}]. Options: height, yFmt, labelEvery.
 */
export function bars(items, { width = 520, height = 160, yFmt = shortNum, color = 'var(--accent)', labelEvery, xLabelFmt, yLabel } = {}) {
  if (!items || !items.length) return emptyChart(width, height);
  const ml = 36, mr = 8, mt = 8, mb = 22;
  const g = frame(width, height, 'viz-bars');
  const max = Math.max(1e-9, ...items.map((i) => i.value || 0));
  const ticks = niceTicks(0, max, 4);
  const y = linear(0, Math.max(max, ticks[ticks.length - 1] || max), height - mb, mt);
  yAxis(g, y, ticks, ml, width - mr, yFmt);
  const bw = (width - ml - mr) / items.length;
  const every = labelEvery || Math.max(1, Math.ceil(items.length / Math.floor((width - ml) / 46)));
  items.forEach((it, i) => {
    const x0 = ml + i * bw, v = it.value || 0;
    const top = y(v);
    const dw = Math.min(bw * 0.76, 34);
    const rect = s('rect', { x: x0 + (bw - dw) / 2, y: top, width: Math.max(1, dw), height: Math.max(0, height - mb - top), rx: Math.min(3, bw / 4), class: 'viz-bar', style: { fill: it.color || color } });
    tip(rect, it.tip || `${it.label}: ${yFmt(v)}`);
    g.appendChild(rect);
    if (i % every === 0) g.appendChild(axisText(x0 + bw / 2, height - 7, xLabelFmt ? xLabelFmt(it.label, i) : it.label));
  });
  if (yLabel) g.appendChild(axisText(4, mt + 2, yLabel, 'start', { class: 'viz-axis viz-axis-title' }));
  return g;
}

/**
 * Stacked vertical bars. rows: [{label, parts: {seriesKey: value}}], series: [{key, label, color}].
 */
export function stackedBars(rows, series, { width = 520, height = 160, yFmt = shortNum, xLabelFmt } = {}) {
  if (!rows || !rows.length) return emptyChart(width, height);
  const ml = 36, mr = 8, mt = 8, mb = 22;
  const g = frame(width, height, 'viz-bars');
  const totals = rows.map((r) => series.reduce((a, sr) => a + (r.parts[sr.key] || 0), 0));
  const max = Math.max(1e-9, ...totals);
  const ticks = niceTicks(0, max, 4);
  const y = linear(0, Math.max(max, ticks[ticks.length - 1] || max), height - mb, mt);
  yAxis(g, y, ticks, ml, width - mr, yFmt);
  const bw = (width - ml - mr) / rows.length;
  const every = Math.max(1, Math.ceil(rows.length / Math.floor((width - ml) / 46)));
  rows.forEach((r, i) => {
    let acc = 0;
    const x0 = ml + i * bw;
    for (const sr of series) {
      const v = r.parts[sr.key] || 0;
      if (v <= 0) continue;
      const y0 = y(acc), y1 = y(acc + v);
      const dw = Math.min(bw * 0.76, 34);
      g.appendChild(tip(s('rect', { x: x0 + (bw - dw) / 2, y: y1, width: Math.max(1, dw), height: Math.max(0, y0 - y1), class: 'viz-bar', style: { fill: sr.color } }),
        `${r.label} · ${sr.label}: ${yFmt(v)}`));
      acc += v;
    }
    if (i % every === 0) g.appendChild(axisText(x0 + bw / 2, height - 7, xLabelFmt ? xLabelFmt(r.label, i) : r.label));
  });
  return g;
}

// ---------------------------------------------------------------- histogram

/**
 * Histogram. values: number[] or {series: {key: number[]}}. Options: bins, min, max, logX, colors, xFmt.
 * Series are stacked in each bin.
 */
export function histogram(input, { width = 520, height = 160, bins = 20, min, max, logX = false, xFmt = shortNum, colors = {}, labels = {} } = {}) {
  const seriesMap = Array.isArray(input) ? { all: input } : (input?.series || {});
  const keys = Object.keys(seriesMap);
  const all = keys.flatMap((k) => seriesMap[k].filter(Number.isFinite));
  if (!all.length) return emptyChart(width, height);
  let lo = min ?? Math.min(...all), hi = max ?? Math.max(...all);
  if (logX) { lo = Math.max(lo, 0.1); hi = Math.max(hi, lo * 1.5); }
  if (hi === lo) hi = lo + 1;
  const edges = [];
  for (let i = 0; i <= bins; i++) edges.push(logX ? 10 ** (Math.log10(lo) + (Math.log10(hi) - Math.log10(lo)) * (i / bins)) : lo + (hi - lo) * (i / bins));
  const counts = keys.map(() => new Array(bins).fill(0));
  keys.forEach((k, ki) => {
    for (const v of seriesMap[k]) {
      if (!Number.isFinite(v)) continue;
      let idx;
      if (logX) idx = Math.floor(((Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * bins);
      else idx = Math.floor(((v - lo) / (hi - lo)) * bins);
      counts[ki][clamp(idx, 0, bins - 1)]++;
    }
  });
  const ml = 30, mr = 8, mt = 8, mb = 22;
  const g = frame(width, height, 'viz-hist');
  const totals = edges.slice(0, -1).map((_, i) => keys.reduce((a, _k, ki) => a + counts[ki][i], 0));
  const cmax = Math.max(1, ...totals);
  const yt = niceTicks(0, cmax, 3).filter((t) => Number.isInteger(t));
  const y = linear(0, Math.max(cmax, yt[yt.length - 1] || 1), height - mb, mt);
  yAxis(g, y, yt, ml, width - mr, (v) => String(v));
  const x = logX ? logScale(lo, hi, ml, width - mr) : linear(lo, hi, ml, width - mr);
  for (let i = 0; i < bins; i++) {
    let acc = 0;
    keys.forEach((k, ki) => {
      const c = counts[ki][i];
      if (!c) return;
      const x0 = x(edges[i]), x1 = x(edges[i + 1]);
      const y0 = y(acc), y1 = y(acc + c);
      g.appendChild(tip(s('rect', { x: x0 + 0.5, y: y1, width: Math.max(0.5, x1 - x0 - 1), height: Math.max(0, y0 - y1), class: 'viz-bar', style: { fill: colors[k] || SERIES[ki % SERIES.length] } }),
        `${labels[k] || k}: ${c} in [${xFmt(edges[i])}, ${xFmt(edges[i + 1])})`));
      acc += c;
    });
  }
  const ticks = logX ? logTicks(lo, hi) : niceTicks(lo, hi, 5);
  const every = Math.max(1, Math.ceil(ticks.length / 8));
  xAxis(g, x, ticks.filter((_, i) => i % every === 0), height - mb, xFmt);
  g.appendChild(s('line', { x1: ml, x2: width - mr, y1: height - mb, y2: height - mb, class: 'viz-axis-line' }));
  return g;
}

// ---------------------------------------------------------------- horizontal stacked bar

/** One horizontal stacked bar. segments: [{label, value, color, hatched?}]. */
export function stackedBar(segments, { width = 360, height = 14, total, fmt = shortNum, rounded = true } = {}) {
  const g = frame(width, height, 'viz-stack');
  const tot = total ?? segments.reduce((a, sg) => a + Math.max(0, sg.value || 0), 0);
  g.appendChild(s('rect', { x: 0, y: 0, width, height, rx: rounded ? height / 2 : 2, class: 'viz-well' }));
  if (!tot) return g;
  let acc = 0;
  const clipId = `c${Math.random().toString(36).slice(2, 8)}`;
  g.appendChild(s('defs', {}, s('clipPath', { id: clipId }, s('rect', { x: 0, y: 0, width, height, rx: rounded ? height / 2 : 2 }))));
  const grp = s('g', { 'clip-path': `url(#${clipId})` });
  for (const sg of segments) {
    const v = Math.max(0, sg.value || 0);
    if (!v) continue;
    const w = (v / tot) * width;
    grp.appendChild(tip(s('rect', { x: acc, y: 0, width: Math.max(0.5, w), height, class: sg.hatched ? 'viz-hatch' : 'viz-bar', style: sg.hatched ? {} : { fill: sg.color } }),
      `${sg.label}: ${fmt(v)} (${((v / tot) * 100).toFixed(1)}%)`));
    acc += w;
  }
  g.appendChild(grp);
  return g;
}

// ---------------------------------------------------------------- scatter

/**
 * Scatter plot. points: [{x, y, color?, tip?, r?}]. lines: [{points:[{x,y}], color, dash?, label?}].
 * Options: xFmt, yFmt, logY, xTime (format x as clock time), xLabel, yLabel.
 */
export function scatter(points, { width = 520, height = 180, xFmt = shortNum, yFmt = shortNum, logY = false, lines = [], xLabel, yLabel, xMin, xMax, yMin, yMax } = {}) {
  const pts = (points || []).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
  if (!pts.length) return emptyChart(width, height);
  const ml = 40, mr = 10, mt = 10, mb = 24;
  const g = frame(width, height, 'viz-scatter');
  const allY = pts.map((p) => p.y).concat(lines.flatMap((l) => l.points.map((p) => p.y))).filter(Number.isFinite);
  let x0 = xMin ?? Math.min(...pts.map((p) => p.x)), x1 = xMax ?? Math.max(...pts.map((p) => p.x));
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  let y0 = yMin ?? (logY ? Math.max(0.1, Math.min(...allY)) : Math.min(0, ...allY)), y1 = yMax ?? Math.max(...allY);
  if (y0 === y1) y1 = y0 + 1;
  const x = linear(x0, x1, ml, width - mr);
  const y = logY ? logScale(y0, y1 * 1.1, height - mb, mt) : linear(y0, y1 * 1.05, height - mb, mt);
  const yt = logY ? logTicks(y0, y1) : niceTicks(y0, y1, 4);
  yAxis(g, y, yt.filter((_, i, a) => a.length < 7 || i % 2 === 0), ml, width - mr, yFmt);
  const allInt = pts.every((p) => Number.isInteger(p.x));
  xAxis(g, x, niceTicks(x0, x1, 5).filter((t) => t >= x0 && t <= x1 && (!allInt || Number.isInteger(t))), height - mb, xFmt);
  g.appendChild(s('line', { x1: ml, x2: width - mr, y1: height - mb, y2: height - mb, class: 'viz-axis-line' }));
  for (const p of pts) {
    g.appendChild(tip(s('circle', { cx: x(p.x), cy: y(p.y), r: p.r || 3, class: 'viz-dot', style: { fill: p.color || 'var(--accent)' } }), p.tip));
  }
  for (const l of lines) {
    const lp = l.points.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
    if (lp.length < 2) continue;
    const d = lp.map((p, i) => `${i ? 'L' : 'M'}${x(p.x).toFixed(1)},${y(clamp(p.y, y0, y1 * 1.1)).toFixed(1)}`).join('');
    g.appendChild(s('path', { d, class: 'viz-line', style: { stroke: l.color || 'var(--fg-muted)', strokeDasharray: l.dash || null } }));
    if (l.label) { const lastP = lp[lp.length - 1]; g.appendChild(axisText(x(lastP.x) - 2, y(clamp(lastP.y, y0, y1)) - 5, l.label, 'end', { class: 'viz-axis viz-line-label' })); }
  }
  if (xLabel) g.appendChild(axisText(width - mr, height - mb - 4, xLabel, 'end', { class: 'viz-axis viz-axis-title' }));
  if (yLabel) g.appendChild(axisText(ml + 4, mt + 8, yLabel, 'start', { class: 'viz-axis viz-axis-title' }));
  return g;
}

// ---------------------------------------------------------------- reliability diagram

/** bins: [{lo, hi, conf, acc, count}] with acc = fraction correct. Draws the diagonal. */
export function reliability(bins, { width = 320, height = 240, lo = 0.5, hi = 1 } = {}) {
  const ml = 34, mr = 10, mt = 10, mb = 36;
  const g = frame(width, height, 'viz-rel');
  const x = linear(lo, hi, ml, width - mr), y = linear(0, 1, height - mb, mt);
  yAxis(g, y, [0, 0.25, 0.5, 0.75, 1], ml, width - mr, (v) => v.toFixed(2));
  xAxis(g, x, niceTicks(lo, hi, 5), height - mb, (v) => v.toFixed(1));
  g.appendChild(s('line', { x1: x(lo), y1: y(lo), x2: x(hi), y2: y(hi), class: 'viz-diag' }));
  for (const b of bins) {
    const x0 = x(b.lo), x1 = x(b.hi);
    if (b.count) {
      const top = y(b.acc);
      g.appendChild(tip(s('rect', { x: x0 + 1, y: top, width: Math.max(1, x1 - x0 - 2), height: Math.max(0, y(0) - top), class: 'viz-bar', style: { fill: confColor(1 - Math.abs(b.acc - b.conf) * 2), opacity: 0.8 } }),
        `top-p ${b.lo.toFixed(2)}–${b.hi.toFixed(2)} · accuracy ${(b.acc * 100).toFixed(0)}% · mean conf ${b.conf.toFixed(3)} · n=${b.count}`));
      g.appendChild(s('circle', { cx: x(b.conf), cy: y(b.conf), r: 2, class: 'viz-gap-dot' }));
    }
    g.appendChild(axisText((x0 + x1) / 2, height - 6, b.count ? String(b.count) : '', 'middle', { class: 'viz-axis viz-count' }));
  }
  g.appendChild(axisText(width - mr, height - mb - 4, 'top p', 'end', { class: 'viz-axis viz-axis-title' }));
  g.appendChild(axisText(ml + 4, mt + 8, 'accuracy', 'start', { class: 'viz-axis viz-axis-title' }));
  return g;
}

// ---------------------------------------------------------------- waterfall

/**
 * Timing waterfall on one axis. segments: [{label, start, dur, color, hatched?, note?}] in ms.
 */
export function waterfall(segments, { width = 480, rowH = 18, labelW = 118, total } = {}) {
  const segs = segments.filter((sg) => Number.isFinite(sg.dur) && sg.dur >= 0);
  const height = segs.length * (rowH + 6) + 24;
  const g = frame(width, height, 'viz-waterfall');
  const end = total ?? Math.max(1, ...segs.map((sg) => sg.start + sg.dur));
  const x = linear(0, end, labelW, width - 10);
  segs.forEach((sg, i) => {
    const yy = 4 + i * (rowH + 6);
    g.appendChild(axisText(labelW - 6, yy + rowH / 2 + 4, sg.label, 'end', { class: 'viz-axis viz-wf-label' }));
    g.appendChild(s('rect', { x: labelW, y: yy, width: width - 10 - labelW, height: rowH, rx: 3, class: 'viz-well' }));
    const w = Math.max(sg.hatched ? 24 : 1.5, x(sg.start + sg.dur) - x(sg.start));
    g.appendChild(tip(s('rect', { x: x(sg.start), y: yy, width: w, height: rowH, rx: 3, class: sg.hatched ? 'viz-hatch' : 'viz-bar', style: sg.hatched ? {} : { fill: sg.color || 'var(--accent)' } }),
      `${sg.label}: ${sg.note || shortMs(sg.dur)}${sg.hatched ? '' : ` (from ${shortMs(sg.start)})`}`));
    g.appendChild(axisText(Math.min(width - 12, x(sg.start) + w + 4), yy + rowH / 2 + 4, sg.note || shortMs(sg.dur), x(sg.start) + w + 60 > width ? 'end' : 'start', { class: 'viz-axis viz-wf-val' }));
  });
  const ticks = niceTicks(0, end, 5);
  xAxis(g, x, ticks, height - 20, shortMs);
  return g;
}

/**
 * Timing segments from HttpInfo: browser↔proxy, proxy↔OpenJev, server, model.
 * Returns {segments, modelNA}.
 */
export function timingSegments(http) {
  const st = http?.serverTiming || {};
  const client = http?.clientMs;
  const up = st.upstream, total = st.total, server = st.server, model = st.model;
  const modelNA = !Number.isFinite(model) || (model === 0 && (total || 0) > 5);
  const segs = [];
  if (Number.isFinite(client)) {
    const upv = Number.isFinite(up) ? up : Number.isFinite(total) ? total : client;
    const outer = Math.max(0, client - upv);
    segs.push({ label: 'browser↔proxy', start: 0, dur: outer, color: 'var(--fg-muted)', note: `${shortMs(outer)} (client − upstream)` });
    let t = outer / 2;
    if (Number.isFinite(up) && Number.isFinite(total)) {
      const pr = Math.max(0, up - total);
      segs.push({ label: 'proxy↔OpenJev', start: t, dur: pr, color: 'var(--accent-2)', note: `${shortMs(pr)} (upstream − total)` });
      t += pr / 2;
    }
    if (Number.isFinite(total)) {
      if (Number.isFinite(server)) segs.push({ label: 'server', start: t, dur: server, color: 'var(--accent)' });
      if (modelNA) segs.push({ label: 'model', start: t, dur: 0, hatched: true, note: 'not reported (MLX)' });
      else segs.push({ label: 'model', start: t + (server || 0), dur: model, color: 'var(--type-score)' });
    }
  }
  return { segments: segs, modelNA, total: client };
}

/** A tiny legend row. items: [{label, color}] */
export function legend(items) {
  return el('div', { class: 'viz-legend' }, items.map((it) => el('span', { class: 'viz-legend-item' },
    el('i', { class: it.hatched ? 'viz-legend-sw hatched' : 'viz-legend-sw', style: it.hatched ? {} : { background: it.color } }), it.label)));
}

/** Small horizontal meter (HTML) for table cells. */
export function miniBar(value, { color, max = 1, width = 60 } = {}) {
  const v = Number.isFinite(value) ? clamp(value / max, 0, 1) : 0;
  return el('span', { class: 'viz-minibar', style: { width: `${width}px` } },
    el('i', { style: { width: `${(v * 100).toFixed(1)}%`, background: color || confColor(value) } }));
}

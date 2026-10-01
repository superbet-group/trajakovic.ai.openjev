// core/dom.js — DOM helpers (builder B): h()/svg() element builders, clipboard,
// downloads, escaping, debounce, plus a small anchored popover used by chips and menus.
// User strings always go in through textContent (h() children), never innerHTML.

import { toast } from '/js/core/toast.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const PROPS = new Set(['value', 'checked', 'selected', 'indeterminate', 'muted']);

function applyAttrs(el, attrs, isSvg) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class' || k === 'className') {
      const cls = Array.isArray(v) ? v.filter(Boolean).join(' ') : String(v);
      if (cls) el.setAttribute('class', cls);
    } else if (k === 'style') {
      if (typeof v === 'string') el.setAttribute('style', v);
      else for (const [sk, sv] of Object.entries(v)) {
        if (sv === undefined || sv === null) continue;
        if (sk.startsWith('--')) el.style.setProperty(sk, sv);
        else el.style[sk] = sv;
      }
    } else if (k === 'dataset') {
      for (const [dk, dv] of Object.entries(v)) if (dv !== undefined && dv !== null) el.dataset[dk] = dv;
    } else if (k.startsWith('on') && typeof v === 'function') {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (!isSvg && PROPS.has(k)) {
      el[k] = v;
    } else if (k === 'ref' && typeof v === 'function') {
      v(el);
    } else {
      el.setAttribute(k, v === true ? '' : String(v));
    }
  }
}

function appendChildren(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false || c === true) continue;
    if (Array.isArray(c)) appendChildren(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

// B-private: append children with h()'s rules (null/false skipped, arrays flattened)
export function append(el, ...children) {
  appendChildren(el, children);
  return el;
}

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  let props = null;
  if (attrs) {
    for (const k of PROPS) if (k in attrs) { (props ||= {})[k] = attrs[k]; }
    if (props) { attrs = { ...attrs }; for (const k in props) delete attrs[k]; }
  }
  applyAttrs(el, attrs, false);
  appendChildren(el, children);
  // properties last, so a <select>'s value can pick one of its (just appended) options
  if (props) for (const [k, v] of Object.entries(props)) if (v !== undefined && v !== null) el[k] = v;
  return el;
}

export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  applyAttrs(el, attrs, true);
  appendChildren(el, children);
  return el;
}

export function clear(el) {
  if (!el) return el;
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export async function copyText(text) {
  const s = String(text ?? '');
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(s);
      ok = true;
    }
  } catch { ok = false; }
  if (!ok) {
    try {
      const ta = h('textarea', { style: 'position:fixed;left:-9999px;top:0;opacity:0' });
      ta.value = s;
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand('copy');
      ta.remove();
    } catch { ok = false; }
  }
  toast(ok ? 'Copied' : 'Copy failed', { kind: ok ? 'ok' : 'err', timeout: 1400 });
  return ok;
}

export function download(filename, data, mime = 'application/json') {
  let blob;
  if (data instanceof Blob) blob = data;
  else if (typeof data === 'string') blob = new Blob([data], { type: mime });
  else blob = new Blob([JSON.stringify(data, null, 2)], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: filename, style: 'display:none' });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
}

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function debounce(fn, ms) {
  let t = null;
  const d = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => { t = null; fn(...args); }, ms);
  };
  d.flush = (...args) => { clearTimeout(t); t = null; fn(...args); };
  d.cancel = () => { clearTimeout(t); t = null; };
  return d;
}

// ---- popover (B-private addition) -------------------------------------------------
// popover(anchor, content, {align:'start'|'end', placement:'below'|'above', className})
// → {el, close}. Closes on outside click, Esc, window resize, or when another opens.
let openPop = null;

export function popover(anchor, content, { align = 'start', placement = 'below', className = '', onClose } = {}) {
  if (openPop) openPop.close();
  const el = h('div', { class: ['popover', className], role: 'dialog' }, content);
  document.body.appendChild(el);
  const place = () => {
    const r = anchor.getBoundingClientRect();
    const pw = el.offsetWidth, ph = el.offsetHeight;
    let left = align === 'end' ? r.right - pw : r.left;
    left = Math.max(8, Math.min(left, window.innerWidth - pw - 8));
    let top = placement === 'above' ? r.top - ph - 6 : r.bottom + 6;
    if (placement === 'below' && top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 6);
    if (placement === 'above' && top < 8) top = r.bottom + 6;
    el.style.left = `${Math.round(left)}px`;
    el.style.top = `${Math.round(top)}px`;
  };
  place();
  const onDown = (ev) => { if (!el.contains(ev.target) && !anchor.contains(ev.target)) close(); };
  const onKey = (ev) => { if (ev.key === 'Escape') { ev.stopPropagation(); ev.preventDefault(); close(); } };
  const onResize = () => close();
  setTimeout(() => document.addEventListener('mousedown', onDown, true), 0);
  window.addEventListener('keydown', onKey, true);
  window.addEventListener('resize', onResize);
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    document.removeEventListener('mousedown', onDown, true);
    window.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', onResize);
    el.remove();
    if (openPop && openPop.el === el) openPop = null;
    onClose?.();
  }
  openPop = { el, close, reposition: place };
  return openPop;
}

// A menu inside a popover: items [{label, icon?: Node, danger?, onClick, disabled?} | '-']
export function menuList(items, close) {
  return h('div', { class: 'menu', role: 'menu' },
    items.map((it) => it === '-' ? h('div', { class: 'menu-sep' }) :
      h('button', {
        class: ['menu-item', it.danger && 'danger', it.active && 'active'], role: 'menuitem', disabled: it.disabled,
        onClick: (ev) => { ev.stopPropagation(); close?.(); it.onClick?.(ev); },
      }, it.icon || null, h('span', {}, it.label), it.hint ? h('span', { class: 'menu-hint faint mono' }, it.hint) : null)));
}

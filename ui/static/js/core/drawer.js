// core/drawer.js — the right-side drawer #drawer (builder B).
// One drawer at a time; render(el) may return a cleanup. Width is resizable by dragging
// the left edge and persisted in ojui.layout.v1.drawerWidth. Esc handling lives in the shell.

import { h, clear } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { getLayout, setLayout } from '/js/core/store.js';

let cleanup = null;

function el() { return document.getElementById('drawer'); }

export function isDrawerOpen() {
  const d = el();
  return !!d && !d.hidden;
}

function applyWidth(w) {
  const width = Math.max(360, Math.min(Math.round(w), Math.floor(window.innerWidth * 0.9)));
  document.documentElement.style.setProperty('--drawer-w', `${width}px`);
  return width;
}

export function openDrawer({ title, render } = {}) {
  closeDrawer();
  const d = el();
  if (!d) return;
  applyWidth(getLayout().drawerWidth || 560);
  const body = h('div', { class: 'drawer-body' });
  const grip = h('div', { class: 'drawer-grip', title: 'Drag to resize' });
  grip.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    grip.setPointerCapture(ev.pointerId);
    document.body.classList.add('resizing');
    const move = (e) => applyWidth(window.innerWidth - e.clientX);
    const up = (e) => {
      grip.releasePointerCapture(ev.pointerId);
      grip.removeEventListener('pointermove', move);
      grip.removeEventListener('pointerup', up);
      document.body.classList.remove('resizing');
      setLayout({ drawerWidth: applyWidth(window.innerWidth - e.clientX) });
    };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', up);
  });
  d.append(
    grip,
    h('div', { class: 'drawer-head' },
      h('div', { class: 'drawer-title' }, title || ''),
      h('button', { class: 'icon-btn', title: 'Close (Esc)', 'aria-label': 'Close drawer', onClick: () => closeDrawer() }, icon('x'))),
    body);
  d.hidden = false;
  document.getElementById('app')?.classList.add('drawer-open');
  try {
    const c = render?.(body);
    cleanup = typeof c === 'function' ? c : null;
  } catch (err) {
    console.warn('[drawer] render failed', err);
    body.appendChild(h('pre', { class: 'mono' }, String(err?.stack || err)));
  }
}

export function closeDrawer() {
  const d = el();
  if (!d) return;
  try { cleanup?.(); } catch (err) { console.warn('[drawer] cleanup failed', err); }
  cleanup = null;
  clear(d);
  d.hidden = true;
  document.getElementById('app')?.classList.remove('drawer-open');
}

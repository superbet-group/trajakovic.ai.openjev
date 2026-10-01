// core/toast.js — transient notifications in #toast-root (builder B).
// Also listens for `oj:toast` on the bus so any module can toast without importing this.

import { on } from '/js/core/bus.js';

export function toast(message, { kind = 'info', timeout = 2500 } = {}) {
  const root = document.getElementById('toast-root');
  if (!root) return;
  const el = document.createElement('div');
  el.className = `toast toast-${kind}`;
  el.setAttribute('role', kind === 'err' ? 'alert' : 'status');
  const dot = document.createElement('span');
  dot.className = 'toast-dot';
  const txt = document.createElement('span');
  txt.textContent = String(message ?? '');
  el.append(dot, txt);
  el.addEventListener('click', () => dismiss());
  root.appendChild(el);
  while (root.children.length > 5) root.firstElementChild.remove();
  requestAnimationFrame(() => el.classList.add('show'));
  let gone = false;
  function dismiss() {
    if (gone) return;
    gone = true;
    el.classList.remove('show');
    setTimeout(() => el.remove(), 200);
  }
  if (timeout > 0) setTimeout(dismiss, timeout);
  return dismiss;
}

on('oj:toast', (d) => {
  if (d && d.message) toast(d.message, { kind: d.kind || 'info', timeout: d.timeout ?? 2500 });
});

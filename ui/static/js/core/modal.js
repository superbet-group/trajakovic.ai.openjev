// core/modal.js — modal dialogs in #modal-root, plus confirm/prompt helpers (builder B).
// Modals stack; Esc and a backdrop click close the top one. Focus returns to the opener.

import { h } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';

const stack = [];

window.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && stack.length) {
    ev.preventDefault();
    ev.stopPropagation();
    stack[stack.length - 1].close();
  }
}, true);

export function isModalOpen() { return stack.length > 0; }
export function closeTopModal() { stack[stack.length - 1]?.close(); }

export function openModal({ title, body, actions = [], wide = false, onClose, className = '' } = {}) {
  const root = document.getElementById('modal-root') || document.body;
  const opener = document.activeElement;
  let closed = false;
  const handle = { close };
  const footer = actions.length ? h('div', { class: 'modal-foot' },
    actions.map((a) => h('button', {
      class: ['btn', a.kind === 'primary' && 'primary', a.kind === 'danger' && 'danger', a.kind === 'ghost' && 'ghost'],
      onClick: async (ev) => {
        const r = await a.onClick?.(ev, handle);
        if (r !== false) close();
      },
    }, a.label))) : null;
  const dlg = h('div', { class: ['modal', wide && 'wide', className], role: 'dialog', 'aria-modal': 'true', 'aria-label': typeof title === 'string' ? title : 'Dialog' },
    h('div', { class: 'modal-head' },
      h('div', { class: 'modal-title' }, title || ''),
      h('button', { class: 'icon-btn', title: 'Close (Esc)', 'aria-label': 'Close', onClick: () => close() }, icon('x'))),
    h('div', { class: 'modal-body' }, body || null),
    footer);
  const backdrop = h('div', { class: 'modal-backdrop' }, dlg);
  backdrop.addEventListener('mousedown', (ev) => { if (ev.target === backdrop) close(); });
  root.appendChild(backdrop);
  requestAnimationFrame(() => backdrop.classList.add('show'));
  const entry = { close };
  stack.push(entry);
  setTimeout(() => {
    const f = dlg.querySelector('[autofocus], input:not([type=checkbox]):not([disabled]), textarea, select, .btn.primary');
    (f || dlg.querySelector('button'))?.focus();
  }, 30);

  function close() {
    if (closed) return;
    closed = true;
    const i = stack.indexOf(entry);
    if (i >= 0) stack.splice(i, 1);
    backdrop.remove();
    try { onClose?.(); } catch (err) { console.warn('[modal] onClose failed', err); }
    if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
  }
  return handle;
}

export async function confirmDialog(message, { danger = false, okLabel, title } = {}) {
  return new Promise((resolve) => {
    let result = false;
    openModal({
      title: title || (danger ? 'Are you sure?' : 'Confirm'),
      body: h('p', { class: 'modal-msg' }, message),
      actions: [
        { label: 'Cancel', kind: 'ghost' },
        { label: okLabel || (danger ? 'Delete' : 'OK'), kind: danger ? 'danger' : 'primary', onClick: () => { result = true; } },
      ],
      onClose: () => resolve(result),
    });
  });
}

export async function promptDialog(message, defaultValue = '', { title, multiline = false, placeholder = '' } = {}) {
  return new Promise((resolve) => {
    let result = null;
    const input = multiline
      ? h('textarea', { class: 'textarea', rows: 8, value: defaultValue ?? '', placeholder })
      : h('input', { class: 'input', type: 'text', value: defaultValue ?? '', placeholder });
    const m = openModal({
      title: title || 'Input',
      body: h('div', { class: 'col' }, message ? h('label', { class: 'modal-msg' }, message) : null, input),
      actions: [
        { label: 'Cancel', kind: 'ghost' },
        { label: 'OK', kind: 'primary', onClick: () => { result = input.value; } },
      ],
      onClose: () => resolve(result),
    });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && (!multiline || ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        result = input.value;
        m.close();
      }
    });
    setTimeout(() => { input.focus(); if (!multiline) input.select(); }, 40);
  });
}

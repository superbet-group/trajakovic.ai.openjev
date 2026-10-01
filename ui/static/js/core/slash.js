// core/slash.js — slash-command registry and the popup menu (builder B).
// The menu opens while a textarea's content starts with "/" and its first token is being
// typed; ↑/↓ move, Enter/Tab pick, Esc closes. A complete "/cmd args" line runs on Enter.

import { h, clear } from '/js/core/dom.js';
import { toast } from '/js/core/toast.js';

const cmds = new Map();

export function registerSlash({ name, args = '', description = '', run }) {
  const n = String(name || '').replace(/^\//, '').trim();
  if (!n || typeof run !== 'function') return;
  cmds.set(n, { name: n, args, description, run });
}

export function listSlash() {
  return [...cmds.values()].sort((a, b) => a.name.localeCompare(b.name));
}

// B-private: exact match of the first token against a registered command
export function matchSlash(text) {
  const s = String(text || '').trim();
  if (!s.startsWith('/')) return null;
  const m = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(s);
  if (!m) return null;
  const cmd = cmds.get(m[1]);
  return cmd ? { cmd, argString: (m[2] || '').trim() } : null;
}

export async function runSlash(text, ctx) {
  const m = matchSlash(text);
  if (!m) return false;
  try {
    await m.cmd.run(m.argString, ctx);
  } catch (err) {
    console.warn(`[slash] /${m.cmd.name} failed`, err);
    toast(`/${m.cmd.name}: ${err?.message || err}`, { kind: 'err', timeout: 4000 });
  }
  return true;
}

// B-private: attach the popup to a textarea. host is a positioned container.
export function attachSlashMenu(textarea, { host, getCtx, onRan } = {}) {
  const menu = h('div', { class: 'slash-menu', role: 'listbox', hidden: true });
  (host || textarea.parentElement).appendChild(menu);
  let items = [];
  let sel = 0;
  let open = false;

  function filter() {
    const v = textarea.value;
    if (!v.startsWith('/') || /\s/.test(v)) return null;
    const prefix = v.slice(1).toLowerCase();
    return listSlash().filter((c) => c.name.toLowerCase().startsWith(prefix));
  }

  function paint() {
    clear(menu);
    items.forEach((c, i) => {
      menu.appendChild(h('div', {
        class: ['slash-item', i === sel && 'active'], role: 'option', 'aria-selected': i === sel ? 'true' : 'false',
        onMousedown: (ev) => { ev.preventDefault(); sel = i; pick(false); },
        onMouseenter: () => { sel = i; paint(); },
      },
      h('span', { class: 'slash-name mono' }, `/${c.name}`),
      c.args ? h('span', { class: 'slash-args mono faint' }, c.args) : null,
      h('span', { class: 'slash-desc muted' }, c.description)));
    });
    const active = menu.children[sel];
    active?.scrollIntoView?.({ block: 'nearest' });
  }

  function update() {
    const f = filter();
    if (!f || !f.length) { hide(); return; }
    items = f;
    sel = Math.min(sel, items.length - 1);
    open = true;
    menu.hidden = false;
    paint();
  }

  function hide() {
    open = false;
    menu.hidden = true;
    sel = 0;
  }

  async function pick(viaTab) {
    const c = items[sel];
    if (!c) return;
    hide();
    if (c.args || viaTab) {
      textarea.value = `/${c.name}${c.args ? ' ' : ''}`;
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      textarea.focus();
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      return;
    }
    textarea.value = '';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    await runSlash(`/${c.name}`, getCtx?.() || {});
    onRan?.();
  }

  const onInput = () => update();
  const onKey = (ev) => {
    if (!open) return;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); ev.stopImmediatePropagation(); sel = (sel + 1) % items.length; paint(); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); ev.stopImmediatePropagation(); sel = (sel - 1 + items.length) % items.length; paint(); }
    else if (ev.key === 'Enter' || ev.key === 'Tab') {
      if (ev.shiftKey && ev.key === 'Enter') return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      pick(ev.key === 'Tab');
    } else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopImmediatePropagation(); hide(); }
  };
  const onBlur = () => setTimeout(hide, 120);
  textarea.addEventListener('input', onInput);
  textarea.addEventListener('keydown', onKey);
  textarea.addEventListener('blur', onBlur);
  return {
    isOpen: () => open,
    close: hide,
    destroy() {
      textarea.removeEventListener('input', onInput);
      textarea.removeEventListener('keydown', onKey);
      textarea.removeEventListener('blur', onBlur);
      menu.remove();
    },
  };
}

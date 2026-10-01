// core/theme.js — theme apply/toggle (builder B).
// settings.theme is 'system' | 'dark' | 'light'; 'system' follows prefers-color-scheme live.

import { emit, on } from '/js/core/bus.js';
import { getSettings, setSettings } from '/js/core/store.js';

const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
let inited = false;

export function resolvedTheme() {
  const t = getSettings().theme;
  if (t === 'dark' || t === 'light') return t;
  return mq && mq.matches ? 'light' : 'dark';
}

function apply() {
  const theme = getSettings().theme || 'system';
  const resolved = resolvedTheme();
  const root = document.documentElement;
  const changed = root.dataset.theme !== resolved || root.dataset.themePref !== theme;
  root.dataset.theme = resolved;
  root.dataset.themePref = theme;
  root.style.colorScheme = resolved;
  if (changed) emit('oj:theme-changed', { theme, resolved });
}

export function initTheme() {
  if (inited) return;
  inited = true;
  apply();
  mq?.addEventListener?.('change', () => { if (getSettings().theme === 'system') apply(); });
  on('oj:settings-changed', ({ changed }) => { if (changed.includes('theme')) apply(); });
}

export function setTheme(theme) {
  const t = ['system', 'dark', 'light'].includes(theme) ? theme : 'system';
  setSettings({ theme: t });
  apply();
}

// B-private: system → dark → light → system
export function cycleTheme() {
  const order = ['system', 'dark', 'light'];
  const i = order.indexOf(getSettings().theme);
  setTheme(order[(i + 1) % order.length]);
  return getSettings().theme;
}

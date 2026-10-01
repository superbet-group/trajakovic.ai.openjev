// core/router.js — hash router and view registry (builder B).
// Views mount into #view; the previous view's unmount runs first. Unknown or unregistered
// views render a placeholder rather than throwing, so a failed C module never blanks the app.

import { emit } from '/js/core/bus.js';
import { h, clear } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { peekConversation, getLastConvId } from '/js/core/store.js';

const views = new Map();
const failures = new Map();
let current = { name: null, params: {}, unmount: null };
let started = false;

export function registerView(name, def) {
  views.set(name, def);
  if (started && current.name === name && current.placeholder) render();
}

// B-private
export function hasView(name) { return views.has(name); }
export function viewDef(name) { return views.get(name) || null; }
export function setViewFailure(name, error) { failures.set(name, error); }

export function navigate(hash) {
  const target = hash.startsWith('#') ? hash : `#${hash}`;
  if (location.hash === target) render();
  else location.hash = target;
}

function parse(hash) {
  const raw = (hash || '').replace(/^#/, '');
  const parts = raw.split('/').filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  if (!parts.length) {
    const last = getLastConvId();
    if (last && peekConversation(last)) return { name: 'thread', params: { id: last }, redirect: `#/c/${encodeURIComponent(last)}` };
    return { name: 'welcome', params: {} };
  }
  switch (parts[0]) {
    case 'c': return parts[1] ? { name: 'thread', params: { id: parts[1] } } : { name: 'welcome', params: {} };
    case 'templates': return { name: 'templates', params: {} };
    case 'batch': return { name: 'batch', params: {} };
    case 'stats': return { name: 'stats', params: {} };
    case 'compare': return { name: 'compare', params: { convId: parts[1] || null, turnId: parts[2] || null, turnId2: parts[3] || null } };
    default: return { name: 'welcome', params: {}, notFound: raw };
  }
}

export function currentRoute() {
  return { name: current.name || 'welcome', params: current.params || {} };
}

function placeholder(el, name, err) {
  el.appendChild(h('div', { class: 'view-placeholder' },
    h('div', { class: 'ph-icon' }, icon('alert', 28)),
    h('h2', {}, err ? `The "${name}" view failed to load` : `The "${name}" view is not available`),
    h('p', { class: 'muted' }, err ? 'module failed to load' : 'Its module did not register this view.'),
    err ? h('pre', { class: 'mono ph-error' }, String(err?.stack || err?.message || err)) : null,
    h('a', { class: 'btn', href: '#/' }, 'Back to the thread')));
}

function render() {
  const el = document.getElementById('view');
  if (!el) return;
  const route = parse(location.hash);
  if (route.redirect && location.hash !== route.redirect) history.replaceState(null, '', route.redirect);
  try { current.unmount?.(); } catch (err) { console.warn('[router] unmount failed', err); }
  clear(el);
  el.scrollTop = 0;
  el.dataset.view = route.name;
  const def = views.get(route.name);
  current = { name: route.name, params: route.params, unmount: null, placeholder: !def };
  if (!def) placeholder(el, route.name, failures.get(route.name) || failures.get('*'));
  else {
    try {
      const un = def.mount(el, route.params);
      current.unmount = typeof un === 'function' ? un : null;
    } catch (err) {
      console.warn(`[router] view ${route.name} failed to mount`, err);
      clear(el);
      placeholder(el, route.name, err);
    }
    document.title = def.title && route.name !== 'thread' ? `${def.title} · OpenJev Playground` : 'OpenJev Playground';
  }
  emit('oj:route', { name: route.name, params: route.params });
}

export function initRouter() {
  if (started) return;
  started = true;
  window.addEventListener('hashchange', render);
  render();
}

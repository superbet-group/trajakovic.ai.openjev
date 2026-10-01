// core/bus.js — the app-wide event bus (builder B).
// One EventTarget; every event carries a single `detail` object (see CONTRACT §3.3).

export const bus = new EventTarget();

export function emit(name, detail = {}) {
  bus.dispatchEvent(new CustomEvent(name, { detail }));
}

export function on(name, fn) {
  const handler = (ev) => {
    try { fn(ev.detail); } catch (err) { console.warn(`[bus] handler for ${name} failed`, err); }
  };
  bus.addEventListener(name, handler);
  return () => bus.removeEventListener(name, handler);
}

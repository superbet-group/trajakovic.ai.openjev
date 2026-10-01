// main.js — boot sequence (builder B): store → theme → config → C's barrel (with fallbacks)
// → B's views and slash commands → shell + sidebar → router → health polling.

import { initStore } from '/js/core/store.js';
import { initTheme } from '/js/core/theme.js';
import { getConfig, startHealthPolling } from '/js/core/api.js';
import { initRouter } from '/js/core/router.js';
import { toast } from '/js/core/toast.js';
import { loadJev } from '/js/app/jev.js';
import { registerThreadViews } from '/js/app/thread.js';
import { registerCoreSlash } from '/js/app/commands.js';
import { initShell } from '/js/app/shell.js';
import { initSidebar } from '/js/app/sidebar.js';
import { initComposer } from '/js/app/composer.js';
import { loadMarkdown } from '/js/app/chat.js';

async function boot() {
  const { fallback } = await initStore();
  initTheme();
  await getConfig();
  initComposer();
  registerThreadViews();
  registerCoreSlash();
  initShell();
  initSidebar();
  loadMarkdown();
  await loadJev();
  initRouter();
  startHealthPolling();
  document.documentElement.classList.add('booted');
  if (fallback) toast('IndexedDB is unavailable: conversations are kept in memory and localStorage only', { kind: 'warn', timeout: 6000 });
}

boot().catch((err) => {
  console.warn('[boot] failed', err);
  const v = document.getElementById('view');
  if (v) {
    v.textContent = '';
    const pre = document.createElement('pre');
    pre.className = 'mono boot-error';
    pre.textContent = `OpenJev Playground failed to start:\n\n${err?.stack || err}`;
    v.appendChild(pre);
  }
});

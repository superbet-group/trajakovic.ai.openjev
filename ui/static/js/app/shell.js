// app/shell.js — topbar (sidebar toggle, title with inline rename, mode badge, nav, health
// pill + popover, theme cycle, settings), the upstream banner wiring, layout toggles and
// global keyboard shortcuts (builder B).

import { h, clear, popover, copyText, append } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { on, emit } from '/js/core/bus.js';
import { openModal } from '/js/core/modal.js';
import { isModalOpen } from '/js/core/modal.js';
import { isDrawerOpen, closeDrawer } from '/js/core/drawer.js';
import { navigate, currentRoute, viewDef } from '/js/core/router.js';
import { cycleTheme } from '/js/core/theme.js';
import { renderUpstreamBanner } from '/js/core/errors.js';
import { getSettings, peekConversation, updateConversation } from '/js/core/store.js';
import { getLastHealth, getCachedConfig, checkHealthNow } from '/js/core/api.js';
import { fmtMs, fmtRelTime, shortId } from '/js/core/format.js';
import { openSettings } from '/js/app/settings.js';
import { toggleSidebar, newConversation } from '/js/app/sidebar.js';
import { focusComposer, toggleQuestionTab, stopInflight, isInflight } from '/js/app/composer.js';
import { lastTurnOf } from '/js/app/thread.js';

let titleEl, badgeEl, pillEl, themeBtn, navEls = {};
const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const MOD = isMac ? 'Cmd' : 'Ctrl';

function modelLabel(health) {
  const names = (health?.models || []).map((m) => m.name);
  return names.find((n) => /^openjev-\d/.test(n)) || names.find((n) => n !== 'diffusiongemma-26b') || 'openjev';
}

function renderPill() {
  if (!pillEl) return;
  const hh = getLastHealth();
  clear(pillEl);
  let cls = 'pill-unknown', text = 'checking…';
  if (hh) {
    if (hh.proxyDown) { cls = 'pill-down'; text = 'ui down'; }
    else if (hh.ok) {
      const ms = hh.upstream?.latencyMs;
      cls = ms !== null && ms !== undefined && ms > 500 ? 'pill-warn' : 'pill-ok';
      text = `${modelLabel(hh)} · ${fmtMs(ms)}${cls === 'pill-warn' ? ' · slow' : ''}`;
    } else if (hh.upstream?.errorType === 'auth') { cls = 'pill-warn'; text = 'auth'; }
    else { cls = 'pill-down'; text = 'down'; }
  }
  pillEl.className = `health-pill mono ${cls}`;
  pillEl.append(h('span', { class: 'pill-dot' }), h('span', {}, text));
}

function healthPopover(anchor) {
  const body = h('div', { class: 'health-pop' });
  const paint = (hh) => {
    clear(body);
    const cfg = getCachedConfig();
    const up = hh?.upstream || {};
    append(body,
      h('div', { class: 'hp-head' },
        h('span', { class: `hp-status ${hh?.ok ? 'ok' : 'bad'}` }, hh?.ok ? 'reachable' : hh?.proxyDown ? 'UI server unreachable' : up.errorType === 'auth' ? 'auth rejected' : 'not reachable'),
        h('span', { class: 'spacer' }),
        h('button', { class: 'btn sm ghost', onClick: async () => paint(await checkHealthNow()) }, icon('refresh', 12), 'Check')),
      h('div', { class: 'kv mono' },
        h('span', { class: 'kv-k faint' }, 'upstream'), h('span', { class: 'kv-v' }, up.url || cfg?.openjevUrl || '—'),
        h('span', { class: 'kv-k faint' }, 'latency'), h('span', { class: 'kv-v' }, fmtMs(up.latencyMs)),
        h('span', { class: 'kv-k faint' }, 'status'), h('span', { class: 'kv-v' }, up.status ?? '—'),
        up.requestId ? [h('span', { class: 'kv-k faint' }, 'request id'), h('button', { class: 'kv-v link-btn mono', onClick: () => copyText(up.requestId) }, shortId(up.requestId, 12))] : null,
        up.serverTiming ? [h('span', { class: 'kv-k faint' }, 'server-timing'), h('span', { class: 'kv-v' }, up.serverTiming)] : null,
        h('span', { class: 'kv-k faint' }, 'proxy auth'), h('span', { class: 'kv-v' }, (hh?.authConfigured ?? cfg?.authConfigured) ? 'OPENJEV_API_KEY set' : 'none'),
        getSettings().authOverride ? [h('span', { class: 'kv-k faint' }, 'override'), h('span', { class: 'kv-v warn-text' }, 'active')] : null,
        hh?.proxy ? [h('span', { class: 'kv-k faint' }, 'proxy'), h('span', { class: 'kv-v' }, `up ${fmtMs((hh.proxy.uptimeS || 0) * 1000)} · ${hh.proxy.requests} req`)] : null,
        h('span', { class: 'kv-k faint' }, 'checked'), h('span', { class: 'kv-v' }, hh?.checkedAt ? fmtRelTime(hh.checkedAt) : '—')),
      up.error ? h('div', { class: 'hp-error mono' }, up.error) : null,
      h('div', { class: 'hp-models-title faint mono' }, 'models'),
      (hh?.models || []).length
        ? h('div', { class: 'hp-models' }, hh.models.map((m) => h('div', { class: 'hp-model' },
          h('span', { class: ['mono hp-mname', m.name === 'diffusiongemma-26b' && 'accent-2'] }, m.name),
          h('span', { class: 'faint hp-mdesc' }, m.description || ''),
          m.release_date ? h('span', { class: 'faint mono hp-mdate' }, m.release_date) : null)))
        : h('div', { class: 'faint' }, 'none: start OpenJev with mise run startOpenJev'),
      h('div', { class: 'hp-hints' }, ['start', 'logs', 'status', 'stop'].map((k) => {
        const cmd = cfg?.hints?.[k];
        return cmd ? h('button', { class: 'cmd-chip mono', onClick: () => copyText(cmd) }, icon('terminal', 12), cmd) : null;
      })));
  };
  paint(getLastHealth());
  popover(anchor, body, { align: 'end', className: 'health-popover' });
}

function renderTitle() {
  if (!titleEl) return;
  const r = currentRoute();
  clear(titleEl);
  clear(badgeEl);
  badgeEl.hidden = true;
  if (r.name === 'thread') {
    const c = peekConversation(r.params.id);
    if (!c) return;
    titleEl.appendChild(h('span', { class: 'tb-title-text', title: 'Double-click to rename' }, c.title));
    titleEl.dataset.convId = c.id;
    badgeEl.hidden = false;
    badgeEl.className = `badge mode-badge ${c.mode === 'chat' ? 'mode-chat' : 'mode-s1'}`;
    badgeEl.append(icon(c.mode === 'chat' ? 'chat' : 'bolt', 12), c.mode === 'chat' ? `Chat · ${getSettings().chatModel}` : 'System One');
    document.title = `${c.title} · OpenJev Playground`;
  } else {
    delete titleEl.dataset.convId;
    const def = viewDef(r.name);
    titleEl.appendChild(h('span', { class: 'tb-title-text muted' }, r.name === 'welcome' ? 'New session' : def?.title || r.name));
  }
  for (const [name, el] of Object.entries(navEls)) el.classList.toggle('active', r.name === name);
}

function startRename() {
  const id = titleEl.dataset.convId;
  if (!id) return;
  const c = peekConversation(id);
  if (!c) return;
  const input = h('input', { class: 'input sm tb-rename', value: c.title, 'aria-label': 'Conversation title' });
  clear(titleEl);
  titleEl.appendChild(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const v = input.value.trim();
    if (save && v && v !== c.title) await updateConversation(id, { title: v });
    renderTitle();
  };
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

function renderThemeBtn() {
  if (!themeBtn) return;
  const t = getSettings().theme;
  clear(themeBtn);
  themeBtn.appendChild(icon(t === 'system' ? 'monitor' : t === 'dark' ? 'moon' : 'sun', 16));
  themeBtn.title = `Theme: ${t} (click to cycle)`;
}

export function openShortcuts() {
  const rows = [
    [`${MOD}+K`, 'focus composer'],
    [`${MOD}+Shift+O`, 'new decision'],
    [`${MOD}+B`, 'toggle sidebar'],
    [`${MOD}+J`, 'toggle Builder / JSON question editor'],
    [`${MOD}+I`, 'inspect last turn'],
    [`${MOD}+Enter`, 'send (always)'],
    ['Enter / Shift+Enter', 'send / newline'],
    ['Esc', 'stop a request, or close the drawer / modal'],
    ['/', 'slash commands in the composer'],
    ['?', 'this list'],
  ];
  openModal({
    title: 'Keyboard shortcuts',
    body: h('div', { class: 'shortcuts' }, rows.map(([k, v]) => h('div', { class: 'sc-row' }, h('span', { class: 'kbd' }, k), h('span', { class: 'muted' }, v)))),
  });
}

function inspectLast() {
  const r = currentRoute();
  if (r.name !== 'thread') return;
  const t = lastTurnOf(r.params.id);
  if (t) emit('oj:open-inspector', { convId: r.params.id, turnId: t.id });
}

function isTyping(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable || !!el.closest?.('[contenteditable="true"], .cm-editor, .jse-main');
}

function onKey(ev) {
  const mod = ev.metaKey || ev.ctrlKey;
  const k = ev.key.toLowerCase();
  if (mod && !ev.shiftKey && k === 'k') { ev.preventDefault(); if (!focusComposer()) document.querySelector('.state-input')?.focus(); return; }
  if (mod && ev.shiftKey && k === 'o') { ev.preventDefault(); newConversation('systemone'); return; }
  if (mod && !ev.shiftKey && k === 'b') { ev.preventDefault(); toggleSidebar(); return; }
  if (mod && !ev.shiftKey && k === 'j') { ev.preventDefault(); toggleQuestionTab(); return; }
  if (mod && !ev.shiftKey && k === 'i') { ev.preventDefault(); inspectLast(); return; }
  if (ev.key === 'Escape') {
    if (isModalOpen()) return;
    if (isInflight()) { ev.preventDefault(); stopInflight(); return; }
    if (isDrawerOpen()) { ev.preventDefault(); closeDrawer(); return; }
    const app = document.getElementById('app');
    if (app?.classList.contains('sidebar-open')) { app.classList.remove('sidebar-open'); return; }
    return;
  }
  if (ev.key === '?' && !mod && !isTyping(ev.target) && !isModalOpen()) { ev.preventDefault(); openShortcuts(); }
}

export function initShell() {
  const top = document.getElementById('topbar');
  if (!top) return;
  clear(top);
  titleEl = h('div', { class: 'tb-title', onDblclick: startRename });
  badgeEl = h('span', { class: 'badge mode-badge', hidden: true });
  pillEl = h('button', { class: 'health-pill mono', title: 'OpenJev health (click for details)', onClick: (ev) => healthPopover(ev.currentTarget) });
  themeBtn = h('button', { class: 'icon-btn', 'aria-label': 'Cycle theme', onClick: () => { cycleTheme(); renderThemeBtn(); } });
  const nav = (name, label, ic, hash) => {
    const el = h('a', { class: 'nav-btn', href: hash, title: label }, icon(ic, 15), h('span', { class: 'nav-label' }, label));
    navEls[name] = el;
    return el;
  };
  top.append(
    h('button', { class: 'icon-btn sidebar-toggle', title: `Toggle sidebar (${MOD}+B)`, 'aria-label': 'Toggle sidebar', onClick: toggleSidebar }, icon('menu', 17)),
    titleEl,
    badgeEl,
    h('span', { class: 'spacer' }),
    h('nav', { class: 'tb-nav' },
      nav('templates', 'Templates', 'templates', '#/templates'),
      nav('batch', 'Batch', 'batch', '#/batch'),
      nav('stats', 'Stats', 'stats', '#/stats')),
    pillEl,
    h('button', { class: 'icon-btn', title: 'Keyboard shortcuts (?)', 'aria-label': 'Keyboard shortcuts', onClick: openShortcuts }, icon('keyboard', 16)),
    themeBtn,
    h('button', { class: 'icon-btn', title: 'Settings', 'aria-label': 'Settings', onClick: () => openSettings() }, icon('settings', 16)));
  renderPill();
  renderThemeBtn();

  on('oj:health', ({ health }) => { renderPill(); renderUpstreamBanner(health); });
  on('oj:route', () => renderTitle());
  on('oj:conversation-updated', ({ id }) => { if (titleEl.dataset.convId === id && !titleEl.querySelector('input')) renderTitle(); });
  on('oj:conversations-changed', () => { if (!titleEl.querySelector('input')) renderTitle(); });
  on('oj:settings-changed', ({ changed }) => { if (changed.includes('theme')) renderThemeBtn(); if (changed.includes('chatModel')) renderTitle(); });
  on('oj:open-settings', (d) => openSettings(d || {}));
  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', () => {
    if (window.innerWidth >= 900) document.getElementById('app')?.classList.remove('sidebar-open');
  });
}

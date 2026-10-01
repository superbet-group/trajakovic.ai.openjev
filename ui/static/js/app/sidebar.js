// app/sidebar.js — conversation history (builder B): new decision / new chat, search,
// date groups (Pinned, Today, Yesterday, Last 7 days, Older), row menu (rename, pin,
// duplicate, export, delete), import/export with "strip images", and the totals line.

import { h, clear, popover, menuList, download, debounce } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { on } from '/js/core/bus.js';
import { toast } from '/js/core/toast.js';
import { openModal, confirmDialog, promptDialog } from '/js/core/modal.js';
import { navigate, currentRoute } from '/js/core/router.js';
import {
  listConversations, getConversation, createConversation, updateConversation, deleteConversation, saveConversation,
  exportConversations, importConversations, getTotals, getSettings, stripImagesDeep, getLayout, setLayout,
} from '/js/core/store.js';
import { costOf } from '/js/core/metrics.js';
import { fmtInt, fmtTokens, fmtCost, uid } from '/js/core/format.js';
import { focusComposer } from '/js/app/composer.js';

let root = null;
let listEl = null;
let totalsEl = null;
let search = '';
let fileInput = null;

function groupOf(s) {
  if (s.pinned) return 'Pinned';
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const t = s.updatedAt;
  if (t >= today.getTime()) return 'Today';
  if (t >= today.getTime() - 86400000) return 'Yesterday';
  if (t >= today.getTime() - 7 * 86400000) return 'Last 7 days';
  return 'Older';
}

function activeId() {
  const r = currentRoute();
  return r.name === 'thread' ? r.params.id : null;
}

export async function newConversation(mode = 'systemone') {
  // already on an untouched conversation of this mode: stay, instead of piling up "Untitled"s
  const id = activeId();
  const here = id ? await getConversation(id) : null;
  if (here && here.mode === mode && !here.turns.length && !here.draft?.state) {
    closeOverlay();
    focusComposer();
    return here;
  }
  const c = await createConversation({ mode });
  navigate(`#/c/${encodeURIComponent(c.id)}`);
  closeOverlay();
  return c;
}

function closeOverlay() {
  if (window.innerWidth < 900) document.getElementById('app')?.classList.remove('sidebar-open');
}

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

export function exportDialog(ids) {
  const strip = h('input', { type: 'checkbox', id: 'exp-strip' });
  const n = ids === 'all' ? 'all conversations' : `${ids.length} conversation${ids.length === 1 ? '' : 's'}`;
  openModal({
    title: 'Export',
    body: h('div', { class: 'col' },
      h('p', { class: 'muted' }, `Export ${n} as an ojui-export JSON file. It includes every turn, request, response and timing.`),
      h('label', { class: 'check-row' }, strip, h('span', {}, 'Strip images'), h('span', { class: 'faint' }, 'replace each image data URL with null (much smaller file)'))),
    actions: [
      { label: 'Cancel', kind: 'ghost' },
      {
        label: 'Export', kind: 'primary', onClick: async () => {
          const file = await exportConversations(ids);
          if (strip.checked) file.conversations = file.conversations.map(stripImagesDeep);
          const name = ids !== 'all' && ids.length === 1 ? `openjev-${(file.conversations[0]?.title || 'conversation').replace(/[^\w-]+/g, '-').slice(0, 40)}-${stamp()}.json` : `openjev-conversations-${stamp()}.json`;
          download(name, file);
          toast(`Exported ${file.conversations.length} conversation${file.conversations.length === 1 ? '' : 's'}`, { kind: 'ok' });
        },
      },
    ],
  });
}

export async function importFromFile(file) {
  try {
    const text = await file.text();
    const json = JSON.parse(text);
    const r = await importConversations(json);
    toast(`Imported ${r.imported}${r.skipped ? `, skipped ${r.skipped}` : ''}`, { kind: r.imported ? 'ok' : 'warn' });
  } catch (err) {
    toast(`Import failed: ${err.message}`, { kind: 'err', timeout: 5000 });
  }
}

export function pickImportFile() {
  fileInput?.click();
}

async function renameConv(id) {
  const c = await getConversation(id);
  if (!c) return;
  const t = await promptDialog('Title', c.title, { title: 'Rename conversation' });
  if (t !== null && t.trim()) await updateConversation(id, { title: t.trim() });
}

export async function deleteConvConfirm(id) {
  const c = await getConversation(id);
  if (!c) return;
  if (!(await confirmDialog(`Delete "${c.title}" and its ${c.turns.length} turn${c.turns.length === 1 ? '' : 's'}? The request log and totals keep their history.`, { danger: true }))) return;
  const wasActive = activeId() === id;
  await deleteConversation(id);
  toast('Conversation deleted');
  if (wasActive) navigate('#/');
}

async function duplicateConv(id) {
  const c = await getConversation(id);
  if (!c) return;
  const copy = JSON.parse(JSON.stringify(c));
  copy.id = uid('c');
  copy.title = `${c.title} (copy)`;
  copy.createdAt = Date.now();
  copy.pinned = false;
  await saveConversation(copy);
  navigate(`#/c/${encodeURIComponent(copy.id)}`);
}

function rowMenu(anchor, s) {
  let pop;
  const close = () => pop?.close();
  pop = popover(anchor, menuList([
    { label: 'Rename', icon: icon('edit', 14), onClick: () => renameConv(s.id) },
    { label: s.pinned ? 'Unpin' : 'Pin', icon: icon('pin', 14), onClick: () => updateConversation(s.id, { pinned: !s.pinned }) },
    { label: 'Duplicate', icon: icon('duplicate', 14), onClick: () => duplicateConv(s.id) },
    { label: 'Export', icon: icon('download', 14), onClick: () => exportDialog([s.id]) },
    '-',
    { label: 'Delete', icon: icon('trash', 14), danger: true, onClick: () => deleteConvConfirm(s.id) },
  ], close), { align: 'end' });
}

function row(s, active) {
  const el = h('a', {
    class: ['conv-row', active && 'active', s.pinned && 'pinned'], href: `#/c/${encodeURIComponent(s.id)}`,
    title: s.lastPreview || s.title,
    onClick: () => closeOverlay(),
    onDblclick: (ev) => { ev.preventDefault(); renameConv(s.id); },
  },
  h('span', { class: ['conv-icon', s.mode === 'chat' ? 'chat' : 's1'] }, icon(s.mode === 'chat' ? 'chat' : 'bolt', 14)),
  h('span', { class: 'conv-title' }, s.title),
  h('span', { class: 'conv-count mono faint' }, `${s.turnCount}`),
  h('button', {
    class: 'conv-more icon-btn sm', title: 'More', 'aria-label': `Actions for ${s.title}`,
    onClick: (ev) => { ev.preventDefault(); ev.stopPropagation(); rowMenu(ev.currentTarget, s); },
  }, icon('more', 14)));
  return el;
}

async function renderList() {
  if (!listEl) return;
  const all = await listConversations();
  const q = search.trim().toLowerCase();
  const items = q ? all.filter((s) => s.title.toLowerCase().includes(q) || (s.lastPreview || '').toLowerCase().includes(q)) : all;
  const act = activeId();
  clear(listEl);
  if (!all.length) {
    listEl.appendChild(h('div', { class: 'side-empty faint' }, 'No conversations yet. Start a decision, or pick a template.'));
    return;
  }
  if (!items.length) {
    listEl.appendChild(h('div', { class: 'side-empty faint' }, `Nothing matches "${search}".`));
    return;
  }
  const order = ['Pinned', 'Today', 'Yesterday', 'Last 7 days', 'Older'];
  const groups = new Map(order.map((g) => [g, []]));
  for (const s of items) groups.get(groupOf(s)).push(s);
  for (const g of order) {
    const list = groups.get(g);
    if (!list.length) continue;
    listEl.appendChild(h('div', { class: 'side-group' },
      h('div', { class: 'side-group-label faint mono' }, g),
      list.map((s) => row(s, s.id === act))));
  }
}

function renderTotals() {
  if (!totalsEl) return;
  const t = getTotals();
  const s = getSettings();
  const cost = costOf({ input_tokens: t.inputTokens, output_tokens: t.outputTokens }, s);
  clear(totalsEl);
  totalsEl.append(icon('sigma', 13),
    h('span', {}, `${fmtInt(t.requests)} req · ${fmtTokens(t.inputTokens + t.outputTokens)} tok · ${fmtCost(cost, s.currency)}`));
  if (t.errors) totalsEl.append(h('span', { class: 'err-text' }, ` · ${fmtInt(t.errors)} err`));
}

export function toggleSidebar() {
  const app = document.getElementById('app');
  if (!app) return;
  if (window.innerWidth < 900) {
    app.classList.toggle('sidebar-open');
  } else {
    const collapsed = !app.classList.contains('sidebar-collapsed');
    app.classList.toggle('sidebar-collapsed', collapsed);
    setLayout({ sidebarCollapsed: collapsed });
  }
}

export function initSidebar() {
  root = document.getElementById('sidebar');
  if (!root) return;
  clear(root);
  const app = document.getElementById('app');
  app?.classList.toggle('sidebar-collapsed', !!getLayout().sidebarCollapsed);

  fileInput = h('input', { type: 'file', accept: '.json,application/json', hidden: true, onChange: (ev) => { const f = ev.target.files?.[0]; if (f) importFromFile(f); ev.target.value = ''; } });
  const searchInput = h('input', { class: 'input sm side-search', type: 'search', placeholder: 'Search conversations', 'aria-label': 'Search conversations' });
  searchInput.addEventListener('input', debounce(() => { search = searchInput.value; renderList(); }, 80));

  listEl = h('nav', { class: 'side-list scroll', 'aria-label': 'Conversations' });
  totalsEl = h('a', { class: 'side-totals mono', href: '#/stats', title: 'All-time totals (survive deleting conversations). Open stats.' });

  root.append(
    h('div', { class: 'side-top' },
      h('a', { class: 'brand', href: '#/', title: 'OpenJev Playground' },
        h('span', { class: 'brand-mark' }, icon('logo', 18)),
        h('span', { class: 'brand-name' }, 'OpenJev', h('span', { class: 'brand-sub' }, 'playground'))),
      h('span', { class: 'spacer' }),
      h('button', { class: 'icon-btn', title: 'Collapse sidebar (Cmd+B)', 'aria-label': 'Collapse sidebar', onClick: toggleSidebar }, icon('sidebar', 16))),
    h('div', { class: 'side-actions' },
      h('button', { class: 'btn primary new-btn', title: 'New decision (Cmd+Shift+O)', onClick: () => newConversation('systemone') }, icon('bolt', 14), 'New decision'),
      h('button', { class: 'btn new-btn', title: 'New chat with diffusiongemma-26b', onClick: () => newConversation('chat') }, icon('chat', 14), 'New chat')),
    h('div', { class: 'side-search-wrap' }, icon('search', 14), searchInput),
    listEl,
    h('div', { class: 'side-foot' },
      h('div', { class: 'row side-io' },
        h('button', { class: 'btn sm ghost', onClick: pickImportFile, title: 'Import an ojui-export JSON (or drop it on the sidebar)' }, icon('upload', 13), 'Import'),
        h('button', { class: 'btn sm ghost', onClick: () => exportDialog('all') }, icon('download', 13), 'Export all')),
      totalsEl),
    fileInput);

  // drop a JSON export on the sidebar to import it
  root.addEventListener('dragover', (ev) => { if ([...(ev.dataTransfer?.types || [])].includes('Files')) { ev.preventDefault(); root.classList.add('dropping'); } });
  root.addEventListener('dragleave', (ev) => { if (!root.contains(ev.relatedTarget)) root.classList.remove('dropping'); });
  root.addEventListener('drop', (ev) => {
    root.classList.remove('dropping');
    const f = [...(ev.dataTransfer?.files || [])].find((x) => x.name.endsWith('.json') || x.type === 'application/json');
    if (!f) return;
    ev.preventDefault();
    importFromFile(f);
  });

  // overlay backdrop for narrow screens
  const backdrop = h('div', { class: 'sidebar-backdrop', onClick: () => app?.classList.remove('sidebar-open') });
  app?.appendChild(backdrop);

  on('oj:conversations-changed', () => renderList());
  on('oj:route', () => { renderList(); renderTotals(); });
  on('oj:request-done', () => renderTotals());
  on('oj:settings-changed', ({ changed }) => { if (changed.some((k) => k.startsWith('price') || k === 'currency')) renderTotals(); });
  window.addEventListener('storage', (ev) => { if (ev.key === 'ojui.totals.v1') renderTotals(); });
  renderList();
  renderTotals();
}

export function refreshTotals() { renderTotals(); }

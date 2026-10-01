// core/store.js — persistence (builder B): settings + totals in localStorage (sync), and
// conversations + the request log in IndexedDB `ojui` v1, mirrored in memory so reads
// are cheap and read-modify-write never races. Falls back to memory + localStorage.

import { emit } from '/js/core/bus.js';
import { uid } from '/js/core/format.js';

const SETTINGS_KEY = 'ojui.settings.v1';
const TOTALS_KEY = 'ojui.totals.v1';
const LAST_CONV_KEY = 'ojui.lastConv';
const LAYOUT_KEY = 'ojui.layout.v1';
const FALLBACK_KEY = 'ojui.fallback.conversations.v1';
const MAX_REQUESTS = 5000;

export const DEFAULT_SETTINGS = Object.freeze({
  theme: 'system',
  defaultModel: 'openjev-latest',
  defaultOptions: { steps: null, samples: null, think: null, sequential: false },
  chatModel: 'diffusiongemma-26b',
  chatMaxTokens: 1024,
  chatSystemPrompt: '',
  pricePerMInput: 0,
  pricePerMOutput: 0,
  currency: '$',
  authOverride: '',
  clearStateOnSend: false,
  jsonEditorMode: 'tree',
  questionEditorTab: 'builder',
  showRawByDefault: false,
  autoRetryOverloaded: true,
});

// ---------------------------------------------------------------- localStorage helpers
function lsGet(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch { return fallback; }
}
function lsSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}

// ---------------------------------------------------------------- settings
let settingsCache = null;

function mergeSettings(raw) {
  const s = { ...DEFAULT_SETTINGS, ...(raw && typeof raw === 'object' ? raw : {}) };
  s.defaultOptions = { ...DEFAULT_SETTINGS.defaultOptions, ...((raw && raw.defaultOptions) || {}) };
  return s;
}

export function getSettings() {
  if (!settingsCache) settingsCache = mergeSettings(lsGet(SETTINGS_KEY, {}));
  return settingsCache;
}

export function setSettings(patch) {
  const prev = getSettings();
  const next = mergeSettings({ ...prev, ...patch, defaultOptions: { ...prev.defaultOptions, ...(patch?.defaultOptions || {}) } });
  const changed = Object.keys(next).filter((k) => JSON.stringify(prev[k]) !== JSON.stringify(next[k]));
  settingsCache = next;
  lsSet(SETTINGS_KEY, next);
  if (changed.length) emit('oj:settings-changed', { settings: next, changed });
  return next;
}

// ---------------------------------------------------------------- layout + last conv (B-private)
export function getLayout() {
  return { sidebarCollapsed: false, qeditorOpen: true, drawerWidth: 560, ...lsGet(LAYOUT_KEY, {}) };
}
export function setLayout(patch) {
  const next = { ...getLayout(), ...patch };
  lsSet(LAYOUT_KEY, next);
  return next;
}
export function getLastConvId() {
  try { return localStorage.getItem(LAST_CONV_KEY) || null; } catch { return null; }
}
export function setLastConvId(id) {
  try { if (id) localStorage.setItem(LAST_CONV_KEY, id); else localStorage.removeItem(LAST_CONV_KEY); } catch { /* ignore */ }
}

// ---------------------------------------------------------------- IndexedDB
let db = null;
let usingFallback = false;
const convs = new Map();
let requests = [];

function openDB() {
  return new Promise((resolve, reject) => {
    if (!('indexedDB' in window)) { reject(new Error('IndexedDB unavailable')); return; }
    const req = indexedDB.open('ojui', 1);
    req.onupgradeneeded = () => {
      const d = req.result;
      if (!d.objectStoreNames.contains('conversations')) {
        const s = d.createObjectStore('conversations', { keyPath: 'id' });
        s.createIndex('updatedAt', 'updatedAt');
      }
      if (!d.objectStoreNames.contains('requests')) {
        const s = d.createObjectStore('requests', { keyPath: 'id' });
        s.createIndex('ts', 'ts');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('IndexedDB open failed'));
    req.onblocked = () => reject(new Error('IndexedDB blocked'));
  });
}

function idbAll(store) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, 'readonly');
    const r = t.objectStore(store).getAll();
    r.onsuccess = () => resolve(r.result || []);
    r.onerror = () => reject(r.error);
  });
}

function idbWrite(store, fn) {
  if (!db) return Promise.resolve(false);
  return new Promise((resolve) => {
    try {
      const t = db.transaction(store, 'readwrite');
      fn(t.objectStore(store));
      t.oncomplete = () => resolve(true);
      t.onerror = () => { console.warn('[store] write failed', t.error); resolve(false); };
      t.onabort = () => { console.warn('[store] write aborted', t.error); resolve(false); };
    } catch (err) {
      console.warn('[store] write threw', err);
      resolve(false);
    }
  });
}

let fallbackTimer = null;
function writeFallback() {
  clearTimeout(fallbackTimer);
  fallbackTimer = null;
  if (!lsSet(FALLBACK_KEY, [...convs.values()])) {
    // quota: retry without image data
    const slim = [...convs.values()].map(stripImagesDeep);
    lsSet(FALLBACK_KEY, slim);
  }
}
function persistFallback() {
  clearTimeout(fallbackTimer);
  fallbackTimer = setTimeout(writeFallback, 300);
}

// B-private: write a debounced localStorage fallback now (pagehide), so a closing tab keeps it
export function flushPendingWrites() {
  if (fallbackTimer) writeFallback();
}

let writeFailWarned = false;
function putConv(conv, op = 'put') {
  if (usingFallback) { persistFallback(); return Promise.resolve(true); }
  return idbWrite('conversations', (s) => s.put(conv)).then((ok) => {
    if (ok) announce(op, conv.id);
    else if (!writeFailWarned) {
      writeFailWarned = true;  // quota or an uncloneable value: tell the user once, not per keystroke
      emit('oj:toast', { message: 'Could not save a conversation to IndexedDB (storage full?). Export your history to keep it.', kind: 'err', timeout: 8000 });
    }
    return ok;
  });
}

// ---------------------------------------------------------------- cross-tab sync
// Every tab mirrors the DB in memory and writes whole conversations, so without this a second
// tab silently overwrites turns the first one added. Receivers re-read the record in place.
const TAB_ID = uid('tab');
let chan = null;
function announce(op, id) {
  try { chan?.postMessage({ op, id, from: TAB_ID }); } catch { /* ignore */ }
}

function idbGet(store, key) {
  return new Promise((resolve) => {
    try {
      const r = db.transaction(store, 'readonly').objectStore(store).get(key);
      r.onsuccess = () => resolve(r.result || null);
      r.onerror = () => resolve(null);
    } catch { resolve(null); }
  });
}

const turnSig = (t) => JSON.stringify(t, (k, v) => (typeof v === 'string' && v.length > 2048 ? v.length : v));

async function onSync(msg) {
  if (!msg || msg.from === TAB_ID || !db) return;
  if (msg.op === 'clear') { convs.clear(); emit('oj:conversations-changed', {}); return; }
  if (msg.op === 'delete') { if (convs.delete(msg.id)) emit('oj:conversations-changed', {}); return; }
  const raw = await idbGet('conversations', msg.id);
  if (!raw) return;
  const local = convs.get(msg.id);
  if (msg.op === 'draft') { if (local) local.draft = raw.draft ?? null; return; }
  const incoming = normalizeConversation(raw);
  if (!local) {
    convs.set(incoming.id, incoming);
  } else {
    // a turn this tab still has in flight stays ours (another tab's mount marks it aborted)
    const pending = new Map(local.turns.filter((t) => t.status === 'pending').map((t) => [t.id, t]));
    incoming.turns = incoming.turns.map((t) => (t.status === 'aborted' && pending.get(t.id)) || t);
    const before = new Map(local.turns.map((t) => [t.id, turnSig(t)]));
    for (const k of Object.keys(local)) if (!(k in incoming)) delete local[k];
    Object.assign(local, incoming);  // in place: views and the composer hold this object
    for (const t of local.turns) {
      if (before.has(t.id) && before.get(t.id) !== turnSig(t)) emit('oj:turn-updated', { convId: local.id, turn: t });
    }
  }
  const c = convs.get(msg.id);
  emit('oj:conversation-updated', { id: c.id, conversation: c });
  emit('oj:conversations-changed', {});
}

export async function initStore() {
  getSettings();
  try {
    db = await Promise.race([openDB(), new Promise((_, rej) => setTimeout(() => rej(new Error('IndexedDB open timed out')), 4000))]);
    const [c, r] = await Promise.all([idbAll('conversations'), idbAll('requests')]);
    for (const conv of c) convs.set(conv.id, normalizeConversation(conv));
    requests = r.sort((a, b) => a.ts - b.ts);
    try {
      chan = new BroadcastChannel('ojui.store.v1');
      chan.onmessage = (ev) => { onSync(ev.data).catch((e) => console.warn('[store] sync failed', e)); };
    } catch { chan = null; }
  } catch (err) {
    console.warn('[store] IndexedDB unavailable, using memory + localStorage', err);
    db = null;
    usingFallback = true;
    for (const conv of lsGet(FALLBACK_KEY, []) || []) if (conv && conv.id) convs.set(conv.id, normalizeConversation(conv));
  }
  try { window.addEventListener('pagehide', flushPendingWrites); } catch { /* ignore */ }
  return { fallback: usingFallback };
}

export function isFallbackStore() { return usingFallback; }

// ---------------------------------------------------------------- conversations
function defaultOptions() {
  const s = getSettings();
  return { model: s.defaultModel, ...s.defaultOptions };
}

function normalizeConversation(c) {
  const now = Date.now();
  return {
    id: c.id || uid('c'),
    title: typeof c.title === 'string' && c.title ? c.title : 'Untitled',
    mode: c.mode === 'chat' ? 'chat' : 'systemone',
    createdAt: c.createdAt || now,
    updatedAt: c.updatedAt || c.createdAt || now,
    pinned: !!c.pinned,
    options: { ...defaultOptions(), ...(c.options || {}) },
    system: c.system ?? '',
    draft: c.draft ?? null,
    turns: Array.isArray(c.turns) ? c.turns : [],
    ...(c.chatOptions ? { chatOptions: c.chatOptions } : {}),
  };
}

function preview(turn) {
  if (!turn) return '';
  if (turn.kind === 'chat') return String(turn.user || '').slice(0, 120);
  const st = turn.request?.state;
  if (typeof st === 'string') return st.slice(0, 120);
  try { return JSON.stringify(st).slice(0, 120); } catch { return ''; }
}

function summary(c) {
  return {
    id: c.id, title: c.title, mode: c.mode, createdAt: c.createdAt, updatedAt: c.updatedAt,
    pinned: !!c.pinned, turnCount: c.turns.length, lastPreview: preview(c.turns[c.turns.length - 1]),
  };
}

export async function listConversations() {
  return [...convs.values()].map(summary).sort((a, b) => (Number(b.pinned) - Number(a.pinned)) || (b.updatedAt - a.updatedAt));
}

export async function getConversation(id) {
  return convs.get(id) || null;
}

// B-private: sync lookup for the router / thread
export function peekConversation(id) {
  return convs.get(id) || null;
}

export async function createConversation({ mode = 'systemone', title } = {}) {
  const s = getSettings();
  const now = Date.now();
  const conv = normalizeConversation({
    id: uid('c'), title: title || 'Untitled', mode, createdAt: now, updatedAt: now,
    options: defaultOptions(), system: mode === 'chat' ? s.chatSystemPrompt || '' : '', turns: [],
  });
  convs.set(conv.id, conv);
  await putConv(conv);
  emit('oj:conversation-updated', { id: conv.id, conversation: conv });
  emit('oj:conversations-changed', {});
  return conv;
}

export async function saveConversation(conv) {
  if (!conv || !conv.id) return conv;
  const c = convs.get(conv.id) === conv ? conv : normalizeConversation(conv);
  c.updatedAt = Date.now();
  convs.set(c.id, c);
  await putConv(c);
  emit('oj:conversation-updated', { id: c.id, conversation: c });
  emit('oj:conversations-changed', {});
  return c;
}

export async function updateConversation(id, patch) {
  const c = convs.get(id);
  if (!c) return null;
  Object.assign(c, patch || {});
  return saveConversation(c);
}

// B-private: persist the unsent composer draft without reordering the sidebar
export async function saveDraft(id, draft) {
  const c = convs.get(id);
  if (!c) return;
  c.draft = draft;
  await putConv(c, 'draft');
}

export async function deleteConversation(id) {
  if (!convs.has(id)) return;
  convs.delete(id);
  if (usingFallback) persistFallback();
  else if (await idbWrite('conversations', (s) => s.delete(id))) announce('delete', id);
  if (getLastConvId() === id) setLastConvId(null);
  emit('oj:conversations-changed', {});
}

// B-private
export async function deleteAllConversations() {
  convs.clear();
  if (usingFallback) persistFallback();
  else if (await idbWrite('conversations', (s) => s.clear())) announce('clear', null);
  setLastConvId(null);
  emit('oj:conversations-changed', {});
}

function autoTitle(conv, turn) {
  let t = '';
  if (turn.kind === 'chat') t = String(turn.user || '');
  else {
    const st = turn.request?.state;
    if (typeof st === 'string') t = st;
    else if (st && typeof st === 'object') {
      const k = Array.isArray(st) ? '0' : Object.keys(st)[0];
      t = `JSON: ${k ?? '{}'}`;
    }
  }
  t = t.replace(/\s+/g, ' ').trim();
  if (!t) return;
  conv.title = t.length > 48 ? `${t.slice(0, 48).trimEnd()}…` : t;
}

export async function appendTurn(convId, turn) {
  const c = convs.get(convId);
  if (!c) return null;
  c.turns.push(turn);
  if ((c.title === 'Untitled' || !c.title) && c.turns.length === 1) autoTitle(c, turn);
  c.updatedAt = Date.now();
  await putConv(c);
  emit('oj:turn-updated', { convId, turn });
  emit('oj:conversation-updated', { id: c.id, conversation: c });
  emit('oj:conversations-changed', {});
  return turn;
}

export async function updateTurn(convId, turnId, patch) {
  const c = convs.get(convId);
  if (!c) return null;
  const turn = c.turns.find((t) => t.id === turnId);
  if (!turn) return null;
  Object.assign(turn, patch || {});
  await putConv(c);
  emit('oj:turn-updated', { convId, turn });
  return turn;
}

export async function deleteTurn(convId, turnId) {
  const c = convs.get(convId);
  if (!c) return;
  const before = c.turns.length;
  c.turns = c.turns.filter((t) => t.id !== turnId);
  if (c.turns.length !== before) await saveConversation(c);
}

// ---------------------------------------------------------------- export / import
function clone(v) {
  try { return structuredClone(v); } catch { return JSON.parse(JSON.stringify(v)); }
}

function stripImagesDeep(conv) {
  const c = clone(conv);
  for (const t of c.turns || []) {
    if (t.request && Array.isArray(t.request.images)) t.request.images = t.request.images.map(() => null);
  }
  if (c.draft && Array.isArray(c.draft.images)) c.draft.images = c.draft.images.map((i) => ({ ...i, dataUrl: null }));
  return c;
}
export { stripImagesDeep };

export async function exportConversations(ids) {
  const list = ids === 'all' || !ids ? [...convs.values()] : ids.map((id) => convs.get(id)).filter(Boolean);
  return { format: 'ojui-export', version: 1, exportedAt: Date.now(), app: 'openjev-ui', conversations: list.map(clone) };
}

export async function importConversations(file) {
  let list = [];
  if (Array.isArray(file)) list = file;
  else if (file && file.format === 'ojui-export' && Array.isArray(file.conversations)) list = file.conversations;
  else if (file && typeof file === 'object' && Array.isArray(file.turns)) list = [file];
  let imported = 0, skipped = 0;
  for (const raw of list) {
    if (!raw || typeof raw !== 'object' || !Array.isArray(raw.turns)) { skipped++; continue; }
    const c = normalizeConversation(clone(raw));
    const have = convs.get(c.id);
    if (have) {
      // re-importing the same export: skip exact copies instead of duplicating them
      const same = have.updatedAt === c.updatedAt && have.turns.map((t) => t.id).join('|') === c.turns.map((t) => t.id).join('|');
      if (same) { skipped++; continue; }
      c.id = uid('c');
    }
    convs.set(c.id, c);
    await putConv(c);
    imported++;
  }
  if (imported) emit('oj:conversations-changed', {});
  return { imported, skipped };
}

// ---------------------------------------------------------------- request log
export async function logRequest(record) {
  if (!record) return;
  requests.push(record);
  if (!usingFallback) await idbWrite('requests', (s) => s.put(record));
  if (requests.length > MAX_REQUESTS) {
    const drop = requests.splice(0, requests.length - MAX_REQUESTS);
    if (!usingFallback) idbWrite('requests', (s) => { for (const r of drop) s.delete(r.id); });
  }
}

export async function listRequests({ since = 0, limit = MAX_REQUESTS } = {}) {
  const out = requests.filter((r) => r.ts >= since);
  return out.length > limit ? out.slice(out.length - limit) : out.slice();
}

export async function clearRequests() {
  requests = [];
  if (!usingFallback) await idbWrite('requests', (s) => s.clear());
}

// ---------------------------------------------------------------- totals
function emptyTotals() {
  return {
    since: Date.now(), requests: 0, ok: 0, errors: 0, aborted: 0,
    inputTokens: 0, outputTokens: 0, imageCount: 0, estImageTokens: 0, questions: 0,
    clientMsSum: 0, serverTotalMsSum: 0, chatCompletionTokens: 0, chatStreamMsSum: 0,
    byModel: {}, byStatus: {}, byEndpoint: {},
  };
}

export function getTotals() {
  const t = lsGet(TOTALS_KEY, null);
  if (!t || typeof t !== 'object') {
    const e = emptyTotals();
    lsSet(TOTALS_KEY, e);
    return e;
  }
  return { ...emptyTotals(), ...t };
}

export function addToTotals(record) {
  if (!record) return;
  const t = getTotals();
  const n = (v) => Number(v) || 0;
  t.requests += 1;
  if (record.ok) t.ok += 1;
  else if (record.errorKind === 'aborted') t.aborted += 1;
  else t.errors += 1;
  const inT = n(record.usage?.inputTokens), outT = n(record.usage?.outputTokens);
  t.inputTokens += inT;
  t.outputTokens += outT;
  t.imageCount += n(record.imageCount);
  t.estImageTokens += n(record.estImageTokens);
  t.questions += n(record.questionCount);
  t.clientMsSum += n(record.clientMs);
  t.serverTotalMsSum += n(record.serverTiming?.total);
  if (record.endpoint === '/v1/chat/completions') {
    t.chatCompletionTokens += outT;
    t.chatStreamMsSum += n(record.chat?.streamMs);
  }
  const m = record.model || 'unknown';
  t.byModel[m] ||= { requests: 0, inputTokens: 0, outputTokens: 0 };
  t.byModel[m].requests += 1;
  t.byModel[m].inputTokens += inT;
  t.byModel[m].outputTokens += outT;
  const st = String(record.status ?? 0);
  t.byStatus[st] = (t.byStatus[st] || 0) + 1;
  t.byEndpoint[record.endpoint] = (t.byEndpoint[record.endpoint] || 0) + 1;
  lsSet(TOTALS_KEY, t);
}

export function resetTotals() {
  lsSet(TOTALS_KEY, emptyTotals());
}

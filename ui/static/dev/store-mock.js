// Dev harness only (builder C): an in-memory stand-in for /js/core/store.js, swapped in by the
// import map in /dev-harness.html so the harness never touches the real ojui IndexedDB or
// localStorage keys. Same exported names and semantics as the contract's §3.6.

import { emit } from '/js/core/bus.js';

const P = 'ojui.dev.';
export const DEFAULT_SETTINGS = Object.freeze({
  theme: 'dark', defaultModel: 'openjev-latest', defaultOptions: { steps: null, samples: null, think: null, sequential: false },
  chatModel: 'diffusiongemma-26b', chatMaxTokens: 1024, chatSystemPrompt: '', pricePerMInput: 0.2, pricePerMOutput: 0.8, currency: '$',
  authOverride: '', clearStateOnSend: false, clearStateOn: 'answer', threadLayout: 'v2', jsonEditorMode: 'tree', questionEditorTab: 'builder', showRawByDefault: false, autoRetryOverloaded: true,
});
const ls = {
  get(k, d) { try { const r = localStorage.getItem(P + k); return r === null ? d : JSON.parse(r); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(P + k, JSON.stringify(v)); } catch { /* ignore */ } },
};
let settings = { ...DEFAULT_SETTINGS, ...ls.get('settings', {}) };
export function getSettings() { return settings; }
export function setSettings(patch) {
  settings = { ...settings, ...patch };
  ls.set('settings', settings);
  emit('oj:settings-changed', { settings, changed: Object.keys(patch || {}) });
  return settings;
}
let layout = ls.get('layout', { sidebarCollapsed: false, qeditorOpen: true, drawerWidth: 600 });
export function getLayout() { return layout; }
export function setLayout(p) { layout = { ...layout, ...p }; ls.set('layout', layout); return layout; }
let lastConv = null;
export function getLastConvId() { return lastConv; }
export function setLastConvId(id) { lastConv = id; }

const convs = new Map();
let requests = [];
const clone = (v) => JSON.parse(JSON.stringify(v));
const id = (p) => `${p}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

export async function initStore() {}
export function isFallbackStore() { return true; }
export async function listConversations() {
  return [...convs.values()].sort((a, b) => (b.pinned - a.pinned) || (b.updatedAt - a.updatedAt)).map((c) => ({
    id: c.id, title: c.title, mode: c.mode, createdAt: c.createdAt, updatedAt: c.updatedAt, pinned: c.pinned, turnCount: c.turns.length, lastPreview: '',
  }));
}
export async function getConversation(cid) { const c = convs.get(cid); return c ? clone(c) : null; }
export function peekConversation(cid) { return convs.get(cid) || null; }
export async function createConversation({ mode = 'systemone', title } = {}) {
  const now = Date.now();
  const c = { id: id('c'), title: title || 'Untitled', mode, createdAt: now, updatedAt: now, pinned: false,
    options: { model: settings.defaultModel, ...settings.defaultOptions }, system: '', draft: null, turns: [] };
  convs.set(c.id, c); lastConv = c.id;
  emit('oj:conversations-changed', {});
  return clone(c);
}
export async function saveConversation(conv) { conv.updatedAt = Date.now(); convs.set(conv.id, clone(conv)); emit('oj:conversation-updated', { id: conv.id, conversation: conv }); emit('oj:conversations-changed', {}); return conv; }
export async function updateConversation(cid, patch) { const c = convs.get(cid); if (!c) return null; Object.assign(c, patch, { updatedAt: Date.now() }); emit('oj:conversation-updated', { id: cid, conversation: clone(c) }); return clone(c); }
export async function saveDraft(cid, draft) { const c = convs.get(cid); if (c) c.draft = draft; }
export function flushPendingWrites() {}
export async function deleteConversation(cid) { convs.delete(cid); emit('oj:conversations-changed', {}); }
export async function deleteAllConversations() { convs.clear(); emit('oj:conversations-changed', {}); }
export async function appendTurn(convId, turn) {
  const c = convs.get(convId); if (!c) throw new Error('no such conversation');
  c.turns.push(clone(turn)); c.updatedAt = Date.now();
  emit('oj:turn-updated', { convId, turn });
  return turn;
}
export async function updateTurn(convId, turnId, patch) {
  const c = convs.get(convId); const t = c?.turns.find((x) => x.id === turnId);
  if (!t) throw new Error('no such turn');
  Object.assign(t, clone(patch));
  emit('oj:turn-updated', { convId, turn: clone(t) });
  return clone(t);
}
export async function deleteTurn(convId, turnId) { const c = convs.get(convId); if (c) c.turns = c.turns.filter((t) => t.id !== turnId); }
export function stripImagesDeep(v) { return v; }
export async function exportConversations() { return { format: 'ojui-export', version: 1, exportedAt: Date.now(), app: 'openjev-ui', conversations: [...convs.values()].map(clone) }; }
export async function importConversations() { return { imported: 0, skipped: 0 }; }
export async function logRequest(record) { if (record) requests.push(record); }
export async function listRequests({ since = 0, limit = 5000 } = {}) { const out = requests.filter((r) => r.ts >= since); return out.slice(-limit); }
export async function clearRequests() { requests = []; }
function emptyTotals() { return { since: Date.now(), requests: 0, ok: 0, errors: 0, aborted: 0, inputTokens: 0, outputTokens: 0, imageCount: 0, estImageTokens: 0, questions: 0, clientMsSum: 0, serverTotalMsSum: 0, chatCompletionTokens: 0, chatStreamMsSum: 0, byModel: {}, byStatus: {}, byEndpoint: {} }; }
let totals = emptyTotals();
export function getTotals() { return clone(totals); }
export function addToTotals(r) {
  if (!r) return;
  totals.requests++; if (r.ok) totals.ok++; else if (r.errorKind === 'aborted') totals.aborted++; else totals.errors++;
  totals.inputTokens += r.usage?.inputTokens || 0; totals.outputTokens += r.usage?.outputTokens || 0;
  totals.imageCount += r.imageCount || 0; totals.estImageTokens += r.estImageTokens || 0; totals.questions += r.questionCount || 0;
}
export function resetTotals() { totals = emptyTotals(); }

// user templates: same six exports and event as store.js, under the ojui.dev. prefix
let tpl = null;
function tplData() { if (!tpl) { const r = ls.get('templates', null); tpl = { v: 1, items: Array.isArray(r?.items) ? r.items : [], hidden: Array.isArray(r?.hidden) ? r.hidden : [] }; } return tpl; }
function tplCommit(next, op, tid) { tpl = next; ls.set('templates', next); emit('oj:templates-changed', { op, id: tid }); return true; }
export function listUserTemplates() { return tplData().items.slice().sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0)).map(clone); }
export function getUserTemplate(tid) { const t = tplData().items.find((x) => x.id === tid); return t ? clone(t) : null; }
export function saveUserTemplate(t) {
  const title = String(t?.title ?? '').trim().slice(0, 80);
  if (!title) throw new Error('Template title is required');
  if (!t.questions || typeof t.questions !== 'object' || !Object.keys(t.questions).length) throw new Error('Template needs at least one question');
  const data = tplData();
  const prev = t.id ? data.items.find((x) => x.id === t.id) : null;
  if (!prev && data.items.length >= 200) throw new Error('At most 200 templates: delete one first');
  const now = Date.now();
  const opts = Object.fromEntries(Object.entries(t.options || {}).filter(([k, v]) => k !== 'model' && v !== null && v !== undefined && v !== false));
  const saved = clone({ ...t, id: prev ? prev.id : (t.id || id('tpl')), title, category: String(t.category ?? '').trim().toLowerCase() || 'mine',
    description: String(t.description ?? ''), stateIsJson: !!t.stateIsJson, state: t.stateIsJson ? t.state : String(t.state ?? ''),
    batchStates: (t.batchStates || []).slice(0, 1000).map(String), builtin: false, createdAt: prev?.createdAt || now, updatedAt: now });
  if (Object.keys(opts).length) saved.options = opts; else delete saved.options;
  tplCommit({ ...data, items: prev ? data.items.map((x) => (x.id === prev.id ? saved : x)) : [...data.items, saved] }, 'put', saved.id);
  return clone(saved);
}
export function deleteUserTemplate(tid) { const data = tplData(); if (!data.items.some((x) => x.id === tid)) return false; return tplCommit({ ...data, items: data.items.filter((x) => x.id !== tid) }, 'delete', tid); }
export function getHiddenTemplateIds() { return tplData().hidden.slice(); }
export function setTemplateHidden(tid, hidden) {
  const data = tplData();
  if (!!hidden === data.hidden.includes(tid)) return true;
  return tplCommit({ ...data, hidden: hidden ? [...data.hidden, tid] : data.hidden.filter((x) => x !== tid) }, 'hide', tid);
}

// harness helper: seed a conversation directly
export function __putConversation(c) { convs.set(c.id, clone(c)); lastConv = c.id; }

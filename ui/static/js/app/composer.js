// app/composer.js — the bottom composer (builder B): question-editor panel (C), state input
// (Text | JSON), image tray, option chips, send/stop, chat-mode controls, draft persistence,
// and the send pipeline for System One and chat turns. Handles oj:composer-load globally.

import { h, clear, debounce, popover, menuList, append } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { on } from '/js/core/bus.js';
import { toast } from '/js/core/toast.js';
import { openModal } from '/js/core/modal.js';
import { navigate, currentRoute } from '/js/core/router.js';
import { attachSlashMenu, matchSlash, runSlash } from '/js/core/slash.js';
import {
  getSettings, getConversation, createConversation, updateConversation, saveDraft, appendTurn, updateTurn,
  getLayout, setLayout, getLastConvId, flushPendingWrites,
} from '/js/core/store.js';
import { buildSystemOneBody, systemOne, getLastHealth, getCachedConfig } from '/js/core/api.js';
import { estimateTokens, costOf, sha256Hex, IMAGE_TOKENS_EST } from '/js/core/metrics.js';
import { uid, fmtBytes, fmtInt, fmtCost } from '/js/core/format.js';
import { J } from '/js/app/jev.js';
import { buildChatBody, runChatTurn, chatOptions } from '/js/app/chat.js';

const ACCEPT = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const MAX_IMAGES = 8;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export const STARTER_QUESTIONS = {
  needs_reply: { type: 'noul', instructions: 'Does this message need a reply from a human?' },
};

let cur = null;          // the mounted composer
let inflight = null;     // {controller, convId, turnId, kind}
let pendingLoad = null;  // a composer-load waiting for the thread to mount
let qTab = null;
const inflightListeners = new Set();

const clone = (v) => { try { return structuredClone(v); } catch { return JSON.parse(JSON.stringify(v)); } };

// ================================================================== public API
export function getDraft() {
  if (!cur) return { state: '', stateIsJson: false, questions: {}, images: [] };
  syncFromEditors();
  const d = cur.draft;
  return { state: d.state, stateIsJson: d.stateIsJson, questions: clone(d.questions), images: d.images.slice(), options: { ...cur.conv.options }, ...(d.parentTurnId ? { parentTurnId: d.parentTurnId } : {}) };
}

export function loadDraft(partial) { return handleLoad(partial || {}); }

export async function submit(opts = {}) {
  if (!cur) return;
  if (cur.conv.mode === 'chat') return sendChat();
  return sendSystemOne(opts);
}

export function clearComposer() {
  if (!cur) return;
  cur.draft.state = '';
  cur.draft.images = [];
  cur.draft.parentTurnId = null;
  cur.notices.validation = null;
  cur.notices.json = null;
  renderStateInput();
  renderTray();
  renderNotices();
  refreshEstimate();
  persist();
}

export function focusComposer() {
  if (!cur) return false;
  if (cur.jsonEd) {
    if (typeof cur.jsonEd.focus === 'function') cur.jsonEd.focus();
    else cur.stateHost.querySelector('textarea, [contenteditable]')?.focus();
  } else cur.textarea?.focus();
  return true;
}

export function toggleQuestionTab() {
  if (!cur || cur.conv.mode !== 'systemone' || !cur.qeditor) return;
  qTab = qTab === 'json' ? 'builder' : 'json';
  setPanelOpen(true);
  try { cur.qeditor.setTab(qTab); } catch (err) { console.warn(err); }
}

export function setQuestionTab(tab) {
  if (!cur || !cur.qeditor) return;
  qTab = tab;
  setPanelOpen(true);
  try { cur.qeditor.setTab(tab); } catch (err) { console.warn(err); }
}

export function isInflight() { return !!inflight; }
export function stopInflight() {
  if (!inflight) return false;
  inflight.controller.abort();
  return true;
}
export function onInflightChange(fn) { inflightListeners.add(fn); return () => inflightListeners.delete(fn); }

export function addImageFiles(files) { return addFiles(files); }

export function slashCtx() {
  return {
    conversation: cur?.conv || null,
    settings: getSettings(),
    composer: { getDraft, loadDraft, submit, clear: clearComposer },
    navigate,
  };
}

// ================================================================== draft helpers
function requestToDraft(turn) {
  const r = turn.request || {};
  const isObj = r.state !== null && typeof r.state === 'object';
  const images = (r.images || []).map((u, i) => {
    const meta = turn.imagesMeta?.[i] || {};
    const dataUrl = typeof u === 'string' ? u : (u && u.base64 ? `data:${u.content_type};base64,${u.base64}` : null);
    return { id: uid('img'), name: meta.name || `image-${i + 1}`, type: meta.type || (dataUrl ? dataUrl.slice(5, dataUrl.indexOf(';')) : ''), bytes: meta.bytes || (dataUrl ? Math.round(dataUrl.length * 0.75) : 0), width: meta.width || 0, height: meta.height || 0, dataUrl };
  });
  return {
    state: isObj ? JSON.stringify(r.state, null, 2) : String(r.state ?? ''),
    stateIsJson: isObj,
    questions: clone(r.questions || {}),
    images,
    options: { model: r.model, steps: r.steps ?? null, samples: r.samples ?? null, think: r.think ?? null, sequential: !!r.sequential },
  };
}

function initialDraft(conv) {
  const d = conv.draft;
  if (d && typeof d === 'object') {
    return {
      state: typeof d.state === 'string' ? d.state : '', stateIsJson: !!d.stateIsJson,
      questions: d.questions && typeof d.questions === 'object' ? d.questions : {},
      images: Array.isArray(d.images) ? d.images.filter((i) => i && i.dataUrl) : [],
      parentTurnId: d.parentTurnId || null,
    };
  }
  if (conv.mode === 'systemone') {
    const last = [...conv.turns].reverse().find((t) => t.kind === 'systemone' && t.request?.questions);
    return { state: '', stateIsJson: false, questions: clone(last ? last.request.questions : STARTER_QUESTIONS), images: [], parentTurnId: null };
  }
  return { state: '', stateIsJson: false, questions: {}, images: [], parentTurnId: null };
}

const persist = debounce(() => {
  if (!cur) return;
  const d = cur.draft;
  saveDraft(cur.conv.id, { state: d.state, stateIsJson: d.stateIsJson, questions: d.questions, images: d.images, parentTurnId: d.parentTurnId || null });
}, 500);

function syncFromEditors() {
  if (!cur) return;
  if (cur.qeditor) {
    try { const q = cur.qeditor.get(); if (q && typeof q === 'object') cur.draft.questions = q; } catch { /* keep last */ }
  }
  if (cur.jsonEd) {
    try {
      const v = cur.jsonEd.get();
      cur.draft.state = JSON.stringify(v, null, 2);
      cur.notices.json = null;
    } catch (err) {
      cur.notices.json = err?.message || 'invalid JSON';
    }
  }
}

// ================================================================== composer-load
async function handleLoad(d) {
  let conv = null;
  const route = currentRoute();
  const curId = cur?.conv?.id || (route.name === 'thread' ? route.params.id : null);
  if (!d.newConversation) {
    if (curId) conv = await getConversation(curId);
    if (!conv) { const last = getLastConvId(); conv = last ? await getConversation(last) : null; }
  }
  const isChatText = d.chat === true;
  if (d.newConversation || !conv || (conv.mode === 'chat' && !isChatText)) {
    conv = await createConversation({ mode: isChatText ? 'chat' : 'systemone', title: d.title });
  } else if (d.title && conv.turns.length === 0) {
    await updateConversation(conv.id, { title: d.title });
  }
  pendingLoad = { ...d, convId: conv.id };
  if (cur && cur.conv.id === conv.id) await applyPending();
  else navigate(`#/c/${encodeURIComponent(conv.id)}`);
}

async function applyPending() {
  const d = pendingLoad;
  if (!cur || !d || d.convId !== cur.conv.id) return;
  pendingLoad = null;
  const dr = cur.draft;
  if ('state' in d && d.state !== undefined) {
    if (typeof d.state === 'string') { dr.state = d.state; if (!('stateIsJson' in d)) dr.stateIsJson = false; }
    else { dr.state = JSON.stringify(d.state, null, 2); if (!('stateIsJson' in d)) dr.stateIsJson = true; }
  }
  if ('stateIsJson' in d) {
    dr.stateIsJson = !!d.stateIsJson;
    if (dr.stateIsJson && d.state && typeof d.state === 'object') dr.state = JSON.stringify(d.state, null, 2);
  }
  if (d.questions && typeof d.questions === 'object') {
    dr.questions = clone(d.questions);
    if (cur.qeditor) { try { cur.qeditor.set(dr.questions); } catch (err) { console.warn(err); } }
    cur.qvalid = J.validateQuestions(dr.questions);
  }
  if (Array.isArray(d.images)) {
    dr.images = d.images.map((i, k) => (typeof i === 'string'
      ? { id: uid('img'), name: `image-${k + 1}`, type: i.slice(5, i.indexOf(';')), bytes: Math.round(i.length * 0.75), width: 0, height: 0, dataUrl: i }
      : { id: i.id || uid('img'), ...i })).slice(0, MAX_IMAGES);
  }
  if (d.options && typeof d.options === 'object' && cur.conv.mode === 'systemone') {
    const base = { model: cur.conv.options.model, steps: null, samples: null, think: null, sequential: false };
    await updateConversation(cur.conv.id, { options: { ...base, ...d.options } });
  }
  if ('parentTurnId' in d) dr.parentTurnId = d.parentTurnId || null;
  else if (d.questions && typeof d.questions === 'object') dr.parentTurnId = null; // a new question set is a new ask, not a re-ask
  if (d.source) cur.source = d.source;
  cur.notices.validation = null;
  cur.notices.json = null;
  renderStateInput();
  renderTray();
  renderChips();
  renderNotices();
  renderPanelHead();
  refreshEstimate();
  persist();
  if (d.highlight && cur.qeditor) {
    setPanelOpen(true);
    try { cur.qeditor.setTab('builder'); cur.qeditor.highlightErrors(d.highlight); } catch (err) { console.warn(err); }
  }
  if (d.submit) await submit({ skipValidation: !!d.skipValidation });
  else focusComposer();
}

// ================================================================== mount
export function mountComposer(el, conv) {
  unmountComposer();
  qTab = qTab || getSettings().questionEditorTab || 'builder';
  const c = {
    el, conv, mode: conv.mode, draft: initialDraft(conv), qeditor: null, jsonEd: null, textarea: null,
    notices: { validation: null, json: null }, qvalid: { valid: true, errors: [] }, offs: [], slash: null, source: null,
  };
  cur = c;
  clear(el);
  el.classList.toggle('composer-chat', conv.mode === 'chat');

  const inner = h('div', { class: 'composer-inner' });
  el.appendChild(inner);

  if (conv.mode === 'systemone') {
    c.panel = h('div', { class: 'qeditor-panel', id: 'qeditor-panel' });
    c.panelHead = h('button', { class: 'qeditor-head', type: 'button', onClick: () => setPanelOpen(!c.panel.classList.contains('open')) });
    c.qhost = h('div', { id: 'qeditor' });
    c.panelBody = h('div', { class: 'qeditor-body' }, c.qhost);
    c.panel.append(c.panelHead, c.panelBody);
    inner.appendChild(c.panel);
    c.qeditor = J.mountQuestionEditor(c.qhost, {
      questions: c.draft.questions,
      onChange: (questions, v) => {
        if (cur !== c) return;
        if (questions && typeof questions === 'object') c.draft.questions = questions;
        c.qvalid = v || { valid: true, errors: [] };
        if (c.qvalid.valid && c.notices.validation) { c.notices.validation = null; renderNotices(); }
        renderPanelHead();
        refreshEstimate();
        persist();
      },
    });
    try { c.qeditor?.setTab?.(qTab); } catch { /* ignore */ }
    c.qvalid = J.validateQuestions(c.draft.questions);
    // on a phone the open editor covers most of the thread: start folded, the header shows the summary
    const narrow = window.matchMedia('(max-width: 600px)').matches;
    setPanelOpen(getLayout().qeditorOpen !== false && (!narrow || conv.turns.length === 0), false);
    renderPanelHead();
  }

  c.box = h('div', { class: 'composer-box' });
  c.noticeEl = h('div', { class: 'composer-notices' });
  c.tray = h('div', { class: 'image-tray' });
  c.stateHost = h('div', { class: 'state-host' });
  c.chips = h('div', { class: 'composer-chips' });
  c.estimate = h('span', { class: 'chip est-chip mono', title: 'Pre-send estimate: JSON chars / 3.6 + 280 per image' });
  c.sendBtn = h('button', { class: 'send-btn', type: 'button', onClick: () => (inflight ? stopInflight() : submit()) });
  c.fileInput = h('input', { type: 'file', accept: ACCEPT.join(','), multiple: true, hidden: true, onChange: (ev) => { addFiles(ev.target.files); ev.target.value = ''; } });
  const bar = h('div', { class: 'composer-bar' }, c.chips, h('span', { class: 'spacer' }), c.estimate, c.sendBtn);
  c.box.append(c.noticeEl, c.tray, c.stateHost, bar, c.fileInput);
  inner.appendChild(c.box);
  inner.appendChild(h('div', { class: 'composer-foot faint' },
    h('span', {}, h('span', { class: 'kbd' }, 'Enter'), ' send · ', h('span', { class: 'kbd' }, 'Shift+Enter'), ' newline · ', h('span', { class: 'kbd' }, '/'), ' commands · ',
      h('span', { class: 'kbd' }, '?'), ' shortcuts'),
    conv.mode === 'systemone' ? h('span', {}, 'answers are read from the model\'s probabilities, never parsed text') : h('span', {}, `${getSettings().chatModel} · streaming`)));

  c.box.addEventListener('paste', onPaste);
  // on the whole composer, question editor included: edit an instruction, Cmd+Enter, done
  inner.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey) && !ev.defaultPrevented) { ev.preventDefault(); submit(); }
  });

  renderStateInput();
  renderTray();
  renderChips();
  renderNotices();
  renderSend();
  refreshEstimate();

  c.offs.push(on('oj:settings-changed', ({ changed }) => {
    if (changed.some((k) => ['pricePerMInput', 'pricePerMOutput', 'currency', 'chatMaxTokens', 'chatModel'].includes(k))) { renderChips(); refreshEstimate(); }
  }));
  c.offs.push(on('oj:health', () => renderChips()));
  c.offs.push(on('oj:conversation-updated', ({ id }) => { if (cur === c && id === c.conv.id) renderChips(); }));
  c.offs.push(onInflightChange(() => renderSend()));

  if (pendingLoad && pendingLoad.convId === conv.id) queueMicrotask(() => applyPending());
  return unmountComposer;
}

export function unmountComposer() {
  if (!cur) return;
  persist.flush();
  const c = cur;
  for (const off of c.offs) off();
  try { c.qeditor?.destroy?.(); } catch { /* ignore */ }
  try { c.jsonEd?.destroy?.(); } catch { /* ignore */ }
  c.slash?.destroy();
  cur = null;
}

// ================================================================== panel
function setPanelOpen(open, save = true) {
  if (!cur?.panel) return;
  cur.panel.classList.toggle('open', open);
  cur.panelHead.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (save) setLayout({ qeditorOpen: open });
}

function renderPanelHead() {
  if (!cur?.panelHead) return;
  const q = cur.draft.questions || {};
  const counts = { noul: 0, choice: 0, score: 0 };
  let n = 0;
  for (const v of Object.values(q)) { n++; if (v && counts[v.type] !== undefined) counts[v.type]++; }
  const invalid = cur.qvalid && !cur.qvalid.valid;
  clear(cur.panelHead);
  append(cur.panelHead,
    icon('chevron-right', 14),
    h('span', { class: 'qh-title' }, 'Questions'),
    h('span', { class: 'qh-sum mono' },
      `${n} question${n === 1 ? '' : 's'}`,
      ...['noul', 'choice', 'score'].filter((t) => counts[t]).map((t) => h('span', { class: `qh-type type-${t}` }, ` · ${t} ${counts[t]}`))),
    invalid ? h('span', { class: 'qh-invalid', title: (cur.qvalid.errors || []).map((e) => `${(e.path || []).join('.')}: ${e.message}`).join('\n') || 'invalid' }) : null,
    h('span', { class: 'spacer' }),
    h('span', { class: 'qh-hint faint' }, h('span', { class: 'kbd' }, navigator.platform?.includes('Mac') ? 'Cmd+J' : 'Ctrl+J'), ' builder / JSON'));
}

// ================================================================== state input
function autogrow(ta) {
  ta.style.height = 'auto';
  ta.style.height = `${Math.min(ta.scrollHeight, Math.round(window.innerHeight * 0.4))}px`;
}

function renderStateInput() {
  if (!cur) return;
  const c = cur;
  try { c.jsonEd?.destroy?.(); } catch { /* ignore */ }
  c.jsonEd = null;
  c.slash?.destroy();
  c.slash = null;
  c.textarea = null;
  clear(c.stateHost);
  const d = c.draft;
  if (c.conv.mode === 'systemone' && d.stateIsJson) {
    let value;
    try { value = d.state.trim() ? JSON.parse(d.state) : {}; } catch {
      value = { text: d.state };
      toast('State was not valid JSON; wrapped it as {"text": …}', { kind: 'warn' });
      d.state = JSON.stringify(value, null, 2);
    }
    const host = h('div', { class: 'state-json' });
    c.stateHost.appendChild(host);
    c.jsonEd = J.mountJsonEditor(host, {
      value, mode: 'text',
      onChange: (v, st) => {
        if (cur !== c) return;
        if (st?.valid) { d.state = JSON.stringify(v, null, 2); c.notices.json = null; }
        else c.notices.json = st?.error || 'invalid JSON';
        renderNotices();
        refreshEstimate();
        persist();
      },
    });
    return;
  }
  const ta = h('textarea', {
    class: 'state-input', rows: 1, spellcheck: 'true',
    placeholder: c.conv.mode === 'chat' ? `Message ${getSettings().chatModel}…` : 'State: the text (or JSON) the questions are about…   type / for commands',
    'aria-label': c.conv.mode === 'chat' ? 'Chat message' : 'State',
  });
  ta.value = d.state;
  c.textarea = ta;
  c.stateHost.appendChild(ta);
  // slash menu first, so its Enter handling wins over send
  c.slash = attachSlashMenu(ta, { host: c.box, getCtx: slashCtx, onRan: () => { autogrow(ta); } });
  ta.addEventListener('input', () => {
    d.state = ta.value;
    autogrow(ta);
    refreshEstimate();
    renderSend();
    persist();
  });
  ta.addEventListener('keydown', async (ev) => {
    if (ev.key !== 'Enter' || ev.isComposing) return;
    if (ev.shiftKey && !(ev.metaKey || ev.ctrlKey)) return;
    ev.preventDefault();
    ev.stopPropagation();
    const m = matchSlash(ta.value);
    if (m) {
      const text = ta.value;
      ta.value = '';
      d.state = '';
      autogrow(ta);
      persist();
      await runSlash(text, slashCtx());
      return;
    }
    submit();
  });
  requestAnimationFrame(() => autogrow(ta));
}

function setStateMode(json) {
  if (!cur) return;
  syncFromEditors();
  if (!json && cur.jsonEd) {
    // leaving JSON: keep pretty text
    try { cur.draft.state = JSON.stringify(cur.jsonEd.get(), null, 2); } catch { /* keep draft text */ }
  }
  cur.draft.stateIsJson = json;
  cur.notices.json = null;
  renderStateInput();
  renderChips();
  renderNotices();
  refreshEstimate();
  persist();
  focusComposer();
}

// ================================================================== images
function readAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}
function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('could not decode image'));
    img.src = src;
  });
}

async function processImage(file) {
  const dataUrl = await readAsDataURL(file);
  const img = await loadImage(dataUrl);
  const ref = { id: uid('img'), name: file.name || `pasted-${Date.now().toString(36)}.png`, type: file.type, bytes: file.size, width: img.naturalWidth, height: img.naturalHeight, dataUrl };
  if (file.size > MAX_IMAGE_BYTES) {
    const scale = Math.min(1, 2048 / Math.max(img.naturalWidth, img.naturalHeight));
    const w = Math.max(1, Math.round(img.naturalWidth * scale));
    const hgt = Math.max(1, Math.round(img.naturalHeight * scale));
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = hgt;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, w, hgt);
    ctx.drawImage(img, 0, 0, w, hgt);
    const out = canvas.toDataURL('image/jpeg', 0.9);
    const bytes = Math.round((out.length - out.indexOf(',') - 1) * 0.75);
    toast(`${ref.name} was ${fmtBytes(file.size)}: re-encoded to JPEG ${w}×${hgt}, ${fmtBytes(bytes)}`, { kind: 'warn', timeout: 5000 });
    Object.assign(ref, { dataUrl: out, type: 'image/jpeg', bytes, width: w, height: hgt, name: ref.name.replace(/\.\w+$/, '') + '.jpg' });
  }
  return ref;
}

async function addFiles(fileList) {
  if (!cur) return;
  const c = cur;
  if (c.conv.mode === 'chat') { toast('Images are for System One decisions; chat mode is text only here', { kind: 'warn' }); return; }
  const files = [...(fileList || [])];
  let added = 0;
  for (const f of files) {
    if (!ACCEPT.includes(f.type)) { toast(`${f.name || 'file'}: only JPEG, PNG, WebP or GIF`, { kind: 'warn' }); continue; }
    if (c.draft.images.length >= MAX_IMAGES) { toast(`At most ${MAX_IMAGES} images per request`, { kind: 'warn' }); break; }
    try {
      const ref = await processImage(f);
      if (cur !== c) return;
      c.draft.images.push(ref);
      added++;
    } catch (err) {
      toast(`${f.name || 'image'}: ${err.message}`, { kind: 'err' });
    }
  }
  if (added) {
    renderTray();
    renderChips();
    refreshEstimate();
    renderSend();
    persist();
  }
}

function onPaste(ev) {
  const files = [...(ev.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
  if (files.length) {
    ev.preventDefault();
    addFiles(files);
  }
}

function renderTray() {
  if (!cur || !cur.tray) return;
  const c = cur;
  clear(c.tray);
  c.tray.hidden = !c.draft.images.length;
  c.draft.images.forEach((img, i) => {
    c.tray.appendChild(h('div', { class: 'thumb', title: `${img.name} · ${img.width}×${img.height}` },
      img.dataUrl ? h('img', { src: img.dataUrl, alt: img.name }) : h('div', { class: 'thumb-missing' }, icon('image', 18)),
      h('div', { class: 'thumb-meta mono' },
        h('span', { class: 'thumb-name' }, img.name),
        h('span', { class: 'faint' }, `${fmtBytes(img.bytes)} · ~${IMAGE_TOKENS_EST} tok`)),
      h('button', {
        class: 'thumb-x', title: 'Remove image', 'aria-label': `Remove ${img.name}`,
        onClick: () => { c.draft.images.splice(i, 1); renderTray(); renderChips(); refreshEstimate(); renderSend(); persist(); },
      }, icon('x', 12))));
  });
}

// ================================================================== chips
function chip(label, { active, title, onClick, cls, iconName } = {}) {
  return h('button', { class: ['chip', active && 'active', cls], type: 'button', title, onClick }, iconName ? icon(iconName, 13) : null, label);
}

function numberPicker(anchor, { title, values, current, min, max, unsetLabel, onPick, explain }) {
  let pop;
  const input = h('input', { class: 'input sm mono', type: 'number', min, max, placeholder: `${min}–${max}`, value: current ?? '' });
  const apply = () => {
    const v = input.value.trim();
    if (v === '') { onPick(null); pop.close(); return; }
    const n = Math.round(Number(v));
    if (!Number.isFinite(n) || n < min || n > max) { input.classList.add('bad'); return; }
    onPick(n);
    pop.close();
  };
  input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); apply(); } });
  const body = h('div', { class: 'picker' },
    h('div', { class: 'picker-title' }, title),
    explain ? h('div', { class: 'picker-explain faint' }, explain) : null,
    h('div', { class: 'picker-grid' },
      h('button', { class: ['chip', (current === null || current === undefined) && 'active'], onClick: () => { onPick(null); pop.close(); } }, unsetLabel),
      values.map((v) => h('button', { class: ['chip mono', current === v && 'active'], onClick: () => { onPick(v); pop.close(); } }, String(v)))),
    h('div', { class: 'row picker-custom' }, input, h('button', { class: 'btn sm', onClick: apply }, 'Set')));
  pop = popover(anchor, body, { placement: 'above' });
  setTimeout(() => input.focus(), 20);
}

function setOption(key, value) {
  if (!cur) return;
  const options = { ...cur.conv.options, [key]: value };
  updateConversation(cur.conv.id, { options });
}

function modelNames() {
  const models = (getLastHealth()?.models || []).map((m) => m.name).filter((n) => n && n !== 'diffusiongemma-26b');
  return models;
}

function renderChips() {
  if (!cur || !cur.chips) return;
  const c = cur;
  clear(c.chips);
  if (c.conv.mode === 'chat') return renderChatChips();
  const o = c.conv.options || {};
  const s = getSettings();

  c.chips.appendChild(h('button', { class: 'icon-btn attach-btn', title: 'Attach images (or paste / drop)', 'aria-label': 'Attach images', onClick: () => c.fileInput.click() }, icon('paperclip', 16)));
  c.chips.appendChild(h('div', { class: 'seg', role: 'group', 'aria-label': 'State format' },
    h('button', { class: ['seg-btn', !c.draft.stateIsJson && 'active'], onClick: () => c.draft.stateIsJson && setStateMode(false) }, 'Text'),
    h('button', { class: ['seg-btn', c.draft.stateIsJson && 'active'], onClick: () => !c.draft.stateIsJson && setStateMode(true) }, 'JSON')));

  const modelChip = chip(h('span', {}, o.model || s.defaultModel, ' ', icon('chevron-down', 11)), {
    cls: 'mono model-chip', title: 'Model for this conversation',
    onClick: (ev) => {
      const names = modelNames();
      if (o.model && !names.includes(o.model)) names.push(o.model);
      const descs = Object.fromEntries((getLastHealth()?.models || []).map((m) => [m.name, m.description]));
      let pop;
      const custom = h('input', { class: 'input sm mono', placeholder: 'custom model id', onKeydown: (e) => { if (e.key === 'Enter' && custom.value.trim()) { setOption('model', custom.value.trim()); pop.close(); } } });
      pop = popover(ev.currentTarget, h('div', { class: 'picker' },
        h('div', { class: 'picker-title' }, 'Model'),
        menuList(names.length ? names.map((n) => ({ label: n, hint: descs[n] ? descs[n].slice(0, 48) : '', active: n === o.model, onClick: () => setOption('model', n) })) : [{ label: 'no models (OpenJev down?)', disabled: true }], () => pop.close()),
        h('div', { class: 'row picker-custom' }, custom)), { placement: 'above' });
    },
  });
  c.chips.appendChild(modelChip);

  c.chips.appendChild(chip(o.steps ? `steps ${o.steps}` : 'steps', {
    active: o.steps !== null && o.steps !== undefined, cls: 'mono', title: 'Denoise steps per read (1–8, default 1)',
    onClick: (ev) => numberPicker(ev.currentTarget, { title: 'steps', explain: 'Denoise steps per read. More steps let the answers settle against each other. Same tokens, more GPU time.', values: [1, 2, 3, 4, 5, 6, 7, 8], current: o.steps, min: 1, max: 8, unsetLabel: 'default', onPick: (v) => setOption('steps', v) }),
  }));
  c.chips.appendChild(chip(o.samples ? `samples ${o.samples}` : 'samples', {
    active: o.samples !== null && o.samples !== undefined, cls: 'mono', title: 'Read N times with different noise and average (1–32)',
    onClick: (ev) => numberPicker(ev.currentTarget, { title: 'samples', explain: 'Read N times with different noise and average. Replaces the automatic re-reads; samples 1 is the fastest answer. N × input tokens.', values: [1, 2, 4, 8, 16, 32], current: o.samples, min: 1, max: 32, unsetLabel: 'default', onPick: (v) => setOption('samples', v) }),
  }));
  c.chips.appendChild(chip(o.think !== null && o.think !== undefined ? `think ${o.think}` : 'think', {
    active: o.think !== null && o.think !== undefined, cls: 'mono', title: 'Write a thought first (0–4096 tokens), then read the answers',
    onClick: (ev) => numberPicker(ev.currentTarget, { title: 'think', explain: 'The model writes a thought, then reads the answers after it. Hard cap in tokens; give multi-step problems 512 or more. Input tokens twice, plus the thought as output tokens.', values: [0, 128, 256, 512, 1024, 2048, 4096], current: o.think, min: 0, max: 4096, unsetLabel: 'off', onPick: (v) => setOption('think', v) }),
  }));
  c.chips.appendChild(chip('sequential', {
    active: !!o.sequential, cls: 'mono', title: 'Read question chunks in order; each chunk sees the answers before it',
    onClick: () => setOption('sequential', !o.sequential),
  }));
  const hasImages = c.draft.images.length > 0;
  if (hasImages && ((o.think !== null && o.think !== undefined) || o.sequential)) {
    c.chips.appendChild(h('span', { class: 'chip warn-chip', title: 'The server returns 400 for images combined with think or sequential. Send anyway to see the error.' }, icon('alert', 13), 'images + ', o.sequential ? 'sequential' : 'think', ' → 400'));
  }
}

function renderChatChips() {
  const c = cur;
  const s = getSettings();
  const o = chatOptions(c.conv);
  const setChat = (patch) => updateConversation(c.conv.id, { chatOptions: { ...o, ...patch } });
  c.chips.appendChild(h('span', { class: 'chip mono static-chip', title: 'Text generation model' }, icon('chat', 13), s.chatModel));
  c.chips.appendChild(chip(`max_tokens ${o.maxTokens}`, {
    cls: 'mono', active: true, title: 'Cap on generated tokens (default 1024, cap 8192)',
    onClick: (ev) => numberPicker(ev.currentTarget, { title: 'max_tokens', values: [128, 256, 512, 1024, 2048, 4096, 8192], current: o.maxTokens, min: 1, max: getCachedConfig()?.limits?.chatMaxTokensCap || 8192, unsetLabel: 'default', onPick: (v) => setChat({ maxTokens: v ?? s.chatMaxTokens }) }),
  }));
  c.chips.appendChild(chip('JSON mode', { active: !!o.jsonMode, cls: 'mono', iconName: 'json', title: 'Send response_format: {type: "json_object"}', onClick: () => setChat({ jsonMode: !o.jsonMode }) }));
  const sys = (c.conv.system || '').trim();
  c.chips.appendChild(chip(sys ? 'system prompt · set' : 'system prompt', {
    active: !!sys, cls: 'mono', title: sys || 'No system prompt',
    onClick: () => {
      const ta = h('textarea', { class: 'textarea mono', rows: 10, value: c.conv.system || '', placeholder: 'You are a concise assistant…' });
      openModal({
        title: 'System prompt',
        body: h('div', { class: 'col' }, h('p', { class: 'muted' }, 'Sent as the first message on every turn of this chat.'), ta),
        actions: [{ label: 'Cancel', kind: 'ghost' }, { label: 'Save', kind: 'primary', onClick: () => updateConversation(c.conv.id, { system: ta.value }) }],
      });
    },
  }));
}

// ================================================================== estimate / notices / send button
function refreshEstimate() {
  if (!cur || !cur.estimate) return;
  const c = cur;
  const s = getSettings();
  let est;
  if (c.conv.mode === 'chat') {
    const body = buildChatBody(c.conv, null, c.draft.state || '');
    est = Math.ceil(JSON.stringify(body.messages).length / 3.6);
  } else {
    let state = c.draft.state;
    if (c.draft.stateIsJson) { try { state = JSON.parse(c.draft.state); } catch { /* raw */ } }
    est = estimateTokens({ state, questions: c.draft.questions, images: c.draft.images });
  }
  const cost = costOf({ input_tokens: est, output_tokens: 0 }, s);
  c.estimate.textContent = `≈ ${fmtInt(est)} tok${cost > 0 ? ` · ${fmtCost(cost, s.currency)}` : ''}`;
}

function renderNotices() {
  if (!cur || !cur.noticeEl) return;
  const c = cur;
  clear(c.noticeEl);
  const d = c.draft;
  if (d.parentTurnId) {
    const idx = c.conv.turns.findIndex((t) => t.id === d.parentTurnId);
    c.noticeEl.appendChild(h('div', { class: 'notice notice-info' },
      icon('corner-down-right', 14),
      h('span', {}, `re-asking #${idx >= 0 ? idx + 1 : '?'} with edits: the result will show Δ against it`),
      h('span', { class: 'spacer' }),
      h('button', { class: 'icon-btn sm', title: 'Clear re-ask link', onClick: () => { d.parentTurnId = null; renderNotices(); persist(); } }, icon('x', 12))));
  }
  if (c.notices.json) {
    c.noticeEl.appendChild(h('div', { class: 'notice notice-err mono' }, icon('alert', 14), h('span', {}, `State JSON: ${c.notices.json}`)));
  }
  const v = c.notices.validation;
  if (v && v.length) {
    c.noticeEl.appendChild(h('div', { class: 'notice notice-warn' },
      h('div', { class: 'notice-main' },
        h('div', { class: 'row' }, icon('alert', 14), h('strong', {}, `The question set has ${v.length} problem${v.length === 1 ? '' : 's'}`)),
        h('ul', { class: 'notice-list mono' }, v.slice(0, 8).map((e) => h('li', {}, h('span', { class: 'faint' }, (e.path || []).join(' › ') || '(questions)'), ` ${e.message}`))),
        v.length > 8 ? h('div', { class: 'faint' }, `…and ${v.length - 8} more`) : null),
      h('div', { class: 'notice-actions col' },
        h('button', { class: 'btn sm danger', title: 'Send the body unchanged, to explore the server\'s 400/422', onClick: () => sendSystemOne({ skipValidation: true }) }, 'Send anyway'),
        h('button', { class: 'btn sm ghost', onClick: () => { c.notices.validation = null; renderNotices(); } }, 'Dismiss'))));
  }
}

function renderSend() {
  if (!cur || !cur.sendBtn) return;
  const c = cur;
  clear(c.sendBtn);
  if (inflight) {
    c.sendBtn.classList.add('stop');
    c.sendBtn.disabled = false;
    c.sendBtn.title = 'Stop (Esc)';
    c.sendBtn.setAttribute('aria-label', 'Stop');
    c.sendBtn.appendChild(icon('stop', 16));
  } else {
    c.sendBtn.classList.remove('stop');
    const empty = !String(c.draft.state || '').trim() && !(c.draft.images || []).length;
    c.sendBtn.disabled = empty && c.conv.mode === 'chat';
    c.sendBtn.title = 'Send (Enter)';
    c.sendBtn.setAttribute('aria-label', 'Send');
    c.sendBtn.appendChild(icon('send', 18));
  }
}

function setInflight(v) {
  inflight = v;
  for (const fn of inflightListeners) { try { fn(!!v); } catch { /* ignore */ } }
}

// ================================================================== sending
async function sendSystemOne({ skipValidation = false } = {}) {
  if (!cur) return;
  if (inflight) { toast('A request is in flight. Esc stops it.', { kind: 'warn' }); return; }
  const c = cur;
  syncFromEditors();
  if (c.notices.json) { renderNotices(); return; }
  let body;
  try {
    body = buildSystemOneBody(c.draft, c.conv.options);
  } catch (err) {
    c.notices.json = err?.message || String(err);
    renderNotices();
    return;
  }
  if (!skipValidation) {
    const v = J.validateQuestions(body.questions);
    if (!v.valid) {
      c.notices.validation = v.errors?.length ? v.errors : [{ path: [], message: 'invalid question set' }];
      renderNotices();
      setPanelOpen(true);
      return;
    }
  }
  c.notices.validation = null;
  // An open question editor plus the composer can cover half the thread, so the answer lands
  // behind it. Fold it for this send (not saved: the next conversation opens it again); the
  // header or Cmd+J brings it back.
  const wrapH = c.el.closest('.thread-wrap')?.clientHeight || window.innerHeight;
  if (c.panel?.classList.contains('open') && c.el.offsetHeight > wrapH * 0.42) setPanelOpen(false, false);
  const parentTurnId = c.draft.parentTurnId || null;
  const turn = {
    id: uid('t'), kind: 'systemone', createdAt: Date.now(), status: 'pending',
    source: c.source || 'thread', label: null, parentTurnId,
    request: body,
    imagesMeta: c.draft.images.map(({ name, type, bytes, width, height }) => ({ name, type, bytes, width, height })),
    response: null, http: null, error: null,
    bodyHash: await sha256Hex(JSON.stringify(body)),
    labels: {},
  };
  c.source = null;
  c.draft.parentTurnId = null;
  if (getSettings().clearStateOnSend) {
    c.draft.state = '';
    c.draft.images = [];
    renderStateInput();
    renderTray();
  }
  renderNotices();
  renderChips();
  refreshEstimate();
  persist();
  await appendTurn(c.conv.id, turn);
  await runSystemOneTurn(c.conv.id, turn);
}

async function runSystemOneTurn(convId, turn) {
  const controller = new AbortController();
  setInflight({ controller, convId, turnId: turn.id, kind: 'systemone' });
  let r;
  try {
    r = await systemOne(turn.request, { signal: controller.signal, source: turn.source || 'thread', convId, turnId: turn.id, label: turn.label, bodyHash: turn.bodyHash });
  } finally {
    setInflight(null);
  }
  await updateTurn(convId, turn.id, {
    status: r.ok ? 'ok' : r.error?.kind === 'aborted' ? 'aborted' : 'error',
    response: r.ok ? r.data : null,
    http: r.http,
    error: r.ok ? null : r.error,
  });
  return r;
}

async function sendChat() {
  if (!cur) return;
  if (inflight) { toast('A reply is streaming. Esc stops it.', { kind: 'warn' }); return; }
  const c = cur;
  const text = String(c.draft.state || '').trim();
  if (!text) return;
  const body = buildChatBody(c.conv, null, text);
  const turn = {
    id: uid('t'), kind: 'chat', createdAt: Date.now(), status: 'pending', source: 'thread',
    user: text, assistant: '', finishReason: null, request: body, usage: null, http: null, error: null,
  };
  c.draft.state = '';
  if (c.textarea) { c.textarea.value = ''; autogrow(c.textarea); }
  refreshEstimate();
  persist();
  await appendTurn(c.conv.id, turn);
  await runChat(c.conv.id, turn);
}

async function runChat(convId, turn) {
  const controller = new AbortController();
  setInflight({ controller, convId, turnId: turn.id, kind: 'chat' });
  try {
    await runChatTurn(convId, turn, { signal: controller.signal, controller });
  } finally {
    setInflight(null);
  }
}

// ================================================================== turn actions (used by the thread)
export function editAndReask(turn) {
  return handleLoad({ ...requestToDraft(turn), parentTurnId: turn.id });
}

export function fixInEditor(turn) {
  return handleLoad({ ...requestToDraft(turn), parentTurnId: turn.id, highlight: turn.error?.details || [] });
}

export async function rerunTurn(convId, turn) {
  if (inflight) { toast('A request is in flight. Esc stops it.', { kind: 'warn' }); return; }
  if (turn.kind === 'chat') return regenerateChat(convId, turn.id);
  const t = {
    id: uid('t'), kind: 'systemone', createdAt: Date.now(), status: 'pending', source: 'thread', label: 'rerun',
    parentTurnId: null, request: clone(turn.request), imagesMeta: clone(turn.imagesMeta || []),
    response: null, http: null, error: null, bodyHash: turn.bodyHash || await sha256Hex(JSON.stringify(turn.request)), labels: {},
  };
  await appendTurn(convId, t);
  await runSystemOneTurn(convId, t);
}

export async function retryTurn(convId, turnId) {
  if (inflight) { toast('A request is in flight. Esc stops it.', { kind: 'warn' }); return; }
  const conv = await getConversation(convId);
  const turn = conv?.turns.find((t) => t.id === turnId);
  if (!turn) return;
  if (turn.kind === 'chat') return regenerateChat(convId, turnId);
  await updateTurn(convId, turnId, { status: 'pending', response: null, http: null, error: null, createdAt: Date.now() });
  await runSystemOneTurn(convId, turn);
}

export async function regenerateChat(convId, turnId) {
  if (inflight) { toast('A reply is streaming. Esc stops it.', { kind: 'warn' }); return; }
  const conv = await getConversation(convId);
  const turn = conv?.turns.find((t) => t.id === turnId);
  if (!turn) return;
  const body = buildChatBody(conv, turn.id, turn.user);
  await updateTurn(convId, turnId, { status: 'pending', request: body, assistant: '', finishReason: null, usage: null, http: null, error: null, createdAt: Date.now() });
  await runChat(convId, turn);
}

// Called on thread mount: a turn left 'pending' by a reload can never finish.
export function isTurnInflight(turnId) { return !!inflight && inflight.turnId === turnId; }

// ================================================================== init
let inited = false;
export function initComposer() {
  if (inited) return;
  inited = true;
  on('oj:composer-load', (d) => { handleLoad(d || {}); });
  // the draft save is debounced 500 ms: write it now if the tab closes or reloads
  window.addEventListener('pagehide', () => { if (cur) { persist.flush(); flushPendingWrites(); } });
}

export function hasComposer() { return !!cur; }
export function composerConvId() { return cur?.conv?.id || null; }

// app/chat.js — diffusiongemma-26b chat mode (builder B): message building, SSE streaming
// into the live turn node (throttled 50 ms Markdown via marked + DOMPurify, or an escaped
// fallback), the live ttft / tok/s counter, JSON-mode tree view and code-block copy buttons.

import { h, clear, copyText } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { chatStream } from '/js/core/api.js';
import { getSettings, updateTurn, getConversation } from '/js/core/store.js';
import { fmtMs, fmtInt } from '/js/core/format.js';
import { J } from '/js/app/jev.js';

const CDN_MARKED = 'https://cdn.jsdelivr.net/npm/marked@18.0.14/lib/marked.esm.js';
const CDN_PURIFY = 'https://cdn.jsdelivr.net/npm/dompurify@3.4.16/dist/purify.es.mjs';

let md = null;       // {marked, purify}
let mdPromise = null;

function withTimeout(p, ms) {
  return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(`timed out after ${ms} ms`)), ms))]);
}

export function loadMarkdown() {
  if (mdPromise) return mdPromise;
  mdPromise = (async () => {
    try {
      const [m, p] = await Promise.all([withTimeout(import(CDN_MARKED), 6000), withTimeout(import(CDN_PURIFY), 6000)]);
      const marked = m.marked || m.default;
      const purify = p.default || p.DOMPurify || p;
      if (!marked || typeof purify?.sanitize !== 'function') throw new Error('unexpected module shape');
      try { marked.setOptions?.({ gfm: true, breaks: true }); } catch { /* ignore */ }
      md = { marked, purify };
    } catch (err) {
      console.warn('[chat] Markdown libraries unavailable, using plain rendering', err);
      md = null;
    }
    return md;
  })();
  return mdPromise;
}

function addCopyButtons(root) {
  for (const pre of root.querySelectorAll('pre')) {
    if (pre.querySelector('.code-copy')) continue;
    pre.classList.add('code-block');
    const code = pre.querySelector('code');
    const lang = (code?.className || '').match(/language-(\S+)/)?.[1];
    pre.appendChild(h('div', { class: 'code-tools' },
      lang ? h('span', { class: 'code-lang mono' }, lang) : null,
      h('button', { class: 'code-copy', title: 'Copy code', onClick: () => copyText((code || pre).textContent) }, icon('copy', 13))));
  }
}

function plainRender(el, text) {
  clear(el);
  const parts = String(text).split(/```/);
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      const nl = part.indexOf('\n');
      const lang = nl > 0 ? part.slice(0, nl).trim() : '';
      const body = nl >= 0 ? part.slice(nl + 1) : part;
      el.appendChild(h('pre', {}, h('code', { class: lang ? `language-${lang}` : null }, body)));
    } else if (part) {
      for (const para of part.split(/\n{2,}/)) if (para.trim()) el.appendChild(h('p', { class: 'pre-wrap' }, para));
    }
  });
}

export function renderMarkdown(el, text) {
  const t = String(text ?? '');
  if (md) {
    try {
      el.innerHTML = md.purify.sanitize(md.marked.parse(t, { async: false }));
      for (const a of el.querySelectorAll('a')) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    } catch { plainRender(el, t); }
  } else plainRender(el, t);
  addCopyButtons(el);
}

// ------------------------------------------------------------------ live streams
const live = new Map(); // turnId → {text, t0, ttftMs, chunks, usage, timer, controller}

export function isStreaming(turnId) { return live.has(turnId); }
export function liveState(turnId) { return live.get(turnId) || null; }

function liveCounterText(st) {
  const now = performance.now();
  const parts = [];
  parts.push(st.ttftMs !== null ? `ttft ${fmtMs(st.ttftMs)}` : `waiting ${fmtMs(now - st.t0)}`);
  const toks = st.usage?.completion_tokens ?? st.chunks;
  if (st.ttftMs !== null) {
    // diffusion decoding can land the whole reply in one burst: rate over the full
    // elapsed time stays meaningful where a post-first-token window would not
    const all = (now - st.t0) / 1000;
    if (all > 0.05) parts.push(`${(toks / all).toFixed(1)} tok/s e2e`);
  }
  parts.push(`${st.usage ? '' : '~'}${fmtInt(toks)} tok`);
  return parts.join(' · ');
}

function paint(turnId) {
  const st = live.get(turnId);
  if (!st) return;
  const node = document.querySelector(`[data-turn-id="${CSS.escape(turnId)}"]`);
  if (!node) return;
  const body = node.querySelector('.chat-md');
  if (body) {
    renderMarkdown(body, st.text);
    body.appendChild(h('span', { class: 'caret' }));
  }
  const counter = node.querySelector('.chat-live');
  if (counter) counter.textContent = liveCounterText(st);
}

function schedulePaint(turnId) {
  const st = live.get(turnId);
  if (!st || st.timer) return;
  st.timer = setTimeout(() => { st.timer = null; paint(turnId); }, 50);
}

// ------------------------------------------------------------------ messages + run
export function buildMessages(conv, uptoTurnId, userText) {
  const msgs = [];
  const sys = (conv.system ?? '').trim();
  if (sys) msgs.push({ role: 'system', content: sys });
  for (const t of conv.turns) {
    if (t.id === uptoTurnId) break;
    if (t.kind !== 'chat' || t.status !== 'ok') continue;
    msgs.push({ role: 'user', content: t.user });
    msgs.push({ role: 'assistant', content: t.assistant || '' });
  }
  msgs.push({ role: 'user', content: userText });
  return msgs;
}

export function chatOptions(conv) {
  const s = getSettings();
  return { maxTokens: s.chatMaxTokens || 1024, jsonMode: false, ...(conv.chatOptions || {}) };
}

export function buildChatBody(conv, uptoTurnId, userText) {
  const s = getSettings();
  const o = chatOptions(conv);
  const body = { model: s.chatModel || 'diffusiongemma-26b', messages: buildMessages(conv, uptoTurnId, userText), max_tokens: Number(o.maxTokens) || 1024, stream: true };
  if (o.jsonMode) body.response_format = { type: 'json_object' };
  return body;
}

// Streams `turn.request` into the turn; resolves when the turn is final.
export async function runChatTurn(convId, turn, { signal, controller } = {}) {
  const st = { text: '', t0: performance.now(), ttftMs: null, chunks: 0, usage: null, timer: null, controller };
  live.set(turn.id, st);
  paint(turn.id);
  const ticker = setInterval(() => {
    const node = document.querySelector(`[data-turn-id="${CSS.escape(turn.id)}"] .chat-live`);
    if (node) node.textContent = liveCounterText(st);
  }, 250);
  const r = await chatStream(turn.request, {
    signal,
    onDelta: (_d, full) => {
      if (st.ttftMs === null) st.ttftMs = performance.now() - st.t0;
      st.text = full;
      st.chunks++;
      schedulePaint(turn.id);
    },
    onUsage: (u) => { st.usage = u; },
  }, { convId, turnId: turn.id });
  clearInterval(ticker);
  clearTimeout(st.timer);
  live.delete(turn.id);
  const aborted = r.error?.kind === 'aborted';
  await updateTurn(convId, turn.id, {
    status: r.ok ? 'ok' : aborted ? 'aborted' : 'error',
    assistant: r.text,
    finishReason: r.finishReason,
    usage: r.usage,
    http: r.http,
    error: r.ok ? null : r.error,
  });
  return r;
}

// ------------------------------------------------------------------ rendering
function extractJson(text) {
  const t = String(text || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
  try { return { ok: true, value: JSON.parse(t) }; } catch (err) { return { ok: false, error: err }; }
}

const editors = new Map(); // turnId → json editor handle

export function destroyChatExtras(turnId) {
  const ed = editors.get(turnId);
  if (ed) { try { ed.destroy?.(); } catch { /* ignore */ } editors.delete(turnId); }
}

// The assistant side of a chat turn (without meta/toolbar; the thread adds those).
export function renderChatAssistant(turn) {
  const wrap = h('div', { class: 'chat-assistant' });
  const st = live.get(turn.id);
  if (turn.status === 'pending') {
    const body = h('div', { class: 'chat-md md' });
    if (st && st.text) renderMarkdown(body, st.text);
    body.appendChild(h('span', { class: 'caret' }));
    wrap.append(body, h('div', { class: 'chat-live mono' }, st ? liveCounterText(st) : 'connecting…'));
    return wrap;
  }
  const text = turn.assistant || '';
  const jsonMode = !!turn.request?.response_format;
  if (jsonMode && turn.status === 'ok') {
    const parsed = extractJson(text);
    if (parsed.ok) {
      const host = h('div', { class: 'chat-json' });
      wrap.append(h('div', { class: 'chat-json-head faint mono' }, icon('json', 13), 'JSON mode · parsed'), host);
      queueMicrotask(() => {
        destroyChatExtras(turn.id);
        try { editors.set(turn.id, J.mountJsonEditor(host, { value: parsed.value, mode: 'tree', readOnly: true })); } catch (err) { console.warn(err); }
      });
      return wrap;
    }
    wrap.appendChild(h('div', { class: 'chat-json-head warn-text mono' }, icon('alert', 13), `JSON mode · not valid JSON (${parsed.error?.message || 'parse error'})`));
  }
  const body = h('div', { class: 'chat-md md' });
  if (text) renderMarkdown(body, text);
  else if (turn.status === 'ok') body.appendChild(h('p', { class: 'muted' }, '(empty reply)'));
  wrap.appendChild(body);
  if (turn.status === 'aborted') wrap.appendChild(h('div', { class: 'muted turn-stopped' }, icon('stop', 12), 'Stopped'));
  if (turn.finishReason === 'length') wrap.appendChild(h('div', { class: 'warn-text mono chat-note' }, `finish: length (max_tokens ${turn.request?.max_tokens ?? ''} reached)`));
  return wrap;
}

// B-private: used by /rerun and Regenerate
export async function conversationFor(convId) { return getConversation(convId); }

// app/thread.js — the conversation thread view (builder B): System One decision cards (v2) or
// user bubbles + assistant blocks (classic), pending skeleton, error card, C's renderResult /
// chat Markdown, C's meta bar, hover toolbars, per-turn replacement on oj:turn-updated,
// autoscroll, and image drag & drop.

import { h, clear, copyText, debounce, download } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { on, emit } from '/js/core/bus.js';
import { toast } from '/js/core/toast.js';
import { registerView, navigate } from '/js/core/router.js';
import { renderError } from '/js/core/errors.js';
import { getCachedConfig } from '/js/core/api.js';
import { getSettings, peekConversation, setLastConvId, updateTurn, deleteTurn, exportConversations } from '/js/core/store.js';
import { fmtMs, fmtClock, fmtRelTime, fmtBytes } from '/js/core/format.js';
import { J, rubricQuestions } from '/js/app/jev.js';
import { renderChatAssistant, destroyChatExtras } from '/js/app/chat.js';
import { renderWelcome } from '/js/app/welcome.js';
import {
  mountComposer, editAndReask, fixInEditor, rerunTurn, retryTurn, regenerateChat, isTurnInflight, addImageFiles, stopInflight,
  focusComposer, openConversationInBatch,
} from '/js/app/composer.js';

let statsOpen = false;

function stateView(state) {
  if (state !== null && typeof state === 'object') {
    return h('pre', { class: 'bubble-json mono' }, JSON.stringify(state, null, 2));
  }
  const s = String(state ?? '');
  const long = s.length > 900;
  const el = h('div', { class: ['bubble-text', long && 'clamped'] }, s || h('span', { class: 'faint' }, '(empty state)'));
  if (!long) return el;
  const more = h('button', { class: 'link-btn', onClick: () => { el.classList.toggle('clamped'); more.textContent = el.classList.contains('clamped') ? 'show more' : 'show less'; } }, 'show more');
  return h('div', {}, el, more);
}

function optionBadges(req) {
  const out = [];
  if (req.steps != null) out.push(`steps ${req.steps}`);
  if (req.samples != null) out.push(`samples ${req.samples}`);
  if (req.think != null) out.push(`think ${req.think}`);
  if (req.sequential) out.push('seq');
  return out.map((t) => h('span', { class: 'badge opt-badge mono' }, t));
}

function scrollToTurn(id) {
  const node = document.getElementById(`turn-${id}`);
  if (!node) return;
  node.scrollIntoView({ behavior: 'smooth', block: 'center' });
  node.classList.add('flash');
  setTimeout(() => node.classList.remove('flash'), 1200);
}

function toolbar(items) {
  return h('div', { class: 'turn-toolbar' }, items.filter(Boolean).map((it) => h('button', {
    class: ['tb-btn', it.danger && 'danger'], title: it.title || it.label, 'aria-label': it.title || it.label, onClick: it.onClick,
  }, icon(it.icon, 14), it.showLabel ? h('span', {}, it.label) : null)));
}

function pendingBlock(turn) {
  const req = turn.request || {};
  const q = Object.keys(req.questions || {}).length;
  const what = req.think ? `thinking (≤ ${req.think} tok), then reading ${q} answer${q === 1 ? '' : 's'}` :
    `reading ${q} answer slot${q === 1 ? '' : 's'}${req.sequential ? ' in sequence' : ''}`;
  const elapsed = h('span', { class: 'mono elapsed' }, '0.0 s');
  const block = h('div', { class: 'pending-block' },
    h('div', { class: 'pending-head' }, h('span', { class: 'pulse-dot' }), h('span', { class: 'muted' }, what), h('span', { class: 'spacer' }), elapsed,
      h('button', { class: 'btn sm ghost', title: 'Stop (Esc)', onClick: () => stopInflight() }, icon('stop', 12), 'Stop')),
    h('div', { class: 'skeleton sk-line w60' }), h('div', { class: 'skeleton sk-line w90' }), h('div', { class: 'skeleton sk-line w40' }));
  const t0 = turn.createdAt || Date.now();
  let seen = false;
  const timer = setInterval(() => {
    if (block.isConnected) seen = true;
    else if (seen) { clearInterval(timer); return; }
    const ms = Date.now() - t0;
    elapsed.textContent = ms < 60000 ? `${(ms / 1000).toFixed(1)} s` : fmtMs(ms);
  }, 100);
  return block;
}

// ------------------------------------------------------------------ turn nodes
// System One turns come in two layouts (settings.threadLayout): 'v2' decision cards, where the
// state and its answers share one card (one region to screenshot or copy), and 'classic' chat
// bubbles. Both are built from the same pieces below.
function turnTopRow(conv, turn, idx) {
  const parentIdx = turn.parentTurnId ? conv.turns.findIndex((t) => t.id === turn.parentTurnId) : -1;
  return [
    h('span', { class: 'turn-no' }, `#${idx + 1}`),
    h('span', {}, fmtClock(turn.createdAt)),
    turn.source && turn.source !== 'thread' ? h('span', { class: 'badge src-badge' }, turn.source) : null,
    turn.label ? h('span', { class: 'badge label-badge' }, turn.label) : null,
    turn.parentTurnId ? h('button', { class: 'reask-link', onClick: () => scrollToTurn(turn.parentTurnId) }, icon('corner-down-right', 12), parentIdx >= 0 ? `re-ask of #${parentIdx + 1}` : 're-ask') : null,
  ];
}

function imagesRow(turn) {
  const images = Array.isArray(turn.request?.images) ? turn.request.images : [];
  return images.length ? h('div', { class: 'bubble-images' }, images.map((src, i) => {
    const meta = turn.imagesMeta?.[i] || {};
    return typeof src === 'string' && src
      ? h('img', { class: 'bubble-img', src, alt: meta.name || `image ${i + 1}`, title: `${meta.name || ''} ${meta.width ? `${meta.width}×${meta.height}` : ''} ${meta.bytes ? fmtBytes(meta.bytes) : ''}` })
      : h('div', { class: 'bubble-img missing', title: 'image data stripped' }, icon('image', 16));
  })) : null;
}

function qchipsRow(req) {
  return h('div', { class: 'qchips' }, Object.entries(req.questions || {}).map(([qid, q]) => h('span', { class: `qchip type-${q?.type || 'unknown'}`, title: `${q?.type || '?'}${typeof q?.instructions === 'string' ? `: ${q.instructions}` : ''}` }, qid)));
}

function modelBadges(req) {
  return [h('span', { class: 'badge opt-badge mono model-badge' }, req.model || ''), optionBadges(req)];
}

/** The answer area: pending skeleton, error card, stopped line, or C's renderResult. */
function contentFor(conv, turn, ctx) {
  if (turn.status === 'pending') return pendingBlock(turn);
  if (turn.status === 'error') {
    return renderError(turn.error, {
      requestBytes: turn.http?.requestBytes,
      autoRetry: turn.autoRetried ? false : undefined,
      onRetry: async (_e, opts) => {
        if (opts?.auto) await updateTurn(conv.id, turn.id, { autoRetried: true });
        retryTurn(conv.id, turn.id);
      },
      onFix: () => fixInEditor(turn),
      onEdit: () => editAndReask(turn),
    });
  }
  if (turn.status === 'aborted') {
    return h('div', { class: 'turn-stopped muted' }, icon('stop', 12), h('span', {}, 'Stopped'),
      h('button', { class: 'btn sm ghost', onClick: () => retryTurn(conv.id, turn.id) }, icon('refresh', 12), 'Retry'));
  }
  try { return J.renderResult(turn, ctx); } catch (err) { return h('pre', { class: 'mono' }, String(err)); }
}

function metaFor(turn, ctx) {
  try { return J.renderTurnMeta(turn, ctx); } catch (err) { console.warn(err); return null; }
}

function toolbarFor(conv, turn, idx) {
  return toolbar([
    { icon: 'edit', label: 'Edit & re-ask', showLabel: true, onClick: () => editAndReask(turn) },
    turn.status === 'ok' ? { icon: 'copy', label: 'Copy as text', title: 'Copy prompt + answers as Markdown', onClick: () => copyText(J.turnToMarkdown(turn, { turnIndex: idx })) } : null,
    { icon: 'refresh', label: 'Rerun', title: 'Rerun the identical body (reproducibility check)', onClick: () => rerunTurn(conv.id, turn) },
    { icon: 'compare', label: 'Compare…', title: 'Compare variants of this request', onClick: () => navigate(`#/compare/${encodeURIComponent(conv.id)}/${encodeURIComponent(turn.id)}`) },
    { icon: 'inspect', label: 'Inspect', title: 'Inspect request / response / timing (Cmd+I)', onClick: () => emit('oj:open-inspector', { convId: conv.id, turnId: turn.id }) },
    { icon: 'terminal', label: 'Copy curl', onClick: () => copyText(J.buildSnippet('curl', turn, getCachedConfig())) },
    { icon: 'code', label: 'Copy Python', onClick: () => copyText(J.buildSnippet('python', turn, getCachedConfig())) },
    turn.response ? { icon: 'json', label: 'Copy JSON response', onClick: () => copyText(JSON.stringify(turn.response, null, 2)) } : null,
    { icon: 'trash', label: 'Delete turn', danger: true, onClick: () => deleteTurn(conv.id, turn.id).then(() => toast('Turn deleted')) },
  ]);
}

function turnCtx(conv, turn, idx) {
  const parentTurn = turn.parentTurnId ? conv.turns.find((t) => t.id === turn.parentTurnId) || null : null;
  return { conversation: conv, settings: getSettings(), turnIndex: idx, parentTurn, compact: false };
}

function classicSystemoneNode(conv, turn, idx) {
  const req = turn.request || {};
  const ctx = turnCtx(conv, turn, idx);
  const user = h('div', { class: 'msg-user' }, h('div', { class: 'bubble' },
    h('div', { class: 'bubble-top mono faint' }, turnTopRow(conv, turn, idx)),
    stateView(req.state),
    imagesRow(turn),
    h('div', { class: 'bubble-foot' }, qchipsRow(req), h('div', { class: 'opt-badges' }, modelBadges(req)))));
  return h('div', { class: ['turn', 'turn-systemone', `status-${turn.status}`], id: `turn-${turn.id}`, dataset: { turnId: turn.id } },
    user,
    h('div', { class: 'msg-assistant' },
      h('div', { class: 'avatar', title: turn.response?.model || req.model || 'OpenJev' }, icon('logo', 16)),
      h('div', { class: 'assistant-body' }, contentFor(conv, turn, ctx), metaFor(turn, ctx), toolbarFor(conv, turn, idx))));
}

// The <article> holds prompt, answers and meta; the hover toolbar sits outside it, so a
// screenshot of the card leaves the buttons out.
function v2SystemoneNode(conv, turn, idx) {
  const req = turn.request || {};
  const ctx = turnCtx(conv, turn, idx);
  const nImg = Array.isArray(req.images) ? req.images.length : 0;
  const isJson = req.state !== null && typeof req.state === 'object';
  const meta = metaFor(turn, ctx);
  const label = `State${isJson ? ' · JSON' : ''}${nImg ? ` · ${nImg} image${nImg === 1 ? '' : 's'}` : ''}`;
  const card = h('article', { class: 'dcard' },
    h('header', { class: 'dcard-head mono faint' }, turnTopRow(conv, turn, idx), h('span', { class: 'spacer' }), modelBadges(req)),
    h('section', { class: 'dcard-prompt' },
      h('div', { class: 'dcard-label faint' }, label),
      stateView(req.state),
      imagesRow(turn),
      // once answered, the answer cards name each question
      turn.status !== 'ok' ? qchipsRow(req) : null),
    h('div', { class: 'dcard-sep' }),
    h('section', { class: 'dcard-answers' }, contentFor(conv, turn, ctx)),
    meta ? h('footer', { class: 'dcard-foot' }, meta) : null);
  return h('div', { class: ['turn', 'turn-systemone', 'turn-v2', `status-${turn.status}`], id: `turn-${turn.id}`, dataset: { turnId: turn.id } },
    card, toolbarFor(conv, turn, idx));
}

function systemoneNode(conv, turn, idx) {
  return getSettings().threadLayout === 'classic' ? classicSystemoneNode(conv, turn, idx) : v2SystemoneNode(conv, turn, idx);
}

function chatNode(conv, turn, idx) {
  const ctx = { conversation: conv, settings: getSettings(), turnIndex: idx, parentTurn: null, compact: false };
  const user = h('div', { class: 'msg-user' }, h('div', { class: 'bubble' },
    h('div', { class: 'bubble-top mono faint' }, h('span', { class: 'turn-no' }, `#${idx + 1}`), h('span', {}, fmtClock(turn.createdAt))),
    h('div', { class: 'bubble-text' }, turn.user || '')));
  let content;
  if (turn.status === 'error') {
    content = h('div', {},
      turn.assistant ? renderChatAssistant({ ...turn, status: 'aborted' }) : null,
      renderError(turn.error, { onRetry: () => regenerateChat(conv.id, turn.id) }));
  } else content = renderChatAssistant(turn);
  let meta = null;
  try { meta = turn.status === 'pending' ? null : J.renderTurnMeta(turn, ctx); } catch (err) { console.warn(err); }
  const tb = turn.status === 'pending' ? null : toolbar([
    { icon: 'copy', label: 'Copy', onClick: () => copyText(turn.assistant || '') },
    { icon: 'refresh', label: 'Regenerate', onClick: () => regenerateChat(conv.id, turn.id) },
    { icon: 'inspect', label: 'Inspect', onClick: () => emit('oj:open-inspector', { convId: conv.id, turnId: turn.id }) },
    turn.assistant ? {
      icon: 'scale', label: 'Judge with System One', showLabel: true, title: 'Grade this reply with the rubric template in a new decision',
      onClick: () => emit('oj:composer-load', { newConversation: true, state: turn.user ? `Q: ${turn.user}\nA: ${turn.assistant}` : turn.assistant, stateIsJson: false, questions: rubricQuestions(), title: `Judge: ${(turn.user || '').slice(0, 36)}` }),
    } : null,
    { icon: 'terminal', label: 'Copy curl', onClick: () => copyText(J.buildSnippet('curl', turn, getCachedConfig())) },
    { icon: 'trash', label: 'Delete turn', danger: true, onClick: () => deleteTurn(conv.id, turn.id).then(() => toast('Turn deleted')) },
  ]);
  return h('div', { class: ['turn', 'turn-chat', `status-${turn.status}`], id: `turn-${turn.id}`, dataset: { turnId: turn.id } },
    user,
    h('div', { class: 'msg-assistant' },
      h('div', { class: 'avatar chat-avatar', title: turn.request?.model || 'diffusiongemma-26b' }, icon('chat', 15)),
      h('div', { class: 'assistant-body' }, content, meta, tb)));
}

function turnNode(conv, turn, idx) {
  try {
    return turn.kind === 'chat' ? chatNode(conv, turn, idx) : systemoneNode(conv, turn, idx);
  } catch (err) {
    console.warn('[thread] turn render failed', err);
    return h('div', { class: 'turn', id: `turn-${turn.id}`, dataset: { turnId: turn.id } }, h('pre', { class: 'mono' }, String(err?.stack || err)));
  }
}

// ------------------------------------------------------------------ the view
function mountThread(el, params) {
  const id = params.id;
  const conv = peekConversation(id);
  if (!conv) {
    el.appendChild(h('div', { class: 'view-placeholder' },
      h('h2', {}, 'Conversation not found'),
      h('p', { class: 'muted mono' }, id),
      h('a', { class: 'btn', href: '#/' }, 'Back')));
    return () => {};
  }
  setLastConvId(id);
  for (const t of conv.turns) {
    if (t.status === 'pending' && !isTurnInflight(t.id)) {
      Object.assign(t, { status: 'aborted' });
      updateTurn(id, t.id, { status: 'aborted' });
    }
  }

  const header = h('div', { class: 'thread-header', id: 'thread-header' });
  const statsEl = h('div', { class: 'conv-stats', id: 'conv-stats', hidden: !statsOpen });
  const threadEl = h('div', { class: 'thread', id: 'thread' });
  const composerEl = h('div', { class: 'composer', id: 'composer' });
  const dropOverlay = h('div', { class: 'drop-overlay' }, h('div', { class: 'drop-inner' }, icon('image', 28), h('div', {}, 'Drop images to attach'), h('div', { class: 'faint mono' }, 'JPEG · PNG · WebP · GIF · up to 8')));
  const wrap = h('div', { class: ['thread-wrap', `mode-${conv.mode}`] }, header, statsEl, threadEl, composerEl, dropOverlay);
  el.appendChild(wrap);
  const offs = [];

  const nearBottom = () => wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 160;
  const toBottom = (smooth = false) => wrap.scrollTo({ top: wrap.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });

  function renderHeader() {
    clear(header);
    const c = peekConversation(id) || conv;
    const okN = c.turns.filter((t) => t.status === 'ok').length;
    header.append(
      h('span', { class: ['badge', 'mode-badge', c.mode === 'chat' ? 'mode-chat' : 'mode-s1'] }, icon(c.mode === 'chat' ? 'chat' : 'bolt', 12), c.mode === 'chat' ? `Chat · ${getSettings().chatModel}` : 'System One'),
      h('span', { class: 'mono faint th-meta' }, `${c.turns.length} turn${c.turns.length === 1 ? '' : 's'} · ${okN} ok · started ${fmtRelTime(c.createdAt)}`),
      h('span', { class: 'spacer' }),
      // native append would print `false`, hence the spread
      ...(c.mode === 'systemone' ? [h('button', { class: 'btn sm ghost', title: "Open these questions and this conversation's states in Batch", onClick: () => openConversationInBatch() }, icon('batch', 13), 'batch')] : []),
      h('button', { class: ['btn sm ghost', statsOpen && 'active'], title: 'Conversation stats', onClick: () => { statsOpen = !statsOpen; renderStats(); renderHeader(); } }, icon('sigma', 13), 'stats'),
      h('button', { class: 'btn sm ghost', title: 'Export this conversation', onClick: async () => { const f = await exportConversations([c.id]); download(`openjev-${c.id}.json`, f); } }, icon('download', 13)));
  }

  const renderStats = debounce(() => {
    statsEl.hidden = !statsOpen;
    clear(statsEl);
    if (!statsOpen) return;
    const c = peekConversation(id) || conv;
    try {
      const node = J.renderConversationStats(c, { settings: getSettings() });
      if (node instanceof Node) statsEl.appendChild(node);
    } catch (err) { console.warn(err); }
  }, 60);

  function renderAll() {
    const c = peekConversation(id) || conv;
    for (const t of c.turns) destroyChatExtras(t.id);
    clear(threadEl);
    if (!c.turns.length) {
      const hero = h('div', { class: 'thread-hero' });
      renderWelcome(hero, { conv: c });
      threadEl.appendChild(hero);
      return;
    }
    c.turns.forEach((t, i) => threadEl.appendChild(turnNode(c, t, i)));
  }

  function replaceTurn(turn) {
    const c = peekConversation(id) || conv;
    const idx = c.turns.findIndex((t) => t.id === turn.id);
    if (idx < 0) return;
    const stick = nearBottom();
    const existing = threadEl.querySelector(`[data-turn-id="${CSS.escape(turn.id)}"]`);
    destroyChatExtras(turn.id);
    const node = turnNode(c, c.turns[idx], idx);
    if (existing) existing.replaceWith(node);
    else {
      threadEl.querySelector('.thread-hero')?.remove();
      const next = c.turns[idx + 1] && threadEl.querySelector(`[data-turn-id="${CSS.escape(c.turns[idx + 1].id)}"]`);
      if (next) threadEl.insertBefore(node, next); else threadEl.appendChild(node);
    }
    // re-render children that show Δ against this turn
    // ... and siblings with the same bodyHash, so every run shows its seed-stable mark
    const sameBody = (o) => turn.status === 'ok' && turn.bodyHash && o.bodyHash === turn.bodyHash && o.status === 'ok';
    for (const other of c.turns) {
      if (other.id !== turn.id && (other.parentTurnId === turn.id || sameBody(other))) {
        const on2 = threadEl.querySelector(`[data-turn-id="${CSS.escape(other.id)}"]`);
        if (on2) on2.replaceWith(turnNode(c, other, c.turns.indexOf(other)));
      }
    }
    if (stick || !existing) requestAnimationFrame(() => {
      // A finished answer taller than the viewport: show its top (the question and the first
      // cards), not the tail end of the last card.
      const avail = wrap.clientHeight - header.offsetHeight - composerEl.offsetHeight;
      if (existing && turn.status !== 'pending' && node.offsetHeight > avail) {
        const top = wrap.scrollTop + node.getBoundingClientRect().top - wrap.getBoundingClientRect().top - header.offsetHeight - 8;
        wrap.scrollTo({ top, behavior: 'smooth' });
      } else toBottom(!!existing);
    });
  }

  function reconcile() {
    const c = peekConversation(id);
    if (!c) { navigate('#/'); return; }
    const shown = [...threadEl.querySelectorAll(':scope > .turn')].map((n) => n.dataset.turnId);
    const want = c.turns.map((t) => t.id);
    if (shown.join('|') !== want.join('|')) {
      const top = wrap.scrollTop;
      renderAll();
      wrap.scrollTop = top;
    }
    wrap.classList.toggle('mode-chat', c.mode === 'chat');
    renderHeader();
  }

  renderHeader();
  renderAll();
  renderStats();
  const unmountComposer = mountComposer(composerEl, conv);
  // an empty thread shows the welcome hero: keep its heading in view instead of the bottom
  if (conv.turns.length) requestAnimationFrame(() => toBottom());
  // like any chat app: opening a conversation puts the caret in the composer (fine pointers only,
  // so a phone does not pop its keyboard over the thread)
  // Also when focus sits on the button that opened it ("New chat"): left there, Space and Enter
  // would click it again and create a new conversation per keystroke.
  if (matchMedia('(pointer: fine)').matches) {
    setTimeout(() => {
      const a = document.activeElement;
      const typing = a && a !== document.body && (a.matches('input, textarea, select, [contenteditable], [contenteditable] *') || a.closest('.modal, .drawer'));
      if (!typing) focusComposer();
    }, 60);
  }

  offs.push(on('oj:turn-updated', ({ convId, turn }) => {
    if (convId !== id || !turn) return;
    replaceTurn(turn);
    renderStats();
    renderHeader();
  }));
  offs.push(on('oj:conversation-updated', ({ id: cid }) => { if (cid === id) { reconcile(); renderStats(); } }));
  offs.push(on('oj:conversations-changed', () => { if (!peekConversation(id)) navigate('#/'); }));
  offs.push(on('oj:settings-changed', ({ changed }) => {
    if (changed.some((k) => ['pricePerMInput', 'pricePerMOutput', 'currency', 'chatModel', 'threadLayout'].includes(k))) { const top = wrap.scrollTop; renderAll(); wrap.scrollTop = top; renderStats(); renderHeader(); }
  }));

  // drag & drop images anywhere on the thread
  let depth = 0;
  const hasFiles = (ev) => [...(ev.dataTransfer?.types || [])].includes('Files');
  const onEnter = (ev) => { if (!hasFiles(ev) || conv.mode === 'chat') return; ev.preventDefault(); depth++; wrap.classList.add('dropping'); };
  const onOver = (ev) => { if (!hasFiles(ev) || conv.mode === 'chat') return; ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; };
  const onLeave = () => { depth = Math.max(0, depth - 1); if (!depth) wrap.classList.remove('dropping'); };
  const onDrop = (ev) => {
    if (!hasFiles(ev) || conv.mode === 'chat') return;
    ev.preventDefault();
    depth = 0;
    wrap.classList.remove('dropping');
    const files = [...ev.dataTransfer.files].filter((f) => f.type.startsWith('image/'));
    if (files.length) addImageFiles(files);
    else toast('Only images can be dropped here (import conversations from the sidebar)', { kind: 'warn' });
  };
  wrap.addEventListener('dragenter', onEnter);
  wrap.addEventListener('dragover', onOver);
  wrap.addEventListener('dragleave', onLeave);
  wrap.addEventListener('drop', onDrop);

  return () => {
    for (const off of offs) off();
    unmountComposer();
    for (const t of (peekConversation(id)?.turns || [])) destroyChatExtras(t.id);
  };
}

function mountWelcome(el) {
  const wrap = h('div', { class: 'thread-wrap welcome-wrap' });
  el.appendChild(wrap);
  renderWelcome(wrap, { conv: null, standalone: true });
  return () => {};
}

export function registerThreadViews() {
  registerView('thread', { title: 'Thread', icon: 'chat', mount: mountThread });
  registerView('welcome', { title: 'Welcome', icon: 'sparkles', mount: mountWelcome });
}

// B-private: the last turn of the open conversation (for Cmd+I and /rerun)
export function lastTurnOf(convId) {
  const c = peekConversation(convId);
  return c && c.turns.length ? c.turns[c.turns.length - 1] : null;
}

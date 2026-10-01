// Standalone System One decision card (builder C): the v2 card markup of app/thread.js rebuilt from C's renderers, for surfaces outside the thread (the batch detail).
// Same classes as the thread's card (.turn-v2 / .dcard*, styled globally in app.css), without
// the hover toolbar: the host surface brings its own actions.

import { el } from '/js/jev/util.js';
import { renderResult, renderTurnMeta } from '/js/jev/renderers.js';
import { renderError } from '/js/core/errors.js';
import { icon } from '/js/core/icons.js';
import { fmtBytes } from '/js/core/format.js';

function stateView(state) {
  if (state !== null && typeof state === 'object') return el('pre', { class: 'bubble-json mono' }, JSON.stringify(state, null, 2));
  const s = String(state ?? '');
  const long = s.length > 900;
  const box = el('div', { class: ['bubble-text', long && 'clamped'] }, s || el('span', { class: 'faint' }, '(empty state)'));
  if (!long) return box;
  const more = el('button', { class: 'link-btn', type: 'button', onclick: () => { box.classList.toggle('clamped'); more.textContent = box.classList.contains('clamped') ? 'show more' : 'show less'; } }, 'show more');
  return el('div', {}, box, more);
}

// a null image entry (data not kept) renders as a placeholder tile
function imagesRow(turn) {
  const images = Array.isArray(turn.request?.images) ? turn.request.images : [];
  return images.length ? el('div', { class: 'bubble-images' }, images.map((src, i) => {
    const meta = turn.imagesMeta?.[i] || {};
    return typeof src === 'string' && src
      ? el('img', { class: 'bubble-img', src, alt: meta.name || `image ${i + 1}`, title: `${meta.name || ''} ${meta.width ? `${meta.width}×${meta.height}` : ''} ${meta.bytes ? fmtBytes(meta.bytes) : ''}` })
      : el('div', { class: 'bubble-img missing', title: 'image data not kept' }, icon('image', 16));
  })) : null;
}

function qchipsRow(req) {
  return el('div', { class: 'qchips' }, Object.entries(req.questions || {}).map(([qid, q]) => el('span', { class: `qchip type-${q?.type || 'unknown'}`, title: `${q?.type || '?'}${typeof q?.instructions === 'string' ? `: ${q.instructions}` : ''}` }, qid)));
}

function optionBadges(req) {
  const out = [];
  if (req.steps != null) out.push(`steps ${req.steps}`);
  if (req.samples != null) out.push(`samples ${req.samples}`);
  if (req.think != null) out.push(`think ${req.think}`);
  if (req.sequential) out.push('seq');
  return out.map((t) => el('span', { class: 'badge opt-badge mono' }, t));
}

function modelBadges(req) {
  return [el('span', { class: 'badge opt-badge mono model-badge' }, req.model || ''), optionBadges(req)];
}

function contentFor(turn, ctx, onRetry) {
  if (turn.status === 'pending') {
    return el('div', { class: 'pending-block' },
      el('div', { class: 'pending-head' }, el('span', { class: 'pulse-dot' }), el('span', { class: 'muted' }, turn.queued ? 'queued' : 'running…')),
      el('div', { class: 'skeleton sk-line w60' }), el('div', { class: 'skeleton sk-line w90' }), el('div', { class: 'skeleton sk-line w40' }));
  }
  // autoRetry off: the caller already retried 429/529 once, opening the card must not send again
  if (turn.status === 'error') return renderError(turn.error, { requestBytes: turn.http?.requestBytes, onRetry, autoRetry: false });
  if (turn.status === 'aborted') {
    return el('div', { class: 'turn-stopped muted' }, icon('stop', 12), el('span', {}, 'Stopped'),
      onRetry ? el('button', { class: 'btn sm ghost', type: 'button', onclick: () => onRetry() }, icon('refresh', 12), 'Retry') : null);
  }
  try { return renderResult(turn, ctx); } catch (err) { return el('pre', { class: 'mono' }, String(err)); }
}

/** renderTurnCard(turn, ctx, {onRetry?}) → the v2 decision card for a System One turn.
 *  ctx is renderResult's ctx; onRetry (optional) adds Retry to error and stopped cards. */
export function renderTurnCard(turn, ctx = {}, { onRetry } = {}) {
  const req = turn.request || {};
  const nImg = Array.isArray(req.images) ? req.images.length : 0;
  const isJson = req.state !== null && typeof req.state === 'object';
  let meta = null;
  try { meta = renderTurnMeta(turn, ctx); } catch (err) { console.warn(err); }
  const label = `State${isJson ? ' · JSON' : ''}${nImg ? ` · ${nImg} image${nImg === 1 ? '' : 's'}` : ''}`;
  const card = el('article', { class: 'dcard' },
    el('header', { class: 'dcard-head mono faint' },
      el('span', { class: 'turn-no' }, `#${Number.isFinite(ctx.turnIndex) ? ctx.turnIndex + 1 : '?'}`),
      turn.source && turn.source !== 'thread' ? el('span', { class: 'badge src-badge' }, turn.source) : null,
      turn.label ? el('span', { class: 'badge label-badge' }, turn.label) : null,
      el('span', { class: 'spacer' }), modelBadges(req)),
    el('section', { class: 'dcard-prompt' },
      el('div', { class: 'dcard-label faint' }, label),
      stateView(req.state),
      imagesRow(turn),
      // once answered, the answer cards name each question
      turn.status !== 'ok' ? qchipsRow(req) : null),
    el('div', { class: 'dcard-sep' }),
    el('section', { class: 'dcard-answers' }, contentFor(turn, ctx, onRetry)),
    meta ? el('footer', { class: 'dcard-foot' }, meta) : null);
  return el('div', { class: ['turn-v2', `status-${turn.status}`] }, card);
}

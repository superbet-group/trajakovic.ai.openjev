// Builder C barrel: the only module B imports from C (dynamically). init() registers the
// templates / batch / stats / compare views, C's slash commands and the inspector listener;
// the other exports are the contract's §3.10 interface.

import { registerView } from '/js/core/router.js';
import { on } from '/js/core/bus.js';
import { getConversation } from '/js/core/store.js';
import { toast } from '/js/core/toast.js';
import { mountTemplatesView, renderTemplateStrip as _strip, TEMPLATES, getTemplate as _getTemplate } from '/js/jev/templates.js';
import { mountBatchView } from '/js/jev/batch.js';
import { mountStatsView, renderConversationStats as _convStats } from '/js/jev/stats.js';
import { mountCompareView } from '/js/jev/compare.js';
import { openInspector as _openInspector } from '/js/jev/inspector.js';
import { registerJevSlashCommands } from '/js/jev/slashCommands.js';
import { mountQuestionEditor as _mountQE } from '/js/jev/questionEditor.js';
import { mountJsonEditor as _mountJE, loadJsonEditorLib } from '/js/jev/jsonedit.js';
import { validateQuestions as _validate } from '/js/jev/validate.js';
import { renderResult as _renderResult, renderTurnMeta as _renderTurnMeta } from '/js/jev/renderers.js';
import { buildSnippet as _buildSnippet } from '/js/jev/snippets.js';

let inited = false;

export async function init() {
  if (inited) return;
  inited = true;
  const reg = (name, spec) => { try { registerView(name, spec); } catch (e) { console.warn('[ojui] registerView failed', name, e); } };
  reg('templates', { title: 'Templates', icon: 'templates', mount: (el, params) => mountTemplatesView(el, params) });
  reg('batch', { title: 'Batch', icon: 'batch', mount: (el, params) => mountBatchView(el, params) });
  reg('stats', { title: 'Stats', icon: 'stats', mount: (el, params) => mountStatsView(el, params) });
  reg('compare', { title: 'Compare', icon: 'compare', mount: (el, params) => mountCompareView(el, params) });
  registerJevSlashCommands();
  on('oj:open-inspector', async (detail) => {
    const { convId, turnId, turn: given } = detail || {};
    try {
      const conversation = convId ? await getConversation(convId) : null;
      const turn = given || conversation?.turns?.find((t) => t.id === turnId);
      if (!turn) { toast('Turn not found', { kind: 'warn' }); return; }
      _openInspector({ conversation, turn });
    } catch (e) { toast(`Inspector failed: ${e.message || e}`, { kind: 'err' }); }
  });
  loadJsonEditorLib(); // warm the CDN import so the first editor mounts without the fallback flash
}

export function mountQuestionEditor(el, opts = {}) { return _mountQE(el, opts); }
export function mountJsonEditor(el, opts = {}) { return _mountJE(el, opts); }
export function validateQuestions(questions) { const r = _validate(questions); return { valid: r.valid, errors: r.errors, warnings: r.warnings }; }
export function renderResult(turn, ctx = {}) { return _renderResult(turn, ctx); }
export function renderTurnMeta(turn, ctx = {}) { return _renderTurnMeta(turn, ctx); }
export function renderConversationStats(conversation, ctx = {}) { return _convStats(conversation, ctx); }
export function renderTemplateStrip(el, opts = {}) { return _strip(el, opts); }
export function openInspector(args) { return _openInspector(args); }
export function buildSnippet(kind, turn, config) { return _buildSnippet(kind, turn, config); }
/** Template data (integrator addition): B's chat "Judge with System One" reads the 'rubric' questions. */
export const templates = TEMPLATES;
export function getTemplate(id) { return _getTemplate(id); }

const TYPE_META = {
  noul: { label: 'yes / no', color: 'var(--type-noul)', description: 'A yes/no question. Answer: P(yes) in 0..1. Optional criteria {true, false} say what yes and no mean.' },
  choice: { label: 'choice', color: 'var(--type-choice)', description: 'Pick one of up to 255 named options. Answer: the top option, every probability, and a confidence.' },
  score: { label: 'score', color: 'var(--type-score)', description: 'Rate on 1–10 ordered levels. Answer: the expected level E = Σ i·pᵢ (0-indexed), the distribution and a confidence.' },
};
/** ['noul','choice','score'], each also reachable as questionTypes.noul → {label, color, description}. */
export const questionTypes = Object.freeze(Object.assign(['noul', 'choice', 'score'], TYPE_META, {
  meta: TYPE_META,
  list: ['noul', 'choice', 'score'].map((type) => ({ type, ...TYPE_META[type] })),
}));

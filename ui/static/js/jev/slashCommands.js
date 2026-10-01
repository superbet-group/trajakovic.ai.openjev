// Builder C's slash commands: /template, /templates, /batch, /compare, /inspect, /curl,
// /python, /add, /json and /builder. Registered once from init() through core/slash.js.

import { registerSlash } from '/js/core/slash.js';
import { emit } from '/js/core/bus.js';
import { copyText } from '/js/core/dom.js';
import { toast } from '/js/core/toast.js';
import { buildSystemOneBody, getCachedConfig } from '/js/core/api.js';
import { findTemplate, templateToDraft, TEMPLATES } from '/js/jev/templates.js';
import { buildSnippet } from '/js/jev/snippets.js';
import { openInspector } from '/js/jev/inspector.js';
import { queueComparePreset, PRESETS } from '/js/jev/compare.js';
import { getActiveQuestionEditor } from '/js/jev/questionEditor.js';

const go = (ctx, hash) => (typeof ctx?.navigate === 'function' ? ctx.navigate(hash) : (location.hash = hash));

function lastTurn(conv, pred = () => true) {
  const turns = conv?.turns || [];
  for (let i = turns.length - 1; i >= 0; i--) if (pred(turns[i])) return turns[i];
  return null;
}

function draftTurn(ctx) {
  const d = ctx?.composer?.getDraft?.();
  if (!d) return null;
  try {
    const opts = ctx.conversation?.options || ctx.settings?.defaultOptions || {};
    const body = buildSystemOneBody(d, { model: opts.model || ctx.settings?.defaultModel || 'openjev-latest', ...opts });
    return { kind: 'systemone', request: body };
  } catch (e) { toast(`Draft is not sendable: ${e.message}`, { kind: 'err' }); return null; }
}

function snippetFor(kind, ctx) {
  const conv = ctx?.conversation;
  const turn = lastTurn(conv, (t) => !!t.request) || (conv?.mode === 'chat' ? null : draftTurn(ctx));
  if (!turn) { toast('No turn or draft to build a snippet from', { kind: 'warn' }); return; }
  let cfg = null;
  try { cfg = getCachedConfig(); } catch { cfg = null; }
  copyText(buildSnippet(kind, turn, cfg));
}

let registered = false;
export function registerJevSlashCommands() {
  if (registered) return;
  registered = true;
  const reg = (spec) => { try { registerSlash(spec); } catch (e) { console.warn('[ojui] registerSlash failed', spec.name, e); } };

  reg({
    name: 'template', args: '<id|fuzzy>', description: `load a template (${TEMPLATES.map((t) => t.id).slice(0, 4).join(', ')}, …)`,
    run: async (arg, ctx) => {
      const t = findTemplate(arg);
      if (!t) { toast(arg ? `No template matches "${arg}"` : 'Usage: /template <id>', { kind: 'warn' }); if (!arg) go(ctx, '#/templates'); return; }
      const draft = await templateToDraft(t);
      if (ctx?.conversation && ctx.conversation.mode !== 'chat' && ctx.composer?.loadDraft) ctx.composer.loadDraft(draft);
      else emit('oj:composer-load', { ...draft, newConversation: true });
      toast(`Loaded template ${t.title}`, { kind: 'ok' });
    },
  });
  reg({ name: 'templates', description: 'open the template gallery', run: async (_a, ctx) => go(ctx, '#/templates') });
  reg({ name: 'batch', description: 'open batch mode', run: async (_a, ctx) => go(ctx, '#/batch') });
  reg({
    name: 'compare', args: `[${PRESETS.map((p) => p.id).join('|')}]`, description: 'compare the last turn under variants',
    run: async (arg, ctx) => {
      const conv = ctx?.conversation;
      const t = lastTurn(conv, (x) => x.kind === 'systemone' && x.request);
      if (!t) { toast('No System One turn to compare', { kind: 'warn' }); return; }
      if (arg) queueComparePreset(arg.trim());
      go(ctx, `#/compare/${encodeURIComponent(conv.id)}/${encodeURIComponent(t.id)}`);
    },
  });
  reg({
    name: 'inspect', description: 'inspect the last turn',
    run: async (_a, ctx) => {
      const t = lastTurn(ctx?.conversation);
      if (!t) { toast('No turn to inspect', { kind: 'warn' }); return; }
      openInspector({ conversation: ctx.conversation, turn: t });
    },
  });
  reg({ name: 'curl', description: 'copy the last request as curl', run: async (_a, ctx) => snippetFor('curl', ctx) });
  reg({ name: 'python', description: 'copy the last request as Python', run: async (_a, ctx) => snippetFor('python', ctx) });
  reg({
    name: 'add', args: 'noul|choice|score [qid]', description: 'add a question',
    run: async (arg) => {
      const [type, qid] = String(arg || '').trim().split(/\s+/);
      if (!['noul', 'choice', 'score'].includes(type)) { toast('Usage: /add noul|choice|score [qid]', { kind: 'warn' }); return; }
      const ed = getActiveQuestionEditor();
      if (!ed) { toast('Open a System One conversation first', { kind: 'warn' }); return; }
      const id = ed.addQuestion(type, qid);
      toast(`Added ${type} "${id}"`, { kind: 'ok' });
    },
  });
  reg({ name: 'json', description: 'question editor: JSON tab', run: async () => { const ed = getActiveQuestionEditor(); if (ed) ed.setTab('json'); } });
  reg({ name: 'builder', description: 'question editor: Builder tab', run: async () => { const ed = getActiveQuestionEditor(); if (ed) ed.setTab('builder'); } });
}

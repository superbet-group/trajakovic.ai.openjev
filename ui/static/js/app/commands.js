// app/commands.js — builder B's slash commands (CONTRACT §5.11), registered at boot.
// Options changes go through loadDraft({options}) so the composer chips update in one place.

import { h } from '/js/core/dom.js';
import { registerSlash, listSlash } from '/js/core/slash.js';
import { openModal } from '/js/core/modal.js';
import { toast } from '/js/core/toast.js';
import { setTheme } from '/js/core/theme.js';
import { navigate } from '/js/core/router.js';
import { getSettings, setSettings, updateConversation } from '/js/core/store.js';
import { newConversation, deleteConvConfirm, exportDialog, pickImportFile } from '/js/app/sidebar.js';
import { openSettings } from '/js/app/settings.js';
import { rerunTurn, clearComposer } from '/js/app/composer.js';

function needConv(ctx) {
  if (!ctx.conversation) throw new Error('open a conversation first');
  return ctx.conversation;
}

function setOpt(ctx, key, value) {
  const c = needConv(ctx);
  if (c.mode !== 'systemone') throw new Error('options apply to System One decisions');
  ctx.composer.loadDraft({ options: { ...c.options, [key]: value } });
  toast(`${key} = ${value === null ? 'default' : value}`, { kind: 'ok', timeout: 1400 });
}

function intArg(arg, { min, max, unset }) {
  const a = String(arg || '').trim().toLowerCase();
  if (unset.includes(a)) return null;
  const n = Number(a);
  if (!Number.isInteger(n) || n < min || n > max) throw new Error(`expected ${min}–${max} or ${unset.join('|')}`);
  return n;
}

export function registerCoreSlash() {
  registerSlash({ name: 'new', description: 'new decision (System One)', run: () => newConversation('systemone') });
  registerSlash({ name: 'chat', description: 'new chat with diffusiongemma-26b', run: () => newConversation('chat') });
  registerSlash({
    name: 'rename', args: '<title>', description: 'rename this conversation',
    run: async (arg, ctx) => { const c = needConv(ctx); if (!arg) throw new Error('give a title'); await updateConversation(c.id, { title: arg }); },
  });
  registerSlash({ name: 'delete', description: 'delete this conversation', run: (_a, ctx) => deleteConvConfirm(needConv(ctx).id) });
  registerSlash({ name: 'export', description: 'export this conversation', run: (_a, ctx) => exportDialog(ctx.conversation ? [ctx.conversation.id] : 'all') });
  registerSlash({ name: 'import', description: 'import conversations from a .json file', run: () => pickImportFile() });
  registerSlash({
    name: 'theme', args: 'dark|light|system', description: 'set the theme',
    run: (arg) => { const t = arg.trim().toLowerCase(); if (!['dark', 'light', 'system'].includes(t)) throw new Error('dark, light or system'); setTheme(t); },
  });
  registerSlash({
    name: 'model', args: '<name>', description: 'set the model for this decision',
    run: (arg, ctx) => { if (!arg.trim()) throw new Error('give a model id'); setOpt(ctx, 'model', arg.trim()); },
  });
  registerSlash({ name: 'steps', args: '<1-8|default>', description: 'denoise steps per read', run: (a, ctx) => setOpt(ctx, 'steps', intArg(a, { min: 1, max: 8, unset: ['default', 'off', ''] })) });
  registerSlash({ name: 'samples', args: '<1-32|default>', description: 'reads to average', run: (a, ctx) => setOpt(ctx, 'samples', intArg(a, { min: 1, max: 32, unset: ['default', 'off', ''] })) });
  registerSlash({ name: 'think', args: '<0-4096|off>', description: 'thought budget in tokens', run: (a, ctx) => setOpt(ctx, 'think', intArg(a, { min: 0, max: 4096, unset: ['off', 'default', ''] })) });
  registerSlash({
    name: 'seq', args: 'on|off', description: 'sequential chunk reads',
    run: (a, ctx) => { const v = a.trim().toLowerCase(); if (!['on', 'off', 'true', 'false'].includes(v)) throw new Error('on or off'); setOpt(ctx, 'sequential', v === 'on' || v === 'true'); },
  });
  registerSlash({
    name: 'price', args: '<in> [out]', description: 'prices per 1M input / output tokens',
    run: (a) => {
      const [i, o] = a.split(/\s+/).filter(Boolean).map(Number);
      if (!Number.isFinite(i) || i < 0) throw new Error('give a price per 1M input tokens');
      setSettings({ pricePerMInput: i, pricePerMOutput: Number.isFinite(o) && o >= 0 ? o : getSettings().pricePerMOutput });
      toast(`prices: ${getSettings().currency}${getSettings().pricePerMInput} in / ${getSettings().currency}${getSettings().pricePerMOutput} out per 1M`, { kind: 'ok' });
    },
  });
  registerSlash({ name: 'settings', description: 'open settings', run: () => openSettings() });
  registerSlash({ name: 'stats', description: 'open the stats dashboard', run: () => navigate('#/stats') });
  registerSlash({
    name: 'rerun', description: 'rerun the last turn (identical body)',
    run: async (_a, ctx) => {
      const c = needConv(ctx);
      const t = c.turns[c.turns.length - 1];
      if (!t) throw new Error('nothing to rerun yet');
      await rerunTurn(c.id, t);
    },
  });
  registerSlash({ name: 'clear', description: 'clear the composer', run: () => clearComposer() });
  registerSlash({
    name: 'help', description: 'list the commands',
    run: () => openModal({
      title: 'Slash commands',
      body: h('div', { class: 'slash-help' }, listSlash().map((c) => h('div', { class: 'sc-row' },
        h('span', { class: 'mono slash-name' }, `/${c.name}`, c.args ? h('span', { class: 'faint' }, ` ${c.args}`) : null),
        h('span', { class: 'muted' }, c.description)))),
    }),
  });
}

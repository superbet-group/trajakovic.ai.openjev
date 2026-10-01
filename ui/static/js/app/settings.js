// app/settings.js — the settings modal (builder B): theme, System One defaults with the
// README's Extensions explanations, chat defaults, prices, auth override, read-only
// connection info, and a danger zone. Every control writes through setSettings live.

import { h, clear } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { toast } from '/js/core/toast.js';
import { openModal, confirmDialog } from '/js/core/modal.js';
import { setTheme } from '/js/core/theme.js';
import { getSettings, setSettings, clearRequests, resetTotals, deleteAllConversations, isFallbackStore } from '/js/core/store.js';
import { getConfig, listModels, getLastHealth, checkHealthNow } from '/js/core/api.js';
import { navigate } from '/js/core/router.js';
import { fmtMs } from '/js/core/format.js';

const EXPLAIN = {
  steps: 'Denoise steps per read (1–8, default 1). More steps let the answers settle against each other. Same tokens, more GPU time.',
  samples: 'Read N times with different noise and average (1–32). Replaces the automatic re-reads; 1 gives one read, the fastest answer. Cost: N × input tokens.',
  think: 'The model writes a thought (0–4096 tokens, a hard cap), then reads the answers after it. Give multi-step problems 512 or more. Cost: input tokens twice, plus the thought as output tokens. Needs a text state (no images).',
  sequential: 'For long question lists: read the ~12-question chunks in order, each seeing the answers before it. One read per chunk, in series. Needs a text state (no images).',
};

function field(label, control, help) {
  return h('div', { class: 'set-field' },
    h('span', { class: 'set-label' }, label),
    control,
    help ? h('span', { class: 'set-help faint' }, help) : null);
}

function section(title, ...children) {
  return h('section', { class: 'set-section' }, h('h3', { class: 'set-title' }, title), ...children);
}

function numInput(value, { min, max, step = 1, placeholder = 'default', onSet }) {
  const el = h('input', { class: 'input sm mono', type: 'number', min, max, step, placeholder, value: value ?? '' });
  el.addEventListener('change', () => {
    const v = el.value.trim();
    if (v === '') { onSet(null); el.classList.remove('bad'); return; }
    const n = Number(v);
    if (!Number.isFinite(n) || (min !== undefined && n < min) || (max !== undefined && n > max)) { el.classList.add('bad'); return; }
    el.classList.remove('bad');
    onSet(n);
  });
  return el;
}

function checkbox(checked, onSet) {
  const el = h('input', { type: 'checkbox', checked: !!checked });
  el.addEventListener('change', () => onSet(el.checked));
  return el;
}

export async function openSettings({ section: focusSection } = {}) {
  const s = getSettings();
  const config = await getConfig();
  const body = h('div', { class: 'settings' });

  // --- appearance
  const themeSeg = h('div', { class: 'seg' }, ['system', 'dark', 'light'].map((t) => h('button', {
    class: ['seg-btn', s.theme === t && 'active'],
    onClick: (ev) => { setTheme(t); for (const b of themeSeg.children) b.classList.remove('active'); ev.currentTarget.classList.add('active'); },
  }, icon(t === 'system' ? 'monitor' : t === 'dark' ? 'moon' : 'sun', 13), t)));
  body.appendChild(section('Appearance', field('Theme', themeSeg, 'System follows your OS and switches live.')));

  // --- System One defaults
  const modelSel = h('select', { class: 'select sm mono' }, h('option', { value: s.defaultModel }, s.defaultModel));
  const modelDesc = h('span', { class: 'set-help faint' }, '');
  const fillModels = (models) => {
    clear(modelSel);
    const names = models.map((m) => m.name).filter((n) => n !== 'diffusiongemma-26b');
    if (!names.includes(s.defaultModel)) names.unshift(s.defaultModel);
    for (const n of names) modelSel.appendChild(h('option', { value: n }, n));
    modelSel.value = getSettings().defaultModel;
    const desc = Object.fromEntries(models.map((m) => [m.name, m.description]));
    modelDesc.textContent = desc[modelSel.value] || '';
    modelSel.onchange = () => { setSettings({ defaultModel: modelSel.value }); modelDesc.textContent = desc[modelSel.value] || ''; };
  };
  fillModels(getLastHealth()?.models || []);
  listModels().then((r) => { if (r.ok) fillModels(r.models); });
  const setOpt = (k) => (v) => setSettings({ defaultOptions: { [k]: v } });
  body.appendChild(section('System One defaults',
    h('p', { class: 'faint set-note' }, 'Used for new conversations. Each conversation keeps its own options, edited with the chips above the composer. Empty = omit the field (server default).'),
    h('div', { class: 'set-field' }, h('span', { class: 'set-label' }, 'Default model'), modelSel, modelDesc),
    field('steps', numInput(s.defaultOptions.steps, { min: 1, max: 8, onSet: setOpt('steps') }), EXPLAIN.steps),
    field('samples', numInput(s.defaultOptions.samples, { min: 1, max: 32, onSet: setOpt('samples') }), EXPLAIN.samples),
    field('think', numInput(s.defaultOptions.think, { min: 0, max: 4096, placeholder: 'off', onSet: setOpt('think') }), EXPLAIN.think),
    field('sequential', checkbox(s.defaultOptions.sequential, setOpt('sequential')), EXPLAIN.sequential)));

  // --- chat
  const sys = h('textarea', { class: 'textarea mono', rows: 3, value: s.chatSystemPrompt, placeholder: 'Default system prompt for new chats' });
  sys.addEventListener('change', () => setSettings({ chatSystemPrompt: sys.value }));
  const chatModel = h('input', { class: 'input sm mono', value: s.chatModel });
  chatModel.addEventListener('change', () => setSettings({ chatModel: chatModel.value.trim() || 'diffusiongemma-26b' }));
  body.appendChild(section('Chat defaults',
    field('Chat model', chatModel, 'POST /v1/chat/completions. OpenJev serves diffusiongemma-26b.'),
    field('max_tokens', numInput(s.chatMaxTokens, { min: 1, max: config.limits?.chatMaxTokensCap || 8192, placeholder: '1024', onSet: (v) => setSettings({ chatMaxTokens: v ?? 1024 }) }), 'Default 1024, cap 8192. Per-chat value via the composer chip.'),
    field('System prompt', sys, 'Copied into each new chat; edit per chat from the composer.')));

  // --- pricing
  const cur = h('input', { class: 'input sm mono', value: s.currency, maxlength: 4, style: 'width:5em' });
  cur.addEventListener('change', () => setSettings({ currency: cur.value || '$' }));
  body.appendChild(section('Pricing (for the "cost" figures)',
    h('p', { class: 'faint set-note' }, 'Local inference is free; set prices to see what the same traffic would cost on a hosted API. Cost is derived at render time and never stored.'),
    h('div', { class: 'set-grid3' },
      field('per 1M input tokens', numInput(s.pricePerMInput, { min: 0, step: 0.01, placeholder: '0', onSet: (v) => setSettings({ pricePerMInput: v ?? 0 }) })),
      field('per 1M output tokens', numInput(s.pricePerMOutput, { min: 0, step: 0.01, placeholder: '0', onSet: (v) => setSettings({ pricePerMOutput: v ?? 0 }) })),
      field('currency', cur))));

  // --- behaviour
  const jsonMode = h('select', { class: 'select sm' }, ['tree', 'text', 'table'].map((m) => h('option', { value: m }, m)));
  jsonMode.value = s.jsonEditorMode;
  jsonMode.onchange = () => setSettings({ jsonEditorMode: jsonMode.value });
  const qTab = h('select', { class: 'select sm' }, ['builder', 'json'].map((m) => h('option', { value: m }, m)));
  qTab.value = s.questionEditorTab;
  qTab.onchange = () => setSettings({ questionEditorTab: qTab.value });
  const clearSel = h('select', { class: 'select sm' }, [['answer', 'after the answer arrives'], ['send', 'immediately on send'], ['never', 'never']].map(([v, l]) => h('option', { value: v }, l)));
  clearSel.value = s.clearStateOn;
  clearSel.onchange = () => setSettings({ clearStateOn: clearSel.value });
  const layoutSel = h('select', { class: 'select sm' }, [['v2', 'decision cards (prompt + answers in one card)'], ['classic', 'chat bubbles']].map(([v, l]) => h('option', { value: v }, l)));
  layoutSel.value = s.threadLayout;
  layoutSel.onchange = () => setSettings({ threadLayout: layoutSel.value });
  body.appendChild(section('Behaviour',
    field('Clear the state box', clearSel, 'Default: once the answer is back. Failed or stopped requests keep the text so you can fix and resend.'),
    field('Thread layout', layoutSel, 'Decision cards keep the state and its answers in one block, ready to screenshot or copy as text.'),
    field('Auto-retry 429 / 529 once', checkbox(s.autoRetryOverloaded, (v) => setSettings({ autoRetryOverloaded: v })), 'Waits for retry-after, then retries a single time.'),
    field('Show raw error bodies', checkbox(s.showRawByDefault, (v) => setSettings({ showRawByDefault: v }))),
    h('div', { class: 'set-grid3' }, field('JSON editor mode', jsonMode), field('Question editor tab', qTab))));

  // --- auth
  const auth = h('input', { class: 'input sm mono', type: 'text', value: s.authOverride, placeholder: 'e.g. Bearer wrong', autocomplete: 'off', spellcheck: 'false' });
  auth.addEventListener('change', () => { setSettings({ authOverride: auth.value.trim() }); toast(auth.value.trim() ? 'Auth override active' : 'Auth override cleared', { kind: auth.value.trim() ? 'warn' : 'info' }); });
  const authSec = section('Auth override',
    h('div', { class: 'notice notice-warn' }, icon('alert', 14), h('span', {}, 'Sent verbatim as the Authorization header through the proxy, replacing the proxy\'s OPENJEV_API_KEY. Use it to provoke 401/403 and learn the error shapes. Stored in this browser\'s localStorage.')),
    field('Authorization', auth, 'Empty = let the proxy decide (it injects OPENJEV_API_KEY when set).'));
  body.appendChild(authSec);

  // --- connection
  const h0 = getLastHealth();
  const conn = h('div', { class: 'kv mono' });
  const paintConn = (hh) => {
    clear(conn);
    const rows = [
      ['OpenJev URL', config.openjevUrl],
      ['Proxy origin', location.origin],
      ['Auth configured (proxy)', config.authConfigured ? 'yes' : 'no'],
      ['UI version', config.uiVersion + (config._fallback ? ' (config endpoint unavailable)' : '')],
      ['Upstream', hh ? (hh.proxyDown ? 'UI server unreachable' : hh.ok ? `ok · ${fmtMs(hh.upstream?.latencyMs)}` : `${hh.upstream?.errorType || 'down'}: ${hh.upstream?.error || ''}`) : 'not checked yet'],
      ['Models', (hh?.models || []).map((m) => m.name).join(', ') || '—'],
      ['Storage', isFallbackStore() ? 'memory + localStorage (IndexedDB unavailable)' : 'IndexedDB ojui v1'],
    ];
    for (const [k, v] of rows) conn.append(h('span', { class: 'kv-k faint' }, k), h('span', { class: 'kv-v' }, v));
  };
  paintConn(h0);
  body.appendChild(section('Connection',
    conn,
    h('div', { class: 'row' }, h('button', { class: 'btn sm', onClick: async () => paintConn(await checkHealthNow()) }, icon('refresh', 13), 'Check now'))));

  // --- danger
  body.appendChild(section('Danger zone',
    h('div', { class: 'danger-zone' },
      h('button', { class: 'btn sm danger', onClick: async () => { if (await confirmDialog('Clear the whole request log? Stats charts use it; totals are kept.', { danger: true, okLabel: 'Clear log' })) { await clearRequests(); toast('Request log cleared'); } } }, icon('trash', 13), 'Clear request log'),
      h('button', { class: 'btn sm danger', onClick: async () => { if (await confirmDialog('Reset the all-time totals (requests, tokens, cost)?', { danger: true, okLabel: 'Reset totals' })) { resetTotals(); toast('Totals reset'); navigate(location.hash || '#/'); } } }, icon('refresh', 13), 'Reset totals'),
      h('button', { class: 'btn sm danger', onClick: async () => { if (await confirmDialog('Delete ALL conversations? Export them first if you want to keep them.', { danger: true, okLabel: 'Delete all' })) { await deleteAllConversations(); toast('All conversations deleted'); navigate('#/'); } } }, icon('trash', 13), 'Delete all conversations'))));

  openModal({ title: 'Settings', body, wide: true, className: 'settings-modal', actions: [{ label: 'Done', kind: 'primary' }] });
  if (focusSection === 'auth') setTimeout(() => { authSec.scrollIntoView({ block: 'center' }); auth.focus(); }, 60);
}

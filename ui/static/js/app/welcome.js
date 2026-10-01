// app/welcome.js — the empty-conversation hero (builder B): tagline, three one-click
// examples (via oj:composer-load), C's template strip, and a 3-line "how it works" canvas.

import { h } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { emit } from '/js/core/bus.js';
import { navigate } from '/js/core/router.js';
import { createConversation, getSettings } from '/js/core/store.js';
import { J } from '/js/app/jev.js';

export const EXAMPLES = [
  {
    id: 'ex-triage', title: 'Ticket triage', blurb: 'noul + choice + score on one support message',
    state: 'Everything is down and we have a demo with our biggest client at noon.',
    questions: {
      urgent: { type: 'noul', instructions: 'Does the customer need a reply within the hour?' },
      team: { type: 'choice', instructions: 'Which team should handle it?', criteria: { outage: 'service down', billing: 'charges, refunds', feature: 'requests, how-to' } },
      tone: { type: 'score', instructions: 'How upset is the customer?', criteria: ['calm', 'annoyed', 'furious'] },
    },
  },
  {
    id: 'ex-review', title: 'Review sentiment', blurb: 'mixed polarity, graded intensity, sarcasm',
    state: 'Oh great, the battery lasts a whole two hours now. At least the screen is gorgeous.',
    questions: {
      polarity: { type: 'choice', instructions: 'Overall sentiment of the review', criteria: { positive: 'mostly praise', negative: 'mostly complaint', neutral: 'no clear feeling', mixed: 'both praise and complaint' } },
      intensity: { type: 'score', instructions: 'How strong is the feeling?', criteria: ['very mild', 'mild', 'moderate', 'strong', 'very strong'] },
      sarcasm: { type: 'noul', instructions: 'Is any part of the review sarcastic?' },
    },
  },
  {
    id: 'ex-shell', title: 'Shell command safety', blurb: 'guard an agent before it runs a command',
    state: 'curl -fsSL https://get.example.sh | sudo bash && rm -rf ~/.cache/*',
    questions: {
      destructive: { type: 'noul', instructions: 'Can this command delete or overwrite data?' },
      network_egress: { type: 'noul', instructions: 'Does it download or send data over the network?' },
      verdict: { type: 'choice', instructions: 'What should an agent do before running it?', criteria: { allow: 'safe to run unattended', confirm: 'ask the user first', block: 'never run' } },
    },
  },
];

const CHAT_EXAMPLES = [
  'Explain discrete diffusion language models in three short paragraphs.',
  'Write a Python function that parses a Server-Timing header into a dict.',
  'Give me 5 tricky yes/no questions to test a support-ticket classifier.',
];

function howItWorks() {
  const row = (n, a, b, c) => h('div', { class: 'hiw-row mono' },
    h('span', { class: 'hiw-n' }, n), h('span', { class: 'hiw-a' }, a), h('span', { class: 'hiw-arrow' }, '──►'), h('span', { class: 'hiw-b' }, b), h('span', { class: 'hiw-arrow' }, '──►'), h('span', { class: 'hiw-c' }, c));
  return h('div', { class: 'hiw card' },
    h('div', { class: 'hiw-title faint mono' }, 'how it works'),
    row('1', 'canvas in: state + q1 [?] q2 [?] q3 [?]', 'one read-only denoise pass', 'no text is ever written'),
    row('2', 'each [?] is one label token', 'P(yes/no) · P(A/B/C) · P(0/1/2)', 'the distribution is the answer'),
    row('3', 'noul → P(yes)', 'choice → argmax + confidence', 'score → E[level] = Σ i·pᵢ'),
    h('div', { class: 'hiw-foot faint' }, 'confidence = 1 − H(p)/ln K: 1 when certain, 0 when uniform. Question ids never reach the model; it sees q1, q2, q3.'));
}

function exampleChips(conv) {
  return h('div', { class: 'examples' }, EXAMPLES.map((ex) => h('button', {
    class: 'example card',
    onClick: () => emit('oj:composer-load', {
      newConversation: !conv || conv.mode !== 'systemone' || conv.turns.length > 0,
      state: ex.state, stateIsJson: false, questions: ex.questions, title: ex.title,
    }),
  },
  h('div', { class: 'example-head' }, icon('sparkles', 14), h('span', {}, ex.title)),
  h('div', { class: 'example-blurb faint' }, ex.blurb),
  h('div', { class: 'example-types' }, Object.values(ex.questions).map((q) => h('span', { class: `tchip type-${q.type}` }, q.type))))));
}

function quickStart() {
  const ta = h('textarea', { class: 'state-input', rows: 1, placeholder: 'Type any text and press Enter: OpenJev answers a starter yes/no question about it…' });
  ta.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      const text = ta.value.trim();
      if (!text) return;
      // Enter sends, like every chat app; the questions stay editable for the next ask
      emit('oj:composer-load', { newConversation: true, state: text, stateIsJson: false, submit: true });
    }
  });
  ta.addEventListener('input', () => { ta.style.height = 'auto'; ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`; });
  setTimeout(() => ta.focus(), 50);
  return h('div', { class: 'quickstart composer-box' }, ta,
    h('div', { class: 'composer-bar' },
      h('button', { class: 'chip', onClick: async () => { const c = await createConversation({ mode: 'systemone' }); navigate(`#/c/${c.id}`); } }, icon('bolt', 13), 'New decision'),
      h('button', { class: 'chip', onClick: async () => { const c = await createConversation({ mode: 'chat' }); navigate(`#/c/${c.id}`); } }, icon('chat', 13), 'New chat'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'send-btn', title: 'Start', onClick: () => ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' })) }, icon('send', 18))));
}

export function renderWelcome(el, { conv = null, standalone = false } = {}) {
  if (conv && conv.mode === 'chat') {
    el.appendChild(h('div', { class: 'welcome welcome-chat' },
      h('div', { class: 'hero-glow' }),
      h('div', { class: 'hero-mark' }, icon('chat', 28)),
      h('h1', { class: 'hero-title' }, 'Chat with ', h('span', { class: 'mono accent-2' }, getSettings().chatModel)),
      h('p', { class: 'hero-sub muted' }, 'The same DiffusionGemma, generating text instead of reading answers. Tokens stream in; watch ttft and tok/s.'),
      h('div', { class: 'chat-examples' }, CHAT_EXAMPLES.map((t) => h('button', {
        class: 'example card', onClick: () => emit('oj:composer-load', { chat: true, state: t }),
      }, h('span', {}, t))))));
    return;
  }
  const strip = h('div', { class: 'tpl-strip-host' });
  el.appendChild(h('div', { class: ['welcome', standalone && 'standalone'] },
    h('div', { class: 'hero-glow' }),
    h('div', { class: 'hero-mark' }, icon('logo', 30)),
    h('h1', { class: 'hero-title' }, 'Fast, calibrated, typed decisions'),
    h('p', { class: 'hero-sub muted' }, 'Ask OpenJev yes/no, choice and score questions about any text or image. Every answer is a probability distribution read straight off the model, in milliseconds.'),
    standalone ? quickStart() : null,
    h('div', { class: 'section-label faint mono' }, 'try one'),
    exampleChips(conv),
    h('div', { class: 'section-label faint mono' }, 'templates', h('a', { class: 'section-link', href: '#/templates' }, 'all templates', icon('chevron-right', 12))),
    strip,
    howItWorks()));
  try {
    const r = J.renderTemplateStrip(strip, { limit: 6 });
    if (r instanceof Node && !strip.contains(r)) strip.appendChild(r);
  } catch (err) { console.warn('[welcome] template strip failed', err); }
}

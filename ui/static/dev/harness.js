// Dev harness (builder C): boots B's router/theme with the mock store + mock OpenJev, calls
// C's init(), seeds a conversation by really sending requests through core/api.js, and
// renders a gallery of C's components on the home route. Open /dev-harness.html.

import { installMockOpenJev } from '/dev/mock-openjev.js';
installMockOpenJev();

const { el } = await import('/js/jev/util.js');
const { registerView, initRouter, navigate } = await import('/js/core/router.js');
const { on, emit } = await import('/js/core/bus.js');
const { getConfig, systemOne } = await import('/js/core/api.js');
const { sha256Hex } = await import('/js/core/metrics.js');
const store = await import('/js/core/store.js');
const { toast } = await import('/js/core/toast.js');
const J = await import('/js/jev/index.js');
const { TEMPLATES, getTemplate, templateToDraft } = await import('/js/jev/templates.js');

await getConfig();
await J.init();

on('oj:composer-load', (d) => toast(`oj:composer-load → ${d.title || 'draft'} · ${Object.keys(d.questions || {}).length} q${d.newConversation ? ' · new conversation' : ''}${d.images?.length ? ` · ${d.images.length} image` : ''}`, { kind: 'ok', timeout: 3500 }));
on('oj:composer-questions-changed', (d) => { const n = document.getElementById('dh-qstate'); if (n) n.textContent = JSON.stringify({ valid: d.valid, errors: d.errors, count: Object.keys(d.questions || {}).length }, null, 2); });

// ---------------------------------------------------------------- seed a conversation through the real api.js
const conv = await store.createConversation({ title: 'Harness: ticket triage' });
async function send(body, extra = {}) {
  const r = await systemOne(body, { source: 'thread', convId: conv.id, label: extra.label || null });
  const turn = {
    id: `t_${Math.random().toString(36).slice(2, 10)}`, kind: 'systemone', createdAt: Date.now(), status: r.ok ? 'ok' : 'error', source: extra.source || 'thread',
    label: extra.label || null, parentTurnId: extra.parentTurnId || null, request: body, imagesMeta: (body.images || []).map(() => ({ name: 'demo-shapes.png', type: 'image/png', bytes: 20000, width: 512, height: 512 })),
    response: r.data && r.ok ? r.data : null, http: r.http, error: r.error, bodyHash: await sha256Hex(JSON.stringify(body)), labels: extra.labels || {},
  };
  await store.appendTurn(conv.id, turn);
  return turn;
}
const tt = getTemplate('ticket-triage');
const b1 = { model: 'openjev-latest', state: tt.state, questions: tt.questions };
const t1 = await send(b1, { labels: { urgent: { correct: true }, team: { correct: true } } });
const q2 = JSON.parse(JSON.stringify(tt.questions));
q2.team.criteria.security = 'breaches, leaked keys';
delete q2.tone;
q2.sla_breach = { type: 'noul', instructions: 'Has the contractual SLA already been breached?' };
const b2 = { model: 'openjev-latest', state: tt.state, questions: q2, steps: 4 };
const t2 = await send(b2, { parentTurnId: t1.id, labels: { team: { correct: false, expected: 'outage' } } });
const t3 = await send(b2); // rerun: seed-stable
const bad = await send({ model: 'openjev-latest', state: 'x', questions: { tone: { type: 'score', criteria: 'calm' } } });
const seqT = getTemplate('sequential-chain');
const t4 = await send({ model: 'openjev-latest', state: seqT.state, questions: seqT.questions, sequential: true });
const imgDraft = await templateToDraft(getTemplate('image-yes-no'));
const t5 = await send({ model: 'openjev-latest', state: imgDraft.state, questions: imgDraft.questions, images: imgDraft.images.map((i) => i.dataUrl) });
// some batch-ish traffic for the stats dashboard
for (const s of getTemplate('sentiment').batchStates.slice(0, 6)) await systemOne({ model: 'openjev-latest', state: s, questions: getTemplate('sentiment').questions }, { source: 'batch' });
await systemOne({ model: 'nope', state: 'x', questions: { a: { type: 'noul' } } }, { source: 'other' });
const fresh = await store.getConversation(conv.id);
const ctxFor = (turn) => ({ conversation: fresh, settings: store.getSettings(), turnIndex: fresh.turns.findIndex((t) => t.id === turn.id), parentTurn: turn.parentTurnId ? fresh.turns.find((t) => t.id === turn.parentTurnId) : null });

// ---------------------------------------------------------------- chrome
const top = document.getElementById('topbar');
top.appendChild(el('div', { class: 'dh-top' },
  el('strong', {}, 'C dev harness'), el('span', { class: 'faint mono' }, 'mock store · mock OpenJev (MLX-style timing)'), el('span', { class: 'spacer' }),
  el('button', { class: 'btn sm', type: 'button', onclick: () => { const d = document.documentElement; d.setAttribute('data-theme', d.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'); } }, 'toggle theme')));
const side = document.getElementById('sidebar');
const link = (href, label) => el('a', { class: 'btn ghost sm', href }, label);
side.appendChild(el('nav', { class: 'dh-nav' },
  link('#/', 'Components'), link('#/templates', 'Templates'), link('#/batch', 'Batch'), link('#/stats', 'Stats'),
  link(`#/compare/${conv.id}/${t2.id}`, 'Compare (one turn)'), link(`#/compare/${conv.id}/${t1.id}/${t2.id}`, 'Compare (two turns)'),
  el('button', { class: 'btn ghost sm', type: 'button', onclick: () => emit('oj:open-inspector', { convId: conv.id, turnId: t2.id }) }, 'Inspector (turn #2)'),
  el('button', { class: 'btn ghost sm', type: 'button', onclick: () => emit('oj:open-inspector', { convId: conv.id, turnId: bad.id }) }, 'Inspector (422 turn)'),
  el('button', { class: 'btn ghost sm', type: 'button', onclick: () => J.openInspector({ conversation: fresh, turn: t5 }) }, 'Inspector (image turn)')));

// ---------------------------------------------------------------- component gallery (home)
registerView('welcome', {
  title: 'Components',
  mount(root) {
    const home = el('div', { class: 'dh-home' });
    root.appendChild(home);
    const sec = (title, ...kids) => { const s = el('section', { class: 'card dh-sec', dataset: { sec: title } }, el('h3', {}, title), ...kids); home.appendChild(s); return s; };

    const qeHost = el('div');
    sec('Question editor (composer instance)', qeHost, el('pre', { id: 'dh-qstate', class: 'mono dh-pre' }, '…'));
    const qe = J.mountQuestionEditor(qeHost, { questions: tt.questions, onChange: () => {} });
    window.__qe = qe;

    const r2 = el('div');
    sec('renderResult · turn #2 (re-ask of #1, steps 4) with deltas', r2);
    r2.append(J.renderResult(t2, ctxFor(t2)), J.renderTurnMeta(t2, ctxFor(t2)));
    const r3 = el('div');
    sec('renderTurnMeta · turn #3 (rerun of #2 → seed-stable)', r3);
    r3.append(J.renderTurnMeta(t3, ctxFor(t3)));
    const r1 = el('div');
    sec('renderResult · turn #1', r1);
    r1.append(J.renderResult(t1, ctxFor(t1)), J.renderTurnMeta(t1, ctxFor(t1)));
    const r5 = el('div');
    sec('renderResult · image-yes-no', r5);
    r5.append(J.renderResult(t5, ctxFor(t5)), J.renderTurnMeta(t5, ctxFor(t5)));
    const r4 = el('div');
    sec('renderResult · 22 questions (compact table)', r4);
    r4.append(J.renderResult(t4, ctxFor(t4)), J.renderTurnMeta(t4, ctxFor(t4)));
    const rb = el('div');
    sec('renderTurnMeta · 422 turn', rb);
    rb.append(J.renderTurnMeta(bad, ctxFor(bad)));

    const cs = el('div');
    sec('renderConversationStats', cs);
    cs.append(J.renderConversationStats(fresh, { settings: store.getSettings() }));

    const je = el('div');
    sec('mountJsonEditor (tree, schema)', je);
    J.mountJsonEditor(je, { value: getTemplate('pr-risk').state, mode: 'tree' });

    const strip = el('div');
    sec('renderTemplateStrip', strip);
    J.renderTemplateStrip(strip, { limit: 6 });

    const sn = el('pre', { class: 'mono dh-pre', style: { maxHeight: '420px' } });
    sec('buildSnippet python · turn #2', sn);
    sn.textContent = J.buildSnippet('python', t2, null) + '\n\n' + J.buildSnippet('curl', t2, null);

    sec('validateQuestions(bad set)', el('pre', { class: 'mono dh-pre' }, JSON.stringify(J.validateQuestions({ a: { type: 'choice', criteria: {} }, 'b c': { type: 'score', criteria: Array.from({ length: 11 }, (_, i) => String(i)) }, d: { type: 'noul', criteria: { maybe: 'x' } } }), null, 2)));
    return () => qe.destroy();
  },
});

store.setLastConvId(null);
window.__harness = { conv: fresh, turns: { t1, t2, t3, t4, t5, bad }, TEMPLATES, J, store };
initRouter();

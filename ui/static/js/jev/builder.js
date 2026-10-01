// Visual question builder (builder C): one card per question with qid, type, instructions
// and a type-specific criteria editor (noul true/false, choice options with bulk paste,
// score levels with presets). Non-string values are preserved and flagged "complex".

import { el, isObj, uniqueKey, describe } from '/js/jev/util.js';
import { pathKey, LIMITS } from '/js/jev/validate.js';
import { icon } from '/js/core/icons.js';
import { confirmDialog, openModal } from '/js/core/modal.js';

export const SCORE_PRESETS = [
  { id: 'lmh', label: 'low / medium / high', levels: ['low', 'medium', 'high'] },
  { id: 'likert', label: '1–5 Likert', levels: ['1 strongly disagree', '2 disagree', '3 neutral', '4 agree', '5 strongly agree'] },
  { id: 'mood', label: 'calm / annoyed / furious', levels: ['calm', 'annoyed', 'furious'] },
  { id: 'npy', label: 'no / partly / yes', levels: ['no', 'partly', 'yes'] },
  { id: 'eleven', label: '0–10 (11 levels is invalid: show why)', levels: Array.from({ length: 11 }, (_, i) => String(i)) },
];

const TYPE_LABEL = { noul: 'noul · yes/no', choice: 'choice', score: 'score' };
let seq = 0;
const nid = () => `qb${++seq}`;

function fromQuestions(questions) {
  if (!isObj(questions)) return [];
  return Object.entries(questions).map(([qid, q]) => toItem(qid, q));
}

function toItem(qid, q) {
  const src = isObj(q) ? q : {};
  const extra = {};
  for (const [k, v] of Object.entries(src)) if (!['type', 'instructions', 'criteria'].includes(k)) extra[k] = v;
  const it = { uid: nid(), qid, type: src.type, instructions: src.instructions, hasInstr: 'instructions' in src, extra, collapsed: false, rawCriteria: undefined };
  const c = src.criteria;
  if (src.type === 'choice') {
    if (isObj(c)) it.options = Object.entries(c).map(([k, v]) => ({ uid: nid(), k, v }));
    else { it.options = []; it.rawCriteria = c; }
  } else if (src.type === 'score') {
    if (Array.isArray(c)) it.levels = c.map((v) => ({ uid: nid(), v }));
    else { it.levels = []; it.rawCriteria = c; }
  } else if (src.type === 'noul') {
    if (isObj(c)) it.noul = { ...c };
    else if (c === null || c === undefined) it.noul = null;
    else { it.noul = null; it.rawCriteria = c; }
    it.noulHadCriteria = 'criteria' in src;
  } else {
    it.rawCriteria = c;
  }
  return it;
}

function toQuestion(it) {
  const q = { type: it.type };
  if (it.instructions !== undefined && !(it.instructions === '' && !it.hasInstr)) q.instructions = it.instructions;
  if (it.type === 'choice') {
    if (it.rawCriteria !== undefined && !it.options.length) q.criteria = it.rawCriteria;
    else { q.criteria = {}; for (const o of it.options) q.criteria[o.k] = o.v; }
  } else if (it.type === 'score') {
    q.criteria = it.rawCriteria !== undefined && !it.levels.length ? it.rawCriteria : it.levels.map((l) => l.v);
  } else if (it.type === 'noul') {
    if (it.rawCriteria !== undefined) q.criteria = it.rawCriteria;
    else if (it.noul) {
      const c = {};
      for (const [k, v] of Object.entries(it.noul)) if (v !== undefined && !(v === '' && (k === 'true' || k === 'false'))) c[k] = v;
      if (Object.keys(c).length || it.noulHadCriteria) q.criteria = Object.keys(c).length ? c : null;
    } else if (it.noulHadCriteria) q.criteria = null;
  } else if (it.rawCriteria !== undefined) q.criteria = it.rawCriteria;
  Object.assign(q, it.extra);
  return q;
}

const isComplex = (v) => v !== null && v !== undefined && typeof v !== 'string';

/**
 * Mount the builder. → {get, set, showErrors(errors, warnings), localErrors(), addQuestion(type, qid), focus(), destroy}
 */
export function mountBuilder(host, { questions = {}, onChange, limits = LIMITS } = {}) {
  let items = fromQuestions(questions);
  const root = el('div', { class: 'qb' });
  host.appendChild(root);
  let fields = new Map(); // pathKey → element[]
  let cardErr = new Map(); // qid → container
  let lastErrors = [], lastWarnings = [];

  const reg = (path, node) => { const k = pathKey(path); if (!fields.has(k)) fields.set(k, []); fields.get(k).push(node); return node; };

  function get() {
    const out = {};
    for (const it of items) out[it.qid] = toQuestion(it);
    return out;
  }
  function localErrors() {
    const errs = [];
    const seen = new Map();
    for (const it of items) seen.set(it.qid, (seen.get(it.qid) || 0) + 1);
    for (const [qid, n] of seen) if (n > 1) errs.push({ path: [qid], message: `duplicate id "${qid}" (${n}×): only the last one would be sent` });
    for (const it of items) {
      if (it.type === 'choice') {
        const ks = new Map();
        for (const o of it.options) ks.set(o.k, (ks.get(o.k) || 0) + 1);
        for (const [k, n] of ks) if (n > 1) errs.push({ path: [it.qid, 'criteria', k], message: `duplicate option "${k}"` });
      }
    }
    return errs;
  }
  function changed(structural = false) {
    if (structural) render();
    if (typeof onChange === 'function') onChange(get());
  }

  // ------------------------------------------------ rendering
  function render() {
    const focusKey = document.activeElement?.dataset?.qbFocus;
    const caret = document.activeElement?.selectionStart;
    fields = new Map(); cardErr = new Map();
    root.textContent = '';
    if (!items.length) {
      root.appendChild(el('div', { class: 'qb-empty' },
        el('div', { class: 'muted' }, 'No questions yet. A request needs at least one.'),
        el('div', { class: 'row' },
          ['noul', 'choice', 'score'].map((t) => el('button', { class: 'btn sm', type: 'button', onclick: () => api.addQuestion(t) }, icon('plus', 14), ` ${t}`)))));
    }
    items.forEach((it, idx) => root.appendChild(card(it, idx)));
    showErrors(lastErrors, lastWarnings);
    if (focusKey) {
      const n = root.querySelector(`[data-qb-focus="${CSS.escape(focusKey)}"]`);
      if (n) { n.focus(); if (caret !== undefined && n.setSelectionRange) { try { n.setSelectionRange(caret, caret); } catch { /* not text */ } } }
    }
  }

  function autoGrow(ta) { ta.style.height = 'auto'; ta.style.height = `${Math.min(220, ta.scrollHeight + 2)}px`; }

  function card(it, idx) {
    const typeColor = `var(--type-${it.type || 'noul'})`;
    const qidInput = reg([it.qid], el('input', {
      class: 'input mono qb-qid', value: it.qid, spellcheck: 'false', 'aria-label': 'question id', dataset: { qbFocus: `${it.uid}:qid` },
      oninput: (ev) => { it.qid = ev.target.value; changed(); },
      onchange: () => render(),
    }));
    const typeSel = el('select', { class: 'select qb-type', 'aria-label': 'question type', onchange: (ev) => convert(it, ev.target.value, ev.target) },
      ['noul', 'choice', 'score'].map((t) => el('option', { value: t, selected: it.type === t }, TYPE_LABEL[t])),
      ['noul', 'choice', 'score'].includes(it.type) ? null : el('option', { value: String(it.type), selected: true }, `unknown: ${it.type}`));
    reg([it.qid, 'type'], typeSel);
    const move = (d) => { const j = idx + d; if (j < 0 || j >= items.length) return; [items[idx], items[j]] = [items[j], items[idx]]; changed(true); };
    const head = el('div', { class: 'qb-head' },
      el('span', { class: 'qb-idx mono faint', title: 'the model sees this question as q' + (idx + 1) }, `q${idx + 1}`),
      qidInput, typeSel,
      summaryBadge(it),
      el('span', { class: 'spacer' }),
      iconBtn('chevron-down', 'Move up', () => move(-1), idx === 0, 'qb-up'),
      iconBtn('chevron-down', 'Move down', () => move(1), idx === items.length - 1),
      iconBtn('copy', 'Duplicate', () => {
        const copy = toItem(uniqueKey(`${it.qid}_copy`, new Set(items.map((x) => x.qid))), JSON.parse(JSON.stringify(toQuestion(it))));
        items.splice(idx + 1, 0, copy); changed(true);
      }),
      iconBtn('trash', 'Delete', () => { items.splice(idx, 1); changed(true); }),
      iconBtn(it.collapsed ? 'chevron-right' : 'chevron-down', it.collapsed ? 'Expand' : 'Collapse', () => { it.collapsed = !it.collapsed; render(); }),
    );
    const errBox = el('div', { class: 'qb-errs' });
    cardErr.set(it.qid, errBox);
    const c = el('div', { class: ['qb-card', it.collapsed && 'collapsed'], style: { '--qb-color': typeColor }, dataset: { qid: it.qid, type: it.type } }, head);
    if (!it.collapsed) c.appendChild(el('div', { class: 'qb-body' }, instructionsField(it), criteriaEditor(it)));
    c.appendChild(errBox);
    return c;
  }

  function summaryBadge(it) {
    let txt = '';
    if (it.type === 'choice') txt = `${it.options.length} options`;
    else if (it.type === 'score') txt = `${it.levels.length} levels`;
    else if (it.type === 'noul') txt = it.noul && (it.noul.true || it.noul.false) ? 'with criteria' : 'yes / no';
    const complex = isComplex(it.instructions) || it.rawCriteria !== undefined ||
      (it.options || []).some((o) => isComplex(o.v)) || (it.levels || []).some((l) => isComplex(l.v)) ||
      (it.noul && (isComplex(it.noul.true) || isComplex(it.noul.false)));
    return el('span', { class: 'row qb-badges' },
      el('span', { class: 'badge faint mono' }, txt),
      complex ? el('span', { class: 'badge qb-complex', title: 'contains non-string values; edit those in the JSON tab' }, 'complex — edit in JSON') : null);
  }

  function iconBtn(name, title, onclick, disabled = false, extraCls = '') {
    return el('button', { class: ['icon-btn', 'qb-ib', extraCls], type: 'button', title, 'aria-label': title, disabled, onclick }, icon(name, 15));
  }

  function instructionsField(it) {
    if (isComplex(it.instructions)) {
      return el('label', { class: 'qb-field' }, el('span', { class: 'qb-label' }, 'instructions ', el('span', { class: 'badge qb-complex' }, 'complex — edit in JSON')),
        reg([it.qid, 'instructions'], el('textarea', { class: 'textarea mono qb-instr', rows: 2, readOnly: true, value: JSON.stringify(it.instructions) })));
    }
    const ta = reg([it.qid, 'instructions'], el('textarea', {
      class: 'textarea qb-instr', rows: 1, value: it.instructions ?? '', placeholder: placeholderFor(it.type), dataset: { qbFocus: `${it.uid}:instr` },
      oninput: (ev) => { it.instructions = ev.target.value; it.hasInstr = it.hasInstr || ev.target.value !== ''; autoGrow(ev.target); changed(); },
    }));
    requestAnimationFrame(() => autoGrow(ta));
    return el('label', { class: 'qb-field' }, el('span', { class: 'qb-label' }, 'instructions'), ta);
  }

  function placeholderFor(type) {
    return type === 'noul' ? 'A yes/no statement or question, e.g. "Does the customer need a reply within the hour?"'
      : type === 'choice' ? 'What to pick, e.g. "Which team should handle it?"'
        : type === 'score' ? 'What to rate on the scale below, e.g. "How upset is the customer?"' : 'instructions';
  }

  function criteriaEditor(it) {
    if (it.rawCriteria !== undefined && it.type !== 'noul' && !(it.options?.length || it.levels?.length)) {
      return el('div', { class: 'qb-field' }, el('span', { class: 'qb-label' }, 'criteria ', el('span', { class: 'badge qb-complex' }, 'invalid shape — edit in JSON')),
        reg([it.qid, 'criteria'], el('textarea', { class: 'textarea mono', rows: 2, readOnly: true, value: JSON.stringify(it.rawCriteria) })),
        el('button', { class: 'btn sm ghost', type: 'button', onclick: () => { it.rawCriteria = undefined; changed(true); } }, 'Discard and start fresh'));
    }
    if (it.type === 'noul') return noulEditor(it);
    if (it.type === 'choice') return choiceEditor(it);
    if (it.type === 'score') return scoreEditor(it);
    return el('div', { class: 'muted' }, `Unknown type "${it.type}". Pick noul, choice or score.`);
  }

  function noulEditor(it) {
    if (it.rawCriteria !== undefined) {
      return el('div', { class: 'qb-field' }, el('span', { class: 'qb-label' }, 'criteria ', el('span', { class: 'badge qb-complex' }, 'invalid shape — edit in JSON')),
        reg([it.qid, 'criteria'], el('input', { class: 'input mono', readOnly: true, value: JSON.stringify(it.rawCriteria) })));
    }
    const field = (key, label) => {
      const v = it.noul?.[key];
      if (isComplex(v)) return el('label', { class: 'qb-field' }, el('span', { class: 'qb-label' }, label, ' ', el('span', { class: 'badge qb-complex' }, 'complex')),
        reg([it.qid, 'criteria', key], el('input', { class: 'input mono', readOnly: true, value: JSON.stringify(v) })));
      return el('label', { class: 'qb-field' }, el('span', { class: 'qb-label' }, label),
        reg([it.qid, 'criteria', key], el('input', {
          class: 'input', value: v ?? '', placeholder: '(optional)', dataset: { qbFocus: `${it.uid}:${key}` },
          oninput: (ev) => { it.noul = { ...(it.noul || {}), [key]: ev.target.value === '' ? undefined : ev.target.value }; changed(); },
        })));
    };
    const extraKeys = Object.keys(it.noul || {}).filter((k) => k !== 'true' && k !== 'false');
    const hasCrit = it.noul && (it.noul.true !== undefined || it.noul.false !== undefined);
    if (!hasCrit && !extraKeys.length && !it.showCrit) {
      return el('button', { class: 'btn ghost sm qb-addcrit', type: 'button', title: 'optional: say what yes and what no mean', onclick: () => { it.showCrit = true; render(); } }, icon('plus', 13), ' yes / no criteria');
    }
    return el('div', { class: 'qb-noul' },
      el('div', { class: 'qb-grid2' }, field('true', 'yes means'), field('false', 'no means')),
      extraKeys.length ? el('div', { class: 'qb-extra' }, extraKeys.map((k) => reg([it.qid, 'criteria', k], el('span', { class: 'badge qb-bad mono' }, `criteria.${k}`))),
        el('button', { class: 'btn ghost sm', type: 'button', onclick: () => { for (const k of extraKeys) delete it.noul[k]; changed(true); } }, 'remove extra keys')) : null);
  }

  function choiceEditor(it) {
    const list = el('div', { class: 'qb-opts' });
    it.options.forEach((o, i) => {
      const move = (d) => { const j = i + d; if (j < 0 || j >= it.options.length) return; [it.options[i], it.options[j]] = [it.options[j], it.options[i]]; changed(true); };
      const keyIn = reg([it.qid, 'criteria', o.k], el('input', {
        class: 'input mono qb-optkey', value: o.k, placeholder: 'option', spellcheck: 'false', dataset: { qbFocus: `${o.uid}:k` },
        oninput: (ev) => { o.k = ev.target.value; changed(); },
        onchange: () => render(),
        onkeydown: (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); addOption(it, i + 1); } },
      }));
      const descIn = isComplex(o.v)
        ? el('span', { class: 'row qb-desc-complex' }, el('input', { class: 'input mono', readOnly: true, value: JSON.stringify(o.v) }), el('span', { class: 'badge qb-complex' }, 'complex'))
        : el('input', {
          class: 'input qb-optdesc', value: o.v ?? '', placeholder: 'description (optional)', dataset: { qbFocus: `${o.uid}:v` },
          oninput: (ev) => { o.v = ev.target.value === '' ? null : ev.target.value; changed(); },
          onkeydown: (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); addOption(it, i + 1); } },
        });
      list.appendChild(el('div', { class: 'qb-opt' },
        el('span', { class: 'qb-letter mono faint', title: 'the label token the model reads for this option' }, letter(i)),
        keyIn, descIn,
        iconBtn('chevron-down', 'Move up', () => move(-1), i === 0, 'qb-up'),
        iconBtn('chevron-down', 'Move down', () => move(1), i === it.options.length - 1),
        iconBtn('x', 'Remove option', () => { it.options.splice(i, 1); changed(true); })));
    });
    const n = it.options.length;
    return el('div', { class: 'qb-field' },
      el('div', { class: 'qb-label row' }, 'options', el('span', { class: 'spacer' }),
        el('span', { class: ['mono', n > limits.choiceMaxOptions ? 'qb-over' : 'faint'] }, `${n} / ${limits.choiceMaxOptions}`)),
      reg([it.qid, 'criteria'], list),
      el('div', { class: 'row qb-actions' },
        el('button', { class: 'btn sm ghost', type: 'button', onclick: () => addOption(it) }, icon('plus', 14), ' option'),
        el('button', { class: 'btn sm ghost', type: 'button', onclick: () => bulkPaste(it) }, icon('upload', 14), ' bulk paste'),
        n > 1 ? el('button', { class: 'btn sm ghost', type: 'button', title: 'sort options A→Z', onclick: () => { it.options.sort((a, b) => a.k.localeCompare(b.k)); changed(true); } }, 'sort') : null));
  }

  function letter(i) {
    // Mirrors the README: A/B/C… for a choice; beyond 26 the server uses its own label tokens.
    return i < 26 ? String.fromCharCode(65 + i) : `#${i + 1}`;
  }

  function addOption(it, at = it.options.length) {
    const taken = new Set(it.options.map((o) => o.k));
    const o = { uid: nid(), k: uniqueKey(`option_${it.options.length + 1}`, taken), v: null };
    it.options.splice(at, 0, o);
    changed(true);
    const n = root.querySelector(`[data-qb-focus="${o.uid}:k"]`);
    if (n) { n.focus(); n.select(); }
  }

  function bulkPaste(it) {
    const ta = el('textarea', { class: 'textarea mono', rows: 10, placeholder: 'one option per line\nbilling: charges, refunds\noutage: service down\nfeature' });
    const parse = () => ta.value.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
      const m = l.match(/^([^:]+?)\s*:\s*(.*)$/);
      return m ? { k: m[1].trim(), v: m[2].trim() || null } : { k: l, v: null };
    });
    const preview = el('div', { class: 'faint mono' }, '0 options');
    ta.addEventListener('input', () => { preview.textContent = `${parse().length} options`; });
    const apply = (replace) => {
      const rows = parse().map((r) => ({ uid: nid(), ...r }));
      if (replace) it.options = rows; else it.options.push(...rows);
      m.close(); changed(true);
    };
    const m = openModal({
      title: `Bulk paste options · ${it.qid}`,
      body: el('div', { class: 'col' }, el('div', { class: 'muted' }, 'One option per line. "name: description" sets a description.'), ta, preview),
      actions: [
        { label: 'Cancel', kind: 'ghost', onClick: () => m.close() },
        { label: 'Append', onClick: () => apply(false) },
        { label: 'Replace all', kind: 'primary', onClick: () => apply(true) },
      ],
    });
    setTimeout(() => ta.focus(), 30);
  }

  function scoreEditor(it) {
    const list = el('div', { class: 'qb-levels' });
    it.levels.forEach((l, i) => {
      const move = (d) => { const j = i + d; if (j < 0 || j >= it.levels.length) return; [it.levels[i], it.levels[j]] = [it.levels[j], it.levels[i]]; changed(true); };
      const input = isComplex(l.v)
        ? el('span', { class: 'row qb-desc-complex' }, el('input', { class: 'input mono', readOnly: true, value: JSON.stringify(l.v) }), el('span', { class: 'badge qb-complex' }, 'complex'))
        : reg([it.qid, 'criteria', String(i)], el('input', {
          class: 'input', value: l.v ?? '', placeholder: `level ${i}`, dataset: { qbFocus: `${l.uid}:v` },
          oninput: (ev) => { l.v = ev.target.value; changed(); },
          onkeydown: (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); it.levels.splice(i + 1, 0, { uid: nid(), v: '' }); changed(true); focusLevel(it.levels[i + 1]); } },
        }));
      list.appendChild(el('div', { class: ['qb-level', i >= limits.scoreMaxLevels && 'qb-over-row'] },
        el('span', { class: 'qb-letter mono', title: 'the label token the model reads for this level' }, String(i)),
        input,
        iconBtn('chevron-down', 'Move up', () => move(-1), i === 0, 'qb-up'),
        iconBtn('chevron-down', 'Move down', () => move(1), i === it.levels.length - 1),
        iconBtn('x', 'Remove level', () => { it.levels.splice(i, 1); changed(true); })));
    });
    const n = it.levels.length;
    const presetSel = el('select', {
      class: 'select sm qb-preset', 'aria-label': 'score presets',
      onchange: async (ev) => {
        const p = SCORE_PRESETS.find((x) => x.id === ev.target.value);
        ev.target.value = '';
        if (!p) return;
        if (it.levels.some((l) => l.v) && !(await confirmDialog(`Replace ${it.levels.length} levels with "${p.label}"?`))) return;
        it.levels = p.levels.map((v) => ({ uid: nid(), v }));
        changed(true);
      },
    }, el('option', { value: '' }, 'presets…'), SCORE_PRESETS.map((p) => el('option', { value: p.id }, p.label)));
    return el('div', { class: 'qb-field' },
      el('div', { class: 'qb-label row' }, 'levels (low → high, answer is E = Σ i·pᵢ)', el('span', { class: 'spacer' }),
        el('span', { class: ['mono', n > limits.scoreMaxLevels ? 'qb-over' : 'faint'] }, `${n} / ${limits.scoreMaxLevels}`)),
      reg([it.qid, 'criteria'], list),
      el('div', { class: 'row qb-actions' },
        el('button', { class: 'btn sm ghost', type: 'button', disabled: n >= 12, onclick: () => { it.levels.push({ uid: nid(), v: '' }); changed(true); focusLevel(it.levels[it.levels.length - 1]); } }, icon('plus', 14), ' level'),
        presetSel,
        n > 1 ? el('button', { class: 'btn sm ghost', type: 'button', title: 'reverse the order', onclick: () => { it.levels.reverse(); changed(true); } }, 'reverse') : null));
  }
  function focusLevel(l) { const n = root.querySelector(`[data-qb-focus="${l.uid}:v"]`); if (n) n.focus(); }

  async function convert(it, to, selectEl) {
    const from = it.type;
    if (from === to) return;
    const hasCriteria = (from === 'choice' && it.options.length) || (from === 'score' && it.levels.length) || (from === 'noul' && it.noul && Object.values(it.noul).some((v) => v));
    if (from === 'noul' && hasCriteria || to === 'noul' && hasCriteria) {
      const ok = await confirmDialog(`Convert "${it.qid}" from ${from} to ${to}? Its criteria will be dropped.`, { danger: true });
      if (!ok) { selectEl.value = from; return; }
    }
    if (to === 'score') {
      it.levels = from === 'choice' ? it.options.map((o) => ({ uid: nid(), v: o.k })) : ['low', 'medium', 'high'].map((v) => ({ uid: nid(), v }));
    } else if (to === 'choice') {
      it.options = from === 'score'
        ? it.levels.map((l, i) => ({ uid: nid(), k: typeof l.v === 'string' && l.v.trim() ? l.v : `level_${i}`, v: null }))
        : [{ uid: nid(), k: 'yes', v: null }, { uid: nid(), k: 'no', v: null }];
      const seen = new Set();
      for (const o of it.options) { o.k = uniqueKey(o.k, seen); seen.add(o.k); }
    } else if (to === 'noul') {
      it.noul = null; it.noulHadCriteria = false;
    }
    it.rawCriteria = undefined;
    it.type = to;
    changed(true);
  }

  // ------------------------------------------------ errors
  function showErrors(errors = [], warnings = []) {
    lastErrors = errors; lastWarnings = warnings;
    for (const nodes of fields.values()) for (const n of nodes) { n.classList.remove('qb-invalid', 'qb-warn'); n.removeAttribute('data-err'); }
    for (const box of cardErr.values()) box.textContent = '';
    root.querySelectorAll('.qb-card.has-err').forEach((c) => c.classList.remove('has-err'));
    const mark = (list, cls) => {
      for (const e of list) {
        const path = e.path || [];
        let marked = false;
        for (let n = path.length; n >= 1 && !marked; n--) {
          const nodes = fields.get(pathKey(path.slice(0, n)));
          if (nodes) { for (const node of nodes) { node.classList.add(cls); node.setAttribute('data-err', e.message); node.title = e.message; } marked = true; }
        }
        const box = path.length ? cardErr.get(path[0]) : null;
        if (box) {
          box.appendChild(el('div', { class: ['qb-err', cls === 'qb-warn' && 'warn', e.server && 'server'] }, icon('alert', 13), ' ',
            path.length > 1 ? el('span', { class: 'mono' }, path.slice(1).join(' › ') + ': ') : null, e.message, e.server ? el('span', { class: 'badge' }, 'server 422') : null));
          if (cls === 'qb-invalid') box.closest('.qb-card')?.classList.add('has-err');
        }
      }
    };
    mark(warnings, 'qb-warn');
    mark(errors, 'qb-invalid');
  }

  const api = {
    get,
    set(qs) { items = fromQuestions(qs); render(); },
    showErrors,
    localErrors,
    addQuestion(type = 'noul', qid) {
      const taken = new Set(items.map((x) => x.qid));
      const id = qid && !taken.has(qid) ? qid : uniqueKey(qid || `q${items.length + 1}`, taken);
      const q = type === 'choice' ? { type, instructions: '', criteria: { option_a: null, option_b: null } }
        : type === 'score' ? { type, instructions: '', criteria: ['low', 'medium', 'high'] } : { type: 'noul', instructions: '' };
      const it = toItem(id, q);
      items.push(it);
      changed(true);
      const n = root.querySelector(`[data-qb-focus="${it.uid}:instr"]`);
      if (n) { n.focus(); n.scrollIntoView({ block: 'nearest' }); }
      return id;
    },
    scrollTo(qid) { const c = root.querySelector(`.qb-card[data-qid="${CSS.escape(qid)}"]`); if (c) { c.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); c.classList.add('flash'); setTimeout(() => c.classList.remove('flash'), 900); } },
    focus() { const n = root.querySelector('input,textarea'); if (n) n.focus(); },
    destroy() { root.remove(); },
    get element() { return root; },
  };
  render();
  return api;
}

export { describe };

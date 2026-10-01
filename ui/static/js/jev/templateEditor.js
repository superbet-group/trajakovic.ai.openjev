// Template editor (builder C): one modal that creates, edits and duplicates user templates.
// Built-ins are never written: duplicate mode copies one into a new user template. The
// question set is a full question editor; batch states round-trip through parseStates.

import { el, isObj } from '/js/jev/util.js';
import { mountQuestionEditor } from '/js/jev/questionEditor.js';
import { parseStates } from '/js/jev/batch.js';
import { categories } from '/js/jev/templates.js';
import { saveUserTemplate } from '/js/core/store.js';
import { statesToBatchInput, uid } from '/js/core/format.js';
import { openModal, confirmDialog } from '/js/core/modal.js';
import { toast } from '/js/core/toast.js';

const BATCH_FORMATS = [['lines', 'lines'], ['blocks', 'blank-line blocks'], ['jsonl', 'JSONL']];
const OPTION_FIELDS = [['steps', 1, 8], ['samples', 1, 32], ['think', 0, 4096]];

/** Stored batch states → {format, input} for the textarea (JSON templates store JSON text). */
function batchText(src) {
  const raw = Array.isArray(src.batchStates) ? src.batchStates : [];
  const values = src.stateIsJson ? raw.map((s) => { try { return JSON.parse(s); } catch { return s; } }) : raw;
  if (!values.length) return { format: src.stateIsJson || src.batchFormat === 'jsonl' ? 'jsonl' : 'lines', input: '' };
  const bi = statesToBatchInput(values);
  if ((src.stateIsJson || src.batchFormat === 'jsonl') && bi.format !== 'jsonl') {
    return { format: 'jsonl', input: values.map((v) => JSON.stringify(typeof v === 'string' ? v.trim() : v)).join('\n') };
  }
  return { format: bi.format, input: bi.input };
}

function stateText(src) {
  if (!src.stateIsJson) return String(src.state ?? '');
  return typeof src.state === 'string' ? src.state : JSON.stringify(src.state ?? {}, null, 2);
}

/** openTemplateEditor({ mode: 'create'|'edit'|'duplicate', template?, seed?, onSaved? }) → Promise<UserTemplate|null> */
export function openTemplateEditor({ mode = 'create', template = null, seed = null, onSaved } = {}) {
  const src = (mode === 'create' ? seed : template) || {};
  const field = (label, control, hint) => el('label', { class: 'col tpl-field' }, el('span', { class: 'qb-label' }, label), control, hint || null);

  const titleIn = el('input', { class: 'input', type: 'text', maxlength: 80, required: true, autofocus: true, placeholder: 'e.g. Ticket triage (EU)',
    value: mode === 'duplicate' ? `${src.title || 'Template'} (copy)` : (src.title || '') });
  const listId = uid('tpl-cats');
  const catIn = el('input', { class: 'input', type: 'text', list: listId, placeholder: 'mine',
    value: mode === 'edit' ? (src.category || 'mine') : 'mine' });
  const catList = el('datalist', { id: listId }, categories().map((c) => el('option', { value: c })));
  const descIn = el('input', { class: 'input', type: 'text', placeholder: 'What the template is for (optional)', value: mode === 'create' ? '' : (src.description || '') });

  const stateTa = el('textarea', { class: 'textarea mono', rows: 4, spellcheck: 'false', placeholder: 'The default state loaded with the template', value: stateText(src) });
  const jsonBox = el('input', { type: 'checkbox', checked: !!src.stateIsJson, onchange: () => syncFormat() });

  const bt = batchText(src);
  const batchTa = el('textarea', { class: 'textarea mono', rows: 6, spellcheck: 'false', value: bt.input, oninput: () => countStates() });
  const fmtSel = el('select', { class: 'select sm', 'aria-label': 'batch states format', onchange: () => countStates() },
    BATCH_FORMATS.map(([v, label]) => el('option', { value: v, selected: v === bt.format }, label)));
  const batchHint = el('span', { class: 'mono faint' });

  const o = src.options || {};
  const optIns = Object.fromEntries(OPTION_FIELDS.map(([k, min, max]) => [k,
    el('input', { class: 'input sm mono tpl-opt', type: 'number', min, max, placeholder: 'default', 'aria-label': k, value: o[k] ?? '' })]));
  const seqBox = el('input', { type: 'checkbox', checked: !!o.sequential });

  const qHost = el('div', { class: 'tpl-editor-qe' });
  const errBox = el('div', { class: 'qb-err tpl-editor-err', role: 'alert', hidden: true });

  // JSON templates store every batch state as JSON text, so their batch input is always JSONL
  function syncFormat() {
    if (jsonBox.checked) fmtSel.value = 'jsonl';
    fmtSel.disabled = jsonBox.checked;
    countStates();
  }
  function countStates() {
    const r = parseStates(batchTa.value, fmtSel.value);
    batchHint.textContent = `${r.states.length} state${r.states.length === 1 ? '' : 's'}${r.errors.length ? ` · ${r.errors.length} parse errors: ${r.errors[0]}` : ''}`;
    batchHint.classList.toggle('err', r.errors.length > 0);
    return r;
  }

  const body = el('div', { class: 'col tpl-editor' },
    field('Title', titleIn),
    el('div', { class: 'row tpl-editor-pair' }, field('Category', el('span', {}, catIn, catList)), field('Description', descIn)),
    field('State', stateTa, el('label', { class: 'row faint' }, jsonBox, ' JSON state (an object or array)')),
    el('div', { class: 'col tpl-field' },
      el('div', { class: 'row' }, el('span', { class: 'qb-label' }, 'Batch states'), el('span', { class: 'spacer' }), batchHint, fmtSel),
      batchTa),
    el('div', { class: 'col tpl-field' },
      el('span', { class: 'qb-label' }, 'Options (empty = server default)'),
      el('div', { class: 'row tpl-editor-opts' },
        OPTION_FIELDS.map(([k]) => el('label', { class: 'row faint' }, k, optIns[k])),
        el('label', { class: 'row faint' }, seqBox, ' sequential'))),
    el('div', { class: 'col tpl-field' }, el('span', { class: 'qb-label' }, 'Questions'), qHost),
    errBox);

  const qe = mountQuestionEditor(qHost, { questions: isObj(src.questions) ? src.questions : {}, emit: false, tab: 'builder', saveButton: false });
  syncFormat();

  function fail(lines) {
    errBox.textContent = '';
    errBox.append(...lines.flatMap((l, i) => (i ? [el('br'), l] : [l])));
    errBox.hidden = false;
    errBox.scrollIntoView?.({ block: 'nearest' });
    return false;
  }

  function collect() {
    const errs = [];
    const title = titleIn.value.trim();
    if (!title) errs.push('Title is required');
    const v = qe.validate();
    if (!v.valid) for (const e of v.errors.slice(0, 3)) errs.push(`${e.path?.length ? e.path.join(' › ') : 'questions'}: ${e.message}`);
    const json = jsonBox.checked;
    let state = stateTa.value;
    if (json) {
      try {
        state = JSON.parse(stateTa.value);
        if (!state || typeof state !== 'object') throw new Error('expected an object or array');
      } catch (e) { errs.push(`State: not a JSON object or array (${e.message})`); }
    }
    const fmt = fmtSel.value;
    const ps = countStates();
    if (ps.errors.length) errs.push(`Batch states: ${ps.errors[0]}`);
    if (!json && ps.states.some((x) => x.isJson)) errs.push('Batch states: object states need the "JSON state" box');
    const options = {};
    for (const [k, min, max] of OPTION_FIELDS) {
      const raw = optIns[k].value.trim();
      if (raw === '') continue;
      const n = Number(raw);
      if (!Number.isInteger(n) || n < min || n > max) errs.push(`Options: ${k} must be a whole number in ${min}..${max}`);
      else options[k] = n;
    }
    if (seqBox.checked) options.sequential = true;
    if (errs.length) return { errs };
    return {
      fields: {
        title, category: catIn.value.trim().toLowerCase() || 'mine', description: descIn.value.trim(),
        stateIsJson: json, state, questions: qe.get(), options,
        batchStates: ps.states.map((x) => (json ? JSON.stringify(x.state) : String(x.state))),
        ...(!json && fmt === 'jsonl' ? { batchFormat: 'jsonl' } : {}),
      },
    };
  }

  return new Promise((resolve) => {
    let result = null;
    const save = () => {
      const { errs, fields } = collect();
      if (errs) return fail(errs);
      const base = mode === 'edit' ? { ...template }
        : mode === 'duplicate' ? { ...template, id: undefined, createdAt: undefined, from: template.id }
          : {};
      const next = { ...base, ...fields, builtin: false };
      if (!fields.batchFormat) delete next.batchFormat;
      if (!next.id) delete next.id;
      let saved;
      try { saved = saveUserTemplate(next); } catch (e) { return fail([e.message || String(e)]); }
      result = saved;
      toast(`Saved template "${saved.title}"`, { kind: 'ok' });
      try { onSaved?.(saved); } catch (e) { console.warn('[ojui] onSaved failed', e); }
      return true;
    };
    const snapshot = () => JSON.stringify([titleIn.value, catIn.value, descIn.value, stateTa.value, jsonBox.checked,
      batchTa.value, fmtSel.value, OPTION_FIELDS.map(([k]) => optIns[k].value), seqBox.checked, qe.get()]);
    const initial = snapshot();
    openModal({
      title: mode === 'edit' ? 'Edit template' : 'New template', wide: true, className: 'tpl-editor-modal', body,
      // Esc / backdrop / X must not silently drop typed work
      beforeClose: () => result || snapshot() === initial || confirmDialog('Discard the changes to this template?', { danger: true, okLabel: 'Discard' }),
      onClose: () => { try { qe.destroy(); } catch { /* ignore */ } resolve(result); },
      actions: [
        { label: 'Cancel', kind: 'ghost' },
        { label: 'Save template', kind: 'primary', onClick: save },
      ],
    });
    titleIn.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); body.closest('.modal')?.querySelector('.modal-foot .btn.primary')?.click(); } });
  });
}

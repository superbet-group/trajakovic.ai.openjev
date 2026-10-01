// Combined question editor (builder C): Builder and JSON tabs over one QuestionSet, with a
// toolbar (count, add question, from template, paste JSON, save as template), live validation
// and the mapping of server 422 errors. B mounts it into #qeditor; batch mounts its own instance.

import { el, isObj, debounceLocal } from '/js/jev/util.js';
import { validateQuestions, mapServerErrors, typeCounts, QUESTION_SET_SCHEMA } from '/js/jev/validate.js';
import { mountBuilder } from '/js/jev/builder.js';
import { mountJsonEditor } from '/js/jev/jsonedit.js';
import { allTemplates, isUserTemplate } from '/js/jev/templates.js';
import { emit } from '/js/core/bus.js';
import { icon } from '/js/core/icons.js';
import { getSettings, setSettings } from '/js/core/store.js';
import { openModal, confirmDialog } from '/js/core/modal.js';
import { toast } from '/js/core/toast.js';

let active = null;
/** The most recently mounted composer editor (emit: true), for slash commands. */
export function getActiveQuestionEditor() { return active; }

function safeSettings() { try { return getSettings() || {}; } catch { return {}; } }
function saveSetting(patch) { try { setSettings(patch); } catch (e) { console.warn('[ojui] setSettings failed', e); } }

/** Small dropdown menu anchored to a button. items: [{label, hint?, onClick}] */
export function menuButton(label, items, { iconName, cls = 'btn sm ghost' } = {}) {
  const wrap = el('span', { class: 'qb-menu-wrap' });
  const menu = el('div', { class: 'qb-menu card', role: 'menu', hidden: true });
  const close = () => { menu.hidden = true; document.removeEventListener('pointerdown', outside, true); };
  const outside = (ev) => { if (!wrap.contains(ev.target)) close(); };
  const btn = el('button', {
    class: cls, type: 'button', 'aria-haspopup': 'menu',
    onclick: () => {
      if (!menu.hidden) return close();
      menu.textContent = '';
      for (const it of (typeof items === 'function' ? items() : items)) {
        if (it === '-') { menu.appendChild(el('div', { class: 'qb-menu-sep' })); continue; }
        menu.appendChild(el('button', { class: 'qb-menu-item', type: 'button', role: 'menuitem', onclick: () => { close(); it.onClick(); } },
          it.swatch ? el('i', { class: 'qb-swatch', style: { background: it.swatch } }) : null,
          el('span', {}, it.label), it.hint ? el('span', { class: 'faint mono qb-menu-hint' }, it.hint) : null));
      }
      menu.hidden = false;
      document.addEventListener('pointerdown', outside, true);
    },
  }, iconName ? icon(iconName, 14) : null, iconName ? ' ' : null, label, ' ', icon('chevron-down', 12));
  // Esc closes just the dropdown; core/modal.js leaves Esc from an open menu alone
  wrap.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape' || menu.hidden) return;
    ev.stopPropagation();
    close();
    btn.focus();
  });
  wrap.append(btn, menu);
  return wrap;
}

/**
 * mountQuestionEditor(el, {questions, onChange(questions, {valid, errors}), emit = true, tab,
 *   templateContext?: () => {state, stateIsJson, batchStates, options, title}, saveButton = true})
 * → {get, set, validate, highlightErrors, setTab, focus, destroy, addQuestion, getTab, saveAsTemplate}
 * templateContext pre-fills "Save as template" with the host's state, batch states and options.
 */
export function mountQuestionEditor(host, opts = {}) {
  const settings = safeSettings();
  const doEmit = opts.emit !== false;
  let current = isObj(opts.questions) ? JSON.parse(JSON.stringify(opts.questions)) : {};
  let tab = opts.tab || (settings.questionEditorTab === 'json' ? 'json' : 'builder');
  let serverErrs = [];
  let jsonEd = null, jsonError = null;
  let destroyed = false;

  const root = el('div', { class: 'qb-editor' });
  host.appendChild(root);

  const countEl = el('span', { class: 'qb-count mono' });
  const dot = el('span', { class: 'qb-dot', title: 'the question set is invalid', hidden: true });
  const tabBuilder = el('button', { class: 'tab', type: 'button', onclick: () => handle.setTab('builder') }, 'Builder');
  const tabJson = el('button', { class: 'tab', type: 'button', onclick: () => handle.setTab('json') }, 'JSON');

  const addMenu = menuButton('Add question', ['noul', 'choice', 'score'].map((t) => ({
    label: t, swatch: `var(--type-${t})`,
    hint: t === 'noul' ? 'yes / no → P(yes)' : t === 'choice' ? 'pick one of N' : 'rate on 1–10 levels',
    onClick: () => handle.addQuestion(t),
  })), { iconName: 'plus' });
  const tplItem = (t) => ({
    label: t.title, hint: `${isUserTemplate(t) ? 'mine · ' : ''}${Object.keys(t.questions).length} q`,
    onClick: async () => {
      if (Object.keys(current).length && !(await confirmDialog(`Replace the current ${Object.keys(current).length} questions with "${t.title}"?`))) return;
      handle.set(t.questions); notify();
    },
  });
  const tplMenu = menuButton('From template', () => {
    const all = allTemplates();
    const mine = all.filter(isUserTemplate), built = all.filter((t) => !isUserTemplate(t));
    if (!all.length) return [{ label: 'No templates', onClick: () => {} }];
    return [...mine.map(tplItem), ...(mine.length && built.length ? ['-'] : []), ...built.map(tplItem)];
  }, { iconName: 'templates' });
  const pasteBtn = el('button', { class: 'btn sm ghost', type: 'button', onclick: () => pasteJson() }, icon('json', 14), ' Paste JSON');
  const saveBtn = opts.saveButton === false ? null
    : el('button', { class: 'btn sm ghost', type: 'button', title: 'Save these questions as a reusable template', onclick: () => saveAsTemplate() }, icon('templates', 14), ' Save as template');

  const toolbar = el('div', { class: 'qb-toolbar' },
    el('div', { class: 'tabs qb-tabs' }, tabBuilder, tabJson),
    countEl, dot,
    el('span', { class: 'spacer' }),
    addMenu, tplMenu, pasteBtn, saveBtn);
  const builderHost = el('div', { class: 'qb-pane' });
  const jsonHost = el('div', { class: 'qb-pane' });
  const issues = el('div', { class: 'qb-issues' });
  root.append(toolbar, builderHost, jsonHost, issues);

  const builder = mountBuilder(builderHost, {
    questions: current,
    onChange: (qs) => { current = qs; serverErrs = []; notify(); },
  });

  function ensureJson() {
    if (jsonEd) return;
    jsonEd = mountJsonEditor(jsonHost, {
      value: current, mode: safeSettings().jsonEditorMode || 'tree', schema: QUESTION_SET_SCHEMA, minHeight: 220,
      onChange: (value, info) => {
        if (info.valid && value !== undefined) { current = value; jsonError = null; serverErrs = []; }
        else jsonError = info.error || 'invalid JSON';
        notify();
      },
      onModeChange: (m) => saveSetting({ jsonEditorMode: m }),
    });
  }

  function computeValidation() {
    const v = validateQuestions(current);
    const errors = [...v.errors];
    if (tab === 'builder') errors.push(...builder.localErrors());
    if (tab === 'json' && jsonError) errors.unshift({ path: [], message: `JSON: ${jsonError}` });
    errors.push(...serverErrs);
    return { valid: errors.length === 0, errors, warnings: v.warnings };
  }

  const emitDebounced = debounceLocal((payload) => { if (!destroyed) emit('oj:composer-questions-changed', payload); }, 150);

  function renderIssues(res) {
    issues.textContent = '';
    const setLevel = [...res.errors, ...res.warnings].filter((e) => !e.path?.length || tab === 'json');
    const show = tab === 'json' ? [...res.errors.map((e) => ({ ...e, lvl: 'err' })), ...res.warnings.map((e) => ({ ...e, lvl: 'warn' }))]
      : setLevel.map((e) => ({ ...e, lvl: res.errors.includes(e) ? 'err' : 'warn' }));
    if (!show.length) return;
    for (const e of show.slice(0, 12)) {
      issues.appendChild(el('div', { class: ['qb-err', e.lvl === 'warn' && 'warn', e.server && 'server'] }, icon('alert', 13), ' ',
        e.path?.length ? el('button', { class: 'mono qb-path', type: 'button', onclick: () => { handle.setTab('builder'); builder.scrollTo(e.path[0]); } }, e.path.join(' › ')) : null,
        e.path?.length ? ': ' : null, e.message));
    }
    if (show.length > 12) issues.appendChild(el('div', { class: 'faint' }, `…and ${show.length - 12} more`));
  }

  function notify() {
    if (destroyed) return;
    const res = computeValidation();
    const c = typeCounts(current);
    const n = Object.keys(current || {}).length;
    countEl.textContent = `${n} question${n === 1 ? '' : 's'}` + ['noul', 'choice', 'score'].filter((t) => c[t]).map((t) => ` · ${t} ${c[t]}`).join('');
    dot.hidden = res.valid;
    if (tab === 'builder') builder.showErrors(res.errors, res.warnings);
    renderIssues(res);
    if (typeof opts.onChange === 'function') { try { opts.onChange(current, { valid: res.valid, errors: res.errors }); } catch (e) { console.warn(e); } }
    if (doEmit) emitDebounced({ questions: current, valid: res.valid, errors: res.errors });
    return res;
  }

  function applyTab() {
    tabBuilder.classList.toggle('active', tab === 'builder');
    tabJson.classList.toggle('active', tab === 'json');
    builderHost.hidden = tab !== 'builder';
    jsonHost.hidden = tab !== 'json';
  }

  function pasteJson() {
    const ta = el('textarea', { class: 'textarea mono', rows: 14, spellcheck: 'false', placeholder: '{"urgent": {"type": "noul", "instructions": "…"}}\n\nA full request body with "questions" works too.' });
    const msg = el('div', { class: 'faint' }, 'Paste a QuestionSet or a whole /v1/systemone body.');
    const m = openModal({
      title: 'Paste questions JSON', wide: true,
      body: el('div', { class: 'col' }, ta, msg),
      actions: [
        { label: 'Cancel', kind: 'ghost', onClick: () => m.close() },
        {
          label: 'Replace questions', kind: 'primary', onClick: () => {
            try {
              let v = JSON.parse(ta.value);
              if (isObj(v) && isObj(v.questions)) v = v.questions;
              if (!isObj(v)) throw new Error('expected an object of {qid: question}');
              handle.set(v); notify(); m.close();
              toast(`Loaded ${Object.keys(v).length} questions`, { kind: 'ok' });
            } catch (e) { msg.textContent = `Not valid: ${e.message}`; msg.className = 'qb-err'; return false; }
          },
        },
      ],
    });
    setTimeout(() => ta.focus(), 30);
  }

  async function saveAsTemplate() {
    if (!Object.keys(current || {}).length) { toast('Add at least one question first', { kind: 'warn' }); return null; }
    if (!computeValidation().valid) { toast('Fix the question set first', { kind: 'warn' }); return null; }
    let ctx = {};
    try { ctx = (typeof opts.templateContext === 'function' && opts.templateContext()) || {}; } catch (e) { console.warn('[ojui] templateContext failed', e); }
    // loaded lazily: templateEditor imports this module
    const { openTemplateEditor } = await import('/js/jev/templateEditor.js');
    return openTemplateEditor({ mode: 'create', seed: { ...ctx, questions: handle.get() } });
  }

  const handle = {
    get() { return JSON.parse(JSON.stringify(current)); },
    saveAsTemplate,
    set(questions) {
      current = isObj(questions) ? JSON.parse(JSON.stringify(questions)) : {};
      serverErrs = []; jsonError = null;
      builder.set(current);
      if (jsonEd) jsonEd.set(current);
      notify();
    },
    validate() { const r = computeValidation(); return { valid: r.valid, errors: r.errors, warnings: r.warnings }; },
    highlightErrors(serverErrors) {
      serverErrs = mapServerErrors(Array.isArray(serverErrors) ? serverErrors : serverErrors?.details || []);
      if (serverErrs.length) {
        handle.setTab('builder');
        const first = serverErrs.find((e) => e.path.length);
        notify();
        if (first) builder.scrollTo(first.path[0]);
      }
      return serverErrs;
    },
    setTab(next) {
      if (next !== 'builder' && next !== 'json') next = tab === 'builder' ? 'json' : 'builder';
      if (next === tab) return true;
      if (next === 'builder' && jsonEd) {
        const perr = jsonEd.parseError();
        if (perr) {
          toast(`Fix the JSON first: ${perr}`, { kind: 'warn' });
          jsonError = perr; notify();
          return false;
        }
        const v = jsonEd.get();
        if (!isObj(v)) { toast('The question set must be a JSON object', { kind: 'warn' }); return false; }
        current = v;
        builder.set(current);
      }
      if (next === 'json') { ensureJson(); jsonEd.set(current); jsonError = null; }
      tab = next;
      saveSetting({ questionEditorTab: tab });
      applyTab();
      notify();
      return true;
    },
    getTab() { return tab; },
    toggleTab() { return handle.setTab(tab === 'builder' ? 'json' : 'builder'); },
    addQuestion(type = 'noul', qid) {
      if (tab !== 'builder') handle.setTab('builder');
      const id = builder.addQuestion(type, qid);
      return id;
    },
    focus() { if (tab === 'json' && jsonEd) jsonEd.focus(); else builder.focus(); },
    destroy() {
      destroyed = true;
      if (active === handle) active = null;
      builder.destroy();
      if (jsonEd) jsonEd.destroy();
      root.remove();
    },
  };

  if (tab === 'json') { ensureJson(); }
  applyTab();
  notify();
  if (doEmit) active = handle;
  return handle;
}

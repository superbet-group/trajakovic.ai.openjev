// JSON editor wrapper (builder C): vanilla-jsoneditor from the pinned CDN when it loads,
// otherwise a mono <textarea> with live JSON.parse lint (line:col). The fallback shows at
// once and is swapped for the real editor when the CDN module arrives.

import { el, loadCdn, loadCss, jsonErrorPos } from '/js/jev/util.js';
import { icon } from '/js/core/icons.js';

const JSE_URL = 'https://cdn.jsdelivr.net/npm/vanilla-jsoneditor@3.13.0/standalone.js';
const JSE_DARK_CSS = 'https://cdn.jsdelivr.net/npm/vanilla-jsoneditor@3.13.0/themes/jse-theme-dark.css';

let libPromise = null;
let lib = null;
export function loadJsonEditorLib() {
  if (!libPromise) {
    loadCss(JSE_DARK_CSS);
    libPromise = loadCdn(JSE_URL).then((m) => {
      lib = m && typeof m.createJSONEditor === 'function' ? m : null;
      return lib;
    });
  }
  return libPromise;
}
export const jsonEditorAvailable = () => !!lib;

const isDark = () => (document.documentElement.getAttribute('data-theme') || 'dark') !== 'light';

function looksLikeRawJsonText(v) {
  if (typeof v !== 'string') return false;
  const t = v.trim();
  if (t === '') return true;
  if (!/^[[{]/.test(t)) return false;
  try { JSON.parse(t); return true; } catch { return true; } // "{ broken" in text mode is also raw text
}

/**
 * Mount a JSON editor.
 * opts: {value, mode: 'tree'|'text'|'table', readOnly, schema, onChange(value, {valid, error, text}),
 *        onModeChange(mode), text (raw initial text), minHeight}
 * In text mode a string value that looks like JSON text ('{…', '[…' or '') is treated as raw text.
 */
export function mountJsonEditor(host, opts = {}) {
  const st = {
    mode: opts.mode || 'tree',
    readOnly: !!opts.readOnly,
    schema: opts.schema || null,
    text: '',
    editor: null,
    destroyed: false,
    parseError: null,
  };
  if (typeof opts.text === 'string') st.text = opts.text;
  else if (st.mode === 'text' && looksLikeRawJsonText(opts.value)) st.text = opts.value;
  else st.text = opts.value === undefined ? '' : JSON.stringify(opts.value, null, 2);

  const root = el('div', { class: 'viz-jse', style: opts.minHeight ? { minHeight: `${opts.minHeight}px` } : {} });
  host.appendChild(root);
  const themeObs = new MutationObserver(() => root.classList.toggle('jse-theme-dark', isDark()));
  themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  root.classList.toggle('jse-theme-dark', isDark());

  const emit = (value, info) => { if (typeof opts.onChange === 'function') { try { opts.onChange(value, info); } catch (e) { console.warn('[ojui] json onChange failed', e); } } };

  // ------------------------------------------------ fallback textarea
  let ta = null, lint = null, fbWrap = null;
  function lintText() {
    if (!lint) return;
    lint.textContent = '';
    const t = ta.value;
    if (t.trim() === '') { st.parseError = 'empty'; lint.className = 'viz-jse-lint warn'; lint.append(icon('alert', 13), ' empty'); return undefined; }
    try {
      const v = JSON.parse(t);
      st.parseError = null;
      lint.className = 'viz-jse-lint ok';
      lint.append(icon('check', 13), ` valid JSON · ${t.length.toLocaleString()} chars`);
      return v;
    } catch (e) {
      const p = jsonErrorPos(e, t);
      st.parseError = p.line ? `line ${p.line}:${p.col} ${p.message}` : p.message;
      lint.className = 'viz-jse-lint err';
      lint.append(icon('alert', 13), ' ', st.parseError);
      return undefined;
    }
  }
  function renderFallback() {
    root.textContent = '';
    ta = el('textarea', {
      class: 'textarea mono viz-jse-ta', spellcheck: 'false', readOnly: st.readOnly, value: st.text,
      rows: Math.min(24, Math.max(4, st.text.split('\n').length + 1)),
      oninput: () => {
        st.text = ta.value;
        const v = lintText();
        emit(v, { valid: !st.parseError, error: st.parseError, text: st.text });
      },
      onkeydown: (ev) => {
        if (ev.key === 'Tab' && !ev.shiftKey && !st.readOnly) {
          ev.preventDefault();
          const a = ta.selectionStart, b = ta.selectionEnd;
          ta.setRangeText('  ', a, b, 'end');
          ta.dispatchEvent(new Event('input'));
        }
      },
      onblur: () => { if (lib && !st.editor && !st.destroyed) upgrade(); },
    });
    lint = el('div', { class: 'viz-jse-lint' });
    const bar = el('div', { class: 'viz-jse-bar' },
      el('span', { class: 'faint' }, st.mode !== 'text' && !lib ? `${st.mode} view unavailable (editor CDN not loaded) · plain text` : 'text'),
      el('span', { class: 'spacer' }),
      st.readOnly ? null : el('button', { class: 'btn ghost sm', type: 'button', onclick: () => format(2) }, 'Format'),
      st.readOnly ? null : el('button', { class: 'btn ghost sm', type: 'button', onclick: () => format(0) }, 'Compact'),
    );
    fbWrap = el('div', { class: 'viz-jse-fallback' }, bar, ta, lint);
    root.appendChild(fbWrap);
    lintText();
  }
  function format(indent) {
    try { const v = JSON.parse(ta.value); ta.value = JSON.stringify(v, null, indent || undefined); ta.dispatchEvent(new Event('input')); } catch { /* lint already shows it */ }
  }

  // ------------------------------------------------ real editor
  function contentFromText() {
    try { return { json: JSON.parse(st.text) }; } catch { return { text: st.text }; }
  }
  function upgrade() {
    if (st.destroyed || st.editor || !lib) return;
    if (ta && document.activeElement === ta) return; // swap on blur
    root.textContent = '';
    ta = null; lint = null; fbWrap = null;
    const props = {
      content: contentFromText(),
      mode: st.mode,
      readOnly: st.readOnly,
      mainMenuBar: true,
      navigationBar: st.mode !== 'text',
      statusBar: true,
      askToFormat: false,
      onChange: (content, _prev, info) => {
        const errs = info?.contentErrors;
        let value;
        if ('json' in content && content.json !== undefined) {
          value = content.json; st.text = JSON.stringify(value, null, 2); st.parseError = null;
        } else {
          st.text = content.text ?? '';
          try { value = JSON.parse(st.text); st.parseError = null; } catch (e) {
            const p = jsonErrorPos(e, st.text); st.parseError = p.line ? `line ${p.line}:${p.col} ${p.message}` : p.message;
          }
        }
        const schemaErrors = errs && Array.isArray(errs.validationErrors) ? errs.validationErrors.length : 0;
        emit(st.parseError ? undefined : value, { valid: !st.parseError, error: st.parseError, text: st.text, schemaErrors });
      },
      onChangeMode: (m) => { st.mode = m; if (typeof opts.onModeChange === 'function') opts.onModeChange(m); },
    };
    if (st.schema && typeof lib.createAjvValidator === 'function') {
      try { props.validator = lib.createAjvValidator({ schema: st.schema, ajvOptions: { allowUnionTypes: true, strict: false } }); } catch (e) { console.warn('[ojui] schema validator unavailable', e); }
    }
    try {
      st.editor = lib.createJSONEditor({ target: root, props });
    } catch (e) {
      console.warn('[ojui] vanilla-jsoneditor failed to mount, keeping textarea', e);
      st.editor = null;
      renderFallback();
    }
  }

  if (lib) upgrade(); else { renderFallback(); loadJsonEditorLib().then(() => upgrade()); }

  const handle = {
    get() {
      if (st.editor) {
        const c = st.editor.get();
        if (c && 'json' in c && c.json !== undefined) return c.json;
        return JSON.parse(c?.text ?? '');
      }
      return JSON.parse(ta ? ta.value : st.text);
    },
    getText() {
      if (st.editor) { const c = st.editor.get(); return 'json' in c && c.json !== undefined ? JSON.stringify(c.json, null, 2) : (c.text ?? ''); }
      return ta ? ta.value : st.text;
    },
    set(value) {
      st.text = value === undefined ? '' : JSON.stringify(value, null, 2);
      st.parseError = null;
      if (st.editor) { try { st.editor.set({ json: value }); } catch (e) { console.warn('[ojui] json set failed', e); } }
      else if (ta) { ta.value = st.text; ta.rows = Math.min(24, Math.max(4, st.text.split('\n').length + 1)); lintText(); }
    },
    setText(text) {
      st.text = String(text ?? '');
      if (st.editor) st.editor.set(contentFromText());
      else if (ta) { ta.value = st.text; lintText(); }
    },
    setMode(mode) {
      st.mode = mode;
      if (st.editor) { try { st.editor.updateProps({ mode, navigationBar: mode !== 'text' }); } catch (e) { console.warn(e); } }
      else if (fbWrap) renderFallback();
    },
    setReadOnly(ro) { st.readOnly = !!ro; if (st.editor) st.editor.updateProps({ readOnly: st.readOnly }); else if (ta) ta.readOnly = st.readOnly; },
    parseError() {
      if (st.editor) { try { handle.get(); return null; } catch (e) { const p = jsonErrorPos(e, handle.getText()); return p.line ? `line ${p.line}:${p.col} ${p.message}` : p.message; } }
      return st.parseError === 'empty' ? 'empty' : st.parseError;
    },
    focus() { if (st.editor && st.editor.focus) st.editor.focus(); else if (ta) ta.focus(); },
    get isFallback() { return !st.editor; },
    get element() { return root; },
    destroy() {
      st.destroyed = true;
      themeObs.disconnect();
      if (st.editor) { try { st.editor.destroy(); } catch { /* ignore */ } st.editor = null; }
      root.remove();
    },
  };
  return handle;
}

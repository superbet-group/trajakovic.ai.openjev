// Batch states from files (builder C, #/batch): format sniffing, CSV/TSV/JSONL/JSON parsing, merge and cap.
// Pure except readFileText (Blob + TextDecoder, also in node). Nothing leaves the browser.

import { statesToBatchInput } from '/js/core/format.js';

export const MAX_FILE_BYTES = 5 * 1024 * 1024;  // the same as images.js MAX_IMAGE_BYTES
export const MAX_STATES = 5000;                 // per merged input; templates cap batchStates at 1000
export const IMAGE_EXT = /\.(png|jpe?g|webp|gif)$/i;
export const ACCEPT = '.jsonl,.ndjson,.jsonlines,.json,.txt,.text,.log,.md,.csv,.tsv,.tab,text/plain,text/csv,text/tab-separated-values,application/json,application/x-ndjson,image/jpeg,image/png,image/webp,image/gif';

const EXT_KIND = {
  json: 'json', jsonl: 'jsonl', ndjson: 'jsonl', jsonlines: 'jsonl', csv: 'csv', tsv: 'csv', tab: 'csv',
  txt: 'text', text: 'text', log: 'text', md: 'text', markdown: 'text',
};
const MIME_KIND = {
  'application/json': 'json', 'application/x-ndjson': 'jsonl', 'application/jsonl': 'jsonl',
  'text/csv': 'csv', 'text/tab-separated-values': 'csv',
};
const SHEET_EXT = new Set(['xlsx', 'xls', 'ods', 'numbers']);
const BINARY_EXT = new Set(['pdf', 'docx', 'zip', 'gz', 'parquet', 'sqlite', 'db']);
const ARRAY_KEYS = ['states', 'batchStates', 'items', 'data', 'rows', 'records', 'examples'];
const TEXT_COL = /^(text|state|content|input|prompt|message|body|review|comment|question|sentence|description)$/i;

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const clean = (t) => String(t ?? '').replace(/^﻿/, '').replace(/\r\n?/g, '\n');
const extOf = (name) => { const m = /\.([^./\\]+)$/.exec(String(name || '')); return m ? m[1].toLowerCase() : ''; };
const nonEmptyLines = (t, limit = Infinity) => {
  const out = [];
  for (const line of t.split('\n')) { const l = line.trim(); if (l) { out.push(l); if (out.length >= limit) break; } }
  return out;
};
const parses = (l) => { try { JSON.parse(l); return true; } catch { return false; } };
const tryJson = (t) => { try { return { ok: true, v: JSON.parse(t) }; } catch (e) { return { ok: false, msg: e.message }; } };

// ---------------------------------------------------------------- CSV

export function parseCsv(text, delim = ',', quoting = true) {
  const rows = [];
  let row = [], field = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; } else field += c;
    } else if (c === '"' && quoting && field === '') q = true;  // a quote opens quoting only at a field start: 27" stays text
    else if (c === delim) { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(field); rows.push(row); row = []; field = ''; }
    else field += c;
  }
  // an unterminated quote at a field start swallowed the rest: read again with quoting off
  if (q) return parseCsv(text, delim, false);
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((x) => x.trim() !== ''));
}

/** rows → CSV text; a cell is quoted when it holds the delimiter, a quote or a line break. */
export function serializeCsv(rows, delim = ',') {
  const cell = (v) => { const t = String(v ?? ''); return t.includes(delim) || /["\n\r]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
  return rows.map((r) => r.map(cell).join(delim)).join('\n');
}

/** The most frequent of , \t ; outside quotes on the first line; ties go to ',' then '\t'. */
export function sniffDelimiter(text, quoting = true) {
  const t = String(text || '');
  const n = { ',': 0, '\t': 0, ';': 0 };
  let q = false;
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (c === '"' && quoting) {
      // same rule as parseCsv: a quote opens only at the start of text, after a delimiter or a line break
      if (q) q = false;
      else if (i === 0 || t[i - 1] in n || t[i - 1] === '\n' || t[i - 1] === '\r') q = true;
    } else if (!q && (c === '\n' || c === '\r')) break;
    else if (!q && c in n) n[c]++;
  }
  if (q) return sniffDelimiter(t, false);
  let best = ',';
  for (const d of ['\t', ';']) if (n[d] > n[best]) best = d;
  return best;
}

/** A likely text column: a well-known name, else the longest mean cell over the first 200 rows. */
export function guessTextColumn(header, rows) {
  if (!header?.length) return '';
  const named = header.find((h) => TEXT_COL.test(h));
  if (named) return named;
  const sample = (rows || []).slice(0, 200);
  let best = 0, bestLen = -1;
  header.forEach((_, i) => {
    const len = sample.length ? sample.reduce((a, r) => a + String(r[i] ?? '').trim().length, 0) / sample.length : 0;
    if (len > bestLen) { best = i; bestLen = len; }
  });
  return header[best];
}

// ---------------------------------------------------------------- states

/**
 * → {states: [{state, isJson}], errors: [string], skipped, columns?, column?, delimiter?}
 * column: the CSV column (fallback header[0]) or the JSONL field (fallback '*', the whole value).
 */
export function parseStates(text, format, column) {
  const t = String(text || '');
  if (format === 'lines') return { states: t.split('\n').map((x) => x.trim()).filter(Boolean).map((state) => ({ state, isJson: false })), errors: [], skipped: 0 };
  if (format === 'blocks') return { states: t.split(/\n\s*\n/).map((x) => x.trim()).filter(Boolean).map((state) => ({ state, isJson: false })), errors: [], skipped: 0 };
  if (format === 'jsonl') {
    const values = [], errors = [];
    t.split('\n').forEach((line, i) => {
      const l = line.trim();
      if (!l) return;
      try { values.push(JSON.parse(l)); } catch (e) { errors.push(`line ${i + 1}: ${e.message}`); }
    });
    const keys = new Set();
    let seen = 0;
    for (const v of values) {
      if (!isPlainObject(v)) continue;
      for (const k of Object.keys(v)) keys.add(k);
      if (++seen >= 500) break;
    }
    const columns = [...keys];
    // a stale or unknown pick falls back to whole values, as before fields existed
    const field = column && column !== '*' && columns.includes(column) ? column : null;
    if (!field) return { states: values.map((v) => ({ state: v, isJson: typeof v !== 'string' })), errors, skipped: 0, columns, column: '*' };
    const states = [];
    let skipped = 0;
    for (const v of values) {
      if (isPlainObject(v)) {
        let x = v[field];
        if (typeof x === 'string') x = x.trim();
        if (x === undefined || x === null || x === '') skipped++;
        else states.push({ state: x, isJson: typeof x !== 'string' });
      } else if (typeof v === 'string') states.push({ state: v, isJson: false });
      else skipped++;
    }
    return { states, errors, skipped, columns, column: field };
  }
  const delimiter = sniffDelimiter(t);
  const rows = parseCsv(t, delimiter);
  if (!rows.length) return { states: [], errors: [], skipped: 0, columns: [], delimiter };
  const header = rows[0].map((h) => h.trim());
  const col = column === '*' ? '*' : (header.includes(column) ? column : header[0]);
  const idx = header.indexOf(col);
  let skipped = 0;
  const states = rows.slice(1).map((r) => {
    if (col === '*') return { state: Object.fromEntries(header.map((h, i) => [h, r[i] ?? ''])), isJson: true };
    return { state: (r[idx] ?? '').trim(), isJson: false };
  }).filter((x) => { if (x.isJson || x.state !== '') return true; skipped++; return false; });
  return { states, errors: [], skipped, columns: header, column: col, delimiter };
}

// ---------------------------------------------------------------- files

export function isImageFile(file) {
  return String(file?.type || '').startsWith('image/') || IMAGE_EXT.test(String(file?.name || ''));
}

/** File → {text, encoding, binary, note?}: UTF-16 by BOM, else strict UTF-8, else Windows-1252. Throws on spreadsheets and known binaries. */
export async function readFileText(file) {
  const ext = extOf(file?.name);
  if (SHEET_EXT.has(ext)) throw new Error('spreadsheets are not read; export the sheet as CSV first');
  if (BINARY_EXT.has(ext)) throw new Error('not a text format');
  const head = new Uint8Array(await file.slice(0, 65536).arrayBuffer());
  let encoding = head[0] === 0xFF && head[1] === 0xFE ? 'utf-16le' : head[0] === 0xFE && head[1] === 0xFF ? 'utf-16be' : null;
  if (!encoding && head.includes(0)) return { text: '', encoding: null, binary: true };
  const buf = await file.arrayBuffer();
  let text, note;
  if (encoding) text = new TextDecoder(encoding).decode(buf);
  else {
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(buf); encoding = 'utf-8'; }
    catch { text = new TextDecoder('windows-1252').decode(buf); encoding = 'windows-1252'; note = 'decoded as Windows-1252'; }
  }
  return { text: clean(text), encoding, binary: false, note };
}

/**
 * Text of one file → {kind:'states', name, format, input, column, columns, delimiter?, count, skipped, errors, notes, by}
 * | {kind:'batch', ...those + title, questions, options, imageCount} (an ojui-batch export) | {kind:'reject', name, message}.
 * Extension first, then MIME type (only without a known extension), then content.
 */
export function parseUpload(text, filename = '', mime = '') {
  const name = filename || 'pasted text';
  const t = clean(text);
  const reject = (message, extra) => ({ kind: 'reject', name, message, ...extra });
  if (!t.trim()) return reject(`${name} is empty`);
  const ext = extOf(name);
  const type = String(mime || '').toLowerCase().split(';')[0].trim();
  const extKind = EXT_KIND[ext] || null;
  const mimeKind = extKind ? null : MIME_KIND[type] || null;
  const notes = [];
  let by = extKind ? 'extension' : mimeKind ? 'mime' : 'content';

  const states = (format, input, column = '', skipped0 = 0) => {
    const p = parseStates(input, format, column);
    if (format === 'csv' && p.delimiter !== ',') notes.push(p.delimiter === '\t' ? 'tab-separated' : ';-separated');
    return { kind: 'states', name, format, input, column: format === 'csv' ? p.column || '' : format === 'jsonl' ? p.column : '',
      columns: p.columns || [], ...(p.delimiter ? { delimiter: p.delimiter } : {}), count: p.states.length,
      skipped: skipped0 + p.skipped + (format === 'jsonl' ? p.errors.length : 0), errors: p.errors.slice(0, 3), notes, by };
  };
  const fromArray = (arr) => {
    const vals = arr.filter((x) => (typeof x === 'string' ? x.trim() !== '' : x !== null && x !== undefined));
    const b = statesToBatchInput(vals);
    return states(b.format, b.input, b.format === 'jsonl' ? '*' : '', arr.length - vals.length);
  };
  const fromJson = (v) => {
    if (Array.isArray(v)) return fromArray(v);
    if (isPlainObject(v)) {
      if (v.format === 'ojui-export') return reject(`${name} is a conversation export: import it from the sidebar (Import, or drop it there)`, { warn: true });
      if (v.format === 'ojui-batch') {
        if (Number(v.version) > 1) notes.push('newer export version');
        const rows = Array.isArray(v.rows) ? v.rows : [];
        const b = statesToBatchInput(rows.map((r) => r?.state).filter((x) => x !== null && x !== undefined));
        if (!isPlainObject(v.questions) || !Object.keys(v.questions).length) {
          // nothing to restore, but the states are still usable
          notes.push('the export has no question set: states only');
          return states(b.format, b.input, b.format === 'jsonl' ? '*' : '');
        }
        const p = parseStates(b.input, b.format, '*');
        return { kind: 'batch', name, title: String(v.title || ''), questions: v.questions, options: isPlainObject(v.options) ? v.options : {},
          imageCount: Number(v.imageCount) || 0, format: b.format, input: b.input, column: '', columns: p.columns || [],
          count: p.states.length, skipped: 0, errors: [], notes, by };
      }
      const key = ARRAY_KEYS.find((k) => Array.isArray(v[k]));
      if (key) { notes.push(`states from "${key}"`); return fromArray(v[key]); }
      notes.push('a single JSON object: loaded as one state');
      return states('jsonl', JSON.stringify(v), '*');
    }
    const b = statesToBatchInput([v]);
    return states(b.format, b.input, b.format === 'jsonl' ? '*' : '');
  };
  const lineRatio = (limit) => { const ls = nonEmptyLines(t, limit); return ls.length ? ls.filter(parses).length / ls.length : 0; };

  const kind = extKind || mimeKind;
  if (kind === 'json') {
    const j = tryJson(t);
    if (j.ok) return fromJson(j.v);
    if (lineRatio(2000) >= 0.6) { notes.push('not one JSON document; read as JSONL'); return states('jsonl', t, '*'); }
    return reject(`${name}: invalid JSON (${j.msg})`);
  }
  if (kind === 'jsonl') {
    if (lineRatio(2000) < 0.5) {
      // a pretty-printed document saved as .jsonl
      const j = tryJson(t);
      if (j.ok) { notes.push('a JSON document, not JSONL'); return fromJson(j.v); }
    }
    return states('jsonl', t, '*');
  }
  if (kind === 'csv') {
    const d = sniffDelimiter(t);
    const rows = parseCsv(t, d);
    const header = (rows[0] || []).map((h) => h.trim());
    return states('csv', t, guessTextColumn(header, rows.slice(1)));
  }

  // plain text: .txt/.log/.md, no extension, or an unknown one that passed the binary check
  if (ext && !extKind) notes.push('unknown extension, read as text');
  const head = nonEmptyLines(t, 200);
  // objects and arrays only: a .txt of numbers or quoted strings stays lines
  if (head.length && head.filter((l) => (l[0] === '{' || l[0] === '[') && parses(l)).length / head.length >= 0.9) { by = 'content'; return states('jsonl', t, '*'); }
  const first = t.trimStart()[0];
  if (first === '{' || first === '[') { const j = tryJson(t); if (j.ok) { by = 'content'; return fromJson(j.v); } }
  if (!extKind) {
    // never for .txt/.log/.md: prose has commas
    const d = sniffDelimiter(t);
    const firstLine = t.slice(0, t.indexOf('\n') < 0 ? t.length : t.indexOf('\n'));
    if (firstLine.includes(d)) {
      const rows = parseCsv(t, d).slice(0, 5);
      if (rows.length >= 2 && rows[0].length >= 2 && rows.every((r) => r.length === rows[0].length)) {
        by = 'content';
        return states('csv', t, guessTextColumn(rows[0].map((h) => h.trim()), parseCsv(t, d).slice(1)));
      }
    }
  }
  // blocks need two or more of them, one with an inner line break: a trailing blank line is not a block
  const blocks = t.trim().split(/\n\s*\n/).filter((b) => b.trim());
  if (blocks.length >= 2 && blocks.some((b) => b.trim().includes('\n'))) return states('blocks', t);
  return states('lines', t);
}

// ---------------------------------------------------------------- merge + cap

/**
 * Append b to a, both {format, input, column} (column = the JSONL field). Same-header CSVs stay CSV;
 * anything else goes through statesToBatchInput, so JSONL values stay whole and a field pick survives.
 */
export function mergeInputs(a, b) {
  const pickOf = (x) => ({ format: x.format, input: String(x.input || ''), column: x.column ?? '' });
  if (!String(a?.input || '').trim()) return pickOf(b);
  if (!String(b?.input || '').trim()) return pickOf(a);
  if (a.format === 'csv' && b.format === 'csv' && (a.column || '') === (b.column || '')) {
    const d = sniffDelimiter(a.input);
    if (d === sniffDelimiter(b.input)) {
      const ra = parseCsv(a.input, d), rb = parseCsv(b.input, d);
      const head = (r) => JSON.stringify((r[0] || []).map((h) => h.trim()));
      if (ra.length && rb.length && head(ra) === head(rb)) return { format: 'csv', input: serializeCsv([...ra, ...rb.slice(1)], d), column: a.column ?? '' };
    }
  }
  // a JSONL field survives only when both sides send the same one
  const fieldOf = (x) => (x.format === 'jsonl' && x.column && x.column !== '*' ? x.column : '');
  if (a.format === 'jsonl' && b.format === 'jsonl' && fieldOf(a) && fieldOf(a) === fieldOf(b)) {
    return { format: 'jsonl', input: [a.input, b.input].map((x) => x.replace(/\n+$/, '')).join('\n'), column: fieldOf(a) };
  }
  // otherwise resolve each side with its own pick, so no incoming row is dropped by the other side's field
  const resolve = (x) => parseStates(x.input, x.format, x.column || (x.format === 'jsonl' ? '*' : '')).states.map((s) => s.state);
  const r = statesToBatchInput([...resolve(a), ...resolve(b)]);
  return { format: r.format, input: r.input, column: r.format === 'jsonl' ? '*' : '' };
}

/** Keep the first `max` non-empty units (lines, blocks, CSV data rows) → {format, input, column, kept, truncated}. */
export function capInput(x, max = MAX_STATES) {
  const base = { format: x.format, input: String(x.input || ''), column: x.column ?? '' };
  if (base.format === 'csv') {
    const d = sniffDelimiter(base.input);
    const rows = parseCsv(base.input, d);
    const data = Math.max(0, rows.length - 1);
    if (data <= max) return { ...base, kept: data, truncated: 0 };
    return { ...base, input: serializeCsv(rows.slice(0, max + 1), d), kept: max, truncated: data - max };
  }
  if (base.format === 'blocks') {
    const blocks = base.input.split(/\n\s*\n/).filter((b) => b.trim());
    if (blocks.length <= max) return { ...base, kept: blocks.length, truncated: 0 };
    return { ...base, input: blocks.slice(0, max).join('\n\n'), kept: max, truncated: blocks.length - max };
  }
  const lines = base.input.split('\n');
  let kept = 0, truncated = 0, cut = -1;
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].trim()) continue;
    if (kept < max) kept++;
    else { if (cut < 0) cut = i; truncated++; }
  }
  if (!truncated) return { ...base, kept, truncated: 0 };
  return { ...base, input: lines.slice(0, cut).join('\n').replace(/\n+$/, ''), kept, truncated };
}

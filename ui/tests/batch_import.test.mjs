// Unit tests for the batch file import (static/js/jev/batchImport.js), no browser.
// Run: node --test ui/tests/batch_import.test.mjs  (or mise run uiTestJs)

import { registerHooks } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';

const STATIC = fileURLToPath(new URL('../static', import.meta.url));
// the app imports by absolute path (/js/...), the way the server serves it
registerHooks({ resolve: (s, c, next) => next(s.startsWith('/js/') ? pathToFileURL(STATIC + s).href : s, c) });
const M = await import('/js/jev/batchImport.js');

const states = (r) => r.states.map((s) => s.state);

// ---------------------------------------------------------------- parseStates (old behaviour)

test('parseStates: lines and blocks as before', () => {
  assert.deepEqual(states(M.parseStates(' a \n\n b\n', 'lines')), ['a', 'b']);
  assert.deepEqual(states(M.parseStates('a\nb\n\n  \nc\n', 'blocks')), ['a\nb', 'c']);
  assert.equal(M.parseStates('a', 'lines').skipped, 0);
});

test('parseStates: JSONL errors read "line N:"', () => {
  const r = M.parseStates('{"a":1}\n\nnope\n"s"', 'jsonl');
  assert.equal(r.states.length, 2);
  assert.equal(r.errors.length, 1);
  assert.match(r.errors[0], /^line 3: /);
  assert.deepEqual(r.states[1], { state: 's', isJson: false });
});

test('parseStates: CSV named column, *, unknown column', () => {
  const csv = 'id,text\n1,hello\n2,"a, ""b"""\n3,\n';
  const named = M.parseStates(csv, 'csv', 'text');
  assert.deepEqual(states(named), ['hello', 'a, "b"']);
  assert.equal(named.skipped, 1);
  assert.equal(named.delimiter, ',');
  assert.deepEqual(M.parseStates(csv, 'csv', '*').states[0], { state: { id: '1', text: 'hello' }, isJson: true });
  const unk = M.parseStates(csv, 'csv', 'nope');
  assert.equal(unk.column, 'id');
  assert.deepEqual(states(unk), ['1', '2', '3']);
});

test('parseStates: the two-argument call (templateEditor) keeps JSONL objects whole', () => {
  const r = M.parseStates('{"text":"a","id":1}\n{"id":2}', 'jsonl');
  assert.deepEqual(states(r), [{ text: 'a', id: 1 }, { id: 2 }]);
  assert.equal(r.column, '*');
});

// ---------------------------------------------------------------- JSONL field

test('JSONL field: picks one key, counts the rows without it', () => {
  const r = M.parseStates('{"text":"a","id":1}\n{"id":2}', 'jsonl', 'text');
  assert.deepEqual(r.states, [{ state: 'a', isJson: false }]);
  assert.equal(r.skipped, 1);
  assert.deepEqual(r.columns, ['text', 'id']);
  assert.equal(r.column, 'text');
});

test('JSONL field: a missing field falls back to whole objects; strings pass through; non-string values are JSON', () => {
  const r = M.parseStates('{"text":"a"}\n{"text":"b"}', 'jsonl', 'nope');
  assert.equal(r.column, '*');
  assert.deepEqual(states(r), [{ text: 'a' }, { text: 'b' }]);
  const mix = M.parseStates('{"t":" x "}\n"bare"\n{"t":{"k":1}}\n{"t":""}\n5', 'jsonl', 't');
  assert.deepEqual(mix.states, [{ state: 'x', isJson: false }, { state: 'bare', isJson: false }, { state: { k: 1 }, isJson: true }]);
  assert.equal(mix.skipped, 2);
});

// ---------------------------------------------------------------- delimiters and CSV

test('sniffDelimiter: comma, tab, semicolon, ties and quotes', () => {
  assert.equal(M.sniffDelimiter('a,b,c\n1,2,3'), ',');
  assert.equal(M.sniffDelimiter('a\tb\tc'), '\t');
  assert.equal(M.sniffDelimiter('a;b;c\n1,5;2;3'), ';');
  assert.equal(M.sniffDelimiter('a,b\tc'), ',');
  assert.equal(M.sniffDelimiter('a\tb;c'), '\t');
  assert.equal(M.sniffDelimiter('"x;y;z",b\nq'), ',');
  assert.equal(M.sniffDelimiter('plain'), ',');
});

test('CSV branch: tab and semicolon', () => {
  const tsv = M.parseStates('id\ttext\n1\thello, world\n', 'csv', 'text');
  assert.equal(tsv.delimiter, '\t');
  assert.deepEqual(states(tsv), ['hello, world']);
  const semi = M.parseStates('id;text\n1;bonjour\n', 'csv', 'text');
  assert.equal(semi.delimiter, ';');
  assert.deepEqual(states(semi), ['bonjour']);
});

test('parseCsv: quoted newline and "" escape; serializeCsv round-trips', () => {
  const rows = M.parseCsv('a,b\n"x\ny","say ""hi"""\n');
  assert.deepEqual(rows, [['a', 'b'], ['x\ny', 'say "hi"']]);
  for (const d of [',', '\t', ';']) {
    const src = [['h1', 'h2'], ['a,b', 'c;d'], ['tab\there', 'q"uote'], ['multi\nline', 'plain']];
    assert.deepEqual(M.parseCsv(M.serializeCsv(src, d), d), src);
  }
});

test('guessTextColumn: a known name first, else the longest mean', () => {
  assert.equal(M.guessTextColumn(['id', 'Review', 'text'], []), 'Review');
  assert.equal(M.guessTextColumn(['a', 'b'], [['1', 'long words here'], ['2', 'more']]), 'b');
  assert.equal(M.guessTextColumn(['a', 'b'], []), 'a');
});

// ---------------------------------------------------------------- parseUpload

test('parseUpload: .jsonl with BOM and CRLF, and .ndjson', () => {
  const r = M.parseUpload('﻿{"text":"a"}\r\n{"text":"b"}\r\n', 'x.jsonl');
  assert.equal(r.kind, 'states');
  assert.equal(r.format, 'jsonl');
  assert.equal(r.count, 2);
  assert.equal(r.column, '*');
  assert.deepEqual(r.columns, ['text']);
  assert.equal(r.by, 'extension');
  assert.equal(M.parseUpload('{"a":1}\nbad\n', 'x.ndjson').skipped, 1);
});

test('parseUpload: a pretty-printed array named .jsonl goes to the JSON path', () => {
  const r = M.parseUpload(JSON.stringify([{ a: 1 }, { a: 2 }], null, 2), 'x.jsonl');
  assert.equal(r.format, 'jsonl');
  assert.equal(r.count, 2);
  assert.ok(r.notes.includes('a JSON document, not JSONL'));
});

test('parseUpload: .txt as lines and as blocks', () => {
  const l = M.parseUpload('one\ntwo\nthree\n\n\n', 'x.txt');
  assert.equal(l.format, 'lines');
  assert.equal(l.count, 3);
  const b = M.parseUpload('a\nb\n\nc\r\n', 'x.txt');
  assert.equal(b.format, 'blocks');
  assert.equal(b.count, 2);
  assert.equal(M.parseUpload('one\n\ntwo\n', 'x.txt').format, 'lines');
  assert.equal(M.parseUpload('lone CR\rsecond', 'x.txt').count, 2);
});

test('parseUpload: .txt of JSON objects is JSONL; .txt of numbers stays lines; .txt with commas is not CSV', () => {
  const j = M.parseUpload('{"a":1}\n{"a":2}\n', 'x.txt');
  assert.equal(j.format, 'jsonl');
  assert.equal(j.by, 'content');
  assert.equal(M.parseUpload('1\n2\n3\n', 'x.txt').format, 'lines');
  assert.equal(M.parseUpload('a,b\nc,d\n', 'x.txt').format, 'lines');
});

test('parseUpload: .csv guesses the review column; .tsv uses tabs', () => {
  const c = M.parseUpload('id,stars,review\n1,5,great stuff\n2,1,"bad, very bad"\n', 'x.csv');
  assert.equal(c.format, 'csv');
  assert.equal(c.column, 'review');
  assert.equal(c.count, 2);
  const t = M.parseUpload('id\tbody\n1\thi, there\n', 'x.tsv');
  assert.equal(t.delimiter, '\t');
  assert.equal(t.column, 'body');
  assert.ok(t.notes.includes('tab-separated'));
});

test('parseUpload: a file with no extension and CSV content; unknown extension note', () => {
  const r = M.parseUpload('name,comment\nA,first\nB,second\n', 'data');
  assert.equal(r.format, 'csv');
  assert.equal(r.column, 'comment');
  assert.equal(r.by, 'content');
  const u = M.parseUpload('just\nlines\n', 'x.weird');
  assert.equal(u.format, 'lines');
  assert.ok(u.notes.includes('unknown extension, read as text'));
});

test('parseUpload: MIME only without a known extension', () => {
  assert.equal(M.parseUpload('{"a":1}\n{"a":2}', 'x.jsonl', 'application/json').format, 'jsonl');
  assert.equal(M.parseUpload('a,b\n1,2', 'blob', 'text/csv').by, 'mime');
});

test('parseUpload: .json arrays', () => {
  const s = M.parseUpload(JSON.stringify(['one', 'two\nlines', null, '']), 'x.json');
  assert.equal(s.format, 'blocks');
  assert.equal(s.count, 2);
  assert.equal(s.skipped, 2);
  const o = M.parseUpload(JSON.stringify([{ text: 'a', meta: { n: 1 } }, { text: 'b' }]), 'x.json');
  assert.equal(o.format, 'jsonl');
  assert.deepEqual(o.columns, ['text', 'meta']);
  const e = M.parseUpload('[]', 'x.json');
  assert.equal(e.kind, 'states');
  assert.equal(e.count, 0);
});

test('parseUpload: object with an array key, a single object, invalid JSON', () => {
  const i = M.parseUpload(JSON.stringify({ meta: 1, items: ['a', 'b'] }), 'x.json');
  assert.equal(i.format, 'lines');
  assert.equal(i.count, 2);
  assert.ok(i.notes.includes('states from "items"'));
  const one = M.parseUpload('{"text":"a"}', 'x.json');
  assert.equal(one.format, 'jsonl');
  assert.equal(one.count, 1);
  assert.ok(one.notes.some((n) => n.startsWith('a single JSON object')));
  assert.match(M.parseUpload('{nope', 'x.json').message, /invalid JSON/);
  const jl = M.parseUpload('{"a":1}\n{"a":2}\n', 'x.json');
  assert.equal(jl.format, 'jsonl');
  assert.ok(jl.notes.includes('not one JSON document; read as JSONL'));
});

test('parseUpload: ojui-batch restores; without questions only states; ojui-export and empty text are refused', () => {
  const exp = { format: 'ojui-batch', version: 1, title: 'Sentiment', questions: { s: { type: 'noul' } }, options: { steps: 2 }, imageCount: 1,
    rows: [{ index: 1, state: 'good' }, { index: 2, state: 'bad' }, { index: 3, state: null }] };
  const b = M.parseUpload(JSON.stringify(exp), 'batch-x.json');
  assert.equal(b.kind, 'batch');
  assert.equal(b.title, 'Sentiment');
  assert.deepEqual(b.questions, exp.questions);
  assert.equal(b.count, 2);
  assert.equal(b.format, 'lines');
  assert.equal(b.imageCount, 1);
  const nq = M.parseUpload(JSON.stringify({ ...exp, questions: {} }), 'x.json');
  assert.equal(nq.kind, 'states');
  assert.equal(nq.count, 2);
  const conv = M.parseUpload(JSON.stringify({ format: 'ojui-export', conversations: [] }), 'conv.json');
  assert.equal(conv.kind, 'reject');
  assert.match(conv.message, /sidebar/);
  assert.equal(M.parseUpload(' \n\r\n', 'x.txt').kind, 'reject');
});

// ---------------------------------------------------------------- mergeInputs

test('mergeInputs: blank a, lines + lines, lines + JSONL objects', () => {
  const b = { format: 'csv', input: 'a\n1', column: 'a' };
  assert.deepEqual(M.mergeInputs({ format: 'lines', input: ' \n', column: '' }, b), b);
  assert.deepEqual(M.mergeInputs({ format: 'lines', input: 'x\ny' }, { format: 'lines', input: 'z' }), { format: 'lines', input: 'x\ny\nz', column: '' });
  const m = M.mergeInputs({ format: 'lines', input: 'x' }, { format: 'jsonl', input: '{"t":1}', column: '*' });
  assert.equal(m.format, 'jsonl');
  assert.deepEqual(states(M.parseStates(m.input, 'jsonl')), ['x', { t: 1 }]);
});

test('mergeInputs: CSVs with the same header stay CSV; different headers go through statesToBatchInput', () => {
  const same = M.mergeInputs({ format: 'csv', input: 'id,text\n1,a', column: 'text' }, { format: 'csv', input: 'id,text\n2,"b, c"', column: 'text' });
  assert.equal(same.format, 'csv');
  assert.equal(same.column, 'text');
  assert.deepEqual(states(M.parseStates(same.input, 'csv', 'text')), ['a', 'b, c']);
  const diff = M.mergeInputs({ format: 'csv', input: 'id,text\n1,a', column: 'text' }, { format: 'csv', input: 'k,body\n2,b', column: 'body' });
  assert.equal(diff.format, 'lines');
  assert.equal(diff.input, 'a\nb');
  const star = M.mergeInputs({ format: 'csv', input: 'id,text\n1,a', column: '*' }, { format: 'lines', input: 'z' });
  assert.equal(star.format, 'jsonl');
});

test('mergeInputs: each side is resolved with its own pick', () => {
  const m = M.mergeInputs({ format: 'jsonl', input: '{"text":"a","id":1}', column: 'text' }, { format: 'lines', input: 'b' });
  assert.deepEqual(states(M.parseStates(m.input, m.format, m.column)), ['a', 'b']);
});

test('mergeInputs: JSONL with the same field keeps the field', () => {
  const m = M.mergeInputs({ format: 'jsonl', input: '{"text":"a"}', column: 'text' }, { format: 'jsonl', input: '{"text":"b","x":1}', column: 'text' });
  assert.deepEqual([m.format, m.column], ['jsonl', 'text']);
  assert.equal(M.parseStates(m.input, m.format, m.column).states.length, 2);
});

test('mergeInputs: JSONL with differing fields keeps a.length + b.length states', () => {
  const a = { format: 'jsonl', input: '{"text":"a1"}\n{"text":"a2"}', column: 'text' };
  const b = { format: 'jsonl', input: '{"review":"b1"}\n{"review":"b2"}', column: 'review' };
  const m = M.mergeInputs(a, b);
  assert.deepEqual(states(M.parseStates(m.input, m.format, m.column)), ['a1', 'a2', 'b1', 'b2']);
});

test('mergeInputs: same-header CSVs with different columns do not drop rows', () => {
  const a = { format: 'csv', input: 'id,text,review\n1,a,x', column: 'text' };
  const b = { format: 'csv', input: 'id,text,review\n2,,y', column: 'review' };
  const m = M.mergeInputs(a, b);
  assert.deepEqual(states(M.parseStates(m.input, m.format, m.column)), ['a', 'y']);
});

test('parseCsv: a mid-field quote is plain text (TSV and CSV)', () => {
  const tsv = 'id\treview\n1\tThe 27" monitor is great\n2\tbad\n3\tok\n';
  const r = M.parseStates(tsv, 'csv', 'review');
  assert.equal(r.delimiter, '\t');
  assert.deepEqual(states(r), ['The 27" monitor is great', 'bad', 'ok']);
  const csv = 'id,review\n1,The 27" monitor is great\n2,bad\n3,ok\n';
  assert.equal(M.parseStates(csv, 'csv', 'review').states.length, 3);
  assert.equal(M.parseUpload(tsv, 'r.tsv').count, 3);
});

test('parseCsv: an unterminated opening quote falls back to quoting off', () => {
  const r = M.parseStates('id\ttext\n1\t"hello\n2\tworld\n', 'csv', 'text');
  assert.equal(r.states.length, 2);
});

test('sniffDelimiter: a mid-field quote on the header does not stop the count', () => {
  assert.equal(M.sniffDelimiter('id\t27" size\tb\n1\t2\t3'), '\t');
});

// ---------------------------------------------------------------- capInput

test('capInput: lines, blocks, JSONL and CSV at max 3', () => {
  const l = M.capInput({ format: 'lines', input: 'a\n\nb\nc\nd\ne', column: '' }, 3);
  assert.deepEqual([l.input, l.kept, l.truncated], ['a\n\nb\nc', 3, 2]);
  const b = M.capInput({ format: 'blocks', input: 'a\nx\n\nb\n\nc\n\n\nd', column: '' }, 3);
  assert.deepEqual([b.input, b.kept, b.truncated], ['a\nx\n\nb\n\nc', 3, 1]);
  const j = M.capInput({ format: 'jsonl', input: '1\n2\n3\n4', column: '*' }, 3);
  assert.deepEqual([j.input, j.truncated, j.column], ['1\n2\n3', 1, '*']);
  const c = M.capInput({ format: 'csv', input: 'h\n1\n2\n3\n4\n5', column: 'h' }, 3);
  assert.deepEqual([c.input, c.kept, c.truncated], ['h\n1\n2\n3', 3, 2]);
  const same = { format: 'lines', input: 'a\nb\n', column: '' };
  const n = M.capInput(same, 3);
  assert.deepEqual([n.input, n.kept, n.truncated], ['a\nb\n', 2, 0]);
});

// ---------------------------------------------------------------- readFileText

test('readFileText: UTF-8 BOM, UTF-16LE, Windows-1252, binary, spreadsheets', async () => {
  const u8 = await M.readFileText(new File([new Uint8Array([0xEF, 0xBB, 0xBF]), 'a\r\nb'], 'x.txt'));
  assert.deepEqual([u8.text, u8.encoding, u8.binary], ['a\nb', 'utf-8', false]);
  const u16 = await M.readFileText(new File([new Uint8Array([0xFF, 0xFE]), Buffer.from('a\tb\n', 'utf16le')], 'x.tsv'));
  assert.deepEqual([u16.text, u16.encoding], ['a\tb\n', 'utf-16le']);
  const w = await M.readFileText(new File([new Uint8Array([0x63, 0x61, 0x66, 0xE9])], 'x.txt'));
  assert.deepEqual([w.text, w.encoding, w.note], ['café', 'windows-1252', 'decoded as Windows-1252']);
  const bin = await M.readFileText(new File([new Uint8Array([0, 1, 0, 2])], 'bin.dat'));
  assert.equal(bin.binary, true);
  await assert.rejects(M.readFileText(new File(['x'], 'sheet.xlsx')), /export the sheet as CSV/);
  await assert.rejects(M.readFileText(new File(['x'], 'doc.pdf')), /not a text format/);
});

test('isImageFile: by type or extension', () => {
  assert.equal(M.isImageFile({ name: 'a.PNG', type: '' }), true);
  assert.equal(M.isImageFile({ name: 'a.bin', type: 'image/webp' }), true);
  assert.equal(M.isImageFile({ name: 'a.jsonl', type: 'application/json' }), false);
});

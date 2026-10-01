// Code snippets for a turn's request (builder C): curl, Python (typesafe_sdk for System One,
// openai for chat), JS fetch, raw HTTP and OpenAI Python. Snippets target the direct upstream
// (config.openjevUrl), not the UI proxy, and run verbatim.

import { isObj } from '/js/jev/util.js';
import { getCachedConfig } from '/js/core/api.js';

export const BIG_BODY = 100 * 1024;
const EXT_FIELDS = ['images', 'steps', 'samples', 'think', 'sequential'];

function cfg(config) {
  let c = config;
  if (!c) { try { c = getCachedConfig(); } catch { c = null; } }
  return { openjevUrl: (c?.openjevUrl || 'http://127.0.0.1:8080').replace(/\/+$/, ''), authConfigured: !!c?.authConfigured };
}

function endpointOf(turn) { return turn?.kind === 'chat' ? '/v1/chat/completions' : '/v1/systemone'; }
function bodyOf(turn) { return turn?.request || {}; }
const shq = (str) => `'${String(str).replace(/'/g, `'\\''`)}'`;

/** JSON → Python literal, 4-space indent; short containers stay on one line. */
export function pyLiteral(v, indent = 0, width = 76) {
  const pad = ' '.repeat(indent), padIn = ' '.repeat(indent + 4);
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : 'None';
  if (typeof v === 'string') return JSON.stringify(v);
  const inline = (x) => {
    if (Array.isArray(x)) return `[${x.map(inline).join(', ')}]`;
    if (isObj(x)) return `{${Object.entries(x).map(([k, y]) => `${JSON.stringify(k)}: ${inline(y)}`).join(', ')}}`;
    return pyLiteral(x);
  };
  const one = inline(v);
  if (one.length + indent <= width) return one;
  if (Array.isArray(v)) return `[\n${v.map((x) => padIn + pyLiteral(x, indent + 4, width)).join(',\n')},\n${pad}]`;
  return `{\n${Object.entries(v).map(([k, x]) => `${padIn}${JSON.stringify(k)}: ${pyLiteral(x, indent + 4, width)}`).join(',\n')},\n${pad}}`;
}

function jsonBody(body, pretty) { return pretty ? JSON.stringify(body, null, 2) : JSON.stringify(body); }
export function bodySize(turn) { try { return new Blob([JSON.stringify(bodyOf(turn))]).size; } catch { return JSON.stringify(bodyOf(turn)).length; } }

function curlSnippet(turn, c) {
  const body = bodyOf(turn);
  const url = c.openjevUrl + endpointOf(turn);
  const text = JSON.stringify(body);
  const big = text.length > BIG_BODY;
  const lines = [];
  if (big) lines.push(`# The body is ${(text.length / 1024).toFixed(0)} KB (images), so it is read from request.json.`, '# Save it with "Download request.json" in the inspector first.');
  lines.push(`curl -sS${turn?.kind === 'chat' && body.stream ? ' -N' : ''} ${url} \\`);
  if (c.authConfigured) lines.push('  -H "Authorization: Bearer $OPENJEV_API_KEY" \\');
  lines.push('  -H "Content-Type: application/json" \\');
  lines.push(big ? '  -d @request.json' : `  -d ${shq(text.length < 1500 ? jsonBody(body, true) : text)}`);
  return lines.join('\n');
}

function pyAccessors(questions, answersTypes) {
  const ids = Object.keys(questions || {});
  const acc = (qid) => {
    const t = (questions[qid] || {}).type || answersTypes?.[qid];
    const k = JSON.stringify(qid);
    if (t === 'noul') return `r.nouls[${k}].noul`;
    if (t === 'choice') return `r.choices[${k}].choice`;
    if (t === 'score') return `r.scores[${k}].score`;
    return `r.answers[${k}]`;
  };
  if (ids.length && ids.length <= 5) return `print(${ids.map(acc).join(', ')})`;
  return [
    'for qid, a in r.nouls.items():',
    '    print(qid, "P(yes)", round(a.noul, 4))',
    'for qid, a in r.choices.items():',
    '    print(qid, a.choice, round(a.confidence, 3))',
    'for qid, a in r.scores.items():',
    '    print(qid, "E", round(a.score, 2))',
  ].join('\n');
}

function pythonSystemOne(turn, c) {
  const body = bodyOf(turn);
  const big = JSON.stringify(body).length > BIG_BODY;
  const head = [
    'import os',
    big ? 'import json' : null,
    'from typesafe_sdk import TypeSafeClient',
    '',
    `client = TypeSafeClient(base_url=${JSON.stringify(c.openjevUrl)},`,
    '                        api_key=os.environ.get("OPENJEV_API_KEY", "local"))',
  ].filter((x) => x !== null);
  const ext = {};
  for (const k of EXT_FIELDS) if (body[k] !== undefined && body[k] !== null && !(k === 'sequential' && body[k] === false)) ext[k] = body[k];
  let call;
  if (big) {
    call = [
      '# The body is large (images), so it is read from request.json ("Download request.json" in the inspector).',
      'with open("request.json") as f:',
      '    body = json.load(f)',
      'r = client.system_one(',
      '    body["state"],',
      '    body["questions"],',
      '    model=body["model"],',
      `    extra_body={k: body[k] for k in ${pyLiteral(EXT_FIELDS)} if k in body},`,
      ')',
    ];
  } else {
    call = [
      'r = client.system_one(',
      `    ${pyLiteral(body.state, 4)},`,
      `    ${pyLiteral(body.questions || {}, 4)},`,
      `    model=${JSON.stringify(body.model || 'openjev-latest')},`,
      Object.keys(ext).length ? `    extra_body=${pyLiteral(ext, 4)},` : null,
      ')',
    ].filter((x) => x !== null);
  }
  const answerTypes = {};
  for (const [k, a] of Object.entries(turn?.response?.answers || {})) answerTypes[k] = a?.type;
  return [...head, ...call, pyAccessors(body.questions, answerTypes), 'print(r.usage)'].join('\n');
}

function openaiPython(turn, c) {
  const body = bodyOf(turn);
  const args = [
    `    model=${JSON.stringify(body.model || 'diffusiongemma-26b')},`,
    `    messages=${pyLiteral(body.messages || [], 4)},`,
    body.max_tokens ? `    max_tokens=${body.max_tokens},` : null,
    body.response_format ? `    response_format=${pyLiteral(body.response_format, 4)},` : null,
    '    stream=True,',
    '    stream_options={"include_usage": True},',
  ].filter(Boolean);
  return [
    'import os',
    'from openai import OpenAI',
    '',
    `client = OpenAI(base_url=${JSON.stringify(c.openjevUrl + '/v1')},`,
    '                api_key=os.environ.get("OPENJEV_API_KEY", "local"))',
    'stream = client.chat.completions.create(',
    ...args,
    ')',
    'for chunk in stream:',
    '    if chunk.choices and chunk.choices[0].delta.content:',
    '        print(chunk.choices[0].delta.content, end="", flush=True)',
    '    if chunk.usage:',
    '        print("\\n", chunk.usage)',
  ].join('\n');
}

function fetchSnippet(turn, c) {
  const body = bodyOf(turn);
  const big = JSON.stringify(body).length > BIG_BODY;
  const headers = { 'Content-Type': 'application/json' };
  const hdr = [`    "Content-Type": "application/json",`];
  if (c.authConfigured) hdr.push('    Authorization: `Bearer ${process.env.OPENJEV_API_KEY}`,');
  const lines = [
    '// Node 18+ (OpenJev sends no CORS headers, so a browser page on another origin cannot call it).',
    big ? 'import { readFile } from "node:fs/promises";' : null,
    big ? 'const body = JSON.parse(await readFile("request.json", "utf8"));' : `const body = ${JSON.stringify(body, null, 2)};`,
    '',
    `const res = await fetch(${JSON.stringify(c.openjevUrl + endpointOf(turn))}, {`,
    '  method: "POST",',
    '  headers: {',
    ...hdr,
    '  },',
    '  body: JSON.stringify(body),',
    '});',
  ].filter((x) => x !== null);
  void headers;
  if (turn?.kind === 'chat' && body.stream) {
    lines.push(
      'const decoder = new TextDecoder();',
      'for await (const chunk of res.body) {',
      '  for (const line of decoder.decode(chunk, { stream: true }).split("\\n")) {',
      '    if (!line.startsWith("data: ") || line === "data: [DONE]") continue;',
      '    const ev = JSON.parse(line.slice(6));',
      '    process.stdout.write(ev.choices?.[0]?.delta?.content ?? "");',
      '  }',
      '}',
    );
  } else {
    lines.push(
      'console.log(res.status, res.headers.get("server-timing"), res.headers.get("x-request-id"));',
      'const data = await res.json();',
      'console.log(JSON.stringify(data, null, 2));',
    );
  }
  return lines.join('\n');
}

function httpSnippet(turn, c) {
  const body = bodyOf(turn);
  const text = JSON.stringify(body, null, 2);
  let host = '127.0.0.1:8080';
  try { host = new URL(c.openjevUrl).host; } catch { /* keep default */ }
  let len = text.length;
  try { len = new Blob([text]).size; } catch { /* ascii estimate */ }
  return [
    `POST ${endpointOf(turn)} HTTP/1.1`,
    `Host: ${host}`,
    'Content-Type: application/json',
    turn?.kind === 'chat' && body.stream ? 'Accept: text/event-stream' : 'Accept: application/json',
    c.authConfigured ? 'Authorization: Bearer $OPENJEV_API_KEY' : null,
    `Content-Length: ${len}`,
    '',
    text,
  ].filter((x) => x !== null).join('\n');
}

/** buildSnippet(kind, turn, config) → string. kind: 'curl'|'python'|'fetch'|'http'|'openai' */
export function buildSnippet(kind, turn, config) {
  const c = cfg(config);
  const chat = turn?.kind === 'chat';
  switch (kind) {
    case 'curl': return curlSnippet(turn, c);
    case 'python': return chat ? openaiPython(turn, c) : pythonSystemOne(turn, c);
    case 'fetch': return fetchSnippet(turn, c);
    case 'http': return httpSnippet(turn, c);
    case 'openai':
      return chat ? openaiPython(turn, c)
        : '# /v1/systemone is not an OpenAI endpoint. Use the Python (typesafe_sdk) snippet,\n# or requests:\n'
          + `import os, requests\nr = requests.post(${JSON.stringify(c.openjevUrl + '/v1/systemone')},\n    headers={"Authorization": "Bearer " + os.environ.get("OPENJEV_API_KEY", "local")},\n    json=${pyLiteral(bodyOf(turn), 4)})\nprint(r.headers.get("server-timing"), r.json())`;
    default: return `# unknown snippet kind: ${kind}`;
  }
}

export const SNIPPET_KINDS = [
  { id: 'curl', label: 'curl' },
  { id: 'python', label: 'Python' },
  { id: 'fetch', label: 'JS fetch' },
  { id: 'http', label: 'raw HTTP' },
  { id: 'openai', label: 'OpenAI / requests' },
];

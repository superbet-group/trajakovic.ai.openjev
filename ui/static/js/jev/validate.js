// Client-side validation of a QuestionSet, mirroring openjev/api.py (pydantic models plus
// the 400 semantic checks), a JSON Schema for vanilla-jsoneditor's Ajv validator, and the
// mapping of a server 422 `loc` onto builder paths.

import { isObj } from '/js/jev/util.js';

export const LIMITS = { maxQuestions: 256, choiceMaxOptions: 255, scoreMaxLevels: 10 };
export const QID_PATTERN = /^[A-Za-z_][\w-]*$/;
const TYPES = ['noul', 'choice', 'score'];
const KNOWN_KEYS = new Set(['type', 'instructions', 'criteria']);

const isDescribed = (v) => v === null || typeof v === 'string' || typeof v === 'object';
const isContent = (v) => typeof v === 'string' || (v !== null && typeof v === 'object');

/**
 * Validate a QuestionSet. → {valid, errors: [{path, message}], warnings: [{path, message}]}
 * `path` is [qid, field, ...] (empty for set-level problems).
 */
export function validateQuestions(questions, limits = LIMITS) {
  const errors = [], warnings = [];
  const err = (path, message) => errors.push({ path, message });
  const warn = (path, message) => warnings.push({ path, message });
  if (!isObj(questions)) {
    err([], 'questions must be an object of {qid: question}');
    return { valid: false, errors, warnings };
  }
  const ids = Object.keys(questions);
  if (ids.length === 0) err([], 'add at least one question');
  if (ids.length > limits.maxQuestions) err([], `at most ${limits.maxQuestions} questions per request (have ${ids.length})`);
  for (const qid of ids) {
    const q = questions[qid];
    if (qid === '') err([qid], 'question id is empty');
    else if (!QID_PATTERN.test(qid)) warn([qid], `id "${qid}" does not match ${QID_PATTERN.source} (allowed, but awkward in SDK code)`);
    if (!isObj(q)) { err([qid], 'a question must be an object with a type'); continue; }
    if (!TYPES.includes(q.type)) {
      err([qid, 'type'], q.type === undefined ? 'type is missing (noul, choice or score)' : `unknown type "${q.type}" (the server answers 400 api_usage_error)`);
      continue;
    }
    for (const k of Object.keys(q)) if (!KNOWN_KEYS.has(k)) warn([qid, k], `unknown field "${k}" is ignored by the server`);
    if ('instructions' in q && !isDescribed(q.instructions)) err([qid, 'instructions'], 'instructions must be a string, object, array or null');
    if (typeof q.instructions === 'string' && !q.instructions.trim()) warn([qid, 'instructions'], 'instructions are empty');
    if (q.type === 'noul') {
      const c = q.criteria;
      if (c !== undefined && c !== null) {
        if (!isObj(c)) err([qid, 'criteria'], 'noul criteria must be an object {true?, false?}');
        else {
          for (const k of Object.keys(c)) {
            if (k !== 'true' && k !== 'false') err([qid, 'criteria', k], `noul criteria keys are only "true" and "false" (got "${k}")`);
            else if (!isDescribed(c[k])) err([qid, 'criteria', k], `criteria.${k} must be a string, object, array or null`);
          }
        }
      }
    } else if (q.type === 'choice') {
      const c = q.criteria;
      if (c === undefined) err([qid, 'criteria'], 'choice needs criteria: {option: description}');
      else if (!isObj(c)) err([qid, 'criteria'], 'choice criteria must be an object {option: description}');
      else {
        const keys = Object.keys(c);
        if (keys.length === 0) err([qid, 'criteria'], 'a choice needs at least one option (the server answers 400 "no options")');
        if (keys.length > limits.choiceMaxOptions) err([qid, 'criteria'], `at most ${limits.choiceMaxOptions} options (have ${keys.length})`);
        if (keys.length === 1) warn([qid, 'criteria'], 'one option is always chosen with probability 1');
        for (const k of keys) {
          if (k.trim() === '') err([qid, 'criteria', k], 'option name is empty');
          if (!isDescribed(c[k])) err([qid, 'criteria', k], `option "${k}" description must be a string, object, array or null`);
        }
      }
    } else if (q.type === 'score') {
      const c = q.criteria;
      if (c === undefined) err([qid, 'criteria'], 'score needs criteria: [level0, level1, …]');
      else if (!Array.isArray(c)) err([qid, 'criteria'], 'score criteria must be an array of levels');
      else {
        if (c.length === 0) err([qid, 'criteria'], 'a score needs at least one level');
        if (c.length > limits.scoreMaxLevels) err([qid, 'criteria'], `at most ${limits.scoreMaxLevels} levels (have ${c.length}); the labels are the single tokens 0–9, so "10" would be two tokens`);
        if (c.length === 1) warn([qid, 'criteria'], 'one level is answered directly with probability 1');
        c.forEach((lv, i) => {
          if (!isContent(lv)) err([qid, 'criteria', String(i)], `level ${i} must be a string, object or array (null is rejected with a 422)`);
          else if (typeof lv === 'string' && !lv.trim()) warn([qid, 'criteria', String(i)], `level ${i} is empty`);
        });
      }
    }
  }
  return { valid: errors.length === 0, errors, warnings };
}

/** Map a FastAPI 422 detail list onto builder paths: ["body","questions",qid,type,...rest] → [qid, ...rest]. */
export function mapServerErrors(details) {
  if (!Array.isArray(details)) return [];
  const out = [];
  for (const d of details) {
    const loc = Array.isArray(d?.loc) ? d.loc.map(String) : [];
    let i = 0;
    if (loc[i] === 'body') i++;
    if (loc[i] !== 'questions') {
      out.push({ path: [], message: `${loc.join(' › ') || 'body'}: ${d?.msg || 'invalid'}`, server: true });
      continue;
    }
    i++;
    const qid = loc[i++];
    if (qid === undefined) { out.push({ path: [], message: d?.msg || 'invalid questions', server: true }); continue; }
    if (TYPES.includes(loc[i])) i++;
    out.push({ path: [qid, ...loc.slice(i)], message: d?.msg || 'invalid', server: true });
  }
  return out;
}

const DESCRIBED = { type: ['string', 'object', 'array', 'null'] };

/** JSON Schema (draft-07) for a QuestionSet, used through createAjvValidator. */
export const QUESTION_SET_SCHEMA = {
  $schema: 'http://json-schema.org/draft-07/schema#',
  title: 'OpenJev QuestionSet',
  type: 'object',
  minProperties: 1,
  maxProperties: LIMITS.maxQuestions,
  additionalProperties: {
    type: 'object',
    required: ['type'],
    properties: {
      type: { enum: TYPES },
      instructions: DESCRIBED,
    },
    allOf: [
      {
        if: { properties: { type: { const: 'noul' } } },
        then: {
          properties: {
            criteria: {
              type: ['object', 'null'],
              properties: { true: DESCRIBED, false: DESCRIBED },
              additionalProperties: false,
            },
          },
        },
      },
      {
        if: { properties: { type: { const: 'choice' } } },
        then: {
          required: ['criteria'],
          properties: {
            criteria: { type: 'object', minProperties: 1, maxProperties: LIMITS.choiceMaxOptions, additionalProperties: DESCRIBED },
          },
        },
      },
      {
        if: { properties: { type: { const: 'score' } } },
        then: {
          required: ['criteria'],
          properties: {
            criteria: { type: 'array', minItems: 1, maxItems: LIMITS.scoreMaxLevels, items: { type: ['string', 'object', 'array'] } },
          },
        },
      },
    ],
  },
};

/** Count questions by type. */
export function typeCounts(questions) {
  const out = { noul: 0, choice: 0, score: 0 };
  if (isObj(questions)) for (const q of Object.values(questions)) if (q && out[q.type] !== undefined) out[q.type]++;
  return out;
}

export const pathKey = (path) => (path || []).join('\u0000');

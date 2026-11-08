// Template resolution for automation steps — the contract's "Template syntax" section, implemented
// as a pure module (no Angular, no DOM) so it can be unit-tested with plain `node`
// (see template.spec.ts: `npm run test:automations`).
//
//   {{path}}               dotted path with numeric indexes: steps.s3.rows.0, inputs.modele.file_id,
//                          trigger.message.subject, item, item.<Nom de colonne>, index, now, today, run.id
//   $index segment         replaced by the current loop index: {{steps.s4.$index.file.file_id}}
//   key matching           exact key first, then case/accent-insensitive, then ignoring punctuation
//                          and spaces ("Nom_complet" ≈ "Nom complet"); keys containing dots work too
//   exact expression       a string that is EXACTLY one {{…}} → the raw value (object/list/number)
//   interpolation          otherwise values are stringified into the surrounding text
//   when                   falsy = empty, null, false, 0, [], "false" (also "0"/"null") → step skipped

export interface TemplateScope {
  steps: Record<string, unknown>;
  inputs: Record<string, unknown>;
  trigger?: unknown;
  item?: unknown;
  index?: number;
  now?: string;
  today?: string;
  run?: { id: string };
  // Dataset run: the whole automation is replayed once per row. `row` is the current row object
  // (columns addressable as {{row.<Nom de colonne>}}, same matching as `item`).
  row?: Record<string, unknown>;
  row_index?: number;
  row_total?: number;
}

export interface Resolution { found: boolean; value: unknown; }

const EXPR_GLOBAL = /\{\{\s*([^{}]*?)\s*\}\}/g;
const EXPR_ONLY = /^\s*\{\{\s*([^{}]+?)\s*\}\}\s*$/;

/** Lowercase, accents stripped, whitespace collapsed: "Prénom  Élève" → "prenom eleve". */
export function normalizeKey(key: string): string {
  return String(key).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
}

/** Only letters/digits (after normalizeKey): "Nom_complet" and "Nom complet" → "nomcomplet". */
function compactKey(key: string): string {
  return normalizeKey(key).replace(/[^a-z0-9]/g, '');
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Find `segments[i..]` (possibly several segments joined by '.', for keys that contain dots) as a
 *  key of `obj`. Tiers: exact, then normalized, then compact; shortest match first within a tier. */
function matchKey(obj: Record<string, unknown>, segments: string[], i: number): { key: string; consumed: number } | null {
  const keys = Object.keys(obj);
  const tiers: ((k: string) => string)[] = [k => k, normalizeKey, compactKey];
  for (const norm of tiers) {
    const index = new Map<string, string>();
    for (const k of keys) { const n = norm(k); if (!index.has(n)) index.set(n, k); }
    for (let len = 1; i + len <= segments.length; len++) {
      const wanted = norm(segments.slice(i, i + len).join('.'));
      if (wanted === '' && norm !== tiers[0]) continue;
      const hit = index.get(wanted);
      if (hit !== undefined) return { key: hit, consumed: len };
    }
  }
  return null;
}

/** Resolve a dotted path against the scope. `$index` segments become the loop index. */
export function resolvePath(scope: TemplateScope, path: string): Resolution {
  const raw = String(path ?? '').trim();
  if (!raw) return { found: false, value: undefined };
  const segments = raw.split('.').map(s => s.trim());
  for (let k = 0; k < segments.length; k++) {
    if (segments[k] === '$index') {
      if (typeof scope.index !== 'number') return { found: false, value: undefined };
      segments[k] = String(scope.index);
    }
  }
  let current: unknown = scope as unknown;
  let i = 0;
  while (i < segments.length) {
    if (Array.isArray(current)) {
      const seg = segments[i];
      if (!/^\d+$/.test(seg)) return { found: false, value: undefined };
      const idx = Number(seg);
      if (idx >= current.length) return { found: false, value: undefined };
      current = current[idx];
      i++;
      continue;
    }
    if (isPlainObject(current)) {
      const m = matchKey(current, segments, i);
      if (!m) return { found: false, value: undefined };
      current = current[m.key];
      i += m.consumed;
      continue;
    }
    return { found: false, value: undefined }; // path goes deeper than a primitive/null
  }
  return { found: current !== undefined, value: current };
}

/** How a value reads inside a sentence. Lists of simple values → "a, b"; objects → JSON. */
export function stringify(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value);
  if (Array.isArray(value) && value.every(v => v === null || ['string', 'number', 'boolean'].includes(typeof v))) {
    return value.map(v => stringify(v)).join(', ');
  }
  try { return JSON.stringify(value); } catch { return String(value); }
}

/** Render one string. Exactly one expression → raw value; otherwise interpolated text.
 *  Unresolved paths are appended to `missing` (and render as null / ''). */
export function renderString(text: string, scope: TemplateScope, missing: string[] = []): unknown {
  const only = EXPR_ONLY.exec(text);
  if (only) {
    const r = resolvePath(scope, only[1]);
    if (!r.found) { missing.push(only[1].trim()); return null; }
    return r.value;
  }
  if (!text.includes('{{')) return text;
  return text.replace(EXPR_GLOBAL, (_m, expr: string) => {
    const r = resolvePath(scope, expr);
    if (!r.found) { missing.push(expr.trim()); return ''; }
    return stringify(r.value);
  });
}

/** Deep-render a param value (strings inside objects/arrays included). Never mutates the input. */
export function renderValue(value: unknown, scope: TemplateScope, missing: string[] = []): unknown {
  if (typeof value === 'string') return renderString(value, scope, missing);
  if (Array.isArray(value)) return value.map(v => renderValue(v, scope, missing));
  if (isPlainObject(value)) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) out[k] = renderValue(v, scope, missing);
    return out;
  }
  return value;
}

/** The contract's falsiness: empty, null, false, 0, [], "false" — plus "0"/"null"/"undefined" and
 *  blank strings, which is what those values become once interpolated into text. */
export function isFalsy(value: unknown): boolean {
  if (value === null || value === undefined || value === false) return true;
  if (typeof value === 'number') return value === 0 || Number.isNaN(value);
  if (typeof value === 'string') return ['', 'false', '0', 'null', 'undefined'].includes(value.trim().toLowerCase());
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

/** Evaluate a step's `when`. No condition → true. */
export function evaluateWhen(when: unknown, scope: TemplateScope, missing: string[] = []): boolean {
  if (when === undefined || when === null) return true;
  if (typeof when === 'string' && when.trim() === '') return true;
  const v = typeof when === 'string' ? renderString(when, scope, missing) : when;
  return !isFalsy(v);
}

/** True when an expression/string depends on the loop variables (item, index, $index). */
export function usesLoopVars(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  for (const m of value.matchAll(EXPR_GLOBAL)) {
    const expr = m[1].trim();
    if (/^(item|index)(\.|$)/.test(expr) || /(^|\.)\$index(\.|$)/.test(expr)) return true;
  }
  return false;
}

/** Every {{…}} expression in a (nested) value — used for UI hints. */
export function expressionsIn(value: unknown): string[] {
  if (typeof value === 'string') return Array.from(value.matchAll(EXPR_GLOBAL), m => m[1].trim());
  if (Array.isArray(value)) return value.flatMap(expressionsIn);
  if (isPlainObject(value)) return Object.values(value).flatMap(expressionsIn);
  return [];
}

/** Local date as YYYY-MM-DD (the user's day, not UTC's). */
export function localDate(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

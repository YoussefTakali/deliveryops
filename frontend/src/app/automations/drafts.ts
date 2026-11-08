import { browserTimezone } from './describe';
import { Automation, CompileResult, Trigger } from './models';

/** What the AI said about a compiled automation (explanation, open questions, warnings). The data
 *  model has no column for these, so they're kept per automation on this device. */
export interface CompileNotes {
  explanation: string;
  questions: string[];
  warnings: string[];
  answers?: Record<string, string>;
  analysis_summary?: string;
  at: string;
}

const KEY = 'dm.automation.notes.';

export function saveNotes(id: string, notes: CompileNotes) {
  try { localStorage.setItem(KEY + id, JSON.stringify(notes)); } catch { /* storage unavailable */ }
}
export function loadNotes(id: string): CompileNotes | null {
  try { const raw = localStorage.getItem(KEY + id); return raw ? JSON.parse(raw) : null; } catch { return null; }
}
export function dropNotes(id: string) {
  try { localStorage.removeItem(KEY + id); } catch { /* ignore */ }
}

export function warningText(w: unknown): string {
  if (typeof w === 'string') return w;
  if (w && typeof w === 'object') return (w as any).message ?? JSON.stringify(w);
  return String(w ?? '');
}

/** Schedules without a timezone get the browser's (the backend computes next_run_at with it). */
export function withTimezone(t: Trigger | null | undefined): Trigger {
  if (!t || !t.type) return { type: 'manual' };
  if (t.type === 'schedule' && !t.timezone) return { ...t, timezone: browserTimezone() };
  return t;
}

/** Body for POST /api/automations from a compile result. */
export function draftFromCompile(c: CompileResult, source: Automation['source']): Partial<Automation> {
  const inputs: Automation['inputs'] = {};
  for (const [k, v] of Object.entries(c.inputs ?? {})) inputs[k] = { label: v.label || k, type: v.type === 'file' ? 'file' : 'text', value: v.value ?? null };
  return {
    name: c.name || 'Nouvelle automatisation',
    description: c.description || '',
    trigger: withTimezone(c.trigger),
    steps: c.steps ?? [],
    inputs,
    require_confirmation: true,
    source,
  };
}

export function notesFromCompile(c: CompileResult, extra: Partial<CompileNotes> = {}): CompileNotes {
  return {
    explanation: c.explanation || '',
    questions: (c.questions ?? []).filter(Boolean),
    warnings: (c.warnings ?? []).map(warningText).filter(Boolean),
    at: new Date().toISOString(),
    ...extra,
  };
}

import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom, map } from 'rxjs';

import {
  Automation, AutomationRun, CatalogEntry, CompileResult, FileMeta, RecordingAnalysis, RunLogEntry, RunStatus, RunTrigger, Step, Trigger, ValidationResult,
} from './models';

const BASE = '/api/automations';

/** Lists may come back bare or wrapped ({items:[…]}) — accept both. */
function asList<T>(r: any): T[] {
  if (Array.isArray(r)) return r;
  if (r && Array.isArray(r.items)) return r.items;
  if (r && Array.isArray(r.runs)) return r.runs;
  if (r && Array.isArray(r.automations)) return r.automations;
  return [];
}

/** Header carrying the Graph token for mail.* steps. In microsoft auth mode the auth interceptor
 *  (auth.ts) promotes it to the Authorization bearer, which is where the backend reads it then. */
export const GRAPH_HEADER = 'X-Graph-Token';

@Injectable({ providedIn: 'root' })
export class AutomationApi {
  private http = inject(HttpClient);

  catalog() { return firstValueFrom(this.http.get<any>(`${BASE}/catalog`).pipe(map(r => asList<CatalogEntry>(r.steps ?? r.catalog ?? r)))); }
  list() { return firstValueFrom(this.http.get<any>(`${BASE}/`).pipe(map(r => asList<Automation>(r)))); }
  get(id: string) { return firstValueFrom(this.http.get<Automation>(`${BASE}/${id}`)); }
  create(body: Partial<Automation>) { return firstValueFrom(this.http.post<Automation>(`${BASE}/`, body)); }
  update(id: string, body: Partial<Automation>) { return firstValueFrom(this.http.put<Automation>(`${BASE}/${id}`, body)); }
  remove(id: string) { return firstValueFrom(this.http.delete(`${BASE}/${id}`)); }
  activate(id: string) { return firstValueFrom(this.http.post<Automation>(`${BASE}/${id}/activate`, {})); }
  pause(id: string) { return firstValueFrom(this.http.post<Automation>(`${BASE}/${id}/pause`, {})); }
  approveScript(id: string, sha256: string) { return firstValueFrom(this.http.post<Automation>(`${BASE}/${id}/approve-script`, { sha256 })); }
  validate(body: { trigger: Trigger; steps: Step[]; inputs: Automation['inputs'] }) {
    return firstValueFrom(this.http.post<ValidationResult>(`${BASE}/validate`, body));
  }
  /** Vision brain: given what the desktop app perceived on screen (text only + icon crops), which element to act on. */
  visionDecide(body: { goal: string; action: 'click' | 'type' | 'wait'; window?: string; text?: string; candidates: any[]; icons: any[] }) {
    return firstValueFrom(this.http.post<{ index: number; confidence: number; reason?: string }>(`${BASE}/vision/decide`, body));
  }
  /** Runtime locator repair: which of the window's real controls matches what the step wanted. */
  resolveElement(body: { window: string; step_title: string; name?: string; control_type?: string; action: 'click' | 'type'; elements: any[] }) {
    return firstValueFrom(this.http.post<{ index: number; confidence: number; reason?: string; element: any | null }>(`${BASE}/resolve-element`, body));
  }
  compile(body: { description?: string; recording_id?: string; answers?: Record<string, string>; current?: Partial<Automation> }) {
    return firstValueFrom(this.http.post<CompileResult>(`${BASE}/compile`, body));
  }

  // ---- recordings
  uploadRecording(meta: any, frames: Blob[]) {
    const form = new FormData();
    form.append('meta', JSON.stringify(meta));
    frames.forEach((b, i) => form.append(`f${i}`, b, `f${i}.jpg`));
    return firstValueFrom(this.http.post<{ recording_id: string }>(`${BASE}/recordings`, form));
  }
  analyzeRecording(id: string) { return firstValueFrom(this.http.post<RecordingAnalysis>(`${BASE}/recordings/${id}/analyze`, {})); }
  /** Deterministic faithful replay build from a recording (reliable, identical replay). */
  faithfulRecording(id: string) { return firstValueFrom(this.http.post<CompileResult>(`${BASE}/recordings/${id}/faithful`, {})); }

  // ---- files
  uploadFile(file: Blob, name: string, opts: { automation_id?: string; run_id?: string } = {}) {
    const form = new FormData();
    form.append('file', file, name);
    if (opts.automation_id) form.append('automation_id', opts.automation_id);
    if (opts.run_id) form.append('run_id', opts.run_id);
    return firstValueFrom(this.http.post<FileMeta>(`${BASE}/files`, form));
  }
  fileMeta(id: string) { return firstValueFrom(this.http.get<FileMeta>(`${BASE}/files/${id}/meta`)); }
  async downloadFile(id: string): Promise<{ blob: Blob; name: string }> {
    const res = await firstValueFrom(this.http.get(`${BASE}/files/${id}`, { responseType: 'blob', observe: 'response' }));
    const disposition = res.headers.get('Content-Disposition') ?? '';
    const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    const plain = /filename="?([^";]+)"?/i.exec(disposition);
    const name = utf8 ? decodeURIComponent(utf8[1]) : plain?.[1] ?? id;
    return { blob: res.body!, name };
  }

  // ---- runs
  startRun(id: string, body: { trigger: RunTrigger; dry_run: boolean; context?: any }) {
    return firstValueFrom(this.http.post<AutomationRun>(`${BASE}/${id}/runs`, body));
  }
  confirmRun(runId: string) { return firstValueFrom(this.http.post<AutomationRun>(`${BASE}/runs/${runId}/confirm`, {})); }
  cancelRun(runId: string) { return firstValueFrom(this.http.post<AutomationRun>(`${BASE}/runs/${runId}/cancel`, {})); }
  log(runId: string, entries: RunLogEntry[]) { return firstValueFrom(this.http.post(`${BASE}/runs/${runId}/log`, { entries })); }
  finish(runId: string, status: RunStatus, error?: string | null) {
    return firstValueFrom(this.http.post<AutomationRun>(`${BASE}/runs/${runId}/finish`, error ? { status, error } : { status }));
  }
  runs(id: string) { return firstValueFrom(this.http.get<any>(`${BASE}/${id}/runs`).pipe(map(r => asList<AutomationRun>(r)))); }
  run(runId: string) { return firstValueFrom(this.http.get<AutomationRun>(`${BASE}/runs/${runId}`)); }
  /** Execute one server step (params already rendered). Returns the step's output. */
  async step(runId: string, body: { step_id: string; type: string; params: any }, graphToken?: string | null): Promise<any> {
    const headers = graphToken ? new HttpHeaders({ [GRAPH_HEADER]: graphToken }) : undefined;
    const res: any = await firstValueFrom(this.http.post<any>(`${BASE}/runs/${runId}/steps`, body, { headers }));
    // The contract returns the output itself; tolerate an {output: …} envelope too.
    return res && typeof res === 'object' && !Array.isArray(res) && 'output' in res && Object.keys(res).every(k => ['output', 'step_id', 'type', 'ok'].includes(k))
      ? res.output : res;
  }

  // ---- scheduling (desktop runner)
  due() { return firstValueFrom(this.http.get<any>(`${BASE}/due`).pipe(map(r => asList<Automation>(r)))); }
  /** Returns the created run; the HTTP 409 (already claimed on another device) surfaces as an error. */
  claim(id: string) { return firstValueFrom(this.http.post<any>(`${BASE}/${id}/claim`, {}).pipe(map(r => (r && r.run ? r.run : r) as AutomationRun))); }
  mailChecked(id: string, checkedAt: string) { return firstValueFrom(this.http.post(`${BASE}/${id}/mail-checked`, { checked_at: checkedAt })); }
}

/** Human error text from an HttpErrorResponse / Error. */
export function errorText(err: any): string {
  if (!err) return 'erreur inconnue';
  const detail = err?.error?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d: any) => d?.msg || JSON.stringify(d)).join(' ; ');
  if (detail && typeof detail === 'object') return detail.message || JSON.stringify(detail);
  if (err?.status === 0) return 'serveur injoignable';
  if (err?.status === 403) return 'accès refusé (permission « Automatisations » requise)';
  if (err?.status === 404) return 'introuvable';
  return err?.message || String(err);
}

import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, NgZone, effect, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Session, Source } from './api';
import { Auth } from './auth';
import { Desktop } from './desktop';
import { MicrosoftAuth } from './microsoft-auth';
import { NativeNotify } from './native-notify';

export interface Nudge { kind: string; label: string; weight: number; }
export interface CompanionProfile {
  display_name: string; job_role: string; tone: string; language: string; quiet_start: string; quiet_end: string;
  tracking_enabled: boolean; proactive_enabled: boolean; onboarded: boolean; nudges: Nudge[];
}
export interface Suggestion {
  id: string; kind: string; label: string; title: string; body: string; status: string; created_at: string;
  action: { type?: 'chat' | 'link' | 'brief' | 'none' | 'task'; prompt?: string; link?: string; brief?: 'morning' | 'evening' };
}
export interface DaySummary {
  tracked_seconds: number; switches: number;
  by_category: { category: string; seconds: number }[];
  top: { app: string; title: string; seconds: number }[];
  now: { app: string; title: string; category: string; seconds: number } | null;
}
export interface CompanionState {
  profile: CompanionProfile;
  policy: { enabled: boolean; activity_tracking_allowed: boolean; title_capture: string; retention_days: number; proactive_allowed: boolean; memory_learning_allowed: boolean };
  tracking_active: boolean; can_track: boolean; today: DaySummary | null;
  suggestions: Suggestion[]; fresh: string[];
  agenda: { subject: string; start: string; end: string; attendees: string[]; join_url?: string }[];
  memory_count: number; llm: boolean;
}
export interface Memory { id: string; kind: string; content: string; source: string; pinned: boolean; confidence: number; created_at: string; }
// The companion doing real tasks (agentic): plan → clarifying questions → concrete steps the user
// approves and runs through the safe automation pipeline (confirm gate per risky step, exact scripts).
export interface TaskStep { id: string; type: string; title: string; confirm: boolean; side_effect: boolean; }
export interface TaskScript { step_id: string; title: string; script: string; sha256: string; }
export interface TaskQuestion { question: string; options: string[]; recommended?: string; }
export type TaskPlan =
  | { status: 'questions'; questions: TaskQuestion[]; explanation: string }
  | { status: 'plan'; automation_id: string; title: string; explanation: string; steps: TaskStep[];
      scripts: TaskScript[]; inputs: Record<string, { label: string; type: string }>; warnings: string[]; blocking: boolean };
export interface ChatMessage { id?: string; role: 'user' | 'assistant'; content: string; sources?: Source[]; streaming?: boolean; }

const BASE = '/api/companion';
const STATE_POLL_MS = 60_000;
const DRAIN_MS = 30_000;
const NOTIFY_PREFIX = 'companion-';

/**
 * The companion's always-on client (root singleton, started at bootstrap from app.ts). In the desktop
 * app's main window it ships activity slices to the server while the user has opted in, and every
 * minute asks the server what (if anything) is worth offering right now — new offers surface as a
 * native notification, so the companion is present even when the window is hidden in the tray.
 */
@Injectable({ providedIn: 'root' })
export class Companion {
  private http = inject(HttpClient);
  private auth = inject(Auth);
  private session = inject(Session);
  private desktop = inject(Desktop);
  private ms = inject(MicrosoftAuth);
  private notify = inject(NativeNotify);
  private router = inject(Router);
  private zone = inject(NgZone);

  readonly state = signal<CompanionState | null>(null);
  /** Why the very first load failed, so the page can show an error + "Réessayer" instead of an
   *  eternal spinner. Only set while we have no state at all; cleared on the next success. */
  readonly loadError = signal<string | null>(null);
  readonly trackerRunning = signal(false);
  /** A prompt handed over from a notification / suggestion for the page to send. */
  readonly pendingPrompt = signal<string | null>(null);
  readonly pendingBrief = signal<'morning' | 'evening' | null>(null);
  /** A task ("install X") handed over from an accepted suggestion for the page to run via "Faire". */
  readonly pendingTask = signal<string | null>(null);
  /** Ambient context (Phase D): the perimeter/project the user is currently viewing, set by the app
   *  shell from the route, so the companion answers about "this project" without a dropdown. */
  readonly scope = signal<{ perimeter_id?: string; project_id?: string }>({});
  /** A message to send in the companion chat right away (the dock opens and sends it; on the
   *  /companion page the page chat does). Used by « Déboguer avec l'IA ». */
  readonly dockPrompt = signal<string | null>(null);

  /** Hand an error to the companion so it debugs it WITH the user: likely cause, 2-3 fixes (one
   *  recommended), and a question if something is missing. */
  debug(context: string, error: string) {
    this.dockPrompt.set(`[DÉBOGAGE] ${context}
Erreur affichée : « ${error} »
Aide-moi à comprendre ce qui se passe et à le corriger.`);
  }

  private timers: ReturnType<typeof setInterval>[] = [];
  private started = false;
  private announced = new Set<string>();
  readonly isMain = !this.desktop.available || this.desktop.windowRole === 'main';

  constructor() {
    this.notify.intercept(NOTIFY_PREFIX, (_id, link) => this.zone.run(() => void this.router.navigateByUrl(link || '/companion')),
      (id, action) => void this.zone.run(() => this.onToastAction(id, action)));
    effect(() => {
      const ok = this.auth.status() === 'authenticated' && this.allowed();
      if (ok && !this.started) this.start();
      else if (!ok && this.started) this.stop();
    });
  }

  allowed(): boolean {
    const me = this.session.me();
    return !!me && !!me.permissions?.includes('companion.use');
  }

  get tz(): number { return -new Date().getTimezoneOffset(); }

  private start() {
    this.started = true;
    if (!this.isMain) return;
    void this.refresh();
    this.zone.runOutsideAngular(() => {
      this.timers.push(setInterval(() => void this.refresh(), STATE_POLL_MS));
      this.timers.push(setInterval(() => void this.drain(), DRAIN_MS));
    });
  }

  private stop() {
    this.started = false;
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
    void this.desktop.companion?.stop().catch(() => {});
    this.trackerRunning.set(false);
  }

  private async graphHeaders(): Promise<HttpHeaders | undefined> {
    if (!this.ms.configured()) return undefined;
    // Time-box the silent token: MSAL renews an expired token in a hidden iframe, which can hang
    // indefinitely (blocked third-party cookies, offline, a slow tenant). That must never freeze the
    // page on "Chargement…" — without a token we simply skip the live agenda for this refresh.
    const token = await Promise.race([
      this.ms.getTokenSilent().catch(() => null),
      new Promise<null>(resolve => setTimeout(() => resolve(null), 4000)),
    ]);
    return token ? new HttpHeaders({ 'X-Graph-Token': token }) : undefined;
  }

  async refresh(): Promise<CompanionState | null> {
    try {
      const headers = await this.graphHeaders();
      const s = await firstValueFrom(this.http.get<CompanionState>(`${BASE}/state`, { params: { tz: this.tz }, headers }));
      this.zone.run(() => { this.state.set(s); this.loadError.set(null); });
      await this.syncTracker(s);
      this.announce(s);
      return s;
    } catch (e: unknown) {
      // Keep a working page working (a transient poll failure), but if we never loaded at all, tell
      // the user why rather than spinning forever.
      this.zone.run(() => { if (!this.state()) this.loadError.set(this.describeError(e)); });
      return null;
    }
  }

  private describeError(e: unknown): string {
    const status = (e as { status?: number })?.status;
    if (status === 401 || status === 403) return "Vous n'avez pas accès au compagnon (permission « companion.use » manquante). Voyez un administrateur.";
    if (status === 0 || status === undefined) return 'Serveur injoignable — vérifiez que DeliveryOps est bien démarré, puis réessayez.';
    if (status >= 500) return 'Le compagnon a rencontré une erreur côté serveur. Réessayez dans un instant.';
    return `Impossible de charger le compagnon (erreur ${status}).`;
  }

  /** Manual retry from the error card. */
  retry(): Promise<CompanionState | null> {
    this.loadError.set(null);
    return this.refresh();
  }

  /** Run the desktop sampler exactly when the server says tracking is active for this user. */
  private async syncTracker(s: CompanionState) {
    const bridge = this.desktop.companion;
    if (!bridge || !this.isMain) return;
    try {
      const res = s.tracking_active ? await bridge.start() : await bridge.stop();
      this.zone.run(() => this.trackerRunning.set(res.running));
    } catch { /* older desktop build */ }
  }

  private async drain() {
    const bridge = this.desktop.companion;
    if (!bridge || !this.trackerRunning()) return;
    try {
      const { events } = await bridge.drain();
      if (!events.length) return;
      const res = await firstValueFrom(this.http.post<{ stored: number; tracking_active: boolean }>(`${BASE}/activity`, { events }));
      if (!res.tracking_active) { await bridge.stop(); this.zone.run(() => this.trackerRunning.set(false)); }
    } catch { /* offline: slices are dropped rather than queued on disk, by design */ }
  }

  /** New offers become a native toast (once each) when the app isn't in front. */
  private announce(s: CompanionState) {
    for (const sugg of s.suggestions) {
      if (!s.fresh.includes(sugg.id) || this.announced.has(sugg.id)) continue;
      this.announced.add(sugg.id);
      if (document.hasFocus() && this.router.url.startsWith('/companion')) continue;
      this.notify.show({
        id: `${NOTIFY_PREFIX}${sugg.id}`, title: sugg.title, body: sugg.body, link: '/companion',
        actions: sugg.action?.type && sugg.action.type !== 'none' ? [{ id: 'accept', label: 'Oui, merci' }, { id: 'dismiss', label: 'Pas maintenant' }] : [{ id: 'dismiss', label: 'OK' }],
      });
    }
  }

  private async onToastAction(id: string, action: string) {
    const sid = id.slice(NOTIFY_PREFIX.length);
    const sugg = this.state()?.suggestions.find(x => x.id === sid);
    if (!sugg) return;
    if (action === 'accept') { await this.accept(sugg); void this.router.navigateByUrl('/companion'); }
    else await this.decide(sugg, 'dismissed');
  }

  // ---- suggestions ----
  async decide(s: Suggestion, status: 'accepted' | 'dismissed' | 'snoozed' | 'never') {
    await firstValueFrom(this.http.post(`${BASE}/suggestions/${s.id}/decision`, { status }));
    this.state.update(st => st ? { ...st, suggestions: st.suggestions.filter(x => x.id !== s.id) } : st);
  }

  /** Accept an offer: record the (learning) signal and hand its action to the page. */
  async accept(s: Suggestion) {
    await this.decide(s, 'accepted');
    if (s.action?.type === 'task' && s.action.prompt) this.pendingTask.set(s.action.prompt);
    else if (s.action?.type === 'chat' && s.action.prompt) this.pendingPrompt.set(s.action.prompt);
    else if (s.action?.type === 'brief') this.pendingBrief.set(s.action.brief ?? 'morning');
    else if (s.action?.type === 'link' && s.action.link) void this.router.navigateByUrl(s.action.link);
  }

  // ---- profile / memories / privacy ----
  async saveProfile(patch: Partial<CompanionProfile> & { nudge_weights?: Record<string, number> }) {
    await firstValueFrom(this.http.put(`${BASE}/profile`, patch));
    await this.refresh();
  }
  /** Plan a real task ("install Python…"): returns clarifying questions, or a concrete plan (a draft
   *  automation the user approves + runs). Answers to prior questions are sent back to refine. */
  planTask(message: string, answers: Record<string, string> = {}, automationId?: string) {
    return firstValueFrom(this.http.post<TaskPlan>(`${BASE}/task`, { message, answers, automation_id: automationId ?? null }));
  }
  memories() { return firstValueFrom(this.http.get<Memory[]>(`${BASE}/memories`)); }
  addMemory(content: string, kind: string) { return firstValueFrom(this.http.post<Memory>(`${BASE}/memories`, { content, kind })); }
  updateMemory(id: string, patch: Partial<Pick<Memory, 'content' | 'kind' | 'pinned'>>) { return firstValueFrom(this.http.put<Memory>(`${BASE}/memories/${id}`, patch)); }
  deleteMemory(id: string) { return firstValueFrom(this.http.delete(`${BASE}/memories/${id}`)); }
  messages() { return firstValueFrom(this.http.get<ChatMessage[]>(`${BASE}/messages`)); }
  exportMine() { return firstValueFrom(this.http.get<any>(`${BASE}/me/export`)); }
  async eraseMine(scope: 'all' | 'activity' | 'messages') {
    await firstValueFrom(this.http.delete(`${BASE}/me/data`, { params: { scope } }));
    await this.refresh();
  }

  // ---- streaming (chat + briefs) ----
  async stream(path: 'chat' | 'brief', body: object, onEvent: (ev: any) => void, signal?: AbortSignal) {
    const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(await this.auth.currentHeaders()) };
    if (this.ms.configured()) {
      const token = await this.ms.getTokenSilent().catch(() => null);
      if (token) headers['X-Graph-Token'] = token;
    }
    // Phase D: on chat, attach the ambient scope (current perimeter/project) so the companion can
    // read the delivery data the user is looking at.
    const ambient = path === 'chat' ? this.scope() : {};
    const resp = await fetch(`${BASE}/${path}`, { method: 'POST', headers, body: JSON.stringify({ ...body, ...ambient, tz: this.tz }), signal });
    if (!resp.ok || !resp.body) {
      // Governance refusals (AI blocked, daily quota, agent cap) carry a user-facing reason: show it.
      let detail = '';
      try { detail = (await resp.json())?.detail ?? ''; } catch { /* not JSON */ }
      throw new Error(typeof detail === 'string' && detail ? detail
        : resp.status === 429 ? 'Trop de requêtes, patientez quelques secondes.' : `Compagnon indisponible (${resp.status})`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, sep).split('\n').find(l => l.startsWith('data: '));
        buf = buf.slice(sep + 2);
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }

  // ---- administration ----
  adminPolicy() { return firstValueFrom(this.http.get<any>(`${BASE}/admin/policy`)); }
  saveAdminPolicy(patch: object) { return firstValueFrom(this.http.put<any>(`${BASE}/admin/policy`, patch)); }
  adminMetrics(days = 30) { return firstValueFrom(this.http.get<any>(`${BASE}/admin/metrics`, { params: { days } })); }
  adminUsers() { return firstValueFrom(this.http.get<any[]>(`${BASE}/admin/users`)); }
  adminErase(owner: string) { return firstValueFrom(this.http.delete(`${BASE}/admin/users/${encodeURIComponent(owner)}/data`)); }
}

import { Injectable, NgZone, computed, effect, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { Session } from '../api';
import { Auth } from '../auth';
import { Desktop } from '../desktop';
import { MicrosoftAuth } from '../microsoft-auth';
import { NativeNotify } from '../native-notify';
import { AutomationApi, errorText } from './automation-api';
import { outputSummary } from './describe';
import { Automation, AutomationRun, CatalogEntry, FileRef, MailTrigger, RunLogEntry, RunStatus, Step } from './models';
import { RecordingProcessor } from './recording';
import { TemplateScope, evaluateWhen, localDate, renderValue, usesLoopVars } from './template';

const DUE_POLL_MS = 60_000;
const MAIL_POLL_MS = 5 * 60_000;
const FIRST_POLL_DELAY_MS = 8_000;
const MAX_FOREACH = 200;
const MAX_MAIL_TRIGGER_RUNS_PER_POLL = 10;
const NOTIFY_PREFIX = 'automation-';

/** Progress of a run executing in THIS window (the editor shows it live). */
export interface LiveRun {
  runId: string;
  automationId: string;
  name: string;
  dryRun: boolean;
  status: RunStatus;
  stepIndex: number;
  stepTotal: number;
  stepTitle: string;
  iteration: { i: number; n: number } | null;
  log: RunLogEntry[];
  startedAt: string;
  /** A flow.confirm step waiting for the user ("Continuer" / "Annuler"). */
  confirm: { message: string } | null;
  error?: string | null;
}

/** A schedule/mail run the backend created in awaiting_confirmation (require_confirmation). */
export interface PendingConfirmation { run: AutomationRun; automation: Automation; }

class RunCancelled extends Error { constructor(message = 'Exécution annulée') { super(message); } }

function nowIso() { return new Date().toISOString(); }
function optStr(v: unknown): string | undefined { return v === undefined || v === null || v === '' ? undefined : String(v); }
function isTrue(v: unknown): boolean { return v === true || v === 'true' || v === 1 || v === '1'; }

function fileIdOf(v: any): string | null {
  if (!v) return null;
  if (typeof v === 'string') return v;
  if (typeof v === 'object') return v.file_id ?? v.id ?? v.file?.file_id ?? null;
  return null;
}
function toFileRef(meta: any): FileRef {
  return { file_id: meta?.file_id ?? meta?.id, name: meta?.name, size: meta?.size, content_type: meta?.content_type };
}

/**
 * Executes automations (contract section 5). Root singleton started at bootstrap (app.ts).
 *
 *  - Manual runs ("Exécuter maintenant" / "Test à blanc") work everywhere; automations with local
 *    steps (print, files, PowerShell) need the desktop app.
 *  - In the desktop app's MAIN window only, it also runs in the background: every 60 s it polls
 *    /due → /claim (409 = another device got it) → asks for confirmation through a native toast
 *    ("Exécuter" / "Ignorer") when the run is awaiting_confirmation → executes; every 5 min it
 *    checks mail-triggered automations and starts a run per new matching message.
 *  - Steps: templates rendered (template.ts), `foreach` (≤200, output = list), `when`, `on_error`;
 *    routed by the catalog's executor: server → POST /runs/{id}/steps, local → desktop bridge,
 *    control → here (flow.confirm). Progress is logged to the run, the final status via /finish.
 */
@Injectable({ providedIn: 'root' })
export class AutomationRunner {
  private api = inject(AutomationApi);
  private auth = inject(Auth);
  private session = inject(Session);
  private desktop = inject(Desktop);
  private ms = inject(MicrosoftAuth);
  private notify = inject(NativeNotify);
  private router = inject(Router);
  private zone = inject(NgZone);
  // Constructed here so a finished desktop recording is picked up from any page (see recording.ts).
  private recordings = inject(RecordingProcessor);

  /** Background scheduling is possible here (desktop app, main window, recent desktop build). */
  readonly background = this.desktop.available && this.desktop.windowRole === 'main' && !!this.desktop.automation;
  readonly localStepsAvailable = !!this.desktop.automation;
  readonly catalog = signal<CatalogEntry[]>([]);
  readonly live = signal<Record<string, LiveRun>>({});
  readonly pending = signal<PendingConfirmation[]>([]);
  readonly activeCount = signal(0);
  /** Bumped whenever a run starts/ends here, so pages can reload their history. */
  readonly version = signal(0);
  readonly liveList = computed(() => Object.values(this.live()));

  private started = false;
  private timers: ReturnType<typeof setInterval>[] = [];
  private executing = new Set<string>();
  private cancelRequests = new Set<string>();
  private stepConfirms = new Map<string, (ok: boolean) => void>();
  private queue: Promise<unknown> = Promise.resolve();
  private polling = false;
  private mailPolling = false;
  private catalogPromise: Promise<CatalogEntry[]> | null = null;

  constructor() {
    this.notify.intercept(NOTIFY_PREFIX, (id, link) => this.onToastOpen(id, link), (id, action, link) => void this.onToastAction(id, action, link));
    effect(() => {
      const ok = this.auth.status() === 'authenticated' && this.allowed();
      if (ok && !this.started) this.start();
      else if (!ok && this.started) this.stop();
    });
  }

  /** User has the automation.use permission (UI gate; the API enforces it). */
  allowed(): boolean {
    const me = this.session.me();
    return !!me && (!me.permissions || me.permissions.includes('automation.use'));
  }

  private start() {
    this.started = true;
    if (!this.background) return;
    // Timers run outside Angular so an idle app doesn't trigger change detection every minute.
    this.zone.runOutsideAngular(() => {
      this.timers.push(setTimeout(() => { void this.pollDue(); void this.pollMail(); }, FIRST_POLL_DELAY_MS) as any);
      this.timers.push(setInterval(() => void this.pollDue(), DUE_POLL_MS));
      this.timers.push(setInterval(() => void this.pollMail(), MAIL_POLL_MS));
    });
  }

  private stop() {
    this.started = false;
    for (const t of this.timers) { clearInterval(t); clearTimeout(t as any); }
    this.timers = [];
    if (this.background) void this.desktop.automation!.setActiveCount(0).catch(() => {});
  }

  ensureCatalog(): Promise<CatalogEntry[]> {
    if (this.catalog().length) return Promise.resolve(this.catalog());
    if (!this.catalogPromise) {
      this.catalogPromise = this.api.catalog().then(c => { this.catalog.set(c); return c; })
        .finally(() => { this.catalogPromise = null; });
    }
    return this.catalogPromise;
  }

  entry(type: string): CatalogEntry | undefined { return this.catalog().find(e => e.type === type); }

  /** Local steps (print, files, PowerShell) in this automation, by the catalog's executor. */
  localSteps(a: Pick<Automation, 'steps'>): Step[] {
    return (a.steps ?? []).filter(s => (this.entry(s.type)?.executor ?? (s.type.startsWith('local.') ? 'local' : 'server')) === 'local');
  }

  needsGraph(a: Pick<Automation, 'steps' | 'trigger'>): boolean {
    return (a.steps ?? []).some(s => s.type.startsWith('mail.')) || a.trigger?.type === 'mail';
  }

  /** Why this automation can't be run from here (null = it can). */
  cannotRunReason(a: Automation): string | null {
    if (this.localSteps(a).length && !this.localStepsAvailable) {
      return this.desktop.available
        ? 'Mettez à jour l’application bureau pour exécuter les étapes locales (impression, fichiers, PowerShell).'
        : 'Nécessite l’application bureau : cette automatisation imprime, lit ou enregistre des fichiers sur le PC.';
    }
    return null;
  }

  /** Tell the desktop shell how many automations are active (tray + hide-on-close behaviour). */
  async refreshActiveCount(list?: Automation[]) {
    try {
      const all = list ?? await this.api.list();
      const n = all.filter(a => a.status === 'active' && a.trigger?.type !== 'manual').length;
      this.activeCount.set(n);
      if (this.background) await this.desktop.automation!.setActiveCount(n);
    } catch { /* offline — try again next poll */ }
  }

  // ---------------------------------------------------------------- manual runs

  /** "Exécuter maintenant" / "Test à blanc". Acquires the Graph token interactively first if the
   *  automation needs mail (so a consent redirect happens before a run exists), then starts the
   *  run and executes it. Resolves with the run once it has STARTED; `done` settles at the end. */
  async runNow(a: Automation, dryRun: boolean): Promise<{ run: AutomationRun; done: Promise<RunStatus> }> {
    await this.ensureCatalog();
    const reason = this.cannotRunReason(a);
    if (reason) throw new Error(reason);
    if (this.needsGraph(a) && this.ms.configured()) {
      const token = await this.ms.getAutomationToken(true);
      if (!token) throw new Error('Autorisation Microsoft en cours…');
    }
    let run = await this.api.startRun(a.id, { trigger: dryRun ? 'test' : 'manual', dry_run: dryRun });
    if (run.status === 'awaiting_confirmation') run = await this.api.confirmRun(run.id); // the click IS the confirmation
    this.version.update(v => v + 1);
    const done = this.enqueue(() => this.execute(a, run));
    return { run, done };
  }

  cancel(runId: string) {
    this.cancelRequests.add(runId);
    const resolve = this.stepConfirms.get(runId);
    if (resolve) resolve(false);
    if (!this.executing.has(runId)) void this.api.cancelRun(runId).then(() => this.version.update(v => v + 1)).catch(() => {});
  }

  answerStepConfirm(runId: string, ok: boolean) {
    const resolve = this.stepConfirms.get(runId);
    if (resolve) resolve(ok);
  }

  /** Confirm a run that was awaiting confirmation (toast button or in-app button) and execute it. */
  async confirmAndRun(run: AutomationRun, automation?: Automation): Promise<void> {
    const a = automation ?? await this.api.get(run.automation_id);
    this.pending.update(list => list.filter(p => p.run.id !== run.id));
    const reason = this.cannotRunReason(a);
    if (reason) { await this.api.cancelRun(run.id).catch(() => {}); throw new Error(reason); }
    const confirmed = await this.api.confirmRun(run.id);
    this.version.update(v => v + 1);
    void this.enqueue(() => this.execute(a, confirmed));
  }

  async skipRun(run: AutomationRun): Promise<void> {
    this.pending.update(list => list.filter(p => p.run.id !== run.id));
    await this.api.cancelRun(run.id);
    this.version.update(v => v + 1);
  }

  // ---------------------------------------------------------------- background polling

  private async pollDue() {
    if (this.polling || !this.started || this.auth.status() !== 'authenticated') return;
    this.polling = true;
    try {
      await this.ensureCatalog();
      const due = await this.api.due();
      for (const a of due) {
        let run: AutomationRun;
        try { run = await this.api.claim(a.id); }
        catch (err: any) { if (err?.status !== 409) console.warn('[automations] claim failed', a.id, err); continue; }
        await this.dispatchStarted(a, run);
      }
      await this.refreshActiveCount();
    } catch (err) {
      console.warn('[automations] /due poll failed', err);
    } finally {
      this.polling = false;
    }
  }

  /** New matching mails for active mail-triggered automations → one run per message. The mailbox
   *  is read straight from Microsoft Graph with the user's own token (same filter semantics and
   *  item shape as the mail.search step), so polling never creates empty runs. */
  private async pollMail() {
    if (this.mailPolling || !this.started || !this.ms.configured() || this.auth.status() !== 'authenticated') return;
    this.mailPolling = true;
    try {
      const all = await this.api.list();
      const targets = all.filter(a => a.status === 'active' && a.trigger?.type === 'mail');
      if (!targets.length) return;
      const token = (await this.ms.getAutomationToken(false)) ?? (await this.ms.getTokenSilent());
      if (!token) return; // re-auth needed — the user will be asked when they next open the app
      await this.ensureCatalog();
      for (const a of targets) {
        const checkedAt = nowIso();
        const since = a.mail_trigger_checked_at || a.updated_at || checkedAt;
        let messages: any[];
        try { messages = await this.searchMail(token, a.trigger as MailTrigger, since); }
        catch (err) { console.warn('[automations] mail poll failed', a.id, err); continue; }
        // Record the check first: a second device polling a moment later won't re-trigger the same mails.
        await this.api.mailChecked(a.id, checkedAt).catch(() => {});
        for (const message of messages.slice(-MAX_MAIL_TRIGGER_RUNS_PER_POLL)) {
          try {
            const run = await this.api.startRun(a.id, { trigger: 'mail', dry_run: false, context: { trigger: { message } } });
            await this.dispatchStarted(a, run);
          } catch (err) { console.warn('[automations] mail-triggered run failed to start', a.id, err); }
        }
      }
    } catch (err) {
      console.warn('[automations] mail poll failed', err);
    } finally {
      this.mailPolling = false;
    }
  }

  private async searchMail(token: string, t: MailTrigger, sinceIso: string): Promise<any[]> {
    const since = new Date(sinceIso).toISOString();
    const filter = [`receivedDateTime gt ${since}`, ...(t.has_attachment ? ['hasAttachments eq true'] : [])].join(' and ');
    const params = new URLSearchParams({
      $filter: filter, $orderby: 'receivedDateTime desc', $top: '25',
      $select: 'id,subject,from,receivedDateTime,bodyPreview,hasAttachments',
      $expand: 'attachments($select=id,name,size,contentType)',
    });
    const res = await fetch(`https://graph.microsoft.com/v1.0/me/messages?${params}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(`Graph ${res.status}`);
    const body = await res.json();
    const norm = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    const items = (body.value ?? []).map((m: any) => ({
      id: m.id, subject: m.subject ?? '', from_name: m.from?.emailAddress?.name ?? '', from_email: m.from?.emailAddress?.address ?? '',
      received: m.receivedDateTime, body_preview: m.bodyPreview ?? '',
      attachments: (m.attachments ?? []).map((x: any) => ({ id: x.id, name: x.name, size: x.size, content_type: x.contentType })),
    })).filter((m: any) => {
      if (t.from_contains && !norm(`${m.from_name} ${m.from_email}`).includes(norm(t.from_contains))) return false;
      if (t.subject_contains && !norm(m.subject).includes(norm(t.subject_contains))) return false;
      if (t.has_attachment && !m.attachments.length) return false;
      return true;
    });
    return items.reverse(); // oldest first
  }

  /** A run created by claim/startRun in the background: ask first, or execute right away. */
  private async dispatchStarted(a: Automation, run: AutomationRun) {
    this.version.update(v => v + 1);
    if (run.status === 'awaiting_confirmation') { this.askConfirmation(a, run); return; }
    if (run.status !== 'running') return;
    const reason = this.cannotRunReason(a);
    if (reason) { await this.failEarly(run, reason); return; }
    void this.enqueue(() => this.execute(a, run));
  }

  private askConfirmation(a: Automation, run: AutomationRun) {
    this.pending.update(list => [...list.filter(p => p.run.id !== run.id), { run, automation: a }]);
    const why = run.trigger === 'mail' ? `Mail reçu : « ${run.context?.trigger?.message?.subject ?? ''} »` : 'Exécution planifiée';
    this.notify.show({
      id: `${NOTIFY_PREFIX}confirm-${run.id}`,
      title: `Exécuter « ${a.name} » ?`,
      body: `${why}. ${a.steps.length} étape(s).`,
      link: `/automations/${a.id}`,
      actions: [{ id: 'run', label: 'Exécuter' }, { id: 'skip', label: 'Ignorer' }],
    }, { essential: true });
  }

  private onToastOpen(_id: string, link: string) {
    this.zone.run(() => void this.router.navigateByUrl(link || '/automations'));
  }

  private async onToastAction(id: string, action: string, link: string) {
    const confirmRun = /^automation-confirm-(.+)$/.exec(id);
    const stepConfirm = /^automation-step-([^:]+):/.exec(id);
    await this.zone.run(async () => {
      if (confirmRun) {
        const p = this.pending().find(x => x.run.id === confirmRun[1]);
        const run = p?.run ?? await this.api.run(confirmRun[1]).catch(() => null);
        if (!run) return;
        try {
          if (action === 'run') await this.confirmAndRun(run, p?.automation);
          else if (action === 'skip') await this.skipRun(run);
          else this.onToastOpen(id, link);
        } catch (err) {
          this.notify.show({ id: `${NOTIFY_PREFIX}error-${run.id}`, title: 'Automatisation non exécutée', body: errorText(err), link });
        }
        return;
      }
      if (stepConfirm) {
        if (action === 'continue') this.answerStepConfirm(stepConfirm[1], true);
        else if (action === 'cancel') this.answerStepConfirm(stepConfirm[1], false);
        else this.onToastOpen(id, link);
        return;
      }
      this.onToastOpen(id, link);
    });
  }

  private async failEarly(run: AutomationRun, message: string) {
    await this.api.log(run.id, [{ ts: nowIso(), level: 'error', message }]).catch(() => {});
    await this.api.finish(run.id, 'failed', message).catch(() => {});
    this.version.update(v => v + 1);
  }

  private enqueue<T>(job: () => Promise<T>): Promise<T> {
    const next = this.queue.then(job, job);
    this.queue = next.catch(() => undefined);
    return next;
  }

  // ---------------------------------------------------------------- execution

  private patchLive(runId: string, patch: Partial<LiveRun>) {
    this.live.update(m => (m[runId] ? { ...m, [runId]: { ...m[runId], ...patch } } : m));
  }

  /** Runs every step of `run` (already in status running). Never throws; returns the final status. */
  async execute(a: Automation, run: AutomationRun): Promise<RunStatus> {
    if (this.executing.has(run.id)) return 'running';
    this.executing.add(run.id);
    const dryRun = !!run.dry_run;
    const pendingLog: RunLogEntry[] = [];
    const live: LiveRun = {
      runId: run.id, automationId: a.id, name: a.name, dryRun, status: 'running', stepIndex: 0, stepTotal: a.steps.length,
      stepTitle: '', iteration: null, log: [], startedAt: run.started_at || nowIso(), confirm: null,
    };
    this.live.update(m => ({ ...m, [run.id]: live }));

    const log = (level: RunLogEntry['level'], message: string, step_id?: string | null, output_summary?: string | null) => {
      const entry: RunLogEntry = { ts: nowIso(), level, message, ...(step_id ? { step_id } : {}), ...(output_summary ? { output_summary } : {}) };
      pendingLog.push(entry);
      this.live.update(m => (m[run.id] ? { ...m, [run.id]: { ...m[run.id], log: [...m[run.id].log, entry] } } : m));
    };
    const flush = async () => {
      if (!pendingLog.length) return;
      const batch = pendingLog.splice(0);
      try { await this.api.log(run.id, batch); } catch { pendingLog.unshift(...batch); }
    };

    const inputs: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(a.inputs ?? {})) inputs[k] = v?.value ?? null;
    const scope: TemplateScope = {
      steps: {}, inputs, trigger: run.context?.trigger ?? {}, now: nowIso(), today: localDate(), run: { id: run.id },
    };

    // Dataset run: replay the WHOLE automation once per row (each user/line with their own data),
    // exposing the current row as {{row.<column>}}. No dataset → a single normal pass (unchanged).
    const dataset = this.datasetRows(a);
    let passIteration: { i: number; n: number } | null = null;

    // One full pass over the steps against the current scope. Shared by the single pass and each row.
    const runSteps = async () => {
      for (let i = 0; i < a.steps.length; i++) {
        this.throwIfCancelled(run.id);
        const step = a.steps[i];
        const entry = this.entry(step.type);
        const title = step.title || entry?.title || step.type;
        this.patchLive(run.id, { stepIndex: i + 1, stepTitle: title, iteration: passIteration });
        if (!entry) throw new Error(`Étape ${step.id} : type inconnu « ${step.type} »`);
        const output = await this.runStep(a, run, step, entry, scope, dryRun, log);
        scope.steps[step.id] = output;
        await flush();
      }
    };

    let status: RunStatus = 'succeeded';
    let error: string | null = null;
    try {
      await this.ensureCatalog();
      if (dataset && dataset.rows.length > MAX_FOREACH) {
        throw new Error(`Jeu de données « ${dataset.label} » : ${dataset.rows.length} lignes, au-delà de la limite de ${MAX_FOREACH} par exécution.`);
      }
      if (dataset) {
        const total = dataset.rows.length;
        scope.row_total = total;
        // A dry run previews the first row only — replaying every row proves nothing extra and is slow.
        const rows = dryRun ? dataset.rows.slice(0, 1) : dataset.rows;
        const n = rows.length;
        log('info', dryRun
          ? `Test à blanc démarré — aperçu de la 1re ligne sur ${total} du jeu « ${dataset.label} », aucun effet réel.`
          : `Exécution démarrée : ${a.steps.length} étape(s) × ${total} ligne(s) du jeu « ${dataset.label} ».`);
        for (let r = 0; r < n; r++) {
          this.throwIfCancelled(run.id);
          scope.row = rows[r];
          scope.row_index = r;
          scope.steps = {};                    // each row starts fresh — step outputs never bleed across rows
          passIteration = { i: r + 1, n: total };
          this.patchLive(run.id, { iteration: passIteration });
          log('info', `— Ligne ${r + 1}/${total}${this.rowLabel(rows[r])}`);
          await runSteps();
        }
        passIteration = null;
        this.patchLive(run.id, { iteration: null });
        log('info', dryRun ? 'Aperçu terminé (1re ligne).' : `Exécution terminée avec succès (${total} ligne(s)).`);
      } else {
        log('info', dryRun ? `Test à blanc démarré — aucun mail envoyé, rien d’imprimé ni d’enregistré.` : `Exécution démarrée (${a.steps.length} étape(s)).`);
        await runSteps();
        log('info', dryRun ? 'Test à blanc terminé.' : 'Exécution terminée avec succès.');
      }
    } catch (err: any) {
      if (err instanceof RunCancelled || this.cancelRequests.has(run.id)) {
        status = 'cancelled';
        error = err?.message || 'Exécution annulée';
        log('warn', error ?? 'Exécution annulée');
      } else {
        status = 'failed';
        error = err?.message || errorText(err);
        log('error', `Échec : ${error}`);
      }
    } finally {
      await flush();
      try { await this.api.finish(run.id, status, error); } catch { await flush().catch(() => {}); }
      this.executing.delete(run.id);
      this.cancelRequests.delete(run.id);
      this.stepConfirms.delete(run.id);
      this.patchLive(run.id, { status, error, confirm: null, iteration: null });
      this.version.update(v => v + 1);
      this.resultToast(a, run, status, error);
      // Keep the finished run visible a little while so the editor can show the final state.
      setTimeout(() => this.live.update(m => { const { [run.id]: _, ...rest } = m; return rest; }), 15_000);
    }
    return status;
  }

  private throwIfCancelled(runId: string) {
    if (this.cancelRequests.has(runId)) throw new RunCancelled();
  }

  /** The dataset input, if any: the automation runs once per row (see execute). At most one dataset
   *  per automation (enforced by validate); rows are objects {column: value}. */
  private datasetRows(a: Automation): { key: string; label: string; rows: Record<string, unknown>[] } | null {
    for (const [key, spec] of Object.entries(a.inputs ?? {})) {
      if (spec?.type === 'dataset' && Array.isArray(spec.value)) {
        const rows = (spec.value as unknown[]).filter(
          (r): r is Record<string, unknown> => !!r && typeof r === 'object' && !Array.isArray(r));
        return { key, label: spec.label || key, rows };
      }
    }
    return null;
  }

  /** A short, human tag for a dataset row in the log (first couple of column values) so progress is
   *  readable without dumping the whole row. */
  private rowLabel(row: Record<string, unknown>): string {
    const vals = Object.values(row).filter(v => v !== null && v !== undefined && v !== '').slice(0, 2).map(v => String(v).slice(0, 40));
    return vals.length ? ` : ${vals.join(' · ')}` : '';
  }

  /** One step: foreach / when / on_error around executeOnce. Returns the recorded output. */
  private async runStep(a: Automation, run: AutomationRun, step: Step, entry: CatalogEntry, scope: TemplateScope, dryRun: boolean,
    log: (level: RunLogEntry['level'], message: string, step_id?: string | null, output_summary?: string | null) => void): Promise<unknown> {
    const title = step.title || entry.title;
    const onError = step.on_error === 'continue' ? 'continue' : 'stop';
    const whenPerItem = usesLoopVars(step.when);

    if (!whenPerItem) {
      const missing: string[] = [];
      if (!evaluateWhen(step.when, { ...scope, item: undefined, index: undefined }, missing)) {
        log('info', `« ${title} » ignorée (condition non remplie).`, step.id);
        return null;
      }
    }

    const once = async (s: TemplateScope, label: string): Promise<unknown> => {
      const missing: string[] = [];
      if (whenPerItem && !evaluateWhen(step.when, s, missing)) {
        log('info', `${label} ignorée (condition non remplie).`, step.id);
        return null;
      }
      const params = renderValue(step.params ?? {}, s, missing) as Record<string, any>;
      if (missing.length) log('warn', `${label} : valeur(s) introuvable(s) : ${Array.from(new Set(missing)).join(', ')}`, step.id);
      try {
        return await this.executeOnce(a, run, step, entry, params, dryRun);
      } catch (err: any) {
        if (err instanceof RunCancelled) throw err;
        const msg = err?.error ? errorText(err) : (err?.message || errorText(err));
        if (onError === 'continue') { log('error', `${label} : ${msg} (on continue)`, step.id); return null; }
        throw new Error(`${label} : ${msg}`);
      }
    };

    if (step.foreach !== undefined && step.foreach !== null && step.foreach !== '') {
      const missing: string[] = [];
      const list = renderValue(step.foreach, scope, missing);
      if (list === null || list === undefined) {
        log('warn', `« ${title} » : liste à parcourir vide ou introuvable (${missing.join(', ') || step.foreach}) — 0 itération.`, step.id);
        return [];
      }
      if (!Array.isArray(list)) throw new Error(`« ${title} » : foreach doit désigner une liste (${step.foreach}).`);
      if (list.length > MAX_FOREACH) throw new Error(`« ${title} » : ${list.length} éléments, au-delà de la limite de ${MAX_FOREACH} par boucle.`);
      const outputs: unknown[] = [];
      for (let idx = 0; idx < list.length; idx++) {
        this.throwIfCancelled(run.id);
        this.patchLive(run.id, { iteration: { i: idx + 1, n: list.length } });
        outputs.push(await once({ ...scope, item: list[idx], index: idx }, `« ${title} » (${idx + 1}/${list.length})`));
      }
      log('info', `✓ ${title}`, step.id, `${list.length} élément(s) traité(s)`);
      return outputs;
    }

    const output = await once({ ...scope, item: undefined, index: undefined }, `« ${title} »`);
    log('info', `✓ ${title}`, step.id, outputSummary(output) || null);
    return output;
  }

  /** Route one rendered step to its executor. */
  private async executeOnce(a: Automation, run: AutomationRun, step: Step, entry: CatalogEntry, params: Record<string, any>, dryRun: boolean): Promise<unknown> {
    switch (entry.executor) {
      case 'server': {
        const token = step.type.startsWith('mail.') && this.ms.configured() ? await this.ms.getAutomationToken(false) : null;
        if (step.type.startsWith('mail.') && this.ms.configured() && !token) {
          throw new Error('autorisation Microsoft requise (envoi/lecture de mails) — ouvrez l’automatisation et cliquez « Exécuter maintenant » une fois pour l’accorder');
        }
        return this.api.step(run.id, { step_id: step.id, type: step.type, params }, token);
      }
      case 'control':
        return this.executeControl(a, run, step, params, dryRun);
      case 'local':
        return this.executeLocal(a, run, step, entry, params, dryRun);
    }
    throw new Error(`exécuteur inconnu pour ${step.type}`);
  }

  private async executeControl(a: Automation, run: AutomationRun, step: Step, params: Record<string, any>, dryRun: boolean): Promise<unknown> {
    if (step.type === 'flow.wait') {
      const seconds = Math.min(Math.max(Number(params['seconds']) || 0, 0), 600);
      if (!dryRun && seconds) await new Promise(r => setTimeout(r, seconds * 1000));
      return { waited: dryRun ? 0 : seconds };
    }
    if (step.type !== 'flow.confirm') throw new Error(`étape de contrôle inconnue : ${step.type}`);
    const message = String(params['message'] || 'Continuer l’automatisation ?');
    if (dryRun) return { confirmed: true, simulated: true };
    const answer = new Promise<boolean>(resolve => this.stepConfirms.set(run.id, resolve));
    this.patchLive(run.id, { confirm: { message } });
    this.notify.show({
      id: `${NOTIFY_PREFIX}step-${run.id}:${step.id}`, title: a.name, body: message, link: `/automations/${a.id}`,
      actions: [{ id: 'continue', label: 'Continuer' }, { id: 'cancel', label: 'Annuler' }],
    }, { essential: true });
    const ok = await answer;
    this.stepConfirms.delete(run.id);
    this.patchLive(run.id, { confirm: null });
    if (!ok) throw new RunCancelled('Annulée à l’étape de confirmation');
    return { confirmed: true };
  }

  private async executeLocal(a: Automation, run: AutomationRun, step: Step, entry: CatalogEntry, params: Record<string, any>, dryRun: boolean): Promise<unknown> {
    const bridge = this.desktop.automation;
    if (!bridge) throw new Error('étape locale : nécessite l’application bureau DeliveryOps');
    if (dryRun && entry.side_effect) {
      // Test à blanc: side-effecting local steps only describe what they would do.
      switch (step.type) {
        case 'local.print': return { printed: false, preview: { file: params['file_id'] ?? params['path'], printer: params['printer'] || 'imprimante par défaut', copies: params['copies'] ?? 1 } };
        case 'local.save_file': return { path: null, preview: { file: params['file_id'], folder: params['folder'], name: params['name'] ?? null } };
        case 'local.open': return { opened: false, preview: { target: params['target'] } };
        case 'local.powershell': return { stdout: '', stderr: '', exit_code: null, preview: { script: params['script'] } };
        case 'desktop.open_app': return { opened: false, window: null, preview: { app: params['app'], args: params['args'] ?? null } };
        case 'desktop.window': return { found: false, window: null, preview: { window: params['title_contains'], action: params['action'] || 'focus' } };
        case 'desktop.click': return { clicked: false, element: null, preview: { window: params['window'], element: params['name'] || params['automation_id'], type: params['control_type'] ?? null } };
        case 'desktop.type': return { typed: false, element: null, preview: { window: params['window'], field: params['name'] || params['automation_id'] || 'champ actif', text: params['text'] } };
        case 'desktop.keys': return { sent: false, preview: { keys: params['keys'], window: params['window'] ?? null } };
        case 'web.browse': return { url: params['url'], title: '', extracted: {}, text: '', preview: { url: params['url'], actions: params['actions'] ?? [] } };
        case 'vision.click': return { clicked: false, preview: { target: params['target'], window: params['window'] ?? null } };
        case 'vision.type': return { typed: false, preview: { target: params['target'], text: params['text'], window: params['window'] ?? null } };
        default: return { preview: params };
      }
    }
    switch (step.type) {
      case 'local.print': {
        const fid = fileIdOf(params['file_id']);
        if (fid) {
          const { blob, name } = await this.api.downloadFile(fid);
          return bridge.print({ bytes: new Uint8Array(await blob.arrayBuffer()), name, printer: params['printer'] || undefined, copies: Number(params['copies']) || 1 });
        }
        if (!params['path']) throw new Error('file_id ou path requis');
        return bridge.print({ path: String(params['path']), printer: params['printer'] || undefined, copies: Number(params['copies']) || 1 });
      }
      case 'local.save_file': {
        const fid = fileIdOf(params['file_id']);
        if (!fid) throw new Error('file_id requis');
        const { blob, name } = await this.api.downloadFile(fid);
        return bridge.saveFile({ bytes: new Uint8Array(await blob.arrayBuffer()), folder: String(params['folder'] ?? ''), name: String(params['name'] || name) });
      }
      case 'local.read_file': {
        const f = await bridge.readFile({ path: String(params['path'] ?? '') });
        const blob = new Blob([f.bytes as BlobPart], { type: f.content_type });
        const meta = await this.api.uploadFile(blob, f.name, { run_id: run.id });
        return { file: toFileRef({ ...meta, name: meta.name ?? f.name }) };
      }
      case 'local.list_files':
        return bridge.listFiles({ folder: String(params['folder'] ?? ''), pattern: params['pattern'] || undefined,
          modified_since_days: params['modified_since_days'] != null && params['modified_since_days'] !== '' ? Number(params['modified_since_days']) : undefined });
      case 'local.open':
        return bridge.open({ target: String(params['target'] ?? '') });
      case 'local.powershell': {
        // Fresh copy: the approved hashes must come from the server, not from a stale page state.
        const fresh = await this.api.get(a.id);
        const res = await bridge.powershell({ script: String(params['script'] ?? ''), timeout_s: Number(params['timeout_s']) || 120,
          approvedHashes: fresh.approved_script_hashes ?? [] });
        if (res.exit_code !== 0) {
          throw new Error(`le script a échoué (code ${res.exit_code})${res.stderr ? ` : ${res.stderr.slice(0, 400)}` : ''}`);
        }
        return { stdout: res.stdout, stderr: res.stderr, exit_code: res.exit_code };
      }
      case 'desktop.open_app':
        return bridge.openApp({ app: String(params['app'] ?? ''), args: optStr(params['args']), wait_window: optStr(params['wait_window']),
          timeout_s: Number(params['timeout_s']) || undefined });
      case 'desktop.window':
        return bridge.windowAction({ title_contains: String(params['title_contains'] ?? ''), action: optStr(params['action']),
          timeout_s: Number(params['timeout_s']) || undefined });
      case 'desktop.click':
        return this.withResolvedLocator(a, step, 'click', params, loc => bridge.click({
          window: String(params['window'] ?? ''), name: loc.name, automation_id: loc.automation_id, control_type: loc.control_type,
          index: loc.index ?? (Number(params['index']) || 0), double: isTrue(params['double']), timeout_s: loc.timeout_s ?? (Number(params['timeout_s']) || undefined),
        }));
      case 'desktop.type':
        return this.withResolvedLocator(a, step, 'type', params, loc => bridge.type({
          window: String(params['window'] ?? ''), text: String(params['text'] ?? ''), name: loc.name, automation_id: loc.automation_id,
          clear: params['clear'] == null || params['clear'] === '' ? true : isTrue(params['clear']), submit: isTrue(params['submit']),
        }));
      case 'vision.click':
        return this.visionAct(a, run, step, 'click', params);
      case 'vision.type':
        return this.visionAct(a, run, step, 'type', params);
      case 'vision.wait':
        return this.visionAct(a, run, step, 'wait', params);
      case 'vision.read': {
        if (!bridge.visionRead) throw new Error('vision : mettez à jour l’application bureau');
        const r = await bridge.visionRead({ window: optStr(params['window']) });
        return { text: r.text, lines: r.lines };
      }
      case 'desktop.keys':
        return bridge.keys({ keys: String(params['keys'] ?? ''), window: optStr(params['window']) });
      case 'desktop.read':
        return bridge.read({ window: String(params['window'] ?? ''), name: optStr(params['name']), automation_id: optStr(params['automation_id']),
          control_type: optStr(params['control_type']) });
      case 'desktop.read_notifications':
        return bridge.readNotifications({ app_contains: optStr(params['app_contains']), text_contains: optStr(params['text_contains']) });
      case 'desktop.screenshot': {
        const shot = await bridge.screenshot({ window: optStr(params['window']) });
        const meta = await this.api.uploadFile(new Blob([shot.bytes as BlobPart], { type: shot.content_type }), shot.name, { run_id: run.id });
        return { file: toFileRef({ ...meta, name: meta.name ?? shot.name }) };
      }
      case 'web.browse': {
        let actions = params['actions'] ?? [];
        if (typeof actions === 'string') {
          try { actions = JSON.parse(actions); } catch { throw new Error('web.browse : actions doit être une liste JSON'); }
        }
        return bridge.browse({ url: String(params['url'] ?? ''), actions: actions as unknown[], show: isTrue(params['show']),
          timeout_s: Number(params['timeout_s']) || undefined });
      }
    }
    throw new Error(`étape locale inconnue : ${step.type}`);
  }

  /**
   * Self-healing locators for desktop.click / desktop.type. The compiler can only GUESS a control's
   * visible label; when that guess isn't in the real window the old behaviour was to click the
   * nearest lookalike. Now: (1) try a locator already resolved for this step on this PC, (2) try the
   * compiled locator, (3) on failure dump the window's real controls and ask the AI which one matches
   * the step's intent, click that, and remember it for next time.
   */
  private async withResolvedLocator(a: Automation, step: Step, action: 'click' | 'type', params: Record<string, any>,
    run: (loc: { name?: string; automation_id?: string; control_type?: string; index?: number; timeout_s?: number }) => Promise<unknown>): Promise<unknown> {
    const bridge = this.desktop.automation!;
    const window = String(params['window'] ?? '');
    const compiled = { name: optStr(params['name']), automation_id: optStr(params['automation_id']), control_type: optStr(params['control_type']) };
    const key = `dm.locator.${a.id}.${step.id}`;
    let learned: { name?: string; automation_id?: string; control_type?: string } | null = null;
    try { learned = JSON.parse(localStorage.getItem(key) || 'null'); } catch { /* ignore */ }
    if (learned && (learned.name || learned.automation_id)) {
      try { return await run({ ...learned, timeout_s: 8 }); } catch { localStorage.removeItem(key); }
    }
    let firstError: unknown;
    if (compiled.name || compiled.automation_id) {
      try { return await run(compiled); } catch (err) { firstError = err; }
    }
    // Locator repair: what does the window actually contain?
    try {
      const dump = await bridge.dumpElements({ window, max: 300 }).catch(() => null);
      if (!dump || !dump.elements?.length) throw firstError ?? new Error('aucun contrôle lisible dans la fenêtre');
      const res = await this.api.resolveElement({
        window: dump.window || window, step_title: step.title || '', name: compiled.name, control_type: compiled.control_type, action,
        elements: dump.elements.map(e => ({ name: e.name, control_type: e.control_type, automation_id: e.automation_id })),
      }).catch(() => null);
      if (!res || res.index < 0 || !res.element || res.confidence < 0.35) {
        throw new Error(`${errorText(firstError) || 'élément introuvable'} — l'IA n'a pas trouvé de contrôle correspondant dans « ${dump.window || window} »`);
      }
      const chosen = { name: optStr(res.element.name), automation_id: optStr(res.element.automation_id), control_type: optStr(res.element.control_type) };
      const out = await run({ ...chosen, timeout_s: 8 });
      try { localStorage.setItem(key, JSON.stringify(chosen)); } catch { /* storage unavailable */ }
      return out;
    } catch (err) {
      // Last resort: the application exposes nothing usable to the accessibility tree (Citrix, legacy
      // or custom-drawn UI) — look at the screen instead and act on what is visible.
      if (!bridge.visionLocate) throw err;
      const target = compiled.name || step.title || '';
      if (!target) throw err;
      try {
        return await this.visionAct(a, null, step, action, { ...params, target, timeout_s: 6 });
      } catch { throw err; }
    }
  }

  /**
   * Vision-driven action (vision.click / vision.type / vision.wait, and the fallback of desktop.*).
   * The desktop app looks at the screen locally and tries the two AI-free paths first — image memory
   * of what this step clicked last time (~100 ms), then an exact unique label. Only when neither is
   * conclusive is the perceived scene (text + positions, never the screenshot) sent to the backend,
   * where the LLM picks the element from the step's intent. A successful click is remembered as an
   * image patch, so the next run of the same step takes the fast path. Low-confidence decisions are
   * refused: a wrong click is worse than no click.
   */
  private async visionAct(a: Automation, run: AutomationRun | null, step: Step, action: 'click' | 'type' | 'wait', params: Record<string, any>): Promise<unknown> {
    const bridge = this.desktop.automation;
    if (!bridge?.visionLocate || !bridge.visionClickAt) throw new Error('vision : redémarrez l’application bureau après sa mise à jour (fonction d’écran indisponible dans cette version)');
    const target = String(params['target'] ?? '').trim();
    if (!target) throw new Error('vision : cible (target) requise');
    const window = optStr(params['window']);
    const text = String(params['text'] ?? '');
    const timeoutS = Math.min(Math.max(Number(params['timeout_s']) || (action === 'wait' ? 60 : 20), 1), 600);
    const key = `${a.id}.${step.id}.${target}`.slice(0, 300);  // the rendered target: one image memory per loop item
    const started = Date.now();
    const deadline = started + timeoutS * 1000;

    // Invoke-first (faithful replay): if the recorded click carries an accessibility identity, act on
    // the CONTROL through UI Automation (Invoke/Toggle/Select) instead of moving the cursor to a pixel.
    // That is immune to display scaling, occlusion, scrolling and focus races — the usual causes of
    // "clicked the wrong place". Coordinate/vision clicking below stays as the fallback for controls
    // with no usable identity (canvas/Citrix/custom-drawn UIs).
    const gt0 = (params['ground_truth'] && typeof params['ground_truth'] === 'object' ? params['ground_truth'] : {}) as any;
    const el0 = gt0.element && typeof gt0.element === 'object' ? gt0.element : null;
    if (action === 'click' && !isTrue(params['right']) && bridge.click && window && el0 && (optStr(el0.automation_id) || optStr(el0.name))) {
      try {
        const r: any = await bridge.click({
          window, name: optStr(el0.name), automation_id: optStr(el0.automation_id), control_type: optStr(el0.control_type),
          double: isTrue(params['double']), timeout_s: Math.min(timeoutS, 10),
        });
        if (r?.clicked) return { clicked: true, method: 'uia-invoke', label: optStr(el0.name) ?? '', confidence: 0.97 };
      } catch { /* identity not found or not invocable — fall through to on-screen vision below */ }
    }

    let why = '';
    for (;;) {
      if (run) this.throwIfCancelled(run.id);
      // Ground truth captured at record time (faithful replay): the clicked element's identity, an
      // image patch of it, and its position — tried before OCR/AI.
      const gt = (params['ground_truth'] && typeof params['ground_truth'] === 'object' ? params['ground_truth'] : {}) as any;
      const loc = await bridge.visionLocate({ target, key, window, element: gt.element, patch: optStr(gt.patch), point: gt.point, rel: gt.rel });
      let hit: { x: number; y: number; method: string; label: string; confidence: number } | null =
        loc.found ? { x: loc.x!, y: loc.y!, method: loc.method!, label: loc.label ?? '', confidence: loc.confidence ?? 1 } : null;
      if (!hit && loc.scene.candidates.length) {
        const d = await this.api.visionDecide({
          goal: step.title && step.title !== target ? `${target} (étape : ${step.title})` : target, action, window, text,
          candidates: loc.scene.candidates.map(c => ({ i: c.i, label: c.label, control_type: c.control_type, source: c.source, x: c.x, y: c.y, w: c.w, h: c.h })),
          icons: loc.scene.icons,
        }).catch(() => null);
        const chosen = d && d.index >= 0 && d.confidence >= 0.5 ? loc.scene.candidates.find(c => c.i === d.index) : null;
        if (chosen) hit = { x: Math.round(chosen.x + chosen.w / 2), y: Math.round(chosen.y + chosen.h / 2), method: 'ai', label: chosen.label || d!.reason || '', confidence: d!.confidence };
        else why = d?.reason || (loc.scene.ocr ? '' : 'OCR Windows indisponible (installez un module linguistique OCR)');
      }
      if (hit) {
        const waited = Math.round((Date.now() - started) / 1000);
        if (action === 'wait') return { visible: true, label: hit.label, waited_s: waited };
        await bridge.visionClickAt({
          x: hit.x, y: hit.y, double: isTrue(params['double']), right: isTrue(params['right']), window, learnKey: key, label: hit.label,
          type: action === 'type', text, clear: params['clear'] == null || params['clear'] === '' ? true : isTrue(params['clear']), submit: isTrue(params['submit']),
        });
        return { [action === 'type' ? 'typed' : 'clicked']: true, x: hit.x, y: hit.y, method: hit.method, label: hit.label, confidence: hit.confidence };
      }
      if (Date.now() >= deadline) break;
      await new Promise(r => setTimeout(r, 1500));
    }
    if (action === 'wait') return { visible: false, label: '', waited_s: Math.round((Date.now() - started) / 1000) };
    throw new Error(`cible introuvable à l'écran : « ${target} »${why ? ` — ${why}` : ''}`);
  }

  private resultToast(a: Automation, run: AutomationRun, status: RunStatus, error: string | null) {
    if (run.dry_run && document.hasFocus()) return; // the user is watching the test in the editor
    if ((run.trigger === 'manual' || run.trigger === 'test') && document.hasFocus()) return;
    const ok = status === 'succeeded';
    this.notify.show({
      id: `${NOTIFY_PREFIX}result-${run.id}`,
      title: ok ? `« ${a.name} » terminée` : status === 'cancelled' ? `« ${a.name} » annulée` : `« ${a.name} » a échoué`,
      body: ok ? `${a.steps.length} étape(s) exécutée(s)${run.dry_run ? ' (test à blanc)' : ''}.` : (error ?? ''),
      link: `/automations/${a.id}`,
    });
  }
}

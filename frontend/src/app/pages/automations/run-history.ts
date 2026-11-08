import { DatePipe } from '@angular/common';
import { Component, DestroyRef, computed, effect, inject, input, signal } from '@angular/core';

import { AutomationApi, errorText } from '../../automations/automation-api';
import { RUN_STATUS, RUN_TRIGGER, duration } from '../../automations/describe';
import { AutomationRun, RunLogEntry } from '../../automations/models';
import { AutomationRunner, LiveRun } from '../../automations/runner';

/** Run history of one automation: live progress of a run executing here, runs awaiting
 *  confirmation (Exécuter / Ignorer), and past runs with expandable logs. */
@Component({
  selector: 'app-run-history',
  imports: [DatePipe],
  template: `
    @for (lr of live(); track lr.runId) {
      <div class="live" [class.done]="lr.status !== 'running'">
        <div class="row between">
          <strong>@if (lr.status === 'running') { <span class="spin"></span> } {{ lr.dryRun ? 'Test à blanc' : 'Exécution' }} — {{ statusLabel(lr.status) }}</strong>
          @if (lr.status === 'running') { <button class="sm ghost ko" (click)="runner.cancel(lr.runId)">Arrêter</button> }
        </div>
        <div class="bar"><span [style.width.%]="progress(lr)"></span></div>
        <div class="small muted">Étape {{ lr.stepIndex }}/{{ lr.stepTotal }} · {{ lr.stepTitle }}@if (lr.iteration) { · élément {{ lr.iteration.i }}/{{ lr.iteration.n }} }</div>
        @if (lr.confirm) {
          <div class="ask">
            <span>{{ lr.confirm.message }}</span>
            <div class="row"><button class="sm primary" (click)="runner.answerStepConfirm(lr.runId, true)">Continuer</button>
              <button class="sm ghost" (click)="runner.answerStepConfirm(lr.runId, false)">Annuler</button></div>
          </div>
        }
        <ul class="log">
          @for (e of lr.log.slice(-8); track $index) { <li [class]="e.level"><span class="ts">{{ e.ts | date: 'HH:mm:ss' }}</span> {{ e.message }}@if (e.output_summary) { <em> — {{ e.output_summary }}</em> }</li> }
        </ul>
      </div>
    }

    @if (error()) { <p class="small err">{{ error() }}</p> }
    @if (!runs().length && !live().length) { <p class="small faint">Aucune exécution pour l'instant. Commencez par un test à blanc : rien n'est envoyé, imprimé ni enregistré.</p> }

    <div class="runs">
      @for (r of runs(); track r.id) {
        <div class="run" [class.open]="expanded() === r.id">
          <button class="run-head" (click)="toggle(r)">
            <span class="dot" [class]="'dot ' + status(r).cls"></span>
            <span class="what">{{ trigger(r) }}@if (r.dry_run && r.trigger !== 'test') { · test }</span>
            <span class="pill" [class]="'pill ' + status(r).cls">{{ status(r).label }}</span>
            <span class="when small faint">{{ r.started_at | date: 'dd/MM HH:mm' }}@if (r.finished_at) { · {{ took(r) }} }</span>
          </button>
          @if (r.status === 'awaiting_confirmation') {
            <div class="row confirm">
              <span class="small">Cette exécution attend votre confirmation.</span>
              <button class="sm primary" [disabled]="acting()" (click)="confirm(r)">Exécuter</button>
              <button class="sm ghost" [disabled]="acting()" (click)="skip(r)">Ignorer</button>
            </div>
          }
          @if (expanded() === r.id) {
            @if (r.error) { <p class="small err">{{ r.error }}</p> }
            <ul class="log full">
              @for (e of logOf(r); track $index) { <li [class]="e.level"><span class="ts">{{ e.ts | date: 'HH:mm:ss' }}</span> @if (e.step_id) { <code>{{ e.step_id }}</code> } {{ e.message }}@if (e.output_summary) { <em> — {{ e.output_summary }}</em> }</li> }
              @empty { <li class="faint">Aucun journal.</li> }
            </ul>
          }
        </div>
      }
    </div>
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: 10px; }
    .between { justify-content: space-between; }
    .live { border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--border)); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;
      background: var(--surface); animation: rise .2s ease; }
    .live.done { border-color: var(--border); }
    .bar { height: 4px; border-radius: 2px; background: var(--grey-soft); overflow: hidden; }
    .bar span { display: block; height: 100%; background: var(--accent); transition: width .35s ease; }
    .ask { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; background: var(--amber-soft); border-radius: 8px; padding: 8px 10px; font-size: 13px; }
    .log { list-style: none; margin: 0; padding: 0; font-size: 12px; display: flex; flex-direction: column; gap: 2px; }
    .log li { color: var(--muted); line-height: 1.45; word-break: break-word; }
    .log li.warn { color: var(--amber); } .log li.error { color: var(--red); }
    .log em { color: var(--faint); font-style: normal; }
    .log .ts { color: var(--faint); font-variant-numeric: tabular-nums; margin-right: 4px; }
    .log.full { padding: 4px 12px 10px 30px; max-height: 320px; overflow: auto; }
    .runs { display: flex; flex-direction: column; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
    .runs:empty { display: none; }
    .run { border-bottom: 1px solid var(--border); }
    .run:last-child { border-bottom: 0; }
    .run-head { width: 100%; border: 0; border-radius: 0; background: transparent; display: flex; align-items: center; gap: 10px; padding: 8px 12px; font-weight: 400; text-align: left; }
    .run-head:hover { background: var(--surface-2); }
    .what { flex: 1; font-size: 13px; }
    .confirm { padding: 0 12px 8px 30px; }
    .err { color: var(--red); margin: 0; padding: 0 12px; }
    code { font-size: 11px; color: var(--faint); }
    @keyframes rise { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
  `,
})
export class RunHistory {
  private api = inject(AutomationApi);
  protected runner = inject(AutomationRunner);
  readonly automationId = input.required<string>();

  protected runs = signal<AutomationRun[]>([]);
  protected expanded = signal<string | null>(null);
  protected error = signal('');
  protected acting = signal(false);
  protected live = computed(() => this.runner.liveList().filter(l => l.automationId === this.automationId()));

  constructor() {
    effect(() => { this.runner.version(); this.automationId(); void this.load(); });
    // Runs executing elsewhere (another device) or just finishing server-side: refresh while any is in flight.
    const timer = setInterval(() => {
      const localIds = new Set(this.live().map(l => l.runId));
      if (this.runs().some(r => (r.status === 'running' || r.status === 'awaiting_confirmation') && !localIds.has(r.id))) void this.load();
    }, 5000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  async load() {
    try {
      const list = await this.api.runs(this.automationId());
      list.sort((a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? ''));
      this.runs.set(list.slice(0, 30));
      this.error.set('');
    } catch (err) { this.error.set(`Historique indisponible : ${errorText(err)}`); }
  }

  protected status(r: AutomationRun) { return RUN_STATUS[r.status] ?? { label: r.status, cls: '' }; }
  protected statusLabel(s: string) { return RUN_STATUS[s as keyof typeof RUN_STATUS]?.label ?? s; }
  protected trigger(r: AutomationRun) { return RUN_TRIGGER[r.trigger] ?? r.trigger; }
  protected took(r: AutomationRun) { return duration(r.started_at, r.finished_at); }
  protected progress(lr: LiveRun) {
    if (lr.status !== 'running') return 100;
    const within = lr.iteration ? (lr.iteration.i - 1) / lr.iteration.n : 0;
    return Math.round(((lr.stepIndex - 1 + within) / Math.max(1, lr.stepTotal)) * 100);
  }

  protected async toggle(r: AutomationRun) {
    if (this.expanded() === r.id) { this.expanded.set(null); return; }
    this.expanded.set(r.id);
    if (!r.log?.length) {
      try { const full = await this.api.run(r.id); this.runs.update(list => list.map(x => (x.id === r.id ? full : x))); } catch { /* keep summary */ }
    }
  }

  protected logOf(r: AutomationRun): RunLogEntry[] { return r.log ?? []; }

  protected async confirm(r: AutomationRun) {
    this.acting.set(true);
    try { await this.runner.confirmAndRun(r); } catch (err) { this.error.set(errorText(err)); } finally { this.acting.set(false); }
  }
  protected async skip(r: AutomationRun) {
    this.acting.set(true);
    try { await this.runner.skipRun(r); } catch (err) { this.error.set(errorText(err)); } finally { this.acting.set(false); }
  }
}

import { Component, computed, model } from '@angular/core';

import { WEEKDAYS_SHORT, browserTimezone, triggerSummary } from '../../automations/describe';
import { MailTrigger, ScheduleTrigger, Trigger } from '../../automations/models';

/** Trigger editor: manual / schedule / mail, French labels, the browser's timezone for schedules. */
@Component({
  selector: 'app-trigger-editor',
  template: `
    <div class="kinds" role="radiogroup" aria-label="Déclencheur">
      @for (k of kinds; track k.id) {
        <button type="button" role="radio" class="kind" [class.on]="trigger().type === k.id" [attr.aria-checked]="trigger().type === k.id" (click)="setType(k.id)">
          <strong>{{ k.label }}</strong><span class="small muted">{{ k.hint }}</span>
        </button>
      }
    </div>

    @if (schedule(); as s) {
      <div class="fields">
        <div class="f">
          <label for="every">Fréquence</label>
          <select id="every" [value]="s.every" (change)="patch({ every: $any($event.target).value })">
            <option value="day">Chaque jour</option>
            <option value="week">Certains jours de la semaine</option>
            <option value="month">Chaque mois</option>
            <option value="hours">Toutes les N heures</option>
          </select>
        </div>
        @if (s.every === 'hours') {
          <div class="f small-f">
            <label for="ih">Intervalle (heures)</label>
            <input id="ih" type="number" min="1" max="24" [value]="s.interval_hours ?? 1" (change)="patch({ interval_hours: clampInt($any($event.target).value, 1, 24) })" />
          </div>
        } @else {
          <div class="f small-f">
            <label for="at">Heure</label>
            <input id="at" type="time" [value]="s.at ?? '09:00'" (change)="patch({ at: $any($event.target).value || '09:00' })" />
          </div>
        }
        @if (s.every === 'month') {
          <div class="f small-f">
            <label for="dom">Jour du mois</label>
            <input id="dom" type="number" min="1" max="28" [value]="s.day_of_month ?? 1" (change)="patch({ day_of_month: clampInt($any($event.target).value, 1, 28) })" />
          </div>
        }
      </div>
      @if (s.every === 'week') {
        <div class="days" role="group" aria-label="Jours">
          @for (d of weekdays; track $index) {
            <button type="button" class="day" [class.on]="(s.weekdays ?? []).includes($index)" [attr.aria-pressed]="(s.weekdays ?? []).includes($index)" (click)="toggleDay($index)">{{ d }}</button>
          }
          <button type="button" class="sm ghost" (click)="patch({ weekdays: [0, 1, 2, 3, 4] })">Jours ouvrés</button>
        </div>
      }
      <p class="small faint tz">Fuseau horaire : {{ s.timezone || tz }}@if (s.timezone && s.timezone !== tz) { · <button type="button" class="link" (click)="patch({ timezone: tz })">utiliser {{ tz }}</button> }</p>
    }

    @if (mail(); as m) {
      <div class="fields">
        <div class="f"><label for="from">L'expéditeur contient</label>
          <input id="from" [value]="m.from_contains ?? ''" placeholder="ex. rh@entreprise.fr" (change)="patch({ from_contains: $any($event.target).value.trim() || undefined })" /></div>
        <div class="f"><label for="subj">L'objet contient</label>
          <input id="subj" [value]="m.subject_contains ?? ''" placeholder="ex. Stagiaires" (change)="patch({ subject_contains: $any($event.target).value.trim() || undefined })" /></div>
      </div>
      <label class="check"><input type="checkbox" [checked]="!!m.has_attachment" (change)="patch({ has_attachment: $any($event.target).checked || undefined })" /> Uniquement avec une pièce jointe</label>
      <p class="small faint">Vérifié toutes les 5 minutes par l'application bureau. Le mail est disponible dans les étapes sous « trigger.message ».</p>
    }

    <p class="summary">{{ summary() }}</p>
  `,
  styles: `
    .kinds { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; }
    .kind { flex-direction: column; align-items: flex-start; gap: 2px; text-align: left; font-weight: 400; padding: 9px 12px; transition: border-color .15s ease, background-color .15s ease; }
    .kind.on { border-color: var(--accent); background: var(--accent-soft); }
    .kind.on strong { color: var(--accent); }
    .fields { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
    .f { flex: 1 1 200px; }
    .small-f { flex: 0 1 150px; }
    .days { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
    .day { min-width: 46px; justify-content: center; padding: 5px 8px; font-size: 12px; }
    .day.on { background: var(--accent); border-color: var(--accent); color: #fff; }
    .check { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text); cursor: pointer; margin: 4px 0 8px; }
    .check input { width: auto; }
    .tz { margin: 0; }
    .link { border: 0; background: none; padding: 0; color: var(--accent); font-size: inherit; cursor: pointer; }
    .summary { margin: 10px 0 0; font-weight: 500; color: var(--text); }
    @media (max-width: 640px) { .kinds { grid-template-columns: 1fr; } }
  `,
})
export class TriggerEditor {
  readonly trigger = model.required<Trigger>();
  protected weekdays = WEEKDAYS_SHORT;
  protected tz = browserTimezone();
  protected kinds = [
    { id: 'manual' as const, label: 'Manuel', hint: 'Quand je clique sur Exécuter' },
    { id: 'schedule' as const, label: 'Planifié', hint: 'À heure fixe' },
    { id: 'mail' as const, label: 'Mail reçu', hint: 'Quand un mail correspond' },
  ];
  protected schedule = computed(() => { const t = this.trigger(); return t.type === 'schedule' ? t as ScheduleTrigger : null; });
  protected mail = computed(() => { const t = this.trigger(); return t.type === 'mail' ? t as MailTrigger : null; });
  protected summary = computed(() => triggerSummary(this.trigger()));

  protected setType(type: Trigger['type']) {
    if (type === this.trigger().type) return;
    if (type === 'schedule') this.trigger.set({ type: 'schedule', every: 'week', at: '09:00', weekdays: [0], timezone: this.tz });
    else if (type === 'mail') this.trigger.set({ type: 'mail', has_attachment: true });
    else this.trigger.set({ type: 'manual' });
  }

  protected patch(p: Record<string, unknown>) {
    const next: any = { ...this.trigger(), ...p };
    for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k];
    if (next.type === 'schedule') {
      if (!next.timezone) next.timezone = this.tz;
      if (next.every === 'week' && !next.weekdays?.length) next.weekdays = [0];
      if (next.every === 'month' && !next.day_of_month) next.day_of_month = 1;
      if (next.every === 'hours' && !next.interval_hours) next.interval_hours = 1;
      if (next.every !== 'hours' && !next.at) next.at = '09:00';
    }
    this.trigger.set(next);
  }

  protected toggleDay(d: number) {
    const s = this.schedule();
    const days = new Set(s?.weekdays ?? []);
    if (days.has(d)) days.delete(d); else days.add(d);
    this.patch({ weekdays: Array.from(days).sort() });
  }

  protected clampInt(v: string, min: number, max: number) {
    const n = Math.round(Number(v));
    return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : min;
  }
}

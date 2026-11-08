import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api, CompanionDataSummary } from './api';
import { Companion } from './companion.service';

/** "Mes données" (Mon compte): what the companion records and remembers about me, in plain words
 *  and counts — with pause, export and erase at hand. Nothing here shows the content itself. */
@Component({
  selector: 'app-companion-data',
  imports: [DatePipe, DecimalPipe, RouterLink],
  template: `
    @if (data(); as d) {
      <div class="status" [class.on]="d.tracking.active">
        <span class="dot" [class.green]="d.tracking.active"></span>
        <div>
          <strong>{{ d.tracking.active ? 'Suivi d\\'activité en cours' : d.tracking.enabled ? 'Suivi d\\'activité activé (en pause côté organisation)' : 'Suivi d\\'activité désactivé' }}</strong>
          <p class="small muted">
            @if (d.tracking.allowed) {
              {{ d.tracking.enabled ? '' : 'Si vous l\\'activez : ' }}{{ d.tracking.titles === 'full' ? (d.tracking.enabled ? 'Application et titre de fenêtre' : 'application et titre de fenêtre') : (d.tracking.enabled ? 'Nom de l\\'application seulement' : 'nom de l\\'application seulement') }},
              conservés {{ d.tracking.retention_days }} jours@if (d.tracking.excluded_apps) {, {{ d.tracking.excluded_apps }} application(s) jamais enregistrée(s)}.
            } @else {
              Le suivi d'activité n'est pas autorisé pour votre compte : rien n'est enregistré.
            }
          </p>
        </div>
        @if (d.tracking.allowed) {
          <button class="sm" [class.primary]="!d.tracking.enabled" (click)="setTracking(!d.tracking.enabled)" [disabled]="busy()">
            {{ d.tracking.enabled ? 'Mettre en pause' : d.counts.activity ? 'Reprendre' : 'Activer' }}</button>
        }
      </div>

      <dl class="counts">
        <div><dt>Activité</dt><dd>{{ d.counts.activity_hours | number: '1.0-1' }} h<small>{{ d.counts.activity | number }} sessions</small></dd></div>
        <div><dt>Conversation</dt><dd>{{ d.counts.messages | number }}<small>messages</small></dd></div>
        <div><dt>Mémoires</dt><dd>{{ d.counts.memories | number }}<small>{{ d.memory_learning_allowed ? 'apprises ou dites' : 'dites explicitement' }}</small></dd></div>
        <div><dt>Suggestions</dt><dd>{{ d.counts.suggestions | number }}<small>proposées</small></dd></div>
      </dl>
      @if (d.activity_from) {
        <p class="small faint">Activité enregistrée du {{ d.activity_from | date: 'dd/MM/yyyy' }} au {{ d.activity_to | date: 'dd/MM/yyyy' }}.</p>
      }

      <div class="row">
        <button class="sm" (click)="exportAll()" [disabled]="busy()">Exporter tout (JSON)</button>
        <button class="sm" (click)="erase('activity')" [disabled]="busy() || !d.counts.activity">Effacer l'activité</button>
        <button class="sm" (click)="erase('messages')" [disabled]="busy() || !d.counts.messages">Effacer la conversation</button>
        <button class="sm ko" (click)="erase('all')" [disabled]="busy()">Tout effacer</button>
        <span class="spacer"></span>
        <a class="small" routerLink="/companion">Réglages et mémoires du compagnon →</a>
      </div>
      @if (info()) { <p class="small okmsg">{{ info() }}</p> }
    } @else if (error()) {
      <p class="small err">{{ error() }}</p>
    } @else {
      <p class="small muted"><span class="spin"></span> Chargement…</p>
    }
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: 16px; }
    .status { display: flex; gap: 12px; align-items: flex-start; padding: 12px 14px; border-radius: 10px; background: var(--surface-2); border: 1px solid var(--border); }
    .status > div { flex: 1; display: flex; flex-direction: column; gap: 4px; }
    .status .dot { margin-top: 6px; flex: none; }
    .counts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 0; }
    @media (max-width: 560px) { .counts { grid-template-columns: 1fr 1fr; } }
    .counts dt { font-size: 12px; color: var(--muted); }
    .counts dd { margin: 4px 0 0; font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; display: flex; flex-direction: column; }
    .counts dd small { font-size: 11.5px; font-weight: 400; color: var(--faint); }
    .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .spacer { flex: 1; }
    .err { color: var(--red); }
    .okmsg { color: var(--green); }
  `,
})
export class CompanionData {
  private api = inject(Api);
  private companion = inject(Companion);
  protected data = signal<CompanionDataSummary | null>(null);
  protected error = signal('');
  protected info = signal('');
  protected busy = signal(false);

  constructor() { this.load(); }

  private load() {
    this.api.companionDataSummary().subscribe({
      next: d => { this.data.set(d); this.error.set(''); },
      error: e => this.error.set(e?.error?.detail ?? 'Impossible de charger vos données du compagnon.'),
    });
  }

  protected async setTracking(on: boolean) {
    await this.act(async () => {
      await this.companion.saveProfile({ tracking_enabled: on });
      this.flash(on ? 'Suivi d\'activité repris.' : 'Suivi d\'activité en pause : plus rien n\'est enregistré.');
    });
  }

  protected async exportAll() {
    await this.act(async () => {
      const data = await this.companion.exportMine();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      Object.assign(document.createElement('a'), { href: url, download: 'mes-donnees-compagnon.json' }).click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  protected async erase(scope: 'all' | 'activity' | 'messages') {
    const label = scope === 'all' ? 'tout ce que le compagnon sait de vous (mémoires, activité, conversation, préférences)'
      : scope === 'activity' ? 'votre historique d\'activité' : 'votre conversation avec le compagnon';
    if (!confirm(`Effacer définitivement ${label} ?`)) return;
    await this.act(async () => { await this.companion.eraseMine(scope); this.flash('Données effacées.'); });
  }

  private async act(action: () => Promise<void>) {
    this.busy.set(true);
    try { await action(); this.load(); }
    catch (e: any) { this.error.set(e?.error?.detail ?? 'Action impossible.'); }
    finally { this.busy.set(false); }
  }

  private flash(msg: string) { this.info.set(msg); setTimeout(() => this.info.set(''), 3000); }
}

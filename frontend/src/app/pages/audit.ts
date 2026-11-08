import { DatePipe, JsonPipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';

import { Api, Session } from '../api';

import { SectionTabs } from '../section-tabs';
const PAGE = 50;

/** Enterprise audit trail: every agent run, document, human decision, admin and governance action —
 *  searchable (free text over actor / action / object / details), filterable, paginated, exportable. */
@Component({
  selector: 'app-audit',
  imports: [SectionTabs, DatePipe, JsonPipe],
  template: `
    <app-section-tabs group="supervision" />
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Journal d'audit</h1>
          <p>Traçabilité complète : exécutions d'agents, documents, décisions humaines, administration et gouvernance IA.</p>
        </div>
        <div class="row gap">
          <button (click)="exportCsv()" [disabled]="!items().length">Exporter (CSV)</button>
          <button (click)="search(0)">Actualiser</button>
        </div>
      </div>

      <div class="card filters">
        <input class="q" placeholder="Rechercher (utilisateur, action, objet, détail…)" [value]="q()" (input)="q.set($any($event.target).value)" (keydown.enter)="search(0)" />
        <select [value]="action()" (change)="action.set($any($event.target).value); search(0)">
          <option value="">Toutes les actions</option>
          @for (a of facets().actions; track a) { <option [value]="a">{{ a }}</option> }
        </select>
        <select [value]="actor()" (change)="actor.set($any($event.target).value); search(0)">
          <option value="">Tous les acteurs</option>
          @for (a of facets().actors; track a) { <option [value]="a">{{ a }}</option> }
        </select>
        <label class="small">Du <input type="date" [value]="since()" (change)="since.set($any($event.target).value); search(0)" /></label>
        <label class="small">au <input type="date" [value]="until()" (change)="until.set($any($event.target).value); search(0)" /></label>
        <button class="primary" (click)="search(0)">Rechercher</button>
        @if (filtered()) { <button class="ghost" (click)="reset()">Effacer</button> }
      </div>

      <div class="card">
        <div class="card-head"><span class="small muted">{{ total() }} événement(s){{ filtered() ? ' correspondant(s)' : '' }}</span>
          @if (pages() > 1) {
            <div class="row gap"><button class="sm" (click)="search(offset() - PAGE)" [disabled]="offset() === 0">←</button>
              <span class="small">page {{ page() }} / {{ pages() }}</span>
              <button class="sm" (click)="search(offset() + PAGE)" [disabled]="page() >= pages()">→</button></div>
          }
        </div>
        @if (error()) { <p class="small warn">{{ error() }}</p> }
        <table>
          <thead><tr><th>Horodatage</th><th>Acteur</th><th>Action</th><th>Objet</th><th>Détails</th></tr></thead>
          <tbody>
            @for (a of items(); track a.id) {
              <tr>
                <td class="mono">{{ a.ts | date: 'dd/MM/yy HH:mm:ss' }}</td>
                <td><button class="linkish" (click)="actor.set(a.actor); search(0)"><span class="pill" [class.accent]="a.actor.startsWith('agent:') || a.actor === 'orchestrator'" [class.amber]="a.action.startsWith('governance.')">{{ a.actor }}</span></button></td>
                <td class="mono"><button class="linkish mono" (click)="action.set(a.action); search(0)">{{ a.action }}</button></td>
                <td class="mono faint">{{ a.entity }} {{ a.entity_id }}</td>
                <td class="small muted details">
                  @if (a.details?.title) { <div>{{ a.details.title }}</div> }
                  @if (a.details?.comment) { <div>Motif : « {{ a.details.comment }} »</div> }
                  @if (a.details?.effect) { <div>Effet : {{ a.details.effect }}</div> }
                  @if (!a.details?.title && !a.details?.comment) { <span class="mono">{{ a.details | json }}</span> }
                </td>
              </tr>
            } @empty { <tr><td colspan="5" class="empty">Aucun événement.</td></tr> }
          </tbody>
        </table>
      </div>
    </div>
  `,
  styles: `
    .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
    .filters .q { flex: 1; min-width: 240px; }
    .filters select { width: auto; max-width: 220px; }
    .filters label { display: inline-flex; align-items: center; gap: 4px; }
    .filters input[type=date] { width: auto; }
    .row { display: flex; align-items: center; } .gap { gap: 8px; }
    .details { max-width: 520px; word-break: break-word; }
    .linkish { background: none; border: none; padding: 0; cursor: pointer; color: inherit; font: inherit; text-align: left; }
    .linkish:hover { text-decoration: underline; }
  `,
})
export class AuditPage {
  protected readonly PAGE = PAGE;
  private api = inject(Api);
  private session = inject(Session);
  protected items = signal<any[]>([]);
  protected total = signal(0);
  protected offset = signal(0);
  protected q = signal('');
  protected action = signal('');
  protected actor = signal('');
  protected since = signal('');
  protected until = signal('');
  protected facets = signal<{ actions: string[]; actors: string[] }>({ actions: [], actors: [] });
  protected error = signal<string | null>(null);
  protected page = computed(() => Math.floor(this.offset() / PAGE) + 1);
  protected pages = computed(() => Math.max(1, Math.ceil(this.total() / PAGE)));
  protected filtered = computed(() => !!(this.q() || this.action() || this.actor() || this.since() || this.until()));

  constructor() {
    effect(() => {
      this.session.username();
      this.api.auditFacets().subscribe({ next: f => this.facets.set(f), error: () => {} });
      this.search(0);
    });
  }

  search(offset: number) {
    this.offset.set(Math.max(0, offset));
    this.api.auditSearch({ q: this.q(), action: this.action(), actor: this.actor(), since: this.since(), until: this.until(), limit: PAGE, offset: this.offset() })
      .subscribe({
        next: r => { this.items.set(r.body ?? []); this.total.set(+(r.headers.get('X-Total-Count') ?? (r.body?.length ?? 0))); this.error.set(null); },
        error: e => this.error.set(e?.status === 403 ? "Vous n'avez pas accès au journal d'audit." : 'Recherche impossible.'),
      });
  }
  reset() { this.q.set(''); this.action.set(''); this.actor.set(''); this.since.set(''); this.until.set(''); this.search(0); }

  exportCsv() {
    const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const rows = [['horodatage', 'acteur', 'action', 'objet', 'id', 'details'],
      ...this.items().map(a => [a.ts, a.actor, a.action, a.entity, a.entity_id, JSON.stringify(a.details ?? {})])];
    const blob = new Blob(['﻿' + rows.map(r => r.map(esc).join(';')).join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    Object.assign(document.createElement('a'), { href: url, download: `audit_${new Date().toISOString().slice(0, 10)}.csv` }).click();
    URL.revokeObjectURL(url);
  }
}

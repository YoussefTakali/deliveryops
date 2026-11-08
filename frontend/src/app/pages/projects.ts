import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api, DeliveryProject, Session } from '../api';

import { SectionTabs } from '../section-tabs';
/** Delivery projects: a manager runs one or more (per client); périmètres and a team sit under each.
 *  Grouped by client so a manager sees their portfolio of engagements. */
@Component({
  selector: 'app-projects',
  imports: [SectionTabs, RouterLink],
  template: `
    <app-section-tabs group="portfolio" />
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Projets</h1>
          <p>Chaque projet est une mission chez un client, pilotée par un manager. Les périmètres et l'équipe se rattachent au projet.</p>
        </div>
      </div>

      @if (canDecide()) {
        <div class="card">
          <div class="card-head"><h2>Nouveau projet</h2></div>
          <div class="form">
            <select [value]="clientId()" (change)="clientId.set($any($event.target).value)">
              <option value="">— Client —</option>
              @for (c of clients(); track c.id) { <option [value]="c.id">{{ c.name }}</option> }
            </select>
            <input placeholder="Nom du projet" [value]="name()" (input)="name.set($any($event.target).value)" />
            <input placeholder="Technologies (séparées par des virgules)" [value]="techs()" (input)="techs.set($any($event.target).value)" />
            <button class="primary" (click)="create()" [disabled]="!clientId() || !name().trim()">Créer</button>
          </div>
          @if (message()) { <p class="small" [style.color]="messageErr() ? 'var(--red)' : 'var(--green)'">{{ message() }}</p> }
        </div>
      }

      @for (grp of grouped(); track grp.client) {
        <h2 class="cli">{{ grp.client }}</h2>
        <div class="grid g3">
          @for (p of grp.items; track p.id) {
            <a class="card proj" [routerLink]="['/pr', p.id]">
              <div class="row between">
                <strong>{{ p.name }}</strong>
                <span class="pill" [class.green]="p.status === 'active'" [class.amber]="p.status !== 'active'">{{ statusLabel(p.status) }}</span>
              </div>
              <div class="small faint">Manager : {{ p.manager || '—' }}</div>
              @if (p.technologies.length) {
                <div class="chips">@for (t of p.technologies; track t) { <span class="chip">{{ t }}</span> }</div>
              }
              <div class="row foot">
                <span class="pill">{{ p.perimeter_count ?? 0 }} périmètre(s)</span>
                <span class="pill">{{ p.member_count ?? 0 }} membre(s)</span>
              </div>
            </a>
          }
        </div>
      } @empty {
        <p class="empty">Aucun projet pour l'instant.@if (canDecide()) {  Créez-en un ci-dessus.}</p>
      }
    </div>
  `,
  styles: `
    .form { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 8px; align-items: center; }
    @media (max-width: 760px) { .form { grid-template-columns: 1fr; } }
    .cli { margin: 18px 0 8px; }
    .proj { display: flex; flex-direction: column; gap: 8px; }
    .row.between { justify-content: space-between; align-items: center; }
    .chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .chip { font-size: 11px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; padding: 1px 8px; }
    .foot { gap: 6px; margin-top: 2px; }
  `,
})
export class ProjectsPage {
  private api = inject(Api);
  private session = inject(Session);
  protected projects = signal<DeliveryProject[]>([]);
  protected clients = signal<any[]>([]);
  protected clientId = signal(''); protected name = signal(''); protected techs = signal('');
  protected message = signal(''); protected messageErr = signal(false);

  protected canDecide = computed(() => !!this.session.me()?.can_decide);
  protected grouped = computed(() => {
    const by: Record<string, DeliveryProject[]> = {};
    for (const p of this.projects()) (by[p.client] ??= []).push(p);
    return Object.keys(by).sort().map(client => ({ client, items: by[client] }));
  });

  constructor() {
    this.load();
    this.api.clients().subscribe({ next: c => this.clients.set(c), error: () => this.clients.set([]) });
  }

  load() { this.api.projects().subscribe({ next: p => this.projects.set(p), error: () => this.projects.set([]) }); }
  statusLabel(s: string) { return s === 'active' ? 'Actif' : s === 'done' ? 'Terminé' : 'Archivé'; }

  create() {
    const technologies = this.techs().split(',').map(t => t.trim()).filter(Boolean);
    this.api.createProject({ client_id: this.clientId(), name: this.name().trim(), technologies }).subscribe({
      next: () => { this.name.set(''); this.techs.set(''); this.message.set('Projet créé.'); this.messageErr.set(false); this.load(); },
      error: e => { this.message.set(e.error?.detail ?? 'Erreur'); this.messageErr.set(true); },
    });
  }
}

import { Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { Api, Session } from '../api';

@Component({
  selector: 'app-onboarding',
  template: `
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>Intégrer un nouveau périmètre</h1>
          <p>Trois étapes : créer le périmètre, charger la documentation et les données, lancer l'analyse d'onboarding par les agents.</p>
        </div>
      </div>

      <ol class="steps">
        <li class="on">1. Périmètre</li><li>2. Documentation & données</li><li>3. Analyse d'onboarding</li>
      </ol>

      <div class="card stack">
        <div>
          <label for="client">Client</label>
          <select id="client" [value]="clientId()" (change)="clientId.set($any($event.target).value)">
            @for (c of clients(); track c.id) { <option [value]="c.id">{{ c.name }}</option> }
            @if (isDirector()) { <option value="__new">+ Nouveau client…</option> }
          </select>
        </div>
        @if (clientId() === '__new') {
          <div class="grid g2">
            <div><label for="cname">Nom du client</label><input id="cname" [value]="clientName()" (input)="clientName.set($any($event.target).value)" /></div>
            <div><label for="sector">Secteur</label><input id="sector" [value]="sector()" (input)="sector.set($any($event.target).value)" placeholder="Assurance, Banque, Industrie…" /></div>
          </div>
        }
        @if (clientId() && clientId() !== '__new') {
          <div>
            <label for="proj">Projet</label>
            <select id="proj" [value]="projectId()" (change)="projectId.set($any($event.target).value)">
              <option value="">— Sans projet —</option>
              @for (p of clientProjects(); track p.id) { <option [value]="p.id">{{ p.name }}</option> }
            </select>
          </div>
        }
        <div><label for="pname">Nom du périmètre</label><input id="pname" [value]="name()" (input)="name.set($any($event.target).value)" placeholder="Ex. SI Contrats Santé" /></div>
        <div><label for="desc">Description</label><textarea id="desc" rows="3" [value]="description()" (input)="description.set($any($event.target).value)" placeholder="Domaine fonctionnel, volumétrie, contexte de reprise…"></textarea></div>
        <div class="row">
          <button class="primary" [disabled]="!valid() || busy()" (click)="create()">@if (busy()) { <span class="spin"></span> } Créer et continuer</button>
          @if (error()) { <span class="small" style="color: var(--red)">{{ error() }}</span> }
        </div>
      </div>

      <div class="card next">
        <h3>Et ensuite</h3>
        <ul class="small muted">
          <li><strong>Paramétrage</strong> : import CSV/Excel des applications, dépendances, équipe, tickets, temps et backlog, ou connexion Jira / ServiceNow.</li>
          <li><strong>Connaissance</strong> : dépôt du dossier de transfert, DAT, DEX et contrat ; extraction des règles contractuelles à valider.</li>
          <li><strong>Analyse d'onboarding</strong> : cartographie, applications critiques, risques sourcés, plan sur 4 semaines et synthèse exécutive exportable.</li>
        </ul>
      </div>
    </div>
  `,
  styles: `
    .narrow { max-width: 820px; }
    .steps { display: flex; gap: 8px; list-style: none; padding: 0; margin: 0 0 16px; }
    .steps li { flex: 1; padding: 8px 12px; border-radius: 8px; background: var(--grey-soft); color: var(--muted); font-size: 13px; }
    .steps li.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
    .next { margin-top: 16px; }
    .next ul { padding-left: 18px; margin: 8px 0 0; display: flex; flex-direction: column; gap: 6px; }
  `,
})
export class OnboardingPage {
  private api = inject(Api);
  private router = inject(Router);
  private session = inject(Session);
  protected clients = signal<any[]>([]);
  protected clientId = signal('');
  protected clientName = signal('');
  protected sector = signal('');
  protected name = signal('');
  protected description = signal('');
  protected projects = signal<any[]>([]);
  protected projectId = signal('');
  protected busy = signal(false);
  protected error = signal('');
  protected clientProjects = computed(() => this.projects().filter(p => p.client_id === this.clientId() && p.status === 'active'));
  protected isDirector = computed(() => this.session.me()?.role === 'director');
  protected valid = computed(() => !!this.name().trim() && (this.clientId() === '__new' ? !!this.clientName().trim() : !!this.clientId()));

  constructor() {
    this.api.clients().subscribe(c => { this.clients.set(c); if (c.length) this.clientId.set(c[0].id); });
    this.api.projects().subscribe({ next: p => this.projects.set(p), error: () => this.projects.set([]) });
  }

  create() {
    this.busy.set(true);
    this.error.set('');
    const fail = (e: any) => { this.busy.set(false); this.error.set(e.error?.detail ?? 'Erreur'); };
    const createPerimeter = (clientId: string) =>
      this.api.createPerimeter(clientId, this.name(), this.description(), this.projectId() || null).subscribe({
        next: p => { this.busy.set(false); this.session.perimetersVersion.update(v => v + 1); this.router.navigate(['/p', p.id, 'settings']); },
        error: fail,
      });
    if (this.clientId() === '__new') {
      this.api.createClient(this.clientName(), this.sector()).subscribe({ next: c => createPerimeter(c.id), error: fail });
    } else {
      createPerimeter(this.clientId());
    }
  }
}

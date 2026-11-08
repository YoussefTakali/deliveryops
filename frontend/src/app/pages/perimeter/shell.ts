import { Component, effect, inject, input, signal } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Api, Session } from '../../api';

@Component({
  selector: 'app-perimeter-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <div class="page">
      @if (perimeter(); as p) {
        <div class="page-head">
          <div>
            <span class="faint small">{{ p.client }}</span>
            <h1>{{ p.name }}</h1>
            <p>{{ p.description }} · {{ p.applications.length }} applications · {{ p.team.length }} personnes · {{ p.documents.length }} documents</p>
          </div>
        </div>
        <nav class="tabs">
          <a [routerLink]="['/p', p.id]" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Pilotage</a>
          <a [routerLink]="['/p', p.id, 'copilot']" routerLinkActive="active">Copilot</a>
          <a [routerLink]="['/p', p.id, 'knowledge']" routerLinkActive="active">Connaissance & onboarding</a>
          <a [routerLink]="['/p', p.id, 'meetings']" routerLinkActive="active">Réunions</a>
          <a [routerLink]="['/p', p.id, 'equipe']" routerLinkActive="active">Équipe</a>
          <a [routerLink]="['/p', p.id, 'livrables']" routerLinkActive="active">Livrables</a>
          <a [routerLink]="['/p', p.id, 'billing']" routerLinkActive="active">Facturation</a>
          <a [routerLink]="['/p', p.id, 'capacity']" routerLinkActive="active">Capacité</a>
          <a [routerLink]="['/p', p.id, 'incidents']" routerLinkActive="active">Incidents & chiffrage</a>
          <a [routerLink]="['/p', p.id, 'settings']" routerLinkActive="active">Paramétrage</a>
        </nav>
        <router-outlet />
      } @else if (notFound()) {
        <div class="empty card">Périmètre introuvable ou non autorisé pour ce profil.</div>
      } @else {
        <div class="empty"><span class="spin"></span></div>
      }
    </div>
  `,
})
export class PerimeterShell {
  private api = inject(Api);
  private session = inject(Session);
  perimeterId = input.required<string>();
  protected perimeter = signal<any>(null);
  protected notFound = signal(false);

  constructor() {
    effect(() => {
      this.session.username();
      const id = this.perimeterId();
      this.perimeter.set(null);
      this.notFound.set(false);
      this.api.perimeter(id).subscribe({ next: p => this.perimeter.set(p), error: () => this.notFound.set(true) });
    });
  }
}

/** Starts an orchestrated workflow and opens the run page. */
export function launch(api: Api, router: Router, perimeterId: string, workflow: string, params: any = {}, busy?: ReturnType<typeof signal<string>>) {
  busy?.set(workflow);
  api.startRun(perimeterId, workflow, params).subscribe({
    next: run => { busy?.set(''); router.navigate(['/runs', run.id]); },
    error: () => busy?.set(''),
  });
}

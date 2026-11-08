import { DatePipe } from '@angular/common';
import { Component, effect, inject, input, signal } from '@angular/core';
import { Router } from '@angular/router';

import { Api } from '../../api';
import { launch } from './shell';

@Component({
  selector: 'app-incidents',
  imports: [DatePipe],
  template: `
    <div class="grid g2">
      <div class="card">
        <div class="card-head"><div><h2>Chiffrage d'une demande d'évolution</h2><p>L'agent Estimation s'appuie sur les heures réellement consommées par les évolutions similaires et le catalogue d'unités d'œuvre.</p></div></div>
        <textarea rows="4" [value]="request()" (input)="request.set($any($event.target).value)" placeholder="Décrire la demande du client…"></textarea>
        <div class="row" style="margin-top: 8px">
          @for (s of samples; track s) { <button class="sm ghost" (click)="request.set(s)">{{ s }}</button> }
          <span class="spacer"></span>
          <button class="primary" [disabled]="!request().trim() || !!busy()" (click)="estimate()">@if (busy() === 'estimation') { <span class="spin"></span> } Chiffrer</button>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><div><h2>Gestion des problèmes</h2><p>Détection des incidents récurrents, doublons, résolutions connues et demandes hors contrat. Les problèmes validés sont créés automatiquement.</p></div></div>
        <button class="primary" [disabled]="!!busy()" (click)="analyse()">@if (busy() === 'governance') { <span class="spin"></span> } Analyser incidents et dérives</button>
      </div>
    </div>

    <div class="card" style="margin-top: 16px">
      <div class="card-head"><h2>Tickets ouverts ({{ tickets().length }})</h2></div>
      <table>
        <tr><th>Réf.</th><th>Type</th><th>Prio.</th><th>Titre</th><th>Statut</th><th>Ouvert le</th></tr>
        @for (t of tickets(); track t.id) {
          <tr><td class="mono">{{ t.ref }}</td><td class="small muted">{{ t.kind }}</td>
            <td><span class="pill" [class.red]="t.priority === 'P1'" [class.amber]="t.priority === 'P2'">{{ t.priority }}</span></td>
            <td>{{ t.title }}</td><td class="small">{{ t.status }}</td><td class="small mono faint">{{ t.opened_at | date: 'dd/MM/yy HH:mm' }}</td></tr>
        } @empty { <tr><td class="empty">Aucun ticket ouvert.</td></tr> }
      </table>
    </div>
  `,
})
export class IncidentsTab {
  private api = inject(Api);
  private router = inject(Router);
  perimeterId = input.required<string>();
  protected tickets = signal<any[]>([]);
  protected request = signal('');
  protected busy = signal('');
  protected samples = ['Export CSV des sinistres en attente de paiement', 'Ajout d’un contrôle de doublon sur les rapports d’expertise'];

  constructor() {
    effect(() => {
      const id = this.perimeterId();
      this.api.tickets(id).subscribe(t => this.tickets.set(t.filter(x => x.status === 'open' || x.status === 'in_progress')));
    });
  }

  estimate() { launch(this.api, this.router, this.perimeterId(), 'estimation', { request: this.request() }, this.busy); }
  analyse() { launch(this.api, this.router, this.perimeterId(), 'governance', {}, this.busy); }
}

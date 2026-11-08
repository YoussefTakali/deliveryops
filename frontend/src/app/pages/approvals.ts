import { Component, computed, effect, inject, input, signal } from '@angular/core';

import { AGENT_LABELS, Api, Recommendation, Session } from '../api';
import { RecCard } from '../shared';

@Component({
  selector: 'app-approvals',
  imports: [RecCard],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>File de validation</h1>
          <p>Toutes les recommandations des agents passent par une décision humaine tracée avant d'être appliquées.</p>
        </div>
      </div>
      <div class="row filters">
        @for (s of statuses; track s.id) {
          <button class="sm" [class.primary]="status() === s.id" (click)="status.set(s.id)">{{ s.label }}</button>
        }
        <span class="spacer"></span>
        <select [value]="agent()" (change)="agent.set($any($event.target).value)" style="width: 220px">
          <option value="">Tous les agents</option>
          @for (a of agents; track a[0]) { <option [value]="a[0]">{{ a[1] }}</option> }
        </select>
        <select [value]="impact()" (change)="impact.set($any($event.target).value)" style="width: 160px">
          <option value="">Tous impacts</option><option value="high">Impact fort</option><option value="medium">Impact moyen</option><option value="low">Impact faible</option>
        </select>
      </div>
      <p class="small muted count">{{ filtered().length }} élément(s)</p>
      <div class="stack">
        @for (r of filtered(); track r.id) {
          <app-rec [rec]="r" [showPerimeter]="true" (decided)="load()" />
        } @empty { <div class="empty card">Rien à afficher pour ces filtres.</div> }
      </div>
    </div>
  `,
  styles: `.filters { margin-bottom: 8px; } .count { margin-bottom: 10px; }`,
})
export class ApprovalsPage {
  private api = inject(Api);
  private session = inject(Session);
  run = input<string>();  // ?run= from Teams notifications
  protected items = signal<Recommendation[]>([]);
  protected status = signal('pending');
  protected agent = signal('');
  protected impact = signal('');
  protected statuses = [{ id: 'pending', label: 'À décider' }, { id: 'approved', label: 'Validées' }, { id: 'rejected', label: 'Rejetées' }, { id: 'info', label: 'Informations' }];
  protected agents = Object.entries(AGENT_LABELS);
  protected filtered = computed(() => this.items().filter(r => (!this.agent() || r.agent === this.agent()) && (!this.impact() || r.impact === this.impact())));

  constructor() {
    effect(() => {
      this.session.username();
      this.status();
      this.load();
    });
  }

  load() {
    this.api.recommendations({ status: this.status(), run_id: this.run() }).subscribe(r => this.items.set(r));
  }
}

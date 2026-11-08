import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ProjectStore } from './store';

/** The project's périmètres, with each one's service health straight from the portfolio. */
@Component({
  selector: 'app-project-perimeters',
  imports: [RouterLink],
  template: `
    <div class="head">
      <p class="small muted">Chaque périmètre est un lot applicatif du client. L'équipe du projet peut travailler sur tous ; ses livrables sont ceux choisis pour le projet.</p>
      @if (s.canDecide()) { <a class="btn primary sm" routerLink="/onboarding">+ Nouveau périmètre</a> }
    </div>
    <div class="grid">
      @for (per of s.perimeters(); track per.id) {
        <a class="card perim" [routerLink]="['/p', per.id]">
          <div class="top"><span class="dot" [class]="per.overall_status"></span><strong>{{ per.name }}</strong></div>
          <p class="small muted">{{ per.description || '—' }}</p>
          <div class="score">
            @for (k of per.scorecard.slice(0, 3); track k.kpi) {
              <div class="k"><span class="kv" [class]="k.status">{{ k.value ?? '—' }}{{ k.unit === '%' ? '%' : '' }}</span><span class="kl">{{ k.kpi }}</span></div>
            }
          </div>
          <div class="foot small">
            <span>{{ per.applications }} application(s)</span>
            <span>{{ per.backlog_open }} en backlog</span>
            @if (per.pending_decisions) { <span class="warn">{{ per.pending_decisions }} décision(s) en attente</span> }
          </div>
        </a>
      } @empty {
        <div class="card empty-card">
          <p>Aucun périmètre dans ce projet.</p>
          @if (s.canDecide()) { <a class="btn primary" routerLink="/onboarding">Créer le premier périmètre</a> }
        </div>
      }
    </div>
  `,
  styles: `
    .head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
    .head p { margin: 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
    .perim { display: flex; flex-direction: column; gap: 8px; color: inherit; text-decoration: none; transition: border-color .15s; }
    .perim:hover { border-color: var(--accent); }
    .perim p { margin: 0; }
    .top { display: flex; align-items: center; gap: 8px; }
    .score { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .k { display: flex; flex-direction: column; padding: 8px; border-radius: 8px; background: var(--surface-2); }
    .kv { font-weight: 700; font-size: 15px; } .kv.red { color: var(--red); } .kv.amber { color: var(--amber); } .kv.green { color: var(--green); }
    .kl { font-size: 11px; color: var(--muted); }
    .foot { display: flex; gap: 12px; flex-wrap: wrap; color: var(--muted); }
    .warn { color: var(--amber); }
    .empty-card { grid-column: 1 / -1; text-align: center; display: flex; flex-direction: column; align-items: center; gap: 10px; }
  `,
})
export class ProjectPerimeters {
  protected s = inject(ProjectStore);
}

import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ProjectStore } from './store';

interface NextStep { title: string; detail: string; link: string; cta: string; done: boolean; }

/** Vue d'ensemble: the health of each périmètre, what to do next (computed from the project's real
 *  state, in the lifecycle order), the team at a glance and the technologies that drive staffing. */
@Component({
  selector: 'app-project-overview',
  imports: [RouterLink],
  template: `
    @if (s.project(); as p) {
      <div class="card">
        <div class="card-head"><div><h2>Prochaines étapes</h2>
          <p class="small muted">Le cycle d'un projet, calculé à partir de ce qui existe réellement. Le détail est dans chaque onglet.</p></div>
          <span class="count">{{ doneCount() }} / {{ steps().length }}</span></div>
        <div class="bar"><span [style.width.%]="100 * doneCount() / (steps().length || 1)"></span></div>
        <ol class="steps">
          @for (st of steps(); track st.title; let i = $index) {
            <li [class.done]="st.done">
              <span class="check">@if (st.done) { <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 12.5 4 4 8-9"/></svg> } @else { {{ i + 1 }} }</span>
              <div class="grow"><strong>{{ st.title }}</strong><div class="small faint">{{ st.detail }}</div></div>
              @if (!st.done) { <a class="btn sm" [routerLink]="st.link">{{ st.cta }}</a> }
            </li>
          }
        </ol>
      </div>
    }
  `,
  styles: `
    .card-head p { margin: 4px 0 0; }
    .count { font-size: 13px; font-weight: 700; color: var(--accent); }
    .bar { height: 6px; border-radius: 999px; background: var(--surface-2); overflow: hidden; margin: 12px 0 6px; }
    .bar span { display: block; height: 100%; background: var(--green); border-radius: 999px; transition: width .3s; }
    .grow { flex: 1; min-width: 0; }
    .steps { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; }
    .steps li { display: flex; align-items: center; gap: 12px; padding: 14px 0; border-top: 1px solid var(--border); }
    .steps li:first-child { border-top: none; }
    .steps li.done strong { color: var(--muted); }
    .check { width: 26px; height: 26px; border-radius: 50%; border: 2px solid var(--border); flex: none; display: flex; align-items: center; justify-content: center;
             font-size: 12px; font-weight: 700; color: var(--muted); }
    li.done .check { background: var(--green); border-color: var(--green); }
    .check svg { width: 14px; height: 14px; fill: none; stroke: #fff; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
  `,
})
export class ProjectOverview {
  protected s = inject(ProjectStore);
  /** The project lifecycle, computed from what actually exists. */
  protected steps = computed<NextStep[]>(() => {
    const p = this.s.project();
    if (!p) return [];
    const perims = p.perimeters?.length ?? 0;
    const team = p.members?.length ?? 0;
    const base = `/pr/${this.s.id()}`;
    return [
      { title: 'Rattacher un périmètre', detail: 'Au moins une application / un lot du client sous ce projet.', link: '/onboarding', cta: 'Créer', done: perims > 0 },
      { title: 'Renseigner les technologies', detail: 'Elles orientent le staffing par compétences.', link: `${base}/equipe`, cta: 'Renseigner', done: (p.technologies?.length ?? 0) > 0 },
      { title: "Constituer l'équipe", detail: 'Utilisateurs de la plateforme ou personnes saisies manuellement.', link: `${base}/equipe`, cta: 'Ajouter', done: team > 1 },
      { title: 'Choisir les livrables du projet', detail: 'Dans le catalogue de l’entreprise, ou propres au projet.', link: `${base}/livrables`, cta: 'Choisir', done: (this.s.livrables()?.catalog ?? []).some(c => c.enabled) && this.s.activeLivrables() > 0 },
      { title: 'Générer le plan de charge', detail: 'Un document pour tout le projet, à partir des matrices de couverture.', link: `${base}/plan`, cta: 'Générer', done: !!this.s.latestPlan() },
    ];
  });
  protected doneCount = computed(() => this.steps().filter(s => s.done).length);
}

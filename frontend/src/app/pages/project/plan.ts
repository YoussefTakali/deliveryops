import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';

import { Api } from '../../api';
import { Choices, seedRecommended } from '../../choices';
import { DebugAi } from '../../debug-ai';
import { errorText } from '../../http-error';
import { ProjectStore } from './store';

/** Plan de charge — ONE document for the whole project: it aggregates the matrices de couverture of the
 *  project's périmètres and schedules the charge on the project team (days & costs). */
@Component({
  selector: 'app-project-plan',
  imports: [DatePipe, DecimalPipe, Choices, DebugAi],
  template: `
    @if (s.project(); as p) {
      <div class="card">
        <div class="card-head">
          <div><h2>Plan de charge du projet</h2>
            <p class="small muted">Agrège les matrices de couverture de chaque périmètre et planifie la charge semaine par semaine sur l'équipe du projet, avec les coûts.</p></div>
          @if (s.canDecide() && !q() && !draft() && !busy()) { <button class="primary" (click)="start()">{{ s.latestPlan() ? 'Nouveau plan' : 'Générer le plan de charge' }}</button> }
        </div>

        @if (busy()) { <p class="small muted"><span class="spin"></span> Calcul des charges et du planning…</p> }

        @if (q(); as question) {
          <div class="qwrap">
            <p class="small muted">Question {{ step() }} — l'IA a besoin de cette information</p>
            <app-choices [q]="question" [value]="answers()[question.question] ?? ''" (valueChange)="setAnswer(question.question, $event)" (enter)="next()" />
            <div class="row end gap">
              <button (click)="cancel()">Annuler</button>
              <button class="primary" (click)="next()" [disabled]="!(answers()[question.question] ?? '').trim()">Continuer</button>
            </div>
          </div>
        }

        @if (draft(); as d) {
          <div class="summary">
            <div class="stat"><strong>{{ d.totals?.days | number: '1.0-1' }}</strong><span>jours</span></div>
            <div class="stat"><strong>{{ d.totals?.cost | number: '1.0-0' }} €</strong><span>coût estimé</span></div>
            <div class="stat"><strong>{{ d.params?.weeks }}</strong><span>semaines</span></div>
            <div class="stat"><strong>{{ d.members?.length }}</strong><span>ressources</span></div>
          </div>
          @for (n of d.notes ?? []; track n) { <p class="small warn">{{ n }}</p> }
          <table>
            <tr><th>Ressource</th><th>Profil</th><th class="num">TJM</th><th class="num">Jours</th><th class="num">Coût</th></tr>
            @for (m of d.members ?? []; track m.name) {
              <tr><td>{{ m.name }}</td><td class="small">{{ m.profile }}</td><td class="num">{{ m.rate | number: '1.0-0' }} €</td>
                <td class="num">{{ m.total_days | number: '1.0-1' }}</td><td class="num">{{ m.cost | number: '1.0-0' }} €</td></tr>
            }
          </table>
          <div class="row end gap">
            <button (click)="cancel()">Annuler</button>
            <button class="primary" (click)="download()" [disabled]="busy()">Générer le fichier (.xlsx)</button>
          </div>
        }
        @if (msg()) { <p class="small ok">{{ msg() }}</p> }
        <app-debug-ai [context]="'Plan de charge du projet « ' + p.name + ' » (agrège les matrices de couverture de ses périmètres)'" [error]="error()" />
      </div>

      <div class="card">
        <div class="card-head"><h2>Plans générés</h2></div>
        <table>
          <tr><th>Fichier</th><th class="num">Charge</th><th class="num">Coût</th><th>Par</th><th>Le</th><th></th></tr>
          @for (pd of s.plans(); track pd.id) {
            <tr>
              <td>{{ pd.filename }}</td>
              <td class="num">{{ pd.totals?.days ?? '—' }} j</td>
              <td class="num">{{ pd.totals?.cost != null ? ((pd.totals.cost | number: '1.0-0') + ' €') : '—' }}</td>
              <td class="small">{{ s.name(pd.created_by) }}</td>
              <td class="small faint">{{ pd.created_at | date: 'dd/MM/yy HH:mm' }}</td>
              <td><button class="sm ghost" (click)="downloadExisting(pd)">Télécharger</button></td>
            </tr>
          } @empty { <tr><td colspan="6" class="empty">Aucun plan de charge généré pour ce projet.</td></tr> }
        </table>
      </div>
    }
  `,
  styles: `
    .card { margin-bottom: 16px; }
    .card-head p { margin: 4px 0 0; }
    .qwrap { display: flex; flex-direction: column; gap: 10px; max-width: 640px; }
    .row { display: flex; align-items: center; } .end { justify-content: flex-end; } .gap { gap: 8px; }
    .summary { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
    .stat { flex: 1; min-width: 120px; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 12px; border-radius: 10px; background: var(--surface-2); }
    .stat strong { font-size: 20px; } .stat span { font-size: 12px; color: var(--muted); }
    .warn { color: var(--amber); } .ok { color: var(--green); }
  `,
})
export class ProjectPlan {
  protected s = inject(ProjectStore);
  private api = inject(Api);
  protected q = signal<{ question: string; options: string[]; recommended?: string } | null>(null);
  protected answers = signal<Record<string, string>>({});
  protected step = signal(0);
  protected draft = signal<any | null>(null);
  protected busy = signal(false);
  protected msg = signal('');
  protected error = signal<string | null>(null);

  protected start() { this.answers.set({}); this.draft.set(null); this.step.set(0); this.prepare(); }
  protected cancel() { this.q.set(null); this.draft.set(null); this.error.set(null); }
  protected setAnswer(q: string, v: string) { this.answers.update(a => ({ ...a, [q]: v })); }

  private prepare() {
    this.busy.set(true); this.error.set(null); this.msg.set('');
    this.api.planPrepare(this.s.id(), { answers: this.answers(), technologies: this.s.project()?.technologies ?? [] }).subscribe({
      next: r => {
        this.busy.set(false);
        if (r.status === 'ready') { this.q.set(null); this.draft.set(r.draft); return; }
        const next = r.questions[0] ?? null;
        this.q.set(next);
        this.step.update(n => n + 1);
        if (next) this.answers.update(a => seedRecommended([next], a));   // recommended answer pre-selected
      },
      error: async e => { this.busy.set(false); this.q.set(null); this.error.set(await errorText(e, 'Préparation impossible.')); },
    });
  }
  protected next() { const q = this.q(); if (q && (this.answers()[q.question] ?? '').trim()) this.prepare(); }

  protected download() {
    this.busy.set(true);
    this.api.planGenerate(this.s.id(), this.draft()).subscribe({
      next: res => { this.busy.set(false); this.save(res); this.draft.set(null); this.msg.set('Plan de charge généré et enregistré sur le projet.'); this.s.reloadPlans(); },
      error: async e => { this.busy.set(false); this.error.set(await errorText(e, 'Erreur à la génération.')); },
    });
  }
  protected downloadExisting(pd: any) {
    this.api.projectDeliverableFile(this.s.id(), pd.id).subscribe({ next: res => this.save(res), error: async e => this.error.set(await errorText(e, 'Téléchargement impossible.')) });
  }
  private save(res: any) {
    const m = /filename="?([^"]+)"?/.exec(res.headers?.get('Content-Disposition') || '');
    const url = URL.createObjectURL(res.body as Blob);
    Object.assign(document.createElement('a'), { href: url, download: m ? m[1] : 'plan-de-charge.xlsx' }).click();
    URL.revokeObjectURL(url);
  }
}

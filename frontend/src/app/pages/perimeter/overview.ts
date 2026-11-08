import { DatePipe } from '@angular/common';
import { Component, effect, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { Api, Recommendation, Run, STATUS_LABEL, Session } from '../../api';
import { Chart, RecCard } from '../../shared';
import { launch } from './shell';

@Component({
  selector: 'app-overview',
  imports: [DatePipe, RouterLink, Chart, RecCard],
  template: `
    <div class="stack">
      @if (lifecycle(); as lc) {
        <div class="card lifecycle">
          <div class="card-head"><div><h2>Cycle de vie du périmètre</h2><p>{{ lc.done }}/{{ lc.total }} étapes — de la réunion de cadrage au suivi de facturation</p></div></div>
          <ol class="stages">
            @for (s of lc.stages; track s.key) {
              <li [class.done]="s.done" [class.current]="lc.current === s.key">
                <span class="mark">@if (s.done) { ✓ } @else { {{ $index + 1 }} }</span>
                <a [routerLink]="s.link"><strong>{{ s.label }}</strong><span class="small muted">{{ s.detail }}</span></a>
              </li>
            }
          </ol>
        </div>
      }
      <div class="card ask">
        <h2>Interroger les agents</h2>
        <p class="small muted">L'orchestrateur choisit les agents utiles, croise leurs analyses avec la documentation et cite ses sources. Mode lecture seule : rien n'est ajouté à la file de validation.</p>
        <form class="row" (submit)="$event.preventDefault(); ask()">
          <input [value]="question()" (input)="question.set($any($event.target).value)" placeholder="Ex. Pourquoi le respect des SLA baisse-t-il et quelle charge prévoir le mois prochain ?" />
          <button class="primary" [disabled]="!question().trim() || busy()">@if (busy() === 'ask') { <span class="spin"></span> } Demander</button>
        </form>
        <div class="row suggestions">
          @for (s of suggestions; track s) { <button class="sm ghost" type="button" (click)="question.set(s)">{{ s }}</button> }
        </div>
      </div>

      <!-- onboarding and service-health workflows live in their own tabs (Connaissance, Incidents) -->
      <div>
        @for (w of workflows; track w.id) {
          <div class="card wf wf-one">
            <div class="grow"><h3>{{ w.label }}</h3>
              <p class="small muted">{{ w.description }}</p>
              <div class="small faint">{{ w.agents }}</div></div>
            <div class="row">
              <button [class.primary]="w.primary" [disabled]="!!busy()" (click)="start(w.id)">@if (busy() === w.id) { <span class="spin"></span> } Lancer</button>
              @if (w.id === 'copil') { <button class="ghost" (click)="exportDeck()">Télécharger le support (PPTX)</button> }
            </div>
          </div>
        }
      </div>

      @if (kpis(); as k) {
        <div class="grid g5">
          @for (s of k.scorecard; track s.kpi) {
            <div class="card kpi">
              <span class="l"><span class="dot" [class]="s.status"></span> {{ s.kpi }}</span>
              <span class="v">{{ s.value ?? '—' }}{{ s.unit }}</span>
              <span class="small faint">cible {{ s.target }}{{ s.unit }}</span>
            </div>
          }
        </div>
        <div class="grid g2">
          <div class="card">
            <div class="card-head"><div><h2>Tendance incidents et SLA</h2><p>6 derniers mois — barres : incidents ouverts, ligne : respect SLA (%)</p></div></div>
            <app-chart [labels]="months()" [values]="incidentSeries()" [line]="slaSeries()" ariaLabel="Incidents et SLA par mois" />
          </div>
          <div class="card">
            <div class="card-head"><div><h2>SLA du dernier mois</h2><p>Incidents résolus par priorité, contre la cible contractuelle</p></div></div>
            <table>
              <tr><th>Priorité</th><th class="num">Résolus</th><th class="num">Dans les délais</th><th class="num">Taux</th><th class="num">Cible</th><th></th></tr>
              @for (e of slaRows(); track e.p) {
                <tr><td>{{ e.p }}</td><td class="num">{{ e.resolved }}</td><td class="num">{{ e.within_sla }}</td><td class="num">{{ e.pct }} %</td><td class="num">{{ e.target_pct }} %</td>
                  <td><span class="pill" [class.green]="e.met" [class.red]="!e.met">{{ e.met ? 'Tenu' : 'Non tenu' }}</span></td></tr>
              }
            </table>
            <div class="row small muted" style="margin-top: 12px">
              <span>Backlog > 30 j : <strong>{{ k.backlog_over_30d }}</strong></span>
              <span>· Âge moyen : <strong>{{ k.backlog_avg_age_d }} j</strong></span>
              <span>· Top applications : @for (a of k.top_applications; track a.application) { <span class="pill">{{ a.application }} ({{ a.incidents }})</span> }</span>
            </div>
          </div>
        </div>
      }

      <div class="grid g2">
        <div class="card">
          <div class="card-head"><h2>Décisions en attente</h2><a routerLink="/approvals" class="small">Toute la file</a></div>
          <div class="stack">
            @for (r of pending(); track r.id) { <app-rec [rec]="r" (decided)="loadRecs()" /> }
            @empty { <div class="empty">Aucune décision en attente.</div> }
          </div>
        </div>
        <div class="card">
          <div class="card-head"><h2>Exécutions récentes</h2></div>
          <table>
            @for (r of runs(); track r.id) {
              <tr>
                <td><a [routerLink]="['/runs', r.id]">{{ wfLabel[r.workflow] ?? r.workflow }}</a>
                  @if (r.params?.question) { <div class="small faint">« {{ r.params.question }} »</div> }</td>
                <td class="small muted">{{ r.requested_by }}</td>
                <td class="small mono faint">{{ r.started_at | date: 'dd/MM HH:mm' }}</td>
                <td><span class="pill" [class.green]="r.status === 'completed'" [class.amber]="r.status === 'awaiting_approval'" [class.red]="r.status === 'failed'">{{ statusLabel[r.status] ?? r.status }}</span></td>
              </tr>
            } @empty { <tr><td class="empty">Aucune exécution.</td></tr> }
          </table>
        </div>
      </div>
    </div>
  `,
  styles: `
    .wf-one { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; } .wf-one .grow { flex: 1; min-width: 260px; } .wf-one p, .wf-one h3 { margin: 0 0 4px; }
    .lifecycle .stages { list-style: none; margin: 8px 0 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px; }
    .lifecycle li { display: flex; gap: 10px; align-items: flex-start; padding: 10px; border: 1px solid var(--border); border-radius: 10px; }
    .lifecycle li.done { border-color: var(--green, #2e9d5b); } .lifecycle li.current { border-color: var(--accent); background: var(--accent-soft); }
    .lifecycle .mark { width: 22px; height: 22px; border-radius: 50%; border: 1px solid var(--border); display: flex; align-items: center; justify-content: center; font-size: 12px; flex: none; }
    .lifecycle li.done .mark { background: var(--green, #2e9d5b); color: #fff; border-color: transparent; }
    .lifecycle li a { display: flex; flex-direction: column; gap: 2px; text-decoration: none; color: var(--text); font-size: 13px; }

    .ask form input { flex: 1; min-width: 240px; }
    .ask h2 { margin-bottom: 2px; }
    .ask form { margin: 12px 0 6px; }
    .suggestions button { font-weight: 400; }
    .wf { display: flex; flex-direction: column; gap: 6px; }
    .wf .row { margin-top: 6px; }
  `,
})
export class OverviewTab {
  private api = inject(Api);
  private router = inject(Router);
  private session = inject(Session);
  perimeterId = input.required<string>();
  protected lifecycle = signal<any>(null);
  private readonly _lifecycle = effect(() => { const id = this.perimeterId(); this.api.lifecycle(id).subscribe({ next: r => this.lifecycle.set(r), error: () => this.lifecycle.set(null) }); });

  protected kpis = signal<any>(null);
  protected runs = signal<Run[]>([]);
  protected pending = signal<Recommendation[]>([]);
  protected question = signal('');
  protected busy = signal('');
  protected statusLabel = STATUS_LABEL;
  protected wfLabel: Partial<Record<string, string>> = { onboarding: 'Onboarding', copil: 'Préparation COPIL', billing: 'Pré-facturation', capacity: 'Revue de capacité', governance: 'Santé du service', meeting_minutes: 'Compte-rendu', call: 'Compte-rendu', estimation: 'Chiffrage', ask: 'Question' };
  protected suggestions = ['Quels sont les principaux risques du périmètre ?', 'Pourquoi le respect des SLA baisse-t-il ?', 'Y a-t-il des anomalies de facturation ce mois-ci ?', "L'équipe peut-elle absorber le backlog ?"];
  protected workflows = [
    { id: 'copil', label: 'Préparer le COPIL', description: 'Scorecard, dérives, pré-facture, capacité et plan d’action consolidés en un support.', agents: 'Incident → Capacité → Billing → Gouvernance', primary: true },
  ];

  constructor() {
    effect(() => {
      this.session.username();
      const id = this.perimeterId();
      this.api.kpis(id).subscribe(k => this.kpis.set(k));
      this.api.runs(id).subscribe(r => this.runs.set(r.slice(0, 8)));
      this.loadRecs();
    });
  }

  loadRecs() {
    this.api.recommendations({ perimeter_id: this.perimeterId(), status: 'pending' }).subscribe(r => this.pending.set(r.filter(x => x.impact === 'high').slice(0, 5)));
  }

  months = () => (this.kpis()?.monthly ?? []).map((m: any) => m.month.slice(5) + '/' + m.month.slice(2, 4));
  incidentSeries = () => (this.kpis()?.monthly ?? []).map((m: any) => m.incidents_opened);
  slaSeries = () => (this.kpis()?.monthly ?? []).map((m: any) => m.sla_pct);
  slaRows = () => Object.entries(this.kpis()?.sla_by_priority_last_month ?? {}).map(([p, v]: [string, any]) => ({ p, ...v }));

  ask() { launch(this.api, this.router, this.perimeterId(), 'ask', { question: this.question() }, this.busy); }
  exportDeck() { this.api.download(`/api/perimeters/${this.perimeterId()}/exports/copil.pptx`, 'COPIL.pptx'); }
  start(workflow: string) { launch(this.api, this.router, this.perimeterId(), workflow, {}, this.busy); }
}

import { DatePipe } from '@angular/common';
import { Component, DestroyRef, OnInit, computed, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AGENT_LABELS, Api, Run, STATUS_LABEL } from '../api';
import { RecCard, renderMd } from '../shared';

const WORKFLOW_LABEL: Partial<Record<string, string>> = {
  onboarding: "Onboarding d'un périmètre", copil: 'Préparation COPIL', billing: 'Pré-facturation mensuelle', capacity: 'Revue de capacité',
  governance: 'Contrôle santé du service', meeting_minutes: 'Compte-rendu de réunion', call: 'Compte-rendu de réunion', estimation: "Chiffrage d'une demande", ask: 'Question libre',
  review: 'Revue complète du périmètre', risk: 'Analyse des risques', document: 'Génération de document',
};
const GATED = ['prebilling', 'copil', 'staffing', 'onboarding_plan', 'minutes', 'document'];

@Component({
  selector: 'app-run',
  imports: [DatePipe, RouterLink, RecCard],
  template: `
    <div class="page">
      @if (run(); as r) {
        <div class="page-head">
          <div>
            <a class="small" [routerLink]="['/p', r.perimeter_id]">← Retour au périmètre</a>
            <h1>{{ workflowLabel[r.workflow] ?? r.workflow }}</h1>
            <p>Lancé par {{ r.requested_by }} le {{ r.started_at | date: 'dd/MM/yyyy HH:mm' }}
              @if (r.params?.question) { — « {{ r.params.question }} » }
              @if (r.params?.request) { — « {{ r.params.request }} » }</p>
          </div>
          <span class="pill big" [class.green]="r.status === 'completed'" [class.amber]="r.status === 'awaiting_approval'" [class.red]="r.status === 'failed'" [class.accent]="r.status === 'running'">
            @if (r.status === 'running') { <span class="spin"></span> } {{ statusLabel[r.status] ?? r.status }}
          </span>
        </div>

        <div class="layout">
          <div class="card timeline">
            <h2>Orchestration</h2>
            <p class="small muted">Plan, agents exécutés et point de validation humaine.</p>
            <ol>
              @for (s of steps(); track $index) {
                <li [class]="s.status ?? 'info'">
                  <div class="row"><strong>{{ nodeLabel(s.node) }}</strong>
                    @if (s.llm_used === false && s.status === 'completed') { <span class="pill">règles</span> }
                    @if (s.llm_used) { <span class="pill accent">IA</span> }
                    <span class="spacer"></span><span class="faint small mono">{{ s.ts | date: 'HH:mm:ss' }}</span></div>
                  <div class="small muted">{{ s.message }}</div>
                </li>
              }
            </ol>
            @if (r.error) { <p class="small" style="color: var(--red)">{{ r.error }}</p> }
          </div>

          <div class="stack">
            @if (r.output?.answer) {
              <div class="card">
                <h2>Réponse</h2>
                <div class="answer" [innerHTML]="md(r.output.answer.text)"></div>
                @if (r.output.answer.sources.length) {
                  <div class="small muted" style="margin-top: 10px">Sources : @for (s of r.output.answer.sources; track $index) { <span class="pill">{{ s.label }}</span> }</div>
                }
              </div>
            }
            @if (gated().length) {
              <div class="card gate">
                <div class="card-head"><div><h2>Livrables à valider</h2><p>Le workflow reprend automatiquement une fois ces décisions prises.</p></div></div>
                <div class="stack">@for (rec of gated(); track rec.id) { <app-rec [rec]="rec" [showRun]="false" (decided)="refresh()" /> }</div>
              </div>
            }
            @if (others().length) {
              <div class="card">
                <div class="card-head"><h2>Recommandations et alertes ({{ others().length }})</h2></div>
                <div class="stack">@for (rec of others(); track rec.id) { <app-rec [rec]="rec" [showRun]="false" (decided)="refresh()" /> }</div>
              </div>
            }
            @if (r.output && !r.output.answer) {
              <div class="card">
                <h2>Synthèse consolidée</h2>
                <table>
                  <tr><th>Agent</th><th>Synthèse</th><th class="num">Reco.</th></tr>
                  @for (a of agentOutputs(); track a.label) { <tr><td>{{ a.label }}</td><td class="pre small">{{ a.summary }}</td><td class="num">{{ a.recommendations }}</td></tr> }
                </table>
                <p class="small muted" style="margin-top: 8px">Décisions : {{ r.output.decisions.approved }} validée(s), {{ r.output.decisions.rejected }} rejetée(s), {{ r.output.decisions.pending }} en attente.</p>
              </div>
            }
          </div>
        </div>
      } @else { <div class="empty"><span class="spin"></span></div> }
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: 360px 1fr; gap: 16px; align-items: start; }
    .timeline { position: sticky; top: 16px; }
    ol { list-style: none; padding: 0; margin: 14px 0 0; border-left: 2px solid var(--border); }
    li { padding: 0 0 14px 14px; position: relative; }
    li::before { content: ''; position: absolute; left: -7px; top: 5px; width: 12px; height: 12px; border-radius: 50%; background: var(--surface); border: 2px solid var(--faint); }
    li.completed::before { border-color: var(--green); background: var(--green); }
    li.started::before { border-color: var(--accent); }
    li.waiting::before { border-color: var(--amber); background: var(--amber); }
    li.failed::before { border-color: var(--red); background: var(--red); }
    .gate { border-color: var(--amber); }
    .big { font-size: 13px; padding: 4px 12px; }
    .answer { font-size: 14px; line-height: 1.6; }
    @media (max-width: 1000px) { .layout { grid-template-columns: 1fr; } .timeline { position: static; } }
  `,
})
export class RunPage implements OnInit {
  protected md = renderMd;
  private api = inject(Api);
  runId = input.required<string>();
  protected run = signal<Run | null>(null);
  protected statusLabel = STATUS_LABEL;
  protected workflowLabel = WORKFLOW_LABEL;
  protected gated = computed(() => (this.run()?.recommendations ?? []).filter(r => GATED.includes(r.kind)));
  protected others = computed(() => (this.run()?.recommendations ?? []).filter(r => !GATED.includes(r.kind)));
  // hide an agent's "started" entry once it has finished
  protected steps = computed(() => {
    const all = this.run()?.steps ?? [];
    const finished = new Set(all.filter(s => s.status === 'completed' || s.status === 'failed').map(s => s.node));
    return all.filter(s => !(s.status === 'started' && finished.has(s.node)));
  });
  protected agentOutputs = computed(() => Object.values(this.run()?.output?.agents ?? {}) as any[]);
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    inject(DestroyRef).onDestroy(() => clearTimeout(this.timer));
  }

  ngOnInit() { this.refresh(); }

  refresh() {
    clearTimeout(this.timer);
    this.api.run(this.runId()).subscribe(r => {
      this.run.set(r);
      if (r.status === 'running' || r.status === 'awaiting_approval') this.timer = setTimeout(() => this.refresh(), r.status === 'running' ? 1200 : 4000);
    });
  }

  nodeLabel(node: string) {
    return AGENT_LABELS[node] ?? ({ plan: 'Planification', approval_gate: 'Validation humaine', finalize: 'Consolidation' } as Record<string, string>)[node] ?? node;
  }
}

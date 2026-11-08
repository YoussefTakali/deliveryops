import { DecimalPipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { Router } from '@angular/router';

import { Api, euros } from '../../api';
import { launch } from './shell';
import { RouterLink } from '@angular/router';

const ANOMALY_LABEL: Record<string, string> = {
  out_of_scope_absorbed: 'Hors périmètre absorbé', time_material_cap_exceeded: 'Plafond de régie', sla_penalty: 'Pénalité SLA',
  unjustified_time_material: 'Régie non justifiée', misclassified_activity: 'Imputation erronée', missing_timesheets: 'Saisies manquantes',
  work_unit_overrun: 'Dépassement UO', double_billing: 'Double facturation', excessive_daily_hours: 'Saisie excessive',
  unknown_activity_code: 'Code inconnu', unknown_ticket: 'Ticket inconnu', missing_rate: 'Tarif manquant', unknown_work_unit: 'UO hors catalogue',
};

@Component({
  selector: 'app-billing',
  imports: [DecimalPipe, RouterLink],
  template: `
    <div class="stack">
      @if (planVs(); as pv) {
        <div class="card">
          <div class="card-head"><div><h2>Suivi de charge : plan de charge vs réel</h2>
            <p>@if (pv.has_plan) { Jours planifiés pour ce périmètre (dernier plan de charge@if (pv.project_id) { du projet }) contre les temps saisis } @else if (pv.project_id) { Aucun plan de charge pour ce projet — <a [routerLink]="['/pr', pv.project_id, 'plan']">générez-le dans l'onglet Plan de charge du projet</a>. } @else { Aucun plan de charge généré pour ce périmètre — générez-le dans l'onglet Livrables. }</p></div></div>
          @if (pv.has_plan) {
            <table>
              <tr><th>Ressource</th><th class="num">Planifié (j)</th><th class="num">Réel (j)</th><th class="num">Écart</th><th></th></tr>
              @for (r of pv.rows; track r.name) {
                <tr><td>{{ r.name }}</td><td class="num">{{ r.planned_days }}</td><td class="num">{{ r.actual_days }}</td><td class="num">{{ r.delta_days }}</td>
                  <td><span class="pill" [class.over]="r.status === 'over'" [class.under]="r.status === 'under'">{{ r.status === 'over' ? 'dépassement' : r.status === 'under' ? 'sous-consommé' : 'conforme' }}</span></td></tr>
              }
              <tr><th>Total</th><th class="num">{{ pv.totals.planned_days }}</th><th class="num">{{ pv.totals.actual_days }}</th><th></th><th></th></tr>
            </table>
          }
        </div>
      }
      <div class="card row head">
        <div>
          <h2>Pré-facturation</h2>
          <p class="small muted">Montants calculés par le moteur de règles contractuelles (jamais par le LLM). L'agent Billing rédige la note de synthèse et soumet la pré-facture à validation.</p>
        </div>
        <span class="spacer"></span>
        <input type="month" [value]="period()" (change)="period.set($any($event.target).value)" style="width: 170px" />
        <button (click)="exportXlsx()">Exporter (Excel + justificatifs)</button>
        <button class="primary" [disabled]="!!busy()" (click)="runAgent()">@if (busy()) { <span class="spin"></span> } Soumettre à validation</button>
      </div>

      @if (data(); as d) {
        <div class="grid g5">
          <div class="card kpi"><span class="l">Total HT</span><span class="v">{{ eur(d.total_excl_tax) }}</span></div>
          <div class="card kpi"><span class="l">Lignes</span><span class="v">{{ d.lines.length }}</span></div>
          <div class="card kpi"><span class="l">Écarts bloquants</span><span class="v" [style.color]="highCount() ? 'var(--red)' : ''">{{ highCount() }}</span></div>
          <div class="card kpi"><span class="l">Écarts à contrôler</span><span class="v">{{ d.anomalies.length - highCount() }}</span></div>
          <div class="card kpi"><span class="l">Heures saisies</span><span class="v">{{ totalHours() | number: '1.0-0' }} h</span>
            <span class="small faint">@for (h of hours(); track h[0]) { {{ h[0] }} {{ h[1] }} · }</span></div>
        </div>

        <div class="grid g2">
          <div class="card">
            <div class="card-head"><h2>Lignes de pré-facture {{ d.period }}</h2></div>
            <table>
              <tr><th>Section</th><th>Libellé</th><th class="num">Qté</th><th class="num">P.U.</th><th class="num">Montant</th></tr>
              @for (l of d.lines; track $index) {
                <tr [class.neg]="l.amount < 0">
                  <td class="small muted">{{ l.section }}</td>
                  <td>{{ l.label }}<div class="small faint">{{ l.justification }}</div></td>
                  <td class="num">{{ l.quantity | number: '1.0-2' }}</td>
                  <td class="num">{{ eur(l.unit_price) }}</td>
                  <td class="num"><strong>{{ eur(l.amount) }}</strong></td>
                </tr>
              }
              <tr class="total"><td colspan="4">Total HT</td><td class="num">{{ eur(d.total_excl_tax) }}</td></tr>
            </table>
          </div>
          <div class="card">
            <div class="card-head"><h2>Rapport d'écarts</h2></div>
            <div class="stack anomalies">
              @for (a of sorted(); track $index) {
                <div class="anomaly" [class.high]="a.severity === 'high'">
                  <div class="row"><span class="pill" [class.red]="a.severity === 'high'" [class.amber]="a.severity === 'medium'">{{ label(a.kind) }}</span>
                    @if (a.amount) { <span class="small"><strong>{{ eur(a.amount) }}</strong></span> }</div>
                  <p class="small">{{ a.message }}</p>
                </div>
              } @empty { <div class="empty">Aucun écart détecté.</div> }
            </div>
          </div>
        </div>
      } @else { <div class="empty"><span class="spin"></span></div> }
    </div>
  `,
  styles: `
    .pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); } .pill.over { color: var(--red, #c0392b); border-color: currentColor; } .pill.under { color: var(--muted); }

    .head { align-items: center; }
    .head > div { max-width: 760px; }
    tr.neg td { color: var(--red); }
    tr.total td { font-weight: 600; border-top: 2px solid var(--border); }
    .anomalies { gap: 8px; max-height: 640px; overflow-y: auto; }
    .anomaly { border-left: 3px solid var(--amber); padding: 4px 10px; }
    .anomaly.high { border-left-color: var(--red); }
    .kpi .v { font-size: 20px; }
  `,
})
export class BillingTab {
  private api = inject(Api);
  private router = inject(Router);
  perimeterId = input.required<string>();
  protected planVs = signal<any>(null);
  private readonly _planVs = effect(() => { const id = this.perimeterId(); this.api.planVsReel(id).subscribe({ next: r => this.planVs.set(r), error: () => this.planVs.set(null) }); });

  protected period = signal(previousMonth());
  protected data = signal<any>(null);
  protected busy = signal('');
  protected eur = euros;
  protected highCount = computed(() => (this.data()?.anomalies ?? []).filter((a: any) => a.severity === 'high').length);
  protected hours = computed(() => Object.entries(this.data()?.hours_by_code ?? {}) as [string, number][]);
  protected totalHours = computed(() => this.hours().reduce((s, [, h]) => s + h, 0));
  protected sorted = computed(() => [...(this.data()?.anomalies ?? [])].sort((a: any, b: any) => (a.severity === 'high' ? 0 : 1) - (b.severity === 'high' ? 0 : 1)));

  constructor() {
    effect(() => {
      const [y, m] = this.period().split('-').map(Number);
      this.data.set(null);
      this.api.billingPreview(this.perimeterId(), y, m).subscribe(d => this.data.set(d));
    });
  }

  label(kind: string) { return ANOMALY_LABEL[kind] ?? kind; }

  exportXlsx() {
    const [year, month] = this.period().split('-').map(Number);
    this.api.download(`/api/perimeters/${this.perimeterId()}/exports/prebilling.xlsx?year=${year}&month=${month}`, 'prefacture.xlsx');
  }

  runAgent() {
    const [year, month] = this.period().split('-').map(Number);
    launch(this.api, this.router, this.perimeterId(), 'billing', { year, month }, this.busy);
  }
}

function previousMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

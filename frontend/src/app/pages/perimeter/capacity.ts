import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { Router } from '@angular/router';

import { Api } from '../../api';
import { Chart } from '../../shared';
import { launch } from './shell';

@Component({
  selector: 'app-capacity',
  imports: [Chart],
  template: `
    <div class="stack">
      <div class="card scenario">
        <div class="card-head">
          <div><h2>Simulation de scénarios</h2><p>Chaque modification recalcule instantanément la prévision de charge sur 8 semaines.</p></div>
          <button class="primary" [disabled]="!!busy()" (click)="runAgent()">@if (busy()) { <span class="spin"></span> } Faire analyser par l'agent</button>
        </div>
        <div class="controls">
          <div>
            <label for="growth">Évolution du volume d'incidents : <strong>{{ growth() > 0 ? '+' : '' }}{{ growth() }} %</strong></label>
            <input id="growth" type="range" min="-30" max="60" step="5" [value]="growth()" (input)="growth.set(+$any($event.target).value)" />
          </div>
          <div>
            <label for="fte">Renfort : <strong>{{ fte() }} ETP</strong></label>
            <input id="fte" type="range" min="0" max="4" step="0.5" [value]="fte()" (input)="fte.set(+$any($event.target).value)" />
          </div>
          <div>
            <label>Départs / indisponibilités simulés</label>
            <div class="row">
              @for (m of team(); track m.id) {
                <label class="chk"><input type="checkbox" [checked]="removed().includes(m.id)" (change)="toggle(m.id)" /> {{ m.name }}</label>
              }
            </div>
          </div>
        </div>
      </div>

      @if (data(); as d) {
        <div class="grid g5">
          <div class="card kpi"><span class="l">Semaines en surcharge</span><span class="v" [style.color]="d.overload_weeks ? 'var(--red)' : 'var(--green)'">{{ d.overload_weeks }} / {{ d.forecast.length }}</span></div>
          <div class="card kpi"><span class="l">Écart de capacité</span><span class="v">{{ d.capacity_gap_h }} h</span></div>
          <div class="card kpi"><span class="l">Besoin moyen en renfort</span><span class="v">{{ d.fte_gap }} ETP</span></div>
          <div class="card kpi"><span class="l">Pic d'utilisation</span><span class="v">{{ peak() }} %</span></div>
          <div class="card kpi"><span class="l">Compétences à risque</span><span class="v">{{ atRisk().length }}</span></div>
        </div>
        <div class="card">
          <div class="card-head"><div><h2>Prévision de charge</h2><p>Utilisation hebdomadaire (demande RUN prévue + backlog) / capacité disponible, absences incluses</p></div>
            <div class="row small muted"><span class="sw ok"></span> &lt; 85 % <span class="sw tension"></span> tension <span class="sw over"></span> surcharge</div></div>
          <app-chart [labels]="weeks()" [values]="utilization()" [classes]="classes()" [target]="100" targetLabel="100 % capacité" ariaLabel="Utilisation hebdomadaire prévue" />
        </div>
        <div class="grid g2">
          <div class="card">
            <h2>Charge par personne (2 semaines)</h2>
            <table>
              <tr><th>Membre</th><th>Profil</th><th class="num">Affecté</th><th class="num">Capacité</th><th>Charge</th></tr>
              @for (m of d.members; track m.id) {
                <tr><td>{{ m.name }}<div class="small faint">{{ m.skills.join(', ') }}</div></td><td class="muted">{{ m.profile }}</td>
                  <td class="num">{{ m.assigned_h }} h</td><td class="num">{{ m.capacity_2w_h }} h @if (m.absent_days_next_2w) { <div class="small" style="color: var(--amber)">{{ m.absent_days_next_2w }} j absent</div> }</td>
                  <td><span class="load"><span [style.width.%]="min(m.load_pct, 100)" [class.over]="m.load_pct > 100"></span></span> {{ m.load_pct }} %</td></tr>
              }
            </table>
          </div>
          <div class="card">
            <h2>Affectations proposées</h2>
            <table>
              @for (p of d.assignment_proposals; track p.backlog_id) {
                <tr><td>{{ p.title }}<div class="small faint">{{ p.reason }}</div></td>
                  <td>@if (p.member) { <span class="pill accent">{{ p.member }}</span> } @else { <span class="pill red">Aucune ressource</span> }</td>
                  <td class="num small">{{ p.projected_load_pct ? p.projected_load_pct + ' %' : '' }}</td></tr>
              } @empty { <tr><td class="empty">Tout le backlog est affecté.</td></tr> }
            </table>
            @if (atRisk().length) {
              <h3 style="margin-top: 14px">Compétences détenues par une personne au plus</h3>
              <div class="row" style="margin-top: 6px">@for (c of atRisk(); track c.skill) { <span class="pill red">{{ c.skill }} : {{ c.holders.join(', ') || 'personne' }}</span> }</div>
            }
          </div>
        </div>
      }
    </div>
  `,
  styles: `
    .controls { display: grid; grid-template-columns: 1fr 1fr 2fr; gap: 20px; }
    input[type=range] { padding: 0; }
    .chk { display: inline-flex; gap: 5px; align-items: center; color: var(--text); font-size: 13px; margin: 0 10px 0 0; }
    .chk input { width: auto; }
    .sw { width: 12px; height: 8px; border-radius: 2px; display: inline-block; margin-left: 8px; }
    .sw.ok { background: var(--accent); } .sw.tension { background: var(--amber); } .sw.over { background: var(--red); }
    .load { display: inline-block; width: 80px; height: 6px; border-radius: 3px; background: var(--grey-soft); overflow: hidden; vertical-align: middle; margin-right: 6px; }
    .load span { display: block; height: 100%; background: var(--accent); } .load span.over { background: var(--red); }
    .kpi .v { font-size: 20px; }
    @media (max-width: 900px) { .controls { grid-template-columns: 1fr; } }
  `,
})
export class CapacityTab {
  private api = inject(Api);
  private router = inject(Router);
  perimeterId = input.required<string>();

  protected growth = signal(0);
  protected fte = signal(0);
  protected removed = signal<string[]>([]);
  protected team = signal<any[]>([]);
  protected data = signal<any>(null);
  protected busy = signal('');

  protected weeks = computed(() => (this.data()?.forecast ?? []).map((w: any) => w.week.slice(8, 10) + '/' + w.week.slice(5, 7)));
  protected utilization = computed(() => (this.data()?.forecast ?? []).map((w: any) => w.utilization_pct));
  protected classes = computed(() => (this.data()?.forecast ?? []).map((w: any) => (w.status === 'overload' ? 'over' : w.status)));
  protected peak = computed(() => Math.max(0, ...this.utilization().filter((v: number | null) => v != null)));
  protected atRisk = computed(() => (this.data()?.skill_coverage ?? []).filter((c: any) => c.at_risk));

  constructor() {
    effect(() => this.api.perimeter(this.perimeterId()).subscribe(p => this.team.set(p.team)));
    effect(() => {
      this.api.simulate(this.perimeterId(), this.scenario()).subscribe(d => this.data.set(d));
    });
  }

  scenario() { return { incident_growth_pct: this.growth(), add_fte: this.fte(), remove_member_ids: this.removed() }; }
  toggle(id: string) { this.removed.update(r => (r.includes(id) ? r.filter(x => x !== id) : [...r, id])); }
  min(a: number, b: number) { return Math.min(a ?? 0, b); }
  runAgent() { launch(this.api, this.router, this.perimeterId(), 'capacity', { scenario: this.scenario() }, this.busy); }
}

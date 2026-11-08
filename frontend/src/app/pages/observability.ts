import { RouterLink } from '@angular/router';
import { DecimalPipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';

import { AGENT_LABELS, Api, Session } from '../api';
import { Chart } from '../shared';

import { SectionTabs } from '../section-tabs';
@Component({
  selector: 'app-observability',
  imports: [SectionTabs, DecimalPipe, Chart, RouterLink],
  template: `
    <app-section-tabs group="supervision" />
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Observabilité IA</h1>
          <p>Coût, latence et fiabilité des appels aux modèles, et qualité perçue des recommandations mesurée par les décisions humaines.</p>
        </div>
        <select [value]="days()" (change)="days.set(+$any($event.target).value)" style="width: 160px">
          <option value="7">7 derniers jours</option><option value="30">30 derniers jours</option><option value="90">90 derniers jours</option>
        </select>
      </div>

      @if (data(); as d) {
        <div class="grid g5">
          <div class="card kpi"><span class="l">Mode</span><span class="v">{{ d.mode === 'api' ? 'API connectée' : 'Hors ligne' }}</span>
            <span class="small faint">{{ d.models.agent }}</span></div>
          <div class="card kpi"><span class="l">Appels (dont modèle)</span><span class="v">{{ d.totals.calls }} <small>({{ d.totals.llm_calls }})</small></span></div>
          <div class="card kpi"><span class="l">Coût estimé</span><span class="v">{{ d.totals.cost_usd | number: '1.2-2' }} $</span></div>
          <div class="card kpi"><span class="l">Tokens entrée / sortie</span><span class="v">{{ compact(d.totals.input_tokens) }} / {{ compact(d.totals.output_tokens) }}</span></div>
          <div class="card kpi"><span class="l">Workflows (échecs)</span><span class="v">{{ d.totals.runs }} <small>({{ d.totals.runs_failed }})</small></span>
            @if (d.totals.avg_run_s) { <span class="small faint">durée moyenne {{ d.totals.avg_run_s }} s</span> }</div>
        </div>

        <div class="grid g2">
          <div class="card">
            <div class="card-head"><div><h2>Taux d'acceptation des recommandations</h2><p>Part des recommandations validées parmi celles décidées — signal principal de qualité des agents.</p></div></div>
            <table>
              <tr><th>Agent</th><th class="num">Émises</th><th class="num">Validées</th><th class="num">Rejetées</th><th class="num">En attente</th><th>Acceptation</th></tr>
              @for (a of d.acceptance; track a.agent) {
                <tr>
                  <td>{{ label(a.agent) }}</td><td class="num">{{ a.total }}</td><td class="num">{{ a.approved }}</td><td class="num">{{ a.rejected }}</td><td class="num">{{ a.pending }}</td>
                  <td>@if (a.acceptance_pct !== null) { <span class="bar"><span [style.width.%]="a.acceptance_pct" [class.low]="a.acceptance_pct < 60"></span></span> {{ a.acceptance_pct }} % } @else { <span class="faint">—</span> }</td>
                </tr>
              }
            </table>
            @if (rejections().length) {
              <h3 class="sub">Derniers motifs de rejet</h3>
              @for (r of rejections(); track $index) { <div class="small reject"><strong>{{ label(r.agent) }}</strong> — {{ r.title }} : <em>« {{ r.comment }} »</em></div> }
            }
          </div>
          <div class="card">
            <div class="card-head"><div><h2>Appels par jour</h2><p>Nombre d'appels (modèle ou repli déterministe)</p></div></div>
            <app-chart [labels]="dayLabels()" [values]="dayCalls()" ariaLabel="Appels par jour" />
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Par agent</h2><p>Le cache de prompt réduit le coût des contextes répétés (documentation, données du périmètre).</p></div></div>
          <table>
            <tr><th>Agent</th><th class="num">Appels</th><th class="num">Modèle</th><th class="num">Replis</th><th class="num">Erreurs</th><th class="num">Tokens in</th><th class="num">Tokens out</th><th class="num">Cache</th><th class="num">Latence p50</th><th class="num">Coût</th></tr>
            @for (a of d.by_agent; track a.agent) {
              <tr>
                <td>{{ label(a.agent) }}</td><td class="num">{{ a.calls }}</td><td class="num">{{ a.llm_calls }}</td><td class="num">{{ a.fallbacks }}</td>
                <td class="num" [style.color]="a.errors ? 'var(--red)' : ''">{{ a.errors }}</td>
                <td class="num">{{ compact(a.input_tokens) }}</td><td class="num">{{ compact(a.output_tokens) }}</td>
                <td class="num">{{ a.cache_hit_pct !== null ? a.cache_hit_pct + ' %' : '—' }}</td>
                <td class="num">{{ a.p50_latency_ms !== null ? (a.p50_latency_ms / 1000 | number: '1.1-1') + ' s' : '—' }}</td>
                <td class="num">{{ a.cost_usd | number: '1.3-3' }} $</td>
              </tr>
            } @empty { <tr><td class="empty" colspan="10">Aucun appel sur la période.</td></tr> }
          </table>
        </div>

        <div class="grid g2">
          <div class="card">
            <div class="card-head"><div><h2>Par utilisateur</h2><p>Qui consomme l'IA — quotas et blocages dans <a routerLink="/admin/governance">Gouvernance IA</a>.</p></div>
              <input class="usearch" placeholder="Filtrer…" [value]="userFilter()" (input)="userFilter.set($any($event.target).value)" /></div>
            <table>
              <tr><th>Utilisateur</th><th class="num">Appels</th><th class="num">Tokens</th><th class="num">Refusés</th></tr>
              @for (u of users(); track u.username) {
                <tr><td>{{ u.username }}</td><td class="num">{{ u.calls }}</td><td class="num">{{ compact(u.tokens) }}</td>
                  <td class="num" [style.color]="u.blocked ? 'var(--amber)' : ''">{{ u.blocked }}</td></tr>
              } @empty { <tr><td class="empty" colspan="4">Aucune consommation.</td></tr> }
            </table>
          </div>
          <div class="card">
            <div class="card-head"><div><h2>Par modèle</h2><p>Répartition des appels entre les modèles configurés.</p></div></div>
            <table>
              <tr><th>Modèle</th><th class="num">Appels</th><th class="num">Tokens</th><th class="num">Erreurs</th><th class="num">Coût</th></tr>
              @for (m of d.by_model ?? []; track m.model) {
                <tr><td class="mono small">{{ m.model }}</td><td class="num">{{ m.calls }}</td><td class="num">{{ compact(m.tokens) }}</td>
                  <td class="num" [style.color]="m.errors ? 'var(--red)' : ''">{{ m.errors }}</td><td class="num">{{ m.cost_usd | number: '1.3-3' }} $</td></tr>
              } @empty { <tr><td class="empty" colspan="5">—</td></tr> }
            </table>
          </div>
        </div>
      } @else { <div class="empty"><span class="spin"></span></div> }
    </div>
  `,
  styles: `
    .usearch { max-width: 180px; }
    .kpi .v { font-size: 20px; } .kpi .v small { font-size: 13px; color: var(--muted); }
    .bar { display: inline-block; width: 70px; height: 6px; border-radius: 3px; background: var(--grey-soft); overflow: hidden; vertical-align: middle; margin-right: 6px; }
    .bar span { display: block; height: 100%; background: var(--green); } .bar span.low { background: var(--amber); }
    .sub { margin: 14px 0 6px; }
    .reject { padding: 4px 0; border-top: 1px solid var(--border); }
    .grid.g2, .grid.g5 { margin-bottom: 16px; }
  `,
})
export class ObservabilityPage {
  private api = inject(Api);
  private session = inject(Session);
  protected days = signal(30);
  protected data = signal<any>(null);
  protected dayLabels = computed(() => (this.data()?.by_day ?? []).map((d: any) => d.day.slice(8, 10) + '/' + d.day.slice(5, 7)));
  protected dayCalls = computed(() => (this.data()?.by_day ?? []).map((d: any) => d.calls));
  protected userFilter = signal('');
  protected users = computed(() => {
    const f = this.userFilter().trim().toLowerCase();
    return (this.data()?.by_user ?? []).filter((u: any) => !f || u.username.toLowerCase().includes(f));
  });
  protected rejections = computed(() => (this.data()?.acceptance ?? []).flatMap((a: any) => a.rejections.map((r: any) => ({ ...r, agent: a.agent }))).slice(0, 8));

  constructor() {
    effect(() => {
      this.session.username();
      this.api.observability(this.days()).subscribe(d => this.data.set(d));
    });
  }

  label(agent: string) { return AGENT_LABELS[agent] ?? agent; }
  compact(n: number) { return n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + ' M' : n >= 1000 ? (n / 1000).toFixed(1) + ' k' : String(n); }
}

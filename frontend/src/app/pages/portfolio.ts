import { Component, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api, PortfolioItem, Session } from '../api';
import { Chart } from '../shared';

import { SectionTabs } from '../section-tabs';
@Component({
  selector: 'app-portfolio',
  imports: [SectionTabs, RouterLink, Chart],
  template: `
    <app-section-tabs group="portfolio" />
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Portefeuille delivery</h1>
          <p>Santé des périmètres AMS/TMA visibles pour {{ session.me()?.name }}. Les indicateurs sont calculés à partir des tickets et du contrat de chaque client.</p>
        </div>
      </div>

      @if (loading()) { <div class="empty"><span class="spin"></span></div> }
      <div class="grid g2">
        @for (p of items(); track p.id) {
          <a class="card perim" [routerLink]="['/p', p.id]">
            <div class="card-head">
              <div>
                <span class="faint small">{{ p.client }}</span>
                <h2>{{ p.name }}</h2>
              </div>
              <span class="pill" [class]="p.overall_status">{{ statusLabel[p.overall_status] }}</span>
            </div>
            <div class="grid g5 kpis">
              @for (s of p.scorecard; track s.kpi) {
                <div class="kpi">
                  <span class="l"><span class="dot" [class]="s.status"></span> {{ s.kpi }}</span>
                  <span class="v">{{ s.value ?? '—' }}<small>{{ s.unit }}</small></span>
                  <span class="small faint">cible {{ s.target }}{{ s.unit }}</span>
                </div>
              }
            </div>
            <app-chart [labels]="months(p)" [values]="incidents(p)" [line]="sla(p)" ariaLabel="Incidents ouverts par mois et respect des SLA" />
            <div class="small muted legend"><span class="sw bar"></span> incidents ouverts / mois <span class="sw line"></span> respect SLA</div>
            <div class="row foot">
              <span class="pill" [class.red]="p.high_risks > 0">{{ p.high_risks }} alerte(s) majeure(s)</span>
              <span class="pill" [class.amber]="p.pending_decisions > 0">{{ p.pending_decisions }} décision(s) en attente</span>
              <span class="pill">{{ p.applications }} applications</span>
              <span class="pill">{{ p.backlog_open }} tickets ouverts</span>
            </div>
          </a>
        } @empty {
          @if (!loading()) { <div class="empty card">Aucun périmètre accessible avec ce profil.</div> }
        }
      </div>
    </div>
  `,
  styles: `
    .perim { color: var(--text); display: flex; flex-direction: column; gap: 14px; }
    .perim:hover { text-decoration: none; border-color: var(--accent); }
    .kpis { gap: 10px; }
    .kpi .v { font-size: 20px; }
    .kpi .v small { font-size: 12px; color: var(--muted); margin-left: 1px; }
    .legend { display: flex; align-items: center; gap: 6px; margin-top: -8px; }
    .sw { width: 12px; height: 8px; display: inline-block; border-radius: 2px; margin-left: 8px; }
    .sw.bar { background: var(--accent); } .sw.line { height: 2px; background: var(--text); }
    .foot { margin-top: 2px; }
  `,
})
export class PortfolioPage {
  private api = inject(Api);
  protected session = inject(Session);
  protected items = signal<PortfolioItem[]>([]);
  protected loading = signal(true);
  protected statusLabel: Record<string, string> = { green: 'Maîtrisé', amber: 'Sous tension', red: 'En dérive', grey: 'N/A' };

  constructor() {
    effect(() => {
      this.session.username();
      this.loading.set(true);
      this.api.portfolio().subscribe(i => { this.items.set(i); this.loading.set(false); });
    });
  }

  months = (p: PortfolioItem) => p.monthly.map(m => m.month.slice(5) + '/' + m.month.slice(2, 4));
  incidents = (p: PortfolioItem) => p.monthly.map(m => m.incidents_opened);
  sla = (p: PortfolioItem) => p.monthly.map(m => m.sla_pct);
}

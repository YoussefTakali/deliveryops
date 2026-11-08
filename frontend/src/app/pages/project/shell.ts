import { DecimalPipe } from '@angular/common';
import { Component, computed, effect, inject, input } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Api, Session } from '../../api';
import { ProjectStore } from './store';

const SEVERITY: Record<string, number> = { red: 3, amber: 2, green: 1, grey: 0 };

/** A delivery project as a workspace: header with its live KPIs, then tabs — Vue d'ensemble, Périmètres,
 *  Équipe, Livrables (the project's own set) and Plan de charge (one document for the whole project). */
@Component({
  selector: 'app-project-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, DecimalPipe],
  providers: [ProjectStore],
  template: `
    <div class="page">
      @if (s.project(); as p) {
        <div class="crumbs small"><a routerLink="/projects">Projets</a> › <span>{{ p.client }}</span></div>
        <header class="hero">
          <div class="title">
            <div class="row">
              <h1>{{ p.name }}</h1>
              <span class="pill" [class.green]="p.status === 'active'" [class.amber]="p.status !== 'active'">{{ s.statusLabel(p.status) }}</span>
            </div>
            <p class="muted">{{ p.description || 'Mission de TMA / AMS.' }}</p>
            <div class="mgr small">
              <span class="av">{{ s.initials(s.name(p.manager)) }}</span>
              <span>Piloté par <strong>{{ s.name(p.manager) || '—' }}</strong></span>
              @if (p.technologies.length) { <span class="sep">·</span> @for (t of p.technologies.slice(0, 6); track t) { <span class="tech">{{ t }}</span> } }
            </div>
          </div>
          @if (s.canDecide()) {
            <div class="actions">
              @if (p.status === 'active') { <button class="ghost" (click)="setStatus('done')">Clôturer le projet</button> }
              @else { <button (click)="setStatus('active')">Réactiver</button> }
            </div>
          }
        </header>
        @if (p.status !== 'active') {
          <div class="banner">Projet clôturé — son équipe est libérée et peut être affectée à d'autres projets.</div>
        }

        <div class="kpis">
          <a class="kpi" routerLink="perimetres">
            <span class="label">Périmètres</span>
            <span class="value">{{ p.perimeters?.length ?? 0 }}</span>
            <span class="sub"><span class="dot" [class]="worst()"></span> {{ healthLabel() }}</span>
          </a>
          <a class="kpi" routerLink="equipe">
            <span class="label">Équipe</span>
            <span class="value">{{ p.members?.length ?? 0 }}</span>
            <span class="avs">@for (m of (p.members ?? []).slice(0, 5); track m.id) { <span class="av sm" [class.ext]="m.is_external" [title]="m.name || m.owner">{{ s.initials(m.name || s.name(m.owner)) }}</span> }</span>
          </a>
          <a class="kpi" routerLink="livrables">
            <span class="label">Livrables du projet</span>
            <span class="value">{{ s.activeLivrables() }}</span>
            <span class="sub">catalogue + propres au projet</span>
          </a>
          <a class="kpi" routerLink="plan">
            <span class="label">Plan de charge</span>
            @if (s.latestPlan()?.totals; as t) {
              <span class="value">{{ t.days | number: '1.0-0' }} <small>j</small></span>
              <span class="sub">{{ t.cost | number: '1.0-0' }} € planifiés</span>
            } @else {
              <span class="value muted">—</span><span class="sub">à générer</span>
            }
          </a>
        </div>

        <nav class="tabs">
          <a routerLink="./" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Vue d'ensemble</a>
          <a routerLink="perimetres" routerLinkActive="active">Périmètres</a>
          <a routerLink="equipe" routerLinkActive="active">Équipe</a>
          <a routerLink="livrables" routerLinkActive="active">Livrables</a>
          <a routerLink="plan" routerLinkActive="active">Plan de charge</a>
        </nav>
        <router-outlet />
      } @else if (s.loadError()) {
        <div class="card"><p>{{ s.loadError() }}</p><a class="btn" routerLink="/projects">← Tous les projets</a></div>
      } @else {
        <div class="card"><span class="spin"></span> Chargement du projet…</div>
      }
    </div>
  `,
  styles: `
    .crumbs { color: var(--muted); margin-bottom: 6px; } .crumbs a { color: var(--muted); text-decoration: none; } .crumbs a:hover { color: var(--accent); }
    .hero { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; }
    .title .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .title h1 { margin: 0; }
    .title p { margin: 6px 0 8px; max-width: 760px; }
    .mgr { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; color: var(--muted); }
    .mgr strong { color: var(--text); } .sep { opacity: .5; }
    .tech { font-size: 11px; padding: 1px 8px; border-radius: 999px; background: var(--surface-2); border: 1px solid var(--border); color: var(--text); }
    .av { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border-radius: 50%; background: var(--accent-soft); color: var(--accent); font-size: 11px; font-weight: 700; flex: none; }
    .av.sm { width: 24px; height: 24px; font-size: 10px; border: 2px solid var(--surface); margin-left: -6px; }
    .av.sm:first-child { margin-left: 0; }
    .av.ext { background: var(--amber-soft); color: var(--amber); }
    .banner { padding: 9px 12px; border-radius: 10px; background: var(--amber-soft); color: var(--text); font-size: 13px; margin-bottom: 12px; }
    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
    @media (max-width: 900px) { .kpis { grid-template-columns: repeat(2, 1fr); } }
    .kpi { display: flex; flex-direction: column; gap: 4px; padding: 14px 16px; border: 1px solid var(--border); border-radius: var(--radius);
      background: var(--surface); box-shadow: var(--shadow); color: inherit; text-decoration: none; transition: border-color .15s; }
    .kpi:hover { border-color: var(--accent); }
    .kpi .label { font-size: 12px; color: var(--muted); }
    .kpi .value { font-size: 26px; font-weight: 700; line-height: 1.1; } .kpi .value small { font-size: 14px; font-weight: 500; color: var(--muted); }
    .kpi .sub { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
    .avs { display: flex; margin-top: 2px; }
    .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: var(--grey-soft); }
  `,
})
export class ProjectShell {
  projectId = input.required<string>();
  protected s = inject(ProjectStore);
  private api = inject(Api);
  private session = inject(Session);

  protected worst = computed(() => {
    let w = 'grey';
    for (const p of this.s.perimeters()) if ((SEVERITY[p.overall_status] ?? 0) > (SEVERITY[w] ?? 0)) w = p.overall_status;
    return w;
  });
  protected healthLabel = computed(() => ({ red: 'au moins un en alerte', amber: 'à surveiller', green: 'tous au vert', grey: 'pas encore de données' } as Record<string, string>)[this.worst()]);

  constructor() {
    effect(() => { const id = this.projectId(); this.session.username(); this.s.open(id); });
  }

  protected setStatus(status: string) {
    if (status === 'done' && !confirm('Clôturer ce projet ? Son équipe sera libérée pour d’autres projets.')) return;
    this.api.updateProject(this.s.id(), { status }).subscribe({ next: () => this.s.reload() });
  }
}

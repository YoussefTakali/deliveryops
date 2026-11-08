import { Component, inject, signal } from '@angular/core';

import { Companion } from '../companion.service';

import { SectionTabs } from '../section-tabs';
/** Governance of the work companion: what the organisation allows it to collect and do, and how it
 *  is being adopted. Deliberately aggregate-only — no window title, memory or message is ever shown. */
@Component({
  selector: 'app-admin-companion',
  imports: [SectionTabs],
  template: `
    <app-section-tabs group="supervision" />
    <div class="page">
      <div class="page-head">
        <div><h1>Compagnon — politique & supervision</h1>
          <p>Ce que le compagnon a le droit de collecter et de faire dans votre organisation, et son adoption. Les contenus individuels
            (titres de fenêtres, mémoires, conversations) ne sont jamais visibles ici — uniquement des agrégats.</p></div>
      </div>

      @if (metrics(); as m) {
        <div class="grid g4">
          <div class="card kpi"><span>Utilisateurs</span><strong>{{ m.users }}</strong><em>{{ m.active_7d }} actifs sur 7 j</em></div>
          <div class="card kpi"><span>Suivi d'activité</span><strong>{{ m.tracking_opt_in }}</strong><em>ont donné leur accord</em></div>
          <div class="card kpi"><span>Suggestions acceptées</span><strong>{{ m.suggestions.acceptance_pct ?? '—' }}@if (m.suggestions.acceptance_pct != null) { % }</strong><em>{{ m.suggestions.offered }} proposées / {{ m.days }} j</em></div>
          <div class="card kpi"><span>Mémoires apprises</span><strong>{{ m.memories }}</strong><em>{{ m.messages }} messages / {{ m.days }} j</em></div>
        </div>
      }

      <div class="grid g2">
        @if (policy(); as p) {
          <div class="card">
            <h2>Politique de l'organisation</h2>
            <label class="check"><input type="checkbox" [checked]="p.enabled" (change)="save({ enabled: $any($event.target).checked })" /> Compagnon activé</label>
            <label class="check"><input type="checkbox" [checked]="p.proactive_allowed" (change)="save({ proactive_allowed: $any($event.target).checked })" /> Suggestions proactives autorisées</label>
            <label class="check"><input type="checkbox" [checked]="p.memory_learning_allowed" (change)="save({ memory_learning_allowed: $any($event.target).checked })" /> Apprentissage automatique des préférences (à partir des conversations)</label>
            <label class="check"><input type="checkbox" [checked]="p.activity_tracking_allowed" (change)="toggleTracking($any($event.target).checked)" /> Suivi d'activité autorisé (chaque utilisateur doit en plus donner son accord)</label>
            <div class="fields">
              <label>Niveau de capture
                <select [value]="p.title_capture" (change)="save({ title_capture: $any($event.target).value })">
                  <option value="full">Application + titre de fenêtre</option><option value="app_only">Application uniquement (aucun titre)</option>
                </select></label>
              <label>Conservation de l'activité (jours)
                <input type="number" min="1" max="365" [value]="p.retention_days" (change)="save({ retention_days: +$any($event.target).value })" /></label>
              <label>Suggestions max. par heure et par personne
                <input type="number" min="0" max="20" [value]="p.max_nudges_per_hour" (change)="save({ max_nudges_per_hour: +$any($event.target).value })" /></label>
              <label>Applications jamais enregistrées <span class="small muted">(noms de processus, séparés par des virgules)</span>
                <input [value]="p.excluded_apps.join(', ')" (change)="save({ excluded_apps: list($any($event.target).value) })" placeholder="sap, sirh, workday" /></label>
              <label>Mots-clés de titres jamais enregistrés
                <input [value]="p.excluded_keywords.join(', ')" (change)="save({ excluded_keywords: list($any($event.target).value) })" placeholder="confidentiel, licenciement, m&a" /></label>
            </div>
            <p class="small faint">Toujours exclus, quelle que soit la politique : {{ p.always_excluded_keywords.slice(0, 10).join(', ') }}…</p>
            @if (p.updated_at) { <p class="small faint">Dernière modification par {{ p.updated_by }} · journalisée dans l'audit.</p> }
            @if (msg()) { <p class="small muted">{{ msg() }}</p> }
          </div>
        }

        @if (metrics(); as m) {
          <div class="card">
            <h2>Utilité des suggestions</h2>
            <table class="tbl">
              <thead><tr><th>Type</th><th>Proposées</th><th>Acceptées</th><th>Écartées</th><th>« Jamais »</th></tr></thead>
              <tbody>
                @for (k of m.suggestions.by_kind; track k.kind) { <tr><td>{{ k.label }}</td><td>{{ k.offered }}</td><td>{{ k.accepted }}</td><td>{{ k.dismissed }}</td><td>{{ k.never }}</td></tr> }
                @empty { <tr><td colspan="5" class="muted">Aucune suggestion sur la période.</td></tr> }
              </tbody>
            </table>
            <h2 class="mt">Temps par nature d'activité <span class="small muted">(agrégé, tous utilisateurs consentants)</span></h2>
            @for (c of m.time_by_category; track c.category) {
              <div class="bar-row"><span>{{ c.category }}</span><div class="bar"><span [style.width.%]="pct(c.hours, m.time_by_category[0].hours)"></span></div><span class="small muted">{{ c.hours }} h</span></div>
            } @empty { <p class="small muted">Aucune activité collectée.</p> }
          </div>
        }
      </div>

      <div class="card">
        <h2>Adoption par utilisateur <span class="small muted">— compteurs uniquement</span></h2>
        <table class="tbl">
          <thead><tr><th>Utilisateur</th><th>Métier</th><th>Suivi</th><th>Messages 7 j</th><th>Heures suivies 7 j</th><th>Mémoires</th><th>Acceptation</th><th></th></tr></thead>
          <tbody>
            @for (u of users(); track u.owner) {
              <tr><td><strong>{{ u.display_name || u.owner }}</strong><div class="small muted">{{ u.owner }}</div></td><td>{{ u.job_role }}</td>
                <td>{{ u.tracking_enabled ? 'consenti' : '—' }}</td><td>{{ u.messages_7d }}</td><td>{{ u.tracked_hours_7d }}</td><td>{{ u.memories }}</td>
                <td>{{ u.acceptance_pct ?? '—' }}@if (u.acceptance_pct != null) { % }</td>
                <td><button class="sm danger" (click)="erase(u.owner)" title="Droit à l'effacement / départ du collaborateur">Effacer ses données</button></td></tr>
            } @empty { <tr><td colspan="8" class="muted">Aucun utilisateur n'a encore ouvert le compagnon.</td></tr> }
          </tbody>
        </table>
      </div>
    </div>
  `,
  styles: `
    .g4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin-bottom: 14px; }
    .kpi { display: flex; flex-direction: column; gap: 2px; } .kpi span { font-size: 12px; color: var(--muted); } .kpi strong { font-size: 26px; } .kpi em { font-size: 12px; color: var(--muted); font-style: normal; }
    .check { display: flex; gap: 8px; align-items: center; padding: 5px 0; font-size: 13.5px; }
    .fields { display: flex; flex-direction: column; gap: 10px; margin: 12px 0; } .fields label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
    .tbl { width: 100%; border-collapse: collapse; font-size: 13px; } .tbl th { text-align: left; font-size: 11px; color: var(--muted); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--border); }
    .tbl td { padding: 7px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
    .mt { margin-top: 20px; } .bar-row { display: grid; grid-template-columns: 110px 1fr 56px; gap: 8px; align-items: center; font-size: 12px; padding: 3px 0; }
    .bar { height: 7px; border-radius: 4px; background: var(--surface-2); overflow: hidden; } .bar span { display: block; height: 100%; background: var(--accent); }
    button.danger { color: var(--red, #c0392b); border-color: currentColor; }
  `,
})
export class AdminCompanionPage {
  private c = inject(Companion);
  protected policy = signal<any | null>(null);
  protected metrics = signal<any | null>(null);
  protected users = signal<any[]>([]);
  protected msg = signal('');

  constructor() { void this.load(); }

  private async load() {
    const [p, m, u] = await Promise.all([this.c.adminPolicy(), this.c.adminMetrics(), this.c.adminUsers()]);
    this.policy.set(p); this.metrics.set(m); this.users.set(u);
  }

  protected list(v: string) { return v.split(',').map(x => x.trim()).filter(Boolean); }
  protected pct(a: number, b: number) { return b ? Math.round((100 * a) / b) : 0; }

  protected async save(patch: object) {
    const p = await this.c.saveAdminPolicy(patch);
    this.policy.update(old => ({ ...old, ...p }));
    this.msg.set('Politique enregistrée et journalisée.');
  }

  protected async toggleTracking(allowed: boolean) {
    if (!allowed && !confirm('Désactiver le suivi d’activité pour toute l’organisation ? L’activité déjà collectée sera supprimée.')) { await this.load(); return; }
    await this.save({ activity_tracking_allowed: allowed });
    await this.load();
  }

  protected async erase(owner: string) {
    if (!confirm(`Effacer définitivement toutes les données du compagnon pour ${owner} ? (action journalisée)`)) return;
    await this.c.adminErase(owner);
    await this.load();
  }
}

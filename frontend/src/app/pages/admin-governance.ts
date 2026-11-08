import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Component, computed, inject, signal } from '@angular/core';

import { Api, GovernancePolicy, GovernedUser } from '../api';
import { DebugAi } from '../debug-ai';

import { SectionTabs } from '../section-tabs';
/** Enterprise AI governance (permission `governance.manage`): global AI switch, default daily token
 *  quota per user, per-user suspension / AI block / quota override, daily caps per agent, and who
 *  consumes what. Every change is audited (see Journal d'audit). */
@Component({
  selector: 'app-admin-governance',
  imports: [SectionTabs, DecimalPipe, DebugAi, RouterLink],
  template: `
    <app-section-tabs group="supervision" />
    <div class="page gov">
      <div class="page-head">
        <div>
          <h1>Gouvernance IA</h1>
          <p>Contrôlez qui utilise la plateforme et l'IA, et combien : blocage d'un utilisateur, blocage de son IA, quota de tokens par jour (par défaut ou par personne), plafond journalier par agent. Chaque modification est tracée dans le journal d'audit.</p>
        </div>      </div>
      @if (error()) { <app-debug-ai context="Page Gouvernance IA (administration)" [error]="error()" /> }

      <div class="grid g3 kpis">
        <div class="card kpi"><div class="small muted">Tokens consommés aujourd'hui</div><strong>{{ todayTotal() | number }}</strong></div>
        <div class="card kpi"><div class="small muted">Sur {{ usageDays() }} jours</div><strong>{{ periodTotal() | number }}</strong></div>
        <div class="card kpi"><div class="small muted">Appels refusés par la gouvernance</div><strong>{{ blockedTotal() | number }}</strong></div>
      </div>

      @if (policy(); as p) {
        <div class="card">
          <div class="card-head"><h2>Politique de l'organisation</h2>@if (p.updated_by) { <span class="small faint">modifiée par {{ p.updated_by }}</span> }</div>
          <div class="pol">
            <label class="switch">
              <input type="checkbox" [checked]="p.ai_enabled" (change)="setAi($any($event.target).checked)" />
              <span><strong>IA activée pour l'organisation</strong><br><span class="small muted">Désactivée : tous les agents passent en mode dégradé (réponses déterministes), personne ne consomme de tokens.</span></span>
            </label>
            <div class="quota">
              <label class="small"><strong>Quota journalier par utilisateur (tokens)</strong> <span class="faint">— 0 = illimité</span></label>
              <div class="row gap">
                <input type="number" min="0" step="1000" [value]="defaultQuota()" (input)="defaultQuota.set(+$any($event.target).value)" />
                <button class="primary sm" (click)="saveDefault()">Enregistrer</button>
              </div>
            </div>
          </div>
        </div>
      }

      <div class="card">
        <div class="card-head"><h2>Utilisateurs</h2>
          <input class="search" placeholder="Rechercher un utilisateur…" [value]="q()" (input)="q.set($any($event.target).value); loadUsers()" />
        </div>
        <div class="scroll">
        <table>
          <thead><tr><th>Utilisateur</th><th>Aujourd'hui</th><th class="num">7 jours</th><th>Quota / jour</th><th>IA</th><th>Accès</th><th>Note</th></tr></thead>
          <tbody>
            @for (u of users(); track u.username) {
              <tr [class.off]="u.app_blocked">
                <td><strong>{{ u.name }}</strong><div class="small faint">{{ u.username }}</div></td>
                <td class="usage">
                  <div class="small">{{ u.used_today | number }}{{ u.effective_quota ? ' / ' + (u.effective_quota | number) : '' }}</div>
                  @if (u.effective_quota) { <div class="bar"><span [style.width.%]="pct(u)" [class.hot]="pct(u) >= 90"></span></div> }
                </td>
                <td class="num small">{{ u.used_7d | number }}</td>
                <td>
                  <div class="row gap nowrap">
                    <input class="qin" type="number" min="0" step="1000" [placeholder]="'défaut (' + (defaultQuotaSaved() || '∞') + ')'"
                           [value]="u.daily_token_quota ?? ''" (change)="setQuota(u, $any($event.target).value)" title="Vide = quota par défaut, 0 = illimité" />
                    @if (u.daily_token_quota !== null) { <button class="sm ghost" (click)="resetQuota(u)" title="Revenir au quota par défaut">↺</button> }
                  </div>
                </td>
                <td><label class="tgl" [title]="u.is_self ? 'Vous ne pouvez pas vous bloquer' : ''">
                  <input type="checkbox" [checked]="!u.ai_blocked" [disabled]="u.is_self" (change)="toggle(u, 'ai_blocked', !$any($event.target).checked)" />
                  <span class="small">{{ u.ai_blocked ? 'bloquée' : 'autorisée' }}</span></label></td>
                <td><label class="tgl" [title]="u.is_self ? 'Vous ne pouvez pas vous suspendre' : ''">
                  <input type="checkbox" [checked]="!u.app_blocked" [disabled]="u.is_self" (change)="toggle(u, 'app_blocked', !$any($event.target).checked)" />
                  <span class="small">{{ u.app_blocked ? 'suspendu' : 'actif' }}</span></label></td>
                <td><input class="note" [value]="u.note" placeholder="—" (change)="setNote(u, $any($event.target).value)" /></td>
              </tr>
            } @empty { <tr><td colspan="7" class="empty">Aucun utilisateur.</td></tr> }
          </tbody>
        </table>
        </div>
      </div>

      @if (policy(); as p) {
        <div class="card">
          <div class="card-head"><h2>Plafond journalier par agent</h2><span class="small faint">tokens / jour, tous utilisateurs confondus — 0 = illimité</span></div>
          <table class="caps">
            <thead><tr><th>Agent</th><th>Aujourd'hui</th><th>Plafond</th></tr></thead>
            @for (grp of groups(p.agents); track grp.kind) {
            <tbody>
              <tr class="grp"><td colspan="3">{{ grp.label }} <span class="small faint">— {{ grp.hint }}</span></td></tr>
              @for (a of grp.items; track a.key) {
                <tr>
                  <td>{{ a.label }} <span class="small faint">{{ a.key }}</span></td>
                  <td class="usage">
                    <div class="small">{{ a.used_today | number }}{{ a.cap ? ' / ' + (a.cap | number) : '' }}</div>
                    @if (a.cap) { <div class="bar"><span [style.width.%]="capPct(a)" [class.hot]="capPct(a) >= 90"></span></div> }
                  </td>
                  <td><input class="qin" type="number" min="0" step="1000" [value]="a.cap || ''" placeholder="illimité" (change)="setCap(a.key, $any($event.target).value)" /></td>
                </tr>
              }
            </tbody>
            }
          </table>
        </div>
      }

      <p class="small faint">L'historique détaillé (par jour, par agent, par utilisateur, par modèle) est dans <a routerLink="/observability">Observabilité IA</a>.</p>
      @if (saved()) { <p class="small ok">{{ saved() }}</p> }
    </div>
  `,
  styles: `
    /* Vertical rhythm: every block of the page is separated by the same gap. */
    .gov { display: flex; flex-direction: column; gap: 24px; }
    .gov .page-head { margin-bottom: 0; align-items: flex-start; }
    .gov .page-head p { max-width: 820px; line-height: 1.55; }
    .gov .card { padding: 20px 22px; }
    .gov .card-head { margin-bottom: 18px; flex-wrap: wrap; row-gap: 8px; }

    .kpis { gap: 20px; }
    .kpis .kpi { padding: 18px 20px; gap: 6px; }
    .kpis .kpi strong { font-size: 24px; display: block; }

    .pol { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; align-items: start; }
    @media (max-width: 800px) { .pol { grid-template-columns: 1fr; gap: 24px; } }
    .switch { display: flex; gap: 12px; align-items: flex-start; cursor: pointer; margin: 0; line-height: 1.5; }
    .switch input { width: auto; margin-top: 4px; }
    .quota { display: flex; flex-direction: column; gap: 10px; }
    .quota label { margin: 0; }
    .quota input { max-width: 200px; }
    .row { display: flex; align-items: center; } .gap { gap: 10px; } .nowrap { flex-wrap: nowrap; }
    .search { max-width: 280px; }

    /* Tables: roomier cells, controls vertically centred on their row. */
    .scroll { overflow-x: auto; margin: 0 -4px; padding: 0 4px; }
    table th { padding: 10px 14px; }
    table td { padding: 12px 14px; vertical-align: middle; }
    tr.grp td { background: var(--surface-2); font-weight: 600; font-size: 12.5px; padding: 10px 14px; }
    tbody + tbody tr.grp td { border-top: 12px solid var(--surface); }
    .caps td:first-child { width: 50%; }
    .caps td .faint { margin-left: 6px; }
    .qin { width: 140px; } .note { width: 180px; }
    .tgl { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; margin: 0; white-space: nowrap; } .tgl input { width: auto; }
    tr.off td { opacity: .6; }
    .usage { min-width: 150px; }
    .bar { height: 6px; border-radius: 3px; background: var(--grey-soft); overflow: hidden; margin-top: 6px; }
    .bar span { display: block; height: 100%; background: var(--accent); }
    .bar span.hot { background: var(--red); }
    .ok { color: var(--green); }
  `,
})
export class AdminGovernancePage {
  private api = inject(Api);
  protected policy = signal<GovernancePolicy | null>(null);
  protected users = signal<GovernedUser[]>([]);
  protected usage = signal<any>(null);
  protected usageDays = signal(7);
  protected q = signal('');
  protected defaultQuota = signal(0);
  protected defaultQuotaSaved = signal(0);
  protected error = signal<string | null>(null);
  protected saved = signal('');

  protected todayTotal = computed(() => this.users().reduce((n, u) => n + u.used_today, 0));
  protected periodTotal = computed(() => (this.usage()?.by_day ?? []).reduce((n: number, d: any) => n + d.tokens, 0));
  protected blockedTotal = computed(() => (this.usage()?.by_agent ?? []).reduce((n: number, a: any) => n + (a.blocked ?? 0), 0));

  constructor() { this.loadPolicy(); this.loadUsers(); this.loadUsage(); }

  private fail = (e: any) => this.error.set(e?.error?.detail ?? 'Action impossible (droits insuffisants ?)');
  private ok(msg: string) { this.saved.set(msg); this.error.set(null); setTimeout(() => this.saved.set(''), 2500); }

  loadPolicy() {
    this.api.governancePolicy().subscribe({ next: p => { this.policy.set(p); this.defaultQuota.set(p.default_daily_tokens); this.defaultQuotaSaved.set(p.default_daily_tokens); }, error: this.fail });
  }
  loadUsers() { this.api.governanceUsers(this.q()).subscribe({ next: r => this.users.set(r.users), error: this.fail }); }
  loadUsage() { this.api.governanceUsage(this.usageDays()).subscribe({ next: u => this.usage.set(u), error: this.fail }); }

  protected pct(u: GovernedUser) { return u.effective_quota ? Math.min(100, Math.round(100 * u.used_today / u.effective_quota)) : 0; }
  protected groups(agents: GovernancePolicy['agents']) {
    return [
      { kind: 'agent', label: 'Agents', hint: 'orchestrés (pipeline) ou conversationnels', items: agents.filter(a => a.kind !== 'feature') },
      { kind: 'feature', label: 'Fonctionnalités IA', hint: "appels LLM d'une fonctionnalité, pas un agent", items: agents.filter(a => a.kind === 'feature') },
    ].filter(g => g.items.length);
  }
  protected capPct(a: { cap: number; used_today: number }) { return a.cap ? Math.min(100, Math.round(100 * a.used_today / a.cap)) : 0; }

  protected setAi(on: boolean) {
    if (!on && !confirm("Désactiver l'IA pour toute l'organisation ? Les agents passeront en mode dégradé.")) { this.loadPolicy(); return; }
    this.api.saveGovernancePolicy({ ai_enabled: on }).subscribe({ next: p => { this.policy.set(p); this.ok(on ? 'IA réactivée.' : 'IA désactivée pour l’organisation.'); }, error: this.fail });
  }
  protected saveDefault() {
    this.api.saveGovernancePolicy({ default_daily_tokens: Math.max(0, this.defaultQuota() || 0) }).subscribe({
      next: p => { this.policy.set(p); this.defaultQuotaSaved.set(p.default_daily_tokens); this.ok('Quota par défaut enregistré.'); this.loadUsers(); }, error: this.fail });
  }
  protected setCap(agent: string, v: string) {
    this.api.saveGovernancePolicy({ agent_caps: { [agent]: Math.max(0, +v || 0) } }).subscribe({ next: p => { this.policy.set(p); this.ok('Plafond enregistré.'); }, error: this.fail });
  }
  protected toggle(u: GovernedUser, field: 'ai_blocked' | 'app_blocked', value: boolean) {
    if (field === 'app_blocked' && value && !confirm(`Suspendre l'accès de ${u.name} à DeliveryOps ?`)) { this.loadUsers(); return; }
    this.api.saveGovernanceUser(u.username, { [field]: value }).subscribe({ next: () => { this.ok('Enregistré.'); this.loadUsers(); }, error: e => { this.fail(e); this.loadUsers(); } });
  }
  protected setQuota(u: GovernedUser, v: string) {
    const body = v === '' ? { reset_quota: true } : { daily_token_quota: Math.max(0, +v || 0) };
    this.api.saveGovernanceUser(u.username, body).subscribe({ next: () => { this.ok('Quota enregistré.'); this.loadUsers(); }, error: this.fail });
  }
  protected resetQuota(u: GovernedUser) { this.setQuota(u, ''); }
  protected setNote(u: GovernedUser, note: string) { this.api.saveGovernanceUser(u.username, { note }).subscribe({ next: () => this.ok('Note enregistrée.'), error: this.fail }); }
}

import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';

import { Account, Api, PublicRule, Session } from '../api';
import { CompanionData } from '../companion-data';
import { DeviceSessions } from '../device-sessions';
import { PasswordForm } from '../password-form';
import { SignInMethodsCard } from '../sign-in-methods';
import { Theme, ThemeMode } from '../theme';
import { TwoFactor } from '../two-factor';

/** Self-service account page, in every auth mode: profile, security (password, sessions), access
 *  scope and personal AI consumption. Native accounts are fully managed here; Microsoft / Keycloak
 *  accounts are managed by their identity provider, so this links out to it. */
@Component({
  selector: 'app-account',
  imports: [DatePipe, DecimalPipe, PasswordForm, TwoFactor, DeviceSessions, CompanionData, SignInMethodsCard],
  template: `
    <div class="page acct">
      <div class="page-head">
        <div>
          <h1>Mon compte</h1>
          <p>Votre profil, la sécurité de votre compte, vos accès et votre consommation d'IA.</p>
        </div>
      </div>

      @if (error()) { <p class="small err">{{ error() }}</p> }

      @if (acc(); as a) {
        <div class="cols">
          <div class="col">
            <!-- Profile -->
            <section class="card">
              <div class="card-head"><h2>Profil</h2><span class="pill">{{ providerLabel() }}</span></div>
              <div class="profile">
                <div class="avatar" aria-hidden="true">{{ initials() }}</div>
                <div class="who">
                  @if (editing()) {
                    <form class="row" (submit)="$event.preventDefault(); saveName()">
                      <input class="name-in" [value]="nameDraft()" (input)="nameDraft.set($any($event.target).value)" maxlength="200" aria-label="Nom affiché" />
                      <button class="primary sm" type="submit" [disabled]="!nameDraft().trim()">Enregistrer</button>
                      <button class="ghost sm" type="button" (click)="editing.set(false)">Annuler</button>
                    </form>
                  } @else {
                    <div class="row"><strong class="name">{{ a.name }}</strong>
                      @if (a.can_edit_name) { <button class="ghost sm" (click)="startEdit(a)">Modifier</button> }</div>
                  }
                  <div class="muted">{{ a.email }}</div>
                </div>
              </div>
              <dl class="facts">
                <dt>Rôle</dt><dd>{{ a.role_label }}</dd>
                <dt>Membre depuis</dt><dd>{{ a.created_at ? (a.created_at | date: 'dd/MM/yyyy') : '—' }}</dd>
                <dt>Dernière connexion</dt><dd>{{ a.last_login_at ? (a.last_login_at | date: 'dd/MM/yyyy HH:mm') : '—' }}</dd>
              </dl>
              @if (!a.can_edit_name && a.account_url) {
                <p class="small faint note">Votre nom et votre photo viennent de votre compte {{ providerLabel() }}.
                  <a [href]="a.account_url" target="_blank" rel="noopener">Gérer mon compte {{ providerLabel() }} ↗</a></p>
              }
            </section>

            <!-- Security -->
            <section class="card">
              <div class="card-head"><h2>Sécurité</h2></div>
              @if (a.auth_mode === 'native') {
                <app-sign-in-methods (changed)="reload()" />
                @if (a.has_password) {
                  <div class="sep"></div>
                  <h3>Changer mon mot de passe</h3>
                  <app-password-form (done)="reload()" />
                  <div class="sep"></div>
                  <app-two-factor [enabled]="!!a.mfa_enabled" [recoveryLeft]="a.recovery_codes_left" [email]="a.email" (changed)="reload()" />
                }
                <div class="sep"></div>
                <app-device-sessions />
              } @else if (a.password_url) {
                <p class="muted">Votre mot de passe et votre double authentification sont gérés par {{ providerLabel() }} : DeliveryOps ne les connaît pas et ne les stocke pas.</p>
                <div class="row">
                  <a class="btn primary" [href]="a.password_url" target="_blank" rel="noopener">Changer mon mot de passe {{ providerLabel() }} ↗</a>
                  @if (a.account_url && a.account_url !== a.password_url) {
                    <a class="btn" [href]="a.account_url" target="_blank" rel="noopener">Sécurité du compte ↗</a>
                  }
                </div>
              } @else {
                <p class="muted">Mode démonstration : pas de mot de passe (changez de persona avec le sélecteur en haut).</p>
              }
            </section>
          </div>

          <div class="col">
            <!-- AI usage -->
            <section class="card">
              <div class="card-head"><h2>Mon utilisation de l'IA</h2>
                <span class="pill" [class.green]="a.ai.enabled" [class.red]="!a.ai.enabled">{{ a.ai.enabled ? 'Autorisée' : 'Bloquée' }}</span></div>
              <div class="usage">
                <div><div class="small muted">Aujourd'hui</div><strong>{{ a.ai.used_today | number }}</strong><span class="small faint"> tokens</span></div>
                <div><div class="small muted">7 derniers jours</div><strong>{{ a.ai.used_7d | number }}</strong><span class="small faint"> tokens</span></div>
              </div>
              @if (a.ai.daily_quota) {
                <div class="quota">
                  <div class="row between small"><span>Quota journalier</span><span>{{ quotaPct() }} % de {{ a.ai.daily_quota | number }}</span></div>
                  <div class="bar"><span [style.width.%]="quotaPct()" [class.hot]="quotaPct() >= 90"></span></div>
                  <p class="small faint">Le quota se réinitialise chaque jour à minuit (UTC).</p>
                </div>
              } @else {
                <p class="small faint">Aucun quota journalier ne s'applique à votre compte.</p>
              }
              @if (!a.ai.org_enabled) { <p class="small err">L'IA est désactivée pour toute l'organisation par un administrateur.</p> }
              @else if (a.ai.blocked) { <p class="small err">Votre accès à l'IA a été suspendu par un administrateur.</p> }
            </section>

            @if (a.permissions.includes('companion.use')) {
              <!-- What the companion holds about me -->
              <section class="card">
                <div class="card-head"><h2>Mes données</h2><span class="small faint">Compagnon</span></div>
                <app-companion-data />
              </section>
            }

            <!-- Access -->
            <section class="card">
              <div class="card-head"><h2>Mes accès</h2><span class="small faint">{{ a.permissions.length }} fonctionnalité(s)</span></div>
              <div class="small muted label">Clients visibles</div>
              <div class="chips">
                @if (a.clients.includes('*')) { <span class="pill accent">Tous les clients</span> }
                @else { @for (c of a.clients; track c) { <span class="pill">{{ c }}</span> } @empty { <span class="small faint">Aucun client attribué — contactez un administrateur.</span> } }
              </div>
              <details>
                <summary class="small">Voir mes permissions</summary>
                <div class="chips perms">@for (p of a.permissions; track p) { <code>{{ p }}</code> }</div>
              </details>
            </section>

            @if (rules().length) {
              <!-- Company rules in force: what the agents will refuse or warn about -->
              <section class="card">
                <div class="card-head"><h2>Règles de l'organisation</h2><span class="small faint">{{ rules().length }} règle(s)</span></div>
                <ul class="rules">
                  @for (r of rules(); track r.title) {
                    <li><span class="act" [class]="r.action">{{ r.action === 'block' ? 'Interdit' : r.action === 'warn' ? 'Vigilance' : 'Bonne pratique' }}</span>
                      <div><strong>{{ r.title }}</strong>@if (r.text) { <p class="small muted">{{ r.text }}</p> }</div></li>
                  }
                </ul>
              </section>
            }

            <!-- Preferences -->
            <section class="card">
              <div class="card-head"><h2>Préférences</h2></div>
              <label for="theme-sel">Thème de l'interface</label>
              <select id="theme-sel" class="theme-sel" [value]="theme.mode()" (change)="setTheme($any($event.target).value)">
                <option value="auto">Automatique (système)</option>
                <option value="light">Clair</option>
                <option value="dark">Sombre</option>
              </select>
            </section>
          </div>
        </div>
      } @else if (!error()) {
        <p class="muted"><span class="spin"></span> Chargement…</p>
      }
      @if (info()) { <p class="toast" role="status">{{ info() }}</p> }
    </div>
  `,
  styles: `
    .acct { display: flex; flex-direction: column; gap: 24px; max-width: 1100px; }
    .acct .page-head { margin-bottom: 0; }
    .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start; }
    .col { display: flex; flex-direction: column; gap: 24px; min-width: 0; }
    @media (max-width: 980px) { .cols { grid-template-columns: 1fr; } }
    .card { padding: 20px 22px; }
    .card-head { margin-bottom: 18px; }
    h3 { font-size: 13.5px; margin-bottom: 12px; }
    .profile { display: flex; gap: 16px; align-items: center; margin-bottom: 18px; }
    .avatar { width: 52px; height: 52px; border-radius: 50%; background: var(--accent-soft); color: var(--accent);
      display: grid; place-items: center; font-weight: 700; font-size: 18px; flex: none; }
    .who { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
    .name { font-size: 16px; }
    .name-in { width: 220px; }
    .facts { display: grid; grid-template-columns: max-content 1fr; gap: 10px 20px; margin: 0; font-size: 13px; }
    .facts dt { color: var(--muted); }
    .facts dd { margin: 0; }
    .note { margin-top: 16px; }
    .sep { height: 1px; background: var(--border); margin: 22px 0; }
    .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .between { justify-content: space-between; }
    .btn { text-decoration: none; }
    .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .usage { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 18px; }
    .usage strong { font-size: 22px; font-variant-numeric: tabular-nums; }
    .quota { display: flex; flex-direction: column; gap: 8px; }
    .bar { height: 8px; border-radius: 4px; background: var(--grey-soft); overflow: hidden; }
    .bar span { display: block; height: 100%; background: var(--accent); }
    .bar span.hot { background: var(--red); }
    .label { margin-bottom: 8px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    details { margin-top: 16px; }
    summary { cursor: pointer; color: var(--accent); }
    .perms { margin-top: 10px; }
    .perms code { background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px; padding: 2px 7px; }
    .theme-sel { max-width: 260px; }
    .rules { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
    .rules li { display: flex; gap: 10px; align-items: flex-start; }
    .rules p { margin: 3px 0 0; line-height: 1.45; }
    .act { flex: none; font-size: 10.5px; font-weight: 600; border-radius: 999px; padding: 2px 8px; margin-top: 1px; }
    .act.block { background: var(--red-soft); color: var(--red); }
    .act.warn { background: var(--amber-soft); color: var(--amber); }
    .act.guide { background: var(--accent-soft); color: var(--accent); }
    .err { color: var(--red); margin-top: 12px; }
    .toast { position: fixed; right: 24px; bottom: 24px; z-index: 1003; margin: 0; padding: 10px 16px; border-radius: 10px;
      background: var(--green-soft); color: var(--green); border: 1px solid color-mix(in srgb, var(--green) 35%, transparent);
      box-shadow: var(--shadow); font-size: 13px; font-weight: 500; }
  `,
})
export class AccountPage {
  private api = inject(Api);
  private session = inject(Session);
  protected theme = inject(Theme);

  protected acc = signal<Account | null>(null);
  protected error = signal('');
  protected info = signal('');
  protected editing = signal(false);
  protected nameDraft = signal('');

  protected initials = computed(() => (this.acc()?.name || this.acc()?.email || '?')
    .split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map(p => p[0]!.toUpperCase()).join(''));
  protected providerLabel = computed(() => ({ microsoft: 'Microsoft', keycloak: 'Keycloak', native: 'DeliveryOps', dev: 'Démo' } as Record<string, string>)[this.acc()?.auth_mode ?? ''] ?? '');
  protected quotaPct = computed(() => {
    const ai = this.acc()?.ai;
    return ai?.daily_quota ? Math.min(100, Math.round(100 * ai.used_today / ai.daily_quota)) : 0;
  });

  protected rules = signal<PublicRule[]>([]);

  constructor() {
    this.reload();
    this.api.publicRules().subscribe({ next: r => this.rules.set(r), error: () => this.rules.set([]) });
  }

  reload() {
    this.api.account().subscribe({
      next: a => { this.acc.set(a); this.error.set(''); },
      error: e => this.error.set(e?.error?.detail ?? 'Impossible de charger votre compte.'),
    });
  }

  protected startEdit(a: Account) { this.nameDraft.set(a.name); this.editing.set(true); }

  protected saveName() {
    this.api.updateAccount(this.nameDraft().trim()).subscribe({
      next: r => {
        this.editing.set(false);
        this.acc.update(a => a ? { ...a, name: r.name } : a);
        this.session.me.update(m => m ? { ...m, name: r.name } : m);
        this.flash('Nom mis à jour.');
      },
      error: e => this.error.set(e?.error?.detail ?? 'Enregistrement impossible.'),
    });
  }

  protected setTheme(mode: string) { this.theme.set(mode as ThemeMode); }

  private flash(msg: string) { this.info.set(msg); setTimeout(() => this.info.set(''), 3000); }
}

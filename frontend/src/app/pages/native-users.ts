import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';

import { Api, NativeUser, RoleInfo, Session } from '../api';
import { Auth } from '../auth';
import { SectionTabs } from '../section-tabs';

/** User administration for native (email/password) accounts: create, role & clients, temporary
 *  password, unlock after failed sign-ins, reset a lost second factor, disable, delete. */
@Component({
  selector: 'app-native-users',
  imports: [SectionTabs, DatePipe],
  template: `
    <app-section-tabs group="access" />
    <div class="page nu">
      <div class="page-head">
        <div>
          <h1>Utilisateurs</h1>
          <p>Comptes e-mail / mot de passe de DeliveryOps : rôle, clients visibles, sécurité (verrouillage, double authentification).</p>
        </div>
      </div>

      @if (secret(); as s) {
        <div class="card notice" role="status">
          <div>
            <strong>Mot de passe temporaire de {{ s.who }}</strong>
            <p class="small muted">Communiquez-le par un canal sûr : il ne sera plus affiché. La personne devra le changer à sa première connexion.</p>
          </div>
          <code translate="no">{{ s.password }}</code>
          <button class="sm" (click)="copy(s.password)">{{ copied() ? 'Copié ✓' : 'Copier' }}</button>
          <button class="sm ghost" (click)="secret.set(null)" aria-label="Fermer">✕</button>
        </div>
      }

      <section class="card">
        <div class="card-head"><h2>Nouvel utilisateur</h2></div>
        <form class="create" (submit)="$event.preventDefault(); create()">
          <div><label for="nu-email">E-mail</label><input id="nu-email" type="email" [value]="form().email" (input)="patch('email', $any($event.target).value)" placeholder="prenom.nom@entreprise.com" /></div>
          <div><label for="nu-name">Nom complet</label><input id="nu-name" [value]="form().name" (input)="patch('name', $any($event.target).value)" /></div>
          <div>
            <label for="nu-role">Rôle</label>
            <select id="nu-role" [value]="form().role" (change)="patch('role', $any($event.target).value)">
              @for (r of assignableRoles(); track r.id) { <option [value]="r.name">{{ r.label }}</option> }
            </select>
          </div>
          @if (!allClients(form().role)) {
            <div class="wide">
              <label>Clients visibles</label>
              <div class="chips">
                @for (c of clients(); track c.id) {
                  <label class="chip" [class.on]="form().clients.includes(c.id)">
                    <input type="checkbox" [checked]="form().clients.includes(c.id)" (change)="toggleClient(c.id)" /> {{ c.name }}
                  </label>
                } @empty { <span class="small faint">Aucun client.</span> }
              </div>
            </div>
          }
          <div class="actions wide">
            <span class="small faint">{{ auth.signInPassword()
              ? 'Un mot de passe temporaire est généré ; la personne le change à sa première connexion.'
              : 'La personne se connecte avec ' + providerNames() + ' en utilisant cette même adresse e-mail.' }}</span>
            <button class="primary" type="submit" [disabled]="!canCreate() || busy()">@if (busy()) { <span class="spin"></span> } Créer le compte</button>
          </div>
        </form>
      </section>

      <section class="card">
        <div class="card-head">
          <h2>Comptes ({{ users().length }})</h2>
          <input class="search" placeholder="Rechercher…" [value]="search()" (input)="search.set($any($event.target).value); load()" />
        </div>
        <div class="scroll">
          <table>
            <thead><tr><th>Utilisateur</th><th>Rôle</th><th>Clients</th><th>Dernière connexion</th><th>État</th><th></th></tr></thead>
            <tbody>
              @for (u of users(); track u.id) {
                <tr [class.off]="!u.active">
                  <td><strong translate="no">{{ u.name || u.email }}</strong><div class="small faint" translate="no">{{ u.email }}</div>
                    <div class="methods">@for (m of u.sign_in_methods ?? []; track m) { <span class="method">{{ methodLabel(m) }}</span> }</div></td>
                  <td>
                    <select [value]="u.role" [disabled]="isSelf(u)" (change)="update(u, { role: $any($event.target).value })">
                      @for (r of roles(); track r.id) { <option [value]="r.name">{{ r.label }}</option> }
                    </select>
                  </td>
                  <td class="small">{{ allClients(u.role) ? 'Tous' : (clientNames(u.clients) || '—') }}</td>
                  <td class="small faint">{{ u.last_login_at ? (u.last_login_at | date: 'dd/MM/yy HH:mm') : 'jamais' }}</td>
                  <td><div class="states">
                    <span class="pill" [class.green]="u.active" [class.red]="!u.active">{{ u.active ? 'Actif' : 'Désactivé' }}</span>
                    @if (u.role === 'unassigned') { <span class="pill amber">En attente d'un rôle</span> }
                    @if (u.locked) { <span class="pill red">Verrouillé</span> }
                    @if (u.mfa_enabled) { <span class="pill accent">2FA</span> }
                    @if (u.must_change_password) { <span class="pill amber">Mot de passe temporaire</span> }
                  </div></td>
                  <td><div class="acts">
                    @if (u.locked) { <button class="sm primary" (click)="unlock(u)">Déverrouiller</button> }
                    @if (auth.signInPassword()) { <button class="sm" (click)="resetPassword(u)">Nouveau mot de passe</button> }
                    @if (u.mfa_enabled && !isSelf(u)) { <button class="sm" (click)="resetMfa(u)">Réinitialiser la 2FA</button> }
                    @if (!isSelf(u)) {
                      <button class="sm ghost" (click)="update(u, { active: !u.active })">{{ u.active ? 'Désactiver' : 'Réactiver' }}</button>
                      <button class="sm ko" (click)="remove(u)">Supprimer</button>
                    } @else { <span class="small faint">vous</span> }
                  </div></td>
                </tr>
              } @empty { <tr><td class="empty" colspan="6">Aucun compte.</td></tr> }
            </tbody>
          </table>
        </div>
        @if (message()) { <p class="small msg" [class.err]="messageErr()">{{ message() }}</p> }
      </section>
    </div>
  `,
  styles: `
    .nu { display: flex; flex-direction: column; gap: 24px; }
    .nu .page-head { margin-bottom: 0; }
    .card { padding: 20px 22px; }
    .card-head { margin-bottom: 18px; }
    .notice { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; border-color: color-mix(in srgb, var(--accent) 40%, var(--border)); background: var(--accent-soft); }
    .notice > div { flex: 1; min-width: 240px; }
    .notice code { font-size: 16px; padding: 6px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; }
    .create { display: grid; grid-template-columns: 1.3fr 1fr 1fr; gap: 16px 20px; align-items: end; }
    .create .wide { grid-column: 1 / -1; }
    @media (max-width: 900px) { .create { grid-template-columns: 1fr; } }
    .chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .chip { display: inline-flex; align-items: center; gap: 6px; margin: 0; padding: 5px 10px; border: 1px solid var(--border); border-radius: 999px; cursor: pointer; color: var(--text); font-size: 13px; }
    .chip input { width: auto; }
    .chip.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
    .actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .search { max-width: 260px; }
    .scroll { overflow-x: auto; }
    table th { padding: 10px 14px; }
    table td { padding: 12px 14px; vertical-align: middle; }
    tr.off td { opacity: .55; }
    .states { display: flex; flex-wrap: wrap; gap: 4px; }
    .methods { display: flex; gap: 4px; margin-top: 4px; }
    .method { font-size: 10.5px; color: var(--muted); border: 1px solid var(--border); border-radius: 999px; padding: 0 7px; }
    .acts { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
    .msg { margin-top: 12px; color: var(--green); }
    .msg.err { color: var(--red); }
  `,
})
export class NativeUsersPage {
  private api = inject(Api);
  private session = inject(Session);
  protected auth = inject(Auth);
  protected providerNames = computed(() => this.auth.signInProviders().map(p => p.label).join(' ou ') || 'son fournisseur d\'identité');
  protected methodLabel(m: string) { return m === 'password' ? 'Mot de passe' : m === 'microsoft' ? 'Microsoft' : m === 'google' ? 'Google' : m; }
  protected users = signal<NativeUser[]>([]);
  protected roles = signal<RoleInfo[]>([]);
  protected clients = signal<{ id: string; name: string }[]>([]);
  protected search = signal('');
  protected busy = signal(false);
  protected message = signal('');
  protected messageErr = signal(false);
  protected secret = signal<{ who: string; password: string } | null>(null);
  protected copied = signal(false);
  protected form = signal({ email: '', name: '', role: 'consultant', clients: [] as string[] });

  protected assignableRoles = computed(() => this.roles().filter(r => r.name !== 'unassigned'));
  protected canCreate = computed(() => {
    const f = this.form();
    return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email.trim()) && !!f.name.trim() && (this.allClients(f.role) || f.clients.length > 0);
  });

  constructor() {
    this.load();
    this.api.roles().subscribe({ next: r => this.roles.set(r), error: () => this.roles.set([]) });
    this.api.clients().subscribe({ next: c => this.clients.set(c), error: () => this.clients.set([]) });
  }

  load() { this.api.nativeUsers(this.search()).subscribe({ next: u => this.users.set(u), error: e => this.fail(e) }); }

  protected allClients(role: string) { return role === 'admin' || role === 'director'; }
  protected isSelf(u: NativeUser) { return u.email === this.session.me()?.email; }
  protected clientNames(ids: string[]) {
    const names = new Map(this.clients().map(c => [c.id, c.name]));
    return ids.map(id => names.get(id) ?? id).join(', ');
  }

  protected patch(field: 'email' | 'name' | 'role', value: string) { this.form.update(f => ({ ...f, [field]: value })); }
  protected toggleClient(id: string) {
    this.form.update(f => ({ ...f, clients: f.clients.includes(id) ? f.clients.filter(c => c !== id) : [...f.clients, id] }));
  }

  protected create() {
    const f = this.form();
    this.busy.set(true);
    this.api.nativeCreateUser({ email: f.email.trim(), name: f.name.trim(), role: f.role, clients: this.allClients(f.role) ? [] : f.clients }).subscribe({
      next: u => {
        this.busy.set(false);
        this.form.set({ email: '', name: '', role: f.role, clients: [] });
        if (u.temporary_password) this.showSecret(u.name || u.email, u.temporary_password);
        this.ok(`Compte de ${u.name || u.email} créé.`);
        this.load();
      },
      error: e => { this.busy.set(false); this.fail(e); },
    });
  }

  protected update(u: NativeUser, body: { role?: string; active?: boolean }) {
    if (body.active === false && !confirm(`Désactiver le compte de ${u.name || u.email} ? Ses sessions seront fermées.`)) return;
    this.api.nativeUpdateUser(u.id, body).subscribe({ next: () => { this.ok('Enregistré.'); this.load(); }, error: e => { this.fail(e); this.load(); } });
  }

  protected resetPassword(u: NativeUser) {
    if (!confirm(`Générer un nouveau mot de passe temporaire pour ${u.name || u.email} ? L'actuel cessera de fonctionner.`)) return;
    this.api.nativeResetPassword(u.id).subscribe({
      next: r => { this.showSecret(u.name || u.email, r.temporary_password); this.load(); },
      error: e => this.fail(e),
    });
  }

  protected unlock(u: NativeUser) {
    this.api.nativeUnlock(u.id).subscribe({ next: () => { this.ok(`${u.name || u.email} peut de nouveau se connecter.`); this.load(); }, error: e => this.fail(e) });
  }

  protected resetMfa(u: NativeUser) {
    if (!confirm(`Désactiver la double authentification de ${u.name || u.email} (téléphone perdu) ? Il pourra la réactiver depuis Mon compte.`)) return;
    this.api.nativeResetMfa(u.id).subscribe({ next: () => { this.ok('Double authentification réinitialisée.'); this.load(); }, error: e => this.fail(e) });
  }

  protected remove(u: NativeUser) {
    if (!confirm(`Supprimer définitivement le compte de ${u.name || u.email} ?`)) return;
    this.api.nativeDeleteUser(u.id).subscribe({ next: () => { this.ok('Compte supprimé.'); this.load(); }, error: e => this.fail(e) });
  }

  protected copy(text: string) {
    navigator.clipboard?.writeText(text).then(() => { this.copied.set(true); setTimeout(() => this.copied.set(false), 2000); }).catch(() => {});
  }

  private showSecret(who: string, password: string) { this.copied.set(false); this.secret.set({ who, password }); }
  private ok(msg: string) { this.message.set(msg); this.messageErr.set(false); }
  private fail(e: any) {
    const d = e?.error?.detail;
    this.message.set(typeof d === 'string' ? d : Array.isArray(d) ? String(d[0]?.msg ?? 'Erreur').replace(/^Value error, /, '') : 'Action impossible.');
    this.messageErr.set(true);
  }
}

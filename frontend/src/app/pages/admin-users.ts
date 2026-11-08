import { Component, computed, inject, signal } from '@angular/core';

import { AdminUser, Api, Session } from '../api';

import { SectionTabs } from '../section-tabs';
const ROLE_LABEL: Record<string, string> = { director: 'Directeur delivery', delivery_manager: 'Delivery Manager', consultant: 'Consultant' };

@Component({
  selector: 'app-admin-users',
  imports: [SectionTabs],
  template: `
    <app-section-tabs group="access" />
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Utilisateurs</h1>
          <p>Comptes Keycloak, rôle DeliveryOps et clients accessibles. Réservé aux directeurs delivery.</p>
        </div>
      </div>

      @if (!configured()) {
        <div class="empty card">L'API d'administration Keycloak n'est pas configurée sur ce déploiement (KEYCLOAK_ADMIN_BASE_URL / KEYCLOAK_ADMIN_CLIENT_SECRET). Gérez les utilisateurs directement dans la console Keycloak.</div>
      } @else {
        <div class="card">
          <div class="card-head">
            <div><h2>Inviter un utilisateur</h2><p>Un e-mail d'activation (définition du mot de passe) est envoyé si la messagerie du realm est configurée.</p></div>
          </div>
          <form class="grid g3" (submit)="$event.preventDefault(); create()">
            <div><label for="username">Identifiant</label><input id="username" [value]="form().username" (input)="setField('username', $any($event.target).value)" placeholder="karim.haddad" /></div>
            <div><label for="email">E-mail</label><input id="email" type="email" [value]="form().email" (input)="setField('email', $any($event.target).value)" placeholder="karim@client.com" /></div>
            <div><label for="name">Nom complet</label><input id="name" [value]="form().name" (input)="setField('name', $any($event.target).value)" placeholder="Karim Haddad" /></div>
            <div>
              <label for="role">Rôle</label>
              <select id="role" [value]="form().role" (change)="setField('role', $any($event.target).value)">
                <option value="delivery_manager">Delivery Manager</option>
                <option value="consultant">Consultant</option>
                <option value="director">Directeur delivery</option>
              </select>
            </div>
            <div style="grid-column: span 2">
              <label for="clients">Clients accessibles (identifiants séparés par des virgules, laisser vide si Directeur)</label>
              <input id="clients" [value]="form().clientsText" (input)="setField('clientsText', $any($event.target).value)" placeholder="cli-atlas, cli-horizon" />
            </div>
            <div class="row" style="grid-column: 1 / -1; align-items: center">
              <button class="primary" [disabled]="!valid() || busy()">@if (busy()) { <span class="spin"></span> } Créer l'utilisateur</button>
              @if (error()) { <span class="small" style="color: var(--red)">{{ error() }}</span> }
            </div>
          </form>
        </div>

        <div class="card">
          <div class="card-head">
            <h2>Comptes ({{ users().length }})</h2>
            <input placeholder="Rechercher…" [value]="search()" (input)="search.set($any($event.target).value); load()" style="width: 220px" />
          </div>
          <table>
            <tr><th>Utilisateur</th><th>Rôle</th><th>Clients</th><th>Statut</th><th></th></tr>
            @for (u of users(); track u.id) {
              <tr [class.disabled-row]="!u.enabled">
                <td><strong>{{ u.name }}</strong><div class="small faint">{{ u.username }} · {{ u.email }}</div></td>
                <td>
                  <select [value]="u.role ?? ''" (change)="changeRole(u, $any($event.target).value)">
                    <option value="" disabled>—</option>
                    <option value="delivery_manager">Delivery Manager</option>
                    <option value="consultant">Consultant</option>
                    <option value="director">Directeur delivery</option>
                  </select>
                </td>
                <td class="small">{{ u.role === 'director' ? 'Tous' : (u.clients.join(', ') || '—') }}</td>
                <td><span class="pill" [class.green]="u.enabled" [class.red]="!u.enabled">{{ u.enabled ? 'Actif' : 'Désactivé' }}</span></td>
                <td>
                  <div class="row">
                    <button class="sm" (click)="resetPassword(u)">Réinitialiser mot de passe</button>
                    <button class="sm ghost" (click)="toggleEnabled(u)">{{ u.enabled ? 'Désactiver' : 'Réactiver' }}</button>
                    <button class="sm ko" (click)="remove(u)">Supprimer</button>
                  </div>
                </td>
              </tr>
            } @empty { <tr><td class="empty" colspan="5">Aucun utilisateur.</td></tr> }
          </table>
          @if (message()) { <p class="small muted" style="margin-top: 8px">{{ message() }}</p> }
        </div>
      }
    </div>
  `,
  styles: `
    .grid.g3 { gap: 12px 16px; align-items: end; }
    tr.disabled-row { opacity: .55; }
    th, td { padding: 10px 12px; vertical-align: middle; }
    td .row { justify-content: flex-end; flex-wrap: nowrap; }
  `,
})
export class AdminUsersPage {
  private api = inject(Api);
  protected session = inject(Session);
  protected configured = signal(false);
  protected users = signal<AdminUser[]>([]);
  protected search = signal('');
  protected busy = signal(false);
  protected error = signal('');
  protected message = signal('');
  protected form = signal({ username: '', email: '', name: '', role: 'delivery_manager', clientsText: '' });
  protected roleLabel = ROLE_LABEL;
  protected valid = computed(() => {
    const f = this.form();
    return !!f.username.trim() && !!f.email.trim() && !!f.name.trim() && (f.role === 'director' || !!f.clientsText.trim());
  });

  constructor() {
    this.api.adminStatus().subscribe({
      next: s => { this.configured.set(s.configured); if (s.configured) this.load(); },
      error: () => this.configured.set(false),
    });
  }

  setField<K extends keyof ReturnType<typeof this.form>>(key: K, value: string) {
    this.form.update(f => ({ ...f, [key]: value }));
  }

  load() { this.api.adminUsers(this.search()).subscribe(u => this.users.set(u)); }

  create() {
    const f = this.form();
    const clients = f.role === 'director' ? [] : f.clientsText.split(',').map(c => c.trim()).filter(Boolean);
    this.busy.set(true);
    this.error.set('');
    this.api.adminCreateUser({ username: f.username.trim(), email: f.email.trim(), name: f.name.trim(), role: f.role, clients, send_invite: true }).subscribe({
      next: () => { this.busy.set(false); this.form.set({ username: '', email: '', name: '', role: 'delivery_manager', clientsText: '' }); this.load(); },
      error: e => { this.busy.set(false); this.error.set(e.error?.detail ?? 'Erreur lors de la création'); },
    });
  }

  changeRole(u: AdminUser, role: string) {
    this.api.adminUpdateUser(u.id, { role }).subscribe({ next: () => this.load(), error: e => this.message.set(e.error?.detail ?? 'Erreur') });
  }

  toggleEnabled(u: AdminUser) {
    this.api.adminUpdateUser(u.id, { enabled: !u.enabled }).subscribe({ next: () => this.load(), error: e => this.message.set(e.error?.detail ?? 'Erreur') });
  }

  resetPassword(u: AdminUser) {
    this.api.adminResetPassword(u.id).subscribe({
      next: () => this.message.set(`E-mail de réinitialisation envoyé à ${u.email} (si la messagerie du realm est configurée).`),
      error: e => this.message.set(e.error?.detail ?? 'Erreur'),
    });
  }

  remove(u: AdminUser) {
    if (!confirm(`Supprimer le compte de ${u.name} ?`)) return;
    this.api.adminDeleteUser(u.id).subscribe({ next: () => this.load(), error: e => this.message.set(e.error?.detail ?? 'Erreur') });
  }
}

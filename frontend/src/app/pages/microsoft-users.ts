import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';

import { Api, MicrosoftAdminUser, RoleInfo, Session } from '../api';

import { SectionTabs } from '../section-tabs';
/** Admin page for auth_mode=microsoft: people don't get created here — they self-provision by
 *  signing in with their own Microsoft account — an admin only assigns their role (or disables
 *  them) afterward. Everyone starts at "unassigned" (zero permissions) except the very first
 *  person ever to sign in, who is bootstrapped as admin. */
@Component({
  selector: 'app-microsoft-users',
  imports: [SectionTabs, DatePipe],
  template: `
    <app-section-tabs group="access" />
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Utilisateurs</h1>
          <p>Comptes Microsoft connectés à DeliveryOps. Chaque nouvelle personne démarre <strong>sans aucun accès</strong> jusqu'à ce qu'un administrateur lui attribue un rôle.</p>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h2>Comptes ({{ users().length }})</h2>
          <input placeholder="Rechercher…" [value]="search()" (input)="search.set($any($event.target).value); load()" style="width: 220px" />
        </div>
        <table>
          <tr><th>Utilisateur</th><th>Rôle</th><th>Clients</th><th>Dernière connexion</th><th>Statut</th><th></th></tr>
          @for (u of users(); track u.id) {
            <tr [class.disabled-row]="!u.active">
              <td><strong>{{ u.name || u.email }}</strong><div class="small faint">{{ u.email }}</div></td>
              <td>
                <select [value]="u.role" [disabled]="isSelf(u)" (change)="changeRole(u, $any($event.target).value)">
                  @for (r of roles(); track r.id) { <option [value]="r.name">{{ r.label }}</option> }
                </select>
                @if (u.role === 'unassigned') { <div class="small" style="color: var(--amber)">En attente</div> }
              </td>
              <td class="small">{{ u.role === 'admin' || u.role === 'director' ? 'Tous' : (u.clients.join(', ') || '—') }}</td>
              <td class="small faint">{{ u.last_login_at ? (u.last_login_at | date: 'dd/MM/yy HH:mm') : '—' }}</td>
              <td><span class="pill" [class.green]="u.active" [class.red]="!u.active">{{ u.active ? 'Actif' : 'Désactivé' }}</span></td>
              <td class="acts">
                @if (!isSelf(u)) {
                  <button class="sm ghost" (click)="toggleActive(u)">{{ u.active ? 'Désactiver' : 'Réactiver' }}</button>
                } @else {
                  <span class="small faint">vous</span>
                }
              </td>
            </tr>
          } @empty { <tr><td class="empty" colspan="6">Aucun utilisateur pour l'instant — les comptes apparaissent ici dès leur première connexion.</td></tr> }
        </table>
        @if (message()) { <p class="small" [style.color]="messageErr() ? 'var(--red)' : 'var(--green)'" style="margin-top: 8px">{{ message() }}</p> }
      </div>
    </div>
  `,
  styles: `
    tr.disabled-row { opacity: .55; }
    th, td { padding: 10px 12px; vertical-align: middle; }
    td select { width: auto; min-width: 180px; }
    td.acts { text-align: end; white-space: nowrap; }
  `,
})
export class MicrosoftUsersPage {
  private api = inject(Api);
  protected session = inject(Session);
  protected users = signal<MicrosoftAdminUser[]>([]);
  protected roles = signal<RoleInfo[]>([]);
  protected search = signal('');
  protected message = signal('');
  protected messageErr = signal(false);

  constructor() {
    this.load();
    this.api.roles().subscribe({ next: r => this.roles.set(r), error: () => this.roles.set([]) });
  }

  load() { this.api.microsoftUsers(this.search()).subscribe(u => this.users.set(u)); }

  isSelf(u: MicrosoftAdminUser): boolean { return u.email === this.session.me()?.email; }

  changeRole(u: MicrosoftAdminUser, role: string) {
    this.api.updateMicrosoftUser(u.id, { role }).subscribe({
      next: () => { this.setMessage(`Rôle de ${u.name || u.email} mis à jour.`, false); this.load(); },
      error: e => this.setMessage(e.error?.detail ?? 'Erreur', true),
    });
  }

  toggleActive(u: MicrosoftAdminUser) {
    this.api.updateMicrosoftUser(u.id, { active: !u.active }).subscribe({
      next: () => this.load(),
      error: e => this.setMessage(e.error?.detail ?? 'Erreur', true),
    });
  }

  private setMessage(msg: string, err: boolean) { this.message.set(msg); this.messageErr.set(err); }
}

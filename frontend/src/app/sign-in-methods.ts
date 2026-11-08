import { DatePipe } from '@angular/common';
import { Component, inject, output, signal } from '@angular/core';

import { Api, SignInMethods } from './api';
import { Auth } from './auth';
import { takeSsoError } from './sso-errors';

/** Mon compte → how I sign in: my password and the Microsoft / Google accounts linked to this
 *  DeliveryOps account; link another one (round trip through the provider) or remove one. */
@Component({
  selector: 'app-sign-in-methods',
  imports: [DatePipe],
  template: `
    <h3>Méthodes de connexion</h3>
    @if (data(); as d) {
      <ul class="methods">
        @if (d.password.available || d.password.set) {
          <li>
            <span class="logo pw" aria-hidden="true">••</span>
            <div class="who"><strong>Mot de passe</strong>
              <span class="small faint">{{ d.password.set ? 'Défini' : 'Aucun — connectez-vous avec un compte lié' }}</span></div>
          </li>
        }
        @for (i of d.identities; track i.id) {
          <li>
            <span class="logo" [class]="i.provider" aria-hidden="true"></span>
            <div class="who"><strong>{{ i.label }}</strong>
              <span class="small faint" translate="no">{{ i.email }}</span>
              <span class="small faint">Lié le {{ i.created_at | date: 'dd/MM/yyyy' }}@if (i.last_used_at) { · utilisé le {{ i.last_used_at | date: 'dd/MM/yyyy' }} }</span></div>
            <button class="sm ghost" (click)="unlink(i.id, i.label)" [disabled]="busy()">Retirer</button>
          </li>
        }
      </ul>
      @if (d.can_link.length) {
        <div class="row">
          @for (p of d.can_link; track p.provider) {
            <button class="sm" (click)="link(p.provider)" [disabled]="busy()">Lier mon compte {{ p.label }}</button>
          }
        </div>
      }
    }
    @if (info()) { <p class="small okmsg">{{ info() }}</p> }
    @if (error()) { <p class="small err" role="alert">{{ error() }}</p> }
  `,
  styles: `
    h3 { font-size: 13.5px; margin: 0 0 10px; }
    .methods { list-style: none; margin: 0 0 12px; padding: 0; display: flex; flex-direction: column; }
    .methods li { display: flex; align-items: center; gap: 12px; padding: 10px 4px; border-bottom: 1px solid var(--border); }
    .methods li:last-child { border-bottom: 0; }
    .who { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
    .logo { width: 22px; height: 22px; flex: none; border-radius: 5px; background-size: 16px; background-position: center; background-repeat: no-repeat; }
    .logo.pw { display: grid; place-items: center; background: var(--surface-2); border: 1px solid var(--border); color: var(--muted); font-size: 10px; letter-spacing: -1px; }
    .logo.microsoft { background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 21 21'%3E%3Crect x='1' y='1' width='9' height='9' fill='%23f25022'/%3E%3Crect x='11' y='1' width='9' height='9' fill='%237fba00'/%3E%3Crect x='1' y='11' width='9' height='9' fill='%2300a4ef'/%3E%3Crect x='11' y='11' width='9' height='9' fill='%23ffb900'/%3E%3C/svg%3E"); }
    .logo.google { background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23EA4335' d='M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.8 6C12.4 13.6 17.7 9.5 24 9.5z'/%3E%3Cpath fill='%234285F4' d='M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.5 5.8c4.4-4 6.8-10 6.8-17.2z'/%3E%3Cpath fill='%23FBBC05' d='M10.5 28.7c-.5-1.4-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.8-6z'/%3E%3Cpath fill='%2334A853' d='M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.8 2.3-8.4 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.8 6C6.6 42.6 14.6 48 24 48z'/%3E%3C/svg%3E"); }
    .row { display: flex; gap: 8px; flex-wrap: wrap; }
    .okmsg { color: var(--green); }
    .err { color: var(--red); }
  `,
})
export class SignInMethodsCard {
  private api = inject(Api);
  private auth = inject(Auth);
  readonly changed = output<void>();
  protected data = signal<SignInMethods | null>(null);
  protected busy = signal(false);
  protected info = signal('');
  protected error = signal('');

  constructor() {
    // Back from "Lier mon compte …": success (?linked=google) or a refusal (?auth_error=…)
    const url = new URL(window.location.href);
    const linked = url.searchParams.get('linked');
    if (linked) {
      url.searchParams.delete('linked');
      history.replaceState(null, '', url.pathname + url.search);
      this.info.set(`Compte ${linked === 'google' ? 'Google' : 'Microsoft'} lié : vous pouvez maintenant l'utiliser pour vous connecter.`);
    }
    const failed = takeSsoError();
    if (failed) this.error.set(failed);
    this.load();
  }

  private load() { this.api.signInMethods().subscribe({ next: d => this.data.set(d), error: () => this.data.set(null) }); }

  protected link(provider: string) {
    this.busy.set(true);
    this.auth.startSso(provider, { next: '/account', link: true });
  }

  protected unlink(id: string, label: string) {
    if (!confirm(`Ne plus pouvoir vous connecter avec ${label} ?`)) return;
    this.busy.set(true);
    this.error.set('');
    this.api.unlinkSignInMethod(id).subscribe({
      next: () => { this.busy.set(false); this.info.set(`${label} retiré.`); this.load(); this.changed.emit(); },
      error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Action impossible.'); },
    });
  }
}

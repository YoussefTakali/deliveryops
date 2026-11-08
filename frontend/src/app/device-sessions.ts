import { DatePipe } from '@angular/common';
import { Component, inject, output, signal } from '@angular/core';

import { Api, DeviceSession } from './api';

/** Where the account is signed in (native accounts): one row per browser/device, this one flagged;
 *  sign out any other device, or all of them at once. */
@Component({
  selector: 'app-device-sessions',
  imports: [DatePipe],
  template: `
    <div class="row between">
      <h3>Appareils connectés</h3>
      @if (others() > 0) { <button class="sm" (click)="signOutOthers()" [disabled]="busy()">Déconnecter les autres ({{ others() }})</button> }
    </div>
    <ul class="devices">
      @for (s of sessions(); track s.id) {
        <li>
          <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4.5" width="18" height="12" rx="1.5"/><path d="M8.5 20h7M12 16.5V20"/></svg>
          <div class="who">
            <strong translate="no">{{ s.device }}</strong>
            <span class="small faint">
              @if (s.current) { <span class="pill green">Cet appareil</span> }
              Actif {{ s.last_active_at | date: 'dd/MM/yy HH:mm' }} · connecté le {{ s.signed_in_at | date: 'dd/MM/yy' }}@if (s.ip) { · <span translate="no">{{ s.ip }}</span> }
            </span>
          </div>
          @if (!s.current) { <button class="sm ghost" (click)="revoke(s)" [disabled]="busy()">Déconnecter</button> }
        </li>
      } @empty { <li class="small faint">Aucune session active.</li> }
    </ul>
    @if (error()) { <p class="small err">{{ error() }}</p> }
  `,
  styles: `
    h3 { font-size: 13.5px; margin: 0; }
    .row { display: flex; align-items: center; gap: 10px; }
    .between { justify-content: space-between; margin-bottom: 10px; }
    .devices { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
    .devices li { display: flex; align-items: center; gap: 12px; padding: 10px 4px; border-bottom: 1px solid var(--border); }
    .devices li:last-child { border-bottom: 0; }
    .ic { width: 22px; height: 22px; flex: none; fill: none; stroke: var(--muted); stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
    .who { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
    .who .pill { margin-inline-end: 4px; }
    .err { color: var(--red); }
  `,
})
export class DeviceSessions {
  private api = inject(Api);
  readonly changed = output<void>();
  protected sessions = signal<DeviceSession[]>([]);
  protected busy = signal(false);
  protected error = signal('');
  protected others = () => this.sessions().filter(s => !s.current).length;

  constructor() { this.load(); }

  load() { this.api.authSessions().subscribe({ next: s => this.sessions.set(s), error: () => this.sessions.set([]) }); }

  protected revoke(s: DeviceSession) {
    this.busy.set(true);
    this.api.authRevokeSession(s.id).subscribe({
      next: () => { this.busy.set(false); this.load(); this.changed.emit(); },
      error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Action impossible.'); },
    });
  }

  protected signOutOthers() {
    if (!confirm('Déconnecter tous vos autres navigateurs et appareils ?')) return;
    this.busy.set(true);
    this.api.authLogoutOthers().subscribe({
      next: () => { this.busy.set(false); this.load(); this.changed.emit(); },
      error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Action impossible.'); },
    });
  }
}

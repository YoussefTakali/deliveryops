import { Component, inject, input, output, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { Api } from './api';

type Step = 'idle' | 'password' | 'scan' | 'codes' | 'disable' | 'regenerate';

/** Two-factor authentication for native accounts (Mon compte): enrol with an authenticator app (QR code
 *  + first code), save the single-use recovery codes, regenerate them, or turn it off. */
@Component({
  selector: 'app-two-factor',
  template: `
    <div class="tf">
      <div class="row between">
        <div>
          <h3>Double authentification</h3>
          <p class="small muted">{{ enabled()
            ? 'Activée : un code de votre téléphone est demandé après le mot de passe.'
            : 'Protégez votre compte avec une application d\\'authentification (Microsoft Authenticator, Google Authenticator…).' }}</p>
        </div>
        <span class="pill" [class.green]="enabled()">{{ enabled() ? 'Activée' : 'Désactivée' }}</span>
      </div>

      @switch (step()) {
        @case ('idle') {
          <div class="row">
            @if (!enabled()) { <button class="sm primary" (click)="go('password')">Activer</button> }
            @else {
              @if (recoveryLeft() !== null) { <span class="small faint">{{ recoveryLeft() }} code(s) de secours restant(s)</span> }
              <span class="spacer"></span>
              <button class="sm" (click)="go('regenerate')">Nouveaux codes de secours</button>
              <button class="sm ghost" (click)="go('disable')">Désactiver</button>
            }
          </div>
        }
        @case ('password') {
          <form class="stack-sm" (submit)="$event.preventDefault(); start()">
            <label for="tf-pw">Confirmez votre mot de passe</label>
            <div class="row"><input id="tf-pw" type="password" autocomplete="current-password" [value]="password()" (input)="password.set($any($event.target).value)" />
              <button class="sm primary" type="submit" [disabled]="!password() || busy()">Continuer</button>
              <button class="sm ghost" type="button" (click)="go('idle')">Annuler</button></div>
          </form>
        }
        @case ('scan') {
          <div class="scan">
            <img [src]="qr()" width="180" height="180" alt="QR code à scanner avec votre application d'authentification" />
            <div class="stack-sm">
              <p class="small">1. Dans votre application d'authentification, ajoutez un compte et scannez ce code.</p>
              <p class="small faint">Pas de caméra ? Saisissez la clé : <code translate="no">{{ secretGrouped() }}</code></p>
              <label for="tf-code">2. Saisissez le code à 6 chiffres affiché</label>
              <form class="row" (submit)="$event.preventDefault(); enable()">
                <input id="tf-code" class="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" [value]="code()" (input)="code.set($any($event.target).value)" />
                <button class="sm primary" type="submit" [disabled]="code().trim().length < 6 || busy()">Activer</button>
                <button class="sm ghost" type="button" (click)="go('idle')">Annuler</button>
              </form>
            </div>
          </div>
        }
        @case ('codes') {
          <div class="codes-box">
            <p class="small"><strong>Codes de secours</strong> — chacun fonctionne une seule fois si vous n'avez plus votre téléphone.
              Gardez-les en lieu sûr : ils ne seront plus affichés.</p>
            <ul class="codes" translate="no">@for (c of codes(); track c) { <li><code>{{ c }}</code></li> }</ul>
            <div class="row">
              <button class="sm" (click)="copyCodes()">{{ copied() ? 'Copiés ✓' : 'Copier' }}</button>
              <button class="sm" (click)="downloadCodes()">Télécharger (.txt)</button>
              <span class="spacer"></span>
              <button class="sm primary" (click)="finish()">J'ai enregistré mes codes</button>
            </div>
          </div>
        }
        @case ('disable') {
          <form class="stack-sm" (submit)="$event.preventDefault(); disable()">
            <p class="small muted">Pour désactiver, confirmez votre mot de passe et un code actuel (ou un code de secours).</p>
            <div class="row">
              <input type="password" placeholder="Mot de passe" autocomplete="current-password" aria-label="Mot de passe" [value]="password()" (input)="password.set($any($event.target).value)" />
              <input class="code" placeholder="Code" autocomplete="one-time-code" aria-label="Code" [value]="code()" (input)="code.set($any($event.target).value)" />
            </div>
            <div class="row"><button class="sm ko" type="submit" [disabled]="!password() || !code() || busy()">Désactiver la double authentification</button>
              <button class="sm ghost" type="button" (click)="go('idle')">Annuler</button></div>
          </form>
        }
        @case ('regenerate') {
          <form class="row" (submit)="$event.preventDefault(); regenerate()">
            <input class="code" placeholder="Code actuel" inputmode="numeric" autocomplete="one-time-code" aria-label="Code actuel" [value]="code()" (input)="code.set($any($event.target).value)" />
            <button class="sm primary" type="submit" [disabled]="!code() || busy()">Générer de nouveaux codes</button>
            <button class="sm ghost" type="button" (click)="go('idle')">Annuler</button>
          </form>
        }
      }
      @if (error()) { <p class="small err" role="alert">{{ error() }}</p> }
    </div>
  `,
  styles: `
    .tf { display: flex; flex-direction: column; gap: 14px; }
    h3 { font-size: 13.5px; margin-bottom: 4px; }
    .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .between { justify-content: space-between; align-items: flex-start; flex-wrap: nowrap; }
    .spacer { flex: 1; }
    .stack-sm { display: flex; flex-direction: column; gap: 10px; }
    .stack-sm label { margin: 0; }
    .row input { flex: 1; min-width: 140px; }
    .code { max-width: 150px; letter-spacing: .2em; font-variant-numeric: tabular-nums; }
    .scan { display: flex; gap: 20px; align-items: flex-start; flex-wrap: wrap; }
    .scan img { border-radius: 10px; border: 1px solid var(--border); background: #fff; flex: none; }
    .scan .stack-sm { flex: 1; min-width: 220px; }
    .codes-box { border: 1px solid var(--border); border-radius: 10px; padding: 14px; background: var(--surface-2); display: flex; flex-direction: column; gap: 12px; }
    .codes { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 6px; }
    .codes code { font-size: 13px; }
    .err { color: var(--red); margin: 0; }
  `,
})
export class TwoFactor {
  private api = inject(Api);
  readonly enabled = input(false);
  readonly recoveryLeft = input<number | null>(null);
  readonly email = input('');
  readonly changed = output<void>();

  protected step = signal<Step>('idle');
  protected password = signal('');
  protected code = signal('');
  protected qr = signal('');
  protected secret = signal('');
  protected codes = signal<string[]>([]);
  protected busy = signal(false);
  protected error = signal('');
  protected copied = signal(false);

  protected secretGrouped() { return this.secret().replace(/(.{4})/g, '$1 ').trim(); }

  protected go(step: Step) { this.step.set(step); this.password.set(''); this.code.set(''); this.error.set(''); }

  protected async start() {
    await this.run(async () => {
      const r = await firstValueFrom(this.api.mfaSetup(this.password()));
      this.qr.set(r.qr); this.secret.set(r.secret);
      this.password.set('');
      this.step.set('scan');
    });
  }

  protected async enable() {
    await this.run(async () => {
      const r = await firstValueFrom(this.api.mfaEnable(this.code().trim()));
      this.codes.set(r.recovery_codes);
      this.secret.set(''); this.qr.set('');
      this.step.set('codes');
      this.changed.emit();
    });
  }

  protected async disable() {
    await this.run(async () => {
      await firstValueFrom(this.api.mfaDisable(this.password(), this.code().trim()));
      this.go('idle');
      this.changed.emit();
    });
  }

  protected async regenerate() {
    await this.run(async () => {
      const r = await firstValueFrom(this.api.mfaNewRecoveryCodes(this.code().trim()));
      this.codes.set(r.recovery_codes);
      this.step.set('codes');
      this.changed.emit();
    });
  }

  protected finish() { this.codes.set([]); this.go('idle'); }

  private codesText() {
    return `Codes de secours DeliveryOps${this.email() ? ' — ' + this.email() : ''}\nChaque code ne fonctionne qu'une fois.\n\n${this.codes().join('\n')}\n`;
  }

  protected copyCodes() {
    navigator.clipboard?.writeText(this.codesText()).then(() => { this.copied.set(true); setTimeout(() => this.copied.set(false), 2000); }).catch(() => {});
  }

  protected downloadCodes() {
    const url = URL.createObjectURL(new Blob([this.codesText()], { type: 'text/plain' }));
    const a = document.createElement('a');
    a.href = url; a.download = 'deliveryops-codes-de-secours.txt'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private async run(action: () => Promise<void>) {
    this.busy.set(true);
    this.error.set('');
    try { await action(); }
    catch (e: any) { this.error.set(typeof e?.error?.detail === 'string' ? e.error.detail : 'Action impossible.'); }
    finally { this.busy.set(false); }
  }
}

import { Component, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { Api } from './api';
import { Auth } from './auth';
import { takeSsoError } from './sso-errors';

type Step = 'login' | 'mfa' | 'forgot' | 'forgot-sent' | 'reset';

/** Sign-in gate for native (email/password) accounts: password, then the two-factor code when the
 *  account has it, plus "mot de passe oublié" (e-mailed link) and the page that link opens. */
@Component({
  selector: 'app-native-sign-in',
  template: `
    @switch (step()) {
      @case ('login') {
        <h1>Connexion</h1>
        @if (auth.signInProviders().length) {
          <div class="providers">
            @for (p of auth.signInProviders(); track p.id) {
              <button class="provider" type="button" (click)="sso(p.id)" [disabled]="busy()">
                @if (p.id === 'microsoft') {
                  <svg width="18" height="18" viewBox="0 0 21 21" aria-hidden="true"><rect x="1" y="1" width="9" height="9" fill="#f25022"/><rect x="11" y="1" width="9" height="9" fill="#7fba00"/><rect x="1" y="11" width="9" height="9" fill="#00a4ef"/><rect x="11" y="11" width="9" height="9" fill="#ffb900"/></svg>
                } @else {
                  <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.8 6C12.4 13.6 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.5 5.8c4.4-4 6.8-10 6.8-17.2z"/><path fill="#FBBC05" d="M10.5 28.7c-.5-1.4-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.8-6z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.8 2.3-8.4 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.8 6C6.6 42.6 14.6 48 24 48z"/></svg>
                }
                <span>Continuer avec {{ p.label }}</span>
              </button>
            }
          </div>
          @if (auth.signInPassword()) { <div class="or"><span>ou</span></div> }
        }
        @if (error() && !auth.signInPassword()) { <p class="small gerr" role="alert">{{ error() }}</p> }
        @if (auth.signInPassword()) {
        <form class="gform" (submit)="$event.preventDefault(); login()">
          <div><label for="login-email">E-mail</label>
            <input id="login-email" type="email" autocomplete="username" required [value]="email()" (input)="email.set($any($event.target).value)" /></div>
          <div><label for="login-pw">Mot de passe</label>
            <input id="login-pw" type="password" autocomplete="current-password" required [value]="password()" (input)="password.set($any($event.target).value)" /></div>
          @if (error()) { <p class="small gerr" role="alert">{{ error() }}</p> }
          <button class="primary gbtn" type="submit" [disabled]="busy() || !email() || !password()">
            @if (busy()) { <span class="spin"></span> } Se connecter
          </button>
        </form>
        @if (auth.passwordResetByEmail()) {
          <button class="linkbtn" type="button" (click)="go('forgot')">Mot de passe oublié ?</button>
        } @else {
          <p class="small faint gnote">Mot de passe oublié ? Demandez à un administrateur de le réinitialiser.</p>
        }
        }
      }

      @case ('mfa') {
        <h1>Vérification en deux étapes</h1>
        <p class="muted">{{ useRecovery() ? 'Saisissez un de vos codes de secours.' : 'Saisissez le code à 6 chiffres affiché par votre application d\\'authentification.' }}</p>
        <form class="gform" (submit)="$event.preventDefault(); verify()">
          <div>
            <label for="mfa-code">{{ useRecovery() ? 'Code de secours' : 'Code' }}</label>
            <input id="mfa-code" class="code" [attr.inputmode]="useRecovery() ? 'text' : 'numeric'" autocomplete="one-time-code" required
                   [attr.maxlength]="useRecovery() ? 20 : 6" [value]="code()" (input)="code.set($any($event.target).value)" />
          </div>
          @if (error()) { <p class="small gerr" role="alert">{{ error() }}</p> }
          <button class="primary gbtn" type="submit" [disabled]="busy() || !code().trim()">@if (busy()) { <span class="spin"></span> } Vérifier</button>
        </form>
        <button class="linkbtn" type="button" (click)="useRecovery.set(!useRecovery()); code.set(''); error.set('')">
          {{ useRecovery() ? 'Utiliser l\\'application d\\'authentification' : 'Téléphone indisponible ? Utiliser un code de secours' }}</button>
        <button class="linkbtn" type="button" (click)="go('login')">← Retour</button>
      }

      @case ('forgot') {
        <h1>Mot de passe oublié</h1>
        <p class="muted">Indiquez l'e-mail de votre compte : vous recevrez un lien pour choisir un nouveau mot de passe.</p>
        <form class="gform" (submit)="$event.preventDefault(); forgot()">
          <div><label for="forgot-email">E-mail</label>
            <input id="forgot-email" type="email" autocomplete="username" required [value]="email()" (input)="email.set($any($event.target).value)" /></div>
          @if (error()) { <p class="small gerr" role="alert">{{ error() }}</p> }
          <button class="primary gbtn" type="submit" [disabled]="busy() || !email()">@if (busy()) { <span class="spin"></span> } Envoyer le lien</button>
        </form>
        <button class="linkbtn" type="button" (click)="go('login')">← Retour à la connexion</button>
      }

      @case ('forgot-sent') {
        <h1>Vérifiez vos e-mails</h1>
        <p class="muted">{{ info() }}</p>
        <p class="small faint">Le lien est valable 30 minutes. Pensez à regarder dans les courriers indésirables.</p>
        <button class="gbtn" type="button" (click)="go('login')">Retour à la connexion</button>
      }

      @case ('reset') {
        <h1>Nouveau mot de passe</h1>
        <form class="gform" (submit)="$event.preventDefault(); reset()">
          <div><label for="reset-pw">Nouveau mot de passe</label>
            <input id="reset-pw" type="password" autocomplete="new-password" required minlength="8" [value]="password()" (input)="password.set($any($event.target).value)" /></div>
          <div><label for="reset-pw2">Confirmer</label>
            <input id="reset-pw2" type="password" autocomplete="new-password" required [value]="confirm()" (input)="confirm.set($any($event.target).value)" /></div>
          @if (error()) { <p class="small gerr" role="alert">{{ error() }}</p> }
          <button class="primary gbtn" type="submit" [disabled]="busy() || password().length < 8 || password() !== confirm()">
            @if (busy()) { <span class="spin"></span> } Enregistrer et me connecter
          </button>
          @if (password() && password().length < 8) { <p class="small faint">Au moins 8 caractères.</p> }
          @else if (confirm() && password() !== confirm()) { <p class="small faint">Les deux mots de passe ne correspondent pas.</p> }
        </form>
        <button class="linkbtn" type="button" (click)="go('login')">← Retour à la connexion</button>
      }
    }
  `,
  styles: `
    :host { display: block; }
    h1 { font-size: 18px; margin-bottom: 10px; }
    .muted { margin-bottom: 16px; line-height: 1.5; }
    .gform { display: flex; flex-direction: column; gap: 14px; text-align: left; margin-top: 8px; }
    .gbtn { width: 100%; justify-content: center; padding: 11px; font-size: 14px; margin-top: 4px; }
    .gerr { color: var(--red); margin: 0; }
    .gnote { margin-top: 16px; }
    .code { font-size: 20px; letter-spacing: .3em; text-align: center; font-variant-numeric: tabular-nums; }
    .providers { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }
    .provider { width: 100%; justify-content: center; gap: 10px; padding: 10px; font-size: 14px; font-weight: 500; background: var(--surface); }
    .provider:hover { background: var(--surface-2); }
    .or { display: flex; align-items: center; gap: 12px; margin: 18px 0 4px; color: var(--faint); font-size: 12px; }
    .or::before, .or::after { content: ''; flex: 1; height: 1px; background: var(--border); }
    .linkbtn { display: block; margin: 14px auto 0; background: none; border: 0; color: var(--accent); font-size: 13px; cursor: pointer; padding: 2px 6px; }
    .linkbtn:hover { text-decoration: underline; background: none; }
  `,
})
export class NativeSignIn {
  protected auth = inject(Auth);
  private api = inject(Api);

  protected step = signal<Step>('login');
  protected email = signal('');
  protected password = signal('');
  protected confirm = signal('');
  protected code = signal('');
  protected useRecovery = signal(false);
  protected busy = signal(false);
  protected error = signal('');
  protected info = signal('');
  private mfaToken = '';
  private resetToken = '';

  constructor() {
    const ssoError = takeSsoError();  // back from Microsoft / Google with a refusal
    if (ssoError) this.error.set(ssoError);
    // Opened from the e-mailed link: /reset-password?token=…
    const token = new URLSearchParams(window.location.search).get('token');
    if (window.location.pathname === '/reset-password' && token) {
      this.resetToken = token;
      this.step.set('reset');
    }
  }

  protected go(step: Step) {
    if (step === 'login' && this.resetToken) {
      this.resetToken = '';
      history.replaceState(null, '', '/');
    }
    this.step.set(step);
    this.error.set('');
    this.code.set('');
    this.useRecovery.set(false);
  }

  protected sso(provider: string) {
    this.busy.set(true);
    this.auth.startSso(provider, { next: window.location.pathname === '/reset-password' ? '/' : window.location.pathname });
  }

  protected async login() {
    await this.run(async () => {
      const mfa = await this.auth.login(this.email().trim(), this.password());
      if (mfa) { this.mfaToken = mfa; this.step.set('mfa'); }
      this.password.set('');
    });
  }

  protected async verify() {
    await this.run(async () => {
      await this.auth.completeMfa(this.mfaToken, this.code().trim());
      this.cleanUrl();
    }, e => e?.status === 401 && /expir/i.test(e?.error?.detail ?? '') ? (this.go('login'), 'La connexion a expiré : recommencez.') : null);
  }

  protected async forgot() {
    await this.run(async () => {
      const res = await firstValueFrom(this.api.authForgotPassword(this.email().trim()));
      this.info.set(res.message);
      this.step.set('forgot-sent');
    });
  }

  protected async reset() {
    await this.run(async () => {
      const mfa = await this.auth.resetPassword(this.resetToken, this.password());
      this.password.set(''); this.confirm.set('');
      if (mfa) { this.mfaToken = mfa; this.step.set('mfa'); return; }
      this.cleanUrl();
    });
  }

  private cleanUrl() {
    if (window.location.pathname === '/reset-password') history.replaceState(null, '', '/');
  }

  private async run(action: () => Promise<void>, special?: (e: any) => string | null) {
    this.busy.set(true);
    this.error.set('');
    try {
      await action();
    } catch (e: any) {
      const detail = typeof e?.error?.detail === 'string' ? e.error.detail : '';
      this.error.set(special?.(e) ?? (e?.status === 401 ? (detail || 'E-mail ou mot de passe incorrect.')
        : e?.status === 429 || e?.status === 400 ? (detail || 'Action impossible.')
        : e?.status === 422 ? 'Adresse e-mail invalide.'
        : 'Connexion impossible (serveur injoignable ?).'));
    } finally {
      this.busy.set(false);
    }
  }
}

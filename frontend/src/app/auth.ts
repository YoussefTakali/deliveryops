import { HttpInterceptorFn } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import Keycloak from 'keycloak-js';
import { Observable, catchError, firstValueFrom, from, switchMap, throwError } from 'rxjs';

import { Api, Session } from './api';
import { Desktop } from './desktop';
import { languageHeaders } from './languages';
import { MicrosoftAuth, MicrosoftConfig } from './microsoft-auth';

type Mode = 'native' | 'dev' | 'keycloak' | 'microsoft';
type Status = 'loading' | 'authenticated' | 'unauthenticated';

export interface SignInProvider { id: 'microsoft' | 'google'; label: string; }

interface PublicConfig {
  auth_mode: Mode;
  password_reset_email?: boolean;
  sign_in?: { password: boolean; providers: SignInProvider[] } | null;
  keycloak: { url: string; realm: string; clientId: string } | null;
  microsoft: MicrosoftConfig | null;
}

/** Authentication front door: native email/password, Keycloak (OIDC), Microsoft Entra ID (MSAL),
 *  or dev personas. In native mode the access token lives in memory only; the refresh token is an
 *  httpOnly cookie the browser sends automatically. */
@Injectable({ providedIn: 'root' })
export class Auth {
  private ms = inject(MicrosoftAuth);
  private session = inject(Session);
  private api = inject(Api);
  private desktop = inject(Desktop);
  readonly mode = signal<Mode>('dev');
  readonly status = signal<Status>('loading');
  readonly microsoftEnabled = signal(false);
  /** Best-effort email/UPN of the PC's signed-in Windows account, suggested on the sign-in gate. */
  readonly pcAccountHint = signal<string | null>(null);
  readonly microsoftBusy = signal(false);
  readonly microsoftError = signal('');
  /** Native accounts can reset a forgotten password by e-mail (SMTP configured on the server). */
  readonly passwordResetByEmail = signal(false);
  /** Native mode sign-in methods: the password form and/or "Continuer avec Microsoft / Google". */
  readonly signInPassword = signal(true);
  readonly signInProviders = signal<SignInProvider[]>([]);
  keycloak: Keycloak | null = null;

  // Native access token (in-memory). Not persisted: a refresh on load restores the session.
  private accessToken: string | null = null;
  token(): string | null { return this.accessToken; }
  setToken(t: string | null): void { this.accessToken = t; }

  readonly mustChangePassword = computed(() => this.session.me()?.must_change_password === true);

  async init(): Promise<void> {
    let config: PublicConfig;
    try {
      config = await fetch('/api/config').then(r => r.json());
    } catch {
      this.status.set('unauthenticated');
      return; // API unreachable
    }
    this.mode.set(config.auth_mode);
    this.passwordResetByEmail.set(!!config.password_reset_email);
    this.signInPassword.set(config.sign_in?.password ?? true);
    this.signInProviders.set(config.sign_in?.providers ?? []);

    // Microsoft can be the auth front door AND/OR just a data connector, so set it up whenever the
    // backend advertises it, regardless of auth_mode.
    if (config.microsoft?.clientId) {
      this.microsoftEnabled.set(true);
      await this.ms.init(config.microsoft).catch(() => this.microsoftEnabled.set(false));
      // Surface a redirect failure (e.g. tenant requires admin approval) on the gate immediately,
      // rather than leaving the user staring at a plain "Se connecter avec Microsoft" button again
      // with no explanation of what just happened on Microsoft's own page.
      const redirectError = this.ms.redirectError();
      if (redirectError) this.microsoftError.set(redirectError);
    }

    if (config.auth_mode === 'native') {
      await this.restoreNative();
      return;
    }

    if (config.auth_mode === 'keycloak' && config.keycloak) {
      this.keycloak = new Keycloak(config.keycloak);
      await this.keycloak.init({ onLoad: 'login-required', pkceMethod: 'S256', checkLoginIframe: false });
      this.keycloak.onTokenExpired = () => { this.keycloak?.updateToken(30).catch(() => this.keycloak?.login()); };
      this.status.set('authenticated');
      return;
    }

    if (config.auth_mode === 'microsoft') {
      // A cached account (previous session, or the redirect we just came back from) resolves
      // instantly — no network needed.
      if (this.ms.account()) { this.status.set('authenticated'); return; }
      // Otherwise the remaining work is slow (a `whoami` shell-out, and ssoSilent's hidden-iframe
      // round trip to Microsoft, which can take seconds or time out). Don't block startup on it:
      // stay 'loading' so the shell paints a splash immediately and resolve in the background.
      this.status.set('loading');
      void this.resolveMicrosoftSession();
      return;
    }

    this.status.set('authenticated'); // dev: pages handle their own persona switching
  }

  /** Resolves the Microsoft session in the background (see init): detect the PC's account, then try
   *  a silent SSO with it. Leaves status at 'unauthenticated' so the gate takes over if that fails. */
  private async resolveMicrosoftSession(): Promise<void> {
    this.pcAccountHint.set(await this.desktop.getPcAccountHint().catch(() => null));
    const account = await this.ms.ssoSilent(this.pcAccountHint() ?? undefined).catch(() => null);
    this.status.set(account ? 'authenticated' : 'unauthenticated'); // gate shows on 'unauthenticated'
  }

  /** Called from the sign-in gate. Navigates away to Microsoft (redirect flow), so on success this
   *  never returns — the app reloads afterwards and init() completes the sign-in. */
  async signInMicrosoft(): Promise<void> {
    this.microsoftBusy.set(true);
    this.microsoftError.set('');
    try {
      await this.ms.signIn(this.pcAccountHint() ?? undefined);
    } catch (e: any) {
      this.microsoftError.set(e?.errorMessage ?? e?.message ?? 'Connexion Microsoft annulée ou échouée');
      this.microsoftBusy.set(false);
    }
  }

  // ---- native mode ----

  private applyLogin(res: { access_token: string; user: any }): void {
    this.accessToken = res.access_token;
    this.session.me.set(res.user);
    this.status.set('authenticated');
  }

  private async restoreNative(): Promise<void> {
    try {
      const res = await firstValueFrom(this.api.authRefresh());
      this.applyLogin(res);
      // pull the full /api/me (adds auth_mode, llm, etc.)
      try { this.session.me.set(await firstValueFrom(this.api.me())); } catch { /* keep login payload */ }
    } catch {
      this.accessToken = null;
      this.status.set('unauthenticated');
    }
  }

  /** Returns an MFA token when the account has two-factor on (then call completeMfa with a code). */
  async login(email: string, password: string): Promise<string | null> {
    return this.signedIn(await firstValueFrom(this.api.authLogin(email, password)));
  }

  async completeMfa(mfaToken: string, code: string): Promise<void> {
    await this.signedIn(await firstValueFrom(this.api.authMfa(mfaToken, code)));
  }

  /** "Forgot password" link: set a new password; returns an MFA token when two-factor is on. */
  async resetPassword(token: string, password: string): Promise<string | null> {
    return this.signedIn(await firstValueFrom(this.api.authResetPassword(token, password)));
  }

  /** Sign in (or, with `link`, add a sign-in method to the current account) with Microsoft / Google:
   *  a full-page round trip through the provider; the server sets the session and sends us back. */
  startSso(provider: string, opts: { next?: string; link?: boolean } = {}): void {
    const params = new URLSearchParams({ next: opts.next ?? (window.location.pathname + window.location.search) });
    if (opts.link) params.set('link', 'true');
    window.location.assign(`/api/auth/oidc/${provider}/start?${params}`);
  }

  private async signedIn(res: any): Promise<string | null> {
    if (res?.mfa_required) return res.mfa_token as string;
    this.applyLogin(res);
    try { this.session.me.set(await firstValueFrom(this.api.me())); } catch { /* keep login payload */ }
    return null;
  }

  async changePassword(current: string, next: string): Promise<void> {
    const res = await firstValueFrom(this.api.authChangePassword(current, next));
    this.applyLogin(res);
    try { this.session.me.set(await firstValueFrom(this.api.me())); } catch { /* keep login payload */ }
  }

  // ---- shared ----

  /** Resolves the Authorization/X-Dev-User headers this session would currently send with an
   *  HttpClient request (see authInterceptor below), for the rare call site that needs a raw
   *  `fetch()` instead of HttpClient — e.g. reading a streamed (SSE) response body incrementally,
   *  which HttpClient doesn't support well. Best-effort, no 401-retry loop like the interceptor. */
  async currentHeaders(): Promise<Record<string, string>> {
    return { ...languageHeaders(), ...await this.authHeaders() };
  }

  private async authHeaders(): Promise<Record<string, string>> {
    if ((this.mode() === 'microsoft' || this.ms.configured()) && this.ms.configured()) {
      const token = await this.ms.getTokenSilent().catch(() => null);
      if (token) return { Authorization: `Bearer ${token}` };
    }
    if (this.mode() === 'native') {
      return this.accessToken ? { Authorization: `Bearer ${this.accessToken}` } : {};
    }
    if (this.mode() === 'keycloak' && this.keycloak) {
      await this.keycloak.updateToken(30).catch(() => {});
      return this.keycloak.token ? { Authorization: `Bearer ${this.keycloak.token}` } : {};
    }
    return { 'X-Dev-User': this.session.username() };
  }

  get username(): string | undefined {
    if (this.mode() === 'native') return this.session.me()?.email || this.session.me()?.username;
    if (this.mode() === 'microsoft') return this.ms.account()?.username;
    return (this.keycloak?.tokenParsed as any)?.preferred_username;
  }

  async logout(): Promise<void> {
    if (this.mode() === 'native') {
      try { await firstValueFrom(this.api.authLogout()); } catch { /* best effort */ }
      this.accessToken = null;
      this.session.me.set(null);
      this.status.set('unauthenticated');
      return;
    }
    if (this.mode() === 'microsoft') { await this.ms.logout(); this.status.set('unauthenticated'); return; }
    this.keycloak?.logout({ redirectUri: window.location.origin });
  }
}

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(Auth);
  const ms = inject(MicrosoftAuth);
  const session = inject(Session);
  const api = inject(Api);

  // Microsoft data endpoints (and every call when Microsoft is the front door) carry the Graph token.
  const needsGraph = req.url.startsWith('/api/microsoft') || auth.mode() === 'microsoft';
  if (needsGraph && ms.configured()) {
    // Automation steps that call Graph (mail.*) carry a token with the extra automation scopes
    // (Mail.Send) in X-Graph-Token; in microsoft auth mode the backend reads Graph from the
    // Authorization bearer, so that token becomes the bearer (it includes User.Read, so it
    // authenticates the user just as well).
    const graphOverride = auth.mode() === 'microsoft' ? req.headers.get('X-Graph-Token') : null;
    if (graphOverride) return next(req.clone({ setHeaders: { Authorization: `Bearer ${graphOverride}` } }));
    return from(ms.getTokenSilent()).pipe(
      switchMap(token => next(token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req)),
    );
  }

  if (auth.mode() === 'native') {
    // Never attach/refresh on the signed-out endpoints (login, 2FA step, password reset, refresh,
    // logout). The other /api/auth endpoints (change-password, sessions…) act on the signed-in user.
    if (/^\/api\/auth\/(login|mfa|forgot-password|reset-password|refresh|logout)$/.test(req.url)) return next(req);
    const withToken = (t: string | null) => t ? req.clone({ setHeaders: { Authorization: `Bearer ${t}` } }) : req;
    return next(withToken(auth.token())).pipe(
      catchError((err) => {
        if (err?.status !== 401) return throwError(() => err);
        // access token expired: refresh once (cookie), then retry the original request
        return from(firstValueFrom(api.authRefresh())).pipe(
          switchMap((res: any) => { auth.setToken(res.access_token); return next(withToken(res.access_token)); }),
          catchError(() => { void auth.logout(); return throwError(() => err); }),
        );
      }),
    ) as Observable<any>;
  }

  if (auth.mode() === 'keycloak' && auth.keycloak) {
    const kc = auth.keycloak;
    return from(kc.updateToken(30).catch(() => { kc.login(); return false; })).pipe(
      switchMap(() => next(req.clone({ setHeaders: { Authorization: `Bearer ${kc.token}` } }))),
    );
  }

  return next(req.clone({ setHeaders: { 'X-Dev-User': session.username() } }));
};

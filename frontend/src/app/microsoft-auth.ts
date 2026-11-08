import { Injectable, signal } from '@angular/core';
import { AccountInfo, InteractionRequiredAuthError, PublicClientApplication } from '@azure/msal-browser';

export interface MicrosoftConfig {
  clientId: string; authority: string; scopes: string[];
  loginScopes: string[]; basicScopes: string[]; adminScopes: string[];
  /** Extra delegated scopes only the automation runner needs (Mail.Send) — requested on demand. */
  automationScopes?: string[];
}

/**
 * Microsoft Entra ID (Azure AD) sign-in via MSAL.
 *
 * Flow the app uses on load (see Auth.init):
 *   1. ssoSilent() — silently pick up the Microsoft account signed into the PC. Zero prompt when the
 *      device is Entra-joined and the browser has the Windows accounts SSO (Edge/Chrome). Otherwise
 *      it throws InteractionRequiredAuthError and we fall back to a one-click button.
 *   2. acquireTokenSilent() on every API call to get a fresh Graph access token (cached by MSAL).
 *
 * The access token is for Microsoft Graph (audience graph.microsoft.com); the backend relays it to
 * Graph, so the browser never sends it anywhere but our own origin and Microsoft.
 */
@Injectable({ providedIn: 'root' })
export class MicrosoftAuth {
  private msal: PublicClientApplication | null = null;
  private scopes: string[] = [];
  private loginScopes: string[] = [];
  private basicScopes: string[] = [];
  private automationScopes: string[] = [];
  private clientId = '';
  readonly account = signal<AccountInfo | null>(null);
  readonly ready = signal(false);
  /** Set when the redirect we just came back from failed — e.g. the tenant requires admin approval
   *  and filed a pending request instead of signing the user in (see friendlyRedirectError). Read
   *  by Auth.init() right after ms.init() so the gate can show it instead of just re-appearing with
   *  no explanation, which is what handleRedirectPromise().catch(() => null) used to do. */
  readonly redirectError = signal<string | null>(null);

  configured(): boolean {
    return this.msal !== null;
  }

  /** Tenant-admin consent URL for scopes like Files.Read.All/Sites.Read.All/Chat.Read/
   *  Team.ReadBasic.All that Entra gates behind admin approval (see config.py) — only a tenant
   *  admin visiting this link can grant them for the whole org; a regular user consenting for
   *  themselves gets a 403 from Graph forever. Null until we know the signed-in account's tenant. */
  adminConsentUrl(): string | null {
    const tenantId = this.account()?.tenantId;
    if (!tenantId || !this.clientId) return null;
    const redirect = encodeURIComponent(window.location.origin);
    return `https://login.microsoftonline.com/${tenantId}/adminconsent?client_id=${this.clientId}&redirect_uri=${redirect}`;
  }

  async init(config: MicrosoftConfig): Promise<void> {
    this.scopes = config.scopes;
    this.loginScopes = config.loginScopes?.length ? config.loginScopes : ['User.Read'];
    this.basicScopes = config.basicScopes?.length ? config.basicScopes : config.scopes;
    this.automationScopes = config.automationScopes ?? [];
    this.clientId = config.clientId;
    this.msal = new PublicClientApplication({
      auth: { clientId: config.clientId, authority: config.authority, redirectUri: window.location.origin },
      cache: { cacheLocation: 'localStorage' },
    });
    await this.msal.initialize();
    // Completes a redirect sign-in when we've just come back from Microsoft (the auth code is in
    // the URL hash); resolves to null on a normal load. A rejection here means the redirect itself
    // failed (consent denied, or the tenant requires admin approval and only filed a pending request
    // instead of issuing a token) — surfaced via redirectError so the gate can explain it, instead of
    // silently dropping the user back at "Sign in with Microsoft" with no clue why.
    const redirected = await this.msal.handleRedirectPromise().catch((err) => {
      this.redirectError.set(this.friendlyRedirectError(err));
      return null;
    });
    const existing = redirected?.account ?? this.msal.getActiveAccount() ?? this.msal.getAllAccounts()[0] ?? null;
    if (existing) {
      this.msal.setActiveAccount(existing);
      this.account.set(existing);
    }
    this.ready.set(true);
  }

  /** Silent, no prompt. `loginHint` (e.g. the PC's signed-in Windows account, when the desktop
   *  shell can detect it) targets which account to try, which helps this succeed on more machines.
   *  Returns the account on success, null if interaction is required.
   *
   *  Requests only `loginScopes` (just User.Read) — never the full Graph data scope set. Entra
   *  evaluates consent per request, not per scope: bundling in scopes some tenants gate behind
   *  admin approval (Files.Read.All etc.) would block sign-in itself for those tenants, even though
   *  User.Read alone is virtually always self-consentable. Data scopes are acquired separately, on
   *  demand, by getToken() — see its comment. */
  async ssoSilent(loginHint?: string): Promise<AccountInfo | null> {
    if (!this.msal) return null;
    if (this.account()) return this.account();
    try {
      const result = await this.msal.ssoSilent({ scopes: this.loginScopes, ...(loginHint ? { loginHint } : {}) });
      this.msal.setActiveAccount(result.account);
      this.account.set(result.account);
      return result.account;
    } catch {
      return null; // InteractionRequiredAuthError etc. — caller falls back to signIn()
    }
  }

  /** Interactive sign-in, via a full-page redirect rather than a popup. Call from a user click.
   *  `loginHint` pre-selects/pre-fills that account on Microsoft's sign-in page.
   *
   *  Redirect (not popup) on purpose: a popup lands back on our own origin, where the SPA would
   *  re-bootstrap inside a window MSAL is actively using — the classic cause of
   *  BLOCK_NESTED_POPUPS (https://aka.ms/msal.js.errors#block_nested_popups) — and in the Electron
   *  desktop shell a popup also has to host the nested Windows Hello / PIN prompt, which is fragile.
   *  A redirect has neither problem: Microsoft's page owns the whole window, then hands control back
   *  to us via handleRedirectPromise() in init(). It also needs no extra redirect URI registered —
   *  it reuses the app's origin, which is already the registered SPA redirect URI.
   *
   *  Does not resolve in the normal case: the browser navigates away before the promise settles.
   *
   *  Requests only `loginScopes` — see ssoSilent's comment for why. */
  async signIn(loginHint?: string): Promise<void> {
    if (!this.msal) throw new Error('Microsoft auth not configured');
    await this.msal.loginRedirect({ scopes: this.loginScopes, prompt: 'select_account', ...(loginHint ? { loginHint } : {}) });
  }

  /** Fresh Graph access token without prompting, for the data scopes (Mail/Calendar/Files/Teams/…).
   *  Tries the full scope set first (works once a tenant admin has consented to everything via the
   *  "Accorder le consentement admin" flow), and falls back to just the self-consentable `basicScopes`
   *  if the tenant blocks the admin-gated ones — see config.py's scope-tier comment for why a
   *  bundled request can't partially succeed. Null if even the basic set needs interaction. */
  async getTokenSilent(): Promise<string | null> {
    if (!this.msal) return null;
    const account = this.msal.getActiveAccount();
    if (!account) return null;
    try {
      const result = await this.msal.acquireTokenSilent({ scopes: this.scopes, account });
      return result.accessToken;
    } catch {
      try {
        const result = await this.msal.acquireTokenSilent({ scopes: this.basicScopes, account });
        return result.accessToken;
      } catch (err) {
        if (err instanceof InteractionRequiredAuthError) return null;
        return null;
      }
    }
  }

  /** Token with an interactive fallback — for the primary data fetch the user triggered. Silent
   *  covers the normal case; when consent/re-auth is genuinely required this redirects with just
   *  `basicScopes` (not the full admin-gated set) so a restrictive tenant degrades to "4/8 sources"
   *  instead of blocking the whole request — same reasoning as signIn/getTokenSilent above. Does not
   *  resolve, since the page navigates away. */
  async getToken(): Promise<string> {
    const silent = await this.getTokenSilent();
    if (silent) return silent;
    if (!this.msal) throw new Error('Microsoft auth not configured');
    await this.msal.acquireTokenRedirect({ scopes: this.basicScopes });
    throw new Error('Redirection vers Microsoft en cours…'); // unreachable in practice
  }

  /** Graph token for the automation runner's mail.* steps: `basicScopes` + `automationScopes`
   *  (Mail.Send). Same tiering logic as getTokenSilent/getToken: never bundled with the admin-gated
   *  scopes (one admin-only scope would make the whole request fail on restrictive tenants), and
   *  Mail.Send is requested only here — incremental consent the first time an automation that sends
   *  mail runs — never at sign-in.
   *
   *  `interactive`: when silent acquisition needs consent/re-auth, redirect to Microsoft (only from
   *  a user click — "Exécuter maintenant", "Autoriser l'envoi de mails"). Background runs pass false
   *  and get null instead, so a hidden window never navigates away mid-schedule. */
  async getAutomationToken(interactive = false): Promise<string | null> {
    if (!this.msal) return null;
    const account = this.msal.getActiveAccount();
    const scopes = Array.from(new Set([...this.basicScopes, ...this.automationScopes]));
    if (account) {
      try {
        const result = await this.msal.acquireTokenSilent({ scopes, account });
        return result.accessToken;
      } catch { /* consent or re-auth needed — fall through */ }
    }
    if (!interactive) return null;
    await this.msal.acquireTokenRedirect({ scopes, ...(account ? { account } : {}) });
    return null; // unreachable in practice: the page navigates to Microsoft
  }

  /** Whether automation scopes are configured at all (Mail.Send advertised by /api/config). */
  hasAutomationScopes(): boolean { return this.automationScopes.length > 0; }

  /** Turns an MSAL/Entra redirect failure into guidance the user can actually act on. The specific
   *  codes below cover the two shapes a restrictive tenant takes: "consent_required" (user consent
   *  is disabled outright — some tenants then auto-file an admin consent request, which is what
   *  produces the "Request pending" Microsoft page the user lands on) and a flat "access_denied". */
  private friendlyRedirectError(err: any): string {
    const code = String(err?.errorCode || '');
    const msg = String(err?.errorMessage || err?.message || '');
    if (code.includes('consent_required') || msg.includes('AADSTS65004') || /admin.*(approval|consent)/i.test(msg)) {
      return "Votre organisation Microsoft exige l'approbation d'un administrateur pour cette application. "
        + "Si une page \"Request pending\" s'est affichée, votre demande a déjà été envoyée — contactez le "
        + 'service informatique de votre organisation pour l’accélérer, ou connectez-vous avec un autre compte en attendant.';
    }
    if (code.includes('access_denied') || msg.includes('AADSTS65001')) {
      return 'Connexion Microsoft annulée ou permissions refusées.';
    }
    return msg || 'Connexion Microsoft échouée.';
  }

  async logout(): Promise<void> {
    if (!this.msal) return;
    const account = this.msal.getActiveAccount() ?? undefined;
    this.account.set(null);
    await this.msal.logoutPopup({ account }).catch(() => null);
  }
}

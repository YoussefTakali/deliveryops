import { Component, DestroyRef, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api, AppNotification, PortfolioItem, Recommendation, Session, User } from './api';
import { CompanionDock } from './companion-dock';
import { Auth } from './auth';
import { Desktop } from './desktop';
import { AutomationRunner } from './automations/runner';
import { Companion } from './companion.service';
import { LocalIndexer } from './local-indexer';
import { NativeNotify } from './native-notify';
import { CommandPalette } from './command-palette';
import { NativeSignIn } from './native-sign-in';
import { PasswordForm } from './password-form';
import { Theme } from './theme';
import { SECTIONS, SectionKey, usersLink, visibleTabs } from './section-tabs';
import { UiTranslator } from './ui-translator';
import { allLanguages, matchesLanguage } from './languages';

/** Per-notification state of the inline "Approuver" quick action in the bell dropdown. */
interface ApprovalState { phase: 'loading' | 'confirm' | 'busy' | 'done' | 'error'; recs?: Recommendation[]; message?: string; }

const MAX_TOASTS_PER_POLL = 3;
const WORKSPACE_ROUTES = ['/workspace', '/microsoft', '/local-files'];

@Component({
  selector: 'app-root',
  host: {
    '[class.bare]': 'bare()',
    '(document:keydown.escape)': 'inboxOpen.set(false); closeMenu()',
    '(document:click)': 'onDocumentClick($event)',
  },
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CompanionDock, PasswordForm, NativeSignIn, CommandPalette],
  template: `
    @if (showSplash()) {
    <!-- Painted immediately on load, while the Microsoft session resolves in the background -->
    <div class="gate">
      <div class="gate-card">
        <div class="gate-brand">
          <svg width="30" height="30" viewBox="0 0 26 26" aria-hidden="true"><rect width="26" height="26" rx="7" fill="#3553d6"/><circle cx="8" cy="13" r="3" fill="#fff"/><circle cx="18" cy="8" r="2.4" fill="#9fb2ff"/><circle cx="18" cy="18" r="2.4" fill="#9fb2ff"/><path d="M10.5 12 16 9M10.5 14 16 17" stroke="#fff" stroke-width="1.4"/></svg>
          <span>DeliveryOps</span>
        </div>
        <p class="muted"><span class="spin"></span> Connexion en cours…</p>
      </div>
    </div>
    } @else if (showGate()) {
    <!-- Sign-in gate: nothing else renders until this clears -->
    <div class="gate">
      <div class="gate-card">
        <div class="gate-brand">
          <svg width="30" height="30" viewBox="0 0 26 26" aria-hidden="true"><rect width="26" height="26" rx="7" fill="#3553d6"/><circle cx="8" cy="13" r="3" fill="#fff"/><circle cx="18" cy="8" r="2.4" fill="#9fb2ff"/><circle cx="18" cy="18" r="2.4" fill="#9fb2ff"/><path d="M10.5 12 16 9M10.5 14 16 17" stroke="#fff" stroke-width="1.4"/></svg>
          <span>DeliveryOps</span>
        </div>
        @if (auth.mode() === 'microsoft') {
          <h1>Connexion requise</h1>
          @if (auth.pcAccountHint(); as hint) {
            <p class="muted">Compte détecté sur ce PC : <strong>{{ hint }}</strong></p>
            <button class="primary gate-btn" [disabled]="auth.microsoftBusy()" (click)="signIn()">
              @if (auth.microsoftBusy()) { <span class="spin"></span> } Continuer en tant que {{ hint }}
            </button>
          } @else {
            <p class="muted">Connectez-vous avec votre compte Microsoft pour accéder à DeliveryOps et lier vos données (mails, agenda, fichiers, Teams).</p>
            <button class="primary gate-btn" [disabled]="auth.microsoftBusy()" (click)="signIn()">
              @if (auth.microsoftBusy()) { <span class="spin"></span> } Se connecter avec Microsoft
            </button>
          }
          @if (auth.microsoftError()) { <p class="small gate-error">{{ auth.microsoftError() }}</p> }
        } @else if (auth.mode() === 'native') {
          <app-native-sign-in />
        } @else {
          <h1>Connexion requise</h1>
          <p class="muted">Votre session a expiré ou n'a pas pu être restaurée. Rechargez la page pour réessayer.</p>
        }
      </div>
    </div>
    } @else if (auth.mustChangePassword()) {
    <!-- First sign-in with a temporary password (or after an admin reset): change it before anything else -->
    <div class="gate">
      <div class="gate-card wide">
        <div class="gate-brand">
          <svg width="30" height="30" viewBox="0 0 26 26" aria-hidden="true"><rect width="26" height="26" rx="7" fill="#3553d6"/><circle cx="8" cy="13" r="3" fill="#fff"/><circle cx="18" cy="8" r="2.4" fill="#9fb2ff"/><circle cx="18" cy="18" r="2.4" fill="#9fb2ff"/><path d="M10.5 12 16 9M10.5 14 16 17" stroke="#fff" stroke-width="1.4"/></svg>
          <span>DeliveryOps</span>
        </div>
        <h1>Choisissez votre mot de passe</h1>
        <p class="muted">Vous vous êtes connecté avec un mot de passe temporaire. Définissez le vôtre pour continuer.</p>
        <app-password-form [forced]="true" />
        <button class="sm ghost logout-link" (click)="auth.logout()">Se déconnecter</button>
      </div>
    </div>
    } @else if (suspended(); as reason) {
    <!-- Governance: an administrator suspended this account -->
    <div class="gate">
      <div class="gate-card">
        <div class="gate-brand">
          <svg width="30" height="30" viewBox="0 0 26 26" aria-hidden="true"><rect width="26" height="26" rx="7" fill="#3553d6"/><circle cx="8" cy="13" r="3" fill="#fff"/><circle cx="18" cy="8" r="2.4" fill="#9fb2ff"/><circle cx="18" cy="18" r="2.4" fill="#9fb2ff"/><path d="M10.5 12 16 9M10.5 14 16 17" stroke="#fff" stroke-width="1.4"/></svg>
          <span>DeliveryOps</span>
        </div>
        <h1>Accès suspendu</h1>
        <p class="muted">{{ reason }}</p>
        <p class="small muted">Contactez l'administrateur de la plateforme dans votre organisation.</p>
        @if (auth.mode() !== 'dev') { <button class="gate-btn" (click)="auth.logout()">Se déconnecter</button> }
        @else { <select class="persona-sel" [value]="session.username()" (change)="switchUser($any($event.target).value)">
          @for (u of users(); track u.username) { <option [value]="u.username">{{ u.name }}</option> }</select> }
      </div>
    </div>
    } @else if (session.me()?.role === 'unassigned') {
    <!-- Signed in (e.g. first Microsoft / Google sign-in) but no role yet: nothing to show until an admin assigns one -->
    <div class="gate">
      <div class="gate-card">
        <div class="gate-brand">
          <svg width="30" height="30" viewBox="0 0 26 26" aria-hidden="true"><rect width="26" height="26" rx="7" fill="#3553d6"/><circle cx="8" cy="13" r="3" fill="#fff"/><circle cx="18" cy="8" r="2.4" fill="#9fb2ff"/><circle cx="18" cy="18" r="2.4" fill="#9fb2ff"/><path d="M10.5 12 16 9M10.5 14 16 17" stroke="#fff" stroke-width="1.4"/></svg>
          <span>DeliveryOps</span>
        </div>
        <h1>Accès en attente</h1>
        <p class="muted">Bienvenue <strong translate="no">{{ session.me()?.name }}</strong> ! Votre compte est créé ; un administrateur doit maintenant vous attribuer un rôle. Les administrateurs ont été prévenus.</p>
        <button class="gate-btn primary" (click)="recheckAccess()">Vérifier à nouveau</button>
        @if (auth.mode() !== 'dev') { <button class="sm ghost logout-link" (click)="auth.logout()">Se déconnecter</button> }
      </div>
    </div>
    } @else {
    @if (!bare()) {
    <!-- Top navbar -->
    <header class="nav">
      <div class="nav-left">
        <button class="burger" [class.active]="sidebarOpen()" (click)="toggleSidebar()" aria-label="Basculer le menu" [attr.aria-expanded]="sidebarOpen()">
          <svg viewBox="0 0 32 32" aria-hidden="true">
            <path class="line line-top-bottom" d="M27 10 13 10C10.8 10 9 8.2 9 6 9 3.5 10.8 2 13 2 15.2 2 17 3.8 17 6L17 26C17 28.2 18.8 30 21 30 23.2 30 25 28.2 25 26 25 23.8 23.2 22 21 22L7 22"></path>
            <path class="line" d="M7 16 27 16"></path>
          </svg>
        </button>
        <a routerLink="/" class="brand">
          <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true"><rect width="26" height="26" rx="7" fill="#3553d6"/><circle cx="8" cy="13" r="3" fill="#fff"/><circle cx="18" cy="8" r="2.4" fill="#9fb2ff"/><circle cx="18" cy="18" r="2.4" fill="#9fb2ff"/><path d="M10.5 12 16 9M10.5 14 16 17" stroke="#fff" stroke-width="1.4"/></svg>
          <span>DeliveryOps</span>
        </a>
        <button class="searchbtn" (click)="palette.show()" [attr.aria-label]="'Rechercher (' + shortcut + ')'">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <span class="slabel">Rechercher…</span>
          <kbd translate="no">{{ shortcut }}</kbd>
        </button>
      </div>
      <div class="nav-right">
        <button class="iconbtn themebtn" (click)="theme.cycle()" [title]="'Thème : ' + themeLabel[theme.mode()]" aria-label="Changer de thème">
          @if (theme.mode() === 'auto') {
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3v18" fill="currentColor" stroke="none"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/></svg>
          } @else if (theme.mode() === 'dark') {
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
          } @else {
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
          }
          <span class="tlabel">{{ themeLabel[theme.mode()] }}</span>
        </button>
        <button class="iconbtn bell" [class.on]="inboxOpen()" (click)="toggleInbox()" title="Notifications" [attr.aria-label]="unread() + ' notification(s) non lue(s)'" [attr.aria-expanded]="inboxOpen()">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/></svg>
          @if (unread()) { <span class="badge">{{ unread() > 99 ? '99+' : unread() }}</span> }
        </button>
        <div class="umenu">
          <button class="ubtn" [class.on]="menuOpen()" (click)="toggleMenu()" aria-haspopup="menu" [attr.aria-expanded]="menuOpen()" aria-label="Menu du compte">
            <span class="avatar" aria-hidden="true">{{ initials() }}</span>
            <span class="uname" translate="no">{{ displayName() }}</span>
            <svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
          </button>
          @if (menuOpen()) {
            <div class="menu" role="menu" animate.enter="inbox-enter" animate.leave="inbox-leave">
              <div class="mhead">
                <span class="avatar lg" aria-hidden="true">{{ initials() }}</span>
                <div class="mwho">
                  <strong translate="no">{{ displayName() }}</strong>
                  @if (session.me()?.email || auth.username; as mail) { <span class="small muted" translate="no">{{ mail }}</span> }
                  @if (session.me()?.role; as role) { <span class="small faint">{{ roleLabel[role] || role }}</span> }
                </div>
              </div>

              <a class="mitem" role="menuitem" routerLink="/account" (click)="closeMenu()">
                <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/></svg>
                <span>Paramètres du compte</span>
              </a>
              <button class="mitem" role="menuitem" (click)="langOpen.set(!langOpen())" [attr.aria-expanded]="langOpen()">
                <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5z"/></svg>
                <span>Langue</span>
                <span class="mval" translate="no">{{ translator.current().native }}</span>
                <svg class="chev" [class.up]="langOpen()" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
              </button>
              @if (langOpen()) {
                <div class="langs">
                  <input class="lsearch" type="search" placeholder="Rechercher une langue…" [value]="langQuery()" (input)="langQuery.set($any($event.target).value)" (keydown.enter)="pickFirstLanguage()" aria-label="Rechercher une langue" />
                  <div class="llist" translate="no" role="listbox" aria-label="Langues">
                    @for (l of filteredLanguages(); track l.code) {
                      <button class="lopt" role="option" [class.on]="l.code === translator.lang()" [attr.aria-selected]="l.code === translator.lang()" (click)="chooseLanguage(l.code)">
                        <span [attr.dir]="l.rtl ? 'rtl' : null">{{ l.native }}</span>
                        @if (l.native !== l.french) { <small>{{ l.french }}</small> }
                      </button>
                    } @empty { <div class="small faint lnone">Aucune langue trouvée</div> }
                  </div>
                  <p class="small faint lnote">Hors français, l'interface est traduite automatiquement par l'IA de la plateforme.</p>
                </div>
              }
              @if (translator.busy()) { <div class="small muted mstatus"><span class="spin"></span> Traduction de l'interface…</div> }
              @if (translator.notice(); as n) { <div class="small mstatus warn">{{ n }}</div> }
              @if (!desktop.available) {
                <a class="mitem" role="menuitem" routerLink="/download" (click)="closeMenu()">
                  <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14"/></svg>
                  <span>Application bureau</span>
                </a>
              }

              @if (auth.mode() === 'dev') {
                <div class="msep"></div>
                <div class="mlabel">Persona (démo)</div>
                @for (u of users(); track u.username) {
                  <button class="mitem" role="menuitemradio" [attr.aria-checked]="u.username === session.username()" (click)="switchUser(u.username); closeMenu()">
                    <span class="radio" [class.on]="u.username === session.username()" aria-hidden="true"></span>
                    <span translate="no">{{ u.name }}</span><span class="mval">{{ roleLabel[u.role] }}</span>
                  </button>
                }
              } @else {
                <div class="msep"></div>
                <button class="mitem danger" role="menuitem" (click)="closeMenu(); auth.logout()">
                  <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h4a1.5 1.5 0 0 1 1.5 1.5v13A1.5 1.5 0 0 1 18 20h-4M10 16l-4-4 4-4M6 12h10"/></svg>
                  <span>Se déconnecter</span>
                </button>
              }
            </div>
          }
        </div>
      </div>
    </header>

    <!-- Left sidebar -->
    <aside class="side" [class.open]="sidebarOpen()" [attr.aria-hidden]="!sidebarOpen()" [attr.inert]="sidebarOpen() ? null : ''">
      <!-- Minimal sidebar: related pages share one entry and switch with tabs (see section-tabs.ts);
           account settings, language and sign-out live in the account menu (top right). -->
      @if (can('companion.use')) {
        <nav>
          <a routerLink="/companion" routerLinkActive="on">
            <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.5s-7.5-4.4-7.5-10A4.4 4.4 0 0 1 12 7.6a4.4 4.4 0 0 1 7.5 2.9c0 5.6-7.5 10-7.5 10z"/></svg>
            <span>Compagnon</span>
            @if (companion.state()?.suggestions?.length; as n) { <span class="count">{{ n }}</span> }
          </a>
        </nav>
      }

      <div class="section">Pilotage</div>
      <nav>
        <a routerLink="/" [class.on]="inSection('portfolio')">
          <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/></svg>
          <span>Portefeuille</span>
        </a>
        <a routerLink="/approvals" routerLinkActive="on">
          <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.4 2.4 4.6-4.9"/></svg>
          <span>Validations</span>
          @if (pendingDecisions(); as n) { <span class="count" [title]="n + ' décision(s) en attente'">{{ n }}</span> }
        </a>
      </nav>

      <div class="section">Mes outils</div>
      <nav>
        <!-- One hub entry: /workspace covers Microsoft 365 + local files; /microsoft and /local-files
             are its detail views (linked from its cards), so the entry stays active on all three. -->
        <a routerLink="/workspace" [class.on]="inWorkspace()">
          <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/></svg>
          <span>Mon espace</span>
        </a>
        @if (can('techwatch.use')) {
          <a routerLink="/veille" routerLinkActive="on">
            <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19.5V6.5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v13l-3-2-3 2-3-2z"/><path d="M9 9h6M9 12.5h4"/></svg>
            <span>Veille</span>
          </a>
        }
        @if (showAutomations()) {
          <a routerLink="/automations" routerLinkActive="on">
            <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/></svg>
            <span>Automatisations</span>
          </a>
        }
        @if (can('agent.call_assistant')) {
          <a href="/call-assistant" (click)="$event.preventDefault(); openCall()">
            <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3.5" y="14" width="4" height="6" rx="1.5"/><rect x="16.5" y="14" width="4" height="6" rx="1.5"/></svg>
            <span>Assistant d'appel</span>
            @if (desktop.available) { <svg class="ic ext" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-8 8M17 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h4"/></svg> }
          </a>
        }
      </nav>

      @if (sectionLink('supervision') || sectionLink('access') || can('deliverables.catalog')) {
        <div class="section">Administration</div>
        <nav>
          @if (sectionLink('supervision'); as link) {
            <a [routerLink]="link" [class.on]="inSection('supervision')">
              <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l3 7 4-14 3 7h4"/></svg>
              <span>Supervision</span>
            </a>
          }
          @if (sectionLink('access'); as link) {
            <a [routerLink]="link" [class.on]="inSection('access')">
              <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8.5" r="3.3"/><path d="M3 19.5a6 6 0 0 1 12 0"/><path d="M15.5 5.3a3.3 3.3 0 0 1 0 6.4M17.5 14a6 6 0 0 1 3.5 5.5"/></svg>
              <span>Utilisateurs & accès</span>
            </a>
          }
          @if (can('deliverables.catalog')) {
            <a routerLink="/livrables/catalogue" routerLinkActive="on">
              <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3.5H6.5a1 1 0 0 0-1 1v15a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V8z"/><path d="M14 3.5V8h4.5M9 12.5h6M9 16h4"/></svg>
              <span>Catalogue de livrables</span>
            </a>
          }
        </nav>
      }

      <div class="section">Périmètres</div>
      <nav class="perims">
        @for (g of perimeterGroups(); track g.key) {
          @if (g.project) {
            <a class="projhead" [routerLink]="['/pr', g.projectId]" routerLinkActive="on">{{ g.project }}</a>
          }
          @for (p of g.items; track p.id) {
            <a [routerLink]="['/p', p.id]" routerLinkActive="on" [class.nested]="!!g.project">
              <span class="dot" [class]="p.overall_status"></span>
              <span class="pname" translate="no">{{ p.name }}<small>{{ p.client }}</small></span>
            </a>
          }
        } @empty { <div class="small faint none">Aucun périmètre visible.</div> }
        @if (session.me()?.can_decide) {
          <a routerLink="/onboarding" routerLinkActive="on" class="add">
            <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
            <span>Nouveau périmètre</span>
          </a>
        }
      </nav>

      <div class="spacer"></div>
      <div class="foot">
        @if (session.me(); as me) {
          <div class="mode">
            <span class="pill" [class.accent]="me.llm === 'api'" [title]="(me.llm_model ?? '') + ' · auth ' + (me.auth_mode ?? '')">{{ me.llm === 'api' ? 'IA connectée' : 'Mode hors ligne' }}</span>
          </div>
        }
      </div>
    </aside>

    <!-- Mobile backdrop -->
    <button class="backdrop" [class.show]="sidebarOpen()" (click)="closeSidebar()" aria-label="Fermer le menu" tabindex="-1"></button>

    @if (inboxOpen()) {
      <div class="inbox" role="dialog" aria-label="Notifications" animate.enter="inbox-enter" animate.leave="inbox-leave">
        <header class="ib-head">
          <h2>Notifications</h2>
          <div class="ib-tools">
            @if (session.me()?.can_decide) {
              <button class="ib-icon" (click)="scan()" [disabled]="scanning()" title="Analyser mes périmètres maintenant" aria-label="Analyser mes périmètres maintenant">
                <svg [class.turning]="scanning()" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4"/></svg>
              </button>
            }
            <button class="ib-link" (click)="readAll()" [disabled]="!unread()">Tout marquer comme lu</button>
          </div>
        </header>
        <div class="ib-tabs" role="tablist" aria-label="Filtrer les notifications">
          <button role="tab" [attr.aria-selected]="inboxFilter() === 'all'" [class.on]="inboxFilter() === 'all'" (click)="inboxFilter.set('all')">Toutes</button>
          <button role="tab" [attr.aria-selected]="inboxFilter() === 'unread'" [class.on]="inboxFilter() === 'unread'" (click)="inboxFilter.set('unread')">
            Non lues @if (unread()) { <span class="ib-n">{{ unread() }}</span> }</button>
        </div>

        <div class="ib-list">
          @for (g of noteGroups(); track g.label) {
            <div class="ib-day">{{ g.label }}</div>
            @for (n of g.items; track n.id) {
              <article class="ib-item" [class.unread]="!n.read">
                <span class="ib-ico" [attr.data-tone]="noteTone(n)" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    @switch (n.kind) {
                      @case ('approval_required') { <path d="M9 11.5l2.2 2.2L15.5 9.5"/><circle cx="12" cy="12" r="8.5"/> }
                      @case ('run_completed') { <path d="M14 3.5H7a1.5 1.5 0 0 0-1.5 1.5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3.5V8h4.5M9.5 14l1.8 1.8 3.4-3.6"/> }
                      @case ('ai_suggestion') { <path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.5.4.8 1 .8 1.7V16h5.6v-.5c0-.7.3-1.3.8-1.7A6 6 0 0 0 12 3z"/> }
                      @case ('user_pending_approval') { <circle cx="10" cy="8" r="3.5"/><path d="M3.5 20a6.5 6.5 0 0 1 13 0M18.5 8v5M16 10.5h5"/> }
                      @case ('ai_budget') { <path d="M4.5 16.5a8 8 0 1 1 15 0"/><path d="M12 14.5l3.5-4"/><circle cx="12" cy="15" r="1"/> }
                      @case ('sync') { <path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4"/> }
                      @default { <path d="M12 4.5 3.5 19h17z"/><path d="M12 10v4M12 16.5v.01"/> }
                    }
                  </svg>
                </span>
                <div class="ib-body">
                  <button class="ib-main" (click)="openNote(n)">
                    <span class="ib-title">{{ n.title }}</span>
                    @if (n.body) { <span class="ib-text">{{ n.body }}</span> }
                    <span class="ib-meta"><span translate="no">{{ noteContext(n) }}</span> · {{ ago(n.ts) }}</span>
                  </button>

                  <!-- Quick actions: only wired to endpoints that exist; permissions still enforced server-side. -->
                  @if (approvals()[n.id]; as st) {
                    <div class="ib-panel" [class.error]="st.phase === 'error'" [class.done]="st.phase === 'done'">
                      @switch (st.phase) {
                        @case ('loading') { <span><span class="spin"></span> Chargement des décisions…</span> }
                        @case ('confirm') {
                          <span><strong>{{ st.recs!.length }} décision(s)</strong> seront approuvées :</span>
                          <ul>
                            @for (r of st.recs!.slice(0, 4); track r.id) { <li>{{ r.title }}</li> }
                            @if (st.recs!.length > 4) { <li class="faint">… et {{ st.recs!.length - 4 }} autre(s)</li> }
                          </ul>
                          <div class="ib-actions">
                            <button class="ib-btn primary" (click)="confirmApproval(n)">Confirmer</button>
                            <button class="ib-btn" (click)="clearApproval(n.id)">Annuler</button>
                            <button class="ib-btn" (click)="openNote(n)">Voir le détail</button>
                          </div>
                        }
                        @case ('busy') { <span><span class="spin"></span> Approbation en cours…</span> }
                        @default { <span>{{ st.message }}</span> }
                      }
                    </div>
                  } @else if (quickActions(n).length) {
                    <div class="ib-actions">
                      @for (a of quickActions(n); track a.id) {
                        <button class="ib-btn" [class.primary]="a.primary" (click)="runQuickAction(n, a.id)">{{ a.label }}</button>
                      }
                    </div>
                  }
                </div>
                @if (!n.read) { <span class="ib-dot" title="Non lue"></span> }
              </article>
            }
          } @empty {
            <div class="ib-empty">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/><path d="m9.5 10.5 1.8 1.8 3.2-3.3"/></svg>
              <strong>{{ inboxFilter() === 'unread' ? 'Aucune notification non lue' : 'Tout est à jour' }}</strong>
              <span>Alertes de vos périmètres, validations et suggestions apparaîtront ici.</span>
            </div>
          }
        </div>

        @if (notify.supported) {
          <footer class="ib-foot">
            <label class="toggle">
              <input type="checkbox" role="switch" [checked]="notify.enabled()" (change)="toggleDesktopNotifs($any($event.target).checked)" />
              <span class="track" aria-hidden="true"><span class="thumb"></span></span>
              <span>Notifications bureau<small>{{ notifyHint() }}</small></span>
            </label>
            @if (notify.enabled()) { <button class="ib-link" (click)="testNotification()">Tester</button> }
          </footer>
          @if (notifyError()) { <div class="small notify-err">{{ notifyError() }}</div> }
        }
      </div>
    }
    }
    <main #mainEl [class.with-nav]="!bare()" [class.shift]="!bare() && sidebarOpen()"><router-outlet /></main>
    @if (can('companion.use') && !currentPath().startsWith('/companion')) { <app-companion-dock /> }
    <app-command-palette #palette [perimeters]="perimeters()" (chooseLanguage)="openLanguageMenu()" (openCall)="openCall()" />
    }
  `,
  styles: `
    :host { --nav-h: 56px; --side-w: 250px; --ease: cubic-bezier(.4,0,.2,1); display: block; min-height: 100vh; }

    /* Sign-in gate: full-screen, replaces the whole app shell until cleared. */
    .gate { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px;
      padding-top: calc(24px + var(--dm-titlebar-h, 0px)); background: var(--bg); }
    .gate-card { width: min(400px, 100%); background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
      padding: 32px 28px; box-shadow: var(--shadow); text-align: center; }
    .gate-brand { display: flex; align-items: center; justify-content: center; gap: 10px; font-weight: 700; font-size: 18px; margin-bottom: 22px; color: var(--text); }
    .gate-card h1 { font-size: 18px; margin-bottom: 10px; }
    .gate-card .muted { margin-bottom: 18px; line-height: 1.5; }
    .gate-btn { width: 100%; justify-content: center; padding: 11px; font-size: 14px; }
    .gate-error { color: var(--red); margin-top: 12px; }
    .gate-card.wide { width: min(460px, 100%); }
    .logout-link { margin-top: 14px; }

    /* Navbar: fixed on top, full width, above the sidebar. Offset below the desktop app's own
       custom titlebar (--dm-titlebar-h, set on <html> by the Electron shell; 0 in the browser). */
    .nav { position: fixed; top: var(--dm-titlebar-h, 0px); left: 0; width: 100%; height: var(--nav-h); z-index: 1001;
      display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 0 16px;
      background: var(--surface); border-bottom: 1px solid var(--border); box-shadow: 0 2px 8px rgba(0,0,0,.06); }
    .nav-left, .nav-right { display: flex; align-items: center; gap: 10px; }
    .brand { display: flex; align-items: center; gap: 10px; color: var(--text); font-weight: 600; font-size: 16px; }
    .brand:hover { text-decoration: none; }
    .burger { background: transparent; border: 0; padding: 4px; cursor: pointer; color: var(--text); display: inline-flex; border-radius: 7px; }
    .burger:hover { background: var(--surface-2); }
    .burger svg { height: 26px; width: 26px; transition: transform .4s var(--ease); }
    .burger .line { fill: none; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 3;
      transition: stroke-dasharray .5s var(--ease), stroke-dashoffset .5s var(--ease); }
    .burger .line-top-bottom { stroke-dasharray: 12 63; }
    .burger.active svg { transform: rotate(-45deg); }
    .burger.active .line-top-bottom { stroke-dasharray: 20 300; stroke-dashoffset: -32.42; }
    .iconbtn { position: relative; display: inline-flex; align-items: center; gap: 6px; background: transparent; border: 0; color: var(--text);
      padding: 7px 8px; border-radius: 8px; cursor: pointer; transition: background-color .15s ease, color .15s ease, transform .15s ease; }
    .iconbtn:hover { background: var(--surface-2); }
    .iconbtn:active { transform: scale(.94); }
    .bell.on { background: var(--accent-soft); color: var(--accent); }
    .tlabel { font-size: 12px; color: var(--muted); }
    .badge { position: absolute; top: 1px; right: -1px; background: #e5484d; color: #fff; font-size: 10px; font-weight: 600; border-radius: 999px;
      padding: 0 5px; line-height: 16px; animation: badge-pop .25s var(--ease); }
    @keyframes badge-pop { from { transform: scale(.4); opacity: 0; } to { transform: scale(1); opacity: 1; } }
    .persona-sel { max-width: 220px; }

    /* Ctrl+K search entry */
    .searchbtn { display: inline-flex; align-items: center; gap: 8px; margin-inline-start: 14px; padding: 6px 8px 6px 12px; min-width: 240px;
      background: var(--surface-2); border: 1px solid var(--border); border-radius: 9px; color: var(--muted); font-size: 13px; font-weight: 400; cursor: pointer; }
    .searchbtn:hover { border-color: color-mix(in srgb, var(--accent) 40%, var(--border)); color: var(--text); }
    .searchbtn .slabel { flex: 1; text-align: start; }
    .searchbtn kbd { font: inherit; font-size: 11px; border: 1px solid var(--border); border-bottom-width: 2px; border-radius: 5px; padding: 0 5px; background: var(--surface); }

    /* Account menu (top right) */
    .umenu { position: relative; }
    .ubtn { display: inline-flex; align-items: center; gap: 8px; background: transparent; border: 1px solid transparent; color: var(--text);
      padding: 4px 8px 4px 4px; border-radius: 999px; cursor: pointer; font-weight: 500; transition: background-color .15s ease, border-color .15s ease; }
    .ubtn:hover, .ubtn.on { background: var(--surface-2); border-color: var(--border); }
    .uname { font-size: 13px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .avatar { width: 28px; height: 28px; border-radius: 50%; background: var(--accent); color: #fff; display: grid; place-items: center;
      font-size: 11.5px; font-weight: 700; letter-spacing: .02em; flex: none; }
    .avatar.lg { width: 40px; height: 40px; font-size: 14px; }
    .chev { flex: none; opacity: .6; transition: transform .2s var(--ease); }
    .ubtn.on .chev, .chev.up { transform: rotate(180deg); }
    .menu { position: absolute; top: calc(100% + 8px); inset-inline-end: 0; width: 300px; max-height: calc(100vh - var(--nav-h) - var(--dm-titlebar-h, 0px) - 24px);
      overflow-y: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 12px 40px rgba(0,0,0,.25);
      padding: 6px; z-index: 1002; transform-origin: top right; }
    .mhead { display: flex; gap: 12px; align-items: center; padding: 10px 10px 12px; border-bottom: 1px solid var(--border); margin-bottom: 6px; }
    .mwho { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .mwho > * { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .mitem { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 10px; border: 0; border-radius: 8px; background: transparent;
      color: var(--text); font: inherit; font-size: 13.5px; font-weight: 400; text-align: start; cursor: pointer; }
    .mitem:hover, .mitem:focus-visible { background: var(--surface-2); text-decoration: none; outline: none; }
    .mitem .ic { width: 17px; height: 17px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; opacity: .8; }
    .mitem.danger { color: var(--red); }
    .mval { margin-inline-start: auto; font-size: 12px; color: var(--muted); max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .mval + .chev { margin-inline-start: 0; }
    .msep { height: 1px; background: var(--border); margin: 6px 4px; }
    .mlabel { text-transform: uppercase; font-size: 10.5px; font-weight: 600; letter-spacing: .07em; color: var(--faint); padding: 6px 10px 4px; }
    .radio { width: 14px; height: 14px; border-radius: 50%; border: 1.5px solid var(--border); flex: none; }
    .radio.on { border: 4px solid var(--accent); }
    .mstatus { padding: 6px 10px; display: flex; align-items: center; gap: 8px; }
    .mstatus.warn { color: var(--amber); }
    .langs { padding: 4px 6px 8px; display: flex; flex-direction: column; gap: 6px; }
    .lsearch { font-size: 13px; padding: 6px 10px; }
    .llist { max-height: 240px; overflow-y: auto; display: flex; flex-direction: column; gap: 1px; border: 1px solid var(--border); border-radius: 8px; padding: 4px; }
    .lopt { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; width: 100%; border: 0; border-radius: 6px; background: transparent;
      color: var(--text); font: inherit; font-size: 13px; font-weight: 400; padding: 6px 8px; text-align: start; cursor: pointer; }
    .lopt:hover { background: var(--surface-2); }
    .lopt.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
    .lopt small { color: var(--faint); font-size: 11px; white-space: nowrap; }
    .lnone { padding: 8px; }
    .lnote { line-height: 1.4; }

    /* Sidebar: fixed under the navbar, slides in/out (transform only — no layout work per frame). */
    .side { position: fixed; top: calc(var(--nav-h) + var(--dm-titlebar-h, 0px)); left: 0; width: var(--side-w);
      height: calc(100vh - var(--nav-h) - var(--dm-titlebar-h, 0px));
      background: var(--sidebar); color: var(--sidebar-text); display: flex; flex-direction: column; padding: 6px 12px 14px;
      overflow-y: auto; overscroll-behavior: contain; z-index: 1000; border-right: 1px solid var(--border);
      transform: translateX(-100%); visibility: hidden;
      transition: transform .28s var(--ease), visibility 0s linear .28s, background-color .28s ease, color .28s ease, border-color .28s ease; }
    .side.open { transform: translateX(0); visibility: visible;
      transition: transform .28s var(--ease), visibility 0s, background-color .28s ease, color .28s ease, border-color .28s ease; }
    nav { display: flex; flex-direction: column; gap: 1px; }
    nav a { position: relative; color: var(--sidebar-text); padding: 7px 10px; border-radius: 8px; display: flex; align-items: center; gap: 10px;
      font-size: 13.5px; transition: background-color .15s ease, color .15s ease; }
    nav a::before { content: ''; position: absolute; left: -12px; top: 7px; bottom: 7px; width: 3px; border-radius: 0 3px 3px 0;
      background: var(--accent); transform: scaleY(0); transition: transform .2s var(--ease); }
    nav a:hover { background: var(--surface-2); color: var(--text); text-decoration: none; }
    nav a.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
    nav a.on::before { transform: scaleY(1); }
    nav a:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
    nav a.add { color: var(--accent); font-size: 13px; }
    nav a .count { margin-left: auto; min-width: 18px; text-align: center; font-size: 10.5px; font-weight: 700; border-radius: 999px; padding: 1px 5px; background: var(--accent); color: #fff; }
    .ic { width: 17px; height: 17px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; opacity: .85; }
    nav a.on .ic { opacity: 1; }
    .ic.ext { width: 13px; height: 13px; margin-left: auto; opacity: .5; }
    .pname { display: flex; flex-direction: column; line-height: 1.25; min-width: 0; }
    .pname small { color: var(--faint); font-size: 11px; font-weight: 400; }
    .perims .dot { flex: none; margin: 0 4px; }
    .perims .projhead { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; opacity: .7; padding: 6px 10px 2px; }
    .perims .projhead.on { color: var(--accent); opacity: 1; }
    .perims a.nested { padding-left: 20px; }
    .none { padding: 4px 10px; }
    .section { text-transform: uppercase; font-size: 10.5px; font-weight: 600; letter-spacing: .07em; color: var(--faint); margin: 16px 10px 5px; }
    .spacer { flex: 1; min-height: 12px; }
    .foot { border-top: 1px solid var(--border); padding-top: 10px; display: flex; flex-direction: column; gap: 8px; }
    .mode { display: flex; gap: 6px; flex-wrap: wrap; padding: 0 6px; }

    /* Main content: offset under the navbar, shifts right when the sidebar is open */
    main { min-width: 0; transition: margin-left .28s var(--ease); margin-left: 0; }
    main.with-nav { padding-top: calc(var(--nav-h) + var(--dm-titlebar-h, 0px)); }
    main.shift { margin-left: var(--side-w); }

    /* Backdrop only for the mobile overlay (fades in/out rather than popping) */
    .backdrop { display: none; position: fixed; inset: calc(var(--nav-h) + var(--dm-titlebar-h, 0px)) 0 0 0; z-index: 999;
      background: rgba(0,0,0,.4); border: 0; opacity: 0; pointer-events: none; transition: opacity .25s ease; }

    /* Notification inbox: a quiet list — hairline separators, one tinted icon per kind, a dot for unread */
    .inbox { position: fixed; right: 12px; top: calc(var(--nav-h) + var(--dm-titlebar-h, 0px) + 8px); width: min(400px, calc(100vw - 24px));
      max-height: min(620px, calc(100vh - var(--nav-h) - var(--dm-titlebar-h, 0px) - 20px)); z-index: 1002; display: flex; flex-direction: column;
      background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden;
      box-shadow: 0 16px 40px -12px rgba(0,0,0,.28), 0 2px 6px rgba(0,0,0,.06); transform-origin: top right; }
    .inbox-enter { animation: inbox-in .16s var(--ease); }
    .inbox-leave { animation: inbox-out .12s ease-in forwards; }
    @keyframes inbox-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
    @keyframes inbox-out { from { opacity: 1; } to { opacity: 0; transform: translateY(-3px); } }

    .ib-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px 6px; }
    .ib-head h2 { font-size: 15px; font-weight: 600; }
    .ib-tools { display: flex; align-items: center; gap: 4px; }
    .ib-icon { display: inline-grid; place-items: center; width: 28px; height: 28px; padding: 0; border: 0; border-radius: 7px; background: transparent; color: var(--muted); }
    .ib-icon:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
    .ib-icon svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .ib-icon svg.turning { animation: spin 1s linear infinite; }
    .ib-link { border: 0; background: none; padding: 4px 6px; font-size: 12.5px; font-weight: 500; color: var(--accent); border-radius: 6px; }
    .ib-link:hover:not(:disabled) { background: var(--accent-soft); }
    .ib-link:disabled { color: var(--faint); background: none; opacity: 1; }

    .ib-tabs { display: flex; gap: 16px; padding: 0 16px; border-bottom: 1px solid var(--border); }
    .ib-tabs button { border: 0; border-radius: 0; background: none; padding: 8px 0; margin-bottom: -1px; font-size: 13px; font-weight: 500;
      color: var(--muted); border-bottom: 2px solid transparent; gap: 6px; }
    .ib-tabs button:hover { background: none; color: var(--text); }
    .ib-tabs button.on { color: var(--text); border-bottom-color: var(--text); }
    .ib-n { font-size: 11px; font-weight: 600; min-width: 18px; padding: 0 5px; border-radius: 999px; background: var(--surface-2); color: var(--muted); text-align: center; }

    .ib-list { overflow-y: auto; flex: 1; overscroll-behavior: contain; }
    .ib-day { position: sticky; top: 0; z-index: 1; padding: 10px 16px 4px; font-size: 11.5px; font-weight: 600; color: var(--faint); background: var(--surface); }
    .ib-item { position: relative; display: flex; gap: 12px; padding: 10px 28px 12px 16px; transition: background-color .12s ease; }
    .ib-item + .ib-item::before { content: ''; position: absolute; top: 0; left: 56px; right: 0; height: 1px; background: var(--border); }
    .ib-item:hover { background: var(--surface-2); }
    .ib-ico { flex: none; width: 28px; height: 28px; margin-top: 1px; border-radius: 50%; display: grid; place-items: center;
      background: var(--surface-2); color: var(--muted); }
    .ib-ico svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .ib-ico[data-tone="red"] { background: var(--red-soft); color: var(--red); }
    .ib-ico[data-tone="amber"] { background: var(--amber-soft); color: var(--amber); }
    .ib-ico[data-tone="green"] { background: var(--green-soft); color: var(--green); }
    .ib-ico[data-tone="accent"] { background: var(--accent-soft); color: var(--accent); }
    .ib-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
    .ib-main { display: flex; flex-direction: column; align-items: stretch; gap: 2px; width: 100%; padding: 0; border: 0; background: none;
      text-align: start; font-weight: 400; color: var(--text); cursor: pointer; }
    .ib-main:hover { background: none; }
    .ib-main:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 4px; }
    .ib-title { font-size: 13.5px; line-height: 1.35; color: var(--muted); font-weight: 500; }
    .unread .ib-title { color: var(--text); font-weight: 600; }
    .ib-text { font-size: 12.5px; line-height: 1.45; color: var(--muted); white-space: pre-line;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .ib-meta { font-size: 11.5px; color: var(--faint); margin-top: 2px; }
    .ib-dot { position: absolute; top: 16px; right: 12px; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }

    .ib-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .ib-btn { padding: 3px 10px; font-size: 12px; font-weight: 500; border-radius: 6px; border: 1px solid var(--border); background: var(--surface); color: var(--text); }
    .ib-btn:hover { background: var(--surface-2); }
    .ib-btn.primary { background: var(--accent-soft); border-color: transparent; color: var(--accent); }
    .ib-btn.primary:hover { background: color-mix(in srgb, var(--accent) 22%, transparent); }
    .ib-panel { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border-radius: 8px; background: var(--surface-2); font-size: 12.5px; color: var(--text); }
    .ib-panel ul { margin: 0; padding-inline-start: 18px; color: var(--muted); }
    .ib-panel.done { background: var(--green-soft); color: var(--green); }
    .ib-panel.error { background: var(--red-soft); color: var(--red); }
    .ib-panel > span { display: inline-flex; align-items: center; gap: 8px; }

    .ib-empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 40px 24px 44px; text-align: center; color: var(--muted); font-size: 12.5px; }
    .ib-empty svg { width: 32px; height: 32px; margin-bottom: 6px; fill: none; stroke: var(--faint); stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
    .ib-empty strong { color: var(--text); font-size: 13.5px; font-weight: 600; }

    .ib-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 16px; border-top: 1px solid var(--border); background: var(--surface); }
    .ib-foot .toggle > span:last-child { display: flex; flex-direction: column; line-height: 1.25; }
    .ib-foot small { font-size: 11.5px; color: var(--faint); }
    .notify-err { color: var(--red); padding: 0 16px 10px; }
    .toggle { display: inline-flex; align-items: center; gap: 8px; margin: 0; font-size: 13px; color: var(--text); cursor: pointer; }
    .toggle input { position: absolute; opacity: 0; width: 1px; height: 1px; }
    .track { width: 30px; height: 17px; border-radius: 999px; background: var(--border); position: relative; transition: background-color .2s ease; flex: none; }
    .thumb { position: absolute; top: 2px; left: 2px; width: 13px; height: 13px; border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.3); transition: transform .2s var(--ease); }
    .toggle input:checked + .track { background: var(--accent); }
    .toggle input:checked + .track .thumb { transform: translateX(13px); }
    .toggle input:focus-visible + .track { outline: 2px solid var(--accent); outline-offset: 2px; }

    @media (max-width: 900px) {
      main.shift { margin-left: 0; }          /* sidebar overlays instead of pushing content */
      .backdrop { display: block; }
      .backdrop.show { opacity: 1; pointer-events: auto; }
      .side.open { box-shadow: 8px 0 30px rgba(0,0,0,.18); }
      .uname, .tlabel, .searchbtn .slabel, .searchbtn kbd { display: none; }
      .searchbtn { min-width: 0; padding: 7px; margin-inline-start: 4px; background: transparent; border-color: transparent; }
    }
    @media (prefers-reduced-motion: reduce) {
      .inbox-enter, .inbox-leave, .quick, .badge { animation: none !important; }
    }
  `,
})
export class App {
  private api = inject(Api);
  private router = inject(Router);
  protected auth = inject(Auth);
  protected desktop = inject(Desktop);
  protected theme = inject(Theme);
  protected translator = inject(UiTranslator);
  protected menuOpen = signal(false);
  protected langOpen = signal(false);
  protected langQuery = signal('');
  protected filteredLanguages = computed(() => allLanguages().filter(l => matchesLanguage(l, this.langQuery())));
  protected displayName = computed(() => this.session.me()?.name || this.auth.username || '—');
  protected initials = computed(() => this.displayName().split(/[\s@._-]+/).filter(Boolean).slice(0, 2)
    .map(p => p[0]!.toLocaleUpperCase()).join('') || '?');
  protected notify = inject(NativeNotify);
  protected themeLabel: Record<string, string> = { auto: 'Auto', light: 'Clair', dark: 'Sombre' };
  protected bare = signal(this.chromeless(window.location.pathname));
  protected sidebarOpen = signal(window.innerWidth > 900);
  /** Three startup states: splash while the session resolves, then either the sign-in gate or the
   *  app shell. Keeping them distinct is what lets the first paint happen immediately. */
  protected showSplash = computed(() => this.auth.status() === 'loading');
  protected showGate = computed(() => this.auth.status() === 'unauthenticated');
  protected session = inject(Session);
  // Injected here (not lazily by WorkspacePage) so the fs-watcher → upload pipeline starts once at
  // app bootstrap and keeps running regardless of which route/tab is active — see local-indexer.ts.
  private localIndexer = inject(LocalIndexer);
  // Same idea for automations: the runner polls due/mail-triggered automations in the background
  // (desktop app) for the whole app lifetime — see automations/runner.ts.
  private automationRunner = inject(AutomationRunner);
  // Same for the companion: activity shipping + proactive offers run app-wide, not per page.
  protected companion = inject(Companion);
  protected users = signal<User[]>([]);
  protected perimeters = signal<PortfolioItem[]>([]);
  /** Set when the API answers 403 on /api/me: an administrator suspended this account (Gouvernance IA). */
  protected suspended = signal<string | null>(null);
  // Périmètres grouped under their delivery project (ungrouped ones come last, no header).
  protected perimeterGroups = computed(() => {
    const groups = new Map<string, { key: string; project: string | null; projectId: string | null; items: PortfolioItem[] }>();
    for (const p of this.perimeters()) {
      const key = p.project_id || '__none__';
      if (!groups.has(key)) groups.set(key, { key, project: p.project ?? null, projectId: p.project_id ?? null, items: [] });
      groups.get(key)!.items.push(p);
    }
    return [...groups.values()].sort((a, b) => (a.project ? 0 : 1) - (b.project ? 0 : 1) || (a.project || '').localeCompare(b.project || ''));
  });
  protected notifications = signal<AppNotification[]>([]);
  protected unread = computed(() => this.notifications().filter(n => !n.read).length);
  protected inboxOpen = signal(false);
  protected inboxFilter = signal<'all' | 'unread'>('all');
  /** Inbox rows grouped by day, newest first (the API already returns them newest first). */
  protected noteGroups = computed(() => {
    const items = this.notifications().filter(n => this.inboxFilter() === 'all' || !n.read);
    const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const today = startOf(new Date());
    const day = 86_400_000;
    const label = (ts: string) => {
      const d = startOf(new Date(ts));
      return d >= today ? 'Aujourd\'hui' : d >= today - day ? 'Hier' : d >= today - 6 * day ? 'Cette semaine' : 'Plus ancien';
    };
    const groups: { label: string; items: AppNotification[] }[] = [];
    for (const n of items) {
      const l = label(n.ts);
      if (groups.at(-1)?.label === l) groups.at(-1)!.items.push(n); else groups.push({ label: l, items: [n] });
    }
    return groups;
  });

  protected noteTone(n: AppNotification): 'red' | 'amber' | 'green' | 'accent' | 'neutral' {
    switch (n.kind) {
      case 'approval_required': return 'amber';
      case 'run_completed': return 'green';
      case 'ai_suggestion': return 'accent';
      case 'user_pending_approval': return 'neutral';
      case 'sync': return 'red';
      default: return n.severity === 'high' ? 'red' : n.severity === 'medium' ? 'amber' : 'neutral';
    }
  }

  protected noteContext(n: AppNotification): string {
    if (n.perimeter_id) return [n.client, n.perimeter].filter(Boolean).join(' · ');
    return n.kind === 'ai_budget' ? 'Budget IA' : n.kind === 'user_pending_approval' ? 'Utilisateurs' : 'Administration';
  }

  /** "à l'instant", "il y a 5 min", "il y a 3 h", "hier à 14:02", then a date. */
  protected ago(ts: string): string {
    const d = new Date(ts);
    const minutes = Math.round((Date.now() - d.getTime()) / 60_000);
    if (minutes < 1) return 'à l\'instant';
    if (minutes < 60) return `il y a ${minutes} min`;
    if (minutes < 12 * 60) return `il y a ${Math.round(minutes / 60)} h`;
    const time = d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === new Date().toDateString()) return `aujourd'hui à ${time}`;
    if (d.toDateString() === yesterday.toDateString()) return `hier à ${time}`;
    return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' }) + ` à ${time}`;
  }
  protected scanning = signal(false);
  protected approvals = signal<Record<string, ApprovalState>>({});
  protected notifyError = signal('');
  protected currentPath = signal(window.location.pathname);
  protected inWorkspace = computed(() => WORKSPACE_ROUTES.some(r => this.currentPath() === r || this.currentPath().startsWith(r + '/')));
  protected notifyHint = computed(() => {
    if (!this.notify.enabled()) return this.notify.viaDesktop ? 'Désactivées' : 'Désactivées dans ce navigateur';
    if (this.notify.viaDesktop) return this.notify.actionsSupported() ? 'Windows, avec boutons d\'action' : 'Windows (clic pour ouvrir)';
    return 'Navigateur (clic pour ouvrir)';
  });
  protected roleLabel: Record<string, string> = { director: 'Directeur delivery', delivery_manager: 'Delivery Manager', consultant: 'Consultant' };
  private mainEl = viewChild<ElementRef<HTMLElement>>('mainEl');
  private lastSection = this.section(window.location.pathname);

  constructor() {
    this.translator.start();
    this.router.events.subscribe(e => {
      if (e instanceof NavigationEnd) {
        const path = e.urlAfterRedirects.split(/[?#]/)[0];
        this.currentPath.set(path);
        // Ambient context for the companion (Phase D): tell it which perimeter/project is on screen.
        const seg = path.split('/').filter(Boolean);
        this.companion.scope.set(
          seg[0] === 'p' && seg[1] ? { perimeter_id: seg[1] } :
          seg[0] === 'pr' && seg[1] ? { project_id: seg[1] } : {});
        this.bare.set(this.chromeless(e.urlAfterRedirects));
        if (window.innerWidth <= 900) this.sidebarOpen.set(false);  // close the overlay after navigating on mobile
        this.fadeInRoute(path);
      }
    });
    if (this.auth.mode() === 'dev') {
      this.api.devUsers().subscribe({ next: u => this.users.set(u), error: () => this.users.set([]) });
    }
    effect(() => {
      // Gated: nothing loads (no 401s spamming the console) until the sign-in gate clears.
      if (this.auth.status() !== 'authenticated') return;
      this.session.username();
      this.session.perimetersVersion();
      this.api.me().subscribe({
        next: me => { this.session.me.set(me); this.suspended.set(null); },
        error: e => this.suspended.set(e?.status === 403 ? (e?.error?.detail ?? "Votre accès a été suspendu par un administrateur.") : null),
      });
      this.refreshPerimeters();
      this.loadNotifications();
    });
    // Native toast clicks / toast buttons come back here (desktop IPC or Web Notification onclick).
    this.notify.handle(
      (id, link) => this.onToastOpen(id, link),
      (id, action, link) => void this.onToastAction(id, action, link),
    );
    const timer = setInterval(() => this.loadNotifications(), 60_000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  /** Navigation permission check (UI only — the API enforces the real rules). Hidden until /api/me
   *  has loaded, so items don't flash in then disappear. */
  /** "Automatisations" entry: shown with automation.use, for admins, while /api/me is loading, and
   *  for a (non-unassigned) user whose permission list predates the feature (backend not yet
   *  redeployed with the backfill) — the API still enforces the real rule. */
  protected showAutomations(): boolean {
    const me = this.session.me();
    if (!me || !me.permissions) return true;
    if (me.permissions.includes('automation.use') || me.is_admin) return true;
    return me.role !== 'unassigned' && !me.permissions.some(p => p.startsWith('automation.'));
  }

  /** First page of a grouped sidebar entry the user can open, or null to hide the entry. */
  protected sectionLink(key: SectionKey): string | null {
    const me = this.session.me();
    return me ? visibleTabs(key, this.auth.mode(), me.permissions)[0]?.link ?? null : null;
  }

  /** A grouped entry stays highlighted on every page of its group. */
  protected inSection(key: SectionKey): boolean {
    const path = this.currentPath();
    return SECTIONS[key](this.auth.mode()).some(t => t.link === '/' ? path === '/' : path === t.link || path.startsWith(t.link + '/'));
  }

  protected pendingDecisions = computed(() => this.perimeters().reduce((n, p) => n + (p.pending_decisions || 0), 0));

  protected can(permission: string): boolean {
    const me = this.session.me();
    if (!me) return false;
    return !me.permissions || me.permissions.includes(permission);
  }

  refreshPerimeters() {
    if (this.auth.status() !== 'authenticated') return;
    this.api.portfolio().subscribe(p => this.perimeters.set(p));
  }
  loadNotifications() {
    if (this.auth.status() !== 'authenticated') return; // e.g. the 60s poll firing while the gate is up
    this.api.notifications().subscribe(n => {
      this.notifications.set(n.items);
      const user = this.session.me()?.username;
      if (user) this.toastNew(this.notify.takeNew(user, n.items));
    });
  }
  toggleInbox() { this.closeMenu(); this.inboxOpen.update(v => !v); if (this.inboxOpen()) this.loadNotifications(); }
  readAll() { this.api.markAllRead().subscribe(() => this.loadNotifications()); }
  scan() {
    this.scanning.set(true);
    this.api.runWatch().subscribe({ next: () => { this.scanning.set(false); this.loadNotifications(); }, error: () => this.scanning.set(false) });
  }

  openNote(n: AppNotification) {
    this.markRead(n);
    this.inboxOpen.set(false);
    if (n.link) this.router.navigateByUrl(n.link);
  }

  /** Close the dropdowns on any click outside them (and outside the buttons that toggle them). */
  onDocumentClick(e: MouseEvent) {
    const t = e.target as HTMLElement | null;
    if (!t || !t.isConnected) return;
    if (this.inboxOpen() && !t.closest('.inbox') && !t.closest('.bell')) this.inboxOpen.set(false);
    if (this.menuOpen() && !t.closest('.umenu')) this.closeMenu();
  }

  // ------------------------------------------------------------------ account menu

  protected toggleMenu() {
    if (this.menuOpen()) { this.closeMenu(); return; }
    this.inboxOpen.set(false);
    this.menuOpen.set(true);
  }

  protected closeMenu() {
    this.menuOpen.set(false);
    this.langOpen.set(false);
    this.langQuery.set('');
  }

  protected shortcut = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘K' : 'Ctrl K';

  /** From the Ctrl+K palette: open the account menu straight on its language list. */
  protected openLanguageMenu() {
    this.inboxOpen.set(false);
    this.menuOpen.set(true);
    this.langOpen.set(true);
    setTimeout(() => document.querySelector<HTMLInputElement>('.lsearch')?.focus());
  }

  protected chooseLanguage(code: string) {
    this.translator.setLang(code);
    this.langOpen.set(false);
    this.langQuery.set('');
  }

  protected pickFirstLanguage() {
    const first = this.filteredLanguages()[0];
    if (first) this.chooseLanguage(first.code);
  }

  // ------------------------------------------------------------------ quick actions

  protected quickActions(n: AppNotification): { id: string; label: string; primary?: boolean }[] {
    const me = this.session.me();
    switch (n.kind) {
      case 'approval_required':
        return me?.can_decide && this.runIdOf(n)
          ? [{ id: 'approve', label: 'Approuver…', primary: true }, { id: 'open', label: 'Examiner' }]
          : [{ id: 'open', label: 'Examiner' }];
      case 'user_pending_approval':
        return this.can('users.manage') ? [{ id: 'assign', label: 'Attribuer un rôle', primary: true }] : [];
      case 'ai_budget':
        return [{ id: 'open', label: n.link === '/account' ? 'Voir ma consommation' : 'Ouvrir la gouvernance', primary: !n.read }];
      case 'ai_suggestion':
        return n.read ? [{ id: 'open', label: 'Ouvrir' }] : [{ id: 'open', label: 'Ouvrir', primary: true }, { id: 'dismiss', label: 'Ignorer' }];
      case 'run_completed':
        return [{ id: 'open', label: 'Voir les livrables' }];
      default:
        return [];
    }
  }

  protected runQuickAction(n: AppNotification, action: string) {
    if (action === 'approve') { void this.prepareApproval(n); return; }
    if (action === 'dismiss') { this.markRead(n); return; }
    if (action === 'assign') {
      this.markRead(n);
      this.inboxOpen.set(false);
      this.router.navigateByUrl(n.link || usersLink(this.auth.mode()));
      return;
    }
    this.openNote(n);
  }

  /** Step 1 of "Approuver" in the dropdown: show exactly which pending decisions of that run will be
   *  approved before doing anything. */
  private async prepareApproval(n: AppNotification) {
    const runId = this.runIdOf(n);
    if (!runId) return;
    this.setApproval(n.id, { phase: 'loading' });
    try {
      const recs = await this.pendingRecs(runId);
      if (!recs.length) {
        this.markRead(n);
        this.setApproval(n.id, { phase: 'done', message: 'Rien à valider : ces décisions ont déjà été traitées.' });
      } else {
        this.setApproval(n.id, { phase: 'confirm', recs });
      }
    } catch (err) {
      this.setApproval(n.id, { phase: 'error', message: this.errorText(err) });
    }
  }

  /** Step 2: approve every pending decision via the real endpoint (POST /recommendations/:id/decision,
   *  which checks recommendation.decide + perimeter access server-side). */
  protected async confirmApproval(n: AppNotification) {
    const recs = this.approvals()[n.id]?.recs ?? [];
    this.setApproval(n.id, { phase: 'busy' });
    const res = await this.approveRecs(recs);
    if (res.ok) this.markRead(n);
    this.setApproval(n.id, { phase: res.ok ? 'done' : 'error', message: res.message });
  }

  protected clearApproval(id: string) {
    this.approvals.update(a => { const { [id]: _, ...rest } = a; return rest; });
  }

  private setApproval(id: string, st: ApprovalState) { this.approvals.update(a => ({ ...a, [id]: st })); }

  private async approveRecs(recs: Recommendation[]): Promise<{ ok: boolean; message: string }> {
    let done = 0;
    for (const r of recs) {
      try {
        await firstValueFrom(this.api.decide(r.id, true, 'Approuvé depuis une notification DeliveryOps'));
        done++;
      } catch (err: any) {
        if (err?.status === 409) continue; // decided meanwhile by someone else — not an error for us
        const why = this.errorText(err);
        return { ok: false, message: done ? `${done} approuvée(s), puis échec : ${why}` : `Échec de l'approbation : ${why}` };
      }
    }
    return { ok: true, message: done ? `✓ ${done} décision(s) approuvée(s). Le workflow reprend.` : 'Rien à valider : déjà traité.' };
  }

  private pendingRecs(runId: string): Promise<Recommendation[]> {
    return firstValueFrom(this.api.recommendations({ run_id: runId, status: 'pending' }));
  }

  private runIdOf(n: AppNotification): string | null {
    return /^\/runs\/([^/?#]+)/.exec(n.link || '')?.[1] ?? null;
  }

  private markRead(n: AppNotification) {
    if (n.read) return;
    this.notifications.update(list => list.map(x => x.id === n.id ? { ...x, read: true } : x)); // optimistic
    this.api.markRead(n.id).subscribe({ next: () => this.loadNotifications(), error: () => this.loadNotifications() });
  }

  private errorText(err: any): string {
    if (err?.status === 403) return 'vous n\'avez pas le droit de valider ces décisions.';
    return err?.error?.detail || err?.message || 'erreur inconnue';
  }

  // ------------------------------------------------------------------ native notifications

  protected async toggleDesktopNotifs(on: boolean) {
    this.notifyError.set((await this.notify.setEnabled(on)) ?? '');
  }

  protected testNotification() {
    this.notify.show({ id: 'test-' + Date.now(), title: 'DeliveryOps', body: 'Les notifications bureau sont actives.', link: this.currentPath() });
  }

  /** Toast the notifications that just arrived. Capped per poll; the rest are summarised. */
  private async toastNew(fresh: AppNotification[]) {
    if (!fresh.length || !this.notify.enabled()) return;
    const shown = fresh.length > MAX_TOASTS_PER_POLL ? fresh.slice(-(MAX_TOASTS_PER_POLL - 1)) : fresh;
    if (shown.length < fresh.length) {
      const rest = fresh.length - shown.length;
      this.notify.show({ id: 'summary-' + Date.now(), title: `${rest} autre(s) notification(s) DeliveryOps`,
        body: fresh.slice(0, rest).map(n => n.title).slice(0, 3).join(' · '), link: this.currentPath() });
    }
    for (const n of shown) {
      const toast = await this.toastFor(n);
      if (toast) this.notify.show(toast);
    }
  }

  private async toastFor(n: AppNotification) {
    const me = this.session.me();
    const where = n.perimeter_id ? `${n.client} / ${n.perimeter}` : '';
    const clip = (s: string, max = 180) => (s.length > max ? s.slice(0, max - 1) + '…' : s);
    let title = n.title;
    let body = clip([n.body, where].filter(Boolean).join('\n'));
    let actions: { id: string; label: string }[] = [];
    switch (n.kind) {
      case 'approval_required': {
        const runId = this.runIdOf(n);
        if (me?.can_decide && runId) {
          // Tell the user exactly what "Approuver" on the toast will approve.
          let recs: Recommendation[] = [];
          try { recs = await this.pendingRecs(runId); } catch { /* fall back to the generic body */ }
          if (recs.length) {
            body = clip(`${recs.length} décision(s) : ${recs.map(r => r.title).join(' · ')}${where ? '\n' + where : ''}`, 240);
            actions = [{ id: 'approve', label: `Approuver (${recs.length})` }, { id: 'open', label: 'Examiner' }];
          } else if (runId) {
            return null; // already handled elsewhere — don't nag
          }
        }
        break;
      }
      case 'ai_suggestion':
        title = `✨ Suggestion IA — ${n.title}`;
        actions = [{ id: 'open', label: 'Ouvrir' }];
        break;
      case 'user_pending_approval':
        if (this.can('users.manage')) actions = [{ id: 'open', label: 'Attribuer un rôle' }];
        break;
    }
    return { id: n.id, title, body, link: n.link || '/', actions };
  }

  private onToastOpen(id: string, link: string) {
    if (id.startsWith('summary-')) { this.inboxOpen.set(true); this.loadNotifications(); return; }
    const n = this.notifications().find(x => x.id === id);
    if (n) this.markRead(n);
    if (link) this.router.navigateByUrl(link);
  }

  private async onToastAction(id: string, action: string, link: string) {
    if (action !== 'approve') { this.onToastOpen(id, link); return; }
    let n = this.notifications().find(x => x.id === id);
    const runId = /^\/runs\/([^/?#]+)/.exec(link || '')?.[1];
    if (!runId) { this.onToastOpen(id, link); return; }
    let res: { ok: boolean; message: string };
    try {
      res = await this.approveRecs(await this.pendingRecs(runId));
    } catch (err) {
      res = { ok: false, message: `Échec : ${this.errorText(err)}` };
    }
    if (res.ok && n) this.markRead(n);
    this.notify.show({ id: `approve-result-${id}-${Date.now()}`, title: res.ok ? 'Validation effectuée' : 'Validation impossible',
      body: res.message, link });
    if (!res.ok) this.inboxOpen.set(true);
  }

  // ------------------------------------------------------------------ shell

  toggleSidebar() { this.sidebarOpen.update(v => !v); }
  closeSidebar() { this.sidebarOpen.set(false); }

  chromeless(url: string): boolean { return url.startsWith('/call-assistant') || url.startsWith('/automations/recorder'); }

  /** Top-level section of a path: perimeter tabs (/p/:id/...) share one section so switching tabs
   *  inside a perimeter doesn't fade the whole page, only switching pages/perimeters does. */
  private section(path: string): string {
    const parts = path.split('/').filter(Boolean);
    return parts[0] === 'p' ? `p/${parts[1] ?? ''}` : parts[0] === 'runs' ? `runs/${parts[1] ?? ''}` : (parts[0] ?? '');
  }

  /** Subtle fade/rise of the content area on page change (~180ms, skipped for reduced motion). */
  private fadeInRoute(path: string) {
    const section = this.section(path);
    if (section === this.lastSection) return;
    this.lastSection = section;
    const el = this.mainEl()?.nativeElement;
    if (!el || typeof el.animate !== 'function') return;
    try { if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return; } catch { /* ignore */ }
    el.animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 180, easing: 'cubic-bezier(.2,0,0,1)' });
  }

  openCall() {
    if (this.desktop.available) { void this.desktop.openCallAssistant('/call-assistant'); }
    else { this.router.navigateByUrl('/call-assistant'); }
  }

  signIn() { void this.auth.signInMicrosoft(); }

  /** Pending-access screen: reload /api/me, in case an admin assigned a role meanwhile. */
  protected recheckAccess() {
    this.api.me().subscribe({ next: me => { this.session.me.set(me); if (me.role !== 'unassigned') this.refreshPerimeters(); } });
  }

  switchUser(username: string) {
    this.session.setUser(username);
    this.router.navigateByUrl('/');
  }
}

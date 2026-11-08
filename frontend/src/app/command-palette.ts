import { Component, ElementRef, computed, effect, inject, input, output, signal, viewChild } from '@angular/core';
import { Router } from '@angular/router';

import { Api, DeliveryProject, PortfolioItem, Session } from './api';
import { Auth } from './auth';
import { usersLink } from './section-tabs';
import { Theme } from './theme';
import { UiTranslator } from './ui-translator';

type Group = 'Actions' | 'Pages' | 'Périmètres' | 'Projets';

interface Entry {
  group: Group;
  label: string;
  hint?: string;
  keywords?: string;
  permission?: string;
  link?: string;
  run?: () => void;
}

const MAX_PER_GROUP = 6;

function normalize(s: string): string {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

/** Ctrl+K / ⌘K: jump to any page, périmètre or project, or run a common action, from the keyboard.
 *  Only lists what the user is allowed to open (the API still enforces the real rules). */
@Component({
  selector: 'app-command-palette',
  host: { '(document:keydown)': 'onKey($event)' },
  template: `
    @if (open()) {
      <div class="scrim" (click)="close()" aria-hidden="true"></div>
      <div class="palette" role="dialog" aria-modal="true" aria-label="Recherche rapide">
        <div class="search">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <input #box type="text" role="combobox" aria-autocomplete="list" aria-controls="palette-list" [attr.aria-activedescendant]="'pal-' + active()"
                 placeholder="Rechercher une page, un périmètre, une action…" [value]="query()" (input)="setQuery($any($event.target).value)"
                 (keydown)="onInputKey($event)" />
          <kbd>Échap</kbd>
        </div>
        <div class="results" id="palette-list" role="listbox">
          @for (section of sections(); track section.group) {
            <div class="group">{{ section.group }}</div>
            @for (e of section.items; track e.label + e.link; let i = $index) {
              <button type="button" role="option" [id]="'pal-' + (section.offset + i)" [class.on]="active() === section.offset + i"
                      [attr.aria-selected]="active() === section.offset + i"
                      (mouseenter)="active.set(section.offset + i)" (click)="choose(e)">
                <span class="label" [attr.translate]="e.group === 'Périmètres' || e.group === 'Projets' ? 'no' : null">{{ e.label }}</span>
                @if (e.hint) { <span class="hint" translate="no">{{ e.hint }}</span> }
              </button>
            }
          } @empty {
            <div class="none">Aucun résultat pour « {{ query() }} »</div>
          }
        </div>
        <div class="foot small faint"><kbd>↑</kbd><kbd>↓</kbd> naviguer · <kbd>Entrée</kbd> ouvrir</div>
      </div>
    }
  `,
  styles: `
    .scrim { position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 1100; animation: fade .12s ease; }
    .palette { position: fixed; top: max(72px, 12vh); left: 50%; transform: translateX(-50%); width: min(620px, calc(100vw - 24px));
      background: var(--surface); border: 1px solid var(--border); border-radius: 14px; box-shadow: 0 24px 60px rgba(0,0,0,.3);
      z-index: 1101; display: flex; flex-direction: column; max-height: min(560px, 76vh); animation: rise .14s cubic-bezier(.2,0,0,1); }
    @keyframes fade { from { opacity: 0; } }
    @keyframes rise { from { opacity: 0; transform: translate(-50%, -6px); } }
    .search { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
    .search svg { width: 18px; height: 18px; flex: none; fill: none; stroke: var(--muted); stroke-width: 2; stroke-linecap: round; }
    .search input { border: 0; padding: 6px 0; font-size: 15px; background: transparent; }
    .search input:focus { outline: none; }
    kbd { font: inherit; font-size: 11px; color: var(--muted); border: 1px solid var(--border); border-bottom-width: 2px; border-radius: 5px; padding: 0 5px; background: var(--surface-2); }
    .results { overflow-y: auto; padding: 6px; }
    .group { font-size: 10.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .07em; color: var(--faint); padding: 10px 10px 4px; }
    .results button { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; width: 100%; border: 0; border-radius: 8px;
      background: transparent; color: var(--text); font: inherit; font-size: 13.5px; font-weight: 400; padding: 8px 10px; text-align: start; cursor: pointer; }
    .results button.on { background: var(--accent-soft); color: var(--accent); }
    .hint { font-size: 12px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 45%; }
    .none { padding: 24px; text-align: center; color: var(--faint); }
    .foot { border-top: 1px solid var(--border); padding: 8px 14px; display: flex; gap: 6px; align-items: center; }
    @media (prefers-reduced-motion: reduce) { .scrim, .palette { animation: none; } }
  `,
})
export class CommandPalette {
  private router = inject(Router);
  private api = inject(Api);
  private session = inject(Session);
  private auth = inject(Auth);
  private theme = inject(Theme);
  private translator = inject(UiTranslator);

  readonly perimeters = input<PortfolioItem[]>([]);
  /** Asks the shell to open the account menu on its language list. */
  readonly chooseLanguage = output<void>();
  readonly openCall = output<void>();

  protected open = signal(false);
  protected query = signal('');
  protected active = signal(0);
  private projects = signal<DeliveryProject[]>([]);
  private box = viewChild<ElementRef<HTMLInputElement>>('box');

  constructor() {
    effect(() => { if (this.open()) queueMicrotask(() => this.box()?.nativeElement.focus()); });
  }

  show() {
    this.query.set('');
    this.active.set(0);
    this.open.set(true);
    this.api.projects().subscribe({ next: p => this.projects.set(p), error: () => this.projects.set([]) });
  }

  close() { this.open.set(false); }

  private can(permission?: string): boolean {
    if (!permission) return true;
    const perms = this.session.me()?.permissions;
    return !perms || perms.includes(permission);
  }

  private entries = computed<Entry[]>(() => {
    const me = this.session.me();
    const mode = this.auth.mode();
    const pages: Entry[] = [
      { group: 'Pages', label: 'Portefeuille', link: '/', keywords: 'accueil tableau de bord santé kpi' },
      { group: 'Pages', label: 'Projets', link: '/projects', keywords: 'missions clients' },
      { group: 'Pages', label: 'Validations', link: '/approvals', keywords: 'décisions recommandations approuver' },
      { group: 'Pages', label: 'Compagnon', link: '/companion', permission: 'companion.use', keywords: 'assistant chat' },
      { group: 'Pages', label: 'Mon espace', link: '/workspace', keywords: 'microsoft fichiers compétences' },
      { group: 'Pages', label: 'Microsoft 365', link: '/microsoft', keywords: 'mails agenda outlook teams onedrive' },
      { group: 'Pages', label: 'Fichiers du PC', link: '/local-files', keywords: 'documents indexation' },
      { group: 'Pages', label: 'Automatisations', link: '/automations', keywords: 'workflow robot' },
      { group: 'Pages', label: 'Veille technologique', link: '/veille', permission: 'techwatch.use', keywords: 'actualités news cve tendances' },
      { group: 'Pages', label: 'Règles & conformité', link: '/admin/policy', permission: 'policy.manage', keywords: 'supervision sécurité charte confidentialité' },
      { group: 'Pages', label: 'Observabilité IA', link: '/observability', permission: 'observability.view', keywords: 'supervision tokens coûts' },
      { group: 'Pages', label: 'Journal d\'audit', link: '/audit', permission: 'audit.view', keywords: 'supervision historique traces' },
      { group: 'Pages', label: 'Gouvernance IA', link: '/admin/governance', permission: 'governance.manage', keywords: 'supervision quotas blocage' },
      { group: 'Pages', label: 'Modèles & clés API', link: '/admin/llm', permission: 'governance.manage', keywords: 'supervision llm fournisseur nvidia' },
      { group: 'Pages', label: 'Compagnon · supervision', link: '/admin/companion', permission: 'companion.admin', keywords: 'supervision politique' },
      { group: 'Pages', label: 'Utilisateurs', link: usersLink(mode), permission: 'users.manage', keywords: 'comptes accès' },
      { group: 'Pages', label: 'Rôles & permissions', link: '/admin/roles', permission: 'roles.manage', keywords: 'accès droits' },
      { group: 'Pages', label: 'Catalogue de livrables', link: '/livrables/catalogue', permission: 'deliverables.catalog', keywords: 'documents modèles' },
      { group: 'Pages', label: 'Mon compte', link: '/account', keywords: 'profil mot de passe sécurité paramètres' },
      { group: 'Pages', label: 'Application bureau', link: '/download', keywords: 'télécharger installer windows' },
    ];
    const actions: Entry[] = [
      ...(me?.can_decide ? [{ group: 'Actions' as Group, label: 'Nouveau périmètre', link: '/onboarding', keywords: 'créer ajouter client' }] : []),
      { group: 'Actions', label: 'Nouvelle automatisation', link: '/automations/new', keywords: 'créer workflow' },
      { group: 'Actions', label: 'Nouveau livrable', link: '/livrables/nouveau', permission: 'deliverables.catalog', keywords: 'créer document' },
      { group: 'Actions', label: 'Assistant d\'appel', permission: 'agent.call_assistant', keywords: 'réunion teams appel', run: () => this.openCall.emit() },
      { group: 'Actions', label: 'Changer de langue', keywords: 'language idioma sprache traduction', run: () => this.chooseLanguage.emit() },
      { group: 'Actions', label: 'Changer de thème (clair / sombre)', keywords: 'dark mode nuit apparence', run: () => this.theme.cycle() },
      ...(mode !== 'dev' ? [{ group: 'Actions' as Group, label: 'Se déconnecter', keywords: 'logout quitter', run: () => void this.auth.logout() }] : []),
    ];
    const perimeters: Entry[] = this.perimeters().map(p => ({
      group: 'Périmètres' as Group, label: p.name, hint: [p.client, p.project].filter(Boolean).join(' · '), link: `/p/${p.id}`,
    }));
    const projects: Entry[] = this.projects().map(p => ({
      group: 'Projets' as Group, label: p.name, hint: p.client, link: `/pr/${p.id}`, keywords: (p.technologies ?? []).join(' '),
    }));
    return [...actions, ...pages, ...perimeters, ...projects].filter(e => this.can(e.permission));
  });

  protected sections = computed(() => {
    const words = normalize(this.query().trim()).split(/\s+/).filter(Boolean);
    const scored = this.entries()
      .map(e => {
        const label = normalize(e.label);
        const translated = normalize(this.translator.t(e.label));
        const hay = [label, translated, normalize(e.hint ?? ''), normalize(e.keywords ?? ''), normalize(e.group)].join(' ');
        if (!words.every(w => hay.includes(w))) return null;
        const first = words[0] ?? '';
        const score = !first ? 0 : label.startsWith(first) || translated.startsWith(first) ? 2 : label.includes(first) ? 1 : 0;
        return { e, score };
      })
      .filter((x): x is { e: Entry; score: number } => !!x);
    const order: Group[] = words.length ? ['Pages', 'Périmètres', 'Projets', 'Actions'] : ['Actions', 'Pages', 'Périmètres', 'Projets'];
    let offset = 0;
    return order.map(group => {
      const items = scored.filter(x => x.e.group === group).sort((a, b) => b.score - a.score).slice(0, MAX_PER_GROUP).map(x => x.e);
      const section = { group, items, offset };
      offset += items.length;
      return section;
    }).filter(s => s.items.length);
  });

  private flat = computed(() => this.sections().flatMap(s => s.items));

  protected setQuery(q: string) { this.query.set(q); this.active.set(0); }

  protected choose(e: Entry) {
    this.close();
    if (e.run) e.run();
    else if (e.link) void this.router.navigateByUrl(e.link);
  }

  protected onInputKey(ev: KeyboardEvent) {
    const n = this.flat().length;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); this.active.set(n ? (this.active() + 1) % n : 0); this.scrollActive(); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); this.active.set(n ? (this.active() - 1 + n) % n : 0); this.scrollActive(); }
    else if (ev.key === 'Enter') { ev.preventDefault(); const e = this.flat()[this.active()]; if (e) this.choose(e); }
  }

  private scrollActive() {
    queueMicrotask(() => document.getElementById('pal-' + this.active())?.scrollIntoView({ block: 'nearest' }));
  }

  protected onKey(ev: KeyboardEvent) {
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && ev.key.toLowerCase() === 'k') {
      ev.preventDefault();
      if (this.open()) this.close(); else this.show();
    } else if (ev.key === 'Escape' && this.open()) {
      ev.stopPropagation();
      this.close();
    }
  }
}

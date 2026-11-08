import { Component, computed, inject, input } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { Session } from './api';
import { Auth } from './auth';

export type SectionKey = 'portfolio' | 'supervision' | 'access';

interface Tab { label: string; link: string; permission?: string; }

/** The user administration page depends on who manages identities. */
export function usersLink(authMode: string): string {
  return authMode === 'microsoft' ? '/admin/microsoft-users' : authMode === 'native' ? '/admin/accounts' : '/admin/users';
}

/** Related pages share one sidebar entry and switch between each other with these tabs. Tabs the user
 *  has no permission for are hidden (the API still enforces access), and the bar disappears when only
 *  one tab is left. */
export const SECTIONS: Record<SectionKey, (authMode: string) => Tab[]> = {
  portfolio: () => [
    { label: 'Périmètres', link: '/' },
    { label: 'Projets', link: '/projects' },
  ],
  supervision: () => [
    { label: 'Observabilité IA', link: '/observability', permission: 'observability.view' },
    { label: 'Journal d\'audit', link: '/audit', permission: 'audit.view' },
    { label: 'Gouvernance IA', link: '/admin/governance', permission: 'governance.manage' },
    { label: 'Modèles & clés API', link: '/admin/llm', permission: 'governance.manage' },
    { label: 'Règles & conformité', link: '/admin/policy', permission: 'policy.manage' },
    { label: 'Compagnon', link: '/admin/companion', permission: 'companion.admin' },
  ],
  access: mode => [
    { label: 'Utilisateurs', link: usersLink(mode), permission: 'users.manage' },
    { label: 'Rôles & permissions', link: '/admin/roles', permission: 'roles.manage' },
  ],
};

export function visibleTabs(section: SectionKey, authMode: string, permissions: string[] | undefined): Tab[] {
  return SECTIONS[section](authMode).filter(t => !t.permission || !permissions || permissions.includes(t.permission));
}

@Component({
  selector: 'app-section-tabs',
  imports: [RouterLink, RouterLinkActive],
  template: `
    @if (tabs().length > 1) {
      <nav class="tabs" [attr.aria-label]="'Sections'">
        @for (t of tabs(); track t.link) {
          <a [routerLink]="t.link" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">{{ t.label }}</a>
        }
      </nav>
    }
  `,
  styles: `
    :host { display: block; padding: 14px 28px 0; max-width: 1400px; margin-inline: auto; }
    :host:empty { display: none; }
    .tabs { margin-bottom: 0; }
    @media (max-width: 760px) { :host { padding: 10px 16px 0; } }
  `,
})
export class SectionTabs {
  private session = inject(Session);
  private auth = inject(Auth);
  readonly group = input.required<SectionKey>();
  protected tabs = computed(() => {
    const me = this.session.me();
    return me ? visibleTabs(this.group(), this.auth.mode(), me.permissions) : [];
  });
}

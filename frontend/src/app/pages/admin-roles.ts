import { Component, computed, inject, signal } from '@angular/core';

import { Api, PermissionInfo, RoleInfo } from '../api';

import { SectionTabs } from '../section-tabs';
/** Dynamic RBAC made visible: create roles, tick their permissions in a matrix, rename/delete —
 *  then roles are assigned to users on the Utilisateurs page. This is how teams are built
 *  dynamically (a role = a bundle of permissions granted to whoever holds it). */
@Component({
  selector: 'app-admin-roles',
  imports: [SectionTabs],
  template: `
    <app-section-tabs group="access" />
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Rôles &amp; permissions</h1>
          <p>Créez un rôle, cochez ses permissions, puis attribuez-le aux utilisateurs (page <strong>Utilisateurs</strong>). C'est ainsi qu'on construit des équipes de façon dynamique — sans organigramme figé.</p>
        </div>
      </div>

      <div class="cols">
        <div class="card">
          <div class="card-head"><h2>Rôles ({{ roles().length }})</h2></div>
          <div class="stack">
            @for (r of roles(); track r.id) {
              <button class="rolebtn" [class.on]="selected()?.id === r.id" (click)="select(r)">
                <span class="rl">{{ r.label }} @if (r.locked) { <span class="pill">verrouillé</span> } @else if (r.is_system) { <span class="pill">système</span> }</span>
                <small class="faint">{{ r.permissions.length }} permissions · {{ r.user_count }} utilisateur(s)</small>
              </button>
            } @empty { <p class="empty">Aucun rôle.</p> }
          </div>
          <div class="new">
            <input placeholder="Nouveau rôle (ex. Tech Lead)" [value]="newLabel()" (input)="newLabel.set($any($event.target).value)" (keydown.enter)="create()" />
            <button class="primary sm" (click)="create()" [disabled]="!newLabel().trim()">+ Créer</button>
          </div>
        </div>

        <div class="card">
          @if (selected(); as r) {
            <div class="card-head">
              <div><h2>{{ r.label }}</h2><div class="small faint">{{ r.name }}</div></div>
              <div class="row">
                @if (!r.is_system) {
                  <button class="sm ghost" (click)="rename(r)">Renommer</button>
                  <button class="sm ko" (click)="remove(r)">Supprimer</button>
                }
              </div>
            </div>
            @if (r.locked) {
              <p class="small" style="color: var(--amber)">Ce rôle possède toutes les permissions et ne peut pas être restreint.</p>
            }
            @for (cat of categories(); track cat) {
              <div class="catblock">
                <div class="cathead">
                  <strong>{{ cat }}</strong>
                  <button class="sm ghost" (click)="toggleCat(cat)" [disabled]="r.locked">
                    {{ allIn(cat) ? 'Tout décocher' : 'Tout cocher' }}
                  </button>
                </div>
                <div class="perms">
                  @for (p of byCat()[cat]; track p.code) {
                    <label class="perm" [class.dis]="r.locked">
                      <input type="checkbox" [checked]="draft().has(p.code)" [disabled]="r.locked" (change)="toggle(p.code)" />
                      <span>{{ p.label }}</span>
                    </label>
                  }
                </div>
              </div>
            }
            <div class="row foot">
              <button class="primary" (click)="save()" [disabled]="r.locked || !dirty()">Enregistrer</button>
              <span class="small faint">{{ draft().size }} permission(s)@if (dirty()) { · modifications non enregistrées }</span>
            </div>
          } @else {
            <p class="empty">Sélectionnez un rôle à gauche pour éditer ses permissions.</p>
          }
          @if (message()) { <p class="small" [style.color]="messageErr() ? 'var(--red)' : 'var(--green)'" style="margin-top: 8px">{{ message() }}</p> }
        </div>
      </div>
    </div>
  `,
  styles: `
    .cols { display: grid; grid-template-columns: 300px 1fr; gap: 16px; align-items: start; }
    @media (max-width: 860px) { .cols { grid-template-columns: 1fr; } }
    .rolebtn { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; width: 100%; text-align: left; padding: 9px 11px; }
    .rolebtn.on { border-color: var(--accent); background: var(--accent-soft); }
    .rolebtn .rl { display: flex; align-items: center; gap: 6px; font-weight: 600; }
    .new { display: flex; gap: 8px; margin-top: 12px; }
    .catblock { border-top: 1px solid var(--border); padding: 12px 0; }
    .cathead { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; text-transform: capitalize; }
    .perms { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 6px 14px; }
    .perm { display: flex; align-items: center; gap: 8px; font-size: 13px; cursor: pointer; }
    .perm.dis { opacity: .55; cursor: default; }
    .perm input { width: auto; }
    .foot { margin-top: 14px; gap: 12px; align-items: center; }
  `,
})
export class AdminRolesPage {
  private api = inject(Api);
  protected roles = signal<RoleInfo[]>([]);
  protected perms = signal<PermissionInfo[]>([]);
  protected selected = signal<RoleInfo | null>(null);
  protected draft = signal<Set<string>>(new Set());
  protected newLabel = signal('');
  protected message = signal('');
  protected messageErr = signal(false);

  protected categories = computed(() => {
    const seen: string[] = [];
    for (const p of this.perms()) if (!seen.includes(p.category)) seen.push(p.category);
    return seen;
  });
  protected byCat = computed(() => {
    const m: Record<string, PermissionInfo[]> = {};
    for (const p of this.perms()) (m[p.category] ??= []).push(p);
    return m;
  });
  protected dirty = computed(() => {
    const r = this.selected(); if (!r) return false;
    const a = this.draft(), b = new Set(r.permissions);
    if (a.size !== b.size) return true;
    for (const c of a) if (!b.has(c)) return true;
    return false;
  });

  constructor() {
    this.api.permissionCatalog().subscribe({ next: p => this.perms.set(p), error: () => this.perms.set([]) });
    this.load();
  }

  load(keepId?: string) {
    this.api.roles().subscribe({
      next: rs => {
        this.roles.set(rs);
        const keep = keepId ?? this.selected()?.id;
        const found = rs.find(r => r.id === keep) ?? rs[0] ?? null;
        this.select(found);
      },
      error: () => this.roles.set([]),
    });
  }

  select(r: RoleInfo | null) {
    this.selected.set(r);
    this.draft.set(new Set(r?.permissions ?? []));
  }

  allIn(cat: string): boolean {
    const codes = (this.byCat()[cat] ?? []).map(p => p.code);
    return codes.length > 0 && codes.every(c => this.draft().has(c));
  }

  toggle(code: string) {
    const next = new Set(this.draft());
    next.has(code) ? next.delete(code) : next.add(code);
    this.draft.set(next);
  }

  toggleCat(cat: string) {
    const codes = (this.byCat()[cat] ?? []).map(p => p.code);
    const next = new Set(this.draft());
    const all = codes.every(c => next.has(c));
    for (const c of codes) all ? next.delete(c) : next.add(c);
    this.draft.set(next);
  }

  create() {
    const label = this.newLabel().trim();
    if (!label) return;
    let name = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    if (!/^[a-z]/.test(name)) name = 'r_' + name;
    name = name.slice(0, 38) || 'role';
    this.api.createRole(name, label).subscribe({
      next: r => { this.newLabel.set(''); this.setMessage(`Rôle « ${r.label} » créé.`, false); this.load(r.id); },
      error: e => this.setMessage(e.error?.detail ?? 'Erreur à la création', true),
    });
  }

  save() {
    const r = this.selected(); if (!r) return;
    this.api.setRolePermissions(r.id, [...this.draft()]).subscribe({
      next: () => { this.setMessage(`Permissions de « ${r.label} » enregistrées.`, false); this.load(r.id); },
      error: e => this.setMessage(e.error?.detail ?? 'Erreur à l\'enregistrement', true),
    });
  }

  rename(r: RoleInfo) {
    const label = window.prompt('Nouveau libellé du rôle', r.label)?.trim();
    if (!label || label === r.label) return;
    this.api.renameRole(r.id, label).subscribe({
      next: () => { this.setMessage('Rôle renommé.', false); this.load(r.id); },
      error: e => this.setMessage(e.error?.detail ?? 'Erreur', true),
    });
  }

  remove(r: RoleInfo) {
    if (!window.confirm(`Supprimer le rôle « ${r.label} » ? Cette action est définitive.`)) return;
    this.api.deleteRole(r.id).subscribe({
      next: () => { this.setMessage('Rôle supprimé.', false); this.selected.set(null); this.load(); },
      error: e => this.setMessage(e.error?.detail ?? 'Impossible de supprimer ce rôle', true),
    });
  }

  private setMessage(msg: string, err: boolean) { this.message.set(msg); this.messageErr.set(err); }
}

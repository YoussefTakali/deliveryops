import { Injectable, computed, inject, signal } from '@angular/core';

import { Api, DeliveryProject, PortfolioItem, Session } from '../../api';
import { TeamApi } from '../../team.service';

/** Shared state of one project workspace (provided by the project shell, injected by every tab), so a
 *  change in one tab — a member added, a plan generated — is reflected in the header and other tabs. */
@Injectable()
export class ProjectStore {
  private api = inject(Api);
  private team = inject(TeamApi);
  private session = inject(Session);

  readonly id = signal<string>('');
  readonly project = signal<DeliveryProject | null>(null);
  readonly loadError = signal<string | null>(null);
  /** The project's périmètres as the portfolio sees them (status, scorecard, backlog…). */
  readonly perimeters = signal<PortfolioItem[]>([]);
  /** Every document produced at project level (plan de charge + project-level livrables). */
  readonly docs = signal<any[]>([]);
  readonly plans = computed(() => this.docs().filter(d => d.key === 'plan_de_charge'));
  readonly livrables = signal<{ catalog: any[]; project_items: any[]; perimeters: { id: string; name: string }[]; can_edit: boolean; can_manage_catalog: boolean } | null>(null);
  private names = signal<Record<string, string>>({});

  readonly canDecide = computed(() => !!this.session.me()?.can_decide);
  readonly latestPlan = computed(() => this.plans()[0] ?? null);
  readonly activeLivrables = computed(() => {
    const l = this.livrables();
    return l ? l.catalog.filter(c => c.enabled).length + l.project_items.length : 0;
  });

  constructor() {
    this.team.directory().then(d => this.addNames(d.map(p => [p.owner, p.name]))).catch(() => {});
    this.api.externalPeople().subscribe({ next: r => this.addNames(r.people.map((x: any) => [x.owner, x.name])), error: () => {} });
  }

  private addNames(pairs: [string, string][]) { this.names.update(m => ({ ...m, ...Object.fromEntries(pairs) })); }

  open(id: string) {
    this.id.set(id);
    this.reload();
  }

  reload() {
    const id = this.id();
    if (!id) return;
    this.api.project(id).subscribe({
      next: p => { this.project.set(p); this.loadError.set(null); },
      error: e => { this.project.set(null); this.loadError.set(e?.status === 404 ? 'Projet introuvable ou non accessible.' : 'Chargement impossible.'); },
    });
    this.api.portfolio().subscribe({ next: all => this.perimeters.set(all.filter(p => p.project_id === id)), error: () => {} });
    this.reloadPlans();
    this.reloadLivrables();
  }
  reloadPlans() { this.api.projectDeliverables(this.id()).subscribe({ next: r => this.docs.set(r.deliverables), error: () => this.docs.set([]) }); }
  reloadLivrables() { this.api.projectLivrables(this.id()).subscribe({ next: r => this.livrables.set(r), error: () => this.livrables.set(null) }); }

  name(owner: string) { return this.names()[owner] || owner; }
  initials(label: string) { return label.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map(s => s[0]?.toUpperCase()).join(''); }
  statusLabel(s: string) { return s === 'active' ? 'Actif' : s === 'done' ? 'Terminé' : 'Archivé'; }
  roleLabel(r: string) { return r === 'manager' ? 'Manager' : r === 'lead' ? 'Lead' : 'Membre'; }
}

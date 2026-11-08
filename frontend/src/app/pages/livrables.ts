import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api } from '../api';
import { DebugAi } from '../debug-ai';
import { errorText } from '../http-error';

const FORMAT: Record<string, string> = { docx: 'Word', pptx: 'PowerPoint', xlsx: 'Excel', md: 'Markdown' };

/** Catalogue de livrables — the company-wide set every project picks from: the house method (COPIL,
 *  matrice de couverture, plan de charge) plus the livrables the company added. A livrable specific to
 *  one project lives in that project (its Livrables tab) and can be proposed here afterwards. */
@Component({
  selector: 'app-livrables-catalog',
  imports: [RouterLink, DebugAi],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Catalogue de livrables</h1>
          <p>Les livrables que chaque projet de l'entreprise peut choisir. Un projet peut aussi avoir ses propres livrables : ils se créent depuis l'onglet <strong>Livrables</strong> du projet.</p>
        </div>
        @if (canManage()) { <a class="btn primary" routerLink="/livrables/nouveau">+ Livrable du catalogue</a> }
      </div>
      <app-debug-ai context="Chargement du catalogue de livrables" [error]="error()" />

      <div class="how card">
        <div class="step"><span>1</span><div><strong>Le catalogue</strong><p class="small muted">Méthode maison + livrables ajoutés par l'entreprise. Visible par tous les projets.</p></div></div>
        <div class="step"><span>2</span><div><strong>Le projet choisit</strong><p class="small muted">Chaque projet coche ceux qu'il utilise et peut créer ses livrables propres.</p></div></div>
        <div class="step"><span>3</span><div><strong>Les périmètres produisent</strong><p class="small muted">L'onglet Livrables d'un périmètre propose exactement l'ensemble de son projet.</p></div></div>
      </div>

      <h2 class="sec">Ajoutés par l'entreprise</h2>
      <div class="grid">
        @for (t of custom(); track t.key) {
          <div class="card tpl">
            <div class="row between"><strong>{{ t.title }}</strong><span class="fmt">{{ fmt(t.output) }}</span></div>
            <p class="small muted">{{ t.description || '—' }}</p>
            <p class="small faint">{{ t.inputs.length }} entrée(s) · {{ t.sections?.length ?? 0 }} section(s){{ t.examples?.length ? ' · style appris de ' + t.examples.length + ' exemple(s)' : '' }}</p>
            @if (t.can_edit) {
              <div class="row gap">
                <a class="btn sm" [routerLink]="['/livrables', t.custom_id]">Modifier</a>
                <button class="sm ghost danger" (click)="remove(t)">Supprimer</button>
              </div>
            }
          </div>
        } @empty {
          <div class="card empty-card">
            <p class="small muted">Aucun livrable ajouté au catalogue. Un livrable se décrit comme à un collègue — ce qu'il prend en entrée, ce qu'il produit, un ou deux exemples — et l'IA pose ses questions quand quelque chose n'est pas clair.</p>
            @if (canManage()) { <a class="btn primary" routerLink="/livrables/nouveau">Créer le premier</a> }
          </div>
        }
      </div>

      <h2 class="sec">Méthode maison</h2>
      <div class="grid">
        @for (t of builtins(); track t.key) {
          <div class="card tpl builtin">
            <div class="row between"><strong>{{ t.title }}</strong><span class="fmt">{{ fmt(t.output) }}</span></div>
            <p class="small muted">{{ t.description }}</p>
            <p class="small faint">{{ t.key === 'plan_de_charge' ? 'Document de projet — onglet Plan de charge du projet.' : 'Se génère depuis l’onglet Livrables d’un périmètre.' }}</p>
          </div>
        }
      </div>
      @if (!canManage()) { <p class="small faint">Seuls les administrateurs du catalogue (permission « deliverables.catalog ») y ajoutent des livrables.</p> }
    </div>
  `,
  styles: `
    .how { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 8px; }
    @media (max-width: 800px) { .how { grid-template-columns: 1fr; } }
    .step { display: flex; gap: 10px; align-items: flex-start; }
    .step > span { flex: none; width: 24px; height: 24px; border-radius: 50%; background: var(--accent-soft); color: var(--accent); font-weight: 700; font-size: 12px; display: flex; align-items: center; justify-content: center; }
    .step p { margin: 2px 0 0; }
    .sec { font-size: 13px; margin: 22px 0 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
    .tpl { display: flex; flex-direction: column; gap: 8px; }
    .tpl p { margin: 0; }
    .builtin { background: var(--surface-2); }
    .fmt { font-size: 11px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); font-weight: 500; }
    .row { display: flex; align-items: center; } .between { justify-content: space-between; gap: 8px; } .gap { gap: 8px; }
    .danger { color: var(--red); }
    .empty-card { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
    .empty-card p { margin: 0; }
  `,
})
export class LivrablesCatalogPage {
  private api = inject(Api);
  protected builtins = signal<any[]>([]);
  protected custom = signal<any[]>([]);
  protected canManage = signal(false);
  protected error = signal<string | null>(null);

  constructor() { this.load(); }

  protected fmt(o: string) { return FORMAT[o] ?? o; }
  private load() {
    this.api.livrableCatalog().subscribe({
      next: r => { this.builtins.set(r.builtins); this.custom.set(r.custom); this.canManage.set(r.can_manage); this.error.set(null); },
      error: async e => this.error.set(await errorText(e, 'Impossible de charger le catalogue.')),
    });
  }
  protected remove(t: any) {
    if (!confirm(`Retirer « ${t.title} » du catalogue ? Les projets qui l'utilisaient ne le proposeront plus ; les fichiers déjà générés restent.`)) return;
    this.api.deleteLivrable(t.custom_id).subscribe({ next: () => this.load(), error: async e => this.error.set(await errorText(e, 'Suppression impossible.')) });
  }
}

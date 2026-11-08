import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api } from '../../api';
import { DebugAi } from '../../debug-ai';
import { DeliverableTemplate } from '../../deliverables.service';
import { errorText } from '../../http-error';
import { CustomRun } from '../perimeter/custom-run';
import { ProjectStore } from './store';

const FORMAT: Record<string, string> = { docx: 'Word', pptx: 'PowerPoint', xlsx: 'Excel', md: 'Markdown' };

/** Where one livrable is produced: once for the whole project, or per périmètre (and which ones). */
@Component({
  selector: 'app-level-picker',
  template: `
    <div class="lvl">
      @if (item().level_locked) {
        <span class="chip">{{ item().level === 'project' ? 'Niveau projet' : 'Par périmètre' }}</span>
        <span class="small faint">{{ item().level === 'project' ? 'agrège les données de tous les périmètres' : '' }}</span>
      } @else {
        <div class="seg" role="radiogroup" aria-label="Niveau du livrable">
          <button role="radio" [attr.aria-checked]="item().level === 'project'" [class.on]="item().level === 'project'"
                  [disabled]="!canEdit()" (click)="set('project', null)">Projet</button>
          <button role="radio" [attr.aria-checked]="item().level === 'perimeter'" [class.on]="item().level === 'perimeter'"
                  [disabled]="!canEdit()" (click)="set('perimeter', null)">Périmètres</button>
        </div>
        @if (item().level === 'project') { <span class="small faint">un document pour tout le projet</span> }
      }
      @if (item().level === 'perimeter' && perimeters().length) {
        <div class="perims">
          @for (p of perimeters(); track p.id) {
            <label class="small"><input type="checkbox" [checked]="isOn(p.id)" [disabled]="!canEdit()" (change)="toggle(p.id, $any($event.target).checked)" /> {{ p.name }}</label>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .lvl { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
    .seg { display: inline-flex; padding: 2px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
    .seg button { border: none; background: transparent; padding: 3px 10px; font-size: 12px; border-radius: 6px; color: var(--muted); }
    .seg button.on { background: var(--surface); color: var(--text); font-weight: 600; box-shadow: var(--shadow); }
    .chip { font-size: 11.5px; padding: 2px 9px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); font-weight: 600; }
    .perims { flex-basis: 100%; display: flex; flex-wrap: wrap; gap: 4px 14px; }
    .perims input { width: auto; margin-right: 4px; }
  `,
})
export class LevelPicker {
  item = input.required<any>();
  perimeters = input.required<{ id: string; name: string }[]>();
  canEdit = input(false);
  changed = output<{ level: 'project' | 'perimeter'; perimeter_ids: string[] | null }>();

  protected isOn(id: string) { const ids = this.item().perimeter_ids; return ids == null || ids.includes(id); }
  protected set(level: 'project' | 'perimeter', ids: string[] | null) {
    if (level === this.item().level && ids === null && this.item().perimeter_ids == null) return;
    this.changed.emit({ level, perimeter_ids: ids });
  }
  protected toggle(id: string, on: boolean) {
    const all = this.perimeters().map(p => p.id);
    const cur = this.item().perimeter_ids ?? all;
    const next = on ? [...new Set([...cur, id])] : cur.filter((x: string) => x !== id);
    if (!next.length) return;                                     // at least one périmètre
    this.changed.emit({ level: 'perimeter', perimeter_ids: next.length === all.length ? null : next });
  }
}

/** The project's livrables: the ones it picks from the company catalogue + its own project-only ones.
 *  Each is produced either ONCE for the whole project (fed by all its périmètres — generated here) or per
 *  périmètre, for the périmètres chosen (generated from each périmètre's Livrables tab). */
@Component({
  selector: 'app-project-livrables',
  imports: [RouterLink, DebugAi, LevelPicker, CustomRun, DatePipe],
  template: `
    @if (s.livrables(); as l) {
      @if (running(); as tpl) {
        <div class="card run"><app-custom-run [tpl]="tpl" [projectId]="s.id()" (closed)="running.set(null)" (generated)="s.reloadPlans()" /></div>
      }
      <p class="small muted intro">Les livrables appartiennent au projet : il choisit ceux du <strong>catalogue de l'entreprise</strong> qu'il utilise et peut créer ses <strong>propres livrables</strong>. Pour chacun, choisissez le niveau : <strong>Projet</strong> — un seul document, construit à partir des données de tous les périmètres (comme le plan de charge) et généré ici — ou <strong>Périmètres</strong> — généré dans l'onglet Livrables des périmètres cochés.</p>
      <app-debug-ai [context]="'Livrables du projet « ' + (s.project()?.name ?? '') + ' »'" [error]="error()" />

      <section>
        <div class="sec-head">
          <h2>Propres au projet <span class="count">{{ l.project_items.length }}</span></h2>
          @if (l.can_edit) { <a class="btn primary sm" routerLink="/livrables/nouveau" [queryParams]="{ project: s.id() }">+ Nouveau livrable du projet</a> }
        </div>
        <div class="grid">
          @for (t of l.project_items; track t.key) {
            <div class="card tpl">
              <div class="row between"><strong>{{ t.title }}</strong><span class="fmt">{{ fmt(t.output) }}</span></div>
              <p class="small muted">{{ t.description || '—' }}</p>
              <app-level-picker [item]="t" [perimeters]="l.perimeters" [canEdit]="l.can_edit" (changed)="setScope(t.key, $event)" />
              <div class="row gap">
                @if (t.level === 'project') { <button class="sm primary" (click)="run(t)">Générer</button> }
                @else { <span class="small faint">Se génère depuis l'onglet Livrables {{ where(t, l.perimeters) }}</span> }
                @if (t.can_edit) { <a class="btn sm ghost" [routerLink]="['/livrables', t.custom_id]" [queryParams]="{ project: s.id() }">Modifier</a> }
                @if (l.can_manage_catalog) { <button class="sm ghost" (click)="promote(t)" title="En faire une copie dans le catalogue de l'entreprise">Proposer au catalogue</button> }
                @if (t.can_edit) { <button class="sm ghost danger" (click)="remove(t)">Supprimer</button> }
              </div>
            </div>
          } @empty {
            <div class="card empty-card">
              <p class="small muted">Ce projet n'a pas encore de livrable propre. Un livrable du projet décrit ce qu'il prend en entrée et ce qu'il produit — ex. « rapport mensuel client à partir des comptes-rendus et des tickets ».</p>
              @if (l.can_edit) { <a class="btn primary" routerLink="/livrables/nouveau" [queryParams]="{ project: s.id() }">Créer le premier</a> }
            </div>
          }
        </div>
      </section>

      <section>
        <div class="sec-head">
          <h2>Depuis le catalogue de l'entreprise <span class="count">{{ enabledCount() }} / {{ l.catalog.length }}</span></h2>
          @if (l.can_manage_catalog) { <a class="small" routerLink="/livrables/catalogue">Gérer le catalogue →</a> }
        </div>
        <div class="list card">
          @for (t of l.catalog; track t.key) {
            <div class="item" [class.off]="!t.enabled">
              <input type="checkbox" [attr.aria-label]="'Utiliser ' + t.title" [checked]="t.enabled" [disabled]="!l.can_edit || busy()" (change)="toggle(t.key, $any($event.target).checked)" />
              <div class="grow">
                <div><strong>{{ t.title }}</strong> <span class="fmt">{{ fmt(t.output) }}</span>
                  @if (!t.custom) { <span class="tag">méthode maison</span> }</div>
                <div class="small muted">{{ t.description }}</div>
                @if (t.enabled) {
                  <div class="lvlrow">
                    <app-level-picker [item]="t" [perimeters]="l.perimeters" [canEdit]="l.can_edit" (changed)="setScope(t.key, $event)" />
                    @if (t.level === 'project') {
                      @if (t.key === 'plan_de_charge') { <a class="btn sm" [routerLink]="['/pr', s.id(), 'plan']">Onglet Plan de charge →</a> }
                      @else if (t.custom) { <button class="sm primary" (click)="run(t)">Générer</button> }
                    }
                  </div>
                }
              </div>
            </div>
          }
        </div>
        @if (!l.can_edit) { <p class="small faint">Seuls les responsables du projet choisissent ses livrables et leur niveau.</p> }
      </section>

      @if (docs().length) {
        <section>
          <div class="sec-head"><h2>Documents générés au niveau projet <span class="count">{{ docs().length }}</span></h2></div>
          <div class="card">
            <table>
              <tr><th>Livrable</th><th>Fichier</th><th>Par</th><th>Le</th><th></th></tr>
              @for (d of docs(); track d.id) {
                <tr><td>{{ d.title || d.key }}</td><td class="small">{{ d.filename }}</td><td class="small">{{ s.name(d.created_by) }}</td>
                  <td class="small faint">{{ d.created_at | date: 'dd/MM/yy HH:mm' }}</td>
                  <td><button class="sm ghost" (click)="download(d)">Télécharger</button></td></tr>
              }
            </table>
          </div>
        </section>
      }
    } @else {
      <div class="card"><span class="spin"></span> Chargement des livrables…</div>
    }
  `,
  styles: `
    .intro { max-width: 960px; margin: 0 0 12px; }
    .run { margin-bottom: 16px; }
    section { margin-bottom: 22px; }
    .sec-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
    .sec-head h2 { margin: 0; font-size: 15px; display: flex; align-items: center; gap: 8px; }
    .count { font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 999px; background: var(--surface-2); color: var(--muted); }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
    .tpl { display: flex; flex-direction: column; gap: 10px; } .tpl p { margin: 0; }
    .row { display: flex; align-items: center; } .between { justify-content: space-between; gap: 8px; } .gap { gap: 6px; flex-wrap: wrap; }
    .fmt { font-size: 11px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); font-weight: 500; }
    .tag { font-size: 10.5px; padding: 1px 7px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); margin-left: 4px; }
    .danger { color: var(--red); }
    .empty-card { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
    .list { padding: 4px 0; }
    .item { display: flex; align-items: flex-start; gap: 12px; padding: 12px 18px; border-top: 1px solid var(--border); }
    .item:first-child { border-top: none; }
    .item > input { width: auto; margin-top: 4px; }
    .item.off .grow { opacity: .6; }
    .grow { flex: 1; min-width: 0; }
    .lvlrow { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px; }
  `,
})
export class ProjectLivrables {
  protected s = inject(ProjectStore);
  private api = inject(Api);
  protected busy = signal(false);
  protected error = signal<string | null>(null);
  protected running = signal<DeliverableTemplate | null>(null);
  protected enabledCount = computed(() => (this.s.livrables()?.catalog ?? []).filter(c => c.enabled).length);
  /** Project-level documents other than the plan de charge (which has its own tab). */
  protected docs = computed(() => this.s.docs().filter(d => d.key !== 'plan_de_charge'));

  protected fmt(o: string) { return FORMAT[o] ?? o; }
  protected where(t: any, perims: { id: string; name: string }[]) {
    if (t.perimeter_ids == null) return 'de chaque périmètre';
    const names = perims.filter(p => t.perimeter_ids.includes(p.id)).map(p => p.name);
    return names.length === 1 ? `du périmètre ${names[0]}` : `des périmètres : ${names.join(', ')}`;
  }
  protected run(t: DeliverableTemplate) { this.running.set(t); window.scrollTo({ top: 0, behavior: 'smooth' }); }

  protected setScope(key: string, e: { level: 'project' | 'perimeter'; perimeter_ids: string[] | null }) {
    this.api.setLivrableScope(this.s.id(), key, e.level, e.perimeter_ids).subscribe({
      next: r => { this.s.livrables.set(r); this.error.set(null); },
      error: async err => { this.error.set(await errorText(err)); this.s.reloadLivrables(); },
    });
  }
  protected toggle(key: string, on: boolean) {
    const current = (this.s.livrables()?.catalog ?? []).filter(c => c.enabled).map(c => c.key);
    const keys = on ? [...current, key] : current.filter(k => k !== key);
    this.busy.set(true);
    this.api.setProjectLivrables(this.s.id(), keys).subscribe({
      next: r => { this.s.livrables.set(r); this.busy.set(false); this.error.set(null); },
      error: async e => { this.busy.set(false); this.error.set(await errorText(e)); this.s.reloadLivrables(); },
    });
  }
  protected download(d: any) {
    this.api.projectDeliverableFile(this.s.id(), d.id).subscribe({
      next: (res: any) => {
        const url = URL.createObjectURL(res.body as Blob);
        Object.assign(document.createElement('a'), { href: url, download: d.filename || 'livrable' }).click();
        URL.revokeObjectURL(url);
      },
      error: async e => this.error.set(await errorText(e, 'Téléchargement impossible.')),
    });
  }
  protected promote(t: any) {
    if (!confirm(`Ajouter une copie de « ${t.title} » au catalogue de l'entreprise ? Tous les projets pourront l'utiliser.`)) return;
    this.api.promoteLivrable(t.custom_id).subscribe({ next: () => this.s.reloadLivrables(), error: async e => this.error.set(await errorText(e)) });
  }
  protected remove(t: any) {
    if (!confirm(`Supprimer « ${t.title} » ? Les fichiers déjà générés restent disponibles.`)) return;
    this.api.deleteLivrable(t.custom_id).subscribe({ next: () => this.s.reloadLivrables(), error: async e => this.error.set(await errorText(e)) });
  }
}

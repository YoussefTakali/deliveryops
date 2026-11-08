import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api, DeliveryProject } from '../../api';

/** Équipe tab of a périmètre: who from the PROJECT team works on this périmètre, with which profile and
 *  weekly capacity (that's what capacity, billing and the plan de charge use). The team itself — users or
 *  people entered by hand, their skills, the project technologies — is managed once, on the project
 *  (onglet Équipe du projet). A périmètre outside any project keeps a free search + manual entry. */
@Component({
  selector: 'app-equipe-tab',
  imports: [RouterLink],
  template: `
    <div class="stack">
      <div class="card">
        <h2>Affectés à ce périmètre</h2>
        @if (members().length) {
          <table class="tbl">
            <thead><tr><th>Ressource</th><th>Profil</th><th>Compétences</th><th>Capacité / sem.</th><th></th></tr></thead>
            <tbody>
              @for (m of members(); track m.id) {
                <tr>
                  <td><strong>{{ m.name }}</strong>
                    @if (isExt(m.owner)) { <span class="pill ext" title="Saisi manuellement — sans compte">sans compte</span> }</td>
                  <td>{{ m.profile }}</td>
                  <td class="small">{{ m.skills.join(', ') || '—' }}</td>
                  <td>{{ m.weekly_capacity_h }} h</td>
                  <td><button class="sm ghost" (click)="remove(m)">Retirer</button></td>
                </tr>
              }
            </tbody>
          </table>
        } @else { <p class="small muted">Personne n'est encore affecté à ce périmètre.</p> }
      </div>

      @if (project(); as p) {
        <div class="card">
          <div class="card-head">
            <div><h2>Affecter depuis l'équipe du projet</h2>
              <p class="small muted">Classés selon les technologies du projet ({{ p.technologies.join(', ') || 'aucune renseignée' }}), puis la disponibilité.</p></div>
            <a class="small" [routerLink]="['/pr', p.id, 'equipe']">Gérer l'équipe du projet →</a>
          </div>
          <div class="row gap opts">
            <label class="small">Profil <select [value]="profile()" (change)="profile.set($any($event.target).value)">
              @for (pr of profiles; track pr) { <option [value]="pr" [selected]="pr === profile()">{{ pr }}</option> }</select></label>
            <label class="small">Capacité <input type="number" min="1" max="60" [value]="capacity()" (input)="capacity.set(+$any($event.target).value)" /> h / sem.</label>
            <span class="small faint">(les personnes saisies manuellement gardent leur propre profil et capacité)</span>
          </div>
          <table class="tbl">
            <thead><tr><th>Personne</th><th>Compétences utiles</th><th>Disponibilité</th><th></th></tr></thead>
            <tbody>
              @for (c of ranked(); track c.owner) {
                <tr>
                  <td><strong>{{ c.name }}</strong> @if (c.is_external) { <span class="pill ext">sans compte</span> }
                    <div class="small faint">{{ roleLabel(c.role) }}</div></td>
                  <td class="small">@if (c.matched.length) { {{ c.matched.join(', ') }} <span class="score">{{ c.score }}</span> } @else { <span class="faint">—</span> }</td>
                  <td class="small">@if (c.availability) { {{ c.availability.free_weekly_h }} h libres / sem.
                      @if (c.availability.absent_days_4w) { <span class="warn">· {{ c.availability.absent_days_4w }} j d'absence</span> } } @else { <span class="faint">—</span> }</td>
                  <td>@if (isStaffed(c.owner)) { <span class="pill">affecté</span> } @else { <button class="sm primary" (click)="add(c.owner)">Affecter</button> }</td>
                </tr>
              } @empty { <tr><td colspan="4" class="small muted">L'équipe du projet est vide : ajoutez des personnes dans l'onglet Équipe du projet.</td></tr> }
            </tbody>
          </table>
          @if (error()) { <p class="small warn">{{ error() }}</p> }
        </div>
      } @else if (loaded()) {
        <!-- périmètre outside any project: no project team to pick from -->
        <div class="card">
          <h2>Trouver qui peut travailler sur ce périmètre</h2>
          <p class="small muted">Ce périmètre n'est rattaché à aucun projet : recherchez dans la matrice de compétences (compétences déclarées dans « Mon espace »).</p>
          <form class="row gap" (submit)="$event.preventDefault(); search()">
            <input class="grow" [value]="tech()" (input)="tech.set($any($event.target).value)" placeholder="ex. Angular, Java, PostgreSQL" />
            <select [value]="profile()" (change)="profile.set($any($event.target).value)">@for (pr of profiles; track pr) { <option [value]="pr" [selected]="pr === profile()">{{ pr }}</option> }</select>
            <input type="number" min="1" max="60" [value]="capacity()" (input)="capacity.set(+$any($event.target).value)" title="Capacité hebdomadaire (h)" style="width:80px" />
            <button class="primary" [disabled]="busy()">@if (busy()) { <span class="spin"></span> } Rechercher</button>
          </form>
          @if (candidates(); as list) {
            <table class="tbl">
              <tbody>
                @for (c of list; track c.owner) {
                  <tr><td><strong>{{ c.name }}</strong></td><td class="small">{{ c.matched.join(', ') || '—' }}</td>
                    <td class="small">{{ c.availability.free_weekly_h }} h libres / sem.</td>
                    <td>@if (isStaffed(c.owner)) { <span class="pill">affecté</span> } @else { <button class="sm primary" (click)="add(c.owner)">Affecter</button> }</td></tr>
                } @empty { <tr><td class="small muted">Personne ne déclare ces compétences.</td></tr> }
              </tbody>
            </table>
          }
          <details class="manual-box">
            <summary class="small"><strong>+ Ajouter quelqu'un qui n'a pas de compte</strong></summary>
            <div class="manual">
              <input placeholder="Nom et prénom *" [value]="mName()" (input)="mName.set($any($event.target).value)" />
              <input placeholder="E-mail (facultatif)" [value]="mEmail()" (input)="mEmail.set($any($event.target).value)" />
              <input class="wide" placeholder="Compétences, séparées par des virgules" [value]="mSkills()" (input)="mSkills.set($any($event.target).value)" />
              <button class="primary sm" (click)="addManual()" [disabled]="!mName().trim()">Affecter</button>
            </div>
          </details>
          @if (error()) { <p class="small warn">{{ error() }}</p> }
        </div>
      }
    </div>
  `,
  styles: `
    .stack { display: flex; flex-direction: column; gap: 14px; } .card h2 { margin: 0 0 4px; font-size: 15px; }
    .card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
    .card-head p { margin: 0; }
    .tbl { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; } .tbl th, .tbl td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
    .row { display: flex; align-items: center; } .row.gap { gap: 10px; flex-wrap: wrap; } .grow { flex: 1; min-width: 220px; }
    .opts { margin-top: 10px; } .opts select, .opts input { width: auto; margin-left: 4px; } .opts input { width: 64px; }
    .pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--accent); color: var(--accent); }
    .pill.ext { border-color: var(--amber); color: var(--amber); margin-left: 6px; }
    .score { font-size: 10.5px; font-weight: 700; background: var(--accent-soft); color: var(--accent); border-radius: 999px; padding: 0 6px; margin-left: 4px; }
    .warn { color: var(--amber); }
    .manual-box { margin-top: 12px; border-top: 1px dashed var(--border); padding-top: 10px; } .manual-box summary { cursor: pointer; }
    .manual { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; } .manual .wide { grid-column: 1 / -1; }
  `,
})
export class EquipeTab {
  private api = inject(Api);
  perimeterId = input.required<string>();
  protected members = signal<any[]>([]);
  protected project = signal<DeliveryProject | null>(null);
  protected loaded = signal(false);
  private matches = signal<Record<string, any>>({});
  protected candidates = signal<any[] | null>(null);
  protected tech = signal(''); protected profile = signal('confirmed'); protected capacity = signal(35);
  protected busy = signal(false); protected error = signal<string | null>(null);
  protected profiles = ['junior', 'confirmed', 'senior', 'architect'];
  protected mName = signal(''); protected mEmail = signal(''); protected mSkills = signal('');

  /** Project members ranked by their fit with the project technologies, then availability. */
  protected ranked = computed(() => {
    const p = this.project(); if (!p) return [];
    const m = this.matches();
    return (p.members ?? []).map((pm: any) => {
      const hit = m[pm.owner];
      return { owner: pm.owner, name: pm.name || pm.owner, is_external: pm.is_external, role: pm.role_in_project,
               matched: hit?.matched ?? [], score: hit?.score ?? 0, availability: hit?.availability ?? null };
    }).sort((a: any, b: any) => b.score - a.score || (b.availability?.free_weekly_h ?? 0) - (a.availability?.free_weekly_h ?? 0));
  });

  constructor() {
    effect(() => {
      const id = this.perimeterId();
      this.load(id);
      this.api.perimeter(id).subscribe({
        next: per => {
          if (!per.project_id) { this.project.set(null); this.loaded.set(true); return; }
          this.api.project(per.project_id).subscribe({
            next: p => {
              this.project.set(p); this.loaded.set(true);
              this.api.matchSkills(p.technologies, id).subscribe({
                next: r => this.matches.set(Object.fromEntries(r.people.map(x => [x.owner, x]))), error: () => {},
              });
            },
            error: () => { this.project.set(null); this.loaded.set(true); },
          });
        },
        error: () => this.loaded.set(true),
      });
    });
  }

  protected isExt(owner: string | null | undefined) { return !!owner && owner.startsWith('ext:'); }
  protected roleLabel(r: string) { return ({ manager: 'Manager', lead: 'Lead', member: 'Membre' } as Record<string, string>)[r] ?? r; }
  private load(id: string) { this.api.staff(id).subscribe({ next: r => this.members.set(r.members), error: () => this.members.set([]) }); }
  protected isStaffed(owner: string) { return this.members().some(m => m.owner === owner); }

  protected search() {
    const techs = this.tech().split(/[,;]/).map(t => t.trim()).filter(Boolean);
    this.busy.set(true); this.error.set(null);
    this.api.matchSkills(techs, this.perimeterId()).subscribe({
      next: r => { this.candidates.set(r.people); this.busy.set(false); },
      error: () => { this.error.set('Recherche impossible.'); this.busy.set(false); },
    });
  }
  protected add(owner: string) {
    this.api.addStaff(this.perimeterId(), { owners: [owner], profile: this.profile(), weekly_capacity_h: this.capacity() })
      .subscribe({ next: r => { this.members.set(r.members); this.error.set(null); }, error: () => this.error.set('Affectation impossible (droits insuffisants ?).') });
  }
  protected addManual() {
    const skills = this.mSkills().split(/[,;]/).map(t => t.trim()).filter(Boolean);
    this.api.addManualStaff(this.perimeterId(), { name: this.mName().trim(), email: this.mEmail().trim(), profile: this.profile(),
                                                  weekly_capacity_h: this.capacity(), skills }).subscribe({
      next: r => { this.members.set(r.members); this.mName.set(''); this.mEmail.set(''); this.mSkills.set(''); this.error.set(null); },
      error: e => this.error.set(e?.error?.detail ?? 'Ajout impossible (droits insuffisants ?).'),
    });
  }
  protected remove(m: any) {
    this.api.removeStaff(this.perimeterId(), m.id).subscribe({ next: () => this.load(this.perimeterId()), error: () => this.error.set('Retrait impossible.') });
  }
}

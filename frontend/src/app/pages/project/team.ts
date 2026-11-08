import { Component, computed, effect, inject, signal, untracked } from '@angular/core';

import { Api } from '../../api';
import { DebugAi } from '../../debug-ai';
import { TeamApi } from '../../team.service';
import { ProjectStore } from './store';

/** The project team. Members are platform users OR people entered by hand (not every company gives the
 *  app to every employee). They can work on any périmètre of the project; closing the project frees them. */
@Component({
  selector: 'app-project-team',
  imports: [DebugAi],
  template: `
    @if (s.project(); as p) {
      <div class="card techs">
        <div class="card-head"><div><h2>Technologies du projet</h2>
          <p class="small muted">Elles classent les personnes par compétence quand on les affecte à un périmètre, et orientent le plan de charge.</p></div></div>
        @if (s.canDecide()) {
          <div class="row gap">
            <input placeholder="Ex. Java 17, Angular, PostgreSQL" [value]="techEdit()" (input)="techEdit.set($any($event.target).value)" (keydown.enter)="saveTechs()" />
            <button class="sm" (click)="saveTechs()">Enregistrer</button>
          </div>
        }
        <div class="chips">@for (t of p.technologies; track t) { <span class="chip">{{ t }}</span> } @empty { <span class="faint small">Aucune technologie renseignée.</span> }</div>
      </div>
      <div class="grid">
        @for (m of p.members ?? []; track m.id) {
          <div class="card member">
            <div class="top">
              <span class="av" [class.ext]="m.is_external">{{ s.initials(m.name || s.name(m.owner)) }}</span>
              <div class="grow">
                <strong>{{ m.name || s.name(m.owner) }}</strong>
                <div class="small faint">{{ s.roleLabel(m.role_in_project) }}@if (m.is_external) { · <span class="ext">sans compte</span> }</div>
              </div>
              @if (s.canDecide() && m.owner !== p.manager) { <button class="icon" title="Retirer du projet" (click)="remove(m.owner, m.name || s.name(m.owner))">×</button> }
            </div>
            @if (m.is_external && (m.profile || m.email)) {
              <div class="small faint">{{ m.profile }}{{ m.weekly_capacity_h ? ' · ' + m.weekly_capacity_h + ' h/sem.' : '' }}{{ m.email ? ' · ' + m.email : '' }}</div>
            }
            <div class="skills">
              @for (sk of m.skills ?? []; track sk.id) { <span class="skill" [title]="'Niveau ' + sk.level + '/5'">{{ sk.skill }}<span class="lvl">{{ sk.level }}</span></span> }
              @empty { <span class="small faint">Pas de compétences déclarées</span> }
            </div>
          </div>
        }
      </div>

      @if (s.canDecide()) {
        <div class="card add">
          <div class="card-head"><h2>Ajouter au projet</h2>
            <div class="modes" role="tablist">
              <button role="tab" [class.on]="mode() === 'user'" (click)="mode.set('user')">Utilisateur existant</button>
              <button role="tab" [class.on]="mode() === 'manual'" (click)="mode.set('manual')">Saisie manuelle</button>
            </div>
          </div>
          @if (mode() === 'user') {
            <div class="row gap">
              <select [value]="owner()" (change)="owner.set($any($event.target).value)">
                <option value="">— Choisir une personne —</option>
                <optgroup label="Utilisateurs de la plateforme">
                  @for (d of candidates(); track d.owner) { <option [value]="d.owner">{{ d.name }}</option> }
                </optgroup>
                @if (externals().length) {
                  <optgroup label="Saisis manuellement (sans compte)">
                    @for (x of externals(); track x.owner) { <option [value]="x.owner">{{ x.name }}{{ x.profile ? ' · ' + x.profile : '' }}</option> }
                  </optgroup>
                }
              </select>
              <select class="role" [value]="role()" (change)="role.set($any($event.target).value)">
                <option value="member">Membre</option><option value="lead">Lead</option><option value="manager">Manager</option>
              </select>
              <button class="primary" (click)="addUser()" [disabled]="!owner()">Ajouter</button>
            </div>
          } @else {
            <p class="small faint">Pour une personne qui n'a pas de compte — toutes les entreprises ne donnent pas l'application à tous leurs collaborateurs.</p>
            <div class="manual">
              <input placeholder="Nom et prénom *" [value]="mName()" (input)="mName.set($any($event.target).value)" />
              <input placeholder="E-mail (facultatif)" [value]="mEmail()" (input)="mEmail.set($any($event.target).value)" />
              <select [value]="mProfile()" (change)="mProfile.set($any($event.target).value)" title="Profil (grille tarifaire)">
                @for (pr of profiles; track pr) { <option [value]="pr">{{ pr }}</option> }
              </select>
              <input type="number" min="1" max="60" [value]="mCapacity()" (input)="mCapacity.set(+$any($event.target).value)" title="Capacité hebdomadaire (h)" />
              <input class="wide" placeholder="Compétences, séparées par des virgules (ex. Java, Kafka)" [value]="mSkills()" (input)="mSkills.set($any($event.target).value)" />
              <select [value]="role()" (change)="role.set($any($event.target).value)">
                <option value="member">Membre</option><option value="lead">Lead</option><option value="manager">Manager</option>
              </select>
              <button class="primary" (click)="addManual()" [disabled]="!mName().trim()">Ajouter</button>
            </div>
          }
          @if (msg()) { <p class="small ok">{{ msg() }}</p> }
          <app-debug-ai [context]="'Ajout d’un membre à l’équipe du projet « ' + p.name + ' »'" [error]="error()" />
        </div>
      }
    }
  `,
  styles: `
    .techs { margin-bottom: 16px; } .techs .card-head p { margin: 4px 0 0; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
    .chip { font-size: 12px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; padding: 2px 10px; }
    .row input { flex: 1; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-bottom: 16px; }
    .member { display: flex; flex-direction: column; gap: 8px; }
    .top { display: flex; align-items: center; gap: 10px; }
    .grow { flex: 1; min-width: 0; }
    .av { display: inline-flex; align-items: center; justify-content: center; width: 40px; height: 40px; border-radius: 50%; background: var(--accent-soft); color: var(--accent); font-weight: 700; flex: none; }
    .av.ext { background: var(--amber-soft); color: var(--amber); }
    .ext { color: var(--amber); }
    .icon { border: none; background: none; font-size: 18px; color: var(--muted); cursor: pointer; padding: 0 4px; }
    .icon:hover { color: var(--red); }
    .skills { display: flex; flex-wrap: wrap; gap: 5px; }
    .skill { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; padding: 2px 4px 2px 9px; border-radius: 999px; background: var(--surface-2); border: 1px solid var(--border); }
    .lvl { font-size: 10px; font-weight: 700; background: var(--accent-soft); color: var(--accent); border-radius: 999px; padding: 0 6px; }
    .add .card-head { flex-wrap: wrap; gap: 8px; }
    .modes { display: inline-flex; gap: 2px; padding: 2px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface-2); }
    .modes button { border: none; background: transparent; padding: 5px 12px; font-size: 12.5px; border-radius: 7px; color: var(--muted); }
    .modes button.on { background: var(--surface); color: var(--text); box-shadow: var(--shadow); font-weight: 600; }
    .row { display: flex; align-items: center; } .gap { gap: 8px; } .row select:first-child { flex: 1; } .role { width: auto; }
    .manual { display: grid; grid-template-columns: 1.4fr 1.4fr 1fr 80px; gap: 8px; }
    .manual .wide { grid-column: 1 / 3; }
    @media (max-width: 700px) { .manual { grid-template-columns: 1fr 1fr; } .manual .wide { grid-column: 1 / -1; } }
    .ok { color: var(--green); }
  `,
})
export class ProjectTeam {
  protected s = inject(ProjectStore);
  private api = inject(Api);
  private team = inject(TeamApi);
  protected mode = signal<'user' | 'manual'>('user');
  protected directory = signal<{ owner: string; name: string }[]>([]);
  protected externals = signal<any[]>([]);
  protected owner = signal(''); protected role = signal('member');
  protected mName = signal(''); protected mEmail = signal(''); protected mProfile = signal('confirmed');
  protected mCapacity = signal(35); protected mSkills = signal('');
  protected profiles = ['junior', 'confirmed', 'senior', 'architect'];
  protected msg = signal(''); protected error = signal<string | null>(null);
  /** Users not already on the team. */
  protected candidates = computed(() => {
    const on = new Set((this.s.project()?.members ?? []).map(m => m.owner));
    return this.directory().filter(d => !on.has(d.owner));
  });

  protected techEdit = signal('');
  constructor() {
    effect(() => { const t = this.s.project()?.technologies ?? []; untracked(() => this.techEdit.set(t.join(', '))); });
    this.team.directory().then(d => this.directory.set(d)).catch(() => {});
    this.loadExternals();
  }
  protected saveTechs() {
    const technologies = this.techEdit().split(/[,;]/).map(t => t.trim()).filter(Boolean);
    this.api.updateProject(this.s.id(), { technologies }).subscribe({ next: p => { this.s.project.set(p); this.ok('Technologies enregistrées.'); }, error: this.fail });
  }
  private loadExternals() { this.api.externalPeople().subscribe({ next: r => this.externals.set(r.people), error: () => {} }); }
  private ok(m: string) { this.msg.set(m); this.error.set(null); setTimeout(() => this.msg.set(''), 2500); }
  private fail = (e: any) => this.error.set(e?.error?.detail ?? 'Action impossible (droits insuffisants ?)');

  protected addUser() {
    this.api.addProjectMember(this.s.id(), { owner: this.owner(), role_in_project: this.role() }).subscribe({
      next: p => { this.s.project.set(p); this.owner.set(''); this.ok('Membre ajouté.'); }, error: this.fail,
    });
  }
  protected addManual() {
    const who = this.mName().trim();
    const skills = this.mSkills().split(/[,;]/).map(t => t.trim()).filter(Boolean);
    this.api.addProjectManualMember(this.s.id(), { name: who, email: this.mEmail().trim(), profile: this.mProfile(), weekly_capacity_h: this.mCapacity(), skills }, this.role())
      .subscribe({
        next: p => { this.s.project.set(p); this.ok(`${who} a été ajouté(e) à l'équipe.`); this.mName.set(''); this.mEmail.set(''); this.mSkills.set(''); this.loadExternals(); },
        error: this.fail,
      });
  }
  protected remove(owner: string, label: string) {
    if (!confirm(`Retirer ${label} de l'équipe du projet ?`)) return;
    this.api.removeProjectMember(this.s.id(), owner).subscribe({ next: p => this.s.project.set(p), error: this.fail });
  }
}

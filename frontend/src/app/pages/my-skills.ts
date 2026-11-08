import { Component, inject, signal } from '@angular/core';

import { Api } from '../api';

type Skill = { skill: string; level: number; category: string };

const CATEGORY: Record<string, string> = { technology: 'Technologie', functional: 'Fonctionnel', language: 'Langue', method: 'Méthode' };

/** "Mes compétences" — each user's line in the competence matrix. It's what ranks people when they're
 *  staffed on a périmètre and what the plan de charge uses to spread the work. */
@Component({
  selector: 'app-my-skills',
  template: `
    <div class="card">
      <div class="card-head"><div><h2>Mes compétences</h2>
        <p class="small muted">Technologies, domaines fonctionnels, langues, méthodes — niveau de 1 à 5. Elles servent à vous proposer sur les bons projets et à répartir le plan de charge.</p></div>
        @if (saved()) { <span class="small ok">Enregistré</span> }
      </div>
      <div class="chips">
        @for (sk of skills(); track sk.skill) {
          <span class="skill" [title]="category(sk.category)"><strong>{{ sk.skill }}</strong>
            <select [value]="sk.level" (change)="setLevel(sk.skill, +$any($event.target).value)" [attr.aria-label]="'Niveau ' + sk.skill">
              @for (l of levels; track l) { <option [value]="l" [selected]="l === sk.level">{{ l }}</option> }
            </select>
            <button class="x" (click)="remove(sk.skill)" [attr.aria-label]="'Retirer ' + sk.skill">×</button></span>
        } @empty { <span class="small muted">Aucune compétence déclarée.</span> }
      </div>
      <form class="add" (submit)="$event.preventDefault(); add()">
        <input [value]="name()" (input)="name.set($any($event.target).value)" placeholder="ex. Angular, SAP FI, Anglais, Scrum" aria-label="Nouvelle compétence" />
        <select [value]="level()" (change)="level.set(+$any($event.target).value)" aria-label="Niveau">@for (l of levels; track l) { <option [value]="l" [selected]="l === level()">Niveau {{ l }}</option> }</select>
        <select [value]="cat()" (change)="cat.set($any($event.target).value)" aria-label="Catégorie">
          @for (c of cats; track c[0]) { <option [value]="c[0]" [selected]="c[0] === cat()">{{ c[1] }}</option> }
        </select>
        <button class="sm primary" [disabled]="!name().trim()">Ajouter</button>
      </form>
      @if (error()) { <p class="small warn">{{ error() }}</p> }
    </div>
  `,
  styles: `
    .card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; } .card-head p { margin: 4px 0 0; }
    .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .skill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 6px 4px 10px; border-radius: 999px; background: var(--surface-2); border: 1px solid var(--border); font-size: 12.5px; }
    .skill select { width: auto; font-size: 12px; padding: 1px 4px; }
    .x { border: none; background: none; cursor: pointer; color: var(--muted); font-size: 15px; padding: 0 4px; } .x:hover { color: var(--red); }
    .add { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; } .add input { flex: 1; min-width: 200px; } .add select { width: auto; }
    .ok { color: var(--green); } .warn { color: var(--amber); }
  `,
})
export class MySkills {
  private api = inject(Api);
  protected skills = signal<Skill[]>([]);
  protected name = signal(''); protected level = signal(3); protected cat = signal('technology');
  protected saved = signal(false); protected error = signal<string | null>(null);
  protected levels = [1, 2, 3, 4, 5];
  protected cats = Object.entries(CATEGORY);

  constructor() { this.api.mySkills().subscribe({ next: r => this.skills.set(r.skills), error: () => {} }); }

  protected category(c: string) { return CATEGORY[c] ?? c; }
  private save(next: Skill[]) {
    this.skills.set(next);
    this.api.setMySkills(next).subscribe({
      next: r => { this.skills.set(r.skills); this.error.set(null); this.saved.set(true); setTimeout(() => this.saved.set(false), 1500); },
      error: () => this.error.set('Compétences non enregistrées.'),
    });
  }
  protected add() {
    const n = this.name().trim(); if (!n) return;
    this.save([...this.skills().filter(s => s.skill.toLowerCase() !== n.toLowerCase()), { skill: n, level: this.level(), category: this.cat() }]);
    this.name.set('');
  }
  protected setLevel(skill: string, level: number) { this.save(this.skills().map(s => s.skill === skill ? { ...s, level } : s)); }
  protected remove(skill: string) { this.save(this.skills().filter(s => s.skill !== skill)); }
}

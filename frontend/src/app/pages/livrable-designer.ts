import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { Api } from '../api';
import { AutomationApi } from '../automations/automation-api';
import { Choices, seedRecommended } from '../choices';
import { DebugAi } from '../debug-ai';
import { CustomSpec, DeliverableInput, DeliverableQuestion, Deliverables } from '../deliverables.service';
import { errorText } from '../http-error';

type Step = 'describe' | 'working' | 'questions' | 'review';

/** Design (or edit) your own livrable, step by step:
 *   1. say in your own words what it takes as input and what it produces, and add examples of your style;
 *   2. the AI asks what is unclear — one question at a time, its recommended answer pre-selected;
 *   3. review / adjust the resulting model (format, inputs, sections, rules) and save it. */
@Component({
  selector: 'app-livrable-designer',
  imports: [Choices, DebugAi, RouterLink],
  template: `
    <div class="page narrow">
      <div class="page-head">
        <div>
          <a class="small back" [routerLink]="backLink()">← {{ scopeProject() ? 'Livrables du projet' : 'Catalogue de livrables' }}</a>
          <h1>{{ id() ? 'Modifier le livrable' : 'Nouveau livrable' }}</h1>
          <p>Décrivez-le comme à un collègue : l'IA en fait un modèle réutilisable et vous demande ce qui n'est pas clair.</p>
          <div class="scope">@if (scopeProject()) { Livrable propre au projet <strong>{{ projectName() || '…' }}</strong> — utilisé par tous ses périmètres }
            @else { Livrable du <strong>catalogue de l'entreprise</strong> — chaque projet pourra le choisir }</div>
        </div>
      </div>
      <ol class="steps">
        <li [class.on]="step() === 'describe' || step() === 'working'">1. Décrire</li>
        <li [class.on]="step() === 'questions'">2. Préciser</li>
        <li [class.on]="step() === 'review'">3. Vérifier & enregistrer</li>
      </ol>

      <div class="card stack">
        @switch (step()) {
          @case ('describe') {
            <label class="lbl">Que doit produire ce livrable, et à partir de quoi ?</label>
            <textarea rows="6" [value]="description()" (input)="description.set($any($event.target).value)"
              placeholder="Ex. « Un rapport hebdomadaire pour le client, à partir des comptes-rendus de réunion et de l'export des tickets de la semaine : faits marquants, indicateurs, risques et prochaines étapes. Format Word, deux pages maximum. »"></textarea>
            <label class="lbl">Exemples de votre style <span class="faint">(facultatif — des livrables déjà réalisés, l'IA en reprend la structure et le ton)</span></label>
            <div class="row gap wrap">
              @for (f of exampleFiles(); track f.id) { <span class="chipf">{{ f.name }} <button class="x" (click)="removeExample(f.id)" aria-label="Retirer">×</button></span> }
              @for (e of keptExamples(); track e.name) { <span class="chipf">{{ e.name }} <button class="x" (click)="dropKept(e.name)" aria-label="Retirer">×</button></span> }
              <label class="btn sm">{{ uploading() ? 'Envoi…' : '+ Ajouter un exemple' }}
                <input type="file" hidden multiple (change)="pickExamples($any($event.target).files); $any($event.target).value = ''" />
              </label>
            </div>
            <app-debug-ai context="Conception d'un nouveau livrable (analyse de la description et des exemples)" [error]="error()" />
            <div class="row end gap">
              @if (spec()) { <button (click)="step.set('review')">Revenir au modèle</button> }
              <button class="primary" (click)="design({})" [disabled]="description().trim().length < 10 || !!uploading()">Analyser avec l'IA</button>
            </div>
          }
          @case ('working') { <p class="small muted"><span class="spin"></span> Je conçois le modèle à partir de votre description{{ exampleCount() ? ' et de vos exemples' : '' }}…</p> }
          @case ('questions') {
            @let q = questions()[qi()];
            <p class="small muted">Question {{ qi() + 1 }} sur {{ questions().length }}</p>
            @if (q) { <app-choices [q]="q" [value]="answers()[q.question] ?? ''" (valueChange)="setAnswer(q.question, $event)" (enter)="next()" /> }
            <app-debug-ai context="Questions de clarification pendant la conception d'un livrable" [error]="error()" />
            <div class="row end gap">
              @if (qi() > 0) { <button (click)="qi.set(qi() - 1)">Précédent</button> }
              <button class="primary" (click)="next()" [disabled]="!answered()">{{ qi() < questions().length - 1 ? 'Suivant' : 'Continuer' }}</button>
            </div>
          }
          @case ('review') {
            @if (spec(); as s) {
              <div class="grid2">
                <div><label class="lbl">Nom du livrable</label><input [value]="s.title" (input)="patch({ title: $any($event.target).value })" /></div>
                <div><label class="lbl">Format produit</label>
                  <select [value]="s.output" (change)="patch({ output: $any($event.target).value })">
                    <option value="docx">Word (.docx)</option><option value="pptx">PowerPoint (.pptx)</option>
                    <option value="xlsx">Excel (.xlsx)</option><option value="md">Markdown (.md)</option>
                  </select></div>
              </div>

              <label class="lbl">Entrées attendues</label>
              @for (i of s.inputs; track $index; let n = $index) {
                <div class="line">
                  <input [value]="i.label" (input)="setInput(n, { label: $any($event.target).value })" placeholder="Libellé" />
                  <select [value]="i.kind" (change)="setInput(n, { kind: $any($event.target).value })">
                    <option value="files">Fichiers</option><option value="file">Un fichier</option><option value="text">Texte</option>
                  </select>
                  <label class="check"><input type="checkbox" [checked]="!!i.required" (change)="setInput(n, { required: $any($event.target).checked })" /> obligatoire</label>
                  <button class="sm ghost" (click)="removeInput(n)" aria-label="Retirer">×</button>
                </div>
              }
              <button class="sm ghost addbtn" (click)="addInput()">+ Ajouter une entrée</button>

              <label class="lbl">Sections du livrable <span class="faint">(dans l'ordre)</span></label>
              @for (sec of s.sections; track $index; let n = $index) {
                <div class="line sec">
                  <input [value]="sec.title" (input)="setSection(n, { title: $any($event.target).value })" placeholder="Titre de la section" />
                  <input class="grow" [value]="sec.guidance ?? ''" (input)="setSection(n, { guidance: $any($event.target).value })" placeholder="Ce que la section doit contenir" />
                  <button class="sm ghost" (click)="moveSection(n, -1)" [disabled]="n === 0" aria-label="Monter">↑</button>
                  <button class="sm ghost" (click)="removeSection(n)" aria-label="Retirer">×</button>
                </div>
              }
              <button class="sm ghost addbtn" (click)="addSection()">+ Ajouter une section</button>

              <label class="lbl">Règles de style (ton, longueur, public…)</label>
              <textarea rows="4" [value]="s.instructions ?? ''" (input)="patch({ instructions: $any($event.target).value })"></textarea>

              <p class="small faint">Exemples appris : {{ (s.examples ?? []).length ? (s.examples ?? []).map(e => e.name).join(', ') : 'aucun' }}</p>
              <app-debug-ai context="Enregistrement d'un livrable personnalisé" [error]="error()" />
              <div class="row end gap">
                <button (click)="step.set('describe')">← Revoir la description</button>
                <button class="primary" (click)="save()" [disabled]="busy() || !s.title.trim() || !s.sections.length">@if (busy()) { <span class="spin"></span> } Enregistrer</button>
              </div>
            }
          }
        }
      </div>
    </div>
  `,
  styles: `
    .narrow { max-width: 860px; }
    .back { color: var(--muted); text-decoration: none; }
    .scope { display: inline-block; margin-top: 6px; font-size: 12.5px; padding: 4px 10px; border-radius: 8px; background: var(--accent-soft); color: var(--text); }
    .steps { display: flex; gap: 8px; list-style: none; padding: 0; margin: 0 0 14px; flex-wrap: wrap; }
    .steps li { padding: 4px 12px; border-radius: 999px; border: 1px solid var(--border); font-size: 12px; color: var(--muted); }
    .steps li.on { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); font-weight: 600; }
    .stack { display: flex; flex-direction: column; gap: 10px; }
    .lbl { font-weight: 600; font-size: 13px; margin-top: 6px; }
    .row { display: flex; align-items: center; } .end { justify-content: flex-end; } .gap { gap: 8px; } .wrap { flex-wrap: wrap; }
    .grid2 { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; }
    @media (max-width: 700px) { .grid2 { grid-template-columns: 1fr; } }
    .line { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .line input { flex: 1; min-width: 160px; } .line .grow { flex: 2; }
    .line select { width: auto; }
    .check { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; white-space: nowrap; }
    .check input { width: auto; }
    .addbtn { align-self: flex-start; }
    .chipf { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: 999px; background: var(--surface-2); font-size: 12px; }
    .chipf .x { border: none; background: none; cursor: pointer; color: var(--muted); font-size: 14px; padding: 0 2px; }
    textarea { width: 100%; resize: vertical; }
  `,
})
export class LivrableDesignerPage {
  /** Route param (livrables/:id) — undefined for a new livrable. */
  id = input<string | undefined>(undefined);
  /** Query param ?project=<id>: design a livrable of that project (else: the company catalogue). */
  project = input<string | undefined>(undefined);
  private api = inject(Api);
  /** The livrable's project (from the query param, or from the loaded livrable when editing). */
  private loadedProject = signal<string | null>(null);
  protected scopeProject = computed(() => this.loadedProject() ?? this.project() ?? null);
  protected projectName = signal('');
  protected backLink = computed(() => this.scopeProject() ? ['/pr', this.scopeProject()!, 'livrables'] : ['/livrables/catalogue']);
  private svc = inject(Deliverables);
  private files = inject(AutomationApi);
  private router = inject(Router);

  protected step = signal<Step>('describe');
  protected description = signal('');
  protected exampleFiles = signal<{ id: string; name: string }[]>([]);
  protected keptExamples = signal<{ name: string; text: string }[]>([]);
  protected uploading = signal(false);
  protected questions = signal<DeliverableQuestion[]>([]);
  protected answers = signal<Record<string, string>>({});
  protected qi = signal(0);
  protected spec = signal<CustomSpec | null>(null);
  protected busy = signal(false);
  protected error = signal<string | null>(null);

  constructor() {
    effect(() => { const id = this.id(); if (id) void this.loadExisting(id); });
    effect(() => {
      const pid = this.scopeProject();
      if (pid) this.api.project(pid).subscribe({ next: p => this.projectName.set(p.name), error: () => this.projectName.set('') });
    });
  }

  protected exampleCount() { return this.exampleFiles().length + this.keptExamples().length; }

  private async loadExisting(id: string) {
    try {
      const t = await this.svc.customGet(id);
      this.description.set(t.description ?? '');
      this.keptExamples.set(t.examples_full ?? []);
      this.loadedProject.set((t as any).project_id ?? null);
      this.spec.set({ title: t.title, description: t.description, output: t.output as CustomSpec['output'], instructions: t.instructions ?? '',
                      inputs: t.inputs, sections: t.sections ?? [], examples: t.examples_full ?? [] });
      this.step.set('review');
    } catch (e) { this.error.set(await errorText(e, 'Livrable introuvable.')); }
  }

  // ---- 1. describe
  protected async pickExamples(list: FileList | null) {
    if (!list?.length) return;
    this.uploading.set(true); this.error.set(null);
    try {
      for (const f of Array.from(list).slice(0, 3)) {
        const meta: any = await this.files.uploadFile(f, f.name);
        this.exampleFiles.update(a => [...a, { id: meta.file_id ?? meta.id, name: meta.name ?? f.name }]);
      }
    } catch (e) { this.error.set(await errorText(e, "L'envoi de l'exemple a échoué.")); }
    finally { this.uploading.set(false); }
  }
  protected removeExample(id: string) { this.exampleFiles.update(a => a.filter(f => f.id !== id)); }
  protected dropKept(name: string) { this.keptExamples.update(a => a.filter(e => e.name !== name)); }

  protected async design(answers: Record<string, string>) {
    this.error.set(null); this.step.set('working');
    try {
      const res = await this.svc.customDesign({
        description: this.description().trim(), example_file_ids: this.exampleFiles().map(f => f.id),
        examples: this.keptExamples(), answers, current: this.spec(),
      });
      // uploaded examples are now extracted into the spec: keep them as text for the next rounds
      this.keptExamples.set(res.spec.examples ?? []);
      this.exampleFiles.set([]);
      this.spec.set(res.spec);
      if (res.status === 'questions') {
        this.questions.set(res.questions);
        this.answers.set(seedRecommended(res.questions, answers));   // recommended answer pre-selected
        this.qi.set(0);
        this.step.set('questions');
      } else {
        this.step.set('review');
      }
    } catch (e) {
      this.error.set(await errorText(e, "L'analyse a échoué."));
      this.step.set(this.questions().length ? 'questions' : 'describe');
    }
  }

  // ---- 2. questions
  protected setAnswer(q: string, v: string) { this.answers.update(m => ({ ...m, [q]: v })); }
  protected answered() { const q = this.questions()[this.qi()]; return !!q && !!(this.answers()[q.question] ?? '').trim(); }
  protected next() {
    if (!this.answered()) return;
    if (this.qi() < this.questions().length - 1) this.qi.update(i => i + 1);
    else void this.design(this.answers());
  }

  // ---- 3. review / edit
  protected patch(p: Partial<CustomSpec>) { this.spec.update(s => s ? { ...s, ...p } : s); }
  protected setInput(n: number, p: Partial<DeliverableInput>) {
    this.spec.update(s => s ? { ...s, inputs: s.inputs.map((i, k) => k === n ? { ...i, ...p, key: p.label ? this.slug(p.label) || i.key : i.key } : i) } : s);
  }
  protected addInput() { this.spec.update(s => s ? { ...s, inputs: [...s.inputs, { key: `entree_${s.inputs.length + 1}`, label: 'Nouvelle entrée', kind: 'files', required: false }] } : s); }
  protected removeInput(n: number) { this.spec.update(s => s ? { ...s, inputs: s.inputs.filter((_, k) => k !== n) } : s); }
  protected setSection(n: number, p: { title?: string; guidance?: string }) {
    this.spec.update(s => s ? { ...s, sections: s.sections.map((x, k) => k === n ? { ...x, ...p } : x) } : s);
  }
  protected addSection() { this.spec.update(s => s ? { ...s, sections: [...s.sections, { title: '', guidance: '' }] } : s); }
  protected removeSection(n: number) { this.spec.update(s => s ? { ...s, sections: s.sections.filter((_, k) => k !== n) } : s); }
  protected moveSection(n: number, d: number) {
    this.spec.update(s => {
      if (!s || n + d < 0 || n + d >= s.sections.length) return s;
      const a = [...s.sections]; [a[n], a[n + d]] = [a[n + d], a[n]];
      return { ...s, sections: a };
    });
  }
  private slug(v: string) { return v.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40); }

  protected async save() {
    const s = this.spec();
    if (!s) return;
    this.busy.set(true); this.error.set(null);
    try {
      const body = { ...s, description: this.description(), sections: s.sections.filter(x => x.title.trim()), project_id: this.scopeProject() };
      await this.svc.customSave(body, this.id());
      void this.router.navigate(this.backLink());
    } catch (e) { this.error.set(await errorText(e, "L'enregistrement a échoué.")); }
    finally { this.busy.set(false); }
  }
}

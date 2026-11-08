import { Component, computed, inject, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AutomationApi } from '../../automations/automation-api';
import { Choices, seedRecommended } from '../../choices';
import { DebugAi } from '../../debug-ai';
import { CustomDraft, DeliverableInput, DeliverableQuestion, DeliverableTemplate, Deliverables } from '../../deliverables.service';
import { errorText } from '../../http-error';

type Phase = 'inputs' | 'working' | 'questions' | 'ready' | 'done';

const FORMAT_LABEL: Record<string, string> = { docx: 'Word (.docx)', pptx: 'PowerPoint (.pptx)', xlsx: 'Excel (.xlsx)', md: 'Markdown (.md)' };

/** Runs a user-designed livrable on a périmètre — or once for the whole project (projectId): provide its inputs → the AI asks what's unclear (one
 *  question at a time, recommended answer pre-selected) → review the drafted sections → the file. */
@Component({
  selector: 'app-custom-run',
  imports: [Choices, DebugAi, RouterLink],
  template: `
    <div class="work">
      <div class="row between">
        <strong>{{ tpl().title }} <span class="tag">{{ fmt() }}</span></strong>
        <button class="link" (click)="closed.emit()">← Tous les livrables</button>
      </div>
      @if (tpl().description) { <p class="small muted">{{ tpl().description }}</p> }

      @switch (phase()) {
        @case ('inputs') {
          @for (inp of tpl().inputs; track inp.key) {
            <div class="inp">
              <div class="small"><strong>{{ inp.label }}</strong>@if (inp.required) { <span class="req"> *</span> }
                @if (inp.hint) { <span class="faint"> — {{ inp.hint }}</span> }</div>
              @if (inp.kind === 'text') {
                <textarea rows="3" [value]="texts()[inp.key] ?? ''" (input)="setText(inp.key, $any($event.target).value)"></textarea>
              } @else {
                <div class="row gap wrap">
                  @for (f of files()[inp.key] ?? []; track f.id) {
                    <span class="chipf">{{ f.name }} <button class="x" (click)="removeFile(inp.key, f.id)" aria-label="Retirer">×</button></span>
                  }
                  <label class="btn sm">{{ uploading() === inp.key ? 'Envoi…' : (inp.kind === 'files' ? '+ Ajouter des fichiers' : 'Choisir un fichier') }}
                    <input type="file" hidden [multiple]="inp.kind === 'files'" (change)="onPick(inp, $any($event.target).files); $any($event.target).value = ''" />
                  </label>
                </div>
              }
            </div>
          }
          <app-debug-ai [context]="'Préparation du livrable « ' + tpl().title + ' » (entrées fournies)'" [error]="error()" />
          <div class="row end gap">
            @if (tpl().can_edit) { <a class="btn sm ghost" [routerLink]="['/livrables', tpl().custom_id]" [queryParams]="tpl().project_id ? { project: tpl().project_id } : {}">Modifier le modèle</a> }
            <button class="primary" (click)="prepare()" [disabled]="!canPrepare()">Préparer le livrable</button>
          </div>
        }
        @case ('working') { <p class="small muted"><span class="spin"></span> {{ workingLabel() }}</p> }
        @case ('questions') {
          @let q = questions()[step()];
          <p class="small muted">Question {{ step() + 1 }} sur {{ questions().length }} — l'IA a besoin d'une précision</p>
          @if (q) { <app-choices [q]="q" [value]="answers()[q.question] ?? ''" (valueChange)="setAnswer(q.question, $event)" (enter)="next()" /> }
          <app-debug-ai [context]="'Questions de clarification du livrable « ' + tpl().title + ' »'" [error]="error()" />
          <div class="row end gap">
            @if (step() > 0) { <button (click)="step.set(step() - 1)">Précédent</button> }
            <button class="primary" (click)="next()" [disabled]="!answered()">{{ step() < questions().length - 1 ? 'Suivant' : 'Continuer' }}</button>
          </div>
        }
        @case ('ready') {
          @if (draft(); as d) {
            <p class="small faint">Brouillon rédigé {{ usedLlm() ? 'par l’IA dans le style de vos exemples' : 'sans IA (mode hors ligne) — à relire' }}. Relisez avant de générer.</p>
            <div class="preview">
              <h3>{{ d.title }}</h3>
              @for (b of d.sections; track $index) {
                <details [open]="$index < 3"><summary><strong>{{ b.heading }}</strong></summary>
                  @for (p of b.paragraphs ?? []; track $index) { <p class="small">{{ p }}</p> }
                  @if (b.bullets?.length) { <ul class="small">@for (x of b.bullets; track $index) { <li>{{ x }}</li> }</ul> }
                  @if (b.table?.headers?.length) { <p class="small faint">Tableau : {{ b.table!.headers.join(' · ') }} ({{ b.table!.rows.length }} lignes)</p> }
                </details>
              }
              @if (d.gaps?.length) {
                <div class="gaps"><strong class="small">Informations manquantes</strong>
                  <ul class="small">@for (g of d.gaps; track $index) { <li>{{ g }}</li> }</ul></div>
              }
            </div>
          }
          <app-debug-ai [context]="'Génération du fichier du livrable « ' + tpl().title + ' » (' + fmt() + ')'" [error]="error()" />
          <div class="row end gap">
            <button (click)="phase.set('inputs')">Modifier les entrées</button>
            <button class="primary" (click)="generate()" [disabled]="busy()">@if (busy()) { <span class="spin"></span> } Générer le fichier</button>
          </div>
        }
        @case ('done') {
          <div class="doneview">
            <svg class="ic big ok" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.4 2.4 4.6-4.9"/></svg>
            <p><strong>{{ filename() }}</strong> est prêt — enregistré sur le projet et indexé pour les agents.</p>
            <div class="row gap"><button class="primary" (click)="download()">Télécharger à nouveau</button><button (click)="restart()">Générer un autre</button></div>
          </div>
        }
      }
    </div>
  `,
  styles: `
    .work { display: flex; flex-direction: column; gap: 12px; max-width: 720px; }
    .row { display: flex; align-items: center; } .row.between { justify-content: space-between; } .row.end { justify-content: flex-end; } .row.gap { gap: 8px; } .wrap { flex-wrap: wrap; }
    .link { background: none; border: none; color: var(--muted); cursor: pointer; }
    .tag { font-size: 11px; font-weight: 500; color: var(--muted); margin-left: 6px; }
    .inp { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; }
    .req { color: var(--red); }
    .chipf { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: 999px; background: var(--surface-2); font-size: 12px; }
    .chipf .x { border: none; background: none; cursor: pointer; color: var(--muted); font-size: 14px; padding: 0 2px; }
    textarea { width: 100%; resize: vertical; }
    .preview { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
    .preview h3 { margin: 0 0 4px; font-size: 15px; }
    .preview ul { margin: 4px 0 6px; padding-left: 18px; }
    .gaps { margin-top: 6px; padding: 8px 10px; border-radius: 8px; background: var(--amber-soft); }
    .doneview { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 20px; text-align: center; }
    .ic { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .ic.big { width: 40px; height: 40px; } .ic.ok { color: var(--green); }
  `,
})
export class CustomRun {
  tpl = input.required<DeliverableTemplate>();
  perimeterId = input<string | undefined>(undefined);
  /** Set → a project-level run: one document for the whole project, fed by all its périmètres. */
  projectId = input<string | undefined>(undefined);
  closed = output<void>();
  generated = output<void>();
  private svc = inject(Deliverables);
  private filesApi = inject(AutomationApi);

  protected phase = signal<Phase>('inputs');
  protected files = signal<Record<string, { id: string; name: string }[]>>({});
  protected texts = signal<Record<string, string>>({});
  protected uploading = signal<string | null>(null);
  protected questions = signal<DeliverableQuestion[]>([]);
  protected answers = signal<Record<string, string>>({});
  protected step = signal(0);
  protected draft = signal<CustomDraft | null>(null);
  protected usedLlm = signal(false);
  protected busy = signal(false);
  protected error = signal<string | null>(null);
  protected filename = signal('');
  protected workingLabel = signal('');
  private lastBlob: Blob | null = null;

  private target() { return this.projectId() ? { project_id: this.projectId() } : { perimeter_id: this.perimeterId() }; }
  protected fmt = computed(() => FORMAT_LABEL[this.tpl().output] ?? this.tpl().output);

  protected setText(key: string, v: string) { this.texts.update(m => ({ ...m, [key]: v })); }
  protected removeFile(key: string, id: string) { this.files.update(m => ({ ...m, [key]: (m[key] ?? []).filter(f => f.id !== id) })); }
  protected async onPick(inp: DeliverableInput, list: FileList | null) {
    if (!list?.length) return;
    this.uploading.set(inp.key); this.error.set(null);
    try {
      const added: { id: string; name: string }[] = [];
      for (const f of Array.from(list)) {
        const meta: any = await this.filesApi.uploadFile(f, f.name);
        added.push({ id: meta.file_id ?? meta.id, name: meta.name ?? f.name });
      }
      this.files.update(m => ({ ...m, [inp.key]: inp.kind === 'file' ? added.slice(-1) : [...(m[inp.key] ?? []), ...added] }));
    } catch (e) { this.error.set(await errorText(e, "L'envoi du fichier a échoué.")); }
    finally { this.uploading.set(null); }
  }
  protected canPrepare(): boolean {
    return this.tpl().inputs.filter(i => i.required).every(i =>
      i.kind === 'text' ? !!(this.texts()[i.key] ?? '').trim() : (this.files()[i.key] ?? []).length > 0);
  }
  private payload(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const i of this.tpl().inputs) out[i.key] = i.kind === 'text' ? (this.texts()[i.key] ?? '') : (this.files()[i.key] ?? []).map(f => f.id);
    return out;
  }

  protected async prepare(answers: Record<string, string> = {}) {
    this.error.set(null); this.busy.set(true);
    this.workingLabel.set(Object.keys(answers).length ? 'Je rédige le livrable avec vos précisions…' : 'Je lis vos documents et je rédige le livrable…');
    this.phase.set('working');
    try {
      const res = await this.svc.customPrepare(this.tpl().custom_id!, { inputs: this.payload(), answers, ...this.target() });
      this.usedLlm.set(res.used_llm);
      if (res.status === 'questions') {
        this.questions.set(res.questions);
        this.answers.set(seedRecommended(res.questions, answers));   // recommended answer pre-selected
        this.step.set(0);
        this.phase.set('questions');
      } else {
        this.draft.set(res.draft);
        this.phase.set('ready');
      }
    } catch (e) {
      this.error.set(await errorText(e, 'La préparation a échoué.'));
      this.phase.set(this.questions().length ? 'questions' : 'inputs');
    } finally { this.busy.set(false); }
  }
  protected setAnswer(q: string, v: string) { this.answers.update(m => ({ ...m, [q]: v })); }
  protected answered(): boolean { const q = this.questions()[this.step()]; return !!q && !!(this.answers()[q.question] ?? '').trim(); }
  protected next() {
    if (!this.answered()) return;
    if (this.step() < this.questions().length - 1) this.step.update(i => i + 1);
    else void this.prepare(this.answers());
  }

  protected async generate() {
    const d = this.draft();
    if (!d || this.busy()) return;
    this.busy.set(true); this.error.set(null);
    try {
      const { blob, filename } = await this.svc.customGenerate(this.tpl().custom_id!, { draft: d, ...this.target() });
      this.lastBlob = blob; this.filename.set(filename); this.save(blob, filename);
      this.phase.set('done');
      this.generated.emit();
    } catch (e) { this.error.set(await errorText(e, 'La génération du fichier a échoué.')); }
    finally { this.busy.set(false); }
  }
  protected download() { if (this.lastBlob) this.save(this.lastBlob, this.filename()); }
  protected restart() { this.phase.set('inputs'); this.draft.set(null); this.questions.set([]); this.answers.set({}); this.error.set(null); }
  private save(blob: Blob, name: string) {
    const url = URL.createObjectURL(blob);
    Object.assign(document.createElement('a'), { href: url, download: name }).click();
    URL.revokeObjectURL(url);
  }
}

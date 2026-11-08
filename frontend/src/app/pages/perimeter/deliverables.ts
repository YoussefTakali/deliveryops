import { DecimalPipe, SlicePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';

import { RouterLink } from '@angular/router';

import { Api } from '../../api';
import { AutomationApi } from '../../automations/automation-api';
import { seedRecommended } from '../../choices';
import { DebugAi } from '../../debug-ai';
import { Deliverables, DeliverableInput, DeliverableQuestion, DeliverableTemplate, PrepareResult, SampleSet } from '../../deliverables.service';
import { errorText } from '../../http-error';
import { CustomRun } from './custom-run';

type ExtractPhase = 'inputs' | 'analyzing' | 'questions' | 'ready';

/** Livrables tab of a périmètre (project). Two kinds of templates:
 *   - mode 'questions' (COPIL): answer the questions one at a time, then download.
 *   - mode 'extract' (matrice de couverture, plan de charge): provide the declared inputs (files /
 *     Jira data / team members, or a shipped sample set), the app extracts the content and asks ONLY
 *     what's missing, you review the draft, then download the real Office file.
 *  Generated deliverables are stored on the périmètre (history below) and indexed for the agents. */
@Component({
  selector: 'app-deliverables-tab',
  template: `
    <div class="stack">
      @if (!active()) {
        <p class="small muted">@if (perimeter()?.project_id) { Les livrables du projet <strong>{{ perimeter()?.project }}</strong> — choisis dans le catalogue de l'entreprise ou propres au projet, pré-remplis pour ce périmètre. } @else { Les livrables du catalogue de l'entreprise, pré-remplis pour ce périmètre. }</p>
        @if (templates(); as list) {
          <div class="grid">
            @for (t of list; track t.key) {
              <button class="tpl" (click)="start(t)">
                <span class="tico"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true">
                  @if (t.output === 'xlsx') { <rect x="4" y="3.5" width="16" height="17" rx="2"/><path d="M4 9h16M9 9v11.5M15 9v11.5"/> }
                  @else { <rect x="4" y="4" width="16" height="12" rx="2"/><path d="M12 16v4M8 20h8"/> }
                </svg></span>
                <span class="tbody"><strong>{{ t.title }}</strong><span class="small muted">{{ t.description }}</span>
                  <span class="tag">{{ formatLabel(t.output) }} · {{ t.custom ? (t.scope === 'project' ? 'propre au projet' : 'catalogue') : (t.mode === 'extract' ? 'à partir de vos données' : 'par questions') }}</span></span>
              </button>
            } @empty { <p class="muted">Aucun livrable disponible.</p> }
            @if (perimeter()?.project_id) {
              <a class="tpl new" [routerLink]="['/pr', perimeter()!.project_id, 'livrables']">
                <span class="tico"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></span>
                <span class="tbody"><strong>Gérer les livrables du projet</strong><span class="small muted">Choisir dans le catalogue ou créer un livrable propre au projet (entrées → sortie, exemples : l'IA apprend votre style).</span></span>
              </a>
            }
          </div>
        } @else if (loadError()) {
          <div class="card"><p class="muted">{{ loadError() }}</p><button class="primary" (click)="load()">Réessayer</button></div>
        } @else { <div class="card"><span class="spin"></span> Chargement…</div> }

        @if (history().length) {
          <div class="card">
            <h3>Livrables générés pour ce projet</h3>
            <table class="tbl">
              <thead><tr><th>Livrable</th><th>Fichier</th><th>Par</th><th>Le</th><th></th></tr></thead>
              <tbody>
                @for (d of history(); track d.id) {
                  <tr><td>{{ d.title }}</td><td class="small">{{ d.filename }}</td><td class="small">{{ d.created_by }}</td><td class="small">{{ d.created_at | slice:0:10 }}</td>
                    <td><a class="btn sm" [href]="'/api/perimeters/' + perimeterId() + '/deliverables/' + d.id + '/file'" target="_blank">Télécharger</a></td></tr>
                }
              </tbody>
            </table>
          </div>
        }
      } @else if (active()!.custom) {
        <div class="card"><app-custom-run [tpl]="active()!" [perimeterId]="perimeterId()" (closed)="cancel()" (generated)="loadHistory(perimeterId())" /></div>
      } @else {
        <div class="card work">
          <div class="row between"><strong>{{ active()!.title }}</strong><button class="link" (click)="cancel()">← Tous les livrables</button></div>
          <p class="small faint">Projet : {{ projectName() }}{{ clientName() ? ' · ' + clientName() : '' }}</p>

          @if (done(); as d) {
            <div class="doneview">
              <svg class="ic big ok" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.4 2.4 4.6-4.9"/></svg>
              <p><strong>{{ d.filename }}</strong> est prêt — enregistré sur le projet et indexé pour les agents.</p>
              <div class="row gap"><button class="primary" (click)="download()">Télécharger à nouveau</button><button (click)="restart()">Générer un autre</button></div>
            </div>
          } @else if (active()!.mode === 'extract' && phase() === 'inputs') {
            @for (inp of active()!.inputs; track inp.key) {
              <div class="inp">
                <div class="small"><strong>{{ inp.label }}</strong>@if (inp.required) { <span class="req"> *</span> }
                  @if (inp.hint) { <span class="faint"> — {{ inp.hint }}</span> }</div>
                @if (inp.kind === 'file' || inp.kind === 'files') {
                  <div class="row gap wrap">
                    @for (f of files()[inp.key] ?? []; track f.id) {
                      <span class="chipf">{{ f.name }} <button class="x" (click)="removeFile(inp.key, f.id)" aria-label="Retirer">×</button></span>
                    }
                    <label class="btn sm" [class.disabled]="!!selectedSample()">
                      {{ uploading() === inp.key ? 'Envoi…' : (inp.kind === 'files' ? '+ Ajouter des fichiers' : ((files()[inp.key] ?? []).length ? 'Remplacer' : 'Choisir un fichier')) }}
                      <input type="file" hidden [multiple]="inp.kind === 'files'" [accept]="inp.accept || ''" [disabled]="!!selectedSample()"
                             (change)="onPick(inp, $any($event.target).files); $any($event.target).value = ''" />
                    </label>
                  </div>
                } @else if (inp.kind === 'connector') {
                  <label class="check"><input type="checkbox" [checked]="useDemo()[inp.key] ?? false" (change)="setDemo(inp.key, $any($event.target).checked)" [disabled]="!!selectedSample()" />
                    Utiliser les données de démonstration ({{ inp.connector || 'connecteur' }})</label>
                } @else if (inp.kind === 'members') {
                  @if (staff().length) {
                    <div class="row gap wrap">
                      @for (m of staff(); track m.id) {
                        <label class="chip" [class.on]="pickedMembers()[m.id] ?? false">
                          <input type="checkbox" hidden [checked]="pickedMembers()[m.id] ?? false" (change)="toggleMember(m.id, $any($event.target).checked)" />
                          {{ m.name }} <span class="faint">· {{ m.profile }}</span>
                        </label>
                      }
                    </div>
                    <p class="small faint">Aucune sélection = toute l'équipe.</p>
                  } @else { <p class="small warn">Personne n'est affecté à ce projet — voir l'onglet Équipe (sinon on vous demandera qui travaille dessus).</p> }
                } @else {
                  <input [value]="textInputs()[inp.key] ?? ''" (input)="setText(inp.key, $any($event.target).value)" />
                }
              </div>
            }
            @if (samples().length && active()!.key === 'matrice_couverture') {
              <div class="samples">
                <span class="small muted">Ou testez avec un jeu d'exemple :</span>
                @for (s of samples(); track s.set) {
                  <button type="button" class="chip" [class.on]="selectedSample() === s.set" (click)="pickSample(s.set)">{{ s.label }}</button>
                }
                @if (selectedSample()) { <button type="button" class="link" (click)="pickSample(null)">annuler l'exemple</button> }
              </div>
            }
            <app-debug-ai [context]="debugContext()" [error]="error()" />
            <div class="row end gap">
              <button class="primary" (click)="analyze()" [disabled]="!canAnalyze()">{{ active()!.key === 'plan_de_charge' ? 'Préparer le plan' : 'Analyser les documents' }}</button>
            </div>
          } @else if (active()!.mode === 'extract' && phase() === 'analyzing') {
            <p class="small muted"><span class="spin"></span> {{ active()!.key === 'plan_de_charge' ? 'Je calcule les charges et le planning…' : 'J\\'analyse vos documents (exigences, cas d\\'usage, couverture)…' }}</p>
          } @else if (active()!.mode === 'extract' && phase() === 'ready') {
            @if (prep(); as p) {
              @if (active()!.key === 'plan_de_charge') {
                <div class="summary">
                  <div class="stat"><strong>{{ p.draft.totals?.days }}</strong><span class="small muted">jours</span></div>
                  <div class="stat"><strong>{{ p.draft.totals?.cost | number:'1.0-0' }} €</strong><span class="small muted">coût estimé</span></div>
                  <div class="stat"><strong>{{ p.draft.weeks?.length }}</strong><span class="small muted">semaines</span></div>
                  <div class="stat"><strong>{{ p.draft.members?.length }}</strong><span class="small muted">ressources</span></div>
                </div>
                @for (n of p.draft.notes ?? []; track n) { <p class="small warn">{{ n }}</p> }
                <details open><summary class="small">Charge par ressource</summary>
                  <ul class="small preview">@for (m of p.draft.members ?? []; track m.name) { <li><strong>{{ m.name }}</strong> ({{ m.profile }}, TJM {{ m.rate }} €) — {{ m.total_days }} j · {{ m.cost | number:'1.0-0' }} €</li> }</ul></details>
                <details><summary class="small">Charge par exigence</summary>
                  <ul class="small preview">@for (e of (p.draft.exigences ?? []).slice(0, 15); track e.ref + e.name) { <li><span class="mono">{{ e.ref }}</span> {{ e.name }} <span class="faint">— crit. {{ e.crit }} · {{ e.days }} j · {{ e.assignee }} · {{ e.week_start }}→{{ e.week_end }}</span></li> }</ul></details>
              } @else {
                <div class="summary">
                  <div class="stat"><strong>{{ p.draft.exigences?.length }}</strong><span class="small muted">exigences</span></div>
                  <div class="stat"><strong>{{ p.draft.cas_usage?.length }}</strong><span class="small muted">cas d'usage</span></div>
                  <div class="stat"><strong>{{ coverageCount(p) }}</strong><span class="small muted">liens de couverture</span></div>
                </div>
                <p class="small faint">Sources : {{ p.sources.join(', ') }}{{ p.used_llm ? ' · affiné par l’IA' : ' · extraction déterministe' }}</p>
                <details><summary class="small">Aperçu des exigences</summary>
                  <ul class="small preview">@for (e of (p.draft.exigences ?? []).slice(0, 12); track e.ref + e.name) { <li><span class="mono">{{ e.ref }}</span> {{ e.name }} <span class="faint">— {{ e.impact || '?' }} / {{ e.probabilite || '?' }}</span></li> }</ul></details>
                <details><summary class="small">Aperçu des cas d'usage</summary>
                  <ul class="small preview">@for (c of (p.draft.cas_usage ?? []).slice(0, 12); track c.ref + c.name) { <li><span class="mono">{{ c.ref }}</span> {{ c.name }} <span class="faint">— couvre {{ c.exigences.length }} exigence(s)</span></li> }</ul></details>
              }
              <app-debug-ai [context]="debugContext()" [error]="error()" />
              <div class="row end gap">
                <button (click)="backToInputs()">Modifier les entrées</button>
                <button class="primary" (click)="generate()" [disabled]="busy()">@if (busy()) { <span class="spin"></span> } Générer le fichier</button>
              </div>
            }
          } @else {
            @let q = steps()[step()];
            <p class="small muted">Question {{ step() + 1 }} sur {{ steps().length }}</p>
            @if (q) {
              <div class="qblock">
                <div class="qtitle">{{ q.question }}</div>
                @if (q.options.length) {
                  <div class="qopts">
                    @for (o of q.options; track o) {
                      <button type="button" class="opt" [class.on]="optionOn(q, o)" (click)="choose(q, o)"><span class="radio"></span><span class="olabel">{{ o }}</span>
                        @if (o === q.recommended) { <span class="rec" title="L'option recommandée par l'IA">Recommandé</span> }</button>
                    }
                  </div>
                }
                <textarea rows="3" [placeholder]="q.options.length ? 'Autre — écrivez votre réponse' : 'Votre réponse (une par ligne si liste)'"
                          [value]="otherText(q)" (input)="onOther(q, $any($event.target).value)"></textarea>
              </div>
            }
            <app-debug-ai [context]="debugContext()" [error]="error()" />
            <div class="row end gap">
              @if (step() > 0) { <button (click)="prev()">Précédent</button> }
              @if (step() < steps().length - 1) {
                <button class="primary" (click)="next()" [disabled]="!curAnswered()">Suivant</button>
              } @else {
                <button class="primary" (click)="finishQuestions()" [disabled]="!curAnswered() || busy()">@if (busy()) { <span class="spin"></span> } {{ active()!.mode === 'extract' ? 'Continuer' : 'Générer le fichier' }}</button>
              }
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .stack { display: flex; flex-direction: column; gap: 14px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
    .tpl { display: flex; gap: 14px; align-items: flex-start; text-align: left; padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); cursor: pointer; }
    .tpl:hover { border-color: var(--accent); }
    .tico { width: 40px; height: 40px; border-radius: 10px; background: var(--accent-soft); color: var(--accent); display: flex; align-items: center; justify-content: center; flex: none; }
    .tbody { display: flex; flex-direction: column; gap: 4px; min-width: 0; } .tag { font-size: 11px; color: var(--muted); margin-top: 4px; }
    .work { display: flex; flex-direction: column; gap: 12px; max-width: 680px; }
    .row { display: flex; align-items: center; } .row.between { justify-content: space-between; } .row.end { justify-content: flex-end; } .row.gap { gap: 8px; } .wrap { flex-wrap: wrap; }
    .link { background: none; border: none; color: var(--muted); cursor: pointer; }
    .inp { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; }
    .req { color: var(--red, #c0392b); }
    .chipf { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: 999px; background: var(--surface-2); font-size: 12px; }
    .chipf .x { border: none; background: none; cursor: pointer; color: var(--muted); font-size: 14px; padding: 0 2px; }
    .btn.disabled { opacity: .5; pointer-events: none; }
    .check { display: flex; align-items: center; gap: 8px; font-size: 13px; }
    .samples { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .chip { padding: 6px 12px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface); cursor: pointer; font-size: 13px; }
    .chip.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
    .summary { display: flex; gap: 12px; flex-wrap: wrap; } .stat { flex: 1; min-width: 110px; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 12px; border-radius: 10px; background: var(--surface-2); } .stat strong { font-size: 20px; }
    .preview { margin: 6px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 3px; } .mono { font-family: ui-monospace, monospace; font-size: 11px; color: var(--muted); }
    .qblock { display: flex; flex-direction: column; gap: 8px; } .qtitle { font-weight: 600; font-size: 14px; }
    .qopts { display: flex; flex-direction: column; gap: 6px; }
    .opt { display: flex; align-items: center; gap: 10px; text-align: left; width: 100%; padding: 9px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); cursor: pointer; font-size: 13px; color: var(--text); }
    .opt:hover { border-color: var(--accent); } .opt.on { border-color: var(--accent); background: var(--accent-soft); }
    .olabel { flex: 1; } .tpl.new { border-style: dashed; color: inherit; text-decoration: none; }
    .rec { font-size: 10.5px; font-weight: 700; color: var(--green); background: var(--green-soft); border: 1px solid color-mix(in srgb, var(--green) 35%, transparent); border-radius: 999px; padding: 1px 8px; flex: none; }
    .opt .radio { width: 15px; height: 15px; border-radius: 50%; border: 2px solid var(--border); flex: none; position: relative; }
    .opt.on .radio { border-color: var(--accent); } .opt.on .radio::after { content: ''; position: absolute; inset: 2px; border-radius: 50%; background: var(--accent); }
    textarea { width: 100%; resize: vertical; }
    .doneview { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 20px; text-align: center; }
    .tbl { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; } .tbl th, .tbl td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
    .card h3 { margin: 0; font-size: 14px; }
    .ic { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .ic.big { width: 40px; height: 40px; } .ic.ok { color: var(--green, #2e9d5b); }
  `,
  imports: [DecimalPipe, SlicePipe, RouterLink, CustomRun, DebugAi],
})
export class DeliverablesTab {
  private svc = inject(Deliverables);
  private api = inject(Api);
  private files_api = inject(AutomationApi);
  perimeterId = input.required<string>();

  protected templates = signal<DeliverableTemplate[] | null>(null);
  protected loadError = signal<string | null>(null);
  protected perimeter = signal<any>(null);
  protected history = signal<any[]>([]);
  protected staff = signal<any[]>([]);
  protected active = signal<DeliverableTemplate | null>(null);
  protected busy = signal(false);
  protected error = signal<string | null>(null);
  protected done = signal<{ filename: string } | null>(null);
  private lastBlob: Blob | null = null;

  // questions (both modes)
  protected steps = signal<DeliverableQuestion[]>([]);
  protected step = signal(0);
  protected answers = signal<Record<string, string>>({});
  protected other = signal<Record<string, string>>({});

  // extract mode
  protected phase = signal<ExtractPhase>('inputs');
  protected files = signal<Record<string, { id: string; name: string }[]>>({});
  protected useDemo = signal<Record<string, boolean>>({});
  protected textInputs = signal<Record<string, string>>({});
  protected pickedMembers = signal<Record<string, boolean>>({});
  protected samples = signal<SampleSet[]>([]);
  protected selectedSample = signal<string | null>(null);
  protected uploading = signal<string | null>(null);
  protected prep = signal<PrepareResult | null>(null);

  protected projectName = computed(() => this.perimeter()?.name ?? '');
  protected debugContext = computed(() => `Livrable « ${this.active()?.title ?? ''} » (${this.active()?.mode === 'extract' ? 'extraction depuis des documents' : 'questions'}) sur le périmètre « ${this.projectName()} », étape : ${this.phase()}`);
  protected formatLabel(o: string) { return ({ xlsx: 'Excel (.xlsx)', pptx: 'PowerPoint (.pptx)', docx: 'Word (.docx)', md: 'Markdown (.md)' } as Record<string, string>)[o] ?? o; }
  protected clientName = computed(() => this.perimeter()?.client ?? '');

  constructor() {
    effect(() => {
      const id = this.perimeterId();
      untracked(() => void this.load());
      this.api.perimeter(id).subscribe({ next: p => this.perimeter.set(p), error: () => {} });
      this.loadHistory(id);
      this.api.staff(id).subscribe({ next: r => this.staff.set(r.members), error: () => this.staff.set([]) });
    });
  }

  protected async load() {
    this.loadError.set(null);
    // The plan de charge is a PROJECT-level document (it spans all the project's périmètres), so it's
    // generated from the project page — not here at the périmètre level.
    try { this.templates.set((await this.svc.templates(this.perimeterId())).templates.filter(t => t.key !== 'plan_de_charge')); }
    catch { this.loadError.set('Livrables indisponibles pour le moment.'); }
  }
  protected loadHistory(id: string) { this.api.perimeterDeliverables(id).subscribe({ next: r => this.history.set(r.deliverables), error: () => this.history.set([]) }); }

  protected start(t: DeliverableTemplate) {
    this.active.set(t); this.step.set(0); this.other.set({}); this.done.set(null); this.error.set(null); this.lastBlob = null;
    this.files.set({}); this.useDemo.set({}); this.textInputs.set({}); this.pickedMembers.set({}); this.selectedSample.set(null); this.prep.set(null); this.samples.set([]);
    const pre: Record<string, string> = {};
    const skip = new Set<string>();
    if (t.prefill?.projet) { pre[t.prefill.projet] = this.projectName(); skip.add(t.prefill.projet); }
    if (t.prefill?.client) { pre[t.prefill.client] = this.clientName(); skip.add(t.prefill.client); }
    this.answers.set(pre);
    if (t.mode === 'extract') {
      this.phase.set('inputs');
      this.steps.set([]);
      void this.svc.samples(t.key).then(r => this.samples.set(r.sets)).catch(() => this.samples.set([]));
    } else {
      this.steps.set(t.questions.filter(q => !skip.has(q.question)));
    }
  }
  protected cancel() { this.active.set(null); this.loadHistory(this.perimeterId()); }
  protected restart() { const t = this.active(); if (t) this.start(t); }

  // ---- inputs (extract mode) ----
  protected async onPick(inp: DeliverableInput, list: FileList | null) {
    if (!list?.length) return;
    this.uploading.set(inp.key); this.error.set(null);
    try {
      const added: { id: string; name: string }[] = [];
      for (const f of Array.from(list)) {
        const meta: any = await this.files_api.uploadFile(f, f.name);
        added.push({ id: meta.file_id ?? meta.id, name: meta.name ?? f.name });
      }
      this.files.update(m => ({ ...m, [inp.key]: inp.kind === 'file' ? added.slice(-1) : [...(m[inp.key] ?? []), ...added] }));
    } catch { this.error.set("L'envoi du fichier a échoué."); }
    finally { this.uploading.set(null); }
  }
  protected removeFile(key: string, id: string) { this.files.update(m => ({ ...m, [key]: (m[key] ?? []).filter(f => f.id !== id) })); }
  protected setDemo(key: string, on: boolean) { this.useDemo.update(m => ({ ...m, [key]: on })); }
  protected setText(key: string, v: string) { this.textInputs.update(m => ({ ...m, [key]: v })); }
  protected toggleMember(id: string, on: boolean) { this.pickedMembers.update(m => ({ ...m, [id]: on })); }
  protected pickSample(set: string | null) { this.selectedSample.set(set); this.error.set(null); }
  protected canAnalyze(): boolean {
    const t = this.active();
    if (!t) return false;
    if (this.selectedSample()) return true;
    return t.inputs.filter(i => i.required).every(i => (i.kind === 'file' || i.kind === 'files') ? (this.files()[i.key] ?? []).length > 0 : true);
  }
  private inputsPayload(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const inp of this.active()?.inputs ?? []) {
      if (inp.kind === 'file') out[inp.key] = (this.files()[inp.key] ?? [])[0]?.id ?? null;
      else if (inp.kind === 'files') out[inp.key] = (this.files()[inp.key] ?? []).map(f => f.id);
      else if (inp.kind === 'connector') out[inp.key] = { demo: !!this.useDemo()[inp.key] };
      else if (inp.kind === 'members') out[inp.key] = Object.entries(this.pickedMembers()).filter(([, on]) => on).map(([id]) => id);
      else out[inp.key] = this.textInputs()[inp.key] ?? '';
    }
    return out;
  }
  protected async analyze(extraAnswers?: Record<string, string>) {
    const t = this.active();
    if (!t) return;
    this.phase.set('analyzing'); this.error.set(null); this.busy.set(true);
    try {
      const res = await this.svc.prepare(t.key, {
        inputs: this.selectedSample() ? undefined : this.inputsPayload(), samples: this.selectedSample(),
        answers: extraAnswers ?? {}, perimeter_id: this.perimeterId(),
      });
      this.prep.set(res);
      if (res.status === 'questions') {
        this.steps.set(res.questions); this.step.set(0); this.other.set({}); this.phase.set('questions');
        this.answers.update(a => seedRecommended(res.questions, a));   // the AI's recommended answer is pre-selected
      }
      else this.phase.set('ready');
    } catch (e: any) {
      this.phase.set('inputs');
      this.error.set(await errorText(e, "L'analyse a échoué. Vérifiez vos entrées puis réessayez."));
    } finally { this.busy.set(false); }
  }
  protected backToInputs() { this.phase.set('inputs'); this.error.set(null); }
  protected coverageCount(p: PrepareResult): number { return (p.draft.cas_usage ?? []).reduce((n: number, c: any) => n + (c.exigences?.length ?? 0), 0); }

  // ---- questions (shared) ----
  protected setAnswer(q: string, v: string) { this.answers.update(m => ({ ...m, [q]: v })); }
  protected otherText(q: DeliverableQuestion): string {
    const t = this.other()[q.question];
    if (t !== undefined) return t;
    const a = this.answers()[q.question] ?? '';
    return a && !q.options.includes(a) ? a : '';
  }
  protected onOther(q: DeliverableQuestion, v: string) { this.other.update(m => ({ ...m, [q.question]: v })); this.setAnswer(q.question, v.trim() ? v : ''); }
  protected choose(q: DeliverableQuestion, o: string) { this.other.update(m => ({ ...m, [q.question]: '' })); this.setAnswer(q.question, o); }
  protected optionOn(q: DeliverableQuestion, o: string): boolean { return !(this.other()[q.question] ?? '').trim() && this.answers()[q.question] === o; }
  protected curAnswered(): boolean { const q = this.steps()[this.step()]; return !!q && (this.answers()[q.question] ?? '').trim().length > 0; }
  protected next() { if (this.curAnswered() && this.step() < this.steps().length - 1) this.step.update(i => i + 1); }
  protected prev() { this.step.update(i => Math.max(0, i - 1)); }
  protected async finishQuestions() {
    if (!this.curAnswered()) return;
    if (this.active()?.mode === 'extract') {
      const clar: Record<string, string> = {};
      for (const q of this.steps()) clar[q.question] = this.answers()[q.question] ?? '';
      await this.analyze(clar);
    } else {
      await this.generate();
    }
  }

  // ---- generate / download ----
  protected async generate() {
    const t = this.active();
    if (!t || this.busy()) return;
    this.busy.set(true); this.error.set(null);
    try {
      const body = t.mode === 'extract'
        ? { draft: this.prep()?.draft, projet: this.projectName(), perimeter_id: this.perimeterId() }
        : { answers: this.answers(), perimeter_id: this.perimeterId() };
      const { blob, filename } = await this.svc.generate(t.key, body);
      this.lastBlob = blob; this.done.set({ filename }); this.saveBlob(blob, filename);
      this.loadHistory(this.perimeterId());
    } catch (e) { this.error.set(await errorText(e, 'La génération a échoué. Vérifiez vos entrées puis réessayez.')); }
    finally { this.busy.set(false); }
  }
  protected download() { if (this.lastBlob && this.done()) this.saveBlob(this.lastBlob, this.done()!.filename); }
  private saveBlob(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: filename });
    a.click();
    URL.revokeObjectURL(url);
  }
}

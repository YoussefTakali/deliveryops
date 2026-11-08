import { Component, DestroyRef, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { AutomationApi, errorText } from '../../automations/automation-api';
import { parseTable, rowsToCsv } from '../../automations/dataset';
import { triggerSummary } from '../../automations/describe';
import { CompileNotes, dropNotes, loadNotes, notesFromCompile, saveNotes, withTimezone } from '../../automations/drafts';
import { Automation, AutomationInput, CatalogEntry, Step, Trigger, ValidationIssue } from '../../automations/models';
import { AutomationRunner } from '../../automations/runner';
import { Desktop } from '../../desktop';
import { RunHistory } from './run-history';
import { StepCard } from './step-card';
import { TriggerEditor } from './trigger-editor';

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Automation editor: what the AI understood (explanation + open questions to answer and re-run the
 * AI with), the trigger, the inputs the user provides once (e.g. the Word template), the step
 * timeline (each step editable, PowerShell scripts approved one by one), then test / run / activate
 * and the run history. Edits are kept locally until "Enregistrer"; every change is re-validated by
 * the server so blocking problems show up next to the step concerned.
 */
@Component({
  selector: 'app-automation-editor',
  imports: [RouterLink, StepCard, TriggerEditor, RunHistory],
  template: `
    <div class="page">
      <a class="crumb" routerLink="/automations">← Automatisations</a>
      @if (loadError()) {
        <div class="card empty">{{ loadError() }}</div>
      } @else if (!draft()) {
        <div class="empty"><span class="spin"></span></div>
      } @else {
        @let a = draft()!;
        <div class="page-head">
          <div class="grow">
            <input class="title-input" [value]="a.name" (input)="patch({ name: $any($event.target).value })" aria-label="Nom de l'automatisation" />
            <div class="meta">
              <span class="pill" [class.green]="a.status === 'active'" [class.amber]="a.status === 'paused'">{{ statusLabel[a.status] }}</span>
              <span class="small muted">{{ triggerText() }}</span>
              @if (a.next_run_at && a.status === 'active') { <span class="small muted">· prochaine exécution {{ fmt(a.next_run_at) }}</span> }
              @if (dirty()) { <span class="small dirty">· modifications non enregistrées</span> }
            </div>
          </div>
          <div class="head-actions">
            <button [disabled]="!dirty() || saving()" (click)="save()">@if (saving()) { <span class="spin"></span> } Enregistrer</button>
            <button [disabled]="!!cannotRun() || busy()" [title]="cannotRun() ?? ''" (click)="run(true)">Test à blanc</button>
            <button [disabled]="!!cannotRun() || busy()" [title]="cannotRun() ?? ''" (click)="run(false)">Exécuter maintenant</button>
            @if (a.status === 'active') {
              <button class="ghost" [disabled]="busy()" (click)="pause()">Mettre en pause</button>
            } @else {
              <button class="primary" [disabled]="busy() || errors().length > 0 || unapprovedScripts() > 0" [title]="activateBlock()" (click)="activate()">Activer</button>
            }
          </div>
        </div>
        @if (message(); as m) { <div class="banner" [class.err]="messageIsError()">{{ m }}</div> }

        <!-- What the AI understood -->
        @if (notes(); as n) {
          <section class="card ai">
            <div class="ai-head"><span class="spark"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l1.8 5L19 10l-5.2 2L12 17l-1.8-5L5 10l5.2-2z"/><path d="M19 4l.6 1.7L21 6l-1.4.6L19 8l-.6-1.4L17 6l1.4-.3z"/></svg></span><h2>Ce que fera l'automatisation</h2></div>
            @if (n.explanation) { <p class="explain">{{ n.explanation }}</p> }
            @if (n.questions.length) {
              <div class="questions">
                <div class="small muted">L'IA a besoin de quelques précisions :</div>
                @for (q of n.questions; track q) {
                  <label class="q"><span>{{ q }}</span>
                    <input [value]="answers()[q] || ''" (input)="setAnswer(q, $any($event.target).value)" placeholder="Votre réponse" />
                  </label>
                }
              </div>
            }
            <div class="refine">
              <textarea rows="2" [value]="instruction()" (input)="instruction.set($any($event.target).value)" placeholder="Autre changement ? Ex. « envoie aussi une copie à rh@acme.fr », « ouvre d'abord l'application Sage »…"></textarea>
              <button class="primary" [disabled]="recompiling() || (!hasAnswers() && !instruction().trim())" (click)="recompile()">
                @if (recompiling()) { <span class="spin light"></span> } Mettre à jour avec l'IA
              </button>
            </div>
            @if (n.warnings.length) {
              <ul class="warns">@for (w of n.warnings; track w) { <li>{{ w }}</li> }</ul>
            }
          </section>
        }

        <div class="cols">
          <div class="main-col">
            <!-- Steps -->
            <section class="card">
              <div class="card-head"><h2>Étapes</h2><span class="small muted">{{ a.steps.length }} étape(s)</span></div>
              @if (needsDesktop() && !desktop.available) {
                <p class="note">Certaines étapes (applications, sites web, impression, fichiers du PC) s'exécutent dans l'<a routerLink="/download">application bureau</a>.</p>
              }
              @if (hasWebSteps() && desktop.available) {
                <div class="note row">
                  <span>Les sites web utilisent un navigateur dédié aux automatisations : connectez-vous-y une fois aux sites concernés.</span>
                  <button class="sm" (click)="openAutomationBrowser()">Ouvrir le navigateur des automatisations</button>
                </div>
              }
              <div class="timeline">
                @for (s of a.steps; track s.id; let i = $index; let last = $last) {
                  <app-step-card
                    [step]="s" (stepChange)="replaceStep(i, $event)"
                    [index]="i" [last]="last" [entry]="entry(s.type)" [issues]="issuesFor(s.id)"
                    [approved]="isApproved(s)" [approving]="approving()"
                    (approve)="approveScript($event)" (remove)="removeStep(i)" (move)="moveStep(i, $event)" />
                } @empty { <p class="empty small">Aucune étape.</p> }
              </div>
              <div class="add">
                <select #pick>
                  <option value="">+ Ajouter une étape…</option>
                  @for (g of catalogGroups(); track g.label) {
                    <optgroup [label]="g.label">
                      @for (e of g.entries; track e.type) { <option [value]="e.type">{{ e.title }}</option> }
                    </optgroup>
                  }
                </select>
                <button class="sm" (click)="addStep(pick.value); pick.value = ''">Ajouter</button>
              </div>
              @if (generalIssues().length) {
                <ul class="issues">@for (e of generalIssues(); track e.message) { <li>{{ e.message }}</li> }</ul>
              }
            </section>

            <section class="card">
              <div class="card-head"><h2>Historique des exécutions</h2></div>
              <app-run-history [automationId]="a.id" />
            </section>
          </div>

          <div class="side-col">
            <section class="card">
              <h2>Déclencheur</h2>
              <app-trigger-editor [trigger]="a.trigger" (triggerChange)="patch({ trigger: withTz($event) })" />
            </section>

            <section class="card">
                <h2>À fournir une fois</h2>
                @for (k of inputKeys(); track k) {
                  @let inp = a.inputs[k];
                  <div class="input-row">
                    <div class="small"><strong>{{ inp.label || k }}</strong> <span class="faint mono">{{ k }}</span></div>
                    @if (inp.type === 'file') {
                      @if (inp.value?.file_id) { <div class="small ok"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px" aria-hidden="true"><path d="M21 12.5 12.5 21a5 5 0 0 1-7-7l8-8a3.3 3.3 0 0 1 4.7 4.7l-8 8a1.7 1.7 0 0 1-2.4-2.4l7.3-7.3"/></svg> {{ inp.value.name }}</div> } @else { <div class="small warn">Aucun fichier</div> }
                      <label class="btn sm">{{ inp.value?.file_id ? 'Remplacer' : 'Choisir un fichier' }}
                        <input type="file" hidden (change)="uploadInput(k, $any($event.target).files?.[0]); $any($event.target).value = ''" />
                      </label>
                      @if (uploading() === k) { <span class="spin"></span> }
                    } @else if (inp.type === 'dataset') {
                      <div class="ds">
                        <textarea rows="4" [value]="datasetText(k)" (input)="setDataset(k, $any($event.target).value)"
                          placeholder="Collez un tableau — 1re ligne = noms de colonnes (virgule, point-virgule ou tabulation). Ex.
Nom,Email,Montant
Dupont,dupont@acme.fr,1200
Martin,martin@acme.fr,980"></textarea>
                        @if (datasetSummary(inp); as s) { <div class="small ok">{{ s }}</div> } @else { <div class="small warn">Aucune ligne — collez vos données ci-dessus</div> }
                        <p class="small faint">Chaque colonne devient <span class="mono">{{ '{{' }}row.NomDeColonne{{ '}}' }}</span> dans les étapes ; l'automatisation est rejouée une fois par ligne.</p>
                      </div>
                    } @else {
                      <input [value]="inp.value ?? ''" (input)="setInput(k, $any($event.target).value)" />
                    }
                  </div>
                }
                @if (!inputKeys().length) { <p class="small faint">Aucune entrée à fournir.</p> }
                @if (!hasDataset()) {
                  <button class="ghost sm" (click)="addDataset()">+ Jeu de données — rejouer pour chaque ligne (un utilisateur/ligne, avec ses données)</button>
                }
              </section>

            <section class="card">
              <h2>Sécurité</h2>
              <label class="check"><input type="checkbox" [checked]="a.require_confirmation" (change)="patch({ require_confirmation: $any($event.target).checked })" />
                Demander confirmation avant chaque exécution planifiée</label>
              <p class="small faint">Une notification « Exécuter / Ignorer » s'affiche au moment prévu. Désactivez-la une fois l'automatisation éprouvée.</p>
              @if (unapprovedScripts()) { <p class="small warn">{{ unapprovedScripts() }} script(s) PowerShell à approuver avant activation.</p> }
              @if (errors().length) { <p class="small warn">{{ errors().length }} problème(s) bloquant(s) à corriger.</p> }
              <button class="ghost ko sm" (click)="remove()">Supprimer l'automatisation</button>
            </section>
          </div>
        </div>
      }
    </div>
  `,
  styles: `
    .crumb { display: inline-block; margin-bottom: 10px; font-size: 13px; }
    .page-head { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
    .grow { flex: 1; min-width: 260px; }
    .title-input { width: 100%; font-size: 22px; font-weight: 600; border: 1px solid transparent; background: transparent; color: var(--text); padding: 2px 6px; margin-left: -6px; border-radius: 8px; }
    .title-input:hover, .title-input:focus { border-color: var(--border); background: var(--surface); outline: none; }
    .meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 4px; }
    .dirty { color: var(--amber, #d97706); }
    .head-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .banner { margin: 12px 0; padding: 10px 14px; border-radius: 10px; background: var(--surface-2); font-size: 13px; animation: in .2s ease-out; }
    .banner.err { color: var(--red); }
    .ai { margin: 16px 0; border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); }
    .ai-head { display: flex; align-items: center; gap: 8px; }
    .spark { font-size: 16px; }
    .explain { margin: 8px 0 0; line-height: 1.55; }
    .questions { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
    .q { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
    .refine { display: flex; gap: 8px; align-items: flex-end; margin-top: 12px; }
    .refine textarea { flex: 1; resize: vertical; }
    .warns { margin: 10px 0 0; padding-left: 18px; font-size: 12px; color: var(--muted); }
    .cols { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; margin-top: 16px; }
    @media (max-width: 1000px) { .cols { grid-template-columns: 1fr; } }
    .main-col, .side-col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
    .side-col h2 { margin-bottom: 10px; }
    .timeline { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
    .note { font-size: 12px; color: var(--muted); background: var(--surface-2); border-radius: 8px; padding: 8px 10px; margin: 8px 0 0; }
    .note.row { display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap; }
    .add { display: flex; gap: 6px; margin-top: 12px; }
    .add select { flex: 1; }
    .issues { color: var(--red); font-size: 12px; margin: 10px 0 0; padding-left: 18px; }
    .input-row { display: flex; flex-direction: column; gap: 6px; padding: 10px 0; border-top: 1px solid var(--border); }
    .input-row:first-of-type { border-top: 0; padding-top: 0; }
    .ok { color: var(--green); } .warn { color: var(--amber, #d97706); }
    .btn { display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--border); border-radius: 8px; padding: 5px 10px; cursor: pointer; background: var(--surface); width: fit-content; }
    .check { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; }
    .ko { color: var(--red); margin-top: 8px; }
    @keyframes in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
  `,
})
export class AutomationEditorPage {
  private api = inject(AutomationApi);
  private runner = inject(AutomationRunner);
  private router = inject(Router);
  protected desktop = inject(Desktop);
  readonly id = input.required<string>();

  protected statusLabel: Record<string, string> = { draft: 'Brouillon', active: 'Active', paused: 'En pause' };
  protected saved = signal<Automation | null>(null);
  protected draft = signal<Automation | null>(null);
  protected catalog = signal<CatalogEntry[]>([]);
  protected notes = signal<CompileNotes | null>(null);
  protected answers = signal<Record<string, string>>({});
  protected instruction = signal('');
  protected issues = signal<{ errors: ValidationIssue[]; warnings: ValidationIssue[] }>({ errors: [], warnings: [] });
  protected loadError = signal('');
  protected saving = signal(false);
  protected busy = signal(false);
  protected recompiling = signal(false);
  protected approving = signal(false);
  protected uploading = signal<string | null>(null);
  protected message = signal('');
  protected messageIsError = signal(false);

  protected dirty = computed(() => {
    const a = this.draft(), s = this.saved();
    return !!a && !!s && JSON.stringify(this.editable(a)) !== JSON.stringify(this.editable(s));
  });
  protected errors = computed(() => this.issues().errors);
  protected generalIssues = computed(() => this.issues().errors.filter(e => !e.step_id));
  protected triggerText = computed(() => triggerSummary(this.draft()!.trigger));
  protected inputKeys = computed(() => Object.keys(this.draft()?.inputs ?? {}));
  protected hasWebSteps = computed(() => (this.draft()?.steps ?? []).some(s => s.type === 'web.browse'));
  protected needsDesktop = computed(() => this.runner.localSteps(this.draft() ?? { steps: [] }).length > 0);
  protected cannotRun = computed(() => {
    const a = this.draft();
    if (!a) return 'Chargement…';
    if (this.dirty()) return 'Enregistrez d’abord vos modifications';
    return this.runner.cannotRunReason(a) ?? null;
  });
  private approvedSet = signal<Set<string>>(new Set());
  private scriptHashes = signal<Record<string, string>>({});
  protected unapprovedScripts = computed(() => (this.draft()?.steps ?? [])
    .filter(s => s.type === 'local.powershell' && !this.isApproved(s)).length);
  protected activateBlock = computed(() => this.dirty() ? 'Enregistrez d’abord vos modifications'
    : this.errors().length ? 'Corrigez les problèmes bloquants' : this.unapprovedScripts() ? 'Approuvez les scripts PowerShell' : '');
  protected catalogGroups = computed(() => {
    const groups: { label: string; test: (t: string) => boolean }[] = [
      { label: 'Mails et documents', test: t => /^(mail|file|doc)\./.test(t) },
      { label: 'Applications du PC', test: t => t.startsWith('desktop.') },
      { label: 'Web', test: t => t.startsWith('web.') },
      { label: 'Fichiers et impression', test: t => t.startsWith('local.') },
      { label: 'IA', test: t => t.startsWith('ai.') },
      { label: 'Contrôle', test: t => t.startsWith('flow.') },
    ];
    return groups.map(g => ({ label: g.label, entries: this.catalog().filter(e => g.test(e.type)) })).filter(g => g.entries.length);
  });

  private validateTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => { const id = this.id(); untracked(() => void this.load(id)); });
    // Re-validate (debounced) whenever the workflow changes.
    effect(() => {
      const a = this.draft();
      if (!a) return;
      const body = { trigger: a.trigger, steps: a.steps, inputs: a.inputs };
      untracked(() => {
        if (this.validateTimer) clearTimeout(this.validateTimer);
        this.validateTimer = setTimeout(() => void this.validate(body), 400);
      });
    });
    // Script approval is by hash of the exact script text shown.
    effect(() => {
      const scripts = (this.draft()?.steps ?? []).filter(s => s.type === 'local.powershell').map(s => String(s.params?.['script'] ?? ''));
      untracked(() => void Promise.all(scripts.map(async t => [t, await sha256Hex(t)] as const)).then(p => this.scriptHashes.set(Object.fromEntries(p))));
    });
    inject(DestroyRef).onDestroy(() => { if (this.validateTimer) clearTimeout(this.validateTimer); });
  }

  private async load(id: string) {
    this.loadError.set('');
    try {
      const [a, cat] = await Promise.all([this.api.get(id), this.runner.ensureCatalog()]);
      this.catalog.set(cat);
      this.setSaved(a);
      const n = loadNotes(id);
      this.notes.set(n);
      this.answers.set(n?.answers ?? {});
    } catch (err: any) {
      this.loadError.set(err?.status === 404 ? 'Automatisation introuvable.' : `Chargement impossible : ${errorText(err)}`);
    }
  }

  private setSaved(a: Automation) {
    const copy = structuredClone(a);
    this.saved.set(a);
    this.draft.set(copy);
    this.approvedSet.set(new Set(a.approved_script_hashes ?? []));
  }

  private editable(a: Automation) {
    return { name: a.name, description: a.description, trigger: a.trigger, steps: a.steps, inputs: a.inputs, require_confirmation: a.require_confirmation };
  }

  protected entry(type: string) { return this.catalog().find(e => e.type === type); }
  protected issuesFor(stepId: string) { return this.issues().errors.filter(e => e.step_id === stepId); }
  protected isApproved(s: Step) {
    const h = this.scriptHashes()[String(s.params?.['script'] ?? '')];
    return !!h && this.approvedSet().has(h);
  }
  protected withTz(t: Trigger) { return withTimezone(t); }
  protected fmt(iso: string) { return new Date(iso).toLocaleString('fr-FR', { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); }
  protected hasAnswers() { return Object.values(this.answers()).some(v => v.trim()); }

  protected patch(p: Partial<Automation>) { this.draft.update(a => a ? { ...a, ...p } : a); }
  protected replaceStep(i: number, s: Step) { this.draft.update(a => a ? { ...a, steps: a.steps.map((x, j) => j === i ? s : x) } : a); }
  protected removeStep(i: number) { this.draft.update(a => a ? { ...a, steps: a.steps.filter((_, j) => j !== i) } : a); }
  protected moveStep(i: number, delta: number) {
    this.draft.update(a => {
      if (!a) return a;
      const j = i + delta;
      if (j < 0 || j >= a.steps.length) return a;
      const steps = [...a.steps];
      [steps[i], steps[j]] = [steps[j], steps[i]];
      return { ...a, steps };
    });
  }
  protected addStep(type: string) {
    const e = this.entry(type);
    if (!e) return;
    this.draft.update(a => {
      if (!a) return a;
      const used = new Set(a.steps.map(s => s.id));
      let n = a.steps.length + 1;
      while (used.has(`s${n}`)) n++;
      const params: Record<string, any> = {};
      for (const p of e.params) if (p.default !== undefined) params[p.name] = p.default;
      return { ...a, steps: [...a.steps, { id: `s${n}`, type, title: e.title, params }] };
    });
  }
  protected setInput(k: string, value: string) {
    this.draft.update(a => a ? { ...a, inputs: { ...a.inputs, [k]: { ...a.inputs[k], value } } } : a);
  }
  protected setAnswer(q: string, v: string) { this.answers.update(x => ({ ...x, [q]: v })); }

  // ---- dataset inputs (a pasted table → one automation run per row) ----
  /** Raw pasted text per dataset input, kept so the textarea stays editable; parsed into rows on input. */
  private dsText = signal<Record<string, string>>({});

  protected datasetText(k: string): string {
    const cached = this.dsText()[k];
    if (cached !== undefined) return cached;
    const rows = this.draft()?.inputs[k]?.value;
    return Array.isArray(rows) && rows.length ? rowsToCsv(rows) : '';
  }
  protected setDataset(k: string, text: string) {
    this.dsText.update(m => ({ ...m, [k]: text }));
    const rows = parseTable(text);
    this.draft.update(a => a ? { ...a, inputs: { ...a.inputs, [k]: { ...a.inputs[k], value: rows } } } : a);
  }
  protected datasetSummary(inp: AutomationInput): string {
    const rows = Array.isArray(inp.value) ? inp.value as Record<string, unknown>[] : [];
    if (!rows.length) return '';
    const cols = Object.keys(rows[0] ?? {});
    return `${rows.length} ligne(s) · ${cols.length} colonne(s) : ${cols.slice(0, 8).join(', ')}${cols.length > 8 ? '…' : ''}`;
  }
  protected hasDataset(): boolean {
    return Object.values(this.draft()?.inputs ?? {}).some(i => i.type === 'dataset');
  }
  protected addDataset() {
    this.draft.update(a => a && !Object.values(a.inputs).some(i => i.type === 'dataset')
      ? { ...a, inputs: { ...a.inputs, donnees: { label: 'Jeu de données', type: 'dataset', value: [] } } } : a);
  }

  private say(msg: string, isError = false) { this.message.set(msg); this.messageIsError.set(isError); }

  private async validate(body: { trigger: Trigger; steps: Step[]; inputs: Automation['inputs'] }) {
    try {
      const r = await this.api.validate(body);
      const asIssue = (w: any): ValidationIssue => typeof w === 'string' ? { message: w } : w;
      this.issues.set({ errors: (r.errors ?? []).map(asIssue), warnings: (r.warnings ?? []).map(asIssue) });
    } catch { /* validation is advisory in the editor; the server re-checks on activate */ }
  }

  protected async save(): Promise<boolean> {
    const a = this.draft();
    if (!a) return false;
    this.saving.set(true);
    try {
      this.setSaved(await this.api.update(a.id, this.editable(a)));
      this.say('Modifications enregistrées.');
      return true;
    } catch (err) {
      this.say(`Enregistrement impossible : ${errorText(err)}`, true);
      return false;
    } finally {
      this.saving.set(false);
    }
  }

  protected async recompile() {
    const a = this.draft();
    if (!a) return;
    this.recompiling.set(true);
    this.say('');
    try {
      const answers = Object.fromEntries(Object.entries(this.answers()).filter(([, v]) => v.trim()));
      const res = await this.api.compile({
        description: this.instruction().trim() || a.description || a.source?.prompt || a.name,
        recording_id: a.source?.recording_id, answers, current: this.editable(a) as Partial<Automation>,
      });
      // Keep files/values the user already provided for inputs that still exist.
      const inputs: Automation['inputs'] = {};
      for (const [k, v] of Object.entries(res.inputs ?? {})) {
        const type = v.type === 'file' ? 'file' : v.type === 'dataset' ? 'dataset' : 'text';
        inputs[k] = { label: v.label || k, type, value: a.inputs[k]?.value ?? v.value ?? null };
      }
      this.draft.set({ ...a, name: res.name || a.name, description: res.description || a.description, trigger: withTimezone(res.trigger), steps: res.steps ?? a.steps, inputs });
      const n = notesFromCompile(res, { answers });
      saveNotes(a.id, n);
      this.notes.set(n);
      this.instruction.set('');
      this.say('Automatisation mise à jour par l’IA — vérifiez puis enregistrez.');
    } catch (err) {
      this.say(`Mise à jour impossible : ${errorText(err)}`, true);
    } finally {
      this.recompiling.set(false);
    }
  }

  protected async uploadInput(key: string, file: File | undefined) {
    const a = this.draft();
    if (!a || !file) return;
    this.uploading.set(key);
    try {
      const meta = await this.api.uploadFile(file, file.name, { automation_id: a.id });
      this.draft.update(d => d ? { ...d, inputs: { ...d.inputs, [key]: { ...d.inputs[key], value: { file_id: meta.file_id ?? (meta as any).id, name: meta.name ?? file.name } } } } : d);
      this.say(`« ${file.name} » ajouté — pensez à enregistrer.`);
    } catch (err) {
      this.say(`Envoi impossible : ${errorText(err)}`, true);
    } finally {
      this.uploading.set(null);
    }
  }

  protected async approveScript(script: string) {
    const a = this.draft();
    if (!a) return;
    if (this.dirty() && !(await this.save())) return; // the approved text must be the saved one
    this.approving.set(true);
    try {
      const updated = await this.api.approveScript(a.id, await sha256Hex(script));
      this.approvedSet.set(new Set(updated.approved_script_hashes ?? []));
      this.saved.update(s => s ? { ...s, approved_script_hashes: updated.approved_script_hashes } : s);
      this.draft.update(d => d ? { ...d, approved_script_hashes: updated.approved_script_hashes } : d);
      this.say('Script approuvé : il pourra s’exécuter tel quel.');
    } catch (err) {
      this.say(`Approbation impossible : ${errorText(err)}`, true);
    } finally {
      this.approving.set(false);
    }
  }

  protected async run(dryRun: boolean) {
    const a = this.saved();
    if (!a) return;
    this.busy.set(true);
    try {
      const { done } = await this.runner.runNow(a, dryRun);
      this.say(dryRun ? 'Test à blanc en cours — suivez la progression dans l’historique.' : 'Exécution en cours…');
      void done.then(status => {
        this.say(status === 'succeeded' ? (dryRun ? 'Test à blanc réussi : rien n’a été envoyé ni modifié.' : 'Exécution réussie.')
          : `Exécution terminée : ${status}. Détails dans l’historique.`, status !== 'succeeded');
        void this.api.get(a.id).then(x => this.setSaved(x)).catch(() => {});
      });
    } catch (err) {
      this.say(`Lancement impossible : ${errorText(err)}`, true);
    } finally {
      this.busy.set(false);
    }
  }

  protected async activate() {
    const a = this.draft();
    if (!a) return;
    this.busy.set(true);
    try {
      this.setSaved(await this.api.activate(a.id));
      await this.runner.refreshActiveCount();
      this.say(this.saved()!.trigger.type === 'manual' ? 'Automatisation activée (déclenchement manuel).' : `Automatisation activée — ${triggerSummary(this.saved()!.trigger)}.`);
    } catch (err) {
      this.say(`Activation impossible : ${errorText(err)}`, true);
    } finally {
      this.busy.set(false);
    }
  }

  protected async pause() {
    const a = this.draft();
    if (!a) return;
    this.busy.set(true);
    try {
      this.setSaved(await this.api.pause(a.id));
      await this.runner.refreshActiveCount();
      this.say('Automatisation en pause.');
    } catch (err) {
      this.say(`Impossible de mettre en pause : ${errorText(err)}`, true);
    } finally {
      this.busy.set(false);
    }
  }

  protected async remove() {
    const a = this.draft();
    if (!a || !window.confirm(`Supprimer « ${a.name} » et son historique ?`)) return;
    try {
      await this.api.remove(a.id);
      dropNotes(a.id);
      await this.runner.refreshActiveCount();
      await this.router.navigateByUrl('/automations');
    } catch (err) {
      this.say(`Suppression impossible : ${errorText(err)}`, true);
    }
  }

  protected openAutomationBrowser() {
    const first = this.draft()?.steps.find(s => s.type === 'web.browse');
    const url = String(first?.params?.['url'] ?? '');
    void this.desktop.automation?.openBrowser({ url: /^https?:\/\//.test(url) ? url : undefined });
  }
}

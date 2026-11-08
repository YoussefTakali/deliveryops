import { Component, computed, input, model, output, signal } from '@angular/core';

import { stepSummary } from '../../automations/describe';
import { CatalogEntry, CatalogParam, Step, ValidationIssue } from '../../automations/models';
import { StepIcon } from './step-icon';

/** One step of the timeline: icon, title, human summary, badges; expands into a param form
 *  generated from the catalog (with an "Avancé (JSON)" escape hatch). */
@Component({
  selector: 'app-step-card',
  imports: [StepIcon],
  template: `
    <div class="step" [class.open]="open()" [class.has-error]="issues().length">
      <div class="rail"><app-step-icon [type]="step().type" [class]="entry()?.executor ?? ''" /><span class="num">{{ index() + 1 }}</span></div>
      <div class="body">
        <button type="button" class="head" (click)="open.set(!open())" [attr.aria-expanded]="open()">
          <span class="title">{{ step().title || entry()?.title || step().type }}</span>
          <span class="badges">
            @if (step().foreach) { <span class="pill accent" title="{{ step().foreach }}">Pour chaque élément</span> }
            @if (step().when) { <span class="pill" title="{{ step().when }}">Sous condition</span> }
            @if (entry()?.executor === 'local') { <span class="pill amber">Sur ce PC</span> }
            @if (entry()?.side_effect) { <span class="pill red" title="Cette étape agit réellement (envoi, impression, écriture…). Un test à blanc ne fait que la simuler.">Action réelle</span> }
            @if (step().on_error === 'continue') { <span class="pill" title="En cas d'erreur, l'exécution continue">Continue si erreur</span> }
            @if (isScript()) { @if (approved()) { <span class="pill green">Script approuvé</span> } @else { <span class="pill red">Script à approuver</span> } }
          </span>
          <svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        </button>
        <div class="summary small muted">{{ summary() }}</div>
        @for (i of issues(); track $index) { <div class="small issue">{{ i.message }}</div> }
        @if (!entry()) { <div class="small issue">Type d'étape inconnu : {{ step().type }}</div> }

        @if (isScript()) {
          <div class="script">
            <div class="script-head small">
              <span>Script PowerShell exécuté tel quel sur ce PC (sans droits administrateur)</span>
              @if (!approved()) { <button type="button" class="sm primary" [disabled]="approving()" (click)="approve.emit(scriptText())">Approuver ce script</button> }
            </div>
            <pre><code>{{ scriptText() }}</code></pre>
            @if (!approved()) { <p class="small faint">Lisez-le entièrement : l'automatisation ne pourra pas être activée ni exécutée tant que ce script exact n'est pas approuvé. Toute modification demandera une nouvelle approbation.</p> }
          </div>
        }

        @if (open()) {
          <div class="form">
            <div class="row between">
              <div class="f grow"><label>Titre</label><input [value]="step().title ?? ''" (change)="set('title', $any($event.target).value)" /></div>
              <label class="adv"><input type="checkbox" [checked]="advanced()" (change)="toggleAdvanced($any($event.target).checked)" /> Avancé (JSON)</label>
            </div>
            @if (advanced()) {
              <textarea class="mono json" rows="12" [value]="json()" (input)="json.set($any($event.target).value)"></textarea>
              @if (jsonError()) { <div class="small issue">{{ jsonError() }}</div> }
              <div class="row"><button type="button" class="sm primary" (click)="applyJson()">Appliquer</button><button type="button" class="sm ghost" (click)="toggleAdvanced(true)">Réinitialiser</button></div>
            } @else {
              @for (p of params(); track p.name) {
                <div class="f">
                  <label [for]="fid(p)">{{ p.description || p.name }} @if (p.required) { <span class="req">*</span> } <span class="pname">{{ p.name }}</span></label>
                  @switch (p.type) {
                    @case ('boolean') {
                      <label class="check"><input [id]="fid(p)" type="checkbox" [checked]="param(p) === true || param(p) === 'true'" (change)="setParam(p, $any($event.target).checked)" /> Oui</label>
                    }
                    @case ('choice') {
                      <select [id]="fid(p)" [value]="display(p)" (change)="setParam(p, $any($event.target).value)">
                        <option value="">—</option>
                        @for (o of p.options ?? []; track o) { <option [value]="o">{{ o }}</option> }
                      </select>
                    }
                    @case ('text') {
                      <textarea [id]="fid(p)" rows="4" [class.mono]="p.name === 'script'" [value]="display(p)" (change)="setParam(p, $any($event.target).value)"></textarea>
                    }
                    @case ('list') {
                      <textarea [id]="fid(p)" rows="8" class="mono" [value]="displayList(p)" placeholder='[{"do": "click", "selector": "text=Se connecter"}]' (change)="setParam(p, $any($event.target).value)"></textarea>
                    }
                    @default {
                      <input [id]="fid(p)" [value]="display(p)" [placeholder]="placeholder(p)" (change)="setParam(p, $any($event.target).value)" />
                    }
                  }
                </div>
              }
              <div class="grid3">
                <div class="f"><label>Pour chaque élément de (foreach)</label><input [value]="step().foreach ?? ''" [placeholder]="foreachPh" (change)="set('foreach', $any($event.target).value)" /></div>
                <div class="f"><label>Seulement si (when)</label><input [value]="step().when ?? ''" [placeholder]="whenPh" (change)="set('when', $any($event.target).value)" /></div>
                <div class="f"><label>En cas d'erreur</label>
                  <select [value]="step().on_error ?? 'stop'" (change)="set('on_error', $any($event.target).value)">
                    <option value="stop">Arrêter l'exécution</option><option value="continue">Continuer</option>
                  </select></div>
              </div>
              @if (entry()?.output; as out) { <p class="small faint">Résultat disponible sous <code>steps.{{ step().id }}</code> : {{ out }}</p> }
            }
            <div class="row tools">
              <button type="button" class="sm ghost" [disabled]="index() === 0" (click)="move.emit(-1)">↑ Monter</button>
              <button type="button" class="sm ghost" [disabled]="last()" (click)="move.emit(1)">↓ Descendre</button>
              <span class="spacer"></span>
              <button type="button" class="sm ghost ko" (click)="remove.emit()">Supprimer l'étape</button>
            </div>
          </div>
        }
      </div>
    </div>
  `,
  styles: `
    :host { display: block; }
    .step { display: grid; grid-template-columns: 38px 1fr; gap: 12px; position: relative; }
    .rail { display: flex; flex-direction: column; align-items: center; gap: 4px; position: relative; }
    .rail::after { content: ''; position: absolute; top: 40px; bottom: -14px; width: 2px; background: var(--border); }
    :host(:last-child) .rail::after { display: none; }
    .num { font-size: 10px; color: var(--faint); font-weight: 600; }
    .body { min-width: 0; padding-bottom: 16px; }
    .head { width: 100%; border: 0; background: transparent; padding: 4px 0; display: flex; align-items: center; gap: 8px; text-align: left; flex-wrap: wrap; }
    .head:hover { background: transparent; }
    .title { font-weight: 600; font-size: 14px; color: var(--text); }
    .badges { display: flex; gap: 4px; flex-wrap: wrap; }
    .badges .pill { font-size: 11px; }
    .chev { width: 16px; height: 16px; margin-left: auto; fill: none; stroke: var(--faint); stroke-width: 2; transition: transform .2s ease; }
    .open .chev { transform: rotate(180deg); }
    .summary { margin-top: 1px; word-break: break-word; }
    .issue { color: var(--red); margin-top: 4px; }
    .script { margin-top: 8px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
    .script-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 10px; background: var(--surface-2); color: var(--muted); }
    .script pre { margin: 0; padding: 10px 12px; max-height: 360px; overflow: auto; background: var(--bg); font-size: 12px; line-height: 1.5; }
    .script p { padding: 0 10px 8px; margin: 0; }
    .form { margin-top: 10px; padding: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface-2); display: flex; flex-direction: column; gap: 10px;
      animation: open .18s ease; }
    @keyframes open { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
    .between { justify-content: space-between; align-items: flex-end; }
    .grow { flex: 1; }
    .adv, .check { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); cursor: pointer; margin: 0; }
    .adv input, .check input { width: auto; }
    .req { color: var(--red); }
    .pname { font-family: "Cascadia Code", Consolas, monospace; font-size: 10.5px; color: var(--faint); margin-left: 4px; }
    .json { font-size: 12px; }
    textarea.mono { font-family: "Cascadia Code", Consolas, monospace; font-size: 12px; }
    .grid3 { display: grid; grid-template-columns: 1fr 1fr 180px; gap: 10px; }
    .tools { border-top: 1px solid var(--border); padding-top: 8px; }
    @media (max-width: 760px) { .grid3 { grid-template-columns: 1fr; } }
    @media (prefers-reduced-motion: reduce) { .form { animation: none; } }
  `,
})
export class StepCard {
  readonly step = model.required<Step>();
  readonly index = input(0);
  readonly last = input(false);
  readonly entry = input<CatalogEntry | undefined>(undefined);
  readonly issues = input<ValidationIssue[]>([]);
  readonly approved = input(false);
  readonly approving = input(false);
  readonly approve = output<string>();
  readonly remove = output<void>();
  readonly move = output<number>();

  protected foreachPh = 'ex. {{steps.s3.rows}}';
  protected whenPh = 'ex. {{steps.s1.latest}}';
  protected open = signal(false);
  protected advanced = signal(false);
  protected json = signal('');
  protected jsonError = signal('');
  protected summary = computed(() => stepSummary(this.step(), this.entry()));
  protected isScript = computed(() => this.step().type === 'local.powershell');
  protected scriptText = computed(() => String(this.step().params?.['script'] ?? ''));
  /** Catalog params, plus any extra param the AI set that the catalog doesn't list. */
  protected params = computed<CatalogParam[]>(() => {
    const known = this.entry()?.params ?? [];
    const extra = Object.keys(this.step().params ?? {}).filter(k => !known.some(p => p.name === k))
      .map(k => ({ name: k, type: 'string', required: false, description: k }));
    return [...known, ...extra];
  });

  protected fid(p: CatalogParam) { return `p-${this.step().id}-${p.name}`; }
  protected param(p: CatalogParam) { return this.step().params?.[p.name]; }
  protected display(p: CatalogParam): string {
    const v = this.param(p);
    if (v === undefined || v === null) return '';
    return typeof v === 'string' ? v : JSON.stringify(v);
  }
  /** One action per line keeps a web.browse sequence readable and diffable in the form. */
  protected displayList(p: CatalogParam): string {
    const v = this.param(p);
    if (!Array.isArray(v)) return this.display(p);
    return '[\n' + v.map(x => '  ' + JSON.stringify(x)).join(',\n') + '\n]';
  }
  protected placeholder(p: CatalogParam): string {
    if (p.default !== undefined) return `par défaut : ${p.default}`;
    if (p.type === 'file_id') return 'ex. {{steps.s2.file.file_id}}';
    if (p.type === 'emails') return 'ex. {{item.Email}} ou a@x.fr; b@y.fr';
    if (p.type === 'object') return 'ex. {{item}}';
    return '';
  }

  protected setParam(p: CatalogParam, raw: unknown) {
    const params = { ...(this.step().params ?? {}) };
    let v: unknown = raw;
    if (typeof raw === 'string') {
      const s = raw;
      if (s.trim() === '') v = undefined;
      else if (p.type === 'number' && !s.includes('{{') && Number.isFinite(Number(s))) v = Number(s);
      else if (['object', 'file_ids', 'emails', 'list'].includes(p.type) && /^\s*[[{]/.test(s) && !/^\s*\{\{/.test(s)) {
        try { v = JSON.parse(s); } catch { v = s; }
      }
    }
    if (v === undefined || (p.type === 'boolean' && v === false && !p.required)) delete params[p.name];
    else params[p.name] = v;
    this.step.set({ ...this.step(), params });
  }

  protected set(field: 'title' | 'foreach' | 'when' | 'on_error', value: string) {
    const next: any = { ...this.step() };
    if (value.trim() === '' || (field === 'on_error' && value === 'stop')) delete next[field];
    else next[field] = value;
    this.step.set(next);
  }

  protected toggleAdvanced(on: boolean) {
    this.advanced.set(on);
    this.jsonError.set('');
    if (on) this.json.set(JSON.stringify(this.step(), null, 2));
  }

  protected applyJson() {
    try {
      const parsed = JSON.parse(this.json());
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('un objet JSON est attendu');
      if (!parsed.type || typeof parsed.type !== 'string') throw new Error('« type » est obligatoire');
      if (!parsed.id) parsed.id = this.step().id;
      if (!parsed.params || typeof parsed.params !== 'object') parsed.params = {};
      this.step.set(parsed);
      this.jsonError.set('');
    } catch (err: any) {
      this.jsonError.set(`JSON invalide : ${err?.message ?? err}`);
    }
  }
}

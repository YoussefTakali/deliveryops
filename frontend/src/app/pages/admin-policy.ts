import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';

import { Api, PolicyAction, PolicyDocumentInfo, PolicyRule, PolicyRuleIn, PolicyTestResult, PolicyViolation } from '../api';
import { SectionTabs } from '../section-tabs';

const EMPTY: PolicyRuleIn = { title: '', text: '', category: 'confidentialite', action: 'block', keywords: [], active: true };

/** Company rules (permission `policy.manage`): every agent follows them, and what people type into an
 *  AI feature is checked against them — "block" refuses, "warn" answers with a warning, "guide" only
 *  steers the agents. Rules can be written here or extracted from an uploaded policy document. */
@Component({
  selector: 'app-admin-policy',
  imports: [SectionTabs, DatePipe, NgTemplateOutlet],
  template: `
    <app-section-tabs group="supervision" />
    <div class="page pol">
      <div class="page-head">
        <div>
          <h1>Règles & conformité</h1>
          <p>Les règles de l'entreprise s'appliquent à tous les agents et à tous les utilisateurs : les agents les respectent dans chaque réponse,
            et chaque message envoyé à l'IA (copilote, compagnon, recherche, automatisations) est vérifié avant d'être traité.
            Le contenu des messages n'est jamais conservé : seules la règle, la personne et le lieu sont tracés.</p>
        </div>
      </div>

      @if (error()) { <div class="banner bad" role="alert">{{ error() }}</div> }

      <div class="grid g3 stats">
        <div class="card kpi"><span class="l">Règles en vigueur</span><span class="v">{{ inForce().length }}</span>
          <span class="small faint">{{ count('block') }} bloquante(s) · {{ count('warn') }} avertissement(s) · {{ count('guide') }} guide(s)</span></div>
        <div class="card kpi" [class.todo]="proposed().length"><span class="l">À valider</span><span class="v">{{ proposed().length }}</span>
          <span class="small faint">proposées depuis vos documents</span></div>
        <div class="card kpi"><span class="l">Infractions (30 j)</span><span class="v">{{ violations().length }}</span>
          <span class="small faint">{{ blockedCount() }} bloquée(s)</span></div>
      </div>

      <!-- Try a message -->
      <section class="card">
        <div class="card-head"><div><h2>Tester un message</h2><p>Vérifiez ce que les règles actuelles feraient d'un message (rien n'est enregistré).</p></div></div>
        <form class="test" (submit)="$event.preventDefault(); test()">
          <textarea rows="3" placeholder="Ex. : « Voici l'export des contrats du client Atlas, résume-le » ou « Installe Notepad++ depuis ce site »"
                    [value]="testText()" (input)="testText.set($any($event.target).value)" aria-label="Message à tester"></textarea>
          <button class="primary" type="submit" [disabled]="!testText().trim() || testing()">@if (testing()) { <span class="spin"></span> } Tester</button>
        </form>
        @if (testResult(); as r) {
          <div class="verdict" [class]="r.decision">
            <strong>{{ r.decision === 'blocked' ? '⛔ Refusé' : r.decision === 'warned' ? '⚠ Autorisé avec avertissement' : '✓ Autorisé' }}</strong>
            @for (h of r.blocked.concat(r.warned); track h.rule) {
              <div class="small">« {{ h.rule }} » — {{ h.reason }} <span class="faint">({{ h.by === 'ai' ? 'détecté par l\\'IA' : 'mot-clé' }})</span></div>
            }
          </div>
        }
      </section>

      <!-- Rules -->
      <section class="card">
        <div class="card-head">
          <div><h2>Règles</h2><p>Une règle claire et courte ; les mots-clés déclenchent la règle instantanément (écrivez /…/ pour une expression régulière).</p></div>
          @if (editing() !== 'new') { <button class="primary sm" (click)="startNew()">+ Nouvelle règle</button> }
        </div>

        @if (editing() === 'new') { <ng-container *ngTemplateOutlet="editor" /> }

        @if (proposed().length) {
          <h3 class="sub">À valider <span class="pill amber">{{ proposed().length }}</span></h3>
          <ul class="rules">
            @for (r of proposed(); track r.id) {
              @if (editing() === r.id) { <li class="editing"><ng-container *ngTemplateOutlet="editor" /></li> }
              @else {
                <li class="proposed">
                  <span class="act" [class]="r.action">{{ actions()[r.action] }}</span>
                  <div class="body"><strong>{{ r.title }}</strong>@if (r.text) { <p class="small muted">{{ r.text }}</p> }
                    <div class="meta small faint">{{ categories()[r.category] }}@if (r.document) { · depuis « {{ r.document }} » }</div></div>
                  <div class="acts">
                    <button class="sm primary" (click)="accept(r)">Valider</button>
                    <button class="sm" (click)="edit(r)">Modifier</button>
                    <button class="sm ghost" (click)="remove(r)">Écarter</button>
                  </div>
                </li>
              }
            }
          </ul>
          <h3 class="sub">En vigueur</h3>
        }

        <ul class="rules">
          @for (r of current(); track r.id) {
            @if (editing() === r.id) { <li class="editing"><ng-container *ngTemplateOutlet="editor" /></li> }
            @else {
              <li [class.off]="!r.active">
                <span class="act" [class]="r.action">{{ actions()[r.action] }}</span>
                <div class="body"><strong>{{ r.title }}</strong>@if (r.text) { <p class="small muted">{{ r.text }}</p> }
                  <div class="meta small faint">{{ categories()[r.category] }}@if (r.document) { · depuis « {{ r.document }} » }@if (r.updated_by) { · {{ r.updated_by }} }</div>
                  @if (r.keywords.length) { <div class="kws">@for (k of r.keywords; track k) { <code translate="no">{{ k }}</code> }</div> }
                </div>
                <div class="acts">
                  <label class="tgl" [title]="r.active ? 'Désactiver' : 'Activer'"><input type="checkbox" [checked]="r.active" (change)="toggle(r)" /> {{ r.active ? 'Active' : 'Inactive' }}</label>
                  <button class="sm" (click)="edit(r)">Modifier</button>
                  <button class="sm ghost" (click)="remove(r)">Supprimer</button>
                </div>
              </li>
            }
          } @empty { @if (!proposed().length && editing() !== 'new') { <li class="empty">Aucune règle. Ajoutez-en une ou importez un document.</li> } }
        </ul>
      </section>

      <!-- Documents -->
      <section class="card">
        <div class="card-head"><div><h2>Documents de référence</h2>
          <p>Charte informatique, politique de sécurité, règlement… Les règles sont extraites automatiquement puis proposées à votre validation.</p></div></div>
        <label class="drop" [class.busy]="uploading()">
          <input type="file" accept=".pdf,.docx,.txt,.md" (change)="upload($any($event.target))" [disabled]="uploading()" />
          @if (uploading()) { <span><span class="spin"></span> Lecture et extraction des règles…</span> }
          @else { <span><strong>Choisir un fichier</strong> (PDF, Word, texte)</span> }
        </label>
        @if (documents().length) {
          <ul class="docs">
            @for (d of documents(); track d.id) {
              <li>
                <div class="body"><strong translate="no">{{ d.filename }}</strong>
                  <span class="small faint">Importé le {{ d.uploaded_at | date: 'dd/MM/yyyy' }} par {{ d.uploaded_by }} · {{ d.accepted }} en vigueur · {{ d.proposed }} à valider</span></div>
                @if (d.proposed) { <button class="sm primary" (click)="acceptAll(d)">Tout valider</button> }
                <button class="sm ghost" (click)="removeDocument(d)">Supprimer</button>
              </li>
            }
          </ul>
        }
      </section>

      <!-- Breaches -->
      <section class="card">
        <div class="card-head"><div><h2>Infractions récentes</h2><p>30 derniers jours. Le message lui-même n'est jamais conservé.</p></div></div>
        <div class="scroll">
          <table>
            <thead><tr><th>Date</th><th>Utilisateur</th><th>Où</th><th>Règle</th><th>Décision</th><th>Détection</th></tr></thead>
            <tbody>
              @for (v of violations(); track v.ts + v.user + v.rule) {
                <tr>
                  <td class="small">{{ v.ts | date: 'dd/MM HH:mm' }}</td>
                  <td translate="no">{{ v.user }}</td>
                  <td class="small">{{ v.where }}</td>
                  <td>{{ v.rule }}</td>
                  <td><span class="pill" [class.red]="v.decision === 'blocked'" [class.amber]="v.decision === 'warned'">{{ v.decision === 'blocked' ? 'Bloqué' : 'Averti' }}</span></td>
                  <td class="small faint">{{ v.detected_by === 'ai' ? 'IA' : 'Mot-clé' }}</td>
                </tr>
              } @empty { <tr><td colspan="6" class="empty">Aucune infraction.</td></tr> }
            </tbody>
          </table>
        </div>
      </section>

      @if (saved()) { <p class="toast" role="status">{{ saved() }}</p> }
    </div>

    <ng-template #editor>
      <form class="editor" (submit)="$event.preventDefault(); save()">
        <div class="wide"><label for="r-title">Règle</label>
          <input id="r-title" maxlength="200" placeholder="Ex. : Ne jamais transmettre de données personnelles de clients à l'IA" [value]="draft().title" (input)="patch('title', $any($event.target).value)" /></div>
        <div class="wide"><label for="r-text">Précisions (facultatif)</label>
          <textarea id="r-text" rows="2" maxlength="2000" placeholder="Périmètre, exceptions, qui contacter…" [value]="draft().text" (input)="patch('text', $any($event.target).value)"></textarea></div>
        <div><label for="r-cat">Catégorie</label>
          <select id="r-cat" [value]="draft().category" (change)="patch('category', $any($event.target).value)">
            @for (c of categoryList(); track c[0]) { <option [value]="c[0]">{{ c[1] }}</option> }
          </select></div>
        <div><label>Action</label>
          <div class="seg" role="radiogroup">
            @for (a of actionList; track a.id) {
              <button type="button" role="radio" [attr.aria-checked]="draft().action === a.id" [class.on]="draft().action === a.id" [class]="a.id" (click)="patch('action', a.id)">{{ a.label }}</button>
            }
          </div>
          <div class="small faint hint">{{ actionHint(draft().action) }}</div></div>
        <div class="wide"><label for="r-kw">Mots-clés déclencheurs (séparés par des virgules)</label>
          <input id="r-kw" [placeholder]="keywordsPlaceholder" [value]="keywordsText()" (input)="keywordsText.set($any($event.target).value)" /></div>
        <div class="row wide">
          <label class="tgl"><input type="checkbox" [checked]="draft().active" (change)="patch('active', $any($event.target).checked)" /> Active</label>
          <span class="spacer"></span>
          <button class="sm ghost" type="button" (click)="cancel()">Annuler</button>
          <button class="sm primary" type="submit" [disabled]="draft().title.trim().length < 3 || busy()">Enregistrer</button>
        </div>
      </form>
    </ng-template>
  `,
  styles: `
    .pol { display: flex; flex-direction: column; gap: 24px; }
    .pol .page-head { margin-bottom: 0; }
    .pol .page-head p { max-width: 860px; line-height: 1.55; }
    .card { padding: 20px 22px; }
    .card-head { margin-bottom: 16px; align-items: flex-start; }
    .card-head p { margin-top: 4px; }
    .stats { gap: 20px; }
    .kpi { padding: 18px 20px; gap: 6px; }
    .kpi.todo { border-color: var(--amber); }
    .banner.bad { background: var(--red-soft); color: var(--red); padding: 12px 16px; border-radius: 10px; }
    .test { display: flex; gap: 12px; align-items: flex-start; }
    .test textarea { flex: 1; resize: vertical; }
    .verdict { margin-top: 14px; padding: 12px 14px; border-radius: 10px; display: flex; flex-direction: column; gap: 4px; }
    .verdict.allowed { background: var(--green-soft); color: var(--green); }
    .verdict.warned { background: var(--amber-soft); color: var(--amber); }
    .verdict.blocked { background: var(--red-soft); color: var(--red); }
    .sub { font-size: 13px; margin: 6px 0 8px; display: flex; gap: 8px; align-items: center; }
    .rules, .docs { list-style: none; margin: 0 0 8px; padding: 0; display: flex; flex-direction: column; }
    .rules li, .docs li { display: flex; gap: 14px; align-items: flex-start; padding: 12px 4px; border-bottom: 1px solid var(--border); }
    .rules li:last-child, .docs li:last-child { border-bottom: 0; }
    .rules li.proposed { background: color-mix(in srgb, var(--amber-soft) 50%, transparent); border-radius: 8px; padding: 12px 10px; margin-bottom: 4px; border-bottom: 0; }
    .rules li.off { opacity: .55; }
    .rules li.editing { display: block; }
    .body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
    .body p { margin: 0; line-height: 1.45; }
    .act { flex: none; min-width: 64px; text-align: center; font-size: 11px; font-weight: 600; border-radius: 999px; padding: 2px 8px; margin-top: 1px; }
    .act.block { background: var(--red-soft); color: var(--red); }
    .act.warn { background: var(--amber-soft); color: var(--amber); }
    .act.guide { background: var(--accent-soft); color: var(--accent); }
    .kws { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
    .kws code { font-size: 11px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px; }
    .acts { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    .tgl { display: inline-flex; align-items: center; gap: 6px; margin: 0; cursor: pointer; font-size: 12.5px; color: var(--text); }
    .tgl input { width: auto; }
    .empty { justify-content: center; color: var(--faint); }
    .editor { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 18px; padding: 16px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface-2); margin-bottom: 12px; }
    .editor .wide { grid-column: 1 / -1; }
    @media (max-width: 760px) { .editor { grid-template-columns: 1fr; } }
    .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
    .seg button { border: 0; border-radius: 0; background: var(--surface); font-size: 12.5px; padding: 6px 12px; }
    .seg button + button { border-inline-start: 1px solid var(--border); }
    .seg button.on.block { background: var(--red-soft); color: var(--red); }
    .seg button.on.warn { background: var(--amber-soft); color: var(--amber); }
    .seg button.on.guide { background: var(--accent-soft); color: var(--accent); }
    .hint { margin-top: 6px; }
    .row { display: flex; gap: 10px; align-items: center; }
    .spacer { flex: 1; }
    .drop { display: flex; align-items: center; justify-content: center; border: 1.5px dashed var(--border); border-radius: 10px; padding: 18px; cursor: pointer; color: var(--muted); margin: 0 0 12px; }
    .drop:hover { border-color: var(--accent); color: var(--text); }
    .drop input { display: none; }
    .drop.busy { cursor: progress; }
    .scroll { overflow-x: auto; }
    table th { padding: 10px 14px; }
    table td { padding: 10px 14px; vertical-align: middle; }
    .toast { position: fixed; right: 24px; bottom: 24px; z-index: 1003; margin: 0; padding: 10px 16px; border-radius: 10px;
      background: var(--green-soft); color: var(--green); border: 1px solid color-mix(in srgb, var(--green) 35%, transparent);
      box-shadow: var(--shadow); font-size: 13px; font-weight: 500; }
  `,
})
export class AdminPolicyPage {
  private api = inject(Api);
  protected rules = signal<PolicyRule[]>([]);
  protected categories = signal<Record<string, string>>({});
  protected actions = signal<Record<string, string>>({});
  protected documents = signal<PolicyDocumentInfo[]>([]);
  protected violations = signal<PolicyViolation[]>([]);
  protected error = signal('');
  protected saved = signal('');
  protected busy = signal(false);
  protected uploading = signal(false);
  protected editing = signal<string | null>(null);
  protected draft = signal<PolicyRuleIn>({ ...EMPTY });
  protected keywordsText = signal('');
  protected testText = signal('');
  protected testing = signal(false);
  protected testResult = signal<PolicyTestResult | null>(null);

  protected keywordsPlaceholder = 'Ex. : confidentiel, Atlas Assurances, /\\bIBAN\\s*FR\\d{2}/';
  protected actionList: { id: PolicyAction; label: string }[] = [
    { id: 'block', label: 'Bloquer' }, { id: 'warn', label: 'Avertir' }, { id: 'guide', label: 'Guider' },
  ];
  protected proposed = computed(() => this.rules().filter(r => r.proposed));
  protected current = computed(() => this.rules().filter(r => !r.proposed));
  protected inForce = computed(() => this.current().filter(r => r.active));
  protected blockedCount = computed(() => this.violations().filter(v => v.decision === 'blocked').length);
  protected categoryList = computed(() => Object.entries(this.categories()));

  constructor() { this.load(); }

  protected count(action: PolicyAction) { return this.inForce().filter(r => r.action === action).length; }

  protected actionHint(a: PolicyAction) {
    return a === 'block' ? 'Le message est refusé, avec la règle en motif.'
      : a === 'warn' ? 'Le message passe ; la réponse commence par un avertissement.'
      : 'Aucun contrôle des messages : les agents appliquent la règle dans leurs réponses.';
  }

  private load() {
    this.api.policyRules().subscribe({
      next: r => { this.rules.set(r.rules); this.categories.set(r.categories); this.actions.set(r.actions); },
      error: e => this.fail(e),
    });
    this.api.policyDocuments().subscribe({ next: d => this.documents.set(d), error: () => {} });
    this.api.policyViolations().subscribe({ next: v => this.violations.set(v), error: () => {} });
  }

  protected startNew() { this.draft.set({ ...EMPTY }); this.keywordsText.set(''); this.editing.set('new'); }
  protected edit(r: PolicyRule) {
    this.draft.set({ title: r.title, text: r.text, category: r.category, action: r.action, keywords: r.keywords, active: r.active });
    this.keywordsText.set(r.keywords.join(', '));
    this.editing.set(r.id);
  }
  protected cancel() { this.editing.set(null); }
  protected patch<K extends keyof PolicyRuleIn>(field: K, value: PolicyRuleIn[K]) { this.draft.update(d => ({ ...d, [field]: value })); }

  protected save() {
    const body = { ...this.draft(), keywords: this.splitKeywords(this.keywordsText()) };
    const id = this.editing();
    this.busy.set(true);
    const call = id && id !== 'new' ? this.api.updatePolicyRule(id, body) : this.api.createPolicyRule(body);
    call.subscribe({
      next: () => { this.busy.set(false); this.editing.set(null); this.flash('Règle enregistrée — elle s\'applique à tous les agents.'); this.load(); },
      error: e => { this.busy.set(false); this.fail(e); },
    });
  }

  /** Commas separate keywords, except inside a /regex/ (which may contain {2,3}). */
  private splitKeywords(text: string): string[] {
    const out: string[] = [];
    let current = '', inRegex = false;
    for (const ch of text) {
      if (ch === '/' && current.trim() === '') inRegex = true;
      else if (ch === '/' && inRegex) inRegex = false;
      if (ch === ',' && !inRegex) { out.push(current.trim()); current = ''; } else current += ch;
    }
    out.push(current.trim());
    return out.filter(Boolean);
  }

  protected toggle(r: PolicyRule) {
    this.api.updatePolicyRule(r.id, { title: r.title, text: r.text, category: r.category, action: r.action, keywords: r.keywords, active: !r.active })
      .subscribe({ next: () => { this.flash(r.active ? 'Règle désactivée.' : 'Règle activée.'); this.load(); }, error: e => this.fail(e) });
  }

  protected accept(r: PolicyRule) {
    this.api.acceptPolicyRule(r.id).subscribe({ next: () => { this.flash('Règle validée et en vigueur.'); this.load(); }, error: e => this.fail(e) });
  }

  protected remove(r: PolicyRule) {
    if (!confirm(r.proposed ? `Écarter la règle proposée « ${r.title} » ?` : `Supprimer la règle « ${r.title} » ?`)) return;
    this.api.deletePolicyRule(r.id).subscribe({ next: () => { this.flash('Règle supprimée.'); this.load(); }, error: e => this.fail(e) });
  }

  protected upload(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    this.uploading.set(true);
    this.error.set('');
    this.api.uploadPolicyDocument(file).subscribe({
      next: r => {
        this.uploading.set(false);
        this.flash(r.proposed ? `${r.proposed} règle(s) proposée(s) depuis « ${r.filename} » : validez-les ci-dessus.` : `Aucune règle trouvée dans « ${r.filename} ».`);
        this.load();
      },
      error: e => { this.uploading.set(false); this.fail(e); },
    });
  }

  protected acceptAll(d: PolicyDocumentInfo) {
    this.api.acceptPolicyDocument(d.id).subscribe({ next: r => { this.flash(`${r.accepted} règle(s) mise(s) en vigueur.`); this.load(); }, error: e => this.fail(e) });
  }

  protected removeDocument(d: PolicyDocumentInfo) {
    if (!confirm(`Supprimer « ${d.filename} » ? Les règles déjà validées restent en vigueur ; celles à valider sont écartées.`)) return;
    this.api.deletePolicyDocument(d.id).subscribe({ next: () => { this.flash('Document supprimé.'); this.load(); }, error: e => this.fail(e) });
  }

  protected test() {
    this.testing.set(true);
    this.api.testPolicy(this.testText().trim()).subscribe({
      next: r => { this.testing.set(false); this.testResult.set(r); },
      error: e => { this.testing.set(false); this.fail(e); },
    });
  }

  private flash(msg: string) { this.error.set(''); this.saved.set(msg); setTimeout(() => this.saved.set(''), 3500); }
  private fail(e: any) {
    const d = e?.error?.detail;
    this.error.set(typeof d === 'string' ? d : Array.isArray(d) ? String(d[0]?.msg ?? 'Erreur').replace(/^Value error, /, '') : 'Action impossible.');
  }
}

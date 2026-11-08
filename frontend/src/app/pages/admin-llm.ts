import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';

import { Api, LlmConfigIn, LlmEntry, LlmSettings, LlmTestResult } from '../api';

import { SectionTabs } from '../section-tabs';
interface Draft { base_url: string; api_key: string; model: string; }

/** LLM endpoints & API keys (permission `governance.manage`): the org-wide default and per-agent
 *  overrides of endpoint / API key / model, without touching the server's .env. Keys are write-only:
 *  the API only ever returns a masked hint. Every change is audited. */
@Component({
  selector: 'app-admin-llm',
  imports: [SectionTabs, DatePipe],
  template: `
    <app-section-tabs group="supervision" />
    <div class="page llm">
      <div class="page-head">
        <div>
          <h1>Modèles & clés API</h1>
          <p>Choisissez le fournisseur, la clé API et le modèle utilisés par l'IA — pour toute l'organisation ou agent par agent — sans modifier le serveur.
            Un champ vide hérite de la configuration par défaut, puis du fichier .env du serveur. Les clés sont chiffrées et ne sont jamais réaffichées.</p>
        </div>      </div>

      @if (error()) { <div class="banner bad" role="alert">{{ error() }}</div> }

      @if (data(); as d) {
        @if (d.mode === 'offline' || d.mode === 'mock') {
          <div class="banner warn">Le serveur est en mode <strong>{{ d.mode }}</strong> (LLM_MODE) : aucune clé n'est utilisée tant que ce mode n'est pas changé en <code>auto</code> ou <code>api</code>.</div>
        }
        @if (d.encryption_weak) {
          <div class="banner warn">Aucun secret de chiffrement robuste n'est défini sur le serveur (<code>SECRETS_KEY</code> ou <code>AUTH_SECRET</code>). Les clés sont chiffrées avec une clé de développement : définissez-en une avant la production.</div>
        }

        <!-- Org-wide default -->
        <section class="card">
          <div class="card-head">
            <div><h2>Configuration par défaut</h2><p>Utilisée par tous les agents qui n'ont pas leur propre configuration.</p></div>
            <span class="pill" [class.accent]="d.default.effective.source !== 'env'">{{ sourceLabel(d.default.effective.source) }}</span>
          </div>
          <div class="form">
            <div>
              <label for="def-url">Point d'accès (compatible OpenAI)</label>
              <input id="def-url" type="url" [placeholder]="d.env.base_url" [value]="defDraft().base_url" (input)="patchDef('base_url', $any($event.target).value)" />
              <div class="small faint">Vide = {{ d.env.base_url }} (.env)</div>
            </div>
            <div>
              <label for="def-key">Clé API</label>
              <input id="def-key" type="password" autocomplete="new-password"
                     [placeholder]="d.default.has_key ? 'Enregistrée : ' + d.default.key_hint + ' — saisir pour remplacer' : (d.env.has_key ? 'Clé du serveur (.env) utilisée' : 'Aucune clé')"
                     [value]="defDraft().api_key" (input)="patchDef('api_key', $any($event.target).value)" />
              @if (d.default.key_unreadable) { <div class="small err">La clé enregistrée est illisible (secret de chiffrement changé) : ressaisissez-la.</div> }
            </div>
          </div>
          <div class="models small muted">Modèles par niveau (serveur) : raisonnement <code>{{ d.env.models.reasoning }}</code> · agents <code>{{ d.env.models.agent }}</code> · rapide <code>{{ d.env.models.fast }}</code></div>
          @if (tests()['default']; as t) { <div class="test" [class.ok]="t.ok" [class.ko]="!t.ok">{{ t.message }}</div> }
          <div class="actions">
            @if (d.default.updated_by) { <span class="small faint">Modifiée par {{ d.default.updated_by }} le {{ d.default.updated_at | date: 'dd/MM/yyyy HH:mm' }}</span> }
            <span class="spacer"></span>
            @if (d.default.has_key) { <button class="sm ghost" (click)="clearKey('default')">Supprimer la clé</button> }
            <button class="sm" (click)="test('default', defDraft())" [disabled]="testing() === 'default'">@if (testing() === 'default') { <span class="spin"></span> } Tester</button>
            <button class="sm primary" (click)="save('default', defDraft())" [disabled]="!defDirty()">Enregistrer</button>
          </div>
        </section>

        <!-- Per agent -->
        <section class="card">
          <div class="card-head">
            <div><h2>Par agent</h2><p>Donnez à un agent son propre fournisseur, sa propre clé ou un autre modèle (ex. un modèle rapide pour l'assistant d'appel).</p></div>
          </div>
          <div class="scroll">
          <table>
            <thead><tr><th>Agent</th><th>Point d'accès utilisé</th><th>Modèle</th><th>Clé</th><th></th></tr></thead>
            @for (grp of groups(); track grp.kind) {
            <tbody>
              <tr class="grp"><td colspan="5">{{ grp.label }}</td></tr>
              @for (a of grp.items; track a.key) {
                <tr [class.custom]="a.effective.source === 'agent'">
                  <td><strong>{{ a.label }}</strong><div class="small faint">{{ a.key }}</div></td>
                  <td class="url"><span class="small">{{ a.effective.base_url }}</span>
                    <div class="small faint">{{ sourceLabel(a.effective.source) }}</div></td>
                  <td class="small">{{ a.effective.model || 'selon le niveau' }}</td>
                  <td>
                    @if (a.effective.has_key) { <span class="pill green">{{ a.has_key ? a.key_hint : 'héritée' }}</span> }
                    @else { <span class="pill red">aucune</span> }
                  </td>
                  <td class="right"><button class="sm" (click)="toggleEdit(a)">{{ editing() === a.key ? 'Fermer' : 'Configurer' }}</button></td>
                </tr>
                @if (editing() === a.key) {
                  <tr class="editor"><td colspan="5">
                    <div class="form three">
                      <div>
                        <label [for]="'url-' + a.key">Point d'accès</label>
                        <input [id]="'url-' + a.key" type="url" [placeholder]="'Hériter : ' + d.default.effective.base_url" [value]="draft().base_url" (input)="patch('base_url', $any($event.target).value)" />
                      </div>
                      <div>
                        <label [for]="'key-' + a.key">Clé API</label>
                        <input [id]="'key-' + a.key" type="password" autocomplete="new-password"
                               [placeholder]="a.has_key ? 'Enregistrée : ' + a.key_hint : 'Hériter de la clé par défaut'"
                               [value]="draft().api_key" (input)="patch('api_key', $any($event.target).value)" />
                        @if (a.key_unreadable) { <div class="small err">Clé illisible : ressaisissez-la.</div> }
                      </div>
                      <div>
                        <label [for]="'model-' + a.key">Modèle</label>
                        <input [id]="'model-' + a.key" [placeholder]="'Selon le niveau (ex. ' + d.env.models.agent + ')'" [value]="draft().model" (input)="patch('model', $any($event.target).value)" />
                      </div>
                    </div>
                    <p class="small faint hint">Si vous changez le point d'accès, saisissez aussi une clé : la clé par défaut n'est jamais envoyée à un autre fournisseur.</p>
                    @if (tests()[a.key]; as t) { <div class="test" [class.ok]="t.ok" [class.ko]="!t.ok">{{ t.message }}</div> }
                    <div class="actions">
                      @if (a.updated_by) { <span class="small faint">Modifiée par {{ a.updated_by }} le {{ a.updated_at | date: 'dd/MM/yyyy HH:mm' }}</span> }
                      <span class="spacer"></span>
                      @if (a.effective.source === 'agent') { <button class="sm ghost" (click)="reset(a.key)">Revenir à la configuration par défaut</button> }
                      @if (a.has_key) { <button class="sm ghost" (click)="clearKey(a.key)">Supprimer la clé</button> }
                      <button class="sm" (click)="test(a.key, draft())" [disabled]="testing() === a.key">@if (testing() === a.key) { <span class="spin"></span> } Tester</button>
                      <button class="sm primary" (click)="save(a.key, draft())" [disabled]="!dirty(a)">Enregistrer</button>
                    </div>
                  </td></tr>
                }
              }
            </tbody>
            }
          </table>
          </div>
        </section>
        @if (saved()) { <p class="toast" role="status">{{ saved() }}</p> }
      } @else if (!error()) {
        <p class="muted"><span class="spin"></span> Chargement…</p>
      }
    </div>
  `,
  styles: `
    .llm { display: flex; flex-direction: column; gap: 24px; }
    .llm .page-head { margin-bottom: 0; align-items: flex-start; }
    .llm .page-head p { max-width: 820px; line-height: 1.55; }
    .card { padding: 20px 22px; }
    .card-head { margin-bottom: 18px; align-items: flex-start; }
    .card-head p { margin-top: 4px; }
    .banner { border-radius: 10px; padding: 12px 16px; font-size: 13px; line-height: 1.5; }
    .banner.warn { background: var(--amber-soft); color: var(--amber); }
    .banner.bad { background: var(--red-soft); color: var(--red); }
    .form { display: grid; grid-template-columns: 1fr 1fr; gap: 16px 20px; }
    .form.three { grid-template-columns: 1.3fr 1fr 1fr; }
    @media (max-width: 900px) { .form, .form.three { grid-template-columns: 1fr; } }
    .form .small { margin-top: 6px; }
    .models { margin-top: 16px; line-height: 1.8; }
    .actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 18px; }
    .spacer { flex: 1; }
    .test { margin-top: 14px; padding: 9px 12px; border-radius: 8px; font-size: 13px; }
    .test.ok { background: var(--green-soft); color: var(--green); }
    .test.ko { background: var(--red-soft); color: var(--red); }
    .hint { margin-top: 12px; }
    .scroll { overflow-x: auto; }
    table th { padding: 10px 14px; }
    table td { padding: 12px 14px; vertical-align: middle; }
    tr.grp td { background: var(--surface-2); font-weight: 600; font-size: 12.5px; padding: 10px 14px; }
    tbody + tbody tr.grp td { border-top: 12px solid var(--surface); }
    tr.custom td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
    td.url { max-width: 320px; word-break: break-all; }
    td.right { text-align: right; }
    tr.editor > td { background: var(--surface-2); padding: 18px 20px; }
    .err { color: var(--red); }
    .toast { position: fixed; right: 24px; bottom: 24px; z-index: 1003; margin: 0; padding: 10px 16px; border-radius: 10px;
      background: var(--green-soft); color: var(--green); border: 1px solid color-mix(in srgb, var(--green) 35%, transparent);
      box-shadow: var(--shadow); font-size: 13px; font-weight: 500; }
  `,
})
export class AdminLlmPage {
  private api = inject(Api);
  protected data = signal<LlmSettings | null>(null);
  protected error = signal('');
  protected saved = signal('');
  protected editing = signal<string | null>(null);
  protected draft = signal<Draft>({ base_url: '', api_key: '', model: '' });
  protected defDraft = signal<Draft>({ base_url: '', api_key: '', model: '' });
  protected testing = signal<string | null>(null);
  protected tests = signal<Record<string, LlmTestResult>>({});

  protected groups = computed(() => {
    const agents = this.data()?.agents ?? [];
    return [
      { kind: 'agent', label: 'Agents', items: agents.filter(a => a.kind === 'agent') },
      { kind: 'feature', label: 'Fonctionnalités IA', items: agents.filter(a => a.kind !== 'agent') },
    ].filter(g => g.items.length);
  });
  protected defDirty = computed(() => {
    const d = this.data()?.default;
    return !!d && (this.defDraft().api_key.trim() !== '' || this.defDraft().base_url.trim() !== d.base_url);
  });

  constructor() { this.load(); }

  private load() {
    this.api.llmSettings().subscribe({
      next: d => {
        this.data.set(d);
        this.defDraft.set({ base_url: d.default.base_url, api_key: '', model: '' });
        this.error.set('');
      },
      error: e => this.error.set(e?.status === 403 ? 'Accès réservé aux administrateurs de la gouvernance IA.' : (e?.error?.detail ?? 'Chargement impossible.')),
    });
  }

  protected sourceLabel(source: string) {
    return source === 'agent' ? 'Configuration propre à l\'agent' : source === 'default' ? 'Configuration par défaut (interface)' : 'Fichier .env du serveur';
  }

  protected patch(field: keyof Draft, value: string) { this.draft.update(d => ({ ...d, [field]: value })); }
  protected patchDef(field: keyof Draft, value: string) { this.defDraft.update(d => ({ ...d, [field]: value })); }

  protected dirty(a: LlmEntry) {
    const d = this.draft();
    return d.api_key.trim() !== '' || d.base_url.trim() !== a.base_url || d.model.trim() !== a.model;
  }

  protected toggleEdit(a: LlmEntry) {
    if (this.editing() === a.key) { this.editing.set(null); return; }
    this.draft.set({ base_url: a.base_url, api_key: '', model: a.model });
    this.editing.set(a.key);
  }

  private body(d: Draft, key: string): LlmConfigIn {
    const body: LlmConfigIn = { base_url: d.base_url.trim() };
    if (d.api_key.trim()) body.api_key = d.api_key.trim();
    if (key !== 'default') body.model = d.model.trim();
    return body;
  }

  protected save(key: string, d: Draft) {
    this.api.saveLlmConfig(key, this.body(d, key)).subscribe({
      next: () => { this.flash('Configuration enregistrée.'); this.clearTest(key); this.reloadKeepingEditor(key); },
      error: e => this.error.set(this.detail(e)),
    });
  }

  protected clearKey(key: string) {
    if (!confirm('Supprimer la clé API enregistrée ? La configuration héritera de la clé par défaut (ou du serveur).')) return;
    this.api.saveLlmConfig(key, { clear_key: true }).subscribe({
      next: () => { this.flash('Clé supprimée.'); this.reloadKeepingEditor(key); },
      error: e => this.error.set(this.detail(e)),
    });
  }

  protected reset(key: string) {
    if (!confirm('Supprimer la configuration propre à cet agent ? Il utilisera la configuration par défaut.')) return;
    this.api.resetLlmConfig(key).subscribe({
      next: () => { this.flash('Agent revenu à la configuration par défaut.'); this.editing.set(null); this.load(); },
      error: e => this.error.set(this.detail(e)),
    });
  }

  protected test(key: string, d: Draft) {
    this.testing.set(key);
    const body: LlmConfigIn = {};
    if (d.base_url.trim()) body.base_url = d.base_url.trim();
    if (d.api_key.trim()) body.api_key = d.api_key.trim();
    if (key !== 'default' && d.model.trim()) body.model = d.model.trim();
    this.api.testLlmConfig(key, body).subscribe({
      next: r => { this.testing.set(null); this.tests.update(t => ({ ...t, [key]: r })); },
      error: e => { this.testing.set(null); this.tests.update(t => ({ ...t, [key]: { ok: false, message: this.detail(e), base_url: '' } })); },
    });
  }

  private reloadKeepingEditor(key: string) {
    this.api.llmSettings().subscribe({
      next: d => {
        this.data.set(d);
        this.defDraft.set({ base_url: d.default.base_url, api_key: '', model: '' });
        const a = d.agents.find(x => x.key === key);
        if (a && this.editing() === key) this.draft.set({ base_url: a.base_url, api_key: '', model: a.model });
      },
      error: e => this.error.set(this.detail(e)),
    });
  }

  private clearTest(key: string) { this.tests.update(t => { const { [key]: _, ...rest } = t; return rest; }); }

  private detail(e: any): string {
    const d = e?.error?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg).replace(/^Value error, /, '');
    return 'Action impossible.';
  }

  private flash(msg: string) { this.error.set(''); this.saved.set(msg); setTimeout(() => this.saved.set(''), 3000); }
}

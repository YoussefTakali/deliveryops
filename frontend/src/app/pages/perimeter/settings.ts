import { DatePipe, JsonPipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api, DeliveryProject, Session } from '../../api';

@Component({
  selector: 'app-settings',
  imports: [DatePipe, JsonPipe, RouterLink],
  template: `
    <div class="stack">
      @if (readiness(); as r) {
        <div class="card">
          <div class="card-head">
            <div><h2>Préparation du périmètre — {{ r.score }} %</h2><p>Données nécessaires aux agents. L'analyse d'onboarding peut être lancée dès que la documentation et les applications sont chargées.</p></div>
            <a class="btn" [class.primary]="r.ready_for_onboarding" [routerLink]="['/p', perimeterId(), 'knowledge']">Aller à l'onboarding</a>
          </div>
          <div class="progress"><span [style.width.%]="r.score"></span></div>
          <div class="grid g3 checklist">
            @for (i of r.items; track i.id) {
              <div class="check" [class.done]="i.done">
                <span class="tick">{{ i.done ? '✓' : '○' }}</span>
                <div><div>{{ i.label }}</div><div class="small faint">{{ i.detail }}</div></div>
              </div>
            }
          </div>
        </div>
      }

      @if (canEdit() && perim()) {
        <div class="card">
          <div class="card-head"><div><h2>Projet</h2><p>Rattaché à un projet, le périmètre partage son équipe, ses livrables et son plan de charge.</p></div></div>
          <div class="row">
            <select [value]="projectId()" (change)="projectId.set($any($event.target).value)" style="width: 340px" aria-label="Projet">
              <option value="" [selected]="projectId() === ''">— Sans projet —</option>
              @for (pr of clientProjects(); track pr.id) { <option [value]="pr.id" [selected]="pr.id === projectId()">{{ pr.name }}</option> }
            </select>
            <button class="primary sm" (click)="attach()" [disabled]="projectId() === (perim()?.project_id ?? '')">Rattacher</button>
            @if (attachMsg()) { <span class="small muted">{{ attachMsg() }}</span> }
          </div>
        </div>
      }

      @if (canEdit()) {
        <div class="card">
          <div class="card-head"><div><h2>Import de données</h2><p>CSV (séparateur ; ou ,) ou Excel. Les lignes existantes sont mises à jour (clé : nom, référence ticket…). Importer dans l'ordre : applications, dépendances, équipe, tickets, temps, backlog.</p></div></div>
          <table>
            <tr><th>Jeu de données</th><th>Colonnes obligatoires</th><th></th><th>Fichier</th></tr>
            @for (d of datasets(); track d.id) {
              <tr>
                <td><strong>{{ d.label }}</strong></td>
                <td class="small mono muted">{{ d.required.join(', ') }}</td>
                <td><button class="sm ghost" (click)="template(d.id)">Modèle CSV</button></td>
                <td>
                  <input type="file" accept=".csv,.xlsx" (change)="importFile(d.id, $any($event.target))" [disabled]="importing() === d.id" />
                  @if (reports()[d.id]; as rep) {
                    <div class="small" [style.color]="rep.error_count ? 'var(--amber)' : 'var(--green)'">
                      {{ rep.inserted }} ajouté(s), {{ rep.updated }} mis à jour, {{ rep.error_count }} erreur(s)
                    </div>
                    @for (e of rep.errors.slice(0, 5); track $index) { <div class="small faint">Ligne {{ e.row }} : {{ e.message }}</div> }
                  }
                  @if (importErrors()[d.id]) { <div class="small" style="color: var(--red)">{{ importErrors()[d.id] }}</div> }
                </td>
              </tr>
            }
          </table>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Connecteurs</h2><p>Synchronisation automatique des tickets à chaque cycle de veille. Les secrets ne sont jamais stockés : indiquez le nom de la variable d'environnement qui contient le jeton.</p></div></div>
          <table>
            @for (c of connectors(); track c.id) {
              <tr>
                <td><strong>{{ c.name }}</strong><div class="small faint">{{ kinds()[c.kind]?.label ?? c.kind }}</div></td>
                <td class="small">
                  <span class="pill" [class.green]="c.last_status === 'ok'" [class.red]="c.last_status === 'error'" [class.amber]="c.last_status === 'partial'">{{ c.last_status }}</span>
                  @if (c.last_sync_at) { <span class="faint"> {{ c.last_sync_at | date: 'dd/MM HH:mm' }}</span> }
                  <div class="faint">{{ c.last_message }}</div>
                  @if (connMsg()[c.id]) { <div>{{ connMsg()[c.id] }}</div> }
                </td>
                <td class="num">
                  <button class="sm" (click)="test(c.id)">Tester</button>
                  <button class="sm primary" (click)="sync(c.id)">Synchroniser</button>
                  <button class="sm ghost" (click)="removeConnector(c.id)">Supprimer</button>
                </td>
              </tr>
            } @empty { <tr><td class="empty">Aucun connecteur.</td></tr> }
          </table>
          <form class="newconn" (submit)="$event.preventDefault(); addConnector()">
            <div class="row">
              <select [value]="newKind()" (change)="newKind.set($any($event.target).value); newConfig.set({})" style="width: 200px">
                @for (k of kindList(); track k[0]) { <option [value]="k[0]">{{ k[1].label }}</option> }
              </select>
              <input placeholder="Nom" [value]="newName()" (input)="newName.set($any($event.target).value)" style="width: 220px" />
            </div>
            <div class="grid g3 fields">
              @for (f of kinds()[newKind()]?.fields ?? []; track f) {
                <div><label [for]="'f-' + f">{{ f }}</label><input [id]="'f-' + f" [value]="newConfig()[f] ?? ''" (input)="setField(f, $any($event.target).value)" /></div>
              }
            </div>
            <button class="primary">Ajouter le connecteur</button>
            @if (connError()) { <span class="small" style="color: var(--red)"> {{ connError() }}</span> }
          </form>
        </div>
      }

      <div class="card">
        <div class="card-head"><div><h2>Règles contractuelles</h2><p>Utilisées par les moteurs de facturation, de SLA et de détection hors périmètre. Extraites du contrat par l'agent puis validées, ou saisies ici.</p></div>
          <a class="btn" [routerLink]="['/p', perimeterId(), 'knowledge']">Extraire depuis le contrat</a></div>
        @if (editing()) {
          <textarea rows="18" class="mono" [value]="contractText()" (input)="contractText.set($any($event.target).value)"></textarea>
          <div class="row" style="margin-top: 8px">
            <button class="primary" (click)="saveContract()">Enregistrer</button>
            <button class="ghost" (click)="editing.set(false)">Annuler</button>
            @if (contractError()) { <span class="small" style="color: var(--red)">{{ contractError() }}</span> }
          </div>
        } @else {
          <pre class="contract mono">{{ contract() | json }}</pre>
          @if (canEdit()) { <button (click)="edit()">Modifier</button> }
        }
      </div>
    </div>
  `,
  styles: `
    .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .progress { height: 8px; background: var(--grey-soft); border-radius: 4px; overflow: hidden; margin-bottom: 14px; }
    .progress span { display: block; height: 100%; background: var(--accent); }
    .checklist { gap: 8px; }
    .check { display: flex; gap: 10px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; }
    .check.done { border-color: color-mix(in srgb, var(--green) 40%, var(--border)); }
    .tick { font-weight: 700; color: var(--faint); width: 16px; }
    .check.done .tick { color: var(--green); }
    td input[type=file] { font-size: 12px; padding: 4px; }
    .newconn { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
    .fields { width: 100%; gap: 10px; }
    .contract { background: var(--surface-2); border-radius: 8px; padding: 12px; max-height: 360px; overflow: auto; margin: 0 0 10px; white-space: pre-wrap; }
  `,
})
export class SettingsTab {
  private api = inject(Api);
  private session = inject(Session);
  perimeterId = input.required<string>();

  protected readiness = signal<any>(null);
  protected datasets = signal<any[]>([]);
  protected reports = signal<Record<string, any>>({});
  protected importErrors = signal<Record<string, string>>({});
  protected importing = signal('');
  protected connectors = signal<any[]>([]);
  protected kinds = signal<Record<string, { label: string; fields: string[] }>>({});
  protected kindList = computed(() => Object.entries(this.kinds()));
  protected newKind = signal('simulated');
  protected newName = signal('');
  protected newConfig = signal<Partial<Record<string, string>>>({});
  protected connMsg = signal<Record<string, string>>({});
  protected connError = signal('');
  protected contract = signal<any>({});
  protected contractText = signal('');
  protected editing = signal(false);
  protected contractError = signal('');
  protected canEdit = computed(() => !!this.session.me()?.can_decide);
  protected perim = signal<any>(null);
  protected projects = signal<DeliveryProject[]>([]);
  protected projectId = signal('');
  protected attachMsg = signal('');
  /** Only the périmètre's own client's active projects can host it. */
  protected clientProjects = computed(() => this.projects().filter(pr => pr.client_id === this.perim()?.client_id && pr.status === 'active'));

  constructor() {
    this.api.datasets().subscribe(d => this.datasets.set(d));
    this.api.connectorKinds().subscribe(k => this.kinds.set(k));
    effect(() => {
      const id = this.perimeterId();
      this.loadReadiness();
      this.api.perimeter(id).subscribe(p => { this.contract.set(p.contract); this.perim.set(p); this.projectId.set(p.project_id ?? ''); });
      if (this.canEdit()) { this.loadConnectors(); this.api.projects().subscribe({ next: pr => this.projects.set(pr), error: () => this.projects.set([]) }); }
    });
  }

  attach() {
    this.api.attachPerimeter(this.perimeterId(), this.projectId() || null).subscribe({
      next: p => { this.perim.set(p); this.session.perimetersVersion.update(v => v + 1); this.attachMsg.set(p.project_id ? 'Périmètre rattaché au projet.' : 'Périmètre détaché du projet.'); },
      error: e => this.attachMsg.set(e?.error?.detail ?? 'Rattachement impossible.'),
    });
  }
  loadReadiness() { this.api.readiness(this.perimeterId()).subscribe(r => this.readiness.set(r)); }
  loadConnectors() { this.api.connectors(this.perimeterId()).subscribe(c => this.connectors.set(c)); }
  template(dataset: string) { this.api.download(`/api/onboarding/templates/${dataset}`, `${dataset}.csv`); }

  importFile(dataset: string, el: HTMLInputElement) {
    const file = el.files?.[0];
    if (!file) return;
    this.importing.set(dataset);
    this.importErrors.update(e => ({ ...e, [dataset]: '' }));
    this.api.importDataset(this.perimeterId(), dataset, file).subscribe({
      next: rep => { this.importing.set(''); el.value = ''; this.reports.update(r => ({ ...r, [dataset]: rep })); this.loadReadiness(); },
      error: e => { this.importing.set(''); el.value = ''; this.importErrors.update(x => ({ ...x, [dataset]: e.error?.detail ?? 'Échec de l’import' })); },
    });
  }

  setField(field: string, value: string) { this.newConfig.update(c => ({ ...c, [field]: value })); }

  addConnector() {
    this.connError.set('');
    const config: Record<string, any> = Object.fromEntries(Object.entries(this.newConfig()).filter(([, v]) => v !== ''));
    if (config['batch_size']) config['batch_size'] = Number(config['batch_size']);
    this.api.createConnector(this.perimeterId(), { kind: this.newKind(), name: this.newName(), config }).subscribe({
      next: () => { this.newName.set(''); this.newConfig.set({}); this.loadConnectors(); },
      error: e => this.connError.set(e.error?.detail ?? 'Erreur'),
    });
  }

  test(id: string) { this.api.testConnector(id).subscribe(r => this.connMsg.update(m => ({ ...m, [id]: (r.ok ? '✓ ' : '✗ ') + r.message }))); }
  sync(id: string) {
    this.connMsg.update(m => ({ ...m, [id]: 'Synchronisation…' }));
    this.api.syncConnector(id).subscribe(r => { this.connMsg.update(m => ({ ...m, [id]: r.message })); this.loadConnectors(); this.loadReadiness(); });
  }
  removeConnector(id: string) { this.api.deleteConnector(id).subscribe(() => this.loadConnectors()); }

  edit() { this.contractText.set(JSON.stringify(this.contract(), null, 2)); this.editing.set(true); }
  saveContract() {
    let parsed: any;
    try { parsed = JSON.parse(this.contractText()); } catch { this.contractError.set('JSON invalide'); return; }
    this.api.updateContract(this.perimeterId(), parsed).subscribe({
      next: c => { this.contract.set(c); this.editing.set(false); this.contractError.set(''); this.loadReadiness(); },
      error: e => this.contractError.set(e.error?.detail ?? 'Erreur'),
    });
  }
}

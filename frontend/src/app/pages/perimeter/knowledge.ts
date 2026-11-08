import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { Router } from '@angular/router';

import { Api } from '../../api';
import { launch } from './shell';

interface Node { id: string; name: string; technology: string; criticality: string; incidents: number; dependents: number; risk_score: number; single_point_of_failure: boolean; x: number; y: number; }

@Component({
  selector: 'app-knowledge',
  imports: [DatePipe],
  template: `
    <div class="stack">
      <div class="card onboarding">
        <div>
          <h2>Onboarding accéléré</h2>
          <p class="small muted">Le Knowledge Transfer Agent analyse dossiers de transfert, DAT, DEX et contrats, cartographie les dépendances, identifie les applications critiques et les risques, puis propose un plan d'onboarding et une synthèse exécutive.</p>
        </div>
        <div class="row">
          <button (click)="exportDoc()">Dossier d'onboarding (Word)</button>
          <button class="primary" [disabled]="!!busy()" (click)="onboard()">@if (busy() === 'onboarding') { <span class="spin"></span> } Lancer l'analyse d'onboarding</button>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div><h2>Cartographie applicative</h2><p>Consommateurs à gauche, fournisseurs à droite. Taille = score de risque (criticité, dépendants, incidents).</p></div>
          <div class="row small muted legend"><span class="dot red"></span> critique <span class="dot amber"></span> haute <span class="dot"></span> moyenne/basse <span class="spof">SPOF</span> point de défaillance unique</div></div>
        @if (map(); as m) {
          <svg [attr.viewBox]="'0 0 ' + width() + ' ' + height()" class="map" role="img" aria-label="Cartographie des dépendances applicatives">
            <defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arrowhead" /></marker></defs>
            @for (e of edges(); track $index) {
              <path [attr.d]="e.d" class="edge" marker-end="url(#arrow)" [class.hl]="hover() === e.source || hover() === e.target" />
              <text [attr.x]="e.mx" [attr.y]="e.my - 4" class="elabel" text-anchor="middle">{{ e.kind }}</text>
            }
            @for (n of nodes(); track n.id) {
              <g [attr.transform]="'translate(' + n.x + ',' + n.y + ')'" (mouseenter)="hover.set(n.id)" (mouseleave)="hover.set('')" class="node" [class]="'crit-' + n.criticality">
                <rect x="-78" y="-22" width="156" height="44" rx="8" [class.spofbox]="n.single_point_of_failure" />
                <text y="-4" text-anchor="middle" class="nname">{{ n.name }}</text>
                <text y="12" text-anchor="middle" class="nmeta">risque {{ n.risk_score }} · {{ n.incidents }} inc. · {{ n.dependents }} dép.</text>
                <title>{{ n.name }} — {{ n.technology }}</title>
              </g>
            }
          </svg>
          @if (m.cycles.length) { <p class="small" style="color: var(--amber)">Dépendances circulaires : @for (c of m.cycles; track $index) { {{ c.join(' → ') }} } </p> }
          <table class="apps">
            <tr><th>Application</th><th>Technologie</th><th>Criticité</th><th class="num">Incidents</th><th class="num">Dépendants</th><th class="num">Score risque</th></tr>
            @for (n of m.nodes; track n.id) {
              <tr><td>{{ n.name }} @if (n.single_point_of_failure) { <span class="pill red">SPOF</span> }</td><td class="muted">{{ n.technology }}</td>
                <td><span class="pill" [class.red]="n.criticality === 'critical'" [class.amber]="n.criticality === 'high'">{{ n.criticality }}</span></td>
                <td class="num">{{ n.incidents }}</td><td class="num">{{ n.dependents }}</td>
                <td class="num"><span class="bar"><span [style.width.%]="n.risk_score"></span></span> {{ n.risk_score }}</td></tr>
            }
          </table>
        }
      </div>

      <div class="grid g2">
        <div class="card">
          <div class="card-head"><div><h2>Base documentaire</h2><p>Documents indexés pour la recherche et les agents</p></div></div>
          <div class="row upload">
            <select [value]="docType()" (change)="docType.set($any($event.target).value)" style="width: 190px">
              @for (t of docTypes; track t.id) { <option [value]="t.id">{{ t.label }}</option> }
            </select>
            <input type="file" accept=".pdf,.docx,.xlsx,.md,.txt" (change)="upload($any($event.target))" />
          </div>
          @if (uploadMsg()) { <p class="small muted">{{ uploadMsg() }}</p> }
          <table>
            @for (d of documents(); track d.id) {
              <tr><td>{{ d.filename }}</td><td><span class="pill">{{ typeLabel(d.doc_type) }}</span></td><td class="small faint mono">{{ d.uploaded_at | date: 'dd/MM/yy' }}</td></tr>
            }
          </table>
          <div class="row contract">
            <span class="small muted">Contrat : extraire les règles de facturation et de SLA, à valider avant application.</span>
            <button class="sm" (click)="extract()">Extraire les règles</button>
          </div>
          @if (contractMsg()) { <p class="small muted">{{ contractMsg() }}</p> }
        </div>
        <div class="card">
          <div class="card-head"><div><h2>Recherche dans la connaissance</h2><p>Recherche hybride avec citation des sources</p></div></div>
          <form class="row" (submit)="$event.preventDefault(); search()">
            <input [value]="query()" (input)="query.set($any($event.target).value)" placeholder="Ex. procédure de reprise du batch" style="flex: 1" />
            <button class="primary" [disabled]="searching()">{{ searching() ? 'Recherche…' : 'Rechercher' }}</button>
          </form>
          @if (searchMsg()) { <p class="small muted">{{ searchMsg() }}</p> }
          <div class="stack hits">
            @for (h of hits(); track h.chunk_id) {
              <div class="hit"><div class="small"><strong>{{ h.filename }}</strong> <span class="faint">§ {{ h.section || 'intro' }}</span> <span class="pill">{{ (h.score * 100).toFixed(0) }}</span></div>
                <p class="small muted pre">{{ h.text }}</p></div>
            }
          </div>
        </div>
      </div>
    </div>
  `,
  styles: `
    .onboarding { display: flex; gap: 16px; align-items: center; justify-content: space-between; flex-wrap: wrap; }
    .onboarding > div { max-width: 820px; }
    .legend { gap: 6px; }
    .spof { border: 2px dashed var(--red); border-radius: 4px; padding: 0 4px; margin-left: 8px; }
    .map { width: 100%; height: auto; margin: 4px 0 12px; }
    .edge { fill: none; stroke: var(--faint); stroke-width: 1.4; opacity: .7; }
    .edge.hl { stroke: var(--accent); stroke-width: 2.2; opacity: 1; }
    .arrowhead { fill: var(--faint); }
    .elabel { font-size: 9px; fill: var(--faint); }
    .node rect { fill: var(--surface); stroke: var(--border); stroke-width: 1.5; }
    .node.crit-critical rect { stroke: var(--red); fill: var(--red-soft); }
    .node.crit-high rect { stroke: var(--amber); fill: var(--amber-soft); }
    .node rect.spofbox { stroke-dasharray: 5 3; stroke-width: 2.2; }
    .nname { font-size: 12px; font-weight: 600; fill: var(--text); }
    .nmeta { font-size: 9.5px; fill: var(--muted); }
    .node { cursor: default; }
    .apps { margin-top: 6px; }
    .bar { display: inline-block; width: 60px; height: 6px; background: var(--grey-soft); border-radius: 3px; vertical-align: middle; margin-right: 6px; overflow: hidden; }
    .bar span { display: block; height: 100%; background: var(--red); }
    .upload { margin-bottom: 10px; } .upload input { flex: 1; }
    .contract { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; }
    .hits { margin-top: 12px; gap: 10px; max-height: 480px; overflow-y: auto; }
    .hit { border-left: 3px solid var(--accent-soft); padding-left: 10px; }
  `,
})
export class KnowledgeTab {
  private api = inject(Api);
  private router = inject(Router);
  perimeterId = input.required<string>();

  protected map = signal<any>(null);
  protected documents = signal<any[]>([]);
  protected hits = signal<any[]>([]);
  protected query = signal('');
  protected searching = signal(false);
  protected searchMsg = signal('');
  protected docType = signal('transfer');
  protected uploadMsg = signal('');
  protected contractMsg = signal('');
  protected busy = signal('');
  protected hover = signal('');
  protected docTypes = [
    { id: 'transfer', label: 'Dossier de transfert' }, { id: 'dat', label: 'DAT' }, { id: 'dex', label: 'DEX' }, { id: 'contract', label: 'Contrat' },
    { id: 'architecture', label: 'Architecture' }, { id: 'minutes', label: 'Compte-rendu' },
  ];

  private readonly colW = 240;
  private readonly rowH = 78;

  protected nodes = computed<Node[]>(() => {
    const m = this.map();
    if (!m) return [];
    const out = new Map<string, string[]>();
    m.edges.forEach((e: any) => out.set(e.source, [...(out.get(e.source) ?? []), e.target]));
    const level = new Map<string, number>();
    const depth = (id: string, seen: Set<string>): number => {
      if (level.has(id)) return level.get(id)!;
      if (seen.has(id)) return 0;
      seen.add(id);
      const targets = out.get(id) ?? [];
      const d = targets.length ? 1 + Math.max(...targets.map(t => depth(t, seen))) : 0;
      level.set(id, d);
      return d;
    };
    m.nodes.forEach((n: any) => depth(n.id, new Set()));
    const maxLevel = Math.max(0, ...level.values());
    const columns = new Map<number, any[]>();
    m.nodes.forEach((n: any) => { const col = maxLevel - (level.get(n.id) ?? 0); columns.set(col, [...(columns.get(col) ?? []), n]); });
    const tallest = Math.max(...[...columns.values()].map(c => c.length));
    const placed: Node[] = [];
    columns.forEach((list, col) => {
      const offset = ((tallest - list.length) * this.rowH) / 2;
      list.forEach((n, i) => placed.push({ ...n, x: 95 + col * this.colW, y: 40 + offset + i * this.rowH }));
    });
    return placed;
  });
  protected width = computed(() => 190 + Math.max(0, ...this.nodes().map(n => n.x)));
  protected height = computed(() => 40 + Math.max(0, ...this.nodes().map(n => n.y)));
  protected edges = computed(() => {
    const pos = new Map(this.nodes().map(n => [n.id, n]));
    return (this.map()?.edges ?? []).map((e: any) => {
      const a = pos.get(e.source)!, b = pos.get(e.target)!;
      const forward = b.x > a.x;
      const x1 = a.x + (forward ? 78 : -78), x2 = b.x + (forward ? -78 : 78);
      const dx = (x2 - x1) / 2;
      return { ...e, d: `M${x1},${a.y} C${x1 + dx},${a.y} ${x2 - dx},${b.y} ${x2},${b.y}`, mx: (x1 + x2) / 2, my: (a.y + b.y) / 2 };
    });
  });

  constructor() {
    effect(() => {
      const id = this.perimeterId();
      this.api.cartography(id).subscribe(m => this.map.set(m));
      this.loadDocs();
    });
  }

  loadDocs() { this.api.perimeter(this.perimeterId()).subscribe(p => this.documents.set(p.documents)); }
  typeLabel(id: string) { return this.docTypes.find(t => t.id === id)?.label ?? id; }
  search() {
    const q = this.query().trim();
    if (!q) return;
    this.searching.set(true);
    this.searchMsg.set('');
    this.api.search(this.perimeterId(), q).subscribe({
      next: h => { this.hits.set(h); this.searching.set(false); if (!h.length) this.searchMsg.set('Aucun résultat.'); },
      error: e => { this.searching.set(false); this.searchMsg.set(e?.status === 429 ? 'Trop de requêtes, réessayez.' : 'La recherche a échoué, réessayez.'); },
    });
  }
  exportDoc() { this.api.download(`/api/perimeters/${this.perimeterId()}/exports/onboarding.docx`, 'onboarding.docx'); }
  onboard() { launch(this.api, this.router, this.perimeterId(), 'onboarding', {}, this.busy); }

  upload(input: HTMLInputElement) {
    const file = input.files?.[0];
    if (!file) return;
    this.uploadMsg.set(`Indexation de ${file.name}…`);
    this.api.upload(this.perimeterId(), file, this.docType()).subscribe({
      next: () => { this.uploadMsg.set(`${file.name} indexé.`); input.value = ''; this.loadDocs(); },
      error: e => this.uploadMsg.set(e.error?.detail ?? 'Échec de l’import'),
    });
  }

  extract() {
    this.contractMsg.set('Extraction en cours…');
    this.api.extractContract(this.perimeterId()).subscribe({
      next: r => this.contractMsg.set(`Règles extraites : « ${r.title} » ajouté à la file de validation.`),
      error: e => this.contractMsg.set(e.error?.detail ?? 'Échec de l’extraction'),
    });
  }
}

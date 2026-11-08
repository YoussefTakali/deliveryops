import { DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Component, computed, inject, signal } from '@angular/core';

import { Api, PortfolioItem } from '../api';
import { Desktop } from '../desktop';

@Component({
  selector: 'app-local-files',
  imports: [DatePipe, RouterLink],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <a class="crumb" routerLink="/workspace">← Mon espace</a>
          <h1>Fichiers de votre PC</h1>
          <p>Parcourez les documents locaux de votre poste et versez-les directement dans la base de connaissance d'un périmètre. Lecture seule ; rien n'est envoyé ailleurs que vers votre propre serveur DeliveryOps.</p>
        </div>
        @if (desktop.available) { <a class="btn" routerLink="/workspace">Gérer les dossiers dans Mon espace</a> }
      </div>

      @if (!desktop.available) {
        <div class="empty card">
          <p>Cette page nécessite l'<strong>application bureau DeliveryOps</strong> — un navigateur ne peut pas accéder aux fichiers de votre PC.</p>
          <p class="small muted">Lancez l'app : <code>cd desktop &amp;&amp; npm start</code> (voir le README « Application bureau »).</p>
        </div>
      } @else {
        @if (overview(); as ov) {
          <div class="grid g3">
            @for (r of ov.roots; track r.root) {
              <div class="card root" [class.selected]="selectedRoot() === r.root" (click)="selectRoot(r.root)">
                <div class="card-head"><h3 class="path">{{ r.root }}</h3></div>
                @if (!r.exists) { <p class="small faint">Dossier introuvable.</p> }
                @else {
                  <div class="row stats">
                    <span class="pill">{{ r.files }}{{ r.truncated ? '+' : '' }} fichiers</span>
                    <span class="pill">{{ mb(r.total_size) }} Mo</span>
                    @for (k of kinds(r.by_kind); track k[0]) { <span class="pill" [class.accent]="k[0] === 'document'">{{ kindLabel[k[0]] ?? k[0] }} {{ k[1] }}</span> }
                  </div>
                  <div class="small faint exts">{{ (r.top_extensions || []).slice(0,6).map(e => e[0] + ' (' + e[1] + ')').join(' · ') }}</div>
                }
              </div>
            }
          </div>
        }

        @if (selectedRoot()) {
          <div class="card">
            <div class="card-head">
              <h2 class="path">{{ selectedRoot() }}</h2>
              <form class="row" (submit)="$event.preventDefault(); runScan()">
                <input placeholder="Filtrer par nom…" [value]="query()" (input)="query.set($any($event.target).value)" style="width: 220px" />
                <button class="primary">Rechercher</button>
              </form>
            </div>
            @if (loading()) { <div class="empty"><span class="spin"></span></div> }
            <table>
              <tr><th>Fichier</th><th>Type</th><th class="num">Taille</th><th>Modifié</th><th></th></tr>
              @for (f of files(); track f.path) {
                <tr>
                  <td><strong>{{ f.name }}</strong>
                    @if (f.matched_in === 'content') { <span class="pill accent" title="Le terme recherché a été trouvé à l'intérieur du fichier">trouvé dans le contenu</span> }
                    <div class="small faint path">{{ f.dir }}</div></td>
                  <td><span class="pill" [class.accent]="f.kind === 'document'">{{ kindLabel[f.kind] ?? f.ext }}</span></td>
                  <td class="num">{{ mb(f.size) }} Mo</td>
                  <td class="small faint">{{ f.modified | date: 'dd/MM/yy HH:mm' }}</td>
                  <td>
                    <div class="row">
                      @if (f.kind === 'text') { <button class="sm ghost" (click)="preview(f)">Aperçu</button> }
                      @if (f.kind === 'document' || f.kind === 'text') {
                        <select class="sm" (change)="ingest(f, $any($event.target).value); $any($event.target).value=''">
                          <option value="">Ajouter au périmètre…</option>
                          @for (p of perimeters(); track p.id) { <option [value]="p.id">{{ p.name }}</option> }
                        </select>
                      }
                    </div>
                  </td>
                </tr>
              } @empty { @if (!loading()) { <tr><td colspan="5" class="empty">Aucun fichier. Sélectionnez un dossier et lancez une recherche.</td></tr> } }
            </table>
            @if (message()) { <p class="small" [style.color]="messageErr() ? 'var(--red)' : 'var(--green)'">{{ message() }}</p> }
          </div>
        }

        @if (previewFile(); as pf) {
          <div class="card">
            <div class="card-head"><h2 class="path">{{ pf.path }}</h2><button class="sm ghost" (click)="previewFile.set(null)">✕</button></div>
            <pre class="preview mono">{{ pf.text }}</pre>
            @if (pf.truncated) { <p class="small faint">Aperçu tronqué.</p> }
          </div>
        }
      }
    </div>
  `,
  styles: `
    .crumb { display: inline-block; font-size: 12px; margin-bottom: 4px; color: var(--muted); }
    .crumb:hover { color: var(--accent); }
    .root { cursor: pointer; }
    .root.selected { border-color: var(--accent); }
    .path { word-break: break-all; font-size: 13px; }
    .stats { gap: 6px; margin-bottom: 6px; }
    .exts { font-family: var(--mono, monospace); }
    .preview { background: var(--surface-2); border-radius: 8px; padding: 12px; max-height: 460px; overflow: auto; white-space: pre-wrap; margin: 0; }
  `,
})
export class LocalFilesPage {
  protected desktop = inject(Desktop);
  private api = inject(Api);

  protected overview = signal<any>(null);
  protected selectedRoot = signal<string>('');
  protected query = signal('');
  protected files = signal<any[]>([]);
  protected loading = signal(false);
  protected previewFile = signal<any>(null);
  protected perimeters = signal<PortfolioItem[]>([]);
  protected message = signal('');
  protected messageErr = signal(false);
  protected kindLabel: Partial<Record<string, string>> = { document: 'Document', text: 'Texte', image: 'Image', other: 'Autre' };

  constructor() {
    if (this.desktop.available) {
      this.loadOverview();
      this.api.portfolio().subscribe(p => this.perimeters.set(p));
    }
  }

  private async loadOverview() {
    try { this.overview.set(await this.desktop.overview()); } catch (e: any) { this.setMessage(e.message, true); }
  }

  kinds(byKind: Record<string, number>) { return Object.entries(byKind ?? {}); }
  mb(bytes: number) { return (bytes / 1_048_576).toFixed(1); }

  selectRoot(root: string) { this.selectedRoot.set(root); this.files.set([]); this.runScan(); }

  async runScan() {
    if (!this.selectedRoot()) return;
    this.loading.set(true);
    try { const res = await this.desktop.scan(this.selectedRoot(), this.query(), 1500); this.files.set(res.items); }
    catch (e: any) { this.setMessage(e.message, true); }
    finally { this.loading.set(false); }
  }

  async preview(f: any) {
    try { this.previewFile.set(await this.desktop.readText(f.path)); } catch (e: any) { this.setMessage(e.message, true); }
  }

  async ingest(f: any, perimeterId: string) {
    if (!perimeterId) return;
    this.setMessage(`Import de ${f.name}…`, false);
    try {
      const file = await this.desktop.toFile(f.path, f.name);
      this.api.upload(perimeterId, file, 'local').subscribe({
        next: () => this.setMessage(`${f.name} ajouté à la base de connaissance.`, false),
        error: (e) => this.setMessage(e.error?.detail ?? 'Échec de l’import', true),
      });
    } catch (e: any) { this.setMessage(e.message, true); }
  }

  private setMessage(msg: string, err: boolean) { this.message.set(msg); this.messageErr.set(err); }
}

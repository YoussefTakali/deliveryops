import { Component, DestroyRef, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api, Source } from '../api';
import { Desktop } from '../desktop';
import { LocalIndexer } from '../local-indexer';
import { MicrosoftAuth } from '../microsoft-auth';
import { MySkills } from './my-skills';

const AUTOSYNC_KEY = 'dm.ms.autosync';
const AUTOSYNC_MS = 15 * 60 * 1000; // 15 min — polling, since Graph webhooks need a public HTTPS endpoint

function sleep(ms: number): Promise<void> { return new Promise(resolve => setTimeout(resolve, ms)); }

/** "Mon espace" — the personal workspace: what's indexed (Microsoft 365 + local files), search
 *  across it with sources, the controls to grant/revoke access and keep it in sync, and the user's
 *  competence-matrix line ("Mes compétences"). */
@Component({
  selector: 'app-workspace',
  imports: [RouterLink, MySkills],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Mon espace</h1>
          <p>Vos données Microsoft 365 et vos fichiers locaux, indexés pour être cherchés avec contexte. Rien ne quitte votre propre serveur DeliveryOps.</p>
        </div>
        @if (status(); as s) {
          <div class="stat-pill" [class.ready]="s.ready">
            <strong>{{ s.documents }}</strong> document(s) indexé(s)
            @if (s.chunks) { <span class="dim">· {{ s.chunks }} passages</span> }
          </div>
        }
      </div>

      <!-- Search -->
      <div class="card search-card">
        <form class="row" (submit)="$event.preventDefault(); ask()">
          <input placeholder="Chercher dans vos mails, fichiers, agenda, documents locaux…" [value]="question()" (input)="question.set($any($event.target).value)" />
          <button class="primary" [disabled]="!question().trim() || asking()">@if (asking()) { <span class="spin"></span> } Chercher</button>
        </form>
        @if (answer(); as a) {
          <div class="answer">
            <p class="pre">{{ a.answer }}</p>
            @if (a.sources.length) {
              <div class="sources">
                @for (s of a.sources; track s.ref) {
                  <div class="src-chip" [title]="s.excerpt"><span class="pill">{{ s.type }}</span> {{ s.label }}</div>
                }
              </div>
            }
            @if (!a.llm_used) { <p class="small faint">Réponse générée sans IA (mode hors ligne).</p> }
          </div>
        }
      </div>

      <div class="skills-wrap"><app-my-skills /></div>

      <div class="grid g2">
        <!-- Microsoft 365 -->
        <div class="card">
          <div class="card-head"><h2>Microsoft 365</h2>
            <div class="row">
              @if (msAuth.account()) { <span class="pill green">Connecté</span> }
              @if (msAuth.configured()) { <a class="more" routerLink="/microsoft">Parcourir en direct →</a> }
            </div>
          </div>
          @if (!msAuth.configured()) {
            <p class="small faint">Non configuré sur ce serveur (MICROSOFT_TENANT_ID / MICROSOFT_CLIENT_ID).</p>
          } @else if (!msAuth.account()) {
            <p class="muted">Connectez votre compte pour indexer mails, agenda, fichiers OneDrive/SharePoint, Teams et tâches.</p>
            <button class="primary" [disabled]="msBusy()" (click)="connectMicrosoft()">@if (msBusy()) { <span class="spin"></span> } Se connecter avec Microsoft</button>
          } @else {
            <div class="row" style="margin-bottom:10px">
              <button [disabled]="msBusy()" (click)="syncMicrosoft()">@if (msBusy()) { <span class="spin"></span> } Synchroniser maintenant</button>
              <label class="switch"><input type="checkbox" [checked]="autoSync()" (change)="toggleAutoSync($any($event.target).checked)" /> Synchronisation automatique (15 min)</label>
            </div>
            @if (msResult(); as r) {
              <p class="small">
                @for (k of objectKeys(r.indexed); track k) { <span class="pill">{{ k }} · {{ r.indexed[k] }}</span> }
              </p>
            }
          }
          @if (msError()) { <p class="small" style="color:var(--red)">{{ msError() }}</p> }
        </div>

        <!-- Local files -->
        <div class="card">
          <div class="card-head"><h2>Fichiers du PC</h2>
            <div class="row">
              @if (desktop.available && indexer.watching()) { <span class="pill green">Surveillance active</span> }
              @if (desktop.available) { <a class="more" routerLink="/local-files">Parcourir les fichiers →</a> }
            </div>
          </div>
          @if (!desktop.available) {
            <p class="small faint">Nécessite l'<strong>application bureau DeliveryOps</strong> (un navigateur ne peut pas lire les fichiers du PC).</p>
            <a class="btn" routerLink="/download">Télécharger l'app bureau</a>
          } @else {
            <div class="row" style="margin-bottom:10px">
              <button [disabled]="localBusy()" (click)="addFile()">+ Un fichier</button>
              <button [disabled]="localBusy()" (click)="addFolder()">+ Un dossier (indexé)</button>
              @if (!fullDisk()) {
                <button class="ghost" [disabled]="localBusy()" (click)="enableFullDisk()">Indexer tout le PC…</button>
              } @else {
                <button class="ghost ko" (click)="disableFullDisk()">Désactiver l'indexation totale</button>
              }
            </div>
            @if (localBusy()) { <p class="small muted"><span class="spin"></span> {{ localBusyLabel() }} ({{ progress().done }}/{{ progress().total }})</p> }
            @if (indexer.autoIndexed().length) {
              <div class="autoindex">
                <div class="small muted">Synchronisés automatiquement récemment :</div>
                @for (f of indexer.autoIndexed(); track f) { <div class="small faint mono">{{ f }}</div> }
              </div>
            }
            @if (localMessage()) { <p class="small" [style.color]="localError() ? 'var(--red)' : 'var(--green)'">{{ localMessage() }}</p> }
            <p class="small faint" style="margin-top:10px">Tout ajout ou modification dans un dossier autorisé est détecté et indexé automatiquement, sans action de votre part — même si vous quittez cet onglet.</p>
          }
        </div>
      </div>
    </div>
  `,
  styles: `
    .skills-wrap { margin-bottom: 16px; }
    .stat-pill { background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; padding: 6px 14px; font-size: 13px; white-space: nowrap; }
    .stat-pill.ready { border-color: var(--green); }
    .dim { color: var(--muted); }
    .search-card { margin-bottom: 16px; }
    .search-card input { flex: 1; }
    .answer { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border); }
    .sources { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
    .src-chip { font-size: 12px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; padding: 3px 10px; cursor: default; }
    .switch { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); cursor: pointer; }
    .autoindex { background: var(--surface-2); border-radius: 8px; padding: 8px 10px; margin: 8px 0; max-height: 110px; overflow-y: auto; }
    .more { font-size: 12px; font-weight: 500; white-space: nowrap; }
    .btn { display: inline-block; padding: 7px 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
  `,
})
export class WorkspacePage {
  private api = inject(Api);
  protected desktop = inject(Desktop);
  protected msAuth = inject(MicrosoftAuth);
  protected indexer = inject(LocalIndexer);

  protected status = signal<{ documents: number; chunks: number; ready: boolean } | null>(null);
  protected question = signal('');
  protected asking = signal(false);
  protected answer = signal<{ answer: string; llm_used: boolean; sources: Source[] } | null>(null);

  protected msBusy = signal(false);
  protected msError = signal('');
  protected msResult = signal<{ indexed: Record<string, number> } | null>(null);
  protected autoSync = signal(this.readAutoSync());
  private autoSyncTimer: ReturnType<typeof setInterval> | null = null;

  protected localBusy = signal(false);
  protected localBusyLabel = signal('');
  protected localMessage = signal('');
  protected localError = signal(false);
  protected progress = signal({ done: 0, total: 0 });
  protected fullDisk = signal(false);

  constructor() {
    this.refreshStatus();
    if (this.msAuth.configured() && this.msAuth.account() && this.autoSync()) this.startAutoSync();
    if (this.desktop.available) {
      this.desktop.fullDiskStatus().then(v => this.fullDisk.set(v)).catch(() => {});
    }
    const unsubscribeIndexed = this.indexer.onIndexed(() => this.refreshStatus());
    inject(DestroyRef).onDestroy(() => {
      if (this.autoSyncTimer) clearInterval(this.autoSyncTimer);
      unsubscribeIndexed();
    });
  }

  objectKeys(o: Record<string, unknown>) { return Object.keys(o ?? {}); }

  private refreshStatus() {
    this.api.assistantStatus().subscribe({ next: s => this.status.set(s), error: () => {} });
  }

  async ask() {
    const q = this.question().trim();
    if (!q) return;
    this.asking.set(true);
    // Best-effort silent Microsoft token so /ask can look at live calendar/mail; null if not connected.
    const token = this.msAuth.configured() ? await this.msAuth.getTokenSilent().catch(() => null) : null;
    this.api.assistantAsk(q, token).subscribe({
      next: a => { this.answer.set(a); this.asking.set(false); },
      error: () => { this.asking.set(false); },
    });
  }

  // ---- Microsoft ----

  async connectMicrosoft() {
    this.msBusy.set(true);
    this.msError.set('');
    try {
      await this.msAuth.getToken();
      await this.syncMicrosoft();
      if (this.autoSync()) this.startAutoSync();
    } catch (e: any) {
      this.msError.set(e?.message ?? 'Connexion Microsoft échouée');
    } finally {
      this.msBusy.set(false);
    }
  }

  async syncMicrosoft() {
    this.msBusy.set(true);
    this.msError.set('');
    try {
      const token = await this.msAuth.getToken();
      const res = await firstValueFrom(this.api.assistantScanMicrosoft(token));
      this.msResult.set(res);
      this.refreshStatus();
    } catch (e: any) {
      this.msError.set(e?.error?.detail ?? e?.message ?? 'Synchronisation Microsoft échouée');
    } finally {
      this.msBusy.set(false);
    }
  }

  toggleAutoSync(checked: boolean) {
    this.autoSync.set(checked);
    try { localStorage.setItem(AUTOSYNC_KEY, checked ? '1' : '0'); } catch { /* storage unavailable */ }
    if (checked) this.startAutoSync(); else if (this.autoSyncTimer) { clearInterval(this.autoSyncTimer); this.autoSyncTimer = null; }
  }

  private startAutoSync() {
    if (this.autoSyncTimer) return;
    this.autoSyncTimer = setInterval(() => this.syncMicrosoft(), AUTOSYNC_MS);
  }

  private readAutoSync(): boolean {
    try { return localStorage.getItem(AUTOSYNC_KEY) === '1'; } catch { return false; }
  }

  // ---- Local files ----

  async addFile() {
    try {
      const res = await this.desktop.pickFile();
      if (!res.added) return;
      this.localBusy.set(true);
      this.localBusyLabel.set('Indexation du fichier…');
      const name = res.added.split(/[\\/]/).pop() || res.added;
      const file = await this.desktop.toFile(res.added, name);
      await firstValueFrom(this.api.assistantScanLocal([file]));
      this.setLocalMessage(`${name} indexé.`, false);
      this.refreshStatus();
    } catch (e: any) { this.setLocalMessage(e?.message ?? 'Échec', true); }
    finally { this.localBusy.set(false); }
  }

  async addFolder() {
    try {
      const res = await this.desktop.pickFolder();
      if (!res.added) return;
      await this.indexFolder(res.added);
    } catch (e: any) { this.setLocalMessage(e?.message ?? 'Échec', true); }
  }

  async enableFullDisk() {
    const ok = window.confirm(
      "Indexer tout le disque analysera l'ensemble de vos lecteurs (hors dossiers système) et les gardera synchronisés en continu. "
      + 'Cela peut prendre du temps et indexer un grand nombre de documents. Continuer ?'
    );
    if (!ok) return;
    try {
      const res = await this.desktop.grantFullDisk();
      this.fullDisk.set(true);
      for (const root of res.roots) await this.indexFolder(root, true);
    } catch (e: any) { this.setLocalMessage(e?.message ?? 'Échec', true); }
  }

  async disableFullDisk() {
    try { await this.desktop.revokeFullDisk(); this.fullDisk.set(false); this.setLocalMessage('Indexation totale désactivée.', false); }
    catch (e: any) { this.setLocalMessage(e?.message ?? 'Échec', true); }
  }

  /** Bulk-scan a folder (or drive) and upload every indexable file in small, paced batches. */
  private async indexFolder(root: string, quiet = false) {
    this.localBusy.set(true);
    this.localBusyLabel.set(`Analyse de ${root}…`);
    try {
      const scanned = await this.desktop.scan(root, '', 6000);
      const items = (scanned.items as any[]).filter(f => (f.kind === 'text' || f.kind === 'document') && f.size < 8 * 1024 * 1024).slice(0, 400);
      this.progress.set({ done: 0, total: items.length });
      this.localBusyLabel.set(`Indexation de ${root}…`);
      const batchSize = 8;
      let pace = 150; // grows adaptively if the server is still rate-limited after retries
      let skipped = 0;
      for (let i = 0; i < items.length; i += batchSize) {
        const batch = items.slice(i, i + batchSize);
        const files = await Promise.all(batch.map(f => this.desktop.toFile(f.path, f.name)));
        try {
          await this.uploadWithBackoff(files);
          pace = 150;
        } catch {
          // still failing after uploadWithBackoff's own retries: don't abort the whole folder over
          // one stubborn batch — slow down and move on, the file watcher will pick these up later.
          skipped += files.length;
          pace = Math.min(pace * 2, 10_000);
        }
        this.progress.set({ done: Math.min(i + batchSize, items.length), total: items.length });
        await sleep(pace); // pace batches — a good citizen even with the bulk rate budget
      }
      if (!quiet) this.setLocalMessage(`${items.length - skipped} fichier(s) indexé(s) depuis ${root}.` + (skipped ? ` (${skipped} en attente, réessai automatique)` : ''), false);
      this.refreshStatus();
    } catch (e: any) {
      this.setLocalMessage(e?.error?.detail ?? e?.message ?? `Échec de l'indexation de ${root}`, true);
    } finally {
      this.localBusy.set(false);
    }
  }

  /** Upload one batch, retrying on 429 by waiting the server's Retry-After (falls back to a short
   *  fixed backoff) instead of failing the whole indexing run over a transient rate limit. */
  private async uploadWithBackoff(files: File[], attempt = 0): Promise<void> {
    try {
      await firstValueFrom(this.api.assistantScanLocal(files));
    } catch (e: any) {
      if (e?.status === 429 && attempt < 5) {
        const retryAfterS = Number(e?.headers?.get?.('Retry-After')) || 2;
        await sleep(Math.min(retryAfterS, 15) * 1000);
        return this.uploadWithBackoff(files, attempt + 1);
      }
      throw e;
    }
  }

  private setLocalMessage(msg: string, err: boolean) { this.localMessage.set(msg); this.localError.set(err); }
}

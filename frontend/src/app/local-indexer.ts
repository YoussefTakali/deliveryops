import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { Api } from './api';
import { Desktop } from './desktop';

function sleep(ms: number): Promise<void> { return new Promise(resolve => setTimeout(resolve, ms)); }

/** Continuous local-file indexing, as a root singleton instead of something owned by the Workspace
 *  page's component. It used to live inside WorkspacePage and get torn down by DestroyRef.onDestroy
 *  the moment the user navigated to another tab/route — silently pausing indexing until they came
 *  back to "Mon espace". Since this service is `providedIn: 'root'` and starts watching from its own
 *  constructor (which Angular runs once, the first time anything injects it — wired to fire at app
 *  bootstrap via app.ts), the fs-watcher → upload pipeline now runs for the app's entire lifetime. */
@Injectable({ providedIn: 'root' })
export class LocalIndexer {
  private api = inject(Api);
  private desktop = inject(Desktop);

  readonly autoIndexed = signal<string[]>([]);
  readonly watching = signal(false);
  private listeners = new Set<() => void>();

  private pendingChanges = new Set<string>();
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private flushDelayMs = 1500;
  private static readonly FLUSH_BATCH_MAX = 20;

  constructor() {
    if (this.desktop.available) {
      this.watching.set(true);
      this.desktop.onFileChanged((path) => this.queueChange(path));
    }
  }

  /** Called after each file (or batch) is successfully indexed — e.g. to refresh a document count. */
  onIndexed(cb: () => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  private queueChange(path: string) {
    this.pendingChanges.add(path);
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => this.flushChanges(), this.flushDelayMs);
  }

  private async flushChanges() {
    this.flushTimer = null;
    if (!this.pendingChanges.size) return;
    const batch = Array.from(this.pendingChanges).slice(0, LocalIndexer.FLUSH_BATCH_MAX);
    for (const p of batch) this.pendingChanges.delete(p);

    try {
      const files = await Promise.all(batch.map(async (p) => {
        const name = p.split(/[\\/]/).pop() || p;
        try { return await this.desktop.toFile(p, name); } catch { return null; } // e.g. deleted mid-write
      }));
      const valid = files.filter((f): f is File => !!f);
      if (valid.length) {
        await this.uploadWithBackoff(valid);
        this.autoIndexed.update(list => [...valid.map(f => f.name), ...list].slice(0, 8));
        for (const cb of this.listeners) cb();
      }
      this.flushDelayMs = 1500;
    } catch {
      for (const p of batch) this.pendingChanges.add(p);
      this.flushDelayMs = Math.min(this.flushDelayMs * 2, 30_000);
    } finally {
      if (this.pendingChanges.size) this.flushTimer = setTimeout(() => this.flushChanges(), this.flushDelayMs);
    }
  }

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
}

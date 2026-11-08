import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';

import { Api, DownloadInfo, Installer } from '../api';
import { Desktop } from '../desktop';

/** Download page: get the desktop app (installers for Windows / Linux / macOS, the visitor's OS first),
 *  which adds whole-PC file access and the floating
 *  Teams-call assistant on top of everything the web app already does. */
@Component({
  selector: 'app-download',
  imports: [DecimalPipe],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Application bureau</h1>
          <p>Toutes les fonctionnalités du web, plus l'accès aux fichiers de votre PC et l'assistant d'appel Teams.</p>
        </div>
      </div>

      @if (desktop.available) {
        <div class="card ok-card">
          <strong>✓ Vous utilisez déjà l'application bureau.</strong>
          <p class="muted">L'accès aux fichiers locaux et l'assistant d'appel sont disponibles.</p>
        </div>
      }

      <div class="grid g2">
        <div class="card">
          <h2>Ce que l'app bureau ajoute</h2>
          <ul class="feat">
            <li><strong>Toute l'application</strong> — le même produit que le web, dans une fenêtre native.</li>
            <li><strong>Fichiers du PC</strong> — analyse et indexe vos dossiers locaux (lecture seule, dossiers autorisés).</li>
            <li><strong>Assistant d'appel Teams</strong> — une fenêtre flottante toujours au-dessus qui suggère des réponses sourcées pendant l'appel (invisible au partage d'écran).</li>
            <li><strong>Compte Microsoft</strong> — récupère mails, agenda, fichiers et Teams du compte connecté au PC.</li>
          </ul>
        </div>

        <div class="card">
          <h2>Téléchargement</h2>
          @if (loading()) {
            <p class="muted"><span class="spin"></span> Vérification…</p>
          } @else if (groups().length) {
            @for (g of groups(); track g.platform) {
              <div class="platform">
                <h3>{{ platformName[g.platform] }}@if (g.platform === os) { <span class="small faint"> · votre système</span> }</h3>
                @for (i of g.items; track i.filename) {
                  <div class="inst">
                    <a class="btn dl" [class.primary]="g.platform === os" [href]="'/api/download/desktop?file=' + encodeURIComponent(i.filename)" download>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 21h16"/></svg>
                      {{ i.label }}
                    </a>
                    <span class="small muted">{{ i.size / 1048576 | number: '1.0-1' }} Mo</span>
                  </div>
                }
              </div>
            }
            <p class="small faint" style="margin-top:10px">Après installation, l'app demandera l'adresse de ce serveur au premier lancement.</p>
          } @else {
            <p class="muted">Aucun installeur n'est encore publié sur ce serveur.</p>
            <details>
              <summary>Comment le construire</summary>
              <pre class="build">cd desktop
npm install
npm run dist        # Windows (.exe)
npm run dist:linux  # Linux (.AppImage, .deb, .rpm)
# puis copier les fichiers de desktop/dist/
# dans backend/downloads/ — ils apparaîtront ici automatiquement.</pre>
            </details>
          }
        </div>
      </div>
    </div>
  `,
  styles: `
    .feat { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 8px; }
    .feat li { color: var(--text); }
    .ok-card { border-left: 3px solid var(--green); margin-bottom: 16px; }
    .dl { text-decoration: none; }
    .platform + .platform { margin-top: 14px; }
    .platform h3 { margin: 0 0 8px; font-size: 14px; }
    .inst { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
    .build { background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; padding: 12px; overflow-x: auto; white-space: pre; }
    details summary { cursor: pointer; color: var(--accent); margin-bottom: 8px; }
  `,
})
export class DownloadPage {
  private api = inject(Api);
  protected desktop = inject(Desktop);
  protected loading = signal(true);
  protected info = signal<DownloadInfo | null>(null);
  protected readonly os = detectOs();
  protected readonly platformName: Record<Installer['platform'], string> = { windows: 'Windows', linux: 'Linux', macos: 'macOS' };
  protected readonly encodeURIComponent = encodeURIComponent;
  /** Installers grouped by platform, the visitor's OS first. */
  protected groups = computed(() => {
    const all = this.info()?.installers ?? [];
    const order = [this.os, ...(['windows', 'linux', 'macos'] as const).filter((p) => p !== this.os)];
    return order.map((platform) => ({ platform, items: all.filter((i) => i.platform === platform) })).filter((g) => g.items.length);
  });

  constructor() {
    this.api.downloadInfo().subscribe({
      next: (i) => { this.info.set(i); this.loading.set(false); },
      error: () => { this.info.set({ available: false }); this.loading.set(false); },
    });
  }
}

function detectOs(): Installer['platform'] {
  const ua = navigator.userAgent;
  if (/Windows/i.test(ua)) return 'windows';
  if (/Mac OS X|Macintosh/i.test(ua)) return 'macos';
  return 'linux';
}

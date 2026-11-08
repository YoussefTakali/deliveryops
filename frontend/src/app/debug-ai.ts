import { Component, computed, inject, input } from '@angular/core';

import { Session } from './api';
import { Companion } from './companion.service';

/** An error, plus « Déboguer avec l'IA »: hands the error and what the user was doing to the companion,
 *  which answers in the chat with the likely cause and 2-3 fixes (one recommended) — debugging WITH the
 *  user instead of leaving them with a red message. */
@Component({
  selector: 'app-debug-ai',
  template: `
    @if (error()) {
      <div class="dbg" role="alert">
        <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 8v5M12 16h.01"/></svg>
        <span class="msg">{{ error() }}</span>
        @if (canDebug()) {
          <button type="button" class="sm" (click)="debug()" title="Le compagnon analyse l'erreur avec vous">
            <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5 5 6v5.5c0 4.3 3 7.6 7 9 4-1.4 7-4.7 7-9V6z"/><path d="M9.5 11.5 11 13l3.5-3.5"/></svg>
            Déboguer avec l'IA
          </button>
        }
      </div>
    }
  `,
  styles: `
    .dbg { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 10px; border-radius: 10px;
      background: var(--red-soft); border: 1px solid color-mix(in srgb, var(--red) 30%, transparent); color: var(--text); font-size: 13px; }
    .dbg > .ic { color: var(--red); flex: none; }
    .msg { flex: 1; min-width: 180px; }
    .ic { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; vertical-align: -3px; }
    button { display: inline-flex; align-items: center; gap: 5px; }
  `,
})
export class DebugAi {
  /** What the user was doing, in plain words (e.g. « Génération du livrable "Bilan hebdo" »). */
  context = input.required<string>();
  error = input<string | null | undefined>(null);
  private c = inject(Companion);
  private session = inject(Session);
  protected canDebug = computed(() => {
    const perms = this.session.me()?.permissions;
    return !perms || perms.includes('companion.use');
  });
  protected debug() { this.c.debug(this.context(), this.error() || ''); }
}

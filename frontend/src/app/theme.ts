import { Injectable, signal } from '@angular/core';

export type ThemeMode = 'auto' | 'light' | 'dark';

/** Theme controller: 'auto' follows the PC/OS setting (prefers-color-scheme), while 'light' and
 *  'dark' force a fixed theme via [data-theme] on <html>. The choice is persisted per browser. */
@Injectable({ providedIn: 'root' })
export class Theme {
  readonly mode = signal<ThemeMode>(this.read());

  constructor() {
    this.apply(this.mode());
    // Keep the "auto" resolution live if the OS flips theme while the app is open.
    try {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (this.mode() === 'auto') this.apply('auto');
      });
    } catch { /* matchMedia unavailable */ }
  }

  /** True when the effective theme is dark right now (used to pick the toggle icon). */
  isDark(): boolean {
    const m = this.mode();
    if (m !== 'auto') return m === 'dark';
    try { return window.matchMedia('(prefers-color-scheme: dark)').matches; } catch { return false; }
  }

  set(mode: ThemeMode): void {
    this.mode.set(mode);
    try { localStorage.setItem('dm.theme', mode); } catch { /* storage unavailable */ }
    this.apply(mode);
  }

  /** Cycle auto → light → dark → auto. */
  cycle(): void {
    this.set(this.mode() === 'auto' ? 'light' : this.mode() === 'light' ? 'dark' : 'auto');
  }

  private apply(mode: ThemeMode): void {
    const root = document.documentElement;
    if (mode === 'auto') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', mode);
  }

  private read(): ThemeMode {
    try {
      const v = localStorage.getItem('dm.theme');
      if (v === 'light' || v === 'dark' || v === 'auto') return v;
    } catch { /* storage unavailable */ }
    return 'auto';
  }
}

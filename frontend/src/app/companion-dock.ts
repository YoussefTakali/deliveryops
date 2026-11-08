import { Component, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ChatMessage, Companion } from './companion.service';
import { renderMd } from './shared';

/** A floating, dockable companion chat that can be shown or hidden at any time from anywhere in the
 *  app (launcher button, bottom-right). It reuses the app-wide Companion service, so its state and
 *  history are shared with the full /companion page. */
@Component({
  selector: 'app-companion-dock',
  imports: [RouterLink],
  template: `
    <!-- Launcher / visibility toggle -->
    <button class="fab" [class.hidden]="open()" (click)="toggle()" title="Compagnon (assistant)" aria-label="Ouvrir l'assistant">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5 5 6v5.5c0 4.3 3 7.6 7 9 4-1.4 7-4.7 7-9V6z"/><path d="M9 11h6M9 14h4"/></svg>
    </button>

    @if (open()) {
      <div class="dock">
        <div class="dhead">
          <div class="row" style="gap: 6px; align-items: center">
            <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5 5 6v5.5c0 4.3 3 7.6 7 9 4-1.4 7-4.7 7-9V6z"/></svg>
            <strong>Compagnon</strong>
            @if (scopeLabel(); as s) { <span class="scope" title="Le compagnon voit ce contexte de livraison">{{ s }}</span> }
          </div>
          <div class="row" style="gap: 2px">
            <a class="iconbtn" routerLink="/companion" (click)="open.set(false)" title="Ouvrir en plein écran" aria-label="Plein écran">
              <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-7 7M10 19H5v-5M5 19l7-7"/></svg>
            </a>
            <button class="iconbtn" (click)="open.set(false)" title="Masquer" aria-label="Masquer">
              <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>
            </button>
          </div>
        </div>

        <div class="body" #scroller>
          @for (m of messages(); track $index) {
            <div class="msg" [class.me]="m.role === 'user'">
              <div class="bubble">@if (m.role === 'user') { {{ m.content }} } @else { <div [innerHTML]="md(m.content)"></div> }@if (m.streaming && !m.content) { <span class="typing">…</span> }</div>
            </div>
          } @empty {
            <p class="empty">Posez une question sur vos projets, périmètres, réunions ou livrables.</p>
          }
        </div>

        <div class="compose">
          <textarea rows="1" placeholder="Écrire au compagnon…" [value]="draft()"
            (input)="draft.set($any($event.target).value)"
            (keydown.enter)="$event.preventDefault(); send()"></textarea>
          <button class="primary sm" (click)="send()" [disabled]="busy() || !draft().trim()">Envoyer</button>
        </div>
      </div>
    }
  `,
  styles: `
    .fab { position: fixed; right: 20px; bottom: 20px; z-index: 1003; width: 52px; height: 52px; border-radius: 50%;
      background: var(--accent); color: #fff; border: none; box-shadow: var(--shadow); display: flex; align-items: center; justify-content: center; cursor: pointer; }
    .fab svg { width: 26px; height: 26px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .fab.hidden { display: none; }
    .dock { position: fixed; right: 20px; bottom: 20px; z-index: 1003; width: min(380px, calc(100vw - 32px)); height: min(560px, calc(100vh - 96px));
      background: var(--surface); border: 1px solid var(--border); border-radius: 14px; box-shadow: var(--shadow); display: flex; flex-direction: column; overflow: hidden; }
    .dhead { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; border-bottom: 1px solid var(--border); }
    .scope { font-size: 10px; font-weight: 600; background: var(--accent-soft); color: var(--accent); border-radius: 999px; padding: 1px 7px; }
    .iconbtn { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 8px; background: transparent; border: none; cursor: pointer; color: var(--muted); }
    .iconbtn:hover { background: var(--surface-2); color: var(--text); }
    .ic { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .body { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
    .msg { display: flex; }
    .msg.me { justify-content: flex-end; }
    .bubble { max-width: 85%; padding: 8px 11px; border-radius: 12px; background: var(--surface-2); border: 1px solid var(--border); white-space: pre-wrap; word-break: break-word; font-size: 13.5px; line-height: 1.4; }
    .msg.me .bubble { background: var(--accent-soft); border-color: var(--accent-soft); }
    .typing { opacity: .5; }
    .compose { display: flex; gap: 6px; padding: 10px; border-top: 1px solid var(--border); align-items: flex-end; }
    .compose textarea { flex: 1; resize: none; max-height: 120px; }
  `,
})
export class CompanionDock {
  protected md = renderMd;
  private c = inject(Companion);
  protected scopeLabel = computed(() => {
    const s = this.c.scope();
    return s.perimeter_id ? 'ce périmètre' : s.project_id ? 'ce projet' : '';
  });
  protected open = signal(false);
  protected messages = signal<ChatMessage[]>([]);
  protected draft = signal('');
  protected busy = signal(false);
  private loaded = false;
  private scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  constructor() {
    // Keep the dock scrolled to the latest message as it streams.
    effect(() => { this.messages(); queueMicrotask(() => this.scroll()); });
    // « Déboguer avec l'IA » (or any page) can hand the companion a message: open and send it.
    effect(() => {
      const prompt = this.c.dockPrompt();
      if (!prompt) return;
      this.c.dockPrompt.set(null);
      this.open.set(true);
      void (async () => {
        if (!this.loaded) { this.loaded = true; await this.load(); }
        await this.send(prompt);
      })();
    });
  }

  toggle() {
    this.open.update(v => !v);
    if (this.open() && !this.loaded) { this.loaded = true; void this.load(); }
  }

  private async load() {
    try { this.messages.set(await this.c.messages()); } catch { this.messages.set([]); }
  }

  async send(text?: string) {
    const q = (text ?? this.draft()).trim();
    if (!q || this.busy()) return;
    if (text === undefined) this.draft.set('');
    this.busy.set(true);
    this.messages.update(m => [...m, { role: 'user', content: q }]);
    const reply: ChatMessage = { role: 'assistant', content: '', sources: [], streaming: true };
    this.messages.update(m => [...m, reply]);
    try {
      await this.c.stream('chat', { message: q }, ev => {
        if (ev.type === 'meta') reply.sources = ev.sources ?? [];
        else if (ev.type === 'delta') reply.content += ev.text;
        else if (ev.type === 'done' && !reply.content) reply.content = ev.answer ?? '';
        this.messages.update(m => [...m]);
      });
    } catch (e: any) {
      reply.content = reply.content || (e?.message ?? 'Une erreur est survenue.');
    } finally {
      reply.streaming = false;
      this.messages.update(m => [...m]);
      this.busy.set(false);
    }
  }

  private scroll() {
    const el = this.scroller()?.nativeElement;
    if (el) el.scrollTop = el.scrollHeight;
  }
}

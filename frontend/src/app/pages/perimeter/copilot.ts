import { DatePipe } from '@angular/common';
import { Component, ElementRef, effect, inject, input, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api, ChatMessage, Conversation } from '../../api';
import { renderMd } from '../../shared';

const TOOL_LABEL: Record<string, string> = {
  search_documents: 'Recherche documentaire', get_service_kpis: 'Indicateurs de service', list_tickets: 'Tickets', find_similar_incidents: 'Incidents similaires',
  get_application_map: 'Cartographie', get_capacity_forecast: 'Prévision de charge', get_prebilling: 'Pré-facturation', estimate_change_request: 'Chiffrage',
  list_recommendations: 'Recommandations', propose_action: 'Proposition d’action', start_workflow: 'Lancement de workflow',
};

@Component({
  selector: 'app-copilot',
  imports: [DatePipe, RouterLink],
  template: `
    <div class="layout">
      <aside class="card threads">
        <button class="primary" (click)="newThread()">+ Nouvelle conversation</button>
        <div class="list">
          @for (c of conversations(); track c.id) {
            <div class="thread" [class.on]="current()?.id === c.id">
              <button class="ghost title" (click)="open(c.id)">{{ c.title }}<small>{{ c.updated_at | date: 'dd/MM HH:mm' }}</small></button>
              <button class="ghost sm del" (click)="remove(c.id)" aria-label="Supprimer">✕</button>
            </div>
          } @empty { <p class="small faint">Aucune conversation.</p> }
        </div>
      </aside>

      <section class="card chat">
        <div class="messages" #scroller>
          @if (!messages().length) {
            <div class="intro">
              <h2>Copilot Delivery</h2>
              <p class="muted">Posez une question sur le périmètre. L'agent choisit ses outils (documentation, KPI, tickets, capacité, facturation, chiffrage), cite ses sources, et peut proposer une action ou lancer un workflow — toujours soumis à validation humaine.</p>
              <div class="suggestions">
                @for (s of suggestions; track s) { <button class="sm" (click)="send(s)">{{ s }}</button> }
              </div>
            </div>
          }
          @for (m of messages(); track $index) {
            <div class="msg" [class.user]="m.role === 'user'">
              @if (m.role === 'assistant' && m.trace?.length) {
                <div class="trace">
                  @for (t of m.trace; track $index) { <span class="pill" [title]="json(t.input)">{{ toolLabel(t.tool) }} · {{ t.summary }}</span> }
                </div>
              }
              <div class="bubble" [innerHTML]="render(m.text)"></div>
              @if (m.actions?.length) {
                <div class="row small">
                  @for (a of m.actions; track a.id) {
                    @if (a.type === 'run') { <a class="pill accent" [routerLink]="['/runs', a.id]">Workflow {{ a.workflow }} lancé →</a> }
                    @else { <a class="pill amber" routerLink="/approvals">Proposition en attente de validation →</a> }
                  }
                </div>
              }
              @if (m.sources?.length) {
                <details class="small"><summary class="muted">Sources ({{ m.sources!.length }})</summary>
                  @for (s of m.sources; track $index) { <div class="src"><span class="pill">{{ s.type }}</span> {{ s.label }}</div> }
                </details>
              }
              @if (m.role === 'assistant') { <span class="small faint">{{ m.llm_used ? 'IA' : 'Mode hors ligne' }} · {{ m.ts | date: 'HH:mm' }}</span> }
            </div>
          }
          @if (busy()) { <div class="msg"><div class="bubble thinking"><span class="spin"></span> L'agent analyse et interroge ses outils…</div></div> }
        </div>
        <form class="composer" (submit)="$event.preventDefault(); send(draft())">
          <textarea rows="2" [value]="draft()" (input)="draft.set($any($event.target).value)" (keydown.enter)="onEnter($event)" placeholder="Ex. Quels incidents P1/P2 sont en cours et comment ont été résolus les cas similaires ?"></textarea>
          <button class="primary" [disabled]="!draft().trim() || busy()">Envoyer</button>
        </form>
        @if (error()) { <p class="small" style="color: var(--red)">{{ error() }}</p> }
      </section>
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: 260px 1fr; gap: 16px; height: calc(100vh - 210px); min-height: 520px; }
    .threads { display: flex; flex-direction: column; gap: 10px; overflow: hidden; }
    .list { overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
    .thread { display: flex; align-items: center; border-radius: 8px; }
    .thread.on { background: var(--accent-soft); }
    .title { flex: 1; text-align: left; flex-direction: column; align-items: flex-start; font-weight: 400; overflow: hidden; gap: 0; }
    .title small { color: var(--faint); font-size: 11px; }
    .del { opacity: .5; }
    .chat { display: flex; flex-direction: column; padding: 0; overflow: hidden; }
    .messages { flex: 1; overflow-y: auto; padding: 18px; display: flex; flex-direction: column; gap: 14px; }
    .intro { max-width: 640px; margin: 40px auto; text-align: center; display: flex; flex-direction: column; gap: 10px; }
    .suggestions { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; margin-top: 8px; }
    .suggestions button { font-weight: 400; }
    .msg { display: flex; flex-direction: column; gap: 6px; max-width: 85%; }
    .msg.user { align-self: flex-end; align-items: flex-end; }
    .bubble { background: var(--surface-2); border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; font-size: 14px; line-height: 1.55; overflow-wrap: anywhere; }
    .msg.user .bubble { background: var(--accent); color: #fff; border-color: var(--accent); }
    .bubble :is(p, ul) { margin: 0 0 6px; }
    .bubble blockquote { margin: 4px 0; padding-left: 10px; border-left: 3px solid var(--border); color: var(--muted); }
    .thinking { color: var(--muted); display: flex; gap: 8px; align-items: center; }
    .trace { display: flex; flex-wrap: wrap; gap: 4px; }
    .src { margin: 4px 0; }
    .composer { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--border); align-items: flex-end; }
    .composer textarea { resize: none; }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; height: auto; } .threads { max-height: 200px; } .chat { height: 70vh; } }
  `,
})
export class CopilotTab {
  private api = inject(Api);
  perimeterId = input.required<string>();
  private scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  protected conversations = signal<Conversation[]>([]);
  protected current = signal<Conversation | null>(null);
  protected messages = signal<ChatMessage[]>([]);
  protected draft = signal('');
  protected busy = signal(false);
  protected error = signal('');
  protected suggestions = [
    'Quels sont les incidents P1/P2 en cours et comment ont été résolus les cas similaires ?',
    'Le respect des SLA se dégrade-t-il ? Sur quelles applications ?',
    "L'équipe peut-elle absorber +20 % d'incidents le mois prochain ?",
    'Quelles anomalies bloquent la pré-facture du mois dernier ?',
  ];

  constructor() {
    effect(() => { this.perimeterId(); this.newThread(); this.loadThreads(); });
  }

  loadThreads() { this.api.conversations(this.perimeterId()).subscribe(c => this.conversations.set(c)); }
  newThread() { this.current.set(null); this.messages.set([]); this.error.set(''); }
  open(id: string) { this.api.conversation(id).subscribe(c => { this.current.set(c); this.messages.set(c.messages); this.scroll(); }); }
  remove(id: string) {
    this.api.deleteConversation(id).subscribe(() => { if (this.current()?.id === id) this.newThread(); this.loadThreads(); });
  }

  onEnter(event: Event) {
    const e = event as KeyboardEvent;
    if (!e.shiftKey) { e.preventDefault(); this.send(this.draft()); }
  }

  send(text: string) {
    const message = text.trim();
    if (!message || this.busy()) return;
    this.draft.set('');
    this.error.set('');
    this.busy.set(true);
    this.messages.update(m => [...m, { role: 'user', text: message, ts: new Date().toISOString() }]);
    this.scroll();
    this.api.chat(this.perimeterId(), message, this.current()?.id).subscribe({
      next: conv => { this.busy.set(false); this.current.set(conv); this.messages.set(conv.messages); this.loadThreads(); this.scroll(); },
      error: e => { this.busy.set(false); this.error.set(e.error?.detail ?? 'Erreur lors de l’appel à l’agent'); },
    });
  }

  private scroll() {
    setTimeout(() => { const el = this.scroller()?.nativeElement; if (el) el.scrollTop = el.scrollHeight; });
  }

  toolLabel(t: string) { return TOOL_LABEL[t] ?? t; }
  json(v: unknown) { return JSON.stringify(v); }

  /** Minimal, safe markdown: escape HTML first, then bold, quotes, bullets, line breaks. */
  render(text: string): string { return renderMd(text); }
}

import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';

import { Api, WatchItem, WatchState, WatchTopic } from '../api';

/** "Veille": recent news about what I actually work with (skills, projects, applications) and what each
 *  item means for my projects — with a suggested action. Topics are derived, and I can add or mute some. */
@Component({
  selector: 'app-techwatch',
  imports: [DatePipe],
  template: `
    <div class="page tw">
      <div class="page-head">
        <div>
          <h1>Veille technologique</h1>
          <p>L'actualité de vos technologies et de vos projets, et ce qu'elle change pour votre travail.</p>
        </div>
        <button class="primary" (click)="refresh()" [disabled]="busy() || !activeTopics().length">
          @if (busy()) { <span class="spin"></span> Analyse en cours… } @else { Actualiser }
        </button>
      </div>

      @if (error()) { <div class="banner bad" role="alert">{{ error() }}</div> }

      @if (state(); as s) {
        @if (s.briefing; as b) {
          <section class="card summary">
            <div class="row between">
              <h2>À retenir</h2>
              <span class="small faint">Mise à jour {{ b.created_at | date: 'dd/MM à HH:mm' }}@if (!b.llm_used) { · analyse simplifiée (IA indisponible) }</span>
            </div>
            <p>{{ b.summary }}</p>
            @if (b.stale) { <p class="small stale">Cette veille date de plus de 12 heures : actualisez-la pour les dernières nouvelles.</p> }
          </section>
        }

        <!-- Topics -->
        <section class="card">
          <div class="card-head"><div><h2>Mes sujets</h2><p>Déduits de vos compétences, de vos projets et de leurs applications. Cliquez pour ignorer un sujet.</p></div></div>
          <div class="topics">
            @for (t of s.topics.slice(0, showAll() ? 40 : 14); track t.topic) {
              <button class="topic" [class.muted]="t.muted" [class.manual]="t.manual" (click)="toggleMute(t)"
                      [title]="(t.muted ? 'Ignoré — cliquer pour suivre à nouveau. ' : '') + t.reasons.join(' · ')" translate="no">
                {{ t.topic }}@if (t.manual) { <span class="x" (click)="$event.stopPropagation(); removeExtra(t)" aria-label="Ne plus suivre">×</span> }
              </button>
            } @empty { <span class="small faint">Aucun sujet détecté : renseignez vos compétences dans Mon espace ou ajoutez un sujet.</span> }
            @if (s.topics.length > 14) { <button class="linkbtn small" (click)="showAll.set(!showAll())">{{ showAll() ? 'Moins' : '+ ' + (s.topics.length - 14) + ' autres' }}</button> }
          </div>
          <form class="add" (submit)="$event.preventDefault(); addTopic()">
            <input placeholder="Suivre aussi… (ex. Kubernetes, SAP S/4HANA, NIS 2)" maxlength="60" [value]="newTopic()" (input)="newTopic.set($any($event.target).value)" aria-label="Nouveau sujet" />
            <button class="sm" type="submit" [disabled]="!newTopic().trim()">Ajouter</button>
          </form>
        </section>

        <!-- News -->
        @if (s.briefing; as b) {
          @if (b.items.length) {
            <div class="filters row">
              <button class="sm" [class.primary]="!category()" (click)="category.set('')">Tout ({{ b.items.length }})</button>
              @for (c of presentCategories(); track c) {
                <button class="sm" [class.primary]="category() === c" (click)="category.set(c)">{{ s.categories[c] }} ({{ countOf(c) }})</button>
              }
            </div>
            <div class="items">
              @for (i of visible(); track i.url) {
                <article class="card item" [class.hot]="i.category === 'securite' && i.relevance >= 70">
                  <div class="row between top">
                    <span class="cat" [class]="i.category">{{ s.categories[i.category] }}</span>
                    <span class="rel" [title]="'Pertinence pour vous : ' + i.relevance + '/100'"><span [style.width.%]="i.relevance"></span></span>
                  </div>
                  <h3><a [href]="i.url" target="_blank" rel="noopener noreferrer">{{ i.title }}</a></h3>
                  <div class="small faint" translate="no">{{ i.source }}@if (i.published) { · {{ i.published | date: 'dd/MM/yyyy' }} } · {{ i.topic }}</div>
                  <p class="impact"><strong>Pour vous :</strong> {{ i.impact }}</p>
                  @if (i.affected.length) { <div class="chips">@for (a of i.affected; track a) { <span class="pill" translate="no">{{ a }}</span> }</div> }
                  @if (i.action) { <p class="action small">→ {{ i.action }}</p> }
                </article>
              }
            </div>
          } @else {
            <div class="card empty">Rien de marquant sur vos sujets ces dernières semaines.</div>
          }
        } @else if (!busy()) {
          <div class="card empty">
            <p>Votre première veille n'a pas encore été générée.</p>
            <button class="primary" (click)="refresh()" [disabled]="!activeTopics().length">Lancer ma veille</button>
          </div>
        }
      } @else if (!error()) {
        <p class="muted"><span class="spin"></span> Chargement…</p>
      }
    </div>
  `,
  styles: `
    .tw { display: flex; flex-direction: column; gap: 20px; }
    .tw .page-head { margin-bottom: 0; }
    .card { padding: 18px 20px; }
    .card-head { margin-bottom: 14px; }
    .card-head p { margin-top: 4px; }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .between { justify-content: space-between; }
    .summary p { margin-top: 8px; line-height: 1.6; }
    .stale { color: var(--amber); }
    .banner.bad { background: var(--red-soft); color: var(--red); padding: 12px 16px; border-radius: 10px; }
    .topics { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .topic { font-size: 13px; font-weight: 500; padding: 5px 12px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); border: 1px solid transparent; gap: 6px; }
    .topic.manual { border-color: var(--accent); }
    .topic.muted { background: var(--surface-2); color: var(--faint); text-decoration: line-through; }
    .topic .x { font-size: 15px; line-height: 1; opacity: .7; }
    .linkbtn { background: none; border: 0; color: var(--accent); padding: 2px 6px; }
    .add { display: flex; gap: 8px; margin-top: 14px; max-width: 520px; }
    .items { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
    .item { display: flex; flex-direction: column; gap: 8px; }
    .item.hot { border-color: var(--red); }
    .item h3 { font-size: 14.5px; line-height: 1.4; margin: 2px 0 0; }
    .item h3 a { color: var(--text); }
    .item h3 a:hover { color: var(--accent); }
    .top { align-items: center; }
    .cat { font-size: 11px; font-weight: 600; border-radius: 999px; padding: 2px 9px; background: var(--grey-soft); color: var(--muted); }
    .cat.securite { background: var(--red-soft); color: var(--red); }
    .cat.fin_de_support { background: var(--amber-soft); color: var(--amber); }
    .cat.nouvelle_version { background: var(--accent-soft); color: var(--accent); }
    .cat.reglementation { background: rgba(139,92,246,.12); color: #8b5cf6; }
    .rel { width: 70px; height: 6px; border-radius: 3px; background: var(--grey-soft); overflow: hidden; }
    .rel span { display: block; height: 100%; background: var(--accent); }
    .impact { margin: 0; line-height: 1.5; font-size: 13.5px; }
    .chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .action { margin: 0; color: var(--green); font-weight: 500; }
    .empty { text-align: center; color: var(--muted); display: flex; flex-direction: column; gap: 12px; align-items: center; padding: 32px; }
  `,
})
export class TechWatchPage {
  private api = inject(Api);
  protected state = signal<WatchState | null>(null);
  protected busy = signal(false);
  protected error = signal('');
  protected newTopic = signal('');
  protected category = signal('');
  protected showAll = signal(false);

  protected activeTopics = computed(() => (this.state()?.topics ?? []).filter(t => !t.muted));
  private items = computed<WatchItem[]>(() => this.state()?.briefing?.items ?? []);
  protected presentCategories = computed(() => [...new Set(this.items().map(i => i.category))]);
  protected visible = computed(() => this.items().filter(i => !this.category() || i.category === this.category()));

  constructor() {
    this.api.techWatch().subscribe({ next: s => this.state.set(s), error: e => this.fail(e) });
  }

  protected countOf(c: string) { return this.items().filter(i => i.category === c).length; }

  protected refresh() {
    this.busy.set(true);
    this.error.set('');
    this.api.refreshTechWatch().subscribe({
      next: s => { this.busy.set(false); this.category.set(''); this.state.set(s); },
      error: e => { this.busy.set(false); this.fail(e); },
    });
  }

  private saveTopics(extra: string[], muted: string[]) {
    this.api.setWatchTopics(extra, muted).subscribe({ next: s => this.state.set(s), error: e => this.fail(e) });
  }

  private extras() { return (this.state()?.topics ?? []).filter(t => t.manual).map(t => t.topic); }
  private muted() { return (this.state()?.topics ?? []).filter(t => t.muted).map(t => t.topic); }

  protected addTopic() {
    const t = this.newTopic().trim();
    if (!t) return;
    this.newTopic.set('');
    this.saveTopics([...this.extras(), t], this.muted().filter(m => m.toLowerCase() !== t.toLowerCase()));
  }

  protected removeExtra(t: WatchTopic) { this.saveTopics(this.extras().filter(x => x !== t.topic), this.muted()); }

  protected toggleMute(t: WatchTopic) {
    const muted = t.muted ? this.muted().filter(m => m.toLowerCase() !== t.topic.toLowerCase()) : [...this.muted(), t.topic];
    this.saveTopics(this.extras(), muted);
  }

  private fail(e: any) { this.error.set(typeof e?.error?.detail === 'string' ? e.error.detail : 'Veille indisponible pour le moment.'); }
}

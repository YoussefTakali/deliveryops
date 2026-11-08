import { DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Component, computed, inject, signal } from '@angular/core';

import { Api } from '../api';
import { MicrosoftAuth } from '../microsoft-auth';

const SOURCE_LABELS: Record<string, string> = {
  calendar: 'Calendrier (Outlook)', mail: 'Messagerie (Outlook)', files: 'Fichiers récents (OneDrive/SharePoint)',
  sites: 'Sites SharePoint suivis', teams: 'Équipes Teams', chats: 'Conversations Teams',
  tasks: 'Tâches (To Do / Planner)', presence: 'Présence',
};

@Component({
  selector: 'app-microsoft',
  imports: [DatePipe, RouterLink],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <a class="crumb" routerLink="/workspace">← Mon espace</a>
          <h1>Mon espace Microsoft</h1>
          <p>Vos données Microsoft 365 (Outlook, Teams, OneDrive/SharePoint, To Do), lues en votre nom via Microsoft Graph. Aucune donnée n'est stockée : tout est récupéré en direct depuis votre compte.</p>
        </div>
        @if (account(); as a) {
          <div class="who">
            <div class="name">{{ a.name || a.username }}</div>
            <div class="small faint">{{ a.username }}</div>
            <button class="sm ghost" (click)="reload()">Actualiser</button>
          </div>
        }
      </div>

      @if (!msAuth.configured()) {
        <div class="empty card">L'intégration Microsoft n'est pas configurée sur ce déploiement (MICROSOFT_TENANT_ID / MICROSOFT_CLIENT_ID). Voir le README « Microsoft Entra ID ».</div>
      } @else if (needsSignIn()) {
        <div class="card connect">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M3 3h8.5v8.5H3z" fill="#f25022"/><path d="M12.5 3H21v8.5h-8.5z" fill="#7fba00"/><path d="M3 12.5h8.5V21H3z" fill="#00a4ef"/><path d="M12.5 12.5H21V21h-8.5z" fill="#ffb900"/></svg>
          <div>
            <h2>Connexion à votre compte Microsoft</h2>
            <p class="muted">Nous n'avons pas pu vous connecter automatiquement (cela se produit hors des postes joints à Entra ID). Un clic suffit — vous resterez connecté ensuite.</p>
          </div>
          <button class="primary" [disabled]="busy()" (click)="connect()">@if (busy()) { <span class="spin"></span> } Se connecter avec Microsoft</button>
          @if (error()) { <p class="small" style="color: var(--red)">{{ error() }}</p> }
        </div>
      } @else if (loading()) {
        <div class="empty"><span class="spin"></span> Récupération de vos données Microsoft…</div>
      } @else if (data(); as d) {
        <div class="summary card">
          <span class="pill green">{{ d.sources_available }}/{{ d.sources_total }} sources disponibles</span>
          @if (d.profile.job_title) { <span class="pill">{{ d.profile.job_title }}</span> }
          @if (d.profile.office) { <span class="pill">{{ d.profile.office }}</span> }
          @if (presence(); as p) { <span class="pill" [class.green]="p.availability === 'Available'">{{ p.availability }}{{ p.activity && p.activity !== p.availability ? ' · ' + p.activity : '' }}</span> }
        </div>

        @if (consentGapKeys().length) {
          <div class="card consent-gap">
            <div>
              <strong>{{ consentGapKeys().length }} source(s) bloquée(s) par une permission non accordée</strong>
              <p class="small muted">{{ consentGapLabels() }} nécessitent qu'un administrateur de votre organisation Microsoft accorde le consentement une fois pour tout le monde — un utilisateur normal ne peut pas se l'accorder à lui-même.</p>
            </div>
            @if (adminConsentUrl(); as url) {
              <a class="btn primary" [href]="url" target="_blank" rel="noopener">Accorder le consentement admin</a>
            } @else {
              <p class="small faint">Connectez-vous pour obtenir le lien de consentement.</p>
            }
          </div>
        }

        <div class="grid g2">
          @for (key of orderedKeys(); track key) {
            @let src = d.sources[key];
            <div class="card src">
              <div class="card-head">
                <h2>{{ label(key) }}</h2>
                @if (!src.error) { <span class="pill">{{ src.count ?? (src.items?.length || 0) }}</span> }
              </div>
              @if (src.error) {
                <p class="small faint">{{ src.error }}</p>
              } @else {
                @switch (key) {
                  @case ('calendar') {
                    @for (e of src.items; track $index) {
                      <div class="row-item">
                        <div><strong>{{ e.subject || '(sans objet)' }}</strong>@if (e.online) { <span class="pill accent">Teams</span> }</div>
                        <div class="small faint">{{ e.start | date: 'EEE dd/MM HH:mm' }}@if (e.location) { · {{ e.location }} } @if (e.attendees) { · {{ e.attendees }} particip. }</div>
                      </div>
                    } @empty { <p class="empty">Aucun événement à venir.</p> }
                  }
                  @case ('mail') {
                    @if (src.unread) { <p class="small faint" style="margin:-4px 0 8px">{{ src.unread }} non lu(s)</p> }
                    @for (m of src.items; track $index) {
                      <div class="row-item">
                        <div [class.unread]="m.unread"><strong>{{ m.subject || '(sans objet)' }}</strong></div>
                        <div class="small faint">{{ m.from }} · {{ m.received | date: 'dd/MM HH:mm' }}</div>
                        <div class="small muted preview">{{ m.preview }}</div>
                      </div>
                    } @empty { <p class="empty">Boîte vide.</p> }
                  }
                  @case ('files') {
                    @for (f of src.items; track $index) {
                      <div class="row-item"><div><strong>{{ f.name }}</strong></div><div class="small faint">Modifié {{ f.modified | date: 'dd/MM HH:mm' }}@if (f.by) { · {{ f.by }} }</div></div>
                    } @empty { <p class="empty">Aucun fichier récent.</p> }
                  }
                  @case ('teams') {
                    @for (t of src.items; track $index) { <div class="row-item"><strong>{{ t.name }}</strong>@if (t.description) { <div class="small faint">{{ t.description }}</div> }</div> }
                    @empty { <p class="empty">Aucune équipe.</p> }
                  }
                  @case ('chats') {
                    @for (ch of src.items; track $index) { <div class="row-item"><div><strong>{{ ch.topic || 'Conversation' }}</strong></div><div class="small muted preview">{{ ch.last_message }}</div><div class="small faint">{{ ch.last_at | date: 'dd/MM HH:mm' }}</div></div> }
                    @empty { <p class="empty">Aucune conversation.</p> }
                  }
                  @case ('sites') {
                    @for (st of src.items; track $index) { <div class="row-item"><strong>{{ st.name }}</strong><div class="small faint">{{ st.url }}</div></div> }
                    @empty { <p class="empty">Aucun site suivi.</p> }
                  }
                  @case ('tasks') {
                    @for (l of src.lists; track $index) {
                      @if (l.tasks.length) {
                        <div class="tasklist"><div class="small muted">{{ l.name }}</div>
                          @for (t of l.tasks; track $index) { <div class="row-item task"><span [class.high]="t.importance === 'high'">{{ t.title }}</span>@if (t.due) { <span class="small faint"> · {{ t.due | date: 'dd/MM' }}</span> }</div> }
                        </div>
                      }
                    } @empty { <p class="empty">Aucune tâche.</p> }
                  }
                }
              }
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .crumb { display: inline-block; font-size: 12px; margin-bottom: 4px; color: var(--muted); }
    .crumb:hover { color: var(--accent); }
    .who { text-align: right; display: flex; flex-direction: column; gap: 2px; align-items: flex-end; }
    .who .name { font-weight: 600; }
    .connect { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
    .connect > div { flex: 1; min-width: 240px; }
    .summary { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; }
    .consent-gap { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; border-color: var(--accent); }
    .consent-gap > div { flex: 1; min-width: 240px; }
    .consent-gap .btn { white-space: nowrap; }
    .src { display: flex; flex-direction: column; }
    .row-item { padding: 7px 0; border-top: 1px solid var(--border); }
    .row-item:first-of-type { border-top: 0; }
    .preview { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .unread strong { color: var(--accent); }
    .task .high { color: var(--red); font-weight: 600; }
    .tasklist { margin-bottom: 8px; }
  `,
})
export class MicrosoftPage {
  private api = inject(Api);
  protected msAuth = inject(MicrosoftAuth);
  protected account = this.msAuth.account;

  protected data = signal<any>(null);
  protected loading = signal(false);
  protected needsSignIn = signal(false);
  protected busy = signal(false);
  protected error = signal('');
  protected presence = computed(() => { const p = this.data()?.sources?.presence; return p && !p.error ? p : null; });
  private order = ['calendar', 'mail', 'chats', 'files', 'teams', 'tasks', 'sites'];
  protected orderedKeys = computed(() => this.order.filter(k => this.data()?.sources?.[k]));
  protected consentGapKeys = computed(() => {
    const sources = this.data()?.sources ?? {};
    return Object.keys(sources).filter(k => sources[k]?.status === 403);
  });
  protected consentGapLabels = computed(() => this.consentGapKeys().map(k => this.label(k)).join(', '));
  protected adminConsentUrl = computed(() => this.msAuth.adminConsentUrl());

  constructor() {
    if (this.msAuth.configured()) this.load();
  }

  label(key: string) { return SOURCE_LABELS[key] ?? key; }

  private load() {
    this.loading.set(true);
    this.error.set('');
    this.api.microsoftOverview().subscribe({
      next: d => { this.loading.set(false); this.needsSignIn.set(false); this.data.set(d); },
      error: e => {
        this.loading.set(false);
        if (e.status === 401) this.needsSignIn.set(true);
        else this.error.set(e.error?.detail ?? 'Erreur lors de la récupération des données Microsoft');
      },
    });
  }

  reload() { this.data.set(null); this.load(); }

  async connect() {
    this.busy.set(true);
    this.error.set('');
    try {
      await this.msAuth.getToken();  // interactive if needed
      this.needsSignIn.set(false);
      this.load();
    } catch (e: any) {
      this.error.set(e?.message ?? 'Connexion Microsoft annulée ou échouée');
    } finally {
      this.busy.set(false);
    }
  }
}

import { DatePipe } from '@angular/common';
import { Component, DestroyRef, effect, inject, input, signal } from '@angular/core';
import { Router } from '@angular/router';

import { Api } from '../../api';

interface Line { speaker: string; text: string; }
interface Answer { question: string; answer: string; confidence: string; follow_up: string; out_of_scope: boolean; out_of_scope_terms: string[]; sources: any[]; llm_used: boolean; }

const DEMO: Line[] = [
  { speaker: 'Directeur SI client', text: 'Bonjour à tous, on commence par le point sur les incidents du mois.' },
  { speaker: 'Delivery Manager', text: 'Nous avons eu plusieurs échecs du batch indemnisation, tous repris avant 6h sauf un.' },
  { speaker: 'Responsable métier', text: 'Pourquoi le batch indemnisation échoue-t-il aussi souvent en fin de mois ?' },
  { speaker: 'Delivery Manager', text: "Je m'en charge : on vous envoie une analyse de cause racine d'ici vendredi." },
  { speaker: 'Responsable métier', text: 'Pouvez-vous prendre en charge la migration des documents historiques de la GED vers le stockage objet ?' },
  { speaker: 'Directeur SI client', text: 'Quelle est la procédure de reprise si le fichier SEPA n’est pas généré ?' },
  { speaker: 'Architecte', text: 'Attention, PortailExperts tourne encore en PHP 5.6, c’est un risque sécurité à moyen terme.' },
  { speaker: 'Directeur SI client', text: 'On valide le passage en production de la nouvelle version du portail experts le 15.' },
];

@Component({
  selector: 'app-meetings',
  imports: [DatePipe],
  template: `
    @if (!active()) {
      <div class="grid g2">
        <div class="card">
          <h2>Démarrer l'assistant de réunion</h2>
          <p class="small muted">Le Meeting Intelligence Agent écoute la réunion (micro du navigateur ou saisie), détecte les questions, répond en temps réel à partir de la documentation et de l'historique des incidents, signale les demandes hors contrat, puis génère le compte-rendu.</p>
          <form class="row start" (submit)="$event.preventDefault(); create()">
            <input [value]="title()" (input)="title.set($any($event.target).value)" placeholder="Titre de la réunion" style="flex: 1" />
            <button class="primary" [disabled]="!title().trim()">Démarrer</button>
          </form>
          <div class="import">
            <label for="transcript">Ou importer une transcription Teams (.vtt, .docx, .txt) — le compte-rendu est généré automatiquement</label>
            <input id="transcript" type="file" accept=".vtt,.docx,.txt" (change)="importTranscript($any($event.target))" [disabled]="importing()" />
            @if (importError()) { <span class="small" style="color: var(--red)">{{ importError() }}</span> }
          </div>
        </div>
        <div class="card">
          <h2>Réunions</h2>
          <table>
            @for (m of meetings(); track m.id) {
              <tr><td>{{ m.title }}</td><td class="small faint mono">{{ m.held_at | date: 'dd/MM HH:mm' }}</td>
                <td><span class="pill" [class.green]="m.minutes">{{ m.minutes ? 'CR généré' : m.status }}</span></td>
                <td class="num"><button class="sm" (click)="open(m)">Ouvrir</button></td></tr>
            } @empty { <tr><td class="empty">Aucune réunion.</td></tr> }
          </table>
        </div>
      </div>
    } @else {
      @let m = active();
      <div class="head row">
        <button class="ghost sm" (click)="close()">← Réunions</button>
        <h2>{{ m.title }}</h2>
        @if (listening()) { <span class="pill red"><span class="dot red pulse"></span> Écoute en cours</span> }
        <span class="spacer"></span>
        @if (speechSupported) {
          <button (click)="toggleMic()">{{ listening() ? 'Couper le micro' : 'Activer le micro' }}</button>
        }
        <button (click)="playDemo()" [disabled]="demoRunning()">Rejouer un scénario de démo</button>
        <button class="primary" [disabled]="!lines().length || ending()" (click)="end()">@if (ending()) { <span class="spin"></span> } Terminer et générer le CR</button>
      </div>
      <div class="live">
        <div class="card transcript">
          <h3>Transcription</h3>
          <div class="lines">
            @for (l of lines(); track $index) {
              <div class="line"><span class="who">{{ l.speaker }}</span><span>{{ l.text }}</span></div>
            } @empty { <div class="empty">En attente de parole…</div> }
            @if (interim()) { <div class="line interim"><span class="who">…</span><span>{{ interim() }}</span></div> }
          </div>
          <form class="row input" (submit)="$event.preventDefault(); say()">
            <select [value]="speaker()" (change)="speaker.set($any($event.target).value)" style="width: 170px">
              @for (s of speakers; track s) { <option>{{ s }}</option> }
            </select>
            <input [value]="draft()" (input)="draft.set($any($event.target).value)" placeholder="Saisir une intervention ou une question" style="flex: 1" />
            <button>Ajouter</button>
          </form>
        </div>
        <div class="card answers">
          <h3>Assistance en temps réel</h3>
          <p class="small muted">Réponses suggérées au Delivery Manager, sourcées.</p>
          <div class="stack">
            @for (a of answers(); track $index) {
              <div class="answer" [class.oos]="a.out_of_scope">
                <div class="q">« {{ a.question }} »</div>
                @if (a.out_of_scope) { <div class="pill red">Hors périmètre contractuel : {{ a.out_of_scope_terms.join(', ') }}</div> }
                <p>{{ a.answer }}</p>
                <div class="row small">
                  <span class="pill" [class.green]="a.confidence === 'high'" [class.amber]="a.confidence === 'medium'" [class.red]="a.confidence === 'low'">confiance {{ a.confidence }}</span>
                  @for (s of a.sources; track $index) { <span class="pill" [title]="s.excerpt">{{ s.label }}</span> }
                </div>
                @if (a.follow_up) { <div class="small faint">À vérifier : {{ a.follow_up }}</div> }
              </div>
            } @empty { <div class="empty">Les questions détectées apparaîtront ici.</div> }
            @if (thinking()) { <div class="small muted"><span class="spin"></span> Recherche en cours…</div> }
          </div>
        </div>
      </div>
      @if (m.minutes; as mn) {
        <div class="card minutes">
          <div class="card-head"><h3>Compte-rendu généré</h3><button class="sm" (click)="exportMinutes()">Télécharger (Word)</button></div>
          <p class="pre">{{ mn.summary }}</p>
          <div class="grid g3">
            <div><strong>Décisions</strong><ul>@for (d of mn.decisions; track $index) { <li>{{ d }}</li> }</ul></div>
            <div><strong>Actions</strong><ul>@for (a of mn.actions; track $index) { <li>{{ a.action }} — <em>{{ a.owner }}</em> {{ a.due }}</li> }</ul></div>
            <div><strong>Risques</strong><ul>@for (r of mn.risks; track $index) { <li>{{ r.title }}</li> }</ul></div>
          </div>
        </div>
      }
    }
  `,
  styles: `
    .start { margin: 14px 0 10px; }
    .import { border-top: 1px solid var(--border); padding-top: 10px; margin-top: 4px; }
    .head { margin-bottom: 12px; }
    .live { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 16px; }
    .lines { height: 440px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; margin: 10px 0; }
    .line { font-size: 13px; }
    .line .who { display: block; font-size: 12px; }
    .who { color: var(--muted); font-weight: 500; }
    .interim { opacity: .55; font-style: italic; }
    .answers .stack { max-height: 500px; overflow-y: auto; margin-top: 10px; gap: 10px; }
    .answer { border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .answer.oos { border-left-color: var(--red); }
    .q { font-weight: 600; }
    .minutes { margin-top: 16px; }
    .minutes ul { padding-left: 18px; font-size: 13px; }
    .pulse { animation: pulse 1.2s infinite; }
    @keyframes pulse { 50% { opacity: .3; } }
  `,
})
export class MeetingsTab {
  private api = inject(Api);
  private router = inject(Router);
  perimeterId = input.required<string>();

  protected meetings = signal<any[]>([]);
  protected active = signal<any>(null);
  protected title = signal('Comité de suivi');
  protected lines = signal<Line[]>([]);
  protected answers = signal<Answer[]>([]);
  protected draft = signal('');
  protected speaker = signal('Client');
  protected interim = signal('');
  protected listening = signal(false);
  protected thinking = signal(false);
  protected ending = signal(false);
  protected demoRunning = signal(false);
  protected speakers = ['Client', 'Directeur SI client', 'Responsable métier', 'Delivery Manager', 'Architecte'];
  protected speechSupported = typeof window !== 'undefined' && ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window);
  private recognition: any = null;
  private demoTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    effect(() => this.loadMeetings());
    inject(DestroyRef).onDestroy(() => { this.recognition?.stop(); clearTimeout(this.demoTimer); });
  }

  loadMeetings() { this.api.meetings(this.perimeterId()).subscribe(m => this.meetings.set(m)); }

  create() {
    this.api.createMeeting(this.perimeterId(), this.title()).subscribe(m => { this.lines.set([]); this.answers.set([]); this.active.set(m); });
  }

  open(m: any) {
    this.api.meeting(m.id).subscribe(full => {
      this.active.set(full);
      this.answers.set([]);
      this.lines.set((full.transcript || '').split('\n').filter((l: string) => l.trim()).map((l: string) => {
        const i = l.indexOf(':');
        return i > 0 && i < 40 ? { speaker: l.slice(0, i), text: l.slice(i + 1).trim() } : { speaker: '', text: l };
      }));
    });
  }

  close() { this.recognition?.stop(); this.listening.set(false); clearTimeout(this.demoTimer); this.demoRunning.set(false); this.active.set(null); this.loadMeetings(); }

  say() {
    if (!this.draft().trim()) return;
    this.push({ speaker: this.speaker(), text: this.draft() });
    this.draft.set('');
  }

  private push(line: Line) {
    this.lines.update(l => [...l, line]);
    this.thinking.set(true);
    this.api.segment(this.active().id, line.speaker, line.text).subscribe({
      next: res => { this.thinking.set(false); if (res.question_detected) this.answers.update(a => [res, ...a]); },
      error: () => this.thinking.set(false),
    });
  }

  playDemo() {
    this.demoRunning.set(true);
    let i = 0;
    const next = () => {
      if (i >= DEMO.length || !this.active()) { this.demoRunning.set(false); return; }
      this.push(DEMO[i++]);
      this.demoTimer = setTimeout(next, 2200);
    };
    next();
  }

  toggleMic() {
    if (this.listening()) { this.recognition?.stop(); this.listening.set(false); return; }
    const Ctor = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;
    this.recognition = new Ctor();
    this.recognition.lang = 'fr-FR';
    this.recognition.continuous = true;
    this.recognition.interimResults = true;
    this.recognition.onresult = (event: any) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const text = event.results[i][0].transcript.trim();
        if (event.results[i].isFinal && text) this.push({ speaker: this.speaker(), text });
        else interim += text;
      }
      this.interim.set(interim);
    };
    this.recognition.onend = () => { if (this.listening()) this.recognition.start(); };
    this.recognition.start();
    this.listening.set(true);
  }

  protected importing = signal(false);
  protected importError = signal('');

  importTranscript(el: HTMLInputElement) {
    const file = el.files?.[0];
    if (!file) return;
    this.importing.set(true);
    this.importError.set('');
    this.api.importTranscript(this.perimeterId(), file).subscribe({
      next: res => this.router.navigate(['/runs', res.run.id]),
      error: e => { this.importing.set(false); el.value = ''; this.importError.set(e.error?.detail ?? 'Import impossible'); },
    });
  }

  exportMinutes() { this.api.download(`/api/meetings/${this.active().id}/exports/minutes.docx`, 'compte-rendu.docx'); }

  end() {
    this.recognition?.stop();
    this.listening.set(false);
    this.ending.set(true);
    this.api.endMeeting(this.active().id).subscribe({
      next: run => this.router.navigate(['/runs', run.id]),
      error: () => this.ending.set(false),
    });
  }
}

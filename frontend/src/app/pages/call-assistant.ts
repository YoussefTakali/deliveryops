import { HttpClient } from '@angular/common/http';
import { Component, DestroyRef, ElementRef, inject, signal, viewChild } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { Api, PortfolioItem } from '../api';
import { Auth } from '../auth';
import { Desktop } from '../desktop';
import { HALLUCINATIONS, VoiceSegmenter } from '../voice-segmenter';

type Speaker = 'Moi' | 'Interlocuteur';
interface Line { kind: 'line'; id: number; speaker: Speaker; text: string; speakerId?: number; }
/** A distinct voice discovered on the call (see the diarizer in the component). */
interface VoiceSpeaker { id: number; label: string; centroid: number[]; count: number; custom: boolean; }
/** Cosine similarity above which a new utterance is judged to be the same voice as a known speaker.
 *  Depends on the embedding backend the server used: the trained WeSpeaker model separates speakers
 *  at a lower cosine than the numpy log-mel fingerprint fallback. */
const SAME_SPEAKER: Record<string, number> = { wespeaker: 0.5, fbank: 0.82 };
interface Sugg {
  kind: 'sugg'; id: number; prompt: string; target: string; shown: string;
  streaming: boolean; route: string; sources: any[]; error?: boolean; abort?: AbortController;
}
type Item = Line | Sugg;

const PREFS_KEY = 'dm.copilot.prefs';

/** How long the other side must stay silent before we answer their turn (ms). Transcription is
 *  immediate; this only delays the AI suggestion so we don't reply in the middle of a question. */
const TURN_PAUSE_MS = 1200;

/** Does this utterance sound like a finished thought? Ends on sentence punctuation → yes; ends on a
 *  dangling conjunction/preposition → no (they're mid-sentence); otherwise long enough to stand alone.
 *  Used to wait a little longer before answering when the turn still sounds unfinished. */
function looksComplete(text: string): boolean {
  const s = text.trim();
  if (!s) return false;
  if (/[.?!…]$/.test(s)) return true;
  if (/\b(et|ou|mais|donc|car|puis|que|qui|de|des|du|à|au|aux|pour|avec|dans|sur|the|and|or|but|to|of|for|with|in|on)$/i.test(s)) return false;
  return s.split(/\s+/).length >= 8;
}

/** Pure hesitation / filler / bare acknowledgement — no real content to answer. Kept deliberately
 *  narrow so greetings and thanks still reach the small-talk reply path. */
const FILLER = /^(?:(?:e+u+h*|h+e+u+h*|h+u+h+|b+a+h+|b+e+n+|b+e+h+|e+h+|a+h+|o+h+|h+e+i+n+|h+u+m+|h+m+|m+h+|m+m+h?|u+h+|u+m+|e+r+m+|mhm|bon|alors|donc|voil[àa]|ok(?:ay)?|d[’']?accord|ou+a+i+s?|ouaip|oui+|non+|well|so|like|right|yeah|yep|nope)[\s.,!?…-]*)+$/i;
const GREETING = /\b(bonjour|bonsoir|salut|coucou|hello|hi|hey|merci|thanks?|thank you|[çc]a va|comment (?:allez|vas)|au revoir|bye|[àa] bient[ôo]t|enchant[ée])\b/i;
const _FILLER_WORD = /^(?:e+u+h*|h+e+u+h*|h+u+m+|h+m+|m+h+|m+m+h?|b+a+h+|ben|beh|eh+|ah+|oh+|hein|ok|okay|u+h+|u+m+|e+r+m+|ouais|voila|voilà|bon|alors|donc|like|well|so|right|mhm)$/i;

/** Should the copilot actually produce a suggestion for this (aggregated) utterance? We answer real
 *  questions, greetings/thanks and substantive statements — but NOT hesitations, filler or tiny
 *  fragments, which is what made it invent random questions. Transcription still shows regardless. */
function worthAnswering(text: string): boolean {
  const s = text.trim();
  if (s.length < 2 || FILLER.test(s)) return false;
  if (s.includes('?') || GREETING.test(s)) return true;
  const content = s.replace(/[.,!?…]/g, ' ').split(/\s+/).filter(w => w.length >= 2 && !_FILLER_WORD.test(w));
  return content.length >= 3;  // enough real words to be a genuine statement/request
}

// ---- speaker diarization vector helpers (fingerprints are L2-normalised, so dot == cosine) ----
const SPEAKER_COLORS = ['#7cc4ff', '#ffb86b', '#8be28b', '#e28be2', '#e2d18b', '#8be2d1', '#ff9c9c'];
/** Cap on distinct auto-detected voices — beyond this we stop creating new ones (avoids runaway
 *  splitting on a noisy call; the user can still rename whoever is detected). */
const MAX_SPEAKERS = 8;
function dot(a: number[], b: number[]): number {
  let s = 0; const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) s += a[i] * b[i];
  return s;
}
function normalize(v: number[]): number[] {
  let m = 0; for (const x of v) m += x * x;
  m = Math.sqrt(m) || 1;
  return v.map(x => x / m);
}
/** Running-mean update of a centroid with a new (normalised) sample, re-normalised. */
function mixCentroid(centroid: number[], sample: number[], count: number): number[] {
  return normalize(centroid.map((c, i) => (c * count + (sample[i] ?? 0)) / (count + 1)));
}

/**
 * Live call copilot. Listens to your mic ("Moi") and to the call's system audio ("Interlocuteur" —
 * the other participants, captured via getDisplayMedia loopback), transcribes each utterance with
 * local Whisper (POST /api/live/transcribe) and, whenever the other side finishes speaking, streams a
 * suggested reply (POST /api/live/respond, SSE). No project is required: the backend answers small
 * talk conversationally, general questions with web results, and only uses a project's documents
 * when one is selected AND the question is about it. Rendered as a frameless transparent overlay in
 * the desktop app (?overlay=1, see desktop/src/main.js openAssistant), as a normal page otherwise.
 */
@Component({
  selector: 'app-call-assistant',
  template: `
    <div class="shell" [class.overlay]="overlay" [class.compact]="compact()">
      <header class="bar">
        <div class="brand">
          <span class="live" [class.on]="micOn() || callOn()"></span>
          <span>Copilote d'appel</span>
          @if (projectName(); as p) { <span class="tag">{{ p }}</span> }
        </div>
        <div class="win">
          <button class="icon" title="Réglages" (click)="settingsOpen.set(!settingsOpen())" [class.active]="settingsOpen()">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>
          </button>
          <button class="icon" [title]="compact() ? 'Afficher la transcription' : 'Masquer la transcription'" (click)="compact.set(!compact())">
            <svg viewBox="0 0 24 24">@if (compact()) { <path d="M4 6h16M4 12h16M4 18h10"/> } @else { <path d="M4 12h16"/> }</svg>
          </button>
          @if (overlay) {
            <button class="icon close" title="Fermer" (click)="closeWindow()"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
          }
        </div>
      </header>

      @if (settingsOpen()) {
        <section class="settings">
          <label>Projet <span class="opt">optionnel</span>
            <select [value]="perimeterId()" (change)="setPerimeter($any($event.target).value)">
              <option value="">Aucun — conversation libre</option>
              @for (p of perimeters(); track p.id) { <option [value]="p.id">{{ p.client }} · {{ p.name }}</option> }
            </select>
          </label>
          <label>Contexte de l'appel <span class="opt">optionnel</span>
            <textarea rows="3" [value]="context()" (input)="setContext($any($event.target).value)"
              placeholder="Ex. : point mensuel avec le DSI d'ACME, renouvellement du contrat TMA, ton cordial…"></textarea>
          </label>
          <label>Langue de l'appel
            <select [value]="language()" (change)="setLanguage($any($event.target).value)">
              <option value="">Détection automatique</option><option value="fr">Français</option><option value="en">English</option><option value="ar">العربية</option>
            </select>
          </label>
          <label class="check"><input type="checkbox" [checked]="diarizeOn()" (change)="toggleDiarize()" />
            Distinguer les intervenants (qui parle) sur l'appel</label>
          @if (diarizeOn()) {
            <label>Participants attendus <span class="opt">optionnel</span>
              <input [value]="participants()" (input)="setParticipants($any($event.target).value)"
                placeholder="Ex. : Zaid, Sarah, Client — pré-nomme les voix détectées, dans l'ordre" />
            </label>
          }
          @if (desktop.available) {
            <label class="check"><input type="checkbox" [checked]="teamsOn()" (change)="toggleTeams()" />
              Lire les sous-titres Teams (noms exacts des intervenants)
              @if (teamsStatus() === 'reading') { <span class="pill on">actif</span> }
              @else if (teamsStatus() === 'searching') { <span class="pill">recherche de la réunion…</span> }
              @else if (teamsStatus() === 'unavailable') { <span class="pill">activez les sous-titres dans Teams</span> }
            </label>
          }
          <p class="hint">Astuce : utilisez un casque pour que votre micro ne capte pas la voix de vos interlocuteurs.
            Enregistrer d'autres participants peut nécessiter leur consentement selon votre juridiction et votre entreprise.</p>
        </section>
      }

      <div class="sources-bar">
        <button class="src" [class.on]="micOn()" [disabled]="micBusy()" (click)="toggleMic()">
          <svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>
          <span>Moi</span>
          <span class="meter"><span [style.transform]="'scaleX(' + micLevel() + ')'"></span></span>
        </button>
        <button class="src" [class.on]="callOn()" [disabled]="callBusy()" (click)="toggleCall()">
          <svg viewBox="0 0 24 24"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/></svg>
          <span>Interlocuteurs</span>
          <span class="meter"><span [style.transform]="'scaleX(' + callLevel() + ')'"></span></span>
        </button>
      </div>

      @if (diarizeOn() && speakers().length && !compact()) {
        <div class="speakers" title="Voix détectées sur l'appel — cliquez pour renommer">
          @for (s of speakers(); track s.id) {
            <span class="spk" [style.borderColor]="speakerColor(s.id)">
              <span class="dot" [style.background]="speakerColor(s.id)"></span>
              <input [value]="s.label" (change)="renameSpeaker(s.id, $any($event.target).value)" aria-label="Nom du participant" />
            </span>
          }
        </div>
      }

      @if (notice()) { <div class="notice" [class.err]="noticeIsError()">{{ notice() }}</div> }

      <div class="feed" #feed>
        @for (it of items(); track it.id) {
          @if (it.kind === 'line') {
            @if (!compact()) {
              <div class="line" [class.me]="it.speaker === 'Moi'"><span class="who" [style.color]="it.speaker === 'Moi' ? null : speakerColor(it.speakerId)">{{ it.speaker === 'Moi' ? 'Vous' : speakerLabel(it.speakerId) }}</span>{{ it.text }}</div>
            }
          } @else {
            <div class="sugg" [class.err]="it.error">
              <div class="sugg-head">
                <span class="route">{{ routeLabel(it.route) }}</span>
                @if (!it.streaming && !it.error) { <button class="copy" (click)="copy(it)" title="Copier">Copier</button> }
              </div>
              <p class="text">{{ it.shown }}@if (it.streaming || it.shown.length < it.target.length) { <span class="caret"></span> }</p>
              @if (it.sources.length && !it.streaming) {
                <div class="chips">
                  @for (s of it.sources; track $index) {
                    @if (s.type === 'web') { <a class="chip" [href]="s.ref" target="_blank" rel="noopener" [title]="s.excerpt">{{ s.label }}</a> }
                    @else { <span class="chip" [title]="s.excerpt">{{ s.label }}</span> }
                  }
                </div>
              }
            </div>
          }
        } @empty {
          <div class="empty">
            <p>Activez <strong>Moi</strong> et <strong>Interlocuteurs</strong> : dès que votre interlocuteur termine sa phrase, une suggestion de réponse s'affiche ici en temps réel.</p>
            <p class="faint">Vous pouvez aussi poser une question directement ci-dessous.</p>
          </div>
        }
      </div>

      <form class="ask" (submit)="$event.preventDefault(); askDirect()">
        <input [value]="draft()" (input)="draft.set($any($event.target).value)" placeholder="Demander à l'assistant…" />
        <button type="button" class="icon" title="Suggérer une réponse à la dernière phrase" [disabled]="!lastTheirs()" (click)="suggestAgain()">
          <svg viewBox="0 0 24 24"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></svg>
        </button>
        <button class="send" [disabled]="!draft().trim()"><svg viewBox="0 0 24 24"><path d="M4 12h15M13 6l6 6-6 6"/></svg></button>
      </form>
      @if (perimeterId() && items().length) {
        <button class="minutes" [disabled]="minutesBusy()" (click)="generateMinutes()">@if (minutesBusy()) { <span class="spin"></span> } Générer le compte-rendu</button>
      }
    </div>
  `,
  styles: `
    :host { display: block; height: 100vh; }
    .shell {
      --glass: color-mix(in srgb, var(--surface) 82%, transparent);
      height: 100%; display: flex; flex-direction: column; gap: 8px; padding: 10px 12px 12px;
      background: var(--bg); color: var(--text); box-sizing: border-box;
    }
    .shell.overlay {
      margin: 6px; height: calc(100% - 12px); border-radius: 16px; background: var(--glass);
      backdrop-filter: blur(18px) saturate(1.2); box-shadow: 0 8px 32px rgba(0,0,0,.35);
    }
    .bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; -webkit-app-region: drag; user-select: none; min-height: 28px; }
    .brand { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; min-width: 0; }
    .tag { font-weight: 500; font-size: 11px; padding: 1px 8px; border-radius: 999px; background: var(--surface-2); color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 150px; }
    .live { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); opacity: .5; flex: none; }
    .live.on { background: #e5484d; opacity: 1; animation: pulse 1.4s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(229,72,77,.5); } 50% { box-shadow: 0 0 0 5px rgba(229,72,77,0); } }
    .win { display: flex; gap: 2px; -webkit-app-region: no-drag; }
    .icon { width: 28px; height: 28px; padding: 0; border: 0; border-radius: 8px; background: transparent; color: var(--muted); display: grid; place-items: center; cursor: pointer; transition: background .15s, color .15s; }
    .icon:hover, .icon.active { background: var(--surface-2); color: var(--text); }
    .icon.close:hover { background: #e5484d; color: #fff; }
    .icon:disabled { opacity: .35; cursor: default; }
    .icon svg, .src svg, .send svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .settings { display: flex; flex-direction: column; gap: 8px; padding: 10px; border-radius: 12px; background: var(--surface-2); animation: drop .18s ease-out; }
    .settings label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
    .settings select, .settings textarea, .settings input[type=text], .settings label:not(.check) > input { font: inherit; font-size: 13px; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 6px 8px; resize: vertical; }
    .settings label.check { flex-direction: row; align-items: center; gap: 8px; cursor: pointer; flex-wrap: wrap; }
    .settings .pill { font-size: 10px; padding: 1px 6px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }
    .settings .pill.on { color: var(--green, #8be28b); border-color: currentColor; }
    .speakers { display: flex; flex-wrap: wrap; gap: 6px; padding: 2px 0 6px; }
    .spk { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--border); border-left-width: 3px; border-radius: 8px; padding: 2px 6px; background: var(--surface-2); }
    .spk .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
    .spk input { font: inherit; font-size: 11px; font-weight: 600; color: var(--text); background: transparent; border: none; width: 92px; padding: 2px; }
    .spk input:focus { outline: none; background: var(--surface); border-radius: 4px; }
    .opt { font-size: 10px; opacity: .7; }
    .hint { margin: 0; font-size: 11px; color: var(--muted); line-height: 1.4; }
    .sources-bar { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
    .src { display: flex; align-items: center; gap: 6px; padding: 7px 10px; border-radius: 10px; border: 0; background: var(--surface-2); color: var(--muted); font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; transition: background .2s, color .2s; }
    .src.on { background: color-mix(in srgb, var(--accent) 18%, transparent); color: var(--text); }
    .src .meter { flex: 1; height: 4px; border-radius: 2px; background: color-mix(in srgb, var(--muted) 25%, transparent); overflow: hidden; }
    .src .meter span { display: block; height: 100%; background: var(--accent); transform-origin: left; transition: transform .08s linear; }
    .src:not(.on) .meter span { transform: scaleX(0) !important; }
    .notice { font-size: 12px; padding: 6px 10px; border-radius: 8px; background: var(--surface-2); color: var(--muted); }
    .notice.err { color: #e5484d; }
    .feed { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; padding-right: 2px; scroll-behavior: smooth; }
    .line { font-size: 12px; color: var(--muted); line-height: 1.45; animation: rise .2s ease-out; }
    .line .who { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; margin-right: 6px; color: var(--accent); }
    .line.me .who { color: var(--muted); }
    .sugg { padding: 9px 11px; border-radius: 12px; background: color-mix(in srgb, var(--accent) 12%, var(--surface)); animation: rise .22s ease-out; }
    .sugg.err { background: var(--surface-2); }
    .sugg-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px; }
    .route { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--accent); }
    .copy { border: 0; background: transparent; color: var(--muted); font-size: 11px; cursor: pointer; padding: 0; }
    .copy:hover { color: var(--text); }
    .text { margin: 0; font-size: 14px; line-height: 1.5; white-space: pre-wrap; }
    .caret { display: inline-block; width: 7px; height: 14px; margin-left: 2px; vertical-align: -2px; border-radius: 2px; background: var(--accent); animation: blink 1s steps(2) infinite; }
    @keyframes blink { 50% { opacity: 0; } }
    .chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
    .chip { font-size: 10px; padding: 1px 7px; border-radius: 999px; background: var(--surface-2); color: var(--muted); max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .empty { margin: auto 0; text-align: center; font-size: 12px; color: var(--muted); padding: 0 8px; }
    .empty .faint { opacity: .7; }
    .ask { display: flex; gap: 4px; align-items: center; padding: 4px 4px 4px 12px; border-radius: 12px; background: var(--surface-2); }
    .ask input { flex: 1; min-width: 0; border: 0; background: transparent; color: var(--text); font: inherit; font-size: 13px; outline: none; }
    .send { width: 30px; height: 30px; border: 0; border-radius: 9px; background: var(--accent); color: #fff; display: grid; place-items: center; cursor: pointer; }
    .send:disabled { opacity: .35; cursor: default; }
    .minutes { align-self: center; font-size: 12px; border: 0; background: transparent; color: var(--muted); cursor: pointer; }
    .minutes:hover { color: var(--text); }
    @keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
    @keyframes drop { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
    @media (prefers-reduced-motion: reduce) { .line, .sugg, .settings, .live.on, .caret { animation: none; } }
  `,
})
export class CallAssistantPage {
  private api = inject(Api);
  private http = inject(HttpClient);
  private auth = inject(Auth);
  protected desktop = inject(Desktop);
  private feed = viewChild<ElementRef<HTMLElement>>('feed');

  protected overlay = new URLSearchParams(window.location.search).has('overlay');
  protected perimeters = signal<PortfolioItem[]>([]);
  private prefs = readPrefs();
  protected perimeterId = signal<string>(this.prefs.perimeterId ?? '');
  protected context = signal<string>(this.prefs.context ?? '');
  protected language = signal<string>(this.prefs.language ?? '');
  protected settingsOpen = signal(false);
  protected compact = signal(false);
  protected items = signal<Item[]>([]);
  protected draft = signal('');
  protected micOn = signal(false);
  protected callOn = signal(false);
  protected micBusy = signal(false);
  protected callBusy = signal(false);
  protected micLevel = signal(0);
  protected callLevel = signal(0);
  protected notice = signal('');
  protected noticeIsError = signal(false);
  protected minutesBusy = signal(false);
  protected lastTheirs = signal('');

  private seq = 0;
  private mic: VoiceSegmenter | null = null;
  private call: VoiceSegmenter | null = null;
  private raf = 0;
  private pendingTheirs: { text: string; at: number; sugg: Sugg | null } | null = null;
  /** The other side's current turn, accumulated across pauses until they finish (see onTheirTurn). */
  private turnBuf = '';
  private turnTimer: ReturnType<typeof setTimeout> | null = null;
  /** Voices discovered on the call (diarization), and optional names to pre-fill in order. */
  protected speakers = signal<VoiceSpeaker[]>([]);
  protected participants = signal<string>(this.prefs.participants ?? '');
  protected diarizeOn = signal<boolean>(this.prefs.diarize ?? true);
  private speakerModel = 'wespeaker';  // which embedding backend the server used (sets the threshold)
  /** Who spoke last on the call and when — used as a continuity prior so one person talking in two
   *  consecutive segments (or shifting tone) isn't split into two "speakers". */
  private lastSpeaker: { id: number; at: number } | null = null;
  /** Teams live captions as the speaker source (exact names from Teams' own UI, desktop app only). */
  protected teamsOn = signal<boolean>(this.prefs.teams ?? true);
  protected teamsStatus = signal<'off' | 'searching' | 'reading' | 'unavailable'>('off');
  private teamsTimer: ReturnType<typeof setInterval> | null = null;
  private teamsSeen = new Set<string>();
  private teamsBusy = false;

  constructor() {
    if (this.overlay) {
      document.documentElement.style.background = 'transparent';
      document.body.style.background = 'transparent';
    }
    this.api.portfolio().subscribe({ next: p => this.perimeters.set(p), error: () => {} });
    inject(DestroyRef).onDestroy(() => {
      this.mic?.stop();
      this.call?.stop();
      if (this.turnTimer) clearTimeout(this.turnTimer);
      if (this.teamsTimer) clearInterval(this.teamsTimer);
      cancelAnimationFrame(this.raf);
      for (const it of this.items()) if (it.kind === 'sugg') it.abort?.abort();
    });
  }

  protected projectName() {
    const p = this.perimeters().find(x => x.id === this.perimeterId());
    return p ? p.name : '';
  }

  protected routeLabel(route: string) {
    return ({ project: 'Projet', web: 'Web', chat: 'Suggestion', ask: 'Réponse' } as Record<string, string>)[route] ?? 'Suggestion';
  }

  protected setPerimeter(v: string) { this.perimeterId.set(v); this.savePrefs(); }
  protected setContext(v: string) { this.context.set(v); this.savePrefs(); }
  protected setLanguage(v: string) { this.language.set(v); this.savePrefs(); }
  protected setParticipants(v: string) { this.participants.set(v); this.savePrefs(); }
  protected toggleDiarize() { this.diarizeOn.update(v => !v); this.savePrefs(); }
  private savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify({ perimeterId: this.perimeterId(), context: this.context(), language: this.language(), participants: this.participants(), diarize: this.diarizeOn(), teams: this.teamsOn() })); } catch { /* storage unavailable */ }
  }

  // ---- speaker diarization (who is speaking) ----

  /** Attribute an utterance to a speaker, conservatively — the previous version over-split voices on
   *  short, noisy chunks, which is what "confuses the speakers". Rules, in order:
   *   - a short (<1.3s) or missing/weak voiceprint can't identify anyone → it's whoever just spoke;
   *   - a confident match to a known voice → that voice (and its print is sharpened);
   *   - a borderline match → don't split: keep it with the last speaker (or the closest voice);
   *   - only a clearly-different voice from a solid, long-enough sample creates a NEW speaker.
   *  New speakers are pre-named from the "participants" list when provided. */
  private assignSpeaker(emb: number[] | null | undefined, durationS?: number | null): number | undefined {
    const now = Date.now();
    const list = this.speakers();
    const threshold = SAME_SPEAKER[this.speakerModel] ?? 0.5;
    const recentLast = this.lastSpeaker && now - this.lastSpeaker.at < 8000 ? this.lastSpeaker.id : -1;
    const reliable = !!emb && emb.length > 0 && !(durationS != null && durationS < 1.3);

    if (!reliable) {
      // Can't trust this sample to identify anyone — keep it with whoever was just speaking.
      if (recentLast >= 0) { this.lastSpeaker = { id: recentLast, at: now }; return recentLast; }
      return undefined;  // nothing established yet: leave it neutral rather than guess
    }

    let best = -1, bestSim = -1;
    for (const s of list) {
      if (!s.centroid.length) continue;
      const sim = dot(s.centroid, emb!);
      if (sim > bestSim) { bestSim = sim; best = s.id; }
    }
    if (best >= 0 && bestSim >= threshold) {              // confident: known voice
      this.updateCentroid(best, emb!);
      this.lastSpeaker = { id: best, at: now };
      return best;
    }
    if (best >= 0 && bestSim >= threshold - 0.12) {       // borderline: don't split
      const id = recentLast >= 0 ? recentLast : best;
      this.lastSpeaker = { id, at: now };
      return id;
    }
    if (list.length < MAX_SPEAKERS) return this.newSpeaker(emb!);  // clearly a new, distinct voice
    const id = recentLast >= 0 ? recentLast : (best >= 0 ? best : undefined);  // cap reached: stay put
    if (id !== undefined) this.lastSpeaker = { id, at: now };
    return id;
  }

  private updateCentroid(id: number, emb: number[]) {
    this.speakers.update(ss => ss.map(s => s.id === id
      ? { ...s, count: s.count + 1, centroid: s.centroid.length ? mixCentroid(s.centroid, emb, s.count) : normalize(emb) }
      : s));
  }

  private newSpeaker(emb: number[]): number {
    const list = this.speakers();
    const id = list.length + 1;
    const names = this.participants().split(/[,;\n]/).map(n => n.trim()).filter(Boolean);
    const named = names[list.length];
    this.speakers.update(ss => [...ss, { id, label: named || `Interlocuteur ${id}`, centroid: normalize(emb), count: 1, custom: !!named }]);
    this.lastSpeaker = { id, at: Date.now() };
    return id;
  }

  /** Speaker id for a name coming from Teams captions (created on first sight, keeps its colour). */
  private speakerByName(name: string): number {
    const found = this.speakers().find(s => s.label.toLowerCase() === name.toLowerCase());
    if (found) return found.id;
    const id = this.speakers().length + 1;
    this.speakers.update(ss => [...ss, { id, label: name, centroid: [], count: 0, custom: true }]);
    return id;
  }

  // ---- Teams live captions as the speaker source (desktop app only) ----

  protected toggleTeams() {
    this.teamsOn.update(v => !v);
    this.savePrefs();
    if (this.callOn()) { this.stopTeamsPoll(); this.startTeamsPoll(); }
  }

  /** Poll Teams' captions pane every second. Teams attributes every caption to a participant by
   *  name, so this gives exact "who said what" — no voice separation, no confusion when the same
   *  person speaks twice or changes tone. Requires the desktop app and live captions turned on in
   *  the meeting; otherwise we silently keep the audio-based transcript + diarization. */
  private startTeamsPoll() {
    const bridge = this.desktop.automation;
    if (!this.teamsOn() || !bridge || typeof bridge.readTeamsCaptions !== 'function') { this.teamsStatus.set('off'); return; }
    this.teamsSeen.clear();
    this.teamsStatus.set('searching');
    this.teamsTimer = setInterval(() => void this.pollTeams(), 1000);
  }

  private stopTeamsPoll() {
    if (this.teamsTimer) clearInterval(this.teamsTimer);
    this.teamsTimer = null;
    this.teamsStatus.set('off');
  }

  private async pollTeams() {
    if (this.teamsBusy) return;
    this.teamsBusy = true;
    try {
      const res = await this.desktop.automation!.readTeamsCaptions({});
      if (!res.available) { this.teamsStatus.set('searching'); return; }
      if (!res.captions.length) { if (this.teamsStatus() !== 'reading') this.teamsStatus.set('unavailable'); return; }
      this.teamsStatus.set('reading');
      for (const c of res.captions) {
        const key = `${c.speaker}|${c.text}`;
        if (this.teamsSeen.has(key)) continue;
        this.teamsSeen.add(key);
        if (this.teamsSeen.size > 400) this.teamsSeen = new Set([...this.teamsSeen].slice(-200));
        const mine = this.isSelfName(c.speaker);
        if (mine) { this.addLine('Moi', c.text); continue; }
        this.addLine('Interlocuteur', c.text, this.speakerByName(c.speaker));
        this.onTheirTurn(c.text);
      }
    } catch {
      this.teamsStatus.set('searching');
    } finally {
      this.teamsBusy = false;
    }
  }

  /** Teams labels the local user's captions with their own name (or "You"/"Vous"). */
  private isSelfName(name: string): boolean {
    const n = name.trim().toLowerCase();
    if (n === 'you' || n === 'vous' || n === 'moi' || n === 'me') return true;
    const me = (this.auth.username || '').split('@')[0].replace(/[._-]+/g, ' ').trim().toLowerCase();
    return me.length >= 3 && (n === me || me.includes(n) || n.includes(me));
  }

  protected speakerLabel(id: number | undefined): string {
    if (id === undefined) return 'Eux';
    return this.speakers().find(s => s.id === id)?.label ?? `Interlocuteur ${id}`;
  }

  protected speakerColor(id: number | undefined): string {
    if (id === undefined) return 'var(--faint)';
    return SPEAKER_COLORS[(id - 1) % SPEAKER_COLORS.length];
  }

  protected renameSpeaker(id: number, name: string) {
    const label = name.trim();
    this.speakers.update(ss => ss.map(s => s.id === id ? { ...s, label: label || `Interlocuteur ${id}`, custom: !!label } : s));
  }

  // ---- audio capture ----

  async toggleMic() {
    if (this.micOn()) { this.mic?.stop(); this.mic = null; this.micOn.set(false); this.micLevel.set(0); return; }
    this.micBusy.set(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      this.mic = new VoiceSegmenter(stream, b => this.transcribe(b, 'Moi'), l => this.micLevel.set(l));
      this.mic.start();
      this.micOn.set(true);
      this.clearNotice();
    } catch (e: any) {
      this.showNotice(`Micro inaccessible : ${e?.message ?? e}. Vérifiez l'autorisation micro de Windows pour cette application.`, true);
    } finally {
      this.micBusy.set(false);
    }
  }

  async toggleCall() {
    if (this.callOn()) {
      this.call?.stop(); this.call = null; this.callOn.set(false); this.callLevel.set(0);
      if (this.turnTimer) { clearTimeout(this.turnTimer); this.turnTimer = null; } this.turnBuf = '';
      this.stopTeamsPoll();
      return;
    }
    this.callBusy.set(true);
    try {
      // video:true is required by Chromium to unlock system-audio capture; the video track is dropped.
      const display: MediaStream = await (navigator.mediaDevices as any).getDisplayMedia({ video: true, audio: true });
      display.getVideoTracks().forEach(t => t.stop());
      const audio = display.getAudioTracks();
      if (!audio.length) {
        this.showNotice(this.desktop.available
          ? "Aucun son système reçu."
          : "Aucun son reçu : choisissez « Écran entier » (ou l'onglet Teams) et cochez « Partager l'audio du système ».", true);
        return;
      }
      audio[0].addEventListener('ended', () => { this.call?.stop(); this.call = null; this.callOn.set(false); });
      this.speakers.set([]);  // fresh voices for a new call
      this.call = new VoiceSegmenter(new MediaStream(audio), b => this.transcribe(b, 'Interlocuteur'), l => this.callLevel.set(l));
      this.call.start();
      this.startTeamsPoll();
      this.callOn.set(true);
      this.clearNotice();
    } catch (e: any) {
      if (e?.name !== 'NotAllowedError') this.showNotice(`Capture de l'appel impossible : ${e?.message ?? e}`, true);
    } finally {
      this.callBusy.set(false);
    }
  }

  private async transcribe(blob: Blob, speaker: Speaker) {
    const form = new FormData();
    form.append('file', blob, 'segment.webm');
    const lang = this.language();
    // Ask for a voice fingerprint only on the call stream (the other participants), so we can tell
    // them apart. "Moi" is always the user — no diarization needed.
    const diarize = speaker === 'Interlocuteur' && this.diarizeOn();
    // The call context (names, products, acronyms typed in the settings) also primes the transcriber.
    const hint = (this.context() || '').trim().slice(0, 300);
    const qs = [lang ? `language=${lang}` : '', diarize ? 'diarize=true' : '', hint ? `hint=${encodeURIComponent(hint)}` : ''].filter(Boolean).join('&');
    try {
      const res = await firstValueFrom(this.http.post<{ text: string; speaker?: number[] | null; speaker_model?: string; duration_s?: number | null }>(`/api/live/transcribe${qs ? `?${qs}` : ''}`, form));
      const text = (res.text || '').trim();
      if (!text || HALLUCINATIONS.test(text)) return;
      if (speaker === 'Moi' && this.isEcho(text)) return;
      // While Teams captions are being read, they are the source of truth for the other side (exact
      // names, no voice clustering); the audio transcript would only duplicate them.
      if (speaker === 'Interlocuteur' && this.teamsStatus() === 'reading') return;
      if (res.speaker_model) this.speakerModel = res.speaker_model;
      const speakerId = diarize ? this.assignSpeaker(res.speaker, res.duration_s) : undefined;
      this.addLine(speaker, text, speakerId);
      if (speaker === 'Interlocuteur') this.onTheirTurn(text);
    } catch (e: any) {
      if (e?.status === 503) this.showNotice('Transcription indisponible sur le serveur (modèle vocal en cours de chargement ?). Réessai automatique…', true);
      else if (e?.status === 429) this.showNotice('Trop de requêtes — ralentissement temporaire.', true);
    }
  }

  /** Without a headset the mic also hears the other side through the speakers; drop a "Moi" line
   *  that mostly repeats what "Interlocuteur" said in the last few seconds. */
  private isEcho(text: string): boolean {
    const words = (s: string) => new Set(s.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, '').split(/\s+/).filter(w => w.length > 2));
    const mine = words(text);
    if (mine.size < 3) return false;
    const recent = this.items().filter((i): i is Line => i.kind === 'line' && i.speaker === 'Interlocuteur').slice(-3);
    return recent.some(l => {
      const theirs = words(l.text);
      let common = 0;
      mine.forEach(w => { if (theirs.has(w)) common++; });
      return common / mine.size > 0.6;
    });
  }

  private addLine(speaker: Speaker, text: string, speakerId?: number) {
    const line: Line = { kind: 'line', id: ++this.seq, speaker, text, speakerId };
    this.items.update(list => [...list, line].slice(-120));
    this.scrollToEnd();
  }

  /** A new transcribed segment from the other side. The line is already shown; here we only decide
   *  WHEN to answer. We wait for them to actually finish their thought instead of firing on every
   *  pause: each segment is appended to the current turn and answered only after they stay silent
   *  for a short beat (a bit longer when the sentence still sounds unfinished). If a suggestion for
   *  an earlier partial is still on screen, it's dropped and replaced with one for the full turn. */
  private onTheirTurn(text: string) {
    this.turnBuf = this.turnBuf ? `${this.turnBuf} ${text}` : text;
    if (this.turnTimer) clearTimeout(this.turnTimer);
    const p = this.pendingTheirs;
    if (p?.sugg && (p.sugg.streaming || p.sugg.shown.length < 40)) {
      p.sugg.abort?.abort();
      this.items.update(list => list.filter(i => i !== p.sugg));
      this.pendingTheirs = null;
    }
    const delay = looksComplete(this.turnBuf) ? TURN_PAUSE_MS : TURN_PAUSE_MS + 900;
    this.turnTimer = setTimeout(() => this.flushTurn(), delay);
  }

  /** The other side has stayed quiet long enough — answer the aggregated turn, unless it was just
   *  hesitation/filler (in which case we stay silent instead of inventing something to say). */
  private flushTurn() {
    this.turnTimer = null;
    const utterance = this.turnBuf.trim();
    this.turnBuf = '';
    if (!utterance || !worthAnswering(utterance)) return;
    this.lastTheirs.set(utterance);
    const sugg = this.respond(utterance, 'Interlocuteur', 'reply');
    this.pendingTheirs = { text: utterance, at: Date.now(), sugg };
  }

  protected suggestAgain() { const t = this.lastTheirs(); if (t) this.respond(t, 'Interlocuteur', 'reply'); }

  protected askDirect() {
    const q = this.draft().trim();
    if (!q) return;
    // The user is taking over — drop any pending auto-reply to the other side's unfinished turn.
    if (this.turnTimer) { clearTimeout(this.turnTimer); this.turnTimer = null; } this.turnBuf = '';
    this.draft.set('');
    this.addLine('Moi', q);
    this.respond(q, 'Moi', 'ask');
  }

  // ---- streamed suggestions ----

  private respond(utterance: string, speaker: Speaker, mode: 'reply' | 'ask'): Sugg {
    const sugg: Sugg = { kind: 'sugg', id: ++this.seq, prompt: utterance, target: '', shown: '', streaming: true, route: mode === 'ask' ? 'ask' : 'chat', sources: [], abort: new AbortController() };
    this.items.update(list => [...list, sugg]);
    this.scrollToEnd();
    this.startReveal();
    void this.stream(sugg, utterance, speaker, mode);
    return sugg;
  }

  private async stream(sugg: Sugg, utterance: string, speaker: Speaker, mode: 'reply' | 'ask') {
    // Pass the diarized name ("Zaid" / "Interlocuteur 2") as the speaker so the copilot knows who
    // said what on a multi-participant call, not just "Interlocuteur".
    const transcript = this.items().filter((i): i is Line => i.kind === 'line').slice(-16)
      .map(l => ({ speaker: l.speaker === 'Moi' ? 'Moi' : this.speakerLabel(l.speakerId), text: l.text }));
    try {
      const headers = await this.auth.currentHeaders();
      const resp = await fetch('/api/live/respond', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify({ utterance, speaker, mode, transcript, context: this.context() || null, perimeter_id: this.perimeterId() || null, language: this.language() || null }),
        signal: sugg.abort!.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(resp.status === 429 ? 'Trop de requêtes, patientez quelques secondes.' : `Assistant indisponible (${resp.status})`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let sep: number;
        while ((sep = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, sep).split('\n').find(l => l.startsWith('data: '));
          buf = buf.slice(sep + 2);
          if (!line) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === 'meta') { if (mode !== 'ask') sugg.route = ev.route; sugg.sources = ev.sources ?? []; }
          else if (ev.type === 'delta') sugg.target += ev.text;
          else if (ev.type === 'done') {
            // Nothing worth saying (filler/hesitation): drop the whole suggestion silently.
            if (ev.noop || /^noop[.!\s]*$/i.test((ev.answer ?? sugg.target).trim())) {
              sugg.abort?.abort();
              this.items.update(list => list.filter(i => i !== sugg));
              if (this.pendingTheirs?.sugg === sugg) this.pendingTheirs = null;
              return;
            }
            if (!sugg.target) sugg.target = ev.answer ?? '';
          }
        }
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      sugg.error = true;
      if (!sugg.target) sugg.target = e?.message ?? 'Flux interrompu';
    } finally {
      sugg.streaming = false;
      this.touch();
    }
  }

  /** Reveals streamed text progressively (a few characters per frame, faster when far behind)
   *  instead of dumping each network chunk at once, so the answer reads as it's being written. */
  private startReveal() {
    if (this.raf) return;
    const step = () => {
      let pending = false;
      for (const it of this.items()) {
        if (it.kind !== 'sugg') continue;
        // Don't flash the "NOOP" sentinel while it's still streaming — it gets dropped on done.
        if (it.streaming && /^n(o(o(p)?)?)?$/i.test(it.target.trim())) { pending = true; continue; }
        const gap = it.target.length - it.shown.length;
        if (gap > 0) {
          it.shown = it.target.slice(0, it.shown.length + Math.max(1, Math.ceil(gap / 6)));
          pending = true;
        }
        if (it.streaming) pending = true;
      }
      this.touch();
      this.scrollToEnd(true);
      this.raf = pending ? requestAnimationFrame(step) : 0;
    };
    this.raf = requestAnimationFrame(step);
  }

  private touch() { this.items.update(list => [...list]); }

  private scrollToEnd(instant = false) {
    const el = this.feed()?.nativeElement;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (!nearBottom && instant) return; // don't yank the user back down while they scroll up to read
    requestAnimationFrame(() => el.scrollTo({ top: el.scrollHeight, behavior: instant ? 'auto' : 'smooth' }));
  }

  protected copy(s: Sugg) { void navigator.clipboard?.writeText(s.target).catch(() => {}); }

  private showNotice(msg: string, isError: boolean) { this.notice.set(msg); this.noticeIsError.set(isError); }
  private clearNotice() { this.notice.set(''); }

  protected closeWindow() {
    const bridge = (window as any).deliveryDesktop;
    if (bridge?.windowClose) bridge.windowClose(); else window.close();
  }

  /** With a project selected, save the conversation as a meeting of that project and generate the
   *  minutes through the existing meeting pipeline. */
  protected async generateMinutes() {
    const lines = this.items().filter((i): i is Line => i.kind === 'line');
    if (!lines.length || !this.perimeterId()) return;
    this.minutesBusy.set(true);
    try {
      const m = await firstValueFrom(this.api.createMeeting(this.perimeterId(), `Appel ${new Date().toLocaleString('fr-FR')}`));
      for (const l of lines) {
        await firstValueFrom(this.http.post(`/api/meetings/${m.id}/segments`, { speaker: l.speaker === 'Moi' ? 'Moi' : 'Client', text: l.text, auto_answer: false }));
      }
      await firstValueFrom(this.api.endMeeting(m.id));
      this.showNotice('Compte-rendu généré — retrouvez-le dans les réunions du projet.', false);
    } catch (e: any) {
      this.showNotice(e?.error?.detail ?? 'Impossible de générer le compte-rendu', true);
    } finally {
      this.minutesBusy.set(false);
    }
  }
}

function readPrefs(): { perimeterId?: string; context?: string; language?: string; participants?: string; diarize?: boolean; teams?: boolean } {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'); } catch { return {}; }
}

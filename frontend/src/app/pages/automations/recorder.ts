import { HttpClient } from '@angular/common/http';
import { Component, DestroyRef, NgZone, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { Desktop, RecorderState } from '../../desktop';
import { HALLUCINATIONS, VoiceSegmenter } from '../../voice-segmenter';

/**
 * The floating REC bar the desktop recorder opens at the top of the screen while the user performs
 * the task (desktop/src/recorder.js openControlWindow: 400×76, transparent, always on top, excluded
 * from screen capture so it never appears in its own keyframes). Shows elapsed time and what has
 * been captured, drives pause/resume/stop/cancel, and — when narration is on — records the mic,
 * transcribes each spoken sentence locally (POST /api/live/transcribe) and hands the text to the
 * recorder as timestamped narration for the AI.
 */
@Component({
  selector: 'app-recorder-control',
  template: `
    <div class="bar" [class.paused]="state().state === 'paused'">
      <span class="dot" [class.on]="state().state === 'recording'"></span>
      <div class="info">
        <strong>{{ state().state === 'paused' ? 'En pause' : state().state === 'stopping' ? 'Finalisation…' : 'Enregistrement' }} · {{ clock() }}</strong>
        <span class="small">
          {{ state().keyframes ?? 0 }} capture(s) · {{ state().events ?? 0 }} action(s)
          @if (narrationOn()) { · <span [class.hot]="micLevel() > 0.15"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg> {{ state().narration ?? 0 }}</span> }
          @if (state().hook === false) { · <span class="warn" title="Les clics et touches ne sont pas captés sur ce poste">images seules</span> }
        </span>
      </div>
      <div class="actions">
        @if (state().state === 'paused') {
          <button class="icon" title="Reprendre" (click)="resume()"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></button>
        } @else {
          <button class="icon" title="Pause (avant d'afficher une information sensible)" [disabled]="state().state !== 'recording'" (click)="pause()"><svg viewBox="0 0 24 24"><path d="M8 5h3v14H8zM13 5h3v14h-3z"/></svg></button>
        }
        <button class="stop" title="Terminer et analyser" [disabled]="state().state === 'stopping'" (click)="stop()"><span></span> Terminer</button>
        <button class="icon ghost" title="Annuler l'enregistrement" (click)="cancel()"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
      </div>
    </div>
  `,
  styles: `
    :host { display: block; height: 100vh; -webkit-app-region: drag; user-select: none; }
    .bar {
      margin: 6px; height: calc(100% - 12px); box-sizing: border-box; display: flex; align-items: center; gap: 12px; padding: 0 10px 0 16px;
      border-radius: 14px; background: color-mix(in srgb, var(--surface) 90%, transparent); color: var(--text);
      box-shadow: 0 6px 24px rgba(0,0,0,.35); font-size: 13px; transition: background .2s;
    }
    .bar.paused { background: color-mix(in srgb, var(--surface-2) 94%, transparent); }
    .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--muted); flex: none; }
    .dot.on { background: #e5484d; animation: pulse 1.4s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(229,72,77,.55); } 50% { box-shadow: 0 0 0 6px rgba(229,72,77,0); } }
    .info { flex: 1; min-width: 0; display: flex; flex-direction: column; line-height: 1.3; }
    .info strong { font-variant-numeric: tabular-nums; white-space: nowrap; }
    .small { font-size: 11px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .hot { color: var(--accent); }
    .warn { color: #d97706; }
    .actions { display: flex; align-items: center; gap: 4px; -webkit-app-region: no-drag; }
    .icon { width: 32px; height: 32px; padding: 0; border: 0; border-radius: 9px; background: var(--surface-2); color: var(--text); display: grid; place-items: center; cursor: pointer; }
    .icon.ghost { background: transparent; color: var(--muted); }
    .icon:hover { filter: brightness(1.1); }
    .icon svg { width: 15px; height: 15px; fill: currentColor; stroke: currentColor; stroke-width: 0; }
    .icon.ghost svg { fill: none; stroke-width: 2; stroke-linecap: round; }
    .stop { display: flex; align-items: center; gap: 6px; height: 32px; padding: 0 12px; border: 0; border-radius: 9px; background: #e5484d; color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
    .stop span { width: 9px; height: 9px; border-radius: 2px; background: #fff; }
    button:disabled { opacity: .45; cursor: default; }
    @media (prefers-reduced-motion: reduce) { .dot.on { animation: none; } }
  `,
})
export class RecorderControlPage {
  private desktop = inject(Desktop);
  private http = inject(HttpClient);
  private zone = inject(NgZone);

  protected state = signal<RecorderState>({ state: 'recording' });
  protected narrationOn = signal(new URLSearchParams(window.location.search).get('narration') === '1');
  protected micLevel = signal(0);
  protected clock = computed(() => {
    const s = this.state().elapsed_s ?? 0;
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  });
  private mic: VoiceSegmenter | null = null;

  constructor() {
    document.documentElement.style.background = 'transparent';
    document.body.style.background = 'transparent';
    const bridge = this.desktop.automation;
    const stopListening = bridge?.onRecorderState(s => this.zone.run(() => {
      this.state.set(s);
      if (s.state === 'paused' || s.state === 'stopping' || s.state === 'idle') this.muteMic();
      else if (s.state === 'recording' && this.narrationOn() && !this.mic) void this.startMic();
    }));
    void bridge?.recorderState().then(s => this.state.set(s)).catch(() => {});
    if (this.narrationOn()) void this.startMic();
    inject(DestroyRef).onDestroy(() => { stopListening?.(); this.muteMic(); });
  }

  protected pause() { void this.desktop.automation?.pauseRecording().then(s => this.state.set(s)); }
  protected resume() { void this.desktop.automation?.resumeRecording().then(s => this.state.set(s)); }
  protected stop() { this.muteMic(); void this.desktop.automation?.stopRecording(); }
  protected cancel() { this.muteMic(); void this.desktop.automation?.cancelRecording(); }

  private async startMic() {
    if (this.mic) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      this.mic = new VoiceSegmenter(stream, (blob) => void this.transcribe(blob), (l) => this.micLevel.set(l));
      this.mic.start();
    } catch {
      this.narrationOn.set(false); // no mic / permission refused: keep recording without narration
    }
  }

  private muteMic() {
    this.mic?.stop();
    this.mic = null;
    this.micLevel.set(0);
  }

  /** Each spoken sentence → local Whisper → timestamped narration in the recording bundle. */
  private async transcribe(blob: Blob) {
    const spokenAt = Date.now();
    const form = new FormData();
    form.append('file', blob, 'narration.webm');
    try {
      const res = await firstValueFrom(this.http.post<{ text: string }>('/api/live/transcribe', form));
      const text = (res.text || '').trim();
      if (text && !HALLUCINATIONS.test(text)) await this.desktop.automation?.addNarration(text, spokenAt);
    } catch { /* one lost sentence must never interrupt the recording */ }
  }
}

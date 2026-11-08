import { Injectable, NgZone, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { Desktop, RecorderState } from '../desktop';
import { AutomationApi, errorText } from './automation-api';
import { draftFromCompile, notesFromCompile, saveNotes } from './drafts';
import { RecordingBundle } from './models';

export type RecordingPhase = 'idle' | 'recording' | 'uploading' | 'analyzing' | 'compiling' | 'error';

/**
 * Main-window side of "Enregistrer une tâche": when the desktop recorder stops, main.js tells this
 * window a bundle is ready; we take it, upload it (POST /recordings, multipart meta + JPEG frames),
 * analyze it (vision model — failures don't block), compile it into a workflow, save the draft and
 * open the editor. Root singleton so it works whichever page is open when the recording ends.
 */
@Injectable({ providedIn: 'root' })
export class RecordingProcessor {
  private api = inject(AutomationApi);
  private desktop = inject(Desktop);
  private router = inject(Router);
  private zone = inject(NgZone);

  readonly phase = signal<RecordingPhase>('idle');
  readonly message = signal('');
  readonly recorder = signal<RecorderState>({ state: 'idle' });
  private busy = false;

  constructor() {
    const bridge = this.desktop.automation;
    if (!bridge || this.desktop.windowRole !== 'main') return;
    bridge.onRecorderState(s => this.zone.run(() => {
      this.recorder.set(s);
      if (s.state === 'recording' || s.state === 'paused') this.phase.set('recording');
      else if (this.phase() === 'recording' && s.state === 'idle') this.phase.set(s.cancelled ? 'idle' : this.phase());
    }));
    bridge.onRecordingReady(() => this.zone.run(() => void this.process()));
    // A recording that finished while this page was reloading is still waiting in main.
    setTimeout(() => void this.process(true), 3000);
  }

  async start(narration: boolean) {
    const bridge = this.desktop.automation;
    if (!bridge) throw new Error('Nécessite l’application bureau DeliveryOps.');
    this.message.set('');
    const s = await bridge.startRecording({ narration });
    this.recorder.set(s);
    this.phase.set('recording');
    return s;
  }

  async process(quiet = false) {
    const bridge = this.desktop.automation;
    if (!bridge || this.busy) return;
    this.busy = true;
    try {
      const bundle: RecordingBundle | null = await bridge.takeRecording();
      if (!bundle) { if (!quiet && this.phase() === 'recording') this.phase.set('idle'); return; }
      void this.router.navigateByUrl('/automations/new?tab=record');
      if (!bundle.keyframes.length && !bundle.events.length && !bundle.narration.length) {
        throw new Error('L’enregistrement est vide : rien n’a été capturé.');
      }
      this.phase.set('uploading');
      this.message.set(`${bundle.keyframes.length} capture(s), ${bundle.events.length} événement(s), ${bundle.narration.length} commentaire(s)`);
      const meta = {
        duration_s: bundle.duration_s,
        events: bundle.events,
        narration: bundle.narration,
        keyframes: bundle.keyframes.map((k, i) => ({ t: k.t, window_title: k.window_title, process: k.process, field: `f${i}` })),
        screen: bundle.screen ?? null,
        input_hook: bundle.hook,
      };
      const frames = bundle.keyframes.map(k => new Blob([k.jpeg as BlobPart], { type: 'image/jpeg' }));
      const { recording_id } = await this.api.uploadRecording(meta, frames);

      this.phase.set('analyzing');
      let summary = '';
      try { summary = (await this.api.analyzeRecording(recording_id)).summary ?? ''; }
      catch { /* the compiler degrades to events + window titles + narration */ }

      this.phase.set('compiling');
      // Faithful replay is the default for a recording: it reproduces exactly what was done, in order,
      // each target found by image + accessibility + AI and verified. (The AI "compile into a smart,
      // data-driven workflow" path stays available from the editor for those who want it.)
      const compiled = await this.api.faithfulRecording(recording_id);
      const created = await this.api.create(draftFromCompile(compiled, { kind: 'recording', recording_id }));
      saveNotes(created.id, notesFromCompile(compiled, { analysis_summary: summary }));
      this.phase.set('idle');
      this.message.set('');
      await this.router.navigateByUrl(`/automations/${created.id}`);
    } catch (err) {
      this.phase.set('error');
      this.message.set(errorText(err));
    } finally {
      this.busy = false;
    }
  }

  reset() { this.phase.set('idle'); this.message.set(''); }
}

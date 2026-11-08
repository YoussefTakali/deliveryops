// Energy-based voice-activity segmentation shared by the call copilot (pages/call-assistant.ts) and
// the automation recorder's narration (pages/automations/recorder.ts).

/** Whisper's classic hallucinations on silence/music — drop these transcripts. */
export const HALLUCINATIONS = /^(sous-titr|merci d'avoir regardé|merci de votre attention\.?$|thank you for watching|thanks for watching|\.+$|musique|♪)/i;

/** Minimum voiced time in a segment before it's worth transcribing — shorter is a cough/click/edge
 *  that only makes Whisper hallucinate. */
const MIN_VOICED_MS = 300;

/** Splits a live audio stream into one complete recording per utterance, using a simple
 *  energy-based voice-activity detector (adaptive noise floor). Each emitted Blob is a standalone
 *  webm file — unlike MediaRecorder.start(timeslice), whose chunks after the first have no container
 *  header and can't be decoded on their own (which is why the old 4-second chunks after the first
 *  were silently undecodable). Cutting on pauses also gives the copilot natural conversation turns. */
export class VoiceSegmenter {
  private ctx!: AudioContext;
  private analyser!: AnalyserNode;
  private buf!: Float32Array<ArrayBuffer>;
  private timer: ReturnType<typeof setInterval> | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private segStart = 0;
  private voiced = false;
  private voiceMs = 0;
  private lastVoice = 0;
  private noise = 0.004;

  constructor(private stream: MediaStream, private onSegment: (b: Blob) => void, private onLevel: (l: number) => void) {}

  start() {
    this.ctx = new AudioContext();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.ctx.createMediaStreamSource(this.stream).connect(this.analyser);
    this.buf = new Float32Array(this.analyser.fftSize);
    this.openRecorder();
    this.timer = setInterval(() => this.tick(), 50);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.closeRecorder(this.voiced && this.voiceMs >= MIN_VOICED_MS);
    this.stream.getTracks().forEach(t => t.stop());
    void this.ctx?.close().catch(() => {});
  }

  private tick() {
    this.analyser.getFloatTimeDomainData(this.buf);
    let sum = 0;
    for (let i = 0; i < this.buf.length; i++) sum += this.buf[i] * this.buf[i];
    const rms = Math.sqrt(sum / this.buf.length);
    this.onLevel(Math.min(1, rms * 14));
    const now = performance.now();
    if (!this.voiced) this.noise = this.noise * 0.97 + rms * 0.03;
    if (rms > Math.max(0.01, this.noise * 2.8)) {
      this.voiceMs += 50;
      this.lastVoice = now;
      if (this.voiceMs >= 150) this.voiced = true; // ignore clicks/pops shorter than 150ms
    }
    const len = now - this.segStart;
    // Only emit a segment that actually contains enough speech: a sub-300ms blip is a cough, a click
    // or a hesitation edge, and feeding it to Whisper is what produces phantom phrases ("Merci…").
    if (this.voiced && (now - this.lastVoice > 750 || len > 14000)) this.cut(this.voiceMs >= MIN_VOICED_MS);
    else if (!this.voiced && len > 8000) this.cut(false); // drop long silences, keep blobs small
  }

  private cut(emit: boolean) {
    this.closeRecorder(emit);
    this.openRecorder();
  }

  private openRecorder() {
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
    const rec = new MediaRecorder(this.stream, { mimeType });
    const chunks: Blob[] = [];
    rec.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    this.recorder = rec;
    this.chunks = chunks;
    this.segStart = performance.now();
    this.voiced = false;
    this.voiceMs = 0;
    rec.start();
  }

  private closeRecorder(emit: boolean) {
    const rec = this.recorder;
    if (!rec || rec.state === 'inactive') return;
    const chunks = this.chunks;
    rec.onstop = () => { if (emit && chunks.length) this.onSegment(new Blob(chunks, { type: rec.mimeType })); };
    rec.stop();
    this.recorder = null;
  }
}

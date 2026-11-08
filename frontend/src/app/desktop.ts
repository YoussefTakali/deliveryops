import { Injectable } from '@angular/core';

/** Shape of the bridge injected by the Electron preload (desktop/src/preload.js). */
interface DesktopBridge {
  isDesktop: true;
  platform: string;
  roots(): Promise<string[]>;
  overview(): Promise<any>;
  scan(root: string, query?: string, limit?: number): Promise<any>;
  readText(path: string): Promise<any>;
  readBase64(path: string): Promise<{ name: string; base64: string; size: number }>;
  pickFolder(): Promise<{ added: string | null; roots: string[] }>;
  pickFile(): Promise<{ added: string | null }>;
  listDrives(): Promise<string[]>;
  grantFullDisk(): Promise<{ roots: string[]; fullDisk: true }>;
  revokeFullDisk(): Promise<{ roots: string[]; fullDisk: false }>;
  fullDiskStatus(): Promise<boolean>;
  onFileChanged(cb: (path: string) => void): () => void;
  getPcAccountHint(): Promise<string | null>;
  openCallAssistant(route?: string): Promise<boolean>;
  // native OS notifications (optional: older desktop builds don't expose them)
  notify?(payload: DesktopNotifyPayload): Promise<{ shown: boolean; actions: boolean }>;
  notifyCapabilities?(): Promise<{ supported: boolean; actions: boolean }>;
  onNotificationOpen?(cb: (e: { id: string; link: string }) => void): () => void;
  onNotificationAction?(cb: (e: { id: string; action: string; link: string }) => void): () => void;
  // which Electron window this page runs in (older desktop builds: undefined → treat as main)
  windowRole?: 'main' | 'assistant' | 'recorder' | string;
  // Automatisations (optional: older desktop builds don't expose it)
  automation?: DesktopAutomationBridge;
  // Companion activity awareness (opt-in foreground-window sessions; desktop/src/activity.js)
  companion?: DesktopCompanionBridge;
}

export interface ActivitySlice { started_at: string; seconds: number; app: string; title: string; }
export interface DesktopCompanionBridge {
  start(): Promise<{ running: boolean; supported: boolean }>;
  stop(): Promise<{ running: boolean; supported: boolean }>;
  drain(): Promise<{ events: ActivitySlice[]; running: boolean }>;
  state(): Promise<{ running: boolean; supported: boolean; now: { app: string; title: string; seconds: number } | null }>;
  getBackground?(): Promise<{ closeToTray: boolean; openAtLogin: boolean; supported: boolean }>;
  setBackground?(patch: { closeToTray?: boolean; launchAtLogin?: boolean }): Promise<{ closeToTray: boolean; openAtLogin: boolean; supported: boolean }>;
}

export interface RecorderState {
  state: 'idle' | 'recording' | 'paused' | 'stopping';
  elapsed_s?: number; max_s?: number; keyframes?: number; max_keyframes?: number; events?: number; narration?: number;
  hook?: boolean; limit_reason?: string; cancelled?: boolean;
}

export interface UiElement { name: string; control_type: string; automation_id: string; }
export interface VisionCandidate { i: number; source: 'uia' | 'ocr'; label: string; control_type: string; x: number; y: number; w: number; h: number; enabled?: boolean; }
export interface VisionLocate {
  found: boolean; method?: 'image' | 'text'; confidence?: number; x?: number; y?: number; label?: string; ms: number;
  scene: { width: number; height: number; ocr: boolean; window: string | null; candidates: VisionCandidate[]; icons: { i: number; png_b64: string }[] };
}

/** deliveryDesktop.automation — see desktop/src/preload.js + automation-local.js/recorder.js/background.js. */
export interface DesktopAutomationBridge {
  capabilities(): Promise<{ hook: boolean; platform: string; loginItem: { openAtLogin: boolean; supported: boolean } }>;
  print(p: { bytes?: Uint8Array; name?: string; path?: string; printer?: string; copies?: number }): Promise<{ printed: boolean; method: string; printer: string }>;
  printers(): Promise<{ name: string; displayName: string; isDefault: boolean }[]>;
  saveFile(p: { bytes: Uint8Array; folder: string; name: string }): Promise<{ path: string }>;
  readFile(p: { path: string }): Promise<{ name: string; size: number; content_type: string; bytes: Uint8Array }>;
  listFiles(p: { folder: string; pattern?: string; modified_since_days?: number }): Promise<{ files: { path: string; name: string; size: number; modified: string }[]; count: number }>;
  open(p: { target: string }): Promise<{ opened: boolean }>;
  powershell(p: { script: string; timeout_s?: number; approvedHashes: string[] }): Promise<{ stdout: string; stderr: string; exit_code: number | null; timed_out?: boolean }>;
  // Interaction with other applications and websites (desktop/src/automation-interact.js).
  openApp(p: { app: string; args?: string; wait_window?: string; timeout_s?: number }): Promise<{ opened: boolean; window: string | null }>;
  windowAction(p: { title_contains: string; action?: string; timeout_s?: number }): Promise<{ found: boolean; window: string | null }>;
  click(p: { window: string; name?: string; automation_id?: string; control_type?: string; index?: number; double?: boolean; timeout_s?: number }): Promise<{ clicked: boolean; element: UiElement }>;
  type(p: { window: string; text: string; name?: string; automation_id?: string; clear?: boolean; submit?: boolean }): Promise<{ typed: boolean; element: UiElement | null }>;
  keys(p: { keys: string; window?: string }): Promise<{ sent: boolean }>;
  read(p: { window: string; name?: string; automation_id?: string; control_type?: string }): Promise<{ text: string; lines: string[] }>;
  readNotifications(p: { app_contains?: string; text_contains?: string }): Promise<{ notifications: { app: string; text: string }[]; count: number; latest: { app: string; text: string } | null }>;
  screenshot(p: { window?: string }): Promise<{ bytes: Uint8Array; name: string; content_type: string }>;
  browse(p: { url: string; actions?: unknown[]; show?: boolean; timeout_s?: number }): Promise<{ url: string; title: string; extracted: Record<string, unknown>; text: string; downloads: string[] }>;
  openBrowser(p: { url?: string }): Promise<{ opened: boolean }>;
  /** Interactive elements of a window (name / control type / automation id), for AI locator resolution. */
  dumpElements(p: { window: string; max?: number }): Promise<{ window: string; elements: UiElement[]; count: number }>;
  /** Vision (optional: older desktop builds don't expose it). Looks at the screen locally and tries the
   *  AI-free paths (image memory, exact label); always returns what it perceived for the AI decision. */
  visionLocate?(p: { target: string; key?: string; window?: string; element?: { name?: string; control_type?: string; automation_id?: string };
    patch?: string; point?: { x: number; y: number }; rel?: { x: number; y: number } }): Promise<VisionLocate>;
  visionClickAt?(p: { x: number; y: number; double?: boolean; right?: boolean; window?: string; learnKey?: string; label?: string;
    type?: boolean; text?: string; clear?: boolean; submit?: boolean }): Promise<{ done: boolean; x: number; y: number; learned: boolean }>;
  visionRead?(p: { window?: string }): Promise<{ text: string; lines: string[]; window: string | null; ocr: boolean }>;
  visionForget?(p: { key: string }): Promise<{ forgotten: boolean }>;
  /** Live captions of the running Teams meeting, straight from Teams' UI: exact speaker names. */
  readTeamsCaptions(p?: { window?: string }): Promise<{ available: boolean; window: string | null; captions: { speaker: string; text: string }[] }>;
  setActiveCount(count: number): Promise<{ active: number; tray: boolean }>;
  getLaunchAtLogin(): Promise<{ openAtLogin: boolean; supported: boolean }>;
  setLaunchAtLogin(enabled: boolean): Promise<{ openAtLogin: boolean; supported: boolean }>;
  startRecording(opts?: { narration?: boolean }): Promise<RecorderState & { hook: boolean }>;
  pauseRecording(): Promise<RecorderState>;
  resumeRecording(): Promise<RecorderState>;
  stopRecording(): Promise<{ duration_s: number; keyframes: number; events: number } | null>;
  cancelRecording(): Promise<boolean>;
  recorderState(): Promise<RecorderState>;
  addNarration(text: string, at: number): Promise<boolean>;
  takeRecording(): Promise<any | null>;
  onRecorderState(cb: (s: RecorderState) => void): () => void;
  onRecordingReady(cb: (info: { duration_s: number; keyframes: number; events: number }) => void): () => void;
}

export interface DesktopNotifyPayload { id: string; title: string; body: string; link: string; actions?: { id: string; label: string }[]; silent?: boolean; }

declare global {
  interface Window { deliveryDesktop?: DesktopBridge; }
}

/** Thin wrapper over window.deliveryDesktop. `available` is false in a plain browser, so pages can
 *  degrade gracefully (show "open the desktop app to use this"). */
@Injectable({ providedIn: 'root' })
export class Desktop {
  private get bridge(): DesktopBridge | undefined { return window.deliveryDesktop; }

  get available(): boolean { return !!this.bridge?.isDesktop; }
  get platform(): string { return this.bridge?.platform ?? 'web'; }

  roots() { return this.bridge!.roots(); }
  overview() { return this.bridge!.overview(); }
  scan(root: string, query = '', limit = 2000) { return this.bridge!.scan(root, query, limit); }
  readText(path: string) { return this.bridge!.readText(path); }
  readBase64(path: string) { return this.bridge!.readBase64(path); }
  pickFolder() { return this.bridge!.pickFolder(); }
  /** Grant access to a single file rather than a whole folder. */
  pickFile() { return this.bridge!.pickFile(); }
  listDrives() { return this.bridge!.listDrives(); }
  /** "Index everything on this PC" — grants every detected drive. Explicit, reversible. */
  grantFullDisk() { return this.bridge!.grantFullDisk(); }
  revokeFullDisk() { return this.bridge!.revokeFullDisk(); }
  fullDiskStatus() { return this.bridge!.fullDiskStatus(); }
  /** Continuous local indexing: fires with an absolute path whenever a file under an allowed root
   *  is added/changed. Returns an unsubscribe function — call it on component destroy. */
  onFileChanged(cb: (path: string) => void): () => void { return this.bridge!.onFileChanged(cb); }
  /** Best-effort email/UPN of the Windows account signed into this PC (Entra/hybrid-joined
   *  machines only). Null in the browser or when it can't be detected. */
  getPcAccountHint(): Promise<string | null> { return this.available ? this.bridge!.getPcAccountHint() : Promise.resolve(null); }
  openCallAssistant(route?: string) { return this.bridge!.openCallAssistant(route); }
  /** True when this desktop build can show native OS toasts (older installers can't). */
  get canNotify(): boolean { return !!this.bridge?.notify; }
  notify(payload: DesktopNotifyPayload) { return this.bridge!.notify!(payload); }
  notifyCapabilities() { return this.bridge?.notifyCapabilities ? this.bridge.notifyCapabilities() : Promise.resolve({ supported: false, actions: false }); }
  onNotificationOpen(cb: (e: { id: string; link: string }) => void): () => void { return this.bridge?.onNotificationOpen ? this.bridge.onNotificationOpen(cb) : () => {}; }
  onNotificationAction(cb: (e: { id: string; action: string; link: string }) => void): () => void { return this.bridge?.onNotificationAction ? this.bridge.onNotificationAction(cb) : () => {}; }

  /** Which Electron window this page is in ('main' in a browser too, for simplicity). */
  get windowRole(): string {
    if (!this.available) return 'main';
    const role = this.bridge?.windowRole;
    if (role) return role;
    return new URLSearchParams(window.location.search).has('overlay') ? 'overlay' : 'main';
  }
  /** Automation bridge (local steps, recorder, tray) — null in a browser or an older desktop build. */
  get automation(): DesktopAutomationBridge | null { return this.available ? this.bridge?.automation ?? null : null; }
  /** Companion activity bridge — null in a browser or an older desktop build. */
  get companion(): DesktopCompanionBridge | null { return this.available ? this.bridge?.companion ?? null : null; }

  /** Read a local file and turn it into a File for upload to the backend (ingest into knowledge). */
  async toFile(path: string, name: string): Promise<File> {
    const { base64 } = await this.readBase64(path);
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    return new File([bytes], name);
  }
}

'use strict';
// "Enregistrer une tâche" — records the user doing a repetitive task once, as EVIDENCE for the AI
// that compiles it into a semantic workflow (see the Automatisations contract, section 5). We never
// replay these clicks; the backend's vision model reads the keyframes + events + narration.
//
// What is captured while recording (and only while not paused):
//   - keyframes: primary-screen JPEGs (≤1280 px wide, quality 60) every 2 s *only if the screen
//     changed* (cheap diff on a 96×54 downscale), plus shortly after each click;
//   - global mouse clicks (position + button) and key EVENTS through uiohook-napi — never the
//     characters typed: typing is aggregated into "typed N chars", and only shortcuts (Ctrl+C,
//     Ctrl+V…) and special keys (Enter, Tab, Échap…) are named. If uiohook can't load, recording
//     degrades gracefully to timed keyframes + window titles;
//   - the foreground window's title/process (persistent PowerShell helper calling user32), sampled
//     every second and on every event;
//   - narration: the control window's page records the mic, transcribes each utterance through
//     /api/live/transcribe and hands us {text, at}.
// Limits: 10 minutes of active recording or 150 keyframes, whichever comes first (auto-stop).
//
// The control window is small, frameless, transparent, always-on-top and content-protected
// (excluded from screen captures, so it never shows up in its own keyframes).

const { spawn } = require('child_process');

const MAX_ACTIVE_MS = 10 * 60 * 1000;
const MAX_KEYFRAMES = 150;
const FRAME_INTERVAL_MS = 2000;
const WINDOW_SAMPLE_MS = 1000;
const TYPING_IDLE_FLUSH_MS = 2000;
const CLICK_FRAME_DELAY_MS = 600;
const DIFF_W = 96;
const DIFF_H = 54;
const MAX_EVENTS = 5000;
const RECORDER_TITLE = 'Enregistreur DeliveryOps';

let hookModule; // undefined = not tried yet, null = unavailable
function loadHook() {
  if (hookModule !== undefined) return hookModule;
  try {
    hookModule = require('uiohook-napi');
    if (!hookModule || !hookModule.uIOhook) hookModule = null;
  } catch (err) {
    console.warn('[recorder] uiohook-napi unavailable — clicks/keys will not be recorded:', err && err.message);
    hookModule = null;
  }
  return hookModule;
}

// ------------------------------------------------------------------ foreground window helper

const FG_SCRIPT = `
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public static class DmFg {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$uia = $true
try { Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes } catch { $uia = $false }
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  if ($line.StartsWith('e ')) {
    # Element under a clicked point — what the user SAW (label, type), so the compiler can target it by
    # name instead of coordinates. Password fields are never described.
    $out = @{}
    if ($uia) {
      try {
        $p = $line.Split(' ')
        $el = [System.Windows.Automation.AutomationElement]::FromPoint((New-Object System.Windows.Point ([double]$p[1]), ([double]$p[2])))
        if ($el -and -not $el.Current.IsPassword) {
          $n = [string]$el.Current.Name
          if ($n.Length -gt 120) { $n = $n.Substring(0, 120) }
          $out = @{ name = $n; control_type = ($el.Current.ControlType.ProgrammaticName -replace '^ControlType\\.', ''); automation_id = [string]$el.Current.AutomationId; class_name = [string]$el.Current.ClassName }
        }
      } catch {}
    }
    [Console]::Out.WriteLine(($out | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
    continue
  }
  $h = [DmFg]::GetForegroundWindow()
  $sb = New-Object System.Text.StringBuilder 512
  [void][DmFg]::GetWindowText($h, $sb, 512)
  [uint32]$procId = 0
  [void][DmFg]::GetWindowThreadProcessId($h, [ref]$procId)
  $name = ''
  try { $name = (Get-Process -Id $procId -ErrorAction Stop).ProcessName } catch {}
  [Console]::Out.WriteLine((@{ title = $sb.ToString(); process = $name; pid = [int]$procId } | ConvertTo-Json -Compress))
  [Console]::Out.Flush()
}`;

/** One long-lived powershell.exe answering "what's in the foreground?" on each stdin line — a
 *  fresh process per sample would cost ~300 ms and a console flash each time. */
class ForegroundProbe {
  constructor() {
    this.child = null;
    this.waiters = [];
    this.buffer = '';
    this.inflight = null;
  }

  start() {
    if (process.platform !== 'win32' || this.child) return;
    try {
      const encoded = Buffer.from(FG_SCRIPT, 'utf16le').toString('base64');
      this.child = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded], { windowsHide: true });
      this.child.stdout.on('data', (d) => this.onData(d.toString('utf8')));
      this.child.on('error', () => this.fail());
      this.child.on('exit', () => this.fail());
    } catch { this.child = null; }
  }

  onData(chunk) {
    this.buffer += chunk;
    let idx;
    while ((idx = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, idx).trim();
      this.buffer = this.buffer.slice(idx + 1);
      if (!line.startsWith('{')) continue;
      let info = null;
      try { info = JSON.parse(line); } catch { /* ignore */ }
      const w = this.waiters.shift();
      if (w) w(info);
    }
  }

  fail() {
    this.child = null;
    for (const w of this.waiters.splice(0)) w(null);
  }

  /** Resolves {title, process, pid} or null. Concurrent callers share one in-flight query. */
  query() {
    if (!this.child) return Promise.resolve(null);
    if (this.inflight) return this.inflight;
    this.inflight = new Promise((resolve) => {
      const timer = setTimeout(() => resolve(null), 1500);
      this.waiters.push((info) => { clearTimeout(timer); resolve(info); });
      try { this.child.stdin.write('q\n'); } catch { resolve(null); }
    }).finally(() => { this.inflight = null; });
    return this.inflight;
  }

  /** UI Automation element under a screen point: {name, control_type, automation_id, class_name} or null. */
  elementAt(x, y) {
    if (!this.child) return Promise.resolve(null);
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve(null), 1500);
      this.waiters.push((info) => { clearTimeout(timer); resolve(info && Object.keys(info).length ? info : null); });
      try { this.child.stdin.write(`e ${Math.round(x)} ${Math.round(y)}\n`); } catch { resolve(null); }
    });
  }

  stop() {
    const c = this.child;
    this.child = null;
    if (c) { try { c.stdin.end(); } catch { /* gone */ } setTimeout(() => { try { c.kill(); } catch { /* gone */ } }, 500); }
    for (const w of this.waiters.splice(0)) w(null);
  }
}

// ------------------------------------------------------------------ key naming (events only)

function buildKeyNames(UiohookKey) {
  const byCode = new Map();
  for (const [name, code] of Object.entries(UiohookKey || {})) if (typeof code === 'number' && !byCode.has(code)) byCode.set(code, name);
  return byCode;
}

const MODIFIER_NAMES = new Set(['Ctrl', 'CtrlRight', 'Alt', 'AltRight', 'Shift', 'ShiftRight', 'Meta', 'MetaRight', 'CapsLock', 'NumLock', 'ScrollLock']);
const NAMED_KEYS = { Enter: 'Entrée', NumpadEnter: 'Entrée', Tab: 'Tab', Escape: 'Échap', Delete: 'Suppr', PrintScreen: 'Impr écran' };
const FKEY = /^F([1-9]|1[0-9]|2[0-4])$/;
/** Keys that produce characters (letters, digits, punctuation, space, numpad digits/operators). */
function isTypingKey(name) {
  if (!name) return false;
  if (/^[A-Z0-9]$/.test(name) || name === 'Space') return true;
  if (/^Numpad([0-9]|Multiply|Add|Subtract|Decimal|Divide)$/.test(name)) return true;
  return ['Semicolon', 'Equal', 'Comma', 'Minus', 'Period', 'Slash', 'Backquote', 'BracketLeft', 'Backslash', 'BracketRight', 'Quote'].includes(name);
}

// ------------------------------------------------------------------ recorder

class Recorder {
  /**
   * deps: { BrowserWindow, desktopCapturer, screen, preloadPath, appUrl(): string,
   *         onComplete(bundle), onStateChange(state), onBeforeStart(), configurePopups? }
   */
  constructor(deps) {
    this.deps = deps;
    this.win = null;
    this.state = 'idle'; // idle | recording | paused
    this.probe = new ForegroundProbe();
    this.hookListenersAttached = false;
    this.keyNames = null;
    this.reset();
  }

  reset() {
    this.events = [];
    this.keyframes = [];
    this.narration = [];
    this.startedAt = 0;
    this.pausedAt = 0;
    this.pausedTotal = 0;
    this.lastDiff = null;
    this.lastWindow = { title: '', process: '' };
    this.typing = null;
    this.capturing = false;
    this.clickFrameTimer = null;
    this.timers = [];
    this.limitReason = '';
    this.hookActive = false;
  }

  capabilities() {
    return { hook: !!loadHook(), platform: process.platform };
  }

  activeMs(now = Date.now()) {
    if (!this.startedAt) return 0;
    const pausedNow = this.state === 'paused' ? now - this.pausedAt : 0;
    return Math.max(0, now - this.startedAt - this.pausedTotal - pausedNow);
  }

  t(now = Date.now()) { return Math.round(this.activeMs(now) / 100) / 10; }

  publicState() {
    return {
      state: this.state,
      elapsed_s: Math.round(this.activeMs() / 1000),
      max_s: MAX_ACTIVE_MS / 1000,
      keyframes: this.keyframes.length,
      max_keyframes: MAX_KEYFRAMES,
      events: this.events.length,
      narration: this.narration.length,
      hook: this.hookActive,
      limit_reason: this.limitReason,
    };
  }

  emitState() {
    const s = this.publicState();
    if (this.win && !this.win.isDestroyed()) this.win.webContents.send('recorder:state', s);
    this.deps.onStateChange?.(s);
  }

  start({ narration = true } = {}) {
    if (this.state !== 'idle') throw new Error('un enregistrement est déjà en cours');
    this.reset();
    const { screen } = this.deps;
    const primary = screen.getPrimaryDisplay();
    this.display = primary;
    this.screenInfo = {
      width: Math.round(primary.size.width * primary.scaleFactor),
      height: Math.round(primary.size.height * primary.scaleFactor),
      scale_factor: primary.scaleFactor,
    };
    const w = Math.min(1280, this.screenInfo.width);
    this.thumbSize = { width: w, height: Math.round(this.screenInfo.height * (w / this.screenInfo.width)) };
    this.startedAt = Date.now();
    this.state = 'recording';
    this.deps.onBeforeStart?.();
    this.openControlWindow(narration);
    this.probe.start();
    this.startHook();
    this.timers.push(setInterval(() => void this.captureFrame('timer'), FRAME_INTERVAL_MS));
    this.timers.push(setInterval(() => void this.sampleWindow(), WINDOW_SAMPLE_MS));
    this.timers.push(setInterval(() => this.tick(), 1000));
    setTimeout(() => void this.captureFrame('start'), 400);
    this.emitState();
    return { hook: this.hookActive, ...this.publicState() };
  }

  tick() {
    if (this.state === 'idle') return;
    if (this.typing && Date.now() - this.typing.last > TYPING_IDLE_FLUSH_MS) this.flushTyping();
    if (this.state === 'recording' && this.activeMs() >= MAX_ACTIVE_MS) { this.limitReason = 'duration'; void this.stop(); return; }
    this.emitState();
  }

  openControlWindow(narration) {
    const { BrowserWindow, screen, preloadPath, appUrl } = this.deps;
    const { workArea } = screen.getPrimaryDisplay();
    const width = 400;
    const height = 76;
    this.win = new BrowserWindow({
      width, height, x: Math.round(workArea.x + (workArea.width - width) / 2), y: workArea.y + 12,
      frame: false, transparent: true, backgroundColor: '#00000000', hasShadow: false, resizable: false,
      alwaysOnTop: true, skipTaskbar: false, focusable: true, fullscreenable: false, maximizable: false, minimizable: false,
      title: RECORDER_TITLE,
      webPreferences: { preload: preloadPath, contextIsolation: true, nodeIntegration: false, sandbox: false, additionalArguments: ['--dm-window=recorder'] },
    });
    this.win.setAlwaysOnTop(true, 'screen-saver');
    this.win.setContentProtection(true); // never appears in its own keyframes (nor in a shared screen)
    this.win.on('page-title-updated', (e) => e.preventDefault());
    this.deps.configurePopups?.(this.win.webContents);
    this.win.loadURL(`${appUrl()}/automations/recorder?overlay=1${narration ? '&narration=1' : ''}`);
    this.win.webContents.on('did-finish-load', () => this.emitState());
    this.win.on('closed', () => {
      this.win = null;
      // Closing the control window any other way (Alt+F4…) ends the recording rather than leaving
      // an invisible capture running.
      if (this.state !== 'idle') void this.stop();
    });
  }

  // ---------------------------------------------------------------- input hook

  startHook() {
    const mod = loadHook();
    if (!mod) { this.hookActive = false; return; }
    const { uIOhook, UiohookKey } = mod;
    if (!this.keyNames) this.keyNames = buildKeyNames(UiohookKey);
    if (!this.hookListenersAttached) {
      uIOhook.on('mousedown', (e) => this.onMouseDown(e));
      uIOhook.on('keydown', (e) => this.onKeyDown(e));
      this.hookListenersAttached = true;
    }
    try { uIOhook.start(); this.hookActive = true; } catch (err) {
      console.warn('[recorder] uiohook start failed:', err && err.message);
      this.hookActive = false;
    }
  }

  stopHook() {
    const mod = loadHook();
    if (mod && this.hookActive) { try { mod.uIOhook.stop(); } catch { /* ignore */ } }
    this.hookActive = false;
  }

  pushEvent(ev) {
    if (this.events.length >= MAX_EVENTS) return;
    this.events.push(ev);
  }

  inControlWindow(x, y) {
    if (!this.win || this.win.isDestroyed()) return false;
    let p = { x, y };
    try { if (this.deps.screen.screenToDipPoint) p = this.deps.screen.screenToDipPoint({ x, y }); } catch { /* non-Windows */ }
    const b = this.win.getBounds();
    return p.x >= b.x && p.x <= b.x + b.width && p.y >= b.y && p.y <= b.y + b.height;
  }

  onMouseDown(e) {
    if (this.state !== 'recording') return;
    if (this.inControlWindow(e.x, e.y)) return; // clicks on our own Pause/Stop buttons aren't part of the task
    this.flushTyping();
    const button = e.button === 2 ? 'right' : e.button === 3 ? 'middle' : 'left';
    const ev = {
      t: this.t(), type: 'click', x: e.x, y: e.y, button, clicks: e.clicks || 1,
      window_title: this.lastWindow.title, process: this.lastWindow.process,
    };
    this.pushEvent(ev);
    // Which button/field/menu was clicked (name + control type) — lets the AI rebuild the step as
    // "click « Envoyer » in Outlook" instead of a coordinate. Filled in when the lookup returns.
    if (this.probe && this.probe.elementAt) {
      void this.probe.elementAt(e.x, e.y).then((el) => { if (el) ev.element = el; }).catch(() => {});
    }
    // A full-resolution image patch of what was clicked (SikuliX-style ground truth): faithful replay
    // finds this exact patch on screen again, which is what makes apps with no accessibility names
    // (Citrix, remote desktop, custom-drawn UIs) replayable. Best-effort; captured off the click path.
    void this.capturePatch(e.x, e.y).then((patch) => { if (patch) ev.patch = patch; }).catch(() => {});
    void this.sampleWindow();
    if (!this.clickFrameTimer) {
      this.clickFrameTimer = setTimeout(() => { this.clickFrameTimer = null; void this.captureFrame('click'); }, CLICK_FRAME_DELAY_MS);
    }
  }

  /** Full-res crop (PATCH×PATCH physical px) around a click point, base64 PNG. Aligned to the same
   *  physical pixel grid the replay screenshot uses, so it can be matched back exactly. Best-effort. */
  async capturePatch(x, y) {
    const P = 72;
    try {
      const size = { width: this.screenInfo.width, height: this.screenInfo.height };
      const sources = await this.deps.desktopCapturer.getSources({ types: ['screen'], thumbnailSize: size });
      if (this.state !== 'recording') return null;
      const src = sources.find((s) => String(s.display_id) === String(this.display.id)) || sources[0];
      const img = src && src.thumbnail;
      if (!img || img.isEmpty()) return null;
      const rx = Math.max(0, Math.min(size.width - P, Math.round(x - P / 2)));
      const ry = Math.max(0, Math.min(size.height - P, Math.round(y - P / 2)));
      const patch = img.crop({ x: rx, y: ry, width: P, height: P });
      return patch.isEmpty() ? null : patch.toPNG().toString('base64');
    } catch {
      return null;
    }
  }

  onKeyDown(e) {
    if (this.state !== 'recording') return;
    const name = this.keyNames.get(e.keycode) || '';
    if (!name || MODIFIER_NAMES.has(name)) return;
    // Ctrl+Alt is AltGr on AZERTY/most EU layouts, i.e. typing a character (€, @, #…), not a shortcut.
    const altGr = e.ctrlKey && e.altKey;
    const shortcut = !altGr && (e.ctrlKey || e.metaKey || (e.altKey && !isTypingKey(name)) || (e.altKey && /^[A-Z0-9]$/.test(name)));
    if (shortcut) {
      this.flushTyping();
      const mods = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Maj', e.metaKey && 'Win'].filter(Boolean);
      this.pushEvent({ t: this.t(), type: 'shortcut', keys: [...mods, NAMED_KEYS[name] || name].join('+'), window_title: this.lastWindow.title, process: this.lastWindow.process });
      return;
    }
    if (isTypingKey(name) || name === 'Backspace') {
      const now = Date.now();
      if (!this.typing) this.typing = { t: this.t(now), chars: 0, deletions: 0, last: now, window_title: this.lastWindow.title, process: this.lastWindow.process };
      if (name === 'Backspace') this.typing.deletions++; else this.typing.chars++;
      this.typing.last = now;
      return;
    }
    if (NAMED_KEYS[name] || FKEY.test(name)) {
      this.flushTyping();
      const label = (e.shiftKey ? 'Maj+' : '') + (NAMED_KEYS[name] || name);
      this.pushEvent({ t: this.t(), type: 'key', key: label, window_title: this.lastWindow.title, process: this.lastWindow.process });
    }
    // Arrows/Home/End/PageUp… are navigation noise — not recorded.
  }

  flushTyping() {
    const ty = this.typing;
    this.typing = null;
    if (!ty || (!ty.chars && !ty.deletions)) return;
    this.pushEvent({ t: ty.t, type: 'typing', chars: ty.chars, deletions: ty.deletions,
      detail: `a saisi ${ty.chars} caractère(s)${ty.deletions ? `, ${ty.deletions} effacement(s)` : ''}`,
      window_title: ty.window_title, process: ty.process });
  }

  // ---------------------------------------------------------------- foreground window

  async sampleWindow() {
    if (this.state !== 'recording') return;
    const info = await this.probe.query();
    if (!info || this.state !== 'recording') return;
    const title = String(info.title || '');
    const proc = String(info.process || '');
    if (title === RECORDER_TITLE) return; // our own control window
    if (title === this.lastWindow.title && proc === this.lastWindow.process) return;
    this.flushTyping();
    this.lastWindow = { title, process: proc };
    if (title || proc) this.pushEvent({ t: this.t(), type: 'window', window_title: title, process: proc });
  }

  // ---------------------------------------------------------------- keyframes

  async captureFrame(reason) {
    if (this.state !== 'recording' || this.capturing) return;
    if (this.keyframes.length >= MAX_KEYFRAMES) return;
    this.capturing = true;
    try {
      const sources = await this.deps.desktopCapturer.getSources({ types: ['screen'], thumbnailSize: this.thumbSize });
      if (this.state !== 'recording') return;
      const src = sources.find((s) => String(s.display_id) === String(this.display.id)) || sources[0];
      const img = src && src.thumbnail;
      if (!img || img.isEmpty()) return;
      const small = img.resize({ width: DIFF_W, height: DIFF_H, quality: 'good' }).toBitmap();
      const changed = this.lastDiff ? changedPixels(small, this.lastDiff) : Infinity;
      const threshold = reason === 'timer' ? 6 : 1;
      if (changed < threshold) return;
      this.lastDiff = small;
      this.keyframes.push({
        t: this.t(), reason, window_title: this.lastWindow.title, process: this.lastWindow.process,
        jpeg: img.toJPEG(60),
      });
      if (this.keyframes.length >= MAX_KEYFRAMES) { this.limitReason = 'keyframes'; void this.stop(); return; }
      this.emitState();
    } catch (err) {
      console.warn('[recorder] capture failed:', err && err.message);
    } finally {
      this.capturing = false;
    }
  }

  // ---------------------------------------------------------------- controls

  pause() {
    if (this.state !== 'recording') return this.publicState();
    this.flushTyping();
    this.state = 'paused';
    this.pausedAt = Date.now();
    this.pushEvent({ t: this.t(), type: 'pause' });
    this.emitState();
    return this.publicState();
  }

  resume() {
    if (this.state !== 'paused') return this.publicState();
    this.pausedTotal += Date.now() - this.pausedAt;
    this.pausedAt = 0;
    this.state = 'recording';
    this.lastDiff = null; // force a fresh keyframe: the screen may have changed a lot while paused
    this.pushEvent({ t: this.t(), type: 'resume' });
    setTimeout(() => void this.captureFrame('resume'), 300);
    this.emitState();
    return this.publicState();
  }

  addNarration({ text, at } = {}) {
    const clean = String(text || '').trim();
    if (!clean || this.state === 'idle') return false;
    const when = Number(at) || Date.now();
    this.narration.push({ t: this.t(Math.min(when, Date.now())), text: clean.slice(0, 2000) });
    this.emitState();
    return true;
  }

  teardown() {
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
    if (this.clickFrameTimer) clearTimeout(this.clickFrameTimer);
    this.clickFrameTimer = null;
    this.stopHook();
    this.probe.stop();
    const w = this.win;
    this.win = null;
    if (w && !w.isDestroyed()) w.close();
  }

  /** Ends the recording and hands the bundle to onComplete. Narration still being transcribed in
   *  the control window gets a short grace period to arrive first. */
  async stop() {
    if (this.state === 'idle') return null;
    if (this.state === 'paused') this.resume();
    this.flushTyping();
    const durationS = Math.round(this.activeMs() / 100) / 10;
    this.state = 'idle';
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
    this.stopHook();
    if (this.win && !this.win.isDestroyed()) {
      this.win.webContents.send('recorder:state', { ...this.publicState(), state: 'stopping' });
      await new Promise((r) => setTimeout(r, 2500));
    }
    this.teardown();
    const bundle = {
      duration_s: durationS,
      screen: this.screenInfo,
      hook: !!loadHook(),
      limit_reason: this.limitReason || null,
      events: this.events.slice(),
      narration: this.narration.slice().sort((a, b) => a.t - b.t),
      keyframes: this.keyframes.map((k) => ({ t: k.t, reason: k.reason, window_title: k.window_title, process: k.process, jpeg: k.jpeg })),
    };
    this.reset();
    this.deps.onComplete?.(bundle);
    this.deps.onStateChange?.({ state: 'idle' });
    return { duration_s: bundle.duration_s, keyframes: bundle.keyframes.length, events: bundle.events.length };
  }

  cancel() {
    if (this.state === 'idle') return false;
    this.state = 'idle';
    this.teardown();
    this.reset();
    this.deps.onStateChange?.({ state: 'idle', cancelled: true });
    return true;
  }
}

/** Number of pixels (BGRA bitmaps of equal size) whose summed RGB difference exceeds a small
 *  tolerance — robust to JPEG-ish noise/antialiasing, sensitive to real UI changes. */
function changedPixels(a, b) {
  if (!a || !b || a.length !== b.length) return Infinity;
  let n = 0;
  for (let i = 0; i < a.length; i += 4) {
    const d = Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]);
    if (d > 30) n++;
  }
  return n;
}

module.exports = { Recorder, changedPixels, isTypingKey, buildKeyNames, ForegroundProbe, RECORDER_TITLE, MAX_KEYFRAMES, MAX_ACTIVE_MS };

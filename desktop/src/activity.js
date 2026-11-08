'use strict';
// Companion activity awareness: which application/window the user is focused on, as "focus sessions"
// (app, title, start, seconds). This is what lets the companion know what the user is working on
// right now and offer relevant help.
//
// Privacy model (enforced again server-side, see backend/app/companion.py):
//   - OFF by default. It only runs after the user opted in from the Compagnon page AND the org
//     policy allows it; the renderer starts/stops it and the server can tell it to stop.
//   - Nothing is recorded while the machine is idle or locked.
//   - Only the foreground window's process name and title are read — never keystrokes, never
//     screen content, never background windows.
//   - Sessions stay in memory here until the renderer drains them; nothing is written to disk.
//
// One long-lived PowerShell process polls GetForegroundWindow every few seconds (cheaper than a
// process spawn per sample). The script is static; it takes no input.

const { spawn } = require('child_process');

const SAMPLE_SCRIPT = `
$ErrorActionPreference = 'SilentlyContinue'
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public static class DmFg {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@
[Console]::OutputEncoding = [Text.Encoding]::UTF8
while ($true) {
  try {
    $h = [DmFg]::GetForegroundWindow()
    $sb = New-Object Text.StringBuilder 600
    [void][DmFg]::GetWindowText($h, $sb, 600)
    $procId = 0; [void][DmFg]::GetWindowThreadProcessId($h, [ref]$procId)
    $p = Get-Process -Id $procId
    [Console]::Out.WriteLine((@{ app = [string]$p.ProcessName; title = $sb.ToString() } | ConvertTo-Json -Compress))
  } catch {}
  Start-Sleep -Seconds 5
}`;

const SAMPLE_S = 5;
const IDLE_AFTER_S = 120;
const OWN_APP = /^(deliveryops|electron)$/i;

class ActivityTracker {
  constructor({ powerMonitor }) {
    this.powerMonitor = powerMonitor;
    this.proc = null;
    this.current = null; // { app, title, startedAt: Date, seconds }
    this.closed = [];    // finished sessions waiting to be drained
    this.locked = false;
    this.buffer = '';
    if (powerMonitor) {
      powerMonitor.on('lock-screen', () => { this.locked = true; this._close(); });
      powerMonitor.on('unlock-screen', () => { this.locked = false; });
      powerMonitor.on('suspend', () => this._close());
    }
  }

  get running() { return !!this.proc; }

  start() {
    if (this.proc || process.platform !== 'win32') return { running: this.running, supported: process.platform === 'win32' };
    const encoded = Buffer.from(SAMPLE_SCRIPT, 'utf16le').toString('base64');
    this.proc = spawn('powershell.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
      { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    this.proc.stdout.setEncoding('utf8');
    this.proc.stdout.on('data', (chunk) => this._onData(chunk));
    this.proc.on('exit', () => { this.proc = null; this._close(); });
    return { running: true, supported: true };
  }

  stop() {
    if (this.proc) { try { this.proc.kill(); } catch { /* already gone */ } }
    this.proc = null;
    this._close();
    this.closed = [];
    return { running: false, supported: process.platform === 'win32' };
  }

  _onData(chunk) {
    this.buffer += chunk;
    let nl;
    while ((nl = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, nl).trim();
      this.buffer = this.buffer.slice(nl + 1);
      if (!line.startsWith('{')) continue;
      try { this._sample(JSON.parse(line)); } catch { /* partial line */ }
    }
  }

  _sample({ app, title }) {
    const idle = this.powerMonitor ? this.powerMonitor.getSystemIdleTime() >= IDLE_AFTER_S : false;
    if (this.locked || idle || !app || OWN_APP.test(app)) { this._close(); return; }
    const t = String(title || '').slice(0, 500);
    if (this.current && this.current.app === app && this.current.title === t) {
      this.current.seconds += SAMPLE_S;
    } else {
      this._close();
      this.current = { app: String(app).slice(0, 120), title: t, startedAt: new Date(), seconds: SAMPLE_S };
    }
  }

  _close() {
    if (this.current && this.current.seconds >= SAMPLE_S) this.closed.push(this.current);
    this.current = null;
    if (this.closed.length > 500) this.closed = this.closed.slice(-500);
  }

  /** Finished sessions since the last drain, plus the time accrued so far on the session in
   *  progress (it restarts from now; the server merges contiguous slices of the same window). */
  drain() {
    const out = this.closed.map(toWire);
    this.closed = [];
    if (this.current && this.current.seconds >= SAMPLE_S) {
      out.push(toWire(this.current));
      this.current = { ...this.current, startedAt: new Date(), seconds: 0 };
    }
    return { events: out, running: this.running };
  }

  state() {
    return { running: this.running, supported: process.platform === 'win32',
      now: this.current ? { app: this.current.app, title: this.current.title, seconds: this.current.seconds } : null };
  }
}

function toWire(s) { return { started_at: s.startedAt.toISOString(), seconds: s.seconds, app: s.app, title: s.title }; }

module.exports = { ActivityTracker };

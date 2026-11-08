'use strict';
// The automation's EYES. Locates things on screen the way a person does — by what is visible — so an
// automation no longer depends on an application exposing good accessibility names, and works on
// apps that expose none at all (Citrix/remote desktops, custom-drawn UIs, legacy line-of-business
// tools). Three perception layers, fastest first:
//
//   1. Image memory (SikuliX-style template matching, pure JS, ~150 ms, no AI). Every successful
//      click saves a small image patch of what was clicked; next run we simply find that patch on
//      screen again — wherever the window has moved. Ambiguous or weak matches are never trusted.
//   2. Reading the screen: Windows' built-in OCR (~100 ms for a full 1080p screen, fully local, no
//      install) merged with the accessibility tree when a window is named. Exact, unique label
//      matches are clicked directly.
//   3. The BRAIN (backend /api/automations/vision/decide): everything perceived is described as
//      text — label, control type, position — and the org's text LLM chooses what to act on given
//      the step's intent. Measured on this project's models: 6/6 correct in ~0.7 s, against 1/5 for
//      the hosted vision model asked to pick from a marked-up screenshot. Icon-only buttons are
//      sent as tiny crops to be captioned once (cached), so they get a label too.
//
// Privacy: the screenshot never leaves this PC. It lives in memory for matching, the temp PNG used
// by the OCR engine is deleted immediately, and only text labels, positions and ≤64 px icon crops
// go to the server. Every PowerShell script here is static; parameters travel as DM_* env vars.

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { runPowerShell } = require('./automation-local');
const { PS_PRELUDE } = require('./automation-interact');

const PATCH = 72;            // side of a learned image patch (px)
const TEMPLATE_MIN = 0.90;   // zero-mean NCC needed to trust an image match
const TEMPLATE_MARGIN = 0.04; // …and how far ahead of the runner-up elsewhere on screen it must be
const MAX_CANDIDATES = 260;
const MAX_ICONS = 12;

// One process: DPI-aware capture of the primary screen -> PNG (deleted by Node right after),
// WinRT OCR with word boxes, and — when a window is named — its interactive accessibility elements
// with screen rectangles. All coordinates are physical pixels of the primary display.
const PS_SEE = `
Add-Type -AssemblyName System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save($env:DM_OUT, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

$lines = New-Object System.Collections.Generic.List[object]
$ocrOk = $false
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
  $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
  $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation\`1' })[0]
  function Await($op, [Type]$type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); [void]$t.Wait(-1); $t.Result }
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:DM_OUT)) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if ($engine) {
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $ocrOk = $true
    foreach ($ln in $result.Lines) {
      $ws = New-Object System.Collections.Generic.List[object]
      foreach ($w in $ln.Words) { $r = $w.BoundingRect; $ws.Add(@{ t = [string]$w.Text; x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }) }
      if ($ws.Count) { $lines.Add(@{ words = $ws.ToArray() }) }
    }
  }
  $stream.Dispose()
} catch {}

$elements = New-Object System.Collections.Generic.List[object]
$winName = $null; $winRect = $null
if ($env:DM_WINDOW) {
  try {
    $w = Find-Window $env:DM_WINDOW 0
    if ($w) {
      $winName = [string]$w.Current.Name
      $wr = $w.Current.BoundingRectangle
      $winRect = @{ x = [int]$wr.Left; y = [int]$wr.Top; w = [int]$wr.Width; h = [int]$wr.Height }
      $keep = @('Button','MenuItem','Edit','ComboBox','CheckBox','RadioButton','TabItem','ListItem','TreeItem','Hyperlink','SplitButton','DataItem','Document','Text','Image','Custom')
      $n = 0
      foreach ($e in $w.FindAll($T::Descendants, $True_)) {
        if ($n -ge 4000 -or $elements.Count -ge 320) { break }; $n++
        try {
          if ($e.Current.IsOffscreen) { continue }
          $ct = CType $e; if ($keep -notcontains $ct) { continue }
          $inter = Is-Interactive $e
          if (@('Text','Image','Custom','Document') -contains $ct -and -not $inter) { continue }
          $r = $e.Current.BoundingRectangle
          if ($r.Width -lt 6 -or $r.Height -lt 6) { continue }
          $elements.Add(@{ name = [string]$e.Current.Name; control_type = $ct; automation_id = [string]$e.Current.AutomationId; enabled = [bool]$e.Current.IsEnabled; x = [int]$r.Left; y = [int]$r.Top; w = [int]$r.Width; h = [int]$r.Height })
        } catch {}
      }
    }
  } catch {}
}
Emit @{ width = $b.Width; height = $b.Height; ocr = $ocrOk; lines = $lines.ToArray(); elements = $elements.ToArray(); window = $winName; window_rect = $winRect }`;

const PS_CLICK_AT = `
if ($env:DM_WINDOW) { try { $w = Find-Window $env:DM_WINDOW 0; if ($w) { Focus-Window $w } } catch {} }
[void][DmW]::SetCursorPos([int]$env:DM_X, [int]$env:DM_Y)
Start-Sleep -Milliseconds 80
$down = 2; $up = 4
if ($env:DM_RIGHT -eq '1') { $down = 8; $up = 16 }
$n = 1; if ($env:DM_DOUBLE -eq '1') { $n = 2 }
for ($i = 0; $i -lt $n; $i++) { [DmW]::mouse_event($down, 0, 0, 0, [UIntPtr]::Zero); [DmW]::mouse_event($up, 0, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 70 }
if ($env:DM_TYPE -eq '1') {
  Start-Sleep -Milliseconds 150
  if ($env:DM_CLEAR -eq '1') { [System.Windows.Forms.SendKeys]::SendWait('^a{DEL}') }
  [System.Windows.Forms.SendKeys]::SendWait((Escape-Keys $env:DM_TEXT))
  if ($env:DM_SUBMIT -eq '1') { Start-Sleep -Milliseconds 100; [System.Windows.Forms.SendKeys]::SendWait('{ENTER}') }
}
Emit @{ done = $true; x = [int]$env:DM_X; y = [int]$env:DM_Y }`;

const str = (v) => (v === undefined || v === null ? '' : String(v));
const norm = (s) => str(s).normalize('NFD').replace(/\p{M}/gu, '').replace(/&/g, '').replace(/\s+/g, ' ').trim().toLowerCase();

async function ps(script, env, timeoutMs) {
  const res = await runPowerShell(PS_PRELUDE + script, { env, timeoutMs });
  const line = (res.stdout || '').split(/\r?\n/).map((l) => l.trim()).filter((l) => l.startsWith('{')).pop();
  if (res.exit_code !== 0 || !line) {
    const err = (res.stderr || '').split(/\r?\n/).map((l) => l.trim()).find((l) => l && !l.startsWith('At line') && !l.startsWith('+')) || 'échec de la vision';
    throw new Error(err.replace(/^[^:]*Exception[^:]*:\s*/, '').slice(0, 400));
  }
  return JSON.parse(line);
}

// ------------------------------------------------------------------ image helpers (pure JS)

/** BGRA bitmap -> Float32 luminance. */
function toGray(bitmap, width, height) {
  const out = new Float32Array(width * height);
  for (let i = 0, p = 0; i < out.length; i++, p += 4) out[i] = 0.114 * bitmap[p] + 0.587 * bitmap[p + 1] + 0.299 * bitmap[p + 2];
  return out;
}

/** Box-filter downscale by an integer factor. */
function shrink(gray, width, height, f) {
  const w = Math.floor(width / f), h = Math.floor(height / f);
  const out = new Float32Array(w * h);
  const inv = 1 / (f * f);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let s = 0;
      for (let dy = 0; dy < f; dy++) { const row = (y * f + dy) * width + x * f; for (let dx = 0; dx < f; dx++) s += gray[row + dx]; }
      out[y * w + x] = s * inv;
    }
  }
  return { data: out, width: w, height: h };
}

function crop(gray, width, x, y, w, h) {
  const out = new Float32Array(w * h);
  for (let r = 0; r < h; r++) out.set(gray.subarray((y + r) * width + x, (y + r) * width + x + w), r * w);
  return out;
}

/** Zero-mean normalised cross-correlation of `tpl` at every position inside the search rectangle.
 *  Returns the best position/score and the best score found far away from it (ambiguity check). */
function nccSearch(img, iw, ih, tpl, tw, th, x0 = 0, y0 = 0, x1 = iw - tw, y1 = ih - th) {
  const n = tw * th;
  let tMean = 0;
  for (let i = 0; i < n; i++) tMean += tpl[i];
  tMean /= n;
  const t = new Float32Array(n);
  let tVar = 0;
  for (let i = 0; i < n; i++) { t[i] = tpl[i] - tMean; tVar += t[i] * t[i]; }
  if (tVar < 1e-3) return { score: 0, x: 0, y: 0, second: 0 };  // flat patch: nothing to recognise
  const scores = [];
  let best = { score: -1, x: 0, y: 0 };
  x0 = Math.max(0, x0); y0 = Math.max(0, y0); x1 = Math.min(iw - tw, x1); y1 = Math.min(ih - th, y1);
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      let sum = 0, sumSq = 0, dot = 0;
      for (let r = 0; r < th; r++) {
        const base = (y + r) * iw + x, tb = r * tw;
        for (let c = 0; c < tw; c++) { const v = img[base + c]; sum += v; sumSq += v * v; dot += v * t[tb + c]; }
      }
      const iVar = sumSq - (sum * sum) / n;
      const score = iVar > 1e-3 ? dot / Math.sqrt(iVar * tVar) : 0;
      scores.push(score);
      if (score > best.score) best = { score, x, y };
    }
  }
  let second = -1;
  const cols = x1 - x0 + 1;
  for (let i = 0; i < scores.length; i++) {
    const x = x0 + (i % cols), y = y0 + Math.floor(i / cols);
    if ((Math.abs(x - best.x) > tw || Math.abs(y - best.y) > th) && scores[i] > second) second = scores[i];
  }
  return { ...best, second };
}

/** Find a learned patch on the screen: coarse search at 1/4 scale, then pixel refinement. */
function matchTemplate(screenGray, sw, sh, patchGray, pw, ph) {
  const F = 4;
  const small = shrink(screenGray, sw, sh, F);
  const tsmall = shrink(patchGray, pw, ph, F);
  const coarse = nccSearch(small.data, small.width, small.height, tsmall.data, tsmall.width, tsmall.height);
  if (coarse.score < 0.75) return { score: coarse.score, ambiguous: false };
  const cx = coarse.x * F, cy = coarse.y * F;
  const fine = nccSearch(screenGray, sw, sh, patchGray, pw, ph, cx - F - 2, cy - F - 2, cx + F + 2, cy + F + 2);
  return { score: fine.score, x: fine.x + pw / 2, y: fine.y + ph / 2, ambiguous: coarse.second > coarse.score - TEMPLATE_MARGIN };
}

// ------------------------------------------------------------------ perception

/** OCR lines -> clickable text segments. Windows OCR joins everything on one baseline into a line,
 *  so two buttons side by side come back as one line; split where the gap between words is wide. */
function segments(lines) {
  const out = [];
  for (const ln of lines || []) {
    const words = (ln.words || []).filter((w) => w && w.t);
    let cur = [];
    const flush = () => {
      if (!cur.length) return;
      const x = Math.min(...cur.map((w) => w.x)), y = Math.min(...cur.map((w) => w.y));
      const x2 = Math.max(...cur.map((w) => w.x + w.w)), y2 = Math.max(...cur.map((w) => w.y + w.h));
      out.push({ label: cur.map((w) => w.t).join(' '), x, y, w: x2 - x, h: y2 - y });
      cur = [];
    };
    for (const w of words) {
      const prev = cur[cur.length - 1];
      if (prev && w.x - (prev.x + prev.w) > Math.max(prev.h, w.h) * 1.4) flush();
      cur.push(w);
    }
    flush();
  }
  return out;
}

const overlap = (a, b) => {
  const ix = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const iy = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  return (ix * iy) / Math.max(1, Math.min(a.w * a.h, b.w * b.h));
};

function createVision({ nativeImage, app }) {
  let last = null; // { gray, bitmap, width, height, at }
  const templateDir = () => {
    const dir = path.join(app.getPath('userData'), 'vision-templates');
    fs.mkdirSync(dir, { recursive: true });
    return dir;
  };
  const templateFile = (key) => path.join(templateDir(), crypto.createHash('sha1').update(str(key)).digest('hex') + '.json');

  /** Look at the screen: screenshot (kept in memory only) + OCR + accessibility elements. */
  async function see({ window } = {}) {
    const out = path.join(os.tmpdir(), `dm-vision-${crypto.randomBytes(6).toString('hex')}.png`);
    let raw;
    try {
      raw = await ps(PS_SEE, { DM_OUT: out, DM_WINDOW: str(window) }, 45_000);
      const image = nativeImage.createFromPath(out);
      const { width, height } = image.getSize();
      const bitmap = image.toBitmap();
      last = { bitmap, gray: toGray(bitmap, width, height), width, height, at: Date.now() };
    } finally {
      fs.promises.unlink(out).catch(() => {});  // the screenshot never persists on disk
    }
    const cands = [];
    for (const e of raw.elements || []) {
      if (e.x + e.w <= 0 || e.y + e.h <= 0 || e.x >= raw.width || e.y >= raw.height) continue;
      cands.push({ source: 'uia', label: str(e.name).slice(0, 120), control_type: e.control_type, automation_id: str(e.automation_id).slice(0, 80), enabled: e.enabled !== false, x: e.x, y: e.y, w: e.w, h: e.h });
    }
    const inWindow = (b) => !raw.window_rect || overlap(b, raw.window_rect) > 0.5;
    for (const s of segments(raw.lines)) {
      if (!inWindow(s) || s.label.trim().length < 2) continue;
      const dup = cands.some((c) => c.source === 'uia' && c.label && overlap(c, s) > 0.5 && (norm(c.label).includes(norm(s.label)) || norm(s.label).includes(norm(c.label))));
      if (!dup) cands.push({ source: 'ocr', label: s.label.slice(0, 120), control_type: 'Text', automation_id: '', enabled: true, ...s });
    }
    // Icon-only buttons: no label anywhere — send a tiny crop so the server can caption it once.
    const icons = [];
    const candidates = cands.slice(0, MAX_CANDIDATES).map((c, i) => ({ i, ...c }));
    for (const c of candidates) {
      if (icons.length >= MAX_ICONS) break;
      if (c.source !== 'uia' || c.label || c.w > 72 || c.h > 72 || c.w < 10 || c.h < 10) continue;
      if (!['Button', 'SplitButton', 'Image', 'Custom', 'Hyperlink', 'MenuItem'].includes(c.control_type)) continue;
      const png = cropPng(c.x, c.y, c.w, c.h);
      if (png) icons.push({ i: c.i, png_b64: png.toString('base64'), sha1: crypto.createHash('sha1').update(png).digest('hex') });
    }
    return { width: raw.width, height: raw.height, ocr: !!raw.ocr, window: raw.window || null, window_rect: raw.window_rect || null, candidates, icons };
  }

  function cropPng(x, y, w, h) {
    if (!last) return null;
    x = Math.max(0, Math.min(last.width - 1, Math.round(x))); y = Math.max(0, Math.min(last.height - 1, Math.round(y)));
    w = Math.max(1, Math.min(last.width - x, Math.round(w))); h = Math.max(1, Math.min(last.height - y, Math.round(h)));
    const buf = Buffer.alloc(w * h * 4);
    for (let r = 0; r < h; r++) last.bitmap.copy(buf, r * w * 4, ((y + r) * last.width + x) * 4, ((y + r) * last.width + x + w) * 4);
    return nativeImage.createFromBitmap(buf, { width: w, height: h }).toPNG();
  }

  /** Decode a PNG (base64) into gray + size, for matching a patch captured at record time. */
  function grayFromPngB64(b64) {
    try {
      const img = nativeImage.createFromBuffer(Buffer.from(str(b64), 'base64'));
      const { width, height } = img.getSize();
      if (!width || !height) return null;
      return { gray: toGray(img.toBitmap(), width, height), width, height };
    } catch { return null; }
  }

  /** Locate a target, most ROBUST and certain first:
   *   1. the recorded accessibility identity (automation_id, else a unique name+control-type) — the
   *      signal that survives window moves, resolution/DPI changes and theme changes, so it is the
   *      one most likely to still be right "next time" and on another machine;
   *   2. the image patch captured at record time (SikuliX-style ground-truth pixels) — precise when
   *      the accessibility name is missing or ambiguous (canvas/web/icon-only buttons), but locked to
   *      the recording's screen scale;
   *   3. the image patch learned on a previous successful run (image memory);
   *   4. an exact, unique visible label.
   *  Anything less certain is left to the caller's AI decision. Returns the perceived scene too, so
   *  the caller can ask the brain without looking again. `element`, `patch`, `point`, `rel` are the
   *  recorded ground truth (faithful replay); `key` is where the learned patch is stored.
   *
   *  Order rationale (senior note): pixels used to be tried first, but a patch only matches at the
   *  exact screen scale it was captured at, so on any resolution/theme change it silently missed and
   *  fell through — while a rock-solid automation_id sat unused behind it. Identity-first fixes that;
   *  pixels remain the strong fallback for elements that have no usable accessibility name. */
  async function locate({ target, key, window, element, patch, point, rel } = {}) {
    const started = Date.now();
    const scene = await see({ window });
    const done = (hit) => ({ ...hit, scene, ms: Date.now() - started });
    const tryPatch = (gray, w, h, dx, dy, method, label) => {
      if (!gray) return null;
      const m = matchTemplate(last.gray, last.width, last.height, gray instanceof Float32Array ? gray : gray.gray, w, h);
      if (m.score >= TEMPLATE_MIN && !m.ambiguous) return done({ found: true, method, confidence: +m.score.toFixed(3), x: Math.round(m.x + (dx || 0)), y: Math.round(m.y + (dy || 0)), label: label || '' });
      return null;
    };
    // 1) recorded accessibility identity: same automation_id (strongest), or a unique normalized
    //    name + control type. Robust to resolution/DPI/theme changes, unlike the pixel patch.
    if (element && (element.automation_id || element.name)) {
      const wantId = str(element.automation_id);
      const wantName = norm(element.name);
      const wantType = str(element.control_type);
      const byId = wantId ? scene.candidates.filter((c) => c.source === 'uia' && c.automation_id === wantId) : [];
      if (byId.length === 1) { const p = byId[0]; return done({ found: true, method: 'uia-id', confidence: 0.97, x: Math.round(p.x + p.w / 2), y: Math.round(p.y + p.h / 2), label: p.label, candidate: p.i }); }
      const byName = wantName ? scene.candidates.filter((c) => c.source === 'uia' && c.enabled && norm(c.label) === wantName && (!wantType || c.control_type === wantType)) : [];
      if (byName.length === 1) { const p = byName[0]; return done({ found: true, method: 'uia-name', confidence: 0.95, x: Math.round(p.x + p.w / 2), y: Math.round(p.y + p.h / 2), label: p.label, candidate: p.i }); }
    }
    // 2) record-time patch (ground truth from the recording) — for elements without a usable name
    if (patch) {
      const g = grayFromPngB64(patch);
      if (g) { const hit = tryPatch(g, g.width, g.height, 0, 0, 'image-gt', target); if (hit) return hit; }
    }
    // 3) learned image memory
    if (key && fs.existsSync(templateFile(key))) {
      try {
        const t = JSON.parse(fs.readFileSync(templateFile(key), 'utf8'));
        if (t.screen_w === last.width && t.screen_h === last.height) {  // a patch only means something at the scale it was taken
          const gray = new Float32Array(new Uint8Array(Buffer.from(t.gray, 'base64')));
          const hit = tryPatch(gray, t.w, t.h, t.dx, t.dy, 'image', t.label);
          if (hit) return hit;
        }
      } catch { /* unreadable template: fall through and relearn */ }
    }
    // 4) exact unique visible label
    const want = norm(target).replace(/^(le |la |l'|les |the )?(bouton|button|menu|lien|link|onglet|tab|champ|field|case)\s+/, '').replace(/^[«"“']\s*|\s*[»"”']$/g, '');
    if (want.length >= 2) {
      const exact = scene.candidates.filter((c) => c.enabled && norm(c.label) === want);
      const clickable = exact.filter((c) => c.source === 'uia' && c.control_type !== 'Text');
      const pick = clickable.length === 1 ? clickable[0] : exact.length === 1 ? exact[0] : null;
      if (pick) return done({ found: true, method: 'text', confidence: 0.9, x: Math.round(pick.x + pick.w / 2), y: Math.round(pick.y + pick.h / 2), label: pick.label, candidate: pick.i });
    }
    return done({ found: false });
  }

  /** Remember what the thing we are about to click looks like (the SikuliX-style image memory). */
  function learn(key, x, y, label) {
    if (!key || !last) return false;
    const px = Math.max(0, Math.min(last.width - PATCH, Math.round(x - PATCH / 2)));
    const py = Math.max(0, Math.min(last.height - PATCH, Math.round(y - PATCH / 2)));
    const gray = crop(last.gray, last.width, px, py, PATCH, PATCH);
    const bytes = Buffer.from(Uint8Array.from(gray, (v) => Math.max(0, Math.min(255, Math.round(v)))));
    fs.writeFileSync(templateFile(key), JSON.stringify({
      w: PATCH, h: PATCH, screen_w: last.width, screen_h: last.height, label: str(label).slice(0, 120),
      dx: Math.round(x - (px + PATCH / 2)), dy: Math.round(y - (py + PATCH / 2)), gray: bytes.toString('base64'), learned_at: new Date().toISOString(),
    }));
    return true;
  }

  function forget(key) { try { fs.unlinkSync(templateFile(key)); return true; } catch { return false; } }

  async function clickAt({ x, y, double, right, window, learnKey, label, type, text, clear, submit } = {}) {
    if (!Number.isFinite(Number(x)) || !Number.isFinite(Number(y))) throw new Error('coordonnées requises');
    const learned = learnKey ? learn(learnKey, Number(x), Number(y), label) : false;
    const res = await ps(PS_CLICK_AT, {
      DM_X: String(Math.round(Number(x))), DM_Y: String(Math.round(Number(y))), DM_DOUBLE: double ? '1' : '0', DM_RIGHT: right ? '1' : '0', DM_WINDOW: str(window),
      DM_TYPE: type ? '1' : '0', DM_TEXT: str(text), DM_CLEAR: clear === false ? '0' : '1', DM_SUBMIT: submit ? '1' : '0',
    }, 60_000);
    return { ...res, learned };
  }

  /** Everything readable on screen (or inside one window), top to bottom. */
  async function read({ window } = {}) {
    const scene = await see({ window });
    const rows = scene.candidates.filter((c) => c.label).sort((a, b) => (Math.abs(a.y - b.y) > 8 ? a.y - b.y : a.x - b.x));
    const lines = rows.map((c) => c.label);
    return { text: lines.join('\n').slice(0, 20000), lines: lines.slice(0, 1500), window: scene.window, ocr: scene.ocr };
  }

  // __learnForTest: lets a test exercise the image memory without performing a real click.
  return { see, locate, clickAt, read, forget, __learnForTest: async (key, x, y, label) => learn(key, x, y, label) };
}

module.exports = { createVision, matchTemplate, nccSearch, toGray, shrink, segments, PATCH };

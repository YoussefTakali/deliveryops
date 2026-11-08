# 05 — Desktop shell (Electron)

The desktop app (`desktop/`) wraps the same web app and grants native powers a browser can't have. It
exposes a single, fixed, read‑only bridge to the renderer: `window.deliveryDesktop` (via
`contextBridge` in `preload.js`). Windows‑first (PowerShell + UI Automation + WinRT OCR).

## Files (`desktop/src/`)
| File | Responsibility |
|---|---|
| `main.js` | App lifecycle, windows, tray, IPC handlers, single‑instance, login item |
| `preload.js` | The `deliveryDesktop` bridge (local files, automation, companion, notifications) |
| `background.js` | Tray + **close‑to‑tray background mode** (`keepAlive`, `getSettings`) |
| `local-files.js` | Sandboxed local file access (roots, scan, read, drives, full‑disk grant) |
| `watcher.js` | Watches allowed roots → `local:changed` for continuous indexing |
| `pc-account.js` | Best‑effort Windows/Entra account hint (to pre‑fill Microsoft sign‑in) |
| `automation-local.js` | Local steps: print, files, **hash‑approved PowerShell**, launch |
| `automation-interact.js` | Drive other apps: UI‑Automation click/type/read, window ops, web.browse, Teams captions. Holds the shared `PS_PRELUDE` (DPI awareness, UIA helpers) |
| `vision.js` | The automation's "eyes": screenshot + WinRT OCR + UIA + template matching + locate/click |
| `recorder.js` | Screen/action recorder (clicks with element + image patch, keys, keyframes, narration) |
| `titlebar.js` | Custom frameless window controls |

## The bridge (`window.deliveryDesktop`)
Namespaces exposed to the SPA (see `desktop.ts` for the typed wrapper):
- **local files**: `roots/scan/readText/readBase64/pickFolder/grantFullDisk/…`, `onFileChanged`.
- **automation**: `capabilities`, `powershell` (hash‑approved), `openApp/window/click/type/keys/read`,
  `dumpElements`, `web.browse`, `readTeamsCaptions`, `vision*` (locate/clickAt/read/forget), recorder
  controls, tray `setActiveCount`, `getLaunchAtLogin`.
- **companion**: `start/stop/drain/state` (activity), `getBackground/setBackground` (tray + login item).
- **notifications**: native toasts with action buttons (`notify`, `onNotificationOpen/Action`).
- `windowRole` ('main' | 'assistant' | 'recorder') — background services run only in 'main'.

## Background & tray (`background.js`, `main.js`)
Closing the window **hides to the notification‑area tray** by default (`closeToTray`), so DeliveryOps
keeps running (like Discord/Docker) and the companion can keep watching/suggesting. The tray menu
opens the app/companion, shows active automations, toggles background, and quits. Optionally launches
at Windows login. Toggles are surfaced in the companion preferences.

## Activity awareness (`activity.js`)
Opt‑in, policy‑gated. One long‑lived PowerShell polls the **foreground window** (process + title)
every few seconds → in‑memory "focus sessions" the renderer drains to the backend. Never records
keystrokes/screen content; pauses on idle/lock; skips the app's own windows. This feeds the
companion's "what are you doing now" context and **stuck detection**.

## Automation & vision
The record→replay engine and its computer‑vision fallback live here and in the frontend runner. Key
correctness point: the shared PowerShell prelude sets **Per‑Monitor‑V2 DPI awareness** so the
screenshot, UI‑Automation rectangles and cursor coordinates share one physical‑pixel space; and clicks
prefer **coordinate‑free UI‑Automation invocation** (Invoke/Toggle/Select) over moving the cursor.
Full details in [features/automations.md](features/automations.md).

## Packaging
Electron builder produces a Windows installer (`desktop/dist/DeliveryOps Setup 0.1.0.exe`), also
copied to `backend/downloads/` and offered from the in‑app Download page.

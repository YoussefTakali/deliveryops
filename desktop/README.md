# DeliveryOps Desktop

Electron shell around the DeliveryOps web app. A browser can't read the PC's files or float a
window over a Teams call — the desktop app adds both:

- **Local files** — read/scan the folders on your PC (read-only), preview text files, and push
  documents straight into a perimeter's knowledge base. Exposed to the web app through a locked-down
  preload bridge (`window.deliveryDesktop`); every read is checked to stay inside allow-listed roots.
- **Call assistant** — an always-on-top window (`Assistant d'appel` in the sidebar) that floats over
  a Teams call and suggests sourced answers as questions come up.

## Run

The desktop app just loads the running web app (it doesn't bundle it), so start the stack first:

```bash
# from the repo root — either the Docker stack…
docker compose up -d
# …or the dev servers (backend on :8100, frontend on :4300)

# then, in another terminal:
cd desktop
npm install        # first time (downloads Electron)
npm start
```

The server URL is resolved from, in order: the `DELIVERYOPS_URL` env var → a value saved in-app
(**Fichier → Configurer le serveur…**) → `http://localhost:4300`. If the server can't be reached the
app shows a built-in page to enter the URL. Override at launch:

```bash
DELIVERYOPS_URL=http://localhost:4200 npm start   # e.g. an `ng serve` dev server
```

## Package a Windows installer

```bash
cd desktop
npm install
npm run dist          # -> desktop/dist/"DeliveryOps Setup <version>.exe"  (NSIS installer)
npm run dist:portable # -> a single portable .exe (no install)
```

## Package a Linux installer

```bash
cd desktop
npm install
npm run dist:linux    # -> desktop/dist/DeliveryOps-<version>-x86_64.AppImage, -amd64.deb, -x86_64.rpm
npm run dist:appimage # -> just the AppImage (portable, runs on any distro: chmod +x and launch)
```

Install with `sudo dnf install ./dist/DeliveryOps-*.rpm` (Fedora/RHEL) or
`sudo apt install ./dist/DeliveryOps-*.deb` (Debian/Ubuntu). The `.deb`/`.rpm` targets use
electron-builder's bundled `fpm`, whose Ruby needs `libcrypt.so.1`: on Fedora run
`sudo dnf install libxcrypt-compat` once first. The AppImage has no such requirement.

On Fedora 41+ (RPM ≥ 4.20) the bundled `fpm` 1.9.3 can't build the `.rpm` (`rpmbuild` fails with
"File not found … BUILDROOT/opt/DeliveryOps/…"). Use a newer `fpm` instead:

```bash
gem install --user-install --no-document fpm
PATH="$(ruby -e 'print Gem.bindir(Gem.user_dir)'):$HOME/bin:$PATH" USE_SYSTEM_FPM=true npx electron-builder --linux rpm
```

On Linux the Windows-only features (activity tracking, PC account detection, open-at-login) report
themselves as unsupported; PowerShell-based features (UI automation, foreground-window recording)
are Windows-only.

Then publish it so the web app can offer it: copy the `.exe` into `backend/downloads/` (bind-mounted
into the backend container). It appears at **Télécharger l'app bureau** in the web app and at
`GET /api/download/desktop`.

## Test

```bash
npm test    # node-level tests for the filesystem core (no Electron/GUI needed)
```

## Layout

| File | Role |
|---|---|
| `src/main.js` | Electron main: windows, IPC handlers, allow-listed roots |
| `src/preload.js` | `contextBridge` exposing the fixed `window.deliveryDesktop` API |
| `src/local-files.js` | Pure-Node filesystem scan/read/overview (unit-tested) |
| `src/test-local-files.js` | `node` test for the above |

## Packaging (later)

Not set up yet. To ship an installer, add `electron-builder`, and bundle the Python backend (e.g.
PyInstaller) as a sidecar that `main.js` spawns instead of expecting a running stack.

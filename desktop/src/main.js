'use strict';
// DeliveryOps desktop shell (Electron). Loads the DeliveryOps web app in a native window and adds
// two things the browser sandbox can't do:
//   1. Local filesystem access (read/scan the PC's files) — exposed to the app via the preload bridge.
//   2. An always-on-top "call assistant" window that floats over a Teams call and suggests answers.
//
// The web app (frontend + backend) runs as usual (docker compose or the dev servers); this shell
// points at it. The server URL is resolved from, in order: the DELIVERYOPS_URL env var, a value the
// user saved in-app (userData/config.json), else http://localhost:4300. If the app can't reach the
// server it shows a built-in page to set the URL.

const { app, BrowserWindow, ipcMain, dialog, shell, Menu, session, desktopCapturer, Tray, nativeImage, screen } = require('electron');
const { attachTitlebar } = require('./titlebar');
const { Watcher } = require('./watcher');
const { detectPcAccountHint } = require('./pc-account');
const path = require('path');
const fs = require('fs');
const lf = require('./local-files');
const { createLocalSteps } = require('./automation-local');
const { createInteractSteps } = require('./automation-interact');
const { Recorder } = require('./recorder');
const { createBackground } = require('./background');

const DEFAULT_URL = 'http://localhost:4300';
// Domains Microsoft's identity platform uses during interactive sign-in (org + personal accounts,
// and the broker/passkey hops some tenants redirect through). Popups to these are allowed as real
// Electron windows instead of being shelled out to the system browser — see setWindowOpenHandler.
const MICROSOFT_LOGIN_DOMAINS = /^https:\/\/([a-z0-9-]+\.)*(login\.microsoftonline\.com|login\.microsoft\.com|login\.live\.com|login\.windows\.net|account\.microsoft\.com|msauth\.net|msftauth\.net)\//i;
const configPath = () => path.join(app.getPath('userData'), 'config.json');

function loadConfig() {
  try { return JSON.parse(fs.readFileSync(configPath(), 'utf8')); } catch { return {}; }
}
function saveConfig(patch) {
  const next = { ...loadConfig(), ...patch };
  try { fs.writeFileSync(configPath(), JSON.stringify(next, null, 2)); } catch { /* best effort */ }
  return next;
}
function appUrl() {
  return process.env.DELIVERYOPS_URL || loadConfig().serverUrl || DEFAULT_URL;
}

// Roots the user has granted read access to. Seeded with the usual document folders (or restored
// from a previous "index everything"/folder grant); the user can add more via the folder/file
// picker. Every filesystem read is checked against this set.
const savedRoots = loadConfig().extraRoots || [];
const allowedRoots = new Set([...lf.defaultRoots(), ...savedRoots]);
let fullDiskGranted = !!loadConfig().fullDiskGranted;

// Continuous local indexing: any indexable file added/changed under an allowed root is reported to
// the renderer (debounced), which uploads it into the personal knowledge index automatically.
const watcher = new Watcher((changedPath) => {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('local:changed', changedPath);
});

let mainWindow = null;
let assistantWindow = null;
let mainWindowShownOnce = false;

// A built-in page shown when the server can't be reached, to set/repair the server URL.
function configPageHtml(current, error) {
  const esc = (s) => String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  return 'data:text/html;charset=utf-8,' + encodeURIComponent(`<!doctype html><html><head><meta charset="utf-8">
<style>
  body{font:14px/1.5 "Segoe UI",system-ui,sans-serif;background:#0c111b;color:#e6eaf2;margin:0;height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#141b28;border:1px solid #263042;border-radius:12px;padding:28px 30px;width:min(460px,90vw)}
  h1{font-size:18px;margin:0 0 6px} p{color:#9aa6b8;margin:0 0 18px}
  label{display:block;font-size:12px;color:#9aa6b8;margin:0 0 6px}
  input{width:100%;box-sizing:border-box;padding:9px 11px;border-radius:8px;border:1px solid #263042;background:#0c111b;color:#e6eaf2;font:inherit}
  button{margin-top:16px;width:100%;padding:10px;border:0;border-radius:8px;background:#3553d6;color:#fff;font:inherit;font-weight:600;cursor:pointer}
  .err{color:#f07174;font-size:12px;margin-top:10px}
  .logo{display:flex;align-items:center;gap:10px;margin-bottom:14px;font-weight:600}
</style></head><body><div class="card">
  <div class="logo"><span style="display:inline-block;width:24px;height:24px;border-radius:7px;background:#3553d6"></span> DeliveryOps</div>
  <h1>Connexion au serveur</h1>
  <p>Indiquez l'adresse de votre instance DeliveryOps (frontend).</p>
  <label for="u">URL du serveur</label>
  <input id="u" value="${esc(current)}" placeholder="https://delivery.mon-entreprise.com" />
  ${error ? `<div class="err">${esc(error)}</div>` : ''}
  <button onclick="save()">Se connecter</button>
</div><script>
  const i=document.getElementById('u'); i.focus(); i.select();
  i.addEventListener('keydown',e=>{if(e.key==='Enter')save();});
  function save(){ const v=i.value.trim(); if(v && window.deliveryDesktop) window.deliveryDesktop.setServerUrl(v); }
</script></body></html>`);
}

function loadApp(win) { win.loadURL(appUrl()); }

// Allow a popup to Microsoft's own sign-in domains as a real Electron window (see below), and
// recursively grant the SAME trust to any window IT opens in turn. This matters because the sign-in
// popup itself commonly opens a further nested popup mid-flow — a Windows Hello / PIN prompt, a
// security-key ceremony, an account picker, "use a different account" — and each of those is a
// window opened by the FIRST popup's own webContents, not by our main window. setWindowOpenHandler
// only governs windows opened by the webContents it's attached to, so without also attaching it to
// every popup Electron creates for us, that second-level popup falls back to Electron's default
// (deny), which looks exactly like "the PIN popup gets blocked" with no visible error.
function configurePopups(contents, base) {
  contents.setWindowOpenHandler(({ url }) => {
    // MSAL's loginPopup()/acquireTokenPopup() opens a real popup window it polls directly (reads
    // the popup's location bar once Microsoft redirects it back to our origin, then closes it
    // itself) — it needs an actual Electron BrowserWindow to do that, not the system browser.
    // `about:blank` covers the brief moment some flows open a blank popup before navigating it.
    if (url === 'about:blank' || url.startsWith(base) || MICROSOFT_LOGIN_DOMAINS.test(url)) {
      return { action: 'allow', overrideBrowserWindowOptions: { autoHideMenuBar: true, width: 500, height: 700 } };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });
  contents.on('did-create-window', (childWindow) => configurePopups(childWindow.webContents, base));
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440, height: 920, minWidth: 1024, minHeight: 680,
    title: 'DeliveryOps',
    // Launched at Windows sign-in with --hidden (see background.js): the page still loads (the
    // automation runner lives in it) but the window stays in the tray until the user opens it.
    show: !(background.startedHidden && !mainWindowShownOnce),
    backgroundColor: '#0c111b',
    frame: false, // no native titlebar/menu — a custom in-app titlebar is injected instead
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // the preload needs `require`; the bridge still exposes only a fixed API
    },
  });
  attachTitlebar(mainWindow, appUrl);
  loadApp(mainWindow);
  configurePopups(mainWindow.webContents, appUrl());
  // If the server is unreachable, show the config page instead of a blank error screen.
  // isMainFrame matters: did-fail-load also fires for sub-frames, notably MSAL's hidden ssoSilent
  // iframe (points at Microsoft, not our server) and its own occasional ERR_BLOCKED_BY_RESPONSE —
  // without this check that background failure was wiping out the whole app with a false
  // "server unreachable" screen even though localhost:4300 loaded fine.
  mainWindow.webContents.on('did-fail-load', (_e, code, desc, validatedUrl, isMainFrame) => {
    if (!isMainFrame) return;
    if (validatedUrl && validatedUrl.startsWith('data:')) return; // don't loop on the config page itself
    if (code === -3) return; // ERR_ABORTED (navigations/redirects) — ignore
    mainWindow.loadURL(configPageHtml(appUrl(), `Serveur injoignable (${desc}). Vérifiez l'adresse et que le stack tourne.`));
  });
  mainWindow.on('show', () => { mainWindowShownOnce = true; background.onMainShown(); });
  mainWindow.on('hide', () => background.onMainShown());
  // With active automations, closing only hides to the tray so scheduled runs keep happening.
  mainWindow.on('close', (e) => { if (background.interceptClose()) e.preventDefault(); });
  mainWindow.on('closed', () => { mainWindow = null; });
}

function openAssistant(route) {
  if (assistantWindow && !assistantWindow.isDestroyed()) { assistantWindow.focus(); return; }
  // Frameless + transparent: the page draws its own rounded translucent panel (and its own drag bar
  // and close button — see call-assistant.ts `.overlay`), so the overlay has no window chrome or
  // edges over the Teams call.
  const { workArea } = require('electron').screen.getPrimaryDisplay();
  assistantWindow = new BrowserWindow({
    width: 400, height: 620, minWidth: 320, minHeight: 300,
    x: workArea.x + workArea.width - 420, y: workArea.y + 40,
    frame: false, transparent: true, backgroundColor: '#00000000', hasShadow: false, resizable: true,
    alwaysOnTop: true, title: 'Copilote d’appel',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: false, additionalArguments: ['--dm-window=assistant'] },
  });
  assistantWindow.setAlwaysOnTop(true, 'floating');
  // Excludes this window from screen captures/recordings (Windows: sets WDA_EXCLUDEFROMCAPTURE) so
  // the assistant's live suggestions never leak into a shared Teams/Zoom screen — the whole point of
  // a private call-assistant overlay is defeated if the other participants can see it too.
  assistantWindow.setContentProtection(true);
  assistantWindow.loadURL(`${appUrl()}${route || '/call-assistant'}?overlay=1`);
  configurePopups(assistantWindow.webContents, appUrl());
  assistantWindow.on('closed', () => { assistantWindow = null; });
}

// Handles navigator.mediaDevices.getDisplayMedia({ video: true, audio: true }) calls made by the
// renderer (frontend/src/app/pages/call-assistant.ts's "Écouter tout l'appel" mode). This is what
// lets the assistant hear the *other* call participant: Teams plays their voice through the system's
// speakers, and getDisplayMedia + loopback audio is the only way Chromium can capture that, since the
// Web Speech API only ever transcribes the microphone. `useSystemPicker: true` (Electron >= 32) makes
// Windows 10 19041+/Windows 11 show its own native "choose what to share" picker — including a system
// audio toggle — without any UI on our side; the handler below is only invoked as a fallback on older
// Windows, where we just grab the primary screen and request loopback audio explicitly. Either way
// the renderer stops the video track immediately and keeps only the audio.
function configureDisplayMediaCapture() {
  session.defaultSession.setDisplayMediaRequestHandler((_request, callback) => {
    desktopCapturer.getSources({ types: ['screen'] })
      .then((sources) => { callback(sources.length ? { video: sources[0], audio: 'loopback' } : {}); })
      .catch(() => callback({}));
  }, { useSystemPicker: true });
  // Explicit grant for microphone ('media') and getDisplayMedia ('display-capture') requests from
  // our own app — Electron's undocumented default is to auto-approve these, but that default has
  // shifted between versions before, and the call assistant (mic + "Écouter tout l'appel") is
  // useless without it, so this makes the grant unconditional rather than relying on the default.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(permission === 'media' || permission === 'display-capture');
  });
}

function promptServerUrl() {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.loadURL(configPageHtml(appUrl(), ''));
}

function startWatchers() {
  for (const root of allowedRoots) watcher.watch(root);
}

function addRoot(root) {
  allowedRoots.add(root);
  saveConfig({ extraRoots: Array.from(allowedRoots).filter((r) => !lf.defaultRoots().includes(r)) });
  watcher.watch(root);
}

// ---------------------------------------------------------------- IPC

function ok(fn) {
  return async (_event, ...args) => {
    try { return { ok: true, data: await fn(...args) }; }
    catch (err) { return { ok: false, error: String(err && err.message ? err.message : err) }; }
  };
}

// Same as `ok`, but hands the handler the IPC event too (needed to resolve the sender's window).
function okEvent(fn) {
  return async (event, ...args) => {
    try { return { ok: true, data: await fn(event, ...args) }; }
    catch (err) { return { ok: false, error: String(err && err.message ? err.message : err) }; }
  };
}

ipcMain.handle('local:roots', ok(async () => Array.from(allowedRoots)));
ipcMain.handle('local:overview', ok(async () => lf.overview(Array.from(allowedRoots))));
ipcMain.handle('local:scan', ok(async ({ root, query, limit }) => {
  if (!lf.within(root, Array.from(allowedRoots))) throw new Error('root not allowed');
  return lf.scan({ root, query, limit });
}));
ipcMain.handle('local:readText', ok(async ({ path: p }) => lf.readText(p, Array.from(allowedRoots))));
ipcMain.handle('local:readBase64', ok(async ({ path: p }) => lf.readBase64(p, Array.from(allowedRoots))));
ipcMain.handle('local:pickFolder', ok(async () => {
  const res = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] });
  if (res.canceled || !res.filePaths.length) return { added: null, roots: Array.from(allowedRoots) };
  addRoot(res.filePaths[0]);
  return { added: res.filePaths[0], roots: Array.from(allowedRoots) };
}));
// Grant access to a single file (rather than a whole folder): the file's own path becomes an
// allowed "root" of one, and it's indexed once immediately by the renderer after this resolves.
ipcMain.handle('local:pickFile', ok(async () => {
  const res = await dialog.showOpenDialog(mainWindow, { properties: ['openFile'] });
  if (res.canceled || !res.filePaths.length) return { added: null };
  addRoot(res.filePaths[0]);
  return { added: res.filePaths[0] };
}));
ipcMain.handle('local:listDrives', ok(async () => lf.listDrives()));
// "Index everything on this PC": grants every detected drive as a root. Explicit, reversible.
ipcMain.handle('local:grantFullDisk', ok(async () => {
  for (const d of lf.listDrives()) addRoot(d);
  fullDiskGranted = true;
  saveConfig({ fullDiskGranted: true });
  return { roots: Array.from(allowedRoots), fullDisk: true };
}));
ipcMain.handle('local:revokeFullDisk', ok(async () => {
  for (const d of lf.listDrives()) { allowedRoots.delete(d); }
  fullDiskGranted = false;
  saveConfig({ fullDiskGranted: false, extraRoots: Array.from(allowedRoots).filter((r) => !lf.defaultRoots().includes(r)) });
  watcher.unwatchAll();
  startWatchers();
  return { roots: Array.from(allowedRoots), fullDisk: false };
}));
ipcMain.handle('local:fullDiskStatus', ok(async () => fullDiskGranted));
ipcMain.handle('call:openAssistant', ok(async ({ route } = {}) => { openAssistant(route); return true; }));

// window controls for the custom (frameless) titlebar
ipcMain.handle('window:minimize', okEvent(async (event) => { BrowserWindow.fromWebContents(event.sender)?.minimize(); return true; }));
ipcMain.handle('window:toggleMaximize', okEvent(async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return false;
  if (win.isMaximized()) win.unmaximize(); else win.maximize();
  return win.isMaximized();
}));
ipcMain.handle('window:close', okEvent(async (event) => { BrowserWindow.fromWebContents(event.sender)?.close(); return true; }));
ipcMain.handle('config:promptServerUrl', ok(async () => { promptServerUrl(); return true; }));
// The PC's signed-in Windows account (email/UPN), best-effort — see pc-account.js. Cached: the
// underlying `whoami` call doesn't change during the app's lifetime.
let pcAccountHintCache;
ipcMain.handle('local:pcAccountHint', ok(async () => {
  if (pcAccountHintCache === undefined) pcAccountHintCache = await detectPcAccountHint();
  return pcAccountHintCache;
}));
ipcMain.handle('config:getUrl', ok(async () => appUrl()));
ipcMain.handle('config:setUrl', ok(async ({ url } = {}) => {
  const clean = String(url || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\//.test(clean)) throw new Error('URL invalide (http:// ou https:// requis)');
  saveConfig({ serverUrl: clean });
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.loadURL(clean);
  return clean;
}));

// ---------------------------------------------------------------- native OS notifications
// The renderer (frontend/src/app/native-notify.ts) calls `deliveryDesktop.notify(...)` for each new
// DeliveryOps notification; we show it as a native toast. Clicking the toast focuses the main
// window and tells the renderer to navigate to the notification's link ('notification:open').
//
// Toast action buttons ("Approuver" / "Ouvrir"): Electron's Notification `actions` + 'action'
// event are macOS-only. On Windows the only reliable way is a custom `toastXml` whose buttons use
// activationType="protocol": Windows launches `deliveryops://notification/?t=<token>`, which the
// single-instance lock below routes to this running process ('second-instance'). The token is a
// random, single-use, in-memory id we mint per button, so a web page that launches a
// deliveryops:// URL can't trigger an approval — only a button on a toast we showed can. If the
// protocol can't be registered we fall back to plain click-to-open toasts (no buttons).
const { Notification: NativeNotification } = require('electron');
const crypto = require('crypto');

const NOTIFY_PROTOCOL = 'deliveryops';
// Must match the Start-menu shortcut's AppUserModelID (electron-builder sets it to build.appId) for
// Windows to attribute toasts to "DeliveryOps". Unpackaged (`npm start`), Electron's own exe path is
// the documented dev value.
app.setAppUserModelId(app.isPackaged ? 'com.deliveryops.desktop' : process.execPath);

const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) app.quit();

let notifyProtocolRegistered = false;
if (process.platform === 'win32') {
  try {
    notifyProtocolRegistered = process.defaultApp && process.argv.length >= 2
      ? app.setAsDefaultProtocolClient(NOTIFY_PROTOCOL, process.execPath, [path.resolve(process.argv[1])])
      : app.setAsDefaultProtocolClient(NOTIFY_PROTOCOL);
  } catch { notifyProtocolRegistered = false; }
}

const notificationTokens = new Map(); // token -> { id, action, link }
const liveNotifications = new Map(); // notification id -> Notification (a GC'd Notification loses its 'click' handler)
const TOKEN_TTL_MS = 24 * 60 * 60 * 1000;

function focusMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
  return true;
}

function sendToMainRenderer(channel, payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const wc = mainWindow.webContents;
  if (wc.isLoading()) wc.once('did-finish-load', () => wc.send(channel, payload));
  else wc.send(channel, payload);
}

function dispatchNotification(id, action, link) {
  if (action === 'open') {
    if (!focusMainWindow()) {
      // Main window was closed (e.g. only the call assistant is left): reopen it straight on the link.
      if (app.isReady()) { createMainWindow(); mainWindow.loadURL(appUrl() + link); }
      return;
    }
    sendToMainRenderer('notification:open', { id, link });
  } else {
    // Background action (e.g. approve): no need to steal focus — the renderer reports the outcome
    // with a follow-up toast.
    sendToMainRenderer('notification:action', { id, action, link });
  }
}

function handleNotifyProtocolUrl(raw) {
  let token = null;
  try {
    const u = new URL(raw);
    if (u.protocol !== `${NOTIFY_PROTOCOL}:`) return;
    token = u.searchParams.get('t');
  } catch { return; }
  const entry = token ? notificationTokens.get(token) : null;
  if (!entry || entry.expires < Date.now()) { focusMainWindow(); return; } // unknown/expired: just surface the app
  for (const [k, v] of notificationTokens) if (v.id === entry.id) notificationTokens.delete(k); // single-use
  const live = liveNotifications.get(entry.id);
  if (live) { try { live.close(); } catch { /* already gone */ } liveNotifications.delete(entry.id); }
  dispatchNotification(entry.id, entry.action, entry.link);
}

app.on('second-instance', (_event, argv) => {
  const url = (argv || []).find((a) => typeof a === 'string' && a.startsWith(`${NOTIFY_PROTOCOL}://`));
  if (url) handleNotifyProtocolUrl(url);
  else focusMainWindow(); // a second launch of the app just brings the existing window forward
});

function xmlEscape(s) {
  return String(s == null ? '' : s).replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' }[c]));
}

function mintToken(id, action, link) {
  const token = crypto.randomBytes(16).toString('hex');
  notificationTokens.set(token, { id, action, link, expires: Date.now() + TOKEN_TTL_MS });
  return token;
}

function windowsToastXml(id, title, body, link, actions) {
  const buttons = actions.map((a) => {
    const url = `${NOTIFY_PROTOCOL}://notification/?t=${mintToken(id, a.id, link)}`;
    return `<action content="${xmlEscape(a.label)}" activationType="protocol" arguments="${xmlEscape(url)}"/>`;
  }).join('');
  return `<toast><visual><binding template="ToastGeneric"><text>${xmlEscape(title)}</text><text>${xmlEscape(body)}</text>`
    + `<text placement="attribution">DeliveryOps</text></binding></visual><actions>${buttons}</actions></toast>`;
}

ipcMain.handle('notify:show', ok(async (payload = {}) => {
  if (!NativeNotification.isSupported()) return { shown: false, actions: false };
  const id = String(payload.id || crypto.randomUUID()).slice(0, 100);
  const title = String(payload.title || 'DeliveryOps').slice(0, 200);
  const body = String(payload.body || '').slice(0, 500);
  const link = typeof payload.link === 'string' && payload.link.startsWith('/') ? payload.link.slice(0, 500) : '/';
  const actions = (Array.isArray(payload.actions) ? payload.actions : [])
    .filter((a) => a && /^[a-z_]{1,32}$/.test(String(a.id)) && a.label)
    .slice(0, 3)
    .map((a) => ({ id: String(a.id), label: String(a.label).slice(0, 40) }));

  // Drop expired tokens so the map can't grow unbounded over a long session.
  const now = Date.now();
  for (const [k, v] of notificationTokens) if (v.expires < now) notificationTokens.delete(k);

  const useWinActions = process.platform === 'win32' && notifyProtocolRegistered && actions.length > 0;
  const options = { title, body, silent: !!payload.silent };
  if (useWinActions) options.toastXml = windowsToastXml(id, title, body, link, actions);
  if (process.platform === 'darwin' && actions.length) options.actions = actions.map((a) => ({ type: 'button', text: a.label }));

  const n = new NativeNotification(options);
  n.on('click', () => { liveNotifications.delete(id); dispatchNotification(id, 'open', link); });
  n.on('action', (_e, index) => { liveNotifications.delete(id); const a = actions[index]; if (a) dispatchNotification(id, a.id, link); });
  n.on('close', () => liveNotifications.delete(id));
  n.on('failed', (_e, error) => console.warn('[notify] toast failed:', error));
  liveNotifications.set(id, n);
  n.show();
  return { shown: true, actions: useWinActions || (process.platform === 'darwin' && actions.length > 0) };
}));
ipcMain.handle('notify:capabilities', ok(async () => ({
  supported: NativeNotification.isSupported(),
  actions: process.platform === 'darwin' || (process.platform === 'win32' && notifyProtocolRegistered),
})));

// ---------------------------------------------------------------- automations
// Local steps, recorder and background/tray for the "Automatisations" feature. The renderer's
// AutomationRunner (frontend/src/app/automations/runner.ts) drives these through
// deliveryDesktop.automation.* — see automation-local.js / recorder.js / background.js.

function showMainWindow() {
  if (focusMainWindow()) return;
  if (app.isReady()) createMainWindow();
}

const background = createBackground({
  app, Tray, Menu, nativeImage, Notification: NativeNotification,
  getMainWindow: () => mainWindow,
  showMainWindow,
  openRoute: (route) => { showMainWindow(); sendToMainRenderer('notification:open', { id: 'tray', link: route }); },
  loadConfig, saveConfig,
});

const localSteps = createLocalSteps({ getRoots: () => Array.from(allowedRoots), BrowserWindow, shell });

// Desktop-app (UI Automation) and web (dedicated browser session) interaction steps — see automation-interact.js.
const interact = createInteractSteps({ BrowserWindow, desktopCapturer, screen, app, session });
ipcMain.handle('automation:openApp', ok(async (p = {}) => interact.openApp(p)));
ipcMain.handle('automation:window', ok(async (p = {}) => interact.windowAction(p)));
ipcMain.handle('automation:click', ok(async (p = {}) => interact.click(p)));
ipcMain.handle('automation:type', ok(async (p = {}) => interact.type(p)));
ipcMain.handle('automation:keys', ok(async (p = {}) => interact.keys(p)));
ipcMain.handle('automation:read', ok(async (p = {}) => interact.read(p)));
ipcMain.handle('automation:readNotifications', ok(async (p = {}) => interact.readNotifications(p)));
ipcMain.handle('automation:screenshot', ok(async (p = {}) => interact.screenshot(p)));
ipcMain.handle('automation:browse', ok(async (p = {}) => interact.browse(p)));
ipcMain.handle('automation:openBrowser', ok(async (p = {}) => interact.openBrowser(p)));
ipcMain.handle('automation:dumpElements', ok(async (p = {}) => interact.dumpElements(p)));
ipcMain.handle('automation:teamsCaptions', ok(async (p = {}) => interact.readTeamsCaptions(p)));

// Vision: the automation's eyes (local OCR + image memory; the screenshot never leaves the PC) — see vision.js.
const vision = require('./vision').createVision({ nativeImage, app });
ipcMain.handle('vision:locate', ok(async (p = {}) => vision.locate(p)));
ipcMain.handle('vision:clickAt', ok(async (p = {}) => vision.clickAt(p)));
ipcMain.handle('vision:read', ok(async (p = {}) => vision.read(p)));
ipcMain.handle('vision:forget', ok(async (p = {}) => ({ forgotten: vision.forget(p.key) })));

// Companion activity awareness (opt-in; see activity.js for the privacy model). The renderer starts
// it only once the user has consented and the org policy allows it, drains sessions periodically
// and stops it when consent is withdrawn or the server says tracking is off.
const { ActivityTracker } = require('./activity');
const activityTracker = new ActivityTracker({ powerMonitor: require('electron').powerMonitor });
ipcMain.handle('companion:start', ok(async () => activityTracker.start()));
ipcMain.handle('companion:stop', ok(async () => activityTracker.stop()));
ipcMain.handle('companion:drain', ok(async () => activityTracker.drain()));
ipcMain.handle('companion:state', ok(async () => activityTracker.state()));
app.on('before-quit', () => { activityTracker.stop(); });

let pendingRecording = null; // finished bundle waiting for the main window's renderer to take it
const recorder = new Recorder({
  BrowserWindow, desktopCapturer, screen,
  preloadPath: path.join(__dirname, 'preload.js'),
  appUrl,
  configurePopups: (wc) => configurePopups(wc, appUrl()),
  onBeforeStart: () => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.minimize(); },
  onStateChange: (state) => sendToMainRenderer('recorder:state', state),
  onComplete: (bundle) => {
    pendingRecording = bundle;
    showMainWindow();
    sendToMainRenderer('automation:recordingReady', { duration_s: bundle.duration_s, keyframes: bundle.keyframes.length, events: bundle.events.length });
  },
});

ipcMain.handle('automation:capabilities', ok(async () => ({ ...recorder.capabilities(), loginItem: background.loginItem() })));
ipcMain.handle('automation:print', ok(async (p = {}) => localSteps.print(p)));
ipcMain.handle('automation:printers', ok(async () => localSteps.printers()));
ipcMain.handle('automation:saveFile', ok(async (p = {}) => localSteps.saveFile(p)));
ipcMain.handle('automation:readFile', ok(async (p = {}) => localSteps.readFile(p)));
ipcMain.handle('automation:listFiles', ok(async (p = {}) => localSteps.listFiles(p)));
ipcMain.handle('automation:open', ok(async (p = {}) => localSteps.open(p)));
ipcMain.handle('automation:powershell', ok(async (p = {}) => localSteps.powershell(p)));
ipcMain.handle('automation:setActiveCount', ok(async ({ count } = {}) => background.setActiveCount(count)));
ipcMain.handle('automation:getLaunchAtLogin', ok(async () => background.loginItem()));
ipcMain.handle('automation:setLaunchAtLogin', ok(async ({ enabled } = {}) => background.setLoginItem(!!enabled)));
ipcMain.handle('companion:getBackground', ok(async () => background.getSettings()));
ipcMain.handle('companion:setBackground', ok(async ({ closeToTray, launchAtLogin } = {}) => {
  if (typeof closeToTray === 'boolean') background.setCloseToTray(closeToTray);
  if (typeof launchAtLogin === 'boolean') background.setLoginItem(launchAtLogin);
  return background.getSettings();
}));
ipcMain.handle('recorder:start', ok(async (opts = {}) => recorder.start(opts)));
ipcMain.handle('recorder:pause', ok(async () => recorder.pause()));
ipcMain.handle('recorder:resume', ok(async () => recorder.resume()));
ipcMain.handle('recorder:stop', ok(async () => recorder.stop()));
ipcMain.handle('recorder:cancel', ok(async () => recorder.cancel()));
ipcMain.handle('recorder:state', ok(async () => recorder.publicState()));
ipcMain.handle('recorder:narration', ok(async (p = {}) => recorder.addNarration(p)));
// Single hand-off: the renderer takes the bundle once (it may be several MB of JPEGs).
ipcMain.handle('recorder:takeBundle', ok(async () => { const b = pendingRecording; pendingRecording = null; return b; }));

// ---------------------------------------------------------------- lifecycle
app.commandLine.appendSwitch('host-resolver-rules', 'MAP localhost 127.0.0.1');
app.whenReady().then(() => {
  if (!singleInstance) return; // another DeliveryOps instance owns the window (see 'second-instance')
  Menu.setApplicationMenu(null); // no native File/Edit/View/Window menu bar
  configureDisplayMediaCapture();
  createMainWindow();
  background.ensureTray();  // always show the notification-area icon: DeliveryOps lives in the background
  startWatchers();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createMainWindow(); });
});

app.on('before-quit', () => { background.markQuitting(); recorder.cancel(); watcher.unwatchAll(); });

// Keep running in the background (tray) after the window is closed, like Discord/Docker — unless the
// user turned that off or is really quitting.
app.on('window-all-closed', () => { if (process.platform !== 'darwin' && !background.keepAlive()) app.quit(); });

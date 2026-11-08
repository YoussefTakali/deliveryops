'use strict';
// Bridge exposed to the DeliveryOps web app when it runs inside the desktop shell. The renderer
// sees only `window.deliveryDesktop` with this fixed, read-only API — no direct Node/fs access.

const { contextBridge, ipcRenderer } = require('electron');

async function call(channel, payload) {
  const res = await ipcRenderer.invoke(channel, payload);
  if (!res || res.ok !== true) throw new Error(res && res.error ? res.error : 'desktop call failed');
  return res.data;
}

contextBridge.exposeInMainWorld('deliveryDesktop', {
  isDesktop: true,
  platform: process.platform,
  // local files
  roots: () => call('local:roots'),
  overview: () => call('local:overview'),
  scan: (root, query, limit) => call('local:scan', { root, query, limit }),
  readText: (path) => call('local:readText', { path }),
  readBase64: (path) => call('local:readBase64', { path }),
  pickFolder: () => call('local:pickFolder'),
  pickFile: () => call('local:pickFile'),
  listDrives: () => call('local:listDrives'),
  grantFullDisk: () => call('local:grantFullDisk'),
  revokeFullDisk: () => call('local:revokeFullDisk'),
  fullDiskStatus: () => call('local:fullDiskStatus'),
  // Continuous local indexing: fires with an absolute path whenever an indexable file under an
  // allowed root is added/changed (debounced). Returns an unsubscribe function.
  onFileChanged: (cb) => {
    const listener = (_event, changedPath) => cb(changedPath);
    ipcRenderer.on('local:changed', listener);
    return () => ipcRenderer.removeListener('local:changed', listener);
  },
  // call assistant window
  openCallAssistant: (route) => call('call:openAssistant', { route }),
  // server URL configuration
  getServerUrl: () => call('config:getUrl'),
  setServerUrl: (url) => call('config:setUrl', { url }),
  openServerConfig: () => call('config:promptServerUrl'),
  // Best-effort email/UPN of the Windows account signed into this PC (Entra/hybrid-joined
  // machines only) — used to suggest/pre-fill the Microsoft sign-in screen. Null if undetectable.
  getPcAccountHint: () => call('local:pcAccountHint'),
  // custom (frameless) window titlebar controls
  windowMinimize: () => call('window:minimize'),
  windowToggleMaximize: () => call('window:toggleMaximize'),
  windowClose: () => call('window:close'),
  // Native OS notifications (Windows toasts). `actions` = [{ id: 'approve', label: 'Approuver' }, …]
  // become toast buttons where the platform supports it; see notify:show in main.js.
  notify: (payload) => call('notify:show', payload),
  notifyCapabilities: () => call('notify:capabilities'),
  // Toast body clicked → { id, link } (window already brought to front). Returns an unsubscribe fn.
  onNotificationOpen: (cb) => {
    const listener = (_event, data) => cb(data);
    ipcRenderer.on('notification:open', listener);
    return () => ipcRenderer.removeListener('notification:open', listener);
  },
  // Toast action button clicked → { id, action, link }. Returns an unsubscribe fn.
  onNotificationAction: (cb) => {
    const listener = (_event, data) => cb(data);
    ipcRenderer.on('notification:action', listener);
    return () => ipcRenderer.removeListener('notification:action', listener);
  },
  // Which window this page runs in: 'main', 'assistant' (call copilot) or 'recorder' (automation
  // recorder control). Background services (the automation runner) only run in 'main'.
  windowRole: windowRole(),
  // Companion activity awareness (foreground app/window as focus sessions). Opt-in: the renderer
  // only starts it after user consent + org policy; see desktop/src/activity.js.
  companion: {
    start: () => call('companion:start'),
    stop: () => call('companion:stop'),
    drain: () => call('companion:drain'),
    state: () => call('companion:state'),
    // Background/tray + launch-at-login (run like Discord/Docker; reopen from the notification area).
    getBackground: () => call('companion:getBackground'),
    setBackground: (patch) => call('companion:setBackground', patch || {}),
  },
  // Automatisations: local steps, recorder and background mode (see automation-local.js,
  // recorder.js, background.js). File bytes cross the bridge as Uint8Array.
  automation: {
    capabilities: () => call('automation:capabilities'),
    print: (payload) => call('automation:print', payload),
    printers: () => call('automation:printers'),
    saveFile: (payload) => call('automation:saveFile', payload),
    readFile: (payload) => call('automation:readFile', payload),
    listFiles: (payload) => call('automation:listFiles', payload),
    open: (payload) => call('automation:open', payload),
    powershell: (payload) => call('automation:powershell', payload),
    setActiveCount: (count) => call('automation:setActiveCount', { count }),
    getLaunchAtLogin: () => call('automation:getLaunchAtLogin'),
    setLaunchAtLogin: (enabled) => call('automation:setLaunchAtLogin', { enabled }),
    openApp: (payload) => call('automation:openApp', payload),
    windowAction: (payload) => call('automation:window', payload),
    click: (payload) => call('automation:click', payload),
    type: (payload) => call('automation:type', payload),
    keys: (payload) => call('automation:keys', payload),
    read: (payload) => call('automation:read', payload),
    readNotifications: (payload) => call('automation:readNotifications', payload),
    screenshot: (payload) => call('automation:screenshot', payload),
    browse: (payload) => call('automation:browse', payload),
    openBrowser: (payload) => call('automation:openBrowser', payload),
    // The interactive elements of a window (accessibility tree), for AI locator resolution.
    dumpElements: (payload) => call('automation:dumpElements', payload),
    // Live captions of a running Microsoft Teams meeting (speaker + text), read from Teams' own UI.
    readTeamsCaptions: (payload) => call('automation:teamsCaptions', payload || {}),
    // Vision (desktop/src/vision.js): look at the screen locally — image memory, OCR, accessibility —
    // and act at a position. The screenshot stays on this PC; only a text description goes out.
    visionLocate: (payload) => call('vision:locate', payload || {}),
    visionClickAt: (payload) => call('vision:clickAt', payload),
    visionRead: (payload) => call('vision:read', payload || {}),
    visionForget: (payload) => call('vision:forget', payload),
    startRecording: (opts) => call('recorder:start', opts || {}),
    pauseRecording: () => call('recorder:pause'),
    resumeRecording: () => call('recorder:resume'),
    stopRecording: () => call('recorder:stop'),
    cancelRecording: () => call('recorder:cancel'),
    recorderState: () => call('recorder:state'),
    addNarration: (text, at) => call('recorder:narration', { text, at }),
    takeRecording: () => call('recorder:takeBundle'),
    onRecorderState: (cb) => {
      const listener = (_event, data) => cb(data);
      ipcRenderer.on('recorder:state', listener);
      return () => ipcRenderer.removeListener('recorder:state', listener);
    },
    onRecordingReady: (cb) => {
      const listener = (_event, data) => cb(data);
      ipcRenderer.on('automation:recordingReady', listener);
      return () => ipcRenderer.removeListener('automation:recordingReady', listener);
    },
  },
});

function windowRole() {
  const arg = process.argv.find((a) => typeof a === 'string' && a.startsWith('--dm-window='));
  return arg ? arg.slice('--dm-window='.length) : 'main';
}

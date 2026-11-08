'use strict';
// Background mode for automations: while at least one automation is active (the renderer reports
// the count), closing the main window only hides it and DeliveryOps keeps running in the
// notification area so scheduled/mail-triggered runs still happen. Also the opt-in
// "Lancer au démarrage de Windows" (login item started with --hidden → straight to the tray).

const HIDDEN_ARG = '--hidden';

/** 32×32 tray icon drawn in code (brand-blue rounded square with the DeliveryOps node motif), so
 *  no binary asset has to ship with the app. BGRA bitmap for nativeImage.createFromBitmap. */
function drawTrayBitmap(size = 32) {
  const buf = Buffer.alloc(size * size * 4);
  const s = size / 26; // the brand mark is designed on a 26×26 grid (see the web app's logo)
  const radius = 7 * s;
  const circles = [
    { x: 8 * s, y: 13 * s, r: 3 * s, c: [255, 255, 255] },
    { x: 18 * s, y: 8 * s, r: 2.4 * s, c: [255, 178, 159] }, // BGR of #9fb2ff
    { x: 18 * s, y: 18 * s, r: 2.4 * s, c: [255, 178, 159] },
  ];
  const inRounded = (x, y) => {
    const cx = Math.min(Math.max(x, radius), size - radius);
    const cy = Math.min(Math.max(y, radius), size - radius);
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius;
  };
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const px = x + 0.5;
      const py = y + 0.5;
      const i = (y * size + x) * 4;
      if (!inRounded(px, py)) continue; // transparent corner
      let bgr = [214, 83, 53]; // #3553d6
      for (const c of circles) if ((px - c.x) ** 2 + (py - c.y) ** 2 <= c.r * c.r) bgr = c.c;
      buf[i] = bgr[0]; buf[i + 1] = bgr[1]; buf[i + 2] = bgr[2]; buf[i + 3] = 255;
    }
  }
  return buf;
}

/**
 * deps: { app, Tray, Menu, nativeImage, Notification, getMainWindow(), showMainWindow(),
 *         openRoute(route), loadConfig(), saveConfig(patch) }
 */
function createBackground(deps) {
  const { app, Tray, Menu, nativeImage } = deps;
  let tray = null;
  let activeCount = 0;
  let quitting = false;
  let hiddenHintShown = false;
  const startedHidden = process.argv.includes(HIDDEN_ARG);
  // Run in the background like Discord/Docker: closing the window hides it to the notification area
  // and DeliveryOps keeps running (so it can watch what you're doing and offer help). On by default;
  // toggled from the tray menu or the app. Persisted in config.
  let closeToTray = deps.loadConfig().closeToTray !== false;

  function keepAlive() { return closeToTray || activeCount > 0; }

  function icon() {
    const size = 32;
    try { return nativeImage.createFromBitmap(drawTrayBitmap(size), { width: size, height: size }); }
    catch { return nativeImage.createEmpty(); }
  }

  function menu() {
    return Menu.buildFromTemplate([
      { label: 'Ouvrir DeliveryOps', click: () => deps.showMainWindow() },
      { label: 'Ouvrir le compagnon', click: () => deps.openRoute('/companion') },
      { label: `Automatisations actives (${activeCount})`, click: () => deps.openRoute('/automations') },
      { type: 'separator' },
      { label: 'Rester dans la zone de notification', type: 'checkbox', checked: closeToTray,
        click: (item) => setCloseToTray(item.checked) },
      { label: 'Quitter', click: () => { quitting = true; app.quit(); } },
    ]);
  }

  function setCloseToTray(enabled) {
    closeToTray = !!enabled;
    deps.saveConfig({ closeToTray });
    refresh();
    return { closeToTray };
  }

  // The tray icon is present whenever DeliveryOps can keep running invisibly: close-to-tray is on,
  // automations are active, or the main window is currently hidden (e.g. started with --hidden).
  function refresh() {
    const wanted = closeToTray || activeCount > 0 || !isMainVisible();
    if (!wanted) {
      if (tray) { tray.destroy(); tray = null; }
      return;
    }
    if (!tray) {
      tray = new Tray(icon());
      tray.on('click', () => deps.showMainWindow());
      tray.on('double-click', () => deps.showMainWindow());
    }
    tray.setToolTip(activeCount ? `DeliveryOps — ${activeCount} automatisation(s) active(s)` : 'DeliveryOps');
    tray.setContextMenu(menu());
  }

  function isMainVisible() {
    const w = deps.getMainWindow();
    return !!(w && !w.isDestroyed() && w.isVisible());
  }

  return {
    startedHidden,
    setActiveCount(n) {
      activeCount = Math.max(0, Number(n) || 0);
      refresh();
      return { active: activeCount, tray: !!tray };
    },
    keepAlive,
    setCloseToTray,
    getSettings() { return { closeToTray, ...this.loginItem() }; },
    /** Hook for the main window's 'close' event: true = we hid it instead (caller preventDefault). */
    interceptClose() {
      if (quitting || !keepAlive()) return false;
      const w = deps.getMainWindow();
      if (!w || w.isDestroyed()) return false;
      w.hide();
      refresh();
      if (!hiddenHintShown && deps.Notification && deps.Notification.isSupported()) {
        hiddenHintShown = true;
        try {
          new deps.Notification({ title: 'DeliveryOps continue en arrière-plan',
            body: 'Toujours là pour vous aider. Cliquez l’icône dans la zone de notification (flèche ▲) pour rouvrir ou quitter.', silent: true }).show();
        } catch { /* best effort */ }
      }
      return true;
    },
    onMainShown() { refresh(); },
    markQuitting() { quitting = true; },
    isQuitting() { return quitting; },
    ensureTray() { refresh(); },
    loginItem() {
      const settings = app.getLoginItemSettings(loginArgs());
      return { openAtLogin: !!settings.openAtLogin, supported: process.platform === 'win32' || process.platform === 'darwin' };
    },
    setLoginItem(enabled) {
      app.setLoginItemSettings({ openAtLogin: !!enabled, ...loginArgs() });
      deps.saveConfig({ launchAtLogin: !!enabled });
      return this.loginItem();
    },
  };

  /** In dev (`electron .`) the exe is electron.exe and needs the app folder as first argument. */
  function loginArgs() {
    if (process.defaultApp && process.argv.length >= 2) {
      const pathMod = require('path');
      return { path: process.execPath, args: [pathMod.resolve(process.argv[1]), HIDDEN_ARG] };
    }
    return { path: process.execPath, args: [HIDDEN_ARG] };
  }
}

module.exports = { createBackground, drawTrayBitmap, HIDDEN_ARG };

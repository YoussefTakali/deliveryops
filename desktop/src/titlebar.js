'use strict';
// Custom in-app titlebar (Discord-style): the native frame/menu bar is removed (see main.js,
// frame: false) and this injects a thin draggable bar with the app name and window controls
// directly into whatever page is loaded, re-applied on every navigation.

const TITLEBAR_ID = '__dm_titlebar__';
const TITLEBAR_H = '32px';

function css() {
  return `
#${TITLEBAR_ID}{position:fixed;top:0;left:0;right:0;height:${TITLEBAR_H};z-index:2147483647;
  display:flex;align-items:center;justify-content:space-between;
  background:#0c111b;color:#e6eaf2;font:12px "Segoe UI",system-ui,sans-serif;
  -webkit-app-region:drag;user-select:none;border-bottom:1px solid #1c2536;
  transition:background-color .28s ease,color .28s ease,border-color .28s ease}
#${TITLEBAR_ID} .dm-tb-brand{display:flex;align-items:center;gap:8px;padding:0 10px;height:100%}
#${TITLEBAR_ID} .dm-tb-dot{width:14px;height:14px;border-radius:4px;background:#3553d6;flex:none}
#${TITLEBAR_ID} .dm-tb-title{opacity:.85;font-weight:600;letter-spacing:.2px}
#${TITLEBAR_ID} .dm-tb-controls{display:flex;height:100%;-webkit-app-region:no-drag}
#${TITLEBAR_ID} button{-webkit-app-region:no-drag;width:44px;height:${TITLEBAR_H};border:0;background:transparent;
  color:#aeb8ca;display:flex;align-items:center;justify-content:center;cursor:pointer;
  transition:background-color .2s ease,color .2s ease}
#${TITLEBAR_ID} button:hover{background:#1c2536;color:#fff}
#${TITLEBAR_ID} button.dm-tb-close:hover{background:#e5484d;color:#fff}
#${TITLEBAR_ID} svg{width:12px;height:12px;pointer-events:none}
/* Light palette, applied when the app's own theme (data-theme, or the OS in auto mode) is light —
   see applyTheme() below, which mirrors frontend/src/app/theme.ts's resolution logic. */
#${TITLEBAR_ID}.dm-tb-light{background:#ffffff;color:#16202e;border-bottom-color:#e3e8ef}
#${TITLEBAR_ID}.dm-tb-light button{color:#5d6b7e}
#${TITLEBAR_ID}.dm-tb-light button:hover{background:#f4f6f9;color:#16202e}
#${TITLEBAR_ID}.dm-tb-light button.dm-tb-close:hover{background:#e5484d;color:#fff}
`;
}

function html() {
  return `
<div id="${TITLEBAR_ID}">
  <div class="dm-tb-brand"><span class="dm-tb-dot"></span><span class="dm-tb-title">DeliveryOps</span></div>
  <div class="dm-tb-controls">
    <button class="dm-tb-settings" title="Configurer le serveur" aria-label="Configurer le serveur">
      <svg viewBox="0 0 16 16"><path fill="currentColor" d="M8 5a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm6.4 3a5.9 5.9 0 0 0-.1-1l1.6-1.2-1.5-2.6-1.9.6a5.9 5.9 0 0 0-1.7-1L10.4 1H7.6L7.2 2.8a5.9 5.9 0 0 0-1.7 1l-1.9-.6-1.5 2.6L3.7 7a5.9 5.9 0 0 0 0 2l-1.6 1.2 1.5 2.6 1.9-.6a5.9 5.9 0 0 0 1.7 1l.4 1.8h2.8l.4-1.8a5.9 5.9 0 0 0 1.7-1l1.9.6 1.5-2.6L14.3 9a5.9 5.9 0 0 0 .1-1Z"/></svg>
    </button>
    <button class="dm-tb-min" title="Réduire" aria-label="Réduire">
      <svg viewBox="0 0 12 12"><rect x="1" y="5.5" width="10" height="1" fill="currentColor"/></svg>
    </button>
    <button class="dm-tb-max" title="Agrandir" aria-label="Agrandir">
      <svg viewBox="0 0 12 12"><rect x="1.5" y="1.5" width="9" height="9" fill="none" stroke="currentColor"/></svg>
    </button>
    <button class="dm-tb-close" title="Fermer" aria-label="Fermer">
      <svg viewBox="0 0 12 12"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.2"/></svg>
    </button>
  </div>
</div>`;
}

/** Injected script: builds the bar once per document and wires it to the window-control bridge
 *  exposed by preload.js (window.deliveryDesktop.window*). Re-run on every 'dom-ready'. */
function injectedScript(isForeignPage) {
  return `(function(){
    // Expose the titlebar's height so the app's own fixed navbar/sidebar can offset below it
    // instead of hiding underneath (the app reads this instead of a blunt body margin).
    document.documentElement.style.setProperty('--dm-titlebar-h', '${TITLEBAR_H}');
    document.documentElement.classList.add('dm-desktop-app');
    if (document.getElementById('${TITLEBAR_ID}')) return;
    var style = document.createElement('style');
    style.textContent = ${JSON.stringify(css())}${isForeignPage ? ` + ${JSON.stringify(`\nbody{margin-top:${TITLEBAR_H} !important}\n`)}` : ''};
    document.head.appendChild(style);
    var wrap = document.createElement('div');
    wrap.innerHTML = ${JSON.stringify(html())};
    document.body.insertBefore(wrap.firstElementChild, document.body.firstChild);
    var bar = document.getElementById('${TITLEBAR_ID}');
    var bridge = window.deliveryDesktop;
    if (!bridge) return;
    bar.querySelector('.dm-tb-settings').addEventListener('click', function(){ bridge.openServerConfig(); });
    bar.querySelector('.dm-tb-min').addEventListener('click', function(){ bridge.windowMinimize(); });
    bar.querySelector('.dm-tb-close').addEventListener('click', function(){ bridge.windowClose(); });
    var maxBtn = bar.querySelector('.dm-tb-max');
    maxBtn.addEventListener('click', function(){ bridge.windowToggleMaximize(); });
    bar.addEventListener('dblclick', function(e){ if (e.target === bar) bridge.windowToggleMaximize(); });

    // Mirror the app's own light/dark resolution (frontend/src/app/theme.ts): an explicit
    // [data-theme] on <html> wins, otherwise follow the OS (prefers-color-scheme).
    var media = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
    function isDark() {
      var forced = document.documentElement.getAttribute('data-theme');
      if (forced === 'dark') return true;
      if (forced === 'light') return false;
      return !!(media && media.matches);
    }
    function applyTheme() { bar.classList.toggle('dm-tb-light', !isDark()); }
    applyTheme();
    new MutationObserver(applyTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    if (media) { try { media.addEventListener('change', applyTheme); } catch (e) { media.addListener(applyTheme); } }
  })();`;
}

/** Attach the custom titlebar to a BrowserWindow: injected after every load so it survives
 *  navigations — the built-in "configure server" page, and Microsoft's sign-in page, which the
 *  redirect auth flow navigates this (frameless) window to. On those foreign pages the bar is what
 *  keeps the window draggable and closable at all, and they don't know to leave room for it, so
 *  they also get a body offset; our own app positions itself with --dm-titlebar-h instead.
 *  `appOrigin` is a getter because the configured server URL can change at runtime. */
function attachTitlebar(win, appOrigin = () => '') {
  win.webContents.on('dom-ready', () => {
    const current = win.webContents.getURL() || '';
    const base = appOrigin() || '';
    const isForeignPage = !!base && !current.startsWith(base) && !current.startsWith('data:');
    win.webContents.executeJavaScript(injectedScript(isForeignPage)).catch(() => {});
  });
}

module.exports = { attachTitlebar };

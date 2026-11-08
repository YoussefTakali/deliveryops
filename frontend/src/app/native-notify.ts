import { Injectable, inject, signal } from '@angular/core';

import { AppNotification } from './api';
import { Desktop } from './desktop';

const PREF_KEY = 'dm.notify.desktop';          // 'on' | 'off'
const SEEN_PREFIX = 'dm.notify.seen.';          // + username → JSON array of notification ids
const MAX_SEEN = 500;
const MAX_AGE_MS = 24 * 60 * 60 * 1000;         // never toast something older than a day

export interface NativeNotice {
  id: string;
  title: string;
  body: string;
  link: string;
  /** Toast buttons. Only honoured by the desktop app (Windows toast XML / macOS actions). */
  actions?: { id: string; label: string }[];
}

type OpenHandler = (id: string, link: string) => void;
type ActionHandler = (id: string, action: string, link: string) => void;

function readStorage(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function writeStorage(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* storage unavailable */ }
}

/** Native OS notifications for DeliveryOps notifications.
 *  - Desktop app: native Windows toasts through the Electron bridge (deliveryDesktop.notify), with
 *    action buttons ("Approuver", "Ouvrir") routed back here via onNotificationAction.
 *  - Browser: the standard Web Notification API (click → focus the tab + navigate). Permission is
 *    only ever requested from an explicit user gesture (the toggle in the bell dropdown).
 *  Preference: on by default in the desktop app, opt-in in the browser; stored in localStorage. */
@Injectable({ providedIn: 'root' })
export class NativeNotify {
  private desktop = inject(Desktop);
  private onOpen: OpenHandler | null = null;
  private onAction: ActionHandler | null = null;
  /** Feature-owned toasts (e.g. automation confirmations, ids "automation-…") are routed to their
   *  own handlers instead of the app shell's generic ones. */
  private intercepts: { prefix: string; onOpen: OpenHandler; onAction: ActionHandler }[] = [];

  /** Desktop bridge available (vs. Web Notification API in a browser tab). */
  readonly viaDesktop = this.desktop.available && this.desktop.canNotify;
  readonly supported = this.viaDesktop || typeof window !== 'undefined' && 'Notification' in window;
  readonly enabled = signal<boolean>(this.readPref());
  readonly permission = signal<NotificationPermission | 'n/a'>(this.currentPermission());
  /** Whether toast action buttons actually work here (desktop build + protocol registered). */
  readonly actionsSupported = signal(false);

  constructor() {
    if (this.viaDesktop) {
      this.desktop.onNotificationOpen(e => this.dispatchOpen(e.id, e.link));
      this.desktop.onNotificationAction(e => this.dispatchAction(e.id, e.action, e.link));
      this.desktop.notifyCapabilities().then(c => this.actionsSupported.set(!!c.actions)).catch(() => {});
    }
    // A browser permission revoked from site settings while the pref is still "on" → reflect it.
    if (!this.viaDesktop && this.enabled() && this.currentPermission() !== 'granted') this.enabled.set(false);
  }

  /** Register what happens when a toast (or one of its buttons) is clicked. */
  handle(onOpen: OpenHandler, onAction: ActionHandler) { this.onOpen = onOpen; this.onAction = onAction; }

  /** Route toasts whose id starts with `prefix` to dedicated handlers (checked before handle()'s). */
  intercept(prefix: string, onOpen: OpenHandler, onAction: ActionHandler) {
    this.intercepts = [...this.intercepts.filter(i => i.prefix !== prefix), { prefix, onOpen, onAction }];
  }

  private dispatchOpen(id: string, link: string) {
    const hit = this.intercepts.find(i => id.startsWith(i.prefix));
    if (hit) hit.onOpen(id, link); else this.onOpen?.(id, link);
  }

  private dispatchAction(id: string, action: string, link: string) {
    const hit = this.intercepts.find(i => id.startsWith(i.prefix));
    if (hit) hit.onAction(id, action, link); else this.onAction?.(id, action, link);
  }

  /** Turn desktop notifications on/off. In the browser, turning them on asks for permission — call
   *  this from a click handler only. Returns an error message when the browser refused. */
  async setEnabled(on: boolean): Promise<string | null> {
    if (on && !this.viaDesktop) {
      if (!('Notification' in window)) return 'Ce navigateur ne prend pas en charge les notifications.';
      let perm = Notification.permission;
      if (perm === 'default') perm = await Notification.requestPermission();
      this.permission.set(perm);
      if (perm !== 'granted') {
        this.enabled.set(false);
        writeStorage(PREF_KEY, 'off');
        return 'Notifications bloquées par le navigateur — autorisez-les dans les paramètres du site.';
      }
    }
    this.enabled.set(on);
    writeStorage(PREF_KEY, on ? 'on' : 'off');
    return null;
  }

  /** From the latest poll, the notifications that are new since last time (unread, not yet shown
   *  on this device, less than a day old). Records every current id as seen so a reload never
   *  re-toasts. On the very first run for this user on this device nothing is returned — we don't
   *  replay the whole history as a burst of toasts. Called whether or not toasts are enabled, so
   *  turning them on later doesn't flush a backlog either. */
  takeNew(username: string, items: AppNotification[]): AppNotification[] {
    const key = SEEN_PREFIX + username;
    const raw = readStorage(key);
    let seen: string[] = [];
    try { seen = raw ? JSON.parse(raw) : []; } catch { seen = []; }
    const seenSet = new Set(seen);
    const now = Date.now();
    const fresh = raw === null ? [] : items.filter(n => {
      if (n.read || seenSet.has(n.id)) return false;
      const ts = Date.parse(n.ts);
      return Number.isNaN(ts) || now - ts < MAX_AGE_MS;
    });
    const merged = [...seen.filter(id => !items.some(n => n.id === id)), ...items.map(n => n.id)].slice(-MAX_SEEN);
    writeStorage(key, JSON.stringify(merged));
    return fresh.reverse(); // oldest first, so the newest toast ends up on top
  }

  /** Show one native notification (no-op when disabled/unsupported). `essential` bypasses the
   *  user's on/off preference in the desktop app — used for prompts the user explicitly opted into
   *  elsewhere (an automation set to "Demander confirmation avant chaque exécution"). */
  show(n: NativeNotice, opts: { essential?: boolean } = {}): void {
    if (!this.supported) return;
    if (!this.enabled() && !(opts.essential && this.viaDesktop)) return;
    if (this.viaDesktop) {
      this.desktop.notify({ id: n.id, title: n.title, body: n.body, link: n.link, actions: n.actions }).catch(() => {});
      return;
    }
    if (Notification.permission !== 'granted') return;
    try {
      const wn = new Notification(n.title, { body: n.body, tag: n.id, icon: '/favicon.ico' });
      wn.onclick = () => { window.focus(); wn.close(); this.dispatchOpen(n.id, n.link); };
    } catch { /* e.g. mobile Chrome requires a service worker for notifications */ }
  }

  private readPref(): boolean {
    const v = readStorage(PREF_KEY);
    if (v === 'on') return true;
    if (v === 'off') return false;
    return this.desktop.available && this.desktop.canNotify; // default: on in the desktop app, opt-in in the browser
  }

  private currentPermission(): NotificationPermission | 'n/a' {
    return typeof window !== 'undefined' && 'Notification' in window ? Notification.permission : 'n/a';
  }
}

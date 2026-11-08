import { Injectable, computed, effect, inject, signal } from '@angular/core';
import { finalize } from 'rxjs';

import { Api } from './api';
import { Auth } from './auth';
import { SOURCE_LANG, language } from './languages';

/** Translates the (French) interface into the chosen language, in place, for every page.
 *
 *  Works on the rendered DOM rather than per-template keys, so every existing page is covered without
 *  rewriting it: text nodes and the placeholder / title / aria-label attributes are swapped for their
 *  translation, and a MutationObserver re-applies it whenever Angular re-renders. Translations come from
 *  /api/i18n/translate (the platform LLM, cached org-wide) and are also kept in localStorage, so a known
 *  string is swapped before the browser paints it. French needs no work at all: the observer then
 *  returns immediately.
 *
 *  Never translated: anything inside [translate="no"] / .notranslate, code, pre, textarea, editable
 *  content, long texts (user content), and strings that look like e-mails, URLs, paths or codes.
 *  Numbers become placeholders ({0}…), so "12 tokens" and "40 tokens" share one translation. */
@Injectable({ providedIn: 'root' })
export class UiTranslator {
  private api = inject(Api);
  private auth = inject(Auth);

  readonly lang = signal<string>(readStorage('dm.lang') ?? SOURCE_LANG);
  readonly current = computed(() => language(this.lang()));
  /** Strings are being translated for the first time in this language. */
  readonly busy = signal(false);
  /** Why translation is unavailable (AI offline, blocked…), shown in the language menu. */
  readonly notice = signal<string | null>(null);

  private cache = new Map<string, string>();       // template -> translation, for the current language
  private failed = new Set<string>();              // templates the server couldn't translate (don't retry)
  private pending = new Set<string>();
  private inFlight = 0;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private texts = new WeakMap<Text, { src: string; out: string }>();
  private attrs = new WeakMap<Element, Record<string, { src: string; out: string }>>();
  private observer: MutationObserver | null = null;

  constructor() {
    // Strings met before sign-in (login gate) are sent once the session exists.
    effect(() => { if (this.auth.status() === 'authenticated' && this.pending.size) this.scheduleFlush(); });
  }

  start(): void {
    if (this.observer) return;
    this.observer = new MutationObserver(records => this.onMutations(records));
    this.observer.observe(document.documentElement, {
      subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS,
    });
    this.apply(this.lang());
  }

  /** Translation of a French interface string in the current language, when already known. */
  t(source: string): string {
    return this.isSource() ? source : this.translated(source) ?? source;
  }

  setLang(code: string): void {
    if (code === this.lang()) return;
    try { localStorage.setItem('dm.lang', code); } catch { /* storage unavailable */ }
    this.lang.set(code);
    this.apply(code);
  }

  // ------------------------------------------------------------------ language switch

  private apply(code: string): void {
    const lang = language(code);
    document.documentElement.lang = code;
    document.documentElement.dir = lang.rtl ? 'rtl' : 'ltr';
    this.restoreAll();
    this.cache = new Map(Object.entries(readJson(`dm.i18n.${code}`) ?? {}));
    this.failed.clear();
    this.pending.clear();
    this.notice.set(null);
    if (!this.isSource()) this.process(document.documentElement);
  }

  private isSource(): boolean { return this.lang().split('-')[0] === SOURCE_LANG; }

  private restoreAll(): void {
    const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n as Text;
      const rec = this.texts.get(t);
      if (rec && t.data === rec.out) t.data = rec.src;
      this.texts.delete(t);
    }
    for (const el of Array.from(document.querySelectorAll(ATTRS.map(a => `[${a}]`).join(',')))) {
      const recs = this.attrs.get(el);
      if (!recs) continue;
      for (const [name, rec] of Object.entries(recs)) if (el.getAttribute(name) === rec.out) el.setAttribute(name, rec.src);
      this.attrs.delete(el);
    }
  }

  // ------------------------------------------------------------------ DOM work

  private onMutations(records: MutationRecord[]): void {
    if (this.isSource()) return;
    for (const r of records) {
      if (r.type === 'childList') r.addedNodes.forEach(n => this.process(n));
      else if (r.type === 'characterData') this.process(r.target);
      else if (!excluded(r.target as Element)) this.translateAttrs(r.target as Element);
    }
  }

  private process(node: Node): void {
    if (node.nodeType === Node.TEXT_NODE) {
      if (!excluded(node.parentElement)) this.translateText(node as Text);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || excluded(node as Element)) return;
    this.translateAttrs(node as Element);
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: n => n.nodeType === Node.ELEMENT_NODE && isOptOut(n as Element) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n.nodeType === Node.TEXT_NODE) this.translateText(n as Text);
      else this.translateAttrs(n as Element);
    }
  }

  private translateText(t: Text): void {
    const rec = this.texts.get(t);
    if (rec && t.data === rec.out) return;          // our translation is still in place
    const out = this.translated(t.data);
    if (out === null) return;
    this.texts.set(t, { src: t.data, out });
    if (t.data !== out) t.data = out;
  }

  private translateAttrs(el: Element): void {
    for (const name of ATTRS) {
      const value = el.getAttribute(name);
      if (!value) continue;
      const recs = this.attrs.get(el) ?? {};
      if (recs[name] && value === recs[name].out) continue;
      const out = this.translated(value);
      if (out === null) continue;
      recs[name] = { src: value, out };
      this.attrs.set(el, recs);
      if (value !== out) el.setAttribute(name, out);
    }
  }

  /** The translation of a source string, or null when it isn't translatable / not known yet. */
  private translated(source: string): string | null {
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(source)!;
    const core = m[2].replace(/\s+/g, ' ');
    if (!translatable(core)) return null;
    const nums: string[] = [];
    const tpl = core.replace(NUMBER, n => `{${nums.push(n) - 1}}`);
    const hit = this.cache.get(tpl);
    if (hit === undefined) {
      if (!this.failed.has(tpl) && !this.pending.has(tpl)) { this.pending.add(tpl); this.scheduleFlush(); }
      return null;
    }
    return m[1] + hit.replace(/\{(\d+)\}/g, (_, i) => nums[+i] ?? '') + m[3];
  }

  // ------------------------------------------------------------------ server round-trip

  private scheduleFlush(): void {
    if (this.flushTimer || this.auth.status() !== 'authenticated') return;
    this.flushTimer = setTimeout(() => { this.flushTimer = null; this.flush(); }, 200);
  }

  private flush(): void {
    if (!this.pending.size || this.isSource()) return;
    const code = this.lang();
    const batch = [...this.pending].slice(0, 150);
    batch.forEach(t => this.pending.delete(t));
    this.inFlight++;
    this.busy.set(true);
    this.api.translateUi(code, this.current().english, batch).pipe(finalize(() => {
      this.inFlight--;
      if (!this.inFlight && !this.pending.size) this.busy.set(false);
    })).subscribe({
      next: res => {
        if (code !== this.lang()) return;
        for (const t of batch) {
          const out = res.translations[t];
          if (out !== undefined) this.cache.set(t, out); else this.failed.add(t);
        }
        if (!res.available) this.notice.set(res.reason ?? 'Traduction automatique indisponible.');
        writeJson(`dm.i18n.${code}`, Object.fromEntries([...this.cache].slice(-5000)));
        this.process(document.documentElement);
      },
      error: () => { if (code === this.lang()) batch.forEach(t => this.failed.add(t)); },
    });
    if (this.pending.size) this.scheduleFlush();
  }
}

// ------------------------------------------------------------------ helpers

const ATTRS = ['placeholder', 'title', 'aria-label'];
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'CODE', 'PRE', 'TEXTAREA', 'SVG', 'svg']);
const NUMBER = /\d+(?:[.,   ]\d+)*/g;
const MAX_LEN = 300;

function isOptOut(el: Element): boolean {
  return SKIP_TAGS.has(el.tagName) || el.getAttribute('translate') === 'no' || el.classList.contains('notranslate')
    || (el as HTMLElement).isContentEditable;
}

function excluded(el: Element | null): boolean {
  for (let e = el; e; e = e.parentElement) if (isOptOut(e)) return true;
  return false;
}

function translatable(s: string): boolean {
  if (s.length < 2 || s.length > MAX_LEN) return false;
  if ((s.match(/\p{L}/gu) ?? []).length < 2) return false;
  if (/^(https?:\/\/|www\.)/i.test(s) || /^[\w.+-]+@[\w-]+(\.[\w-]+)+$/.test(s)) return false;
  if (!/\s/.test(s) && /[\/\\_@:]|\.\w/.test(s)) return false;   // paths, codes, file names, keys
  return true;
}

function readStorage(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function readJson(key: string): Record<string, string> | null {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null'); } catch { return null; }
}

function writeJson(key: string, value: unknown): void {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* quota / unavailable */ }
}

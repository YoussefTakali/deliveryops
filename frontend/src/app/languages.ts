import { HttpInterceptorFn } from '@angular/common/http';

/** Interface languages. French is the source language of the UI; every other language is produced by
 *  machine translation (see ui-translator.ts). Names come from the browser's Intl data, so each
 *  language is shown in its own script ("Deutsch", "العربية", "日本語"…). */

export const SOURCE_LANG = 'fr';

/** Shown first in the picker. */
const PINNED = ['fr', 'en', 'ar', 'es', 'de', 'it', 'pt', 'zh-Hans'];

const CODES = [
  'af', 'aeb', 'am', 'ar', 'as', 'az', 'ba', 'be', 'bg', 'bm', 'bn', 'bo', 'br', 'bs', 'ca', 'ceb', 'ckb', 'co', 'cs',
  'cy', 'da', 'de', 'dv', 'ee', 'el', 'en', 'eo', 'es', 'et', 'eu', 'fa', 'ff', 'fi', 'fil', 'fj', 'fo', 'fr', 'fy',
  'ga', 'gd', 'gl', 'gn', 'gu', 'ha', 'haw', 'he', 'hi', 'hmn', 'hr', 'ht', 'hu', 'hy', 'id', 'ig', 'is', 'it', 'ja',
  'jv', 'ka', 'kab', 'kk', 'km', 'kn', 'ko', 'kri', 'ku', 'ky', 'la', 'lb', 'lg', 'ln', 'lo', 'lt', 'lv', 'mai', 'mg',
  'mi', 'mk', 'ml', 'mn', 'mr', 'ms', 'mt', 'my', 'nb', 'ne', 'nl', 'nn', 'ny', 'oc', 'om', 'or', 'pa', 'pl', 'ps',
  'pt', 'pt-BR', 'qu', 'rm', 'ro', 'ru', 'rw', 'sa', 'sd', 'si', 'sk', 'sl', 'sm', 'sn', 'so', 'sq', 'sr', 'st', 'su',
  'sv', 'sw', 'ta', 'te', 'tg', 'th', 'ti', 'tk', 'tn', 'to', 'tr', 'ts', 'tt', 'ug', 'uk', 'ur', 'uz', 'vi', 'wo',
  'xh', 'yi', 'yo', 'yue', 'zgh', 'zh-Hans', 'zh-Hant', 'zu',
];

const RTL = new Set(['aeb', 'ar', 'ckb', 'dv', 'fa', 'he', 'ps', 'sd', 'ug', 'ur', 'yi']);

export interface Language { code: string; native: string; english: string; french: string; rtl: boolean; }

function displayName(code: string, inLocale: string): string {
  try {
    const name = new Intl.DisplayNames([inLocale], { type: 'language' }).of(code);
    return name && name !== code ? name.charAt(0).toLocaleUpperCase(inLocale) + name.slice(1) : '';
  } catch { return ''; }
}

export function language(code: string): Language {
  const english = displayName(code, 'en') || code;
  return {
    code, english,
    native: displayName(code, code) || english,
    french: displayName(code, 'fr') || english,
    rtl: RTL.has(code.split('-')[0]),
  };
}

let cached: Language[] | null = null;

export function allLanguages(): Language[] {
  if (!cached) {
    const rest = CODES.filter(c => !PINNED.includes(c)).map(language)
      .sort((a, b) => a.french.localeCompare(b.french, 'fr'));
    cached = [...PINNED.map(language), ...rest];
  }
  return cached;
}

/** Sent with every API call so conversational AI answers come back in the interface language.
 *  Header values must be ASCII: the name is stripped of accents ("Norwegian Bokmål" → "Norwegian Bokmal"). */
export function languageHeaders(): Record<string, string> {
  let code = SOURCE_LANG;
  try { code = localStorage.getItem('dm.lang') || SOURCE_LANG; } catch { /* storage unavailable */ }
  const name = language(code).english.normalize('NFD').replace(/[^\x20-\x7e]/g, '');
  return { 'X-UI-Language': code, 'X-UI-Language-Name': name };
}

export const languageInterceptor: HttpInterceptorFn = (req, next) =>
  req.url.startsWith('/api/') ? next(req.clone({ setHeaders: languageHeaders() })) : next(req);

export function matchesLanguage(l: Language, query: string): boolean {
  const q = query.trim().toLocaleLowerCase();
  if (!q) return true;
  return [l.code, l.native, l.english, l.french].some(s => s.toLocaleLowerCase().includes(q));
}

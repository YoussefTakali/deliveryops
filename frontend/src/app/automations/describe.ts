// French, human-readable descriptions of automations: trigger summaries, step summaries, icons,
// status labels. Pure functions (no Angular) shared by the pages and the runner.

import { AutomationStatus, CatalogEntry, RunStatus, RunTrigger, Step, Trigger } from './models';

export const WEEKDAYS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'];
export const WEEKDAYS_SHORT = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];

export const AUTOMATION_STATUS: Record<AutomationStatus, { label: string; cls: string }> = {
  draft: { label: 'Brouillon', cls: '' },
  active: { label: 'Active', cls: 'green' },
  paused: { label: 'En pause', cls: 'amber' },
};

export const RUN_STATUS: Record<RunStatus, { label: string; cls: string }> = {
  awaiting_confirmation: { label: 'À confirmer', cls: 'amber' },
  running: { label: 'En cours', cls: 'accent' },
  succeeded: { label: 'Réussie', cls: 'green' },
  failed: { label: 'Échec', cls: 'red' },
  cancelled: { label: 'Annulée', cls: '' },
  skipped: { label: 'Ignorée', cls: '' },
};

export const RUN_TRIGGER: Record<RunTrigger, string> = {
  schedule: 'Planifiée', mail: 'Mail reçu', manual: 'Manuelle', test: 'Test à blanc',
};

function joinFr(parts: string[]): string {
  if (parts.length <= 1) return parts.join('');
  return parts.slice(0, -1).join(', ') + ' et ' + parts[parts.length - 1];
}

/** "Chaque lundi à 09:00", "Tous les jours ouvrés à 08:30", "Le 5 de chaque mois à 09:00"… */
export function triggerSummary(trigger: Trigger | null | undefined): string {
  if (!trigger || trigger.type === 'manual') return 'Manuelle (sur demande)';
  if (trigger.type === 'mail') {
    const bits: string[] = [];
    if (trigger.from_contains) bits.push(`de « ${trigger.from_contains} »`);
    if (trigger.subject_contains) bits.push(`dont l'objet contient « ${trigger.subject_contains} »`);
    if (trigger.has_attachment) bits.push('avec pièce jointe');
    return 'À la réception d’un mail' + (bits.length ? ' ' + bits.join(', ') : '');
  }
  const at = trigger.at ? ` à ${trigger.at}` : '';
  switch (trigger.every) {
    case 'hours': {
      const n = Math.max(1, Number(trigger.interval_hours) || 1);
      return n === 1 ? 'Toutes les heures' : `Toutes les ${n} heures`;
    }
    case 'day': return `Chaque jour${at}`;
    case 'month': return `Le ${trigger.day_of_month || 1}${trigger.day_of_month === 1 || !trigger.day_of_month ? 'er' : ''} de chaque mois${at}`;
    case 'week': {
      const days = Array.from(new Set(trigger.weekdays ?? [])).filter(d => d >= 0 && d <= 6).sort();
      if (!days.length) return `Chaque semaine${at}`;
      if (days.length === 7) return `Chaque jour${at}`;
      if (days.length === 5 && days.every((d, i) => d === i)) return `Tous les jours ouvrés${at}`;
      return `Chaque ${joinFr(days.map(d => WEEKDAYS[d]))}${at}`;
    }
  }
  return 'Planifiée';
}

/** "lun. 21 sept. à 09:00" (or "aujourd'hui à 09:00" / "demain à 09:00"). */
export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const time = d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  const today = new Date();
  const dayDiff = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
    - new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()) / 86_400_000);
  if (dayDiff === 0) return `aujourd’hui à ${time}`;
  if (dayDiff === 1) return `demain à ${time}`;
  if (dayDiff === -1) return `hier à ${time}`;
  const date = d.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) });
  return `${date} à ${time}`;
}

export function browserTimezone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Paris'; } catch { return 'Europe/Paris'; }
}

// ------------------------------------------------------------------ steps

/** Stroke icon paths (24×24, drawn with the sidebar's .ic style) per step family. */
const ICONS: Record<string, string[]> = {
  mail: ['M4 6h16v12H4z', 'm4 7 8 6 8-6'],
  send: ['M21 3 10 14', 'M21 3l-7 18-4-7-7-4z'],
  attach: ['M20 11.5 12.5 19a5 5 0 0 1-7-7l8-8a3.3 3.3 0 0 1 4.7 4.7l-8 8a1.7 1.7 0 0 1-2.4-2.4l7.3-7.3'],
  table: ['M4 5h16v14H4z', 'M4 10h16M4 15h16M10 5v14'],
  doc: ['M14 3.5H6.5a1 1 0 0 0-1 1v15a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V8z', 'M14 3.5V8h4.5M9 13h6M9 16.5h4'],
  ai: ['M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z', 'M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z'],
  print: ['M7 9V4h10v5', 'M7 17H5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-2', 'M7 14h10v6H7z'],
  save: ['M12 4v11', 'M7.5 10.5 12 15l4.5-4.5', 'M5 19.5h14'],
  folder: ['M3.5 6.5a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z'],
  open: ['M14 5h5v5', 'M19 5l-8 8', 'M17 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h4'],
  terminal: ['M4 5h16v14H4z', 'm7.5 10 2.5 2-2.5 2', 'M12 14.5h4'],
  confirm: ['M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z', 'm8.5 12.2 2.4 2.4 4.6-4.9'],
  step: ['M5 12h14', 'M13 6l6 6-6 6'],
  app: ['M3.5 5.5h17v13h-17z', 'M3.5 9h17', 'M6 7.2h.01M8 7.2h.01'],
  click: ['M9 4v5', 'M4.8 5.8 7.6 8.6', 'M4 10h5', 'm11 11 8.5 3.2-3.6 1.4-1.4 3.6z'],
  keyboard: ['M3.5 7h17v10h-17z', 'M7 10h.01M10 10h.01M13 10h.01M16 10h.01M8 14h8'],
  eye: ['M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z', 'M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z'],
  bell: ['M18 8.5a6 6 0 0 0-12 0c0 6.5-2.5 8.5-2.5 8.5h17S18 15 18 8.5', 'M13.7 20.5a2 2 0 0 1-3.4 0'],
  camera: ['M4 7.5h3.5L9 5.5h6l1.5 2H20v11H4z', 'M12 10a3 3 0 1 0 0 6 3 3 0 0 0 0-6z'],
  globe: ['M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z', 'M3.5 12h17', 'M12 3.5c2.5 2.6 3.5 5.5 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.5-3.5-8.5s1-5.9 3.5-8.5z'],
  clock: ['M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z', 'M12 7.5V12l3 2'],
};
const TYPE_ICON: Record<string, string> = {
  'mail.search': 'mail', 'mail.get_attachment': 'attach', 'file.extract_table': 'table', 'doc.fill_template': 'doc',
  'mail.send': 'send', 'ai.generate': 'ai', 'file.create': 'doc', 'local.print': 'print', 'local.save_file': 'save',
  'local.read_file': 'folder', 'local.list_files': 'folder', 'local.open': 'open', 'local.powershell': 'terminal', 'flow.confirm': 'confirm',
  'desktop.open_app': 'app', 'desktop.window': 'app', 'desktop.click': 'click', 'desktop.type': 'keyboard', 'desktop.keys': 'keyboard',
  'desktop.read': 'eye', 'desktop.read_notifications': 'bell', 'desktop.screenshot': 'camera', 'web.browse': 'globe',
  'ai.vision': 'ai', 'flow.wait': 'clock',
  'vision.click': 'eye', 'vision.type': 'eye', 'vision.wait': 'eye', 'vision.read': 'eye',
};
export function stepIcon(type: string): string[] { return ICONS[TYPE_ICON[type] ?? 'step']; }

const q = (v: unknown) => `« ${String(v).length > 60 ? String(v).slice(0, 59) + '…' : String(v)} »`;
function refOrValue(v: unknown): string {
  if (v === undefined || v === null || v === '') return '';
  if (typeof v === 'string') {
    const m = /^\s*\{\{\s*(.+?)\s*\}\}\s*$/.exec(v);
    if (m) return friendlyRef(m[1]);
    return q(v);
  }
  if (Array.isArray(v)) return v.map(refOrValue).join(', ');
  if (typeof v === 'object') return 'des valeurs structurées';
  return String(v);
}
/** "steps.s3.rows" → "le résultat de s3 (rows)"; "item.Email" → "la colonne « Email » de l'élément". */
export function friendlyRef(expr: string): string {
  const e = expr.trim();
  if (e === 'item') return 'l’élément courant';
  if (e.startsWith('item.')) return `la colonne « ${e.slice(5)} »`;
  if (e.startsWith('inputs.')) return `l’entrée « ${e.slice(7).split('.')[0]} »`;
  if (e.startsWith('trigger.message')) return 'le mail déclencheur';
  const m = /^steps\.([^.]+)\.?(.*)$/.exec(e);
  if (m) return `le résultat de l’étape ${m[1]}${m[2] ? ` (${m[2].replace('$index.', '')})` : ''}`;
  return `{{${e}}}`;
}

/** One-line French summary of what a step does with its params. */
export function stepSummary(step: Step, entry?: CatalogEntry): string {
  const p = step.params ?? {};
  const parts: string[] = [];
  switch (step.type) {
    case 'mail.search':
      if (p['from_contains']) parts.push(`de ${refOrValue(p['from_contains'])}`);
      if (p['subject_contains']) parts.push(`objet ${refOrValue(p['subject_contains'])}`);
      if (p['body_contains']) parts.push(`contenant ${refOrValue(p['body_contains'])}`);
      if (p['has_attachment']) parts.push('avec pièce jointe');
      parts.push(`${p['since_days'] ?? 7} derniers jours`);
      return 'Mails ' + parts.join(' · ');
    case 'mail.get_attachment':
      return `Pièce jointe${p['name_contains'] ? ` contenant ${refOrValue(p['name_contains'])}` : ''} du mail ${refOrValue(p['message_id']) || '—'}`;
    case 'file.extract_table':
      return `Lire le tableau de ${refOrValue(p['file_id']) || '—'}${p['sheet'] ? ` (feuille ${refOrValue(p['sheet'])})` : ''}`;
    case 'doc.fill_template':
      return `Remplir ${refOrValue(p['template_file_id']) || 'le modèle'} avec ${refOrValue(p['data']) || '—'}${p['output_name'] ? ` → ${refOrValue(p['output_name'])}` : ''}`;
    case 'mail.send':
      return `À ${refOrValue(p['to']) || '—'} · objet ${refOrValue(p['subject']) || '—'}${p['attachments'] ? ' · avec pièce(s) jointe(s)' : ''}`;
    case 'ai.generate':
      return `${refOrValue(p['instruction']) || '—'}${p['output'] === 'json' ? ' (JSON)' : ''}`;
    case 'file.create':
      return `Créer ${refOrValue(p['name']) || 'un fichier'}`;
    case 'local.print':
      return `Imprimer ${refOrValue(p['file_id'] ?? p['path']) || '—'}${p['printer'] ? ` sur ${refOrValue(p['printer'])}` : ''}${Number(p['copies']) > 1 ? ` · ${p['copies']} copies` : ''}`;
    case 'local.save_file':
      return `Enregistrer ${refOrValue(p['file_id']) || '—'} dans ${refOrValue(p['folder']) || '—'}`;
    case 'local.read_file': return `Lire ${refOrValue(p['path']) || '—'}`;
    case 'local.list_files': return `Fichiers de ${refOrValue(p['folder']) || '—'}${p['pattern'] ? ` (${p['pattern']})` : ''}`;
    case 'local.open': return `Ouvrir ${refOrValue(p['target']) || '—'}`;
    case 'local.powershell': {
      const lines = String(p['script'] ?? '').split('\n').filter(l => l.trim()).length;
      return `Script de ${lines} ligne(s) — approbation requise`;
    }
    case 'flow.confirm': return `Demander : ${refOrValue(p['message']) || '—'}`;
    case 'flow.wait': return `Attendre ${p['seconds'] ?? '—'} s`;
    case 'desktop.open_app':
      return `Ouvrir ${refOrValue(p['app']) || '—'}${p['args'] ? ` avec ${refOrValue(p['args'])}` : ''}${p['wait_window'] ? ` · attendre ${refOrValue(p['wait_window'])}` : ''}`;
    case 'desktop.window': {
      const verbs: Record<string, string> = { focus: 'Mettre au premier plan', wait: 'Attendre l’ouverture de', wait_close: 'Attendre la fermeture de',
        close: 'Fermer', maximize: 'Agrandir', minimize: 'Réduire' };
      return `${verbs[String(p['action'] || 'focus')] ?? 'Fenêtre'} ${refOrValue(p['title_contains']) || '—'}`;
    }
    case 'desktop.click':
      return `${p['double'] ? 'Double-cliquer' : 'Cliquer'} sur ${refOrValue(p['name'] || p['automation_id']) || '—'}${p['control_type'] ? ` (${p['control_type']})` : ''} dans ${refOrValue(p['window']) || '—'}`;
    case 'desktop.type':
      return `Saisir ${refOrValue(p['text']) || '—'} dans ${refOrValue(p['name'] || p['automation_id']) || 'le champ actif'} · ${refOrValue(p['window']) || '—'}${p['submit'] ? ' puis Entrée' : ''}`;
    case 'vision.click':
      return `${p['double'] ? 'Double-cliquer' : p['right'] ? 'Clic droit' : 'Cliquer'} (vision) sur ${refOrValue(p['target']) || '—'}${p['window'] ? ` dans ${refOrValue(p['window'])}` : ''}`;
    case 'vision.type':
      return `Saisir ${refOrValue(p['text']) || '—'} dans ${refOrValue(p['target']) || '—'} (vision)${p['submit'] ? ' puis Entrée' : ''}`;
    case 'vision.wait': return `Attendre de voir ${refOrValue(p['target']) || '—'}${p['window'] ? ` dans ${refOrValue(p['window'])}` : ''}`;
    case 'vision.read': return `Lire le texte visible${p['window'] ? ` de ${refOrValue(p['window'])}` : ' à l’écran'}`;
    case 'desktop.keys': return `Touches ${refOrValue(p['keys']) || '—'}${p['window'] ? ` dans ${refOrValue(p['window'])}` : ''}`;
    case 'desktop.read': return `Lire ${refOrValue(p['name'] || p['automation_id']) || 'le texte'} de ${refOrValue(p['window']) || '—'}`;
    case 'desktop.read_notifications':
      return `Notifications Windows${p['app_contains'] ? ` de ${refOrValue(p['app_contains'])}` : ''}${p['text_contains'] ? ` contenant ${refOrValue(p['text_contains'])}` : ''}`;
    case 'desktop.screenshot': return `Capturer ${p['window'] ? refOrValue(p['window']) : 'l’écran'}`;
    case 'ai.vision': return `Analyser ${refOrValue(p['file_id']) || 'une image'} : ${refOrValue(p['question']) || '—'}`;
    case 'web.browse': {
      const n = Array.isArray(p['actions']) ? p['actions'].length : 0;
      return `Aller sur ${refOrValue(p['url']) || '—'}${n ? ` · ${n} action(s)` : ''}${p['show'] ? ' (visible)' : ''}`;
    }
  }
  const generic = (entry?.params ?? []).filter(x => p[x.name] !== undefined && p[x.name] !== '').slice(0, 3)
    .map(x => `${x.name} : ${refOrValue(p[x.name])}`);
  return generic.join(' · ') || entry?.title || step.type;
}

/** Short summary of a step output for run logs ("12 ligne(s)", "Convention Léa.docx"…). */
export function outputSummary(output: unknown): string {
  if (output === null || output === undefined) return '';
  if (Array.isArray(output)) {
    const ok = output.filter(o => o !== null && o !== undefined).length;
    return `${output.length} itération(s)${ok !== output.length ? `, ${output.length - ok} sans résultat` : ''}`;
  }
  if (typeof output !== 'object') return String(output).slice(0, 200);
  const o = output as Record<string, any>;
  if (o['preview']) return 'Aperçu (test à blanc) : ' + clip(JSON.stringify(o['preview']), 240);
  if (typeof o['count'] === 'number' && Array.isArray(o['messages'])) return `${o['count']} mail(s) trouvé(s)${o['latest']?.subject ? ` — dernier : « ${o['latest'].subject} »` : ''}`;
  if (typeof o['count'] === 'number' && Array.isArray(o['rows'])) return `${o['count']} ligne(s) · colonnes : ${(o['columns'] ?? []).slice(0, 6).join(', ')}`;
  if (typeof o['count'] === 'number' && Array.isArray(o['files'])) return `${o['count']} fichier(s)`;
  if (o['file']?.name) return `Fichier « ${o['file'].name} »${o['missing']?.length ? ` · champs sans valeur : ${o['missing'].join(', ')}` : ''}`;
  if (o['sent'] === true) return `Envoyé à ${[].concat(o['to'] ?? []).join(', ')}`;
  if (o['printed'] === true) return `Imprimé${o['printer'] ? ` (${o['printer']})` : ''}`;
  if (o['path']) return `Enregistré : ${o['path']}`;
  if (o['opened']) return 'Ouvert';
  if ('exit_code' in o) return `Code de sortie ${o['exit_code']}${o['stdout'] ? ` · ${clip(String(o['stdout']).split('\n')[0], 160)}` : ''}`;
  if (typeof o['text'] === 'string') return clip(o['text'], 200);
  if (o['confirmed']) return 'Confirmé';
  return clip(JSON.stringify(o), 240);
}

export function clip(s: string, max: number): string { return s.length > max ? s.slice(0, max - 1) + '…' : s; }

/** Durations like "1 min 05 s". */
export function duration(fromIso?: string | null, toIso?: string | null): string {
  if (!fromIso) return '';
  const ms = (toIso ? Date.parse(toIso) : Date.now()) - Date.parse(fromIso);
  if (!Number.isFinite(ms) || ms < 0) return '';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
}

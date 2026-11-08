// Dataset parsing for automations: turn a pasted table into row objects and back. Pure (no Angular,
// no DOM) so it is unit-tested with plain node (see dataset.spec.ts). A dataset input makes the
// whole automation replay once per row, with the row exposed to steps as {{row.<column>}}.

/** Split one delimited line, honouring "quoted, fields" and "" as an escaped quote. */
function splitDelimited(line: string, delim: string): string[] {
  const out: string[] = [];
  let cur = '', inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"') { if (line[i + 1] === '"') { cur += '"'; i++; } else inQuotes = false; }
      else cur += c;
    } else if (c === '"') inQuotes = true;
    else if (c === delim) { out.push(cur); cur = ''; }
    else cur += c;
  }
  out.push(cur);
  return out.map(s => s.trim());
}

/** Parse a pasted table (CSV / TSV / semicolon) into row objects keyed by the header line.
 *  Delimiter is auto-detected from the header (tab, then ';', then ','). Blank lines are ignored;
 *  an empty column header becomes colonne_N so nothing is silently dropped. */
export function parseTable(text: string): Record<string, string>[] {
  const lines = String(text ?? '').replace(/\r\n?/g, '\n').split('\n').filter(l => l.trim() !== '');
  if (lines.length < 2) return [];
  const delim = lines[0].includes('\t') ? '\t' : lines[0].includes(';') ? ';' : ',';
  const headers = splitDelimited(lines[0], delim).map((h, i) => h || `colonne_${i + 1}`);
  const rows: Record<string, string>[] = [];
  for (const line of lines.slice(1)) {
    const cells = splitDelimited(line, delim);
    const row: Record<string, string> = {};
    headers.forEach((h, i) => { row[h] = cells[i] ?? ''; });
    rows.push(row);
  }
  return rows;
}

/** Rebuild a comma-separated table from row objects, so a saved dataset stays editable in a textarea. */
export function rowsToCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return '';
  const headers = Array.from(new Set(rows.flatMap(r => Object.keys(r))));
  const esc = (v: unknown) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [headers.join(','), ...rows.map(r => headers.map(h => esc(r[h])).join(','))].join('\n');
}

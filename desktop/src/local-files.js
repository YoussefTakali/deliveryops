'use strict';
// Local filesystem access for the Electron main process. Pure Node so it can be unit-tested with
// `node` (see test-local-files.js) without launching Electron. Read-only; every read is checked to
// stay within an allow-listed root so a compromised renderer can't traverse to arbitrary paths.

const fs = require('fs');
const path = require('path');
const os = require('os');

const EXCLUDED_DIRS = new Set([
  'node_modules', '.git', '.venv', 'venv', 'env', '__pycache__', '.cache', '.npm', '.gradle', '.m2',
  'dist', 'build', '.angular', '.next', '.idea', '.vscode', 'AppData', '$Recycle.Bin', 'System Volume Information',
  'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
]);

// Text we can preview in-process. PDF/DOCX/XLSX are extracted by the backend on ingest instead.
const TEXT_EXTS = new Set(['.txt', '.md', '.csv', '.tsv', '.json', '.log', '.xml', '.yaml', '.yml',
  '.py', '.js', '.ts', '.html', '.css', '.java', '.sql', '.sh', '.ini', '.cfg', '.env']);
const DOC_EXTS = new Set(['.pdf', '.docx', '.xlsx', '.pptx', '.doc', '.xls']);
const IMG_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp']);

function defaultRoots() {
  const home = os.homedir();
  const candidates = ['Desktop', 'Documents', 'Downloads'].map((d) => path.join(home, d));
  const existing = candidates.filter((p) => safeIsDir(p));
  return existing.length ? existing : [home];
}

// "Index everything on this PC": every drive letter that exists (Windows). EXCLUDED_DIRS still
// prunes system/noise folders (Windows, Program Files, AppData, node_modules...) during scans.
function listDrives() {
  if (process.platform !== 'win32') return [os.homedir()];
  const out = [];
  for (let i = 65; i <= 90; i++) {
    const drive = String.fromCharCode(i) + ':' + path.sep;
    if (safeIsDir(drive)) out.push(drive);
  }
  return out;
}


function safeIsDir(p) {
  try { return fs.statSync(p).isDirectory(); } catch { return false; }
}

function within(target, roots) {
  const resolved = path.resolve(target);
  return roots.some((root) => {
    const r = path.resolve(root);
    if (resolved === r) return true;
    // path.relative handles a root's own trailing separator correctly (path.resolve("C:\\") keeps
    // it, so naively appending path.sep before startsWith would double it and never match — this
    // is what made every file under a full-disk drive root report "path outside allowed roots").
    const rel = path.relative(r, resolved);
    return rel !== '' && !rel.startsWith('..') && !path.isAbsolute(rel);
  });
}

function classify(ext) {
  if (TEXT_EXTS.has(ext)) return 'text';
  if (DOC_EXTS.has(ext)) return 'document';
  if (IMG_EXTS.has(ext)) return 'image';
  return 'other';
}

// Worth sending to the knowledge index (text we can read + documents the backend extracts).
// Binaries (images, executables, archives...) are skipped — indexing them as text would just
// feed the search index garbage.
function isIndexable(ext) {
  return TEXT_EXTS.has(ext) || DOC_EXTS.has(ext);
}

// A query matches a file by name OR by its content (for plain-text kinds — PDFs/DOCX etc. would
// need the backend's extractors, out of scope for this quick filesystem-level search). Bounded so
// a broad scan (e.g. a whole drive) can't turn into reading thousands of files: only the first
// CONTENT_SEARCH_MAX_BYTES of a file are checked, and at most CONTENT_SEARCH_MAX_READS files are
// opened per scan() call — once the budget is spent, matching falls back to filename only.
const CONTENT_SEARCH_MAX_BYTES = 300_000;
const CONTENT_SEARCH_MAX_READS = 500;

function quickContentMatch(fullPath, q, budget) {
  if (budget.reads >= CONTENT_SEARCH_MAX_READS) return false;
  budget.reads++;
  try {
    const fd = fs.openSync(fullPath, 'r');
    try {
      const size = fs.fstatSync(fd).size;
      const len = Math.min(size, CONTENT_SEARCH_MAX_BYTES);
      if (len === 0) return false;
      const buf = Buffer.alloc(len);
      fs.readSync(fd, buf, 0, len, 0);
      return buf.toString('utf8').toLowerCase().includes(q);
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return false; // unreadable/locked/binary-decodes-weird — just skip it, don't fail the scan
  }
}

// Breadth-first scan under `root`, pruning excluded/hidden dirs, capped at `limit` entries. With a
// `query`, a file matches by filename OR by its own content (see quickContentMatch above).
function scan({ root, query = '', limit = 4000 }) {
  if (!safeIsDir(root)) throw new Error('not a directory: ' + root);
  const q = String(query).trim().toLowerCase();
  const out = [];
  const queue = [root];
  const budget = { reads: 0 };
  while (queue.length && out.length < limit) {
    const dir = queue.shift();
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      if (e.name.startsWith('.') || e.name.startsWith('~$')) continue;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (!EXCLUDED_DIRS.has(e.name)) queue.push(full);
        continue;
      }
      if (!e.isFile()) continue;
      const ext = path.extname(e.name).toLowerCase();
      let matchedIn;
      if (q) {
        const nameMatch = e.name.toLowerCase().includes(q);
        if (nameMatch) matchedIn = 'name';
        else if (TEXT_EXTS.has(ext) && quickContentMatch(full, q, budget)) matchedIn = 'content';
        else continue;
      }
      let stat;
      try { stat = fs.statSync(full); } catch { continue; }
      out.push({
        path: full, name: e.name, dir, ext, kind: classify(ext),
        size: stat.size, modified: stat.mtime.toISOString(),
        ...(matchedIn ? { matched_in: matchedIn } : {}),
      });
      if (out.length >= limit) break;
    }
  }
  out.sort((a, b) => b.modified.localeCompare(a.modified));
  return { root, count: out.length, truncated: out.length >= limit, items: out };
}

function overview(roots) {
  const perRoot = [];
  for (const root of roots) {
    if (!safeIsDir(root)) { perRoot.push({ root, exists: false }); continue; }
    const scanned = scan({ root, limit: 6000 });
    const byKind = {};
    const byExt = {};
    let totalSize = 0;
    for (const f of scanned.items) {
      byKind[f.kind] = (byKind[f.kind] || 0) + 1;
      if (f.ext) byExt[f.ext] = (byExt[f.ext] || 0) + 1;
      totalSize += f.size;
    }
    const topExt = Object.entries(byExt).sort((a, b) => b[1] - a[1]).slice(0, 10);
    perRoot.push({
      root, exists: true, files: scanned.count, truncated: scanned.truncated,
      total_size: totalSize, by_kind: byKind, top_extensions: topExt,
      recent: scanned.items.slice(0, 12),
    });
  }
  return { roots: perRoot };
}

function readText(target, roots, maxBytes = 200000) {
  if (!within(target, roots)) throw new Error('path outside allowed roots');
  const ext = path.extname(target).toLowerCase();
  if (!TEXT_EXTS.has(ext)) return { path: target, ext, previewable: false, kind: classify(ext) };
  const fd = fs.openSync(target, 'r');
  try {
    const size = fs.fstatSync(fd).size;
    const buf = Buffer.alloc(Math.min(size, maxBytes));
    fs.readSync(fd, buf, 0, buf.length, 0);
    return { path: target, ext, previewable: true, truncated: size > maxBytes, text: buf.toString('utf8') };
  } finally {
    fs.closeSync(fd);
  }
}

// Read raw bytes (base64) for uploading a document to the backend for extraction/ingest.
function readBase64(target, roots, maxBytes = 25000000) {
  if (!within(target, roots)) throw new Error('path outside allowed roots');
  const size = fs.statSync(target).size;
  if (size > maxBytes) throw new Error('file too large: ' + size);
  return { name: path.basename(target), base64: fs.readFileSync(target).toString('base64'), size };
}

module.exports = { defaultRoots, listDrives, scan, overview, readText, readBase64, within, classify, isIndexable, EXCLUDED_DIRS };

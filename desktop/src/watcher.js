'use strict';
// Continuous local indexing: watches every allow-listed root and reports back (debounced) whenever
// an indexable file is added or changed, so the renderer can push it straight into the knowledge
// base without the user re-scanning manually. Pure Node (fs.watch), no extra dependency.
//
// fs.watch's `recursive: true` option works on Windows and macOS (not Linux) — acceptable here
// since this desktop shell targets Windows. One native watcher per root; adding a root that is a
// sub/superpath of an already-watched one is skipped to avoid duplicate events.

const fs = require('fs');
const path = require('path');
const lf = require('./local-files');

const DEBOUNCE_MS = 1500;

class Watcher {
  constructor(onChange) {
    this.onChange = onChange; // (absolutePath: string) => void
    this.watched = new Map(); // root -> fs.FSWatcher
    this.pending = new Map(); // path -> timeout handle
  }

  _isRedundant(root) {
    const r = path.resolve(root);
    for (const existing of this.watched.keys()) {
      const e = path.resolve(existing);
      if (r === e || r.startsWith(e + path.sep) || e.startsWith(r + path.sep)) return true;
    }
    return false;
  }

  watch(root) {
    if (this._isRedundant(root)) return;
    let fsWatcher;
    try {
      fsWatcher = fs.watch(root, { recursive: true }, (_event, filename) => {
        if (!filename) return;
        const full = path.isAbsolute(filename) ? filename : path.join(root, filename);
        this._debounced(full);
      });
    } catch {
      return; // e.g. permission denied on a system folder — skip silently
    }
    this.watched.set(root, fsWatcher);
  }

  _debounced(full) {
    const base = path.basename(full);
    if (base.startsWith('.') || base.startsWith('~$')) return;
    const parts = full.split(path.sep);
    if (parts.some((p) => lf.EXCLUDED_DIRS.has(p))) return;
    const ext = path.extname(full).toLowerCase();
    if (!lf.isIndexable(ext)) return;

    clearTimeout(this.pending.get(full));
    this.pending.set(full, setTimeout(() => {
      this.pending.delete(full);
      fs.stat(full, (err, stat) => {
        if (err || !stat.isFile() || stat.size > 8 * 1024 * 1024) return; // gone, or too large
        this.onChange(full);
      });
    }, DEBOUNCE_MS));
  }

  unwatchAll() {
    for (const w of this.watched.values()) { try { w.close(); } catch { /* already closed */ } }
    this.watched.clear();
    for (const t of this.pending.values()) clearTimeout(t);
    this.pending.clear();
  }
}

module.exports = { Watcher };

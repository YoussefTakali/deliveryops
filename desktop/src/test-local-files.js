'use strict';
// Plain-node sanity test for local-files.js (no Electron needed): node src/test-local-files.js
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const lf = require('./local-files');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dm-lf-'));
fs.mkdirSync(path.join(tmp, 'sub'));
fs.mkdirSync(path.join(tmp, 'node_modules'));
fs.writeFileSync(path.join(tmp, 'note.txt'), 'hello world contract SLA');
fs.writeFileSync(path.join(tmp, 'sub', 'report.md'), '# Report\nbatch failure');
fs.writeFileSync(path.join(tmp, 'node_modules', 'junk.txt'), 'should be excluded');
fs.writeFileSync(path.join(tmp, 'data.pdf'), '%PDF-1.4 fake');

const scanned = lf.scan({ root: tmp, limit: 100 });
const names = scanned.items.map((f) => f.name).sort();
assert.deepStrictEqual(names, ['data.pdf', 'note.txt', 'report.md'], 'node_modules must be pruned; got ' + names);

const q = lf.scan({ root: tmp, query: 'report' });
assert.strictEqual(q.items.length, 1, 'query filter');

const ov = lf.overview([tmp, path.join(tmp, 'does-not-exist')]);
assert.strictEqual(ov.roots[0].exists, true);
assert.strictEqual(ov.roots[1].exists, false);
assert.ok(ov.roots[0].by_kind.document === 1 && ov.roots[0].by_kind.text === 2, 'kind counts');

const txt = lf.readText(path.join(tmp, 'note.txt'), [tmp]);
assert.ok(txt.previewable && txt.text.includes('contract'), 'text preview');

const pdf = lf.readText(path.join(tmp, 'data.pdf'), [tmp]);
assert.strictEqual(pdf.previewable, false, 'pdf not previewable in-process');

assert.throws(() => lf.readText(path.join(tmp, '..', 'secret.txt'), [tmp]), /outside allowed roots/);

const b64 = lf.readBase64(path.join(tmp, 'note.txt'), [tmp]);
assert.ok(Buffer.from(b64.base64, 'base64').toString() === 'hello world contract SLA', 'base64 read');

// Regression: path.resolve() keeps a drive root's own trailing separator (e.g. "C:\\", unlike any
// regular path, whose trailing separator gets stripped) — this used to break `within()` for every
// file nested under a drive root ("path outside allowed roots" for the whole disk). Reproduce with
// the real drive root of `tmp` rather than a fake path, since that's exactly the shape that broke.
const driveRoot = path.parse(tmp).root;
assert.ok(lf.within(path.join(tmp, 'note.txt'), [driveRoot]), 'within() must accept a drive-root grant for a nested file');
assert.ok(lf.within(path.join(tmp, 'sub', 'report.md'), [driveRoot]), 'within() must accept deeply nested files too');

// Content search: a query that doesn't match any filename should still find it inside a text file.
const byContent = lf.scan({ root: tmp, query: 'batch failure' });
assert.strictEqual(byContent.items.length, 1, 'content search should find report.md by its content');
assert.strictEqual(byContent.items[0].name, 'report.md');
assert.strictEqual(byContent.items[0].matched_in, 'content');

// ---- automation-local.js helpers (Automatisations local steps) ----
const al = require('./automation-local');
assert.strictEqual(al.sha256Hex('Write-Output "é"'), require('crypto').createHash('sha256').update(Buffer.from('Write-Output "é"', 'utf8')).digest('hex'));
const rx = al.patternToRegex('*.xlsx; rapport*.pdf');
assert.ok(rx.test('Stagiaires.XLSX') && rx.test('rapport-2026.pdf') && !rx.test('notes.txt'), 'glob patterns');
assert.strictEqual(al.patternToRegex(''), null);
assert.strictEqual(al.safeName('..\\..\\evil:name?.docx'), 'evil_name_.docx');
assert.strictEqual(al.safeName(''), 'fichier');
process.env.DM_TEST_DIR = tmp;
assert.strictEqual(al.expandPath('%DM_TEST_DIR%\\sub'), path.join(tmp, 'sub'));
const steps = al.createLocalSteps({ getRoots: () => [tmp], BrowserWindow: null, shell: { openExternal: async () => {}, openPath: async () => '' } });
(async () => {
  const saved = await steps.saveFile({ bytes: new Uint8Array(Buffer.from('abc')), folder: path.join(tmp, 'out'), name: 'a.txt' });
  const saved2 = await steps.saveFile({ bytes: new Uint8Array(Buffer.from('abc')), folder: path.join(tmp, 'out'), name: 'a.txt' });
  assert.strictEqual(path.basename(saved2.path), 'a (1).txt', 'no overwrite');
  const listed = await steps.listFiles({ folder: path.join(tmp, 'out'), pattern: '*.txt' });
  assert.strictEqual(listed.count, 2);
  const read = await steps.readFile({ path: saved.path });
  assert.strictEqual(Buffer.from(read.bytes).toString(), 'abc');
  await assert.rejects(steps.saveFile({ bytes: 'YWJj', folder: path.join(tmp, '..'), name: 'x.txt' }), /hors des dossiers autorisés/);
  await assert.rejects(steps.open({ target: path.join(tmp, 'x.exe') }), /refusée/);
  await assert.rejects(steps.powershell({ script: 'Write-Output 1', approvedHashes: ['00'] }), /non approuvé/);
  if (process.platform === 'win32') {
    const script = 'Write-Output "bonjour été"; exit 3';
    const res = await steps.powershell({ script, approvedHashes: [al.sha256Hex(script)], timeout_s: 60 });
    assert.strictEqual(res.stdout, 'bonjour été', 'utf-8 output');
    assert.strictEqual(res.exit_code, 3, 'exit code propagated');
  }
  // ---- recorder.js pure helpers ----
  const rec = require('./recorder');
  assert.ok(rec.isTypingKey('A') && rec.isTypingKey('Space') && !rec.isTypingKey('Enter') && !rec.isTypingKey('Ctrl'));
  const a = Buffer.alloc(16, 0); const b = Buffer.from(a); b[4] = 200; b[5] = 200;
  assert.strictEqual(rec.changedPixels(a, b), 1);
  assert.strictEqual(require('./background').drawTrayBitmap(32).length, 32 * 32 * 4);

  fs.rmSync(tmp, { recursive: true, force: true });
  console.log('local-files.js + automation helpers: ALL TESTS PASSED');
})().catch((err) => { console.error(err); process.exit(1); });

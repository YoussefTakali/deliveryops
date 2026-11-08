// Unit tests for dataset parsing (pasted table → rows → csv). Plain Node (type stripping):
//   cd frontend && npm run test:automations
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { parseTable, rowsToCsv } from './dataset.ts';

test('parseTable reads a comma table with a header row', () => {
  const rows = parseTable('Nom,Email,Montant\nDupont,dupont@acme.fr,1200\nMartin,martin@acme.fr,980');
  assert.deepEqual(rows, [
    { Nom: 'Dupont', Email: 'dupont@acme.fr', Montant: '1200' },
    { Nom: 'Martin', Email: 'martin@acme.fr', Montant: '980' },
  ]);
});

test('parseTable auto-detects tab and semicolon delimiters', () => {
  assert.equal(parseTable('a\tb\n1\t2')[0].b, '2');
  assert.equal(parseTable('a;b\n1;2')[0].b, '2');
});

test('parseTable honours quoted fields containing the delimiter', () => {
  const rows = parseTable('Nom,Adresse\nDupont,"12, rue de la Paix"\n"Martin, Jr","1 av. Foch"');
  assert.equal(rows[0].Adresse, '12, rue de la Paix');
  assert.equal(rows[1].Nom, 'Martin, Jr');
});

test('parseTable ignores blank lines and needs at least a header + one row', () => {
  assert.deepEqual(parseTable('Nom,Email'), []);       // header only
  assert.deepEqual(parseTable(''), []);
  assert.equal(parseTable('Nom,Email\n\nDupont,d@x.fr\n').length, 1);
});

test('rowsToCsv round-trips through parseTable', () => {
  const rows = [{ Nom: 'Dupont', Note: 'a,b' }, { Nom: 'Martin', Note: 'x' }];
  assert.deepEqual(parseTable(rowsToCsv(rows)), rows);
});

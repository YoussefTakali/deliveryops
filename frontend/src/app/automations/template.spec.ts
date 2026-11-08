// Unit tests for the automation template resolver. Plain Node (type stripping, Node >= 22.18):
//   cd frontend && npm run test:automations
// (The Angular workspace has no unit-test target configured; this keeps the resolver covered
// without adding a test framework dependency.)
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { evaluateWhen, isFalsy, normalizeKey, renderString, renderValue, resolvePath, stringify, usesLoopVars, type TemplateScope } from './template.ts';

const rows = [
  { 'Prénom': 'Léa', 'Nom de famille': 'Martin', 'E-mail': 'lea@example.com', 'Date début': '2026-09-21', 'N°. dossier': 'A-1' },
  { 'Prénom': 'Karim', 'Nom de famille': 'Benali', 'E-mail': 'karim@example.com', 'Date début': '2026-09-28', 'N°. dossier': 'A-2' },
];

function scope(extra: Partial<TemplateScope> = {}): TemplateScope {
  return {
    steps: {
      s1: { messages: [{ id: 'm1', subject: 'Stagiaires semaine 39' }], latest: { id: 'm1', subject: 'Stagiaires semaine 39' }, count: 1 },
      s3: { rows, columns: Object.keys(rows[0]), count: 2 },
      s4: [{ file: { file_id: 'f-lea', name: 'Convention Léa.docx' } }, { file: { file_id: 'f-karim', name: 'Convention Karim.docx' } }],
      s5: null,
    },
    inputs: { modele: { file_id: 'tpl-1', name: 'Convention.docx' }, signataire: 'Mme Durand', seuil: 3 },
    trigger: { message: { subject: 'Liste hebdo', attachments: [] } },
    now: '2026-09-21T09:00:00.000Z',
    today: '2026-09-21',
    run: { id: 'run-42' },
    ...extra,
  };
}

test('raw substitution: an exact expression keeps the object/list/number', () => {
  const s = scope();
  assert.deepEqual(renderString('{{steps.s3.rows}}', s), rows);
  assert.deepEqual(renderString('  {{ inputs.modele }} ', s), { file_id: 'tpl-1', name: 'Convention.docx' });
  assert.equal(renderString('{{inputs.seuil}}', s), 3);
  assert.equal(renderString('{{steps.s3.count}}', s), 2);
  assert.equal(renderString('{{steps.s5}}', s), null);
});

test('interpolation: values are stringified into the surrounding text', () => {
  const s = scope({ item: rows[0], index: 0 });
  assert.equal(renderString('Bonjour {{item.Prénom}} {{item.Nom de famille}} !', s), 'Bonjour Léa Martin !');
  assert.equal(renderString('{{steps.s3.count}} stagiaires', s), '2 stagiaires');
  assert.equal(renderString('Run {{run.id}} du {{today}}', s), 'Run run-42 du 2026-09-21');
  assert.equal(renderString('Objet : {{trigger.message.subject}}', s), 'Objet : Liste hebdo');
  assert.equal(renderString('Modèle : {{inputs.modele}}', s), 'Modèle : {"file_id":"tpl-1","name":"Convention.docx"}');
  assert.equal(renderString('Sans expression', s), 'Sans expression');
  assert.equal(stringify(['a', 'b', 3]), 'a, b, 3');
});

test('numeric indexes and nested paths', () => {
  const s = scope();
  assert.equal(renderString('{{steps.s1.messages.0.id}}', s), 'm1');
  assert.equal(renderString('{{steps.s3.rows.1.E-mail}}', s), 'karim@example.com');
  assert.equal(renderString('{{steps.s4.1.file.file_id}}', s), 'f-karim');
});

test('$index pairs the i-th output of an earlier foreach with the current item', () => {
  const s = scope({ item: rows[1], index: 1 });
  assert.equal(renderString('{{steps.s4.$index.file.file_id}}', s), 'f-karim');
  assert.deepEqual(renderValue({ to: '{{item.E-mail}}', attachments: ['{{steps.s4.$index.file.file_id}}'] }, s),
    { to: 'karim@example.com', attachments: ['f-karim'] });
  const missing: string[] = [];
  assert.equal(renderString('{{steps.s4.$index.file.file_id}}', scope(), missing), null, '$index outside a loop is unresolved');
  assert.deepEqual(missing, ['steps.s4.$index.file.file_id']);
  assert.equal(renderString('{{index}}', s), 1);
});

test('column keys: exact, then case/accent-insensitive, then punctuation-insensitive; dotted keys', () => {
  const s = scope({ item: rows[0], index: 0 });
  assert.equal(renderString('{{item.prenom}}', s), 'Léa');
  assert.equal(renderString('{{item.PRÉNOM}}', s), 'Léa');
  assert.equal(renderString('{{item.nom de famille}}', s), 'Martin');
  assert.equal(renderString('{{item.Nom_de_famille}}', s), 'Martin');
  assert.equal(renderString('{{item.email}}', s), 'lea@example.com');
  assert.equal(renderString('{{item.date debut}}', s), '2026-09-21');
  assert.equal(renderString('{{item.N°. dossier}}', s), 'A-1', 'key containing a dot');
  assert.equal(normalizeKey('  Élève   Prénom '), 'eleve prenom');
  // exact key wins over a normalized collision
  const s2 = scope({ item: { Nom: 'exact', nom: 'lower' } });
  assert.equal(renderString('{{item.nom}}', s2), 'lower');
  assert.equal(renderString('{{item.Nom}}', s2), 'exact');
});

test('missing paths: null for exact expressions, empty in text, reported', () => {
  const s = scope({ item: rows[0], index: 0 });
  const missing: string[] = [];
  assert.equal(renderString('{{steps.s9.file}}', s, missing), null);
  assert.equal(renderString('Bonjour {{item.Téléphone}}!', s, missing), 'Bonjour !');
  assert.equal(renderString('{{steps.s1.latest.subject.deeper}}', s, missing), null, 'cannot go deeper than a string');
  assert.equal(renderString('{{steps.s3.rows.9}}', s, missing), null, 'index out of range');
  assert.equal(renderString('{{steps.s5.file}}', s, missing), null, 'through null');
  assert.deepEqual(missing, ['steps.s9.file', 'item.Téléphone', 'steps.s1.latest.subject.deeper', 'steps.s3.rows.9', 'steps.s5.file']);
  assert.equal(resolvePath(s, '').found, false);
});

test('renderValue renders deeply without mutating the input', () => {
  const params = { data: '{{item}}', output_name: 'Convention - {{item.Nom de famille}}.docx', copies: 2, flags: [true, '{{index}}'] };
  const frozen = JSON.stringify(params);
  const out = renderValue(params, scope({ item: rows[0], index: 0 })) as any;
  assert.deepEqual(out.data, rows[0]);
  assert.equal(out.output_name, 'Convention - Martin.docx');
  assert.deepEqual(out.flags, [true, 0]);
  assert.equal(out.copies, 2);
  assert.equal(JSON.stringify(params), frozen);
});

test('when: falsy values skip the step', () => {
  for (const v of [null, undefined, false, 0, '', '  ', 'false', 'FALSE', '0', 'null', []]) assert.equal(isFalsy(v), true, `falsy: ${JSON.stringify(v)}`);
  for (const v of [true, 1, 'oui', 'true', [0], {}, ' x ']) assert.equal(isFalsy(v), false, `truthy: ${JSON.stringify(v)}`);
  const s = scope();
  assert.equal(evaluateWhen('{{steps.s1.latest}}', s), true);
  assert.equal(evaluateWhen('{{steps.s5}}', s), false, 'null output');
  assert.equal(evaluateWhen('{{steps.s9.latest}}', s), false, 'missing path');
  assert.equal(evaluateWhen('{{trigger.message.attachments}}', s), false, 'empty list');
  assert.equal(evaluateWhen('{{steps.s3.count}}', s), true);
  assert.equal(evaluateWhen('{{steps.s1.count}} ', scope({ steps: { s1: { count: 0 } } })), false, 'interpolated 0');
  assert.equal(evaluateWhen('false', s), false);
  assert.equal(evaluateWhen(undefined, s), true, 'no condition');
  assert.equal(evaluateWhen('', s), true, 'blank condition');
  assert.equal(evaluateWhen(false, s), false);
});

test('usesLoopVars detects item/index/$index references', () => {
  assert.equal(usesLoopVars('{{item.Nom}}'), true);
  assert.equal(usesLoopVars('{{ index }}'), true);
  assert.equal(usesLoopVars('{{steps.s4.$index.file}}'), true);
  assert.equal(usesLoopVars('{{steps.s1.latest}}'), false);
  assert.equal(usesLoopVars('{{steps.items}}'), false);
});

test('dataset row scope resolves {{row.<column>}} with accent/case-insensitive column matching', () => {
  const s = scope({ row: { 'Prénom': 'Léa', 'E-mail': 'lea@example.com', 'Montant': 1200 }, row_index: 0, row_total: 2 });
  assert.equal(renderString('{{row.Prénom}}', s), 'Léa');
  assert.equal(renderString('{{row.e-mail}}', s), 'lea@example.com');          // case-insensitive
  assert.equal(renderString('Bonjour {{row.prenom}}', s), 'Bonjour Léa');       // accent-insensitive, interpolated
  assert.equal(renderString('{{row.Montant}}', s), 1200);                       // exact-expression → raw value
  assert.equal(renderString('{{row_index}}', s), 0);
  assert.equal(renderString('{{row_total}}', s), 2);
  const missing: string[] = [];
  assert.equal(renderString('{{row.Inconnu}}', s, missing), null);              // unknown column (exact expr) → null + reported
  assert.deepEqual(missing, ['row.Inconnu']);
  assert.equal(renderString('X: {{row.Inconnu}}', s), 'X: ');                   // unknown column, interpolated → ''
});

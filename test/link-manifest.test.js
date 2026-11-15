import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  collectMarkdownLinks,
  missingMarkdownLinks,
  runLinkAudit,
  parseLinkManifest,
  serializeLinkManifest,
  createLinkManifest,
  buildLinkAuditScope,
  linkRecordKey,
  LinkManifestError,
  LINK_STATUS,
  SKIP_REASONS,
  MANIFEST_FORMAT,
  MANIFEST_VERSION,
  normalizePath
} from '../src/markdown-links.js';

const guide = [
  '# Guide',
  '[exists](./setup.md)',
  '[missing](.//gone.md#part)',
  '[remote](https://example.invalid/x)',
  '[mail](mailto:a@b.example)',
  '[anchor](#section)',
  '[root](/absolute/path)',
  '[query](?page=2)',
  '[encoded](./a%20b.md)',
  '[badpercent](./%zz.md)',
  '`[inline-code](not-checked.md)`',
  '\\[escaped](not-checked.md)',
  '```md',
  '[fenced](not-checked.md)',
  '```',
  '[ref][gone-label]',
  '[gone-label]: missing-ref.md'
].join('\n');

const files = {
  'docs/guide.md': guide,
  'docs/setup.md': '# Setup',
  'docs/a b.md': '# Spaces',
  'README.md': '[home](docs/setup.md)'
};
const inventory = ['docs/guide.md', 'docs/setup.md', 'docs/a b.md', 'README.md'];
const entries = Object.entries(files);

test('collectMarkdownLinks records source, line, raw target, normalized target and status', () => {
  const records = collectMarkdownLinks(entries, inventory);
  const byDestination = Object.fromEntries(records.map(record => [record.destination, record]));

  assert.equal(byDestination['./setup.md'].status, LINK_STATUS.EXISTS);
  assert.equal(byDestination['./setup.md'].target, 'docs/setup.md');
  assert.equal(byDestination['./setup.md'].source, 'docs/guide.md');
  assert.equal(byDestination['./setup.md'].line, 2);

  const gone = byDestination['.//gone.md#part'];
  assert.equal(gone.status, LINK_STATUS.MISSING);
  assert.equal(gone.target, 'docs/gone.md');
  assert.equal(gone.line, 3);

  assert.equal(byDestination['./a%20b.md'].target, 'docs/a b.md');
  assert.equal(byDestination['./a%20b.md'].status, LINK_STATUS.EXISTS);

  assert.deepEqual(
    missingMarkdownLinks(entries, inventory).map(link => link.target).sort(),
    ['docs/gone.md', 'docs/missing-ref.md']
  );
});

test('each historical skip rule is recorded with a distinct reason and never goes missing', () => {
  const records = collectMarkdownLinks(entries, inventory);
  const skipped = Object.fromEntries(records
    .filter(record => record.status === LINK_STATUS.SKIPPED)
    .map(record => [record.destination, record.reason]));

  assert.equal(skipped['https://example.invalid/x'], SKIP_REASONS.EXTERNAL_URL);
  assert.equal(skipped['mailto:a@b.example'], SKIP_REASONS.EXTERNAL_URL);
  assert.equal(skipped['#section'], SKIP_REASONS.ANCHOR);
  assert.equal(skipped['/absolute/path'], SKIP_REASONS.ROOT_PATH);
  assert.equal(skipped['?page=2'], SKIP_REASONS.QUERY);
  assert.equal(skipped['./%zz.md'], SKIP_REASONS.DECODE_FAILED);
  assert.equal(skipped['not-checked.md'], SKIP_REASONS.CODE_EXAMPLE);

  const codeLines = records
    .filter(record => record.reason === SKIP_REASONS.CODE_EXAMPLE)
    .map(record => record.line)
    .sort((a, b) => a - b);
  assert.deepEqual(codeLines, [11, 14]);
  assert.ok(!records.some(record => record.destination === 'not-checked.md' && record.status === LINK_STATUS.MISSING));
  // The escaped link on line 12 is not a candidate at all.
  assert.ok(!records.some(record => record.line === 12));
});

test('a full audit and an interrupted/resumed audit produce identical records', async () => {
  const full = await runLinkAudit(entries, inventory);
  assert.equal(full.resumed, false);
  assert.equal(full.stats.processed, full.records.length);
  assert.equal(full.stats.reused, 0);
  assert.equal(full.stats.missing, full.missing.length);

  // Simulate a crash after the first N settled records: serialize the partial
  // checklist, then resume exactly as the CLI would.
  for (const cut of [0, 1, 3, full.records.length - 1]) {
    const partial = createLinkManifest(full.records.slice(0, cut), full.scope, { generatedAt: '2026-01-01T00:00:00.000Z' });
    const text = serializeLinkManifest(partial);
    const reopened = parseLinkManifest(text);
    const resumed = await runLinkAudit(entries, inventory, { manifest: reopened, scope: full.scope });

    assert.equal(resumed.resumed, true);
    assert.equal(resumed.stats.reused, cut);
    assert.equal(resumed.stats.processed, full.records.length - cut);
    assert.deepEqual(resumed.records, full.records);
    assert.deepEqual(resumed.missing, full.missing);
    assert.deepEqual(resumed.manifest.records, full.manifest.records);
  }
});

test('onSettled only fires for records not already settled in the checklist', async () => {
  const full = await runLinkAudit(entries, inventory);
  const cut = 4;
  const partial = createLinkManifest(full.records.slice(0, cut), full.scope);
  const settled = [];
  const resumed = await runLinkAudit(entries, inventory, {
    manifest: partial,
    scope: full.scope,
    onSettled: record => settled.push(linkRecordKey(record))
  });
  assert.deepEqual(new Set(settled), new Set(full.records.slice(cut).map(linkRecordKey)));
  assert.equal(settled.length, full.records.length - cut);
  assert.equal(resumed.stats.total, full.records.length);
});

test('a scope change (different files or inventory) restarts the audit from scratch', async () => {
  const full = await runLinkAudit(entries, inventory);
  const changedInventory = [...inventory, 'docs/new.md'];
  const rerun = await runLinkAudit(entries, changedInventory, { manifest: full.manifest });
  assert.equal(rerun.restarted, true);
  assert.equal(rerun.resumed, false);
  assert.equal(rerun.stats.reused, 0);
  assert.equal(rerun.stats.processed, full.records.length);

  const otherScope = buildLinkAuditScope([...entries, ['docs/extra.md', '[x](nope.md)']], inventory);
  assert.notEqual(otherScope.hash, full.scope.hash);
  const again = await runLinkAudit(entries, inventory, { manifest: full.manifest, scope: otherScope });
  assert.equal(again.restarted, true);
});

test('editing Markdown contents flips the scope hash even when file lists are unchanged', () => {
  const before = buildLinkAuditScope(entries, inventory);
  const edited = Object.fromEntries(
    Object.entries(files).map(([path, content]) =>
      path === 'docs/guide.md' ? [path, `${content}\n[added](gone.md)`] : [path, content])
  );
  const after = buildLinkAuditScope(Object.entries(edited), inventory);
  assert.deepEqual(after.markdownFiles, before.markdownFiles);
  assert.notEqual(after.hash, before.hash);
  // The hash is deterministic for identical inputs.
  assert.equal(buildLinkAuditScope(entries, inventory).hash, before.hash);
});

test('checklist round trip keeps every record human-readable and stable', () => {
  const full = createLinkManifest(collectMarkdownLinks(entries, inventory), buildLinkAuditScope(entries, inventory));
  const text = serializeLinkManifest(full);
  const lines = text.split('\n');
  assert.equal(lines.at(-1), '');
  const header = JSON.parse(lines[0]);
  assert.equal(header.format, MANIFEST_FORMAT);
  assert.equal(header.version, MANIFEST_VERSION);
  assert.equal(typeof header.scope.hash, 'string');
  for (const line of lines.slice(1, -1)) {
    const record = JSON.parse(line);
    assert.ok(['source', 'line', 'destination', 'target', 'status'].every(key => key in record));
  }
  const reopened = parseLinkManifest(text);
  assert.equal(reopened.truncated, false);
  const { truncated, ...withoutFlag } = reopened;
  assert.deepEqual(withoutFlag, full);
});

const corruptCases = [
  ['empty file', ''],
  ['whitespace only', '   \n'],
  ['wrong header format', '{"format":"something-else","version":1}\n'],
  ['unsupported version', JSON.stringify({ format: MANIFEST_FORMAT, version: 99, scope: { hash: 'abcd' }, generatedAt: 'x' }) + '\n'],
  ['header missing scope', JSON.stringify({ format: MANIFEST_FORMAT, version: 1, generatedAt: 'x' }) + '\n'],
  ['record is not an object', JSON.stringify({ format: MANIFEST_FORMAT, version: 1, scope: { hash: 'abcd' }, generatedAt: 'x' }) + '\n[]\n'],
  ['record missing source', JSON.stringify({ format: MANIFEST_FORMAT, version: 1, scope: { hash: 'abcd' }, generatedAt: 'x' }) + '\n' + JSON.stringify({ line: 1, destination: 'a.md', target: 'a.md', status: 'exists' }) + '\n'],
  ['record bad line number', JSON.stringify({ format: MANIFEST_FORMAT, version: 1, scope: { hash: 'abcd' }, generatedAt: 'x' }) + '\n' + JSON.stringify({ source: 'README.md', line: 0, destination: 'a.md', target: 'a.md', status: 'exists' }) + '\n'],
  ['unknown status', JSON.stringify({ format: MANIFEST_FORMAT, version: 1, scope: { hash: 'abcd' }, generatedAt: 'x' }) + '\n' + JSON.stringify({ source: 'README.md', line: 1, destination: 'a.md', target: 'a.md', status: 'wat' }) + '\n'],
  ['skipped without reason', JSON.stringify({ format: MANIFEST_FORMAT, version: 1, scope: { hash: 'abcd' }, generatedAt: 'x' }) + '\n' + JSON.stringify({ source: 'README.md', line: 1, destination: 'a.md', target: null, status: 'skipped' }) + '\n'],
  ['duplicate identity', JSON.stringify({ format: MANIFEST_FORMAT, version: 1, scope: { hash: 'abcd' }, generatedAt: 'x' }) + '\n' + JSON.stringify({ source: 'README.md', line: 1, destination: 'a.md', target: 'a.md', status: 'exists' }) + '\n' + JSON.stringify({ source: 'README.md', line: 1, destination: 'a.md', target: 'a.md', status: 'exists' }) + '\n'],
  ['blank interior line', JSON.stringify({ format: MANIFEST_FORMAT, version: 1, scope: { hash: 'abcd' }, generatedAt: 'x' }) + '\n\n'],
  ['invalid JSON record', JSON.stringify({ format: MANIFEST_FORMAT, version: 1, scope: { hash: 'abcd' }, generatedAt: 'x' }) + '\n{not json}\n']
];

for (const [name, text] of corruptCases) {
  test(`corrupt checklist is rejected: ${name}`, () => {
    assert.throws(() => parseLinkManifest(text), error => error instanceof LinkManifestError && error.code === 'LINK_MANIFEST_CORRUPT');
  });
}

test('identity key distinguishes links by source, line and raw destination', () => {
  assert.equal(
    linkRecordKey({ source: 'a.md', line: 2, destination: 'x.md' }),
    linkRecordKey({ source: 'a.md', line: 2, destination: 'x.md', target: 'different' })
  );
  assert.notEqual(
    linkRecordKey({ source: 'a.md', line: 2, destination: 'x.md' }),
    linkRecordKey({ source: 'a.md', line: 3, destination: 'x.md' })
  );
});

test('target mismatch on resume forces the candidate to be settled again', async () => {
  const full = await runLinkAudit(entries, inventory);
  const tampered = structuredClone(full.manifest);
  // Same identity (source/line/destination) but a stale normalized target.
  tampered.records[0] = { ...tampered.records[0], target: 'stale/target.md' };
  const rerun = await runLinkAudit(entries, inventory, { manifest: tampered, scope: full.scope });
  assert.equal(rerun.stats.reused, full.records.length - 1);
  assert.equal(rerun.stats.processed, 1);
  assert.deepEqual(rerun.records, full.records);
});

test('normalizePath is shared with the analyze layer', () => {
  assert.equal(normalizePath('docs\\../README.md'), 'README.md');
});

test('a truncated final line (interrupted write) is dropped, not treated as corruption', async () => {
  const full = await runLinkAudit(entries, inventory);
  const headerLine = JSON.stringify({
    format: full.manifest.format,
    version: full.manifest.version,
    generatedAt: full.manifest.generatedAt,
    scope: full.manifest.scope
  });
  const completeLines = full.records.slice(0, 3).map(record => JSON.stringify(record));
  // Simulate a crash halfway through writing the fourth record: no newline.
  const partialText = [headerLine, ...completeLines, '{"source":"docs/guide.md","lin'].join('\n');
  const parsed = parseLinkManifest(partialText);
  assert.equal(parsed.truncated, true);
  assert.equal(parsed.records.length, 3);

  const resumed = await runLinkAudit(entries, inventory, { manifest: parsed, scope: full.scope });
  assert.equal(resumed.resumed, true);
  assert.equal(resumed.stats.reused, 3);
  assert.equal(resumed.stats.processed, full.records.length - 3);
  assert.deepEqual(resumed.records, full.records);
});

test('a complete file is not flagged as truncated', () => {
  const manifest = createLinkManifest([], buildLinkAuditScope(entries, inventory));
  assert.equal(parseLinkManifest(serializeLinkManifest(manifest)).truncated, false);
});

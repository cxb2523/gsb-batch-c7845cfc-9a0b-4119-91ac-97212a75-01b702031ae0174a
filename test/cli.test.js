import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, appendFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { parseLinkManifest } from '../src/markdown-links.js';
const cli = new URL('../bin/reporelief.js', import.meta.url);
const run = (...args) => execFileSync(process.execPath, [fileURLToPath(cli), ...args], {encoding:'utf8'});
// Returns combined stdout + stderr so tests can assert on checkpoint messages.
const runWithStderr = (...args) => {
  const result = spawnSync(process.execPath, [fileURLToPath(cli), ...args], {encoding:'utf8'});
  assert.equal(result.status, 0, result.stderr);
  return `${result.stdout}\n${result.stderr}`;
};
test('CLI inventories binary assets without reading them as source text', async () => {
 const root=await mkdtemp(join(tmpdir(),'reporelief-'));
 try {
  await mkdir(join(root,'docs'));
  await writeFile(join(root,'README.md'),'![asset](docs/image.png)\n[broken](docs/absent.md)');
  await writeFile(join(root,'docs/image.png'),Buffer.from([0,1,2]));
  const report=JSON.parse(run(root,'--json'));
  assert.equal(report.summary.files,1);
  assert.deepEqual(report.findings.filter(f=>f.id.startsWith('docs.link:')).map(f=>f.evidence[0]),['README.md:2: docs/absent.md -> docs/absent.md']);
 } finally {await rm(root,{recursive:true,force:true});}
});
test('showcase report is reproducible from its fixture', async () => {
 const root=fileURLToPath(new URL('../examples/neglected-repo',import.meta.url));
 const expected=await readFile(new URL('../examples/maintenance-plan.md',import.meta.url),'utf8');
 assert.equal(run(root).replaceAll('\r\n','\n'),expected.replaceAll('\r\n','\n'));
});

async function writeLinkFixture(root) {
  await mkdir(join(root,'docs'));
  await writeFile(join(root,'README.md'), [
    '# Project',
    '[guide](docs/guide.md)',
    '[remote](https://example.invalid/x)',
    '[anchor](#section)',
    '`[code](docs/code.md)`',
    '[broken](docs/absent.md)'
  ].join('\n'));
  await writeFile(join(root,'docs','guide.md'), '# Guide\n[home](../README.md)');
}

test('writes a human-readable link checklist and reports missing links only', async () => {
  const root = await mkdtemp(join(tmpdir(),'reporelief-manifest-'));
  const manifest = join(root, 'checklist.jsonl');
  try {
    await writeLinkFixture(root);
    const stdout = run(root, '--manifest', manifest);
    assert.match(stdout, /docs\/absent\.md -> docs\/absent\.md/);
    assert.doesNotMatch(stdout, /example\.invalid|#section|docs\/code\.md/);
    const parsed = parseLinkManifest(await readFile(manifest, 'utf8'));
    assert.equal(parsed.format, 'reporelief-link-manifest');
    assert.equal(parsed.records.length, 6);
    const byDestination = Object.fromEntries(parsed.records.map(record => [record.destination, record]));
    assert.equal(byDestination['docs/absent.md'].status, 'missing');
    assert.equal(byDestination['docs/guide.md'].status, 'exists');
    assert.equal(byDestination['https://example.invalid/x'].reason, 'external-url');
    assert.equal(byDestination['#section'].reason, 'anchor');
    assert.equal(byDestination['docs/code.md'].reason, 'code-example');
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('a second run reuses settled entries and a truncated run resumes the rest', async () => {
  const root = await mkdtemp(join(tmpdir(),'reporelief-resume-'));
  const manifest = join(root, 'checklist.jsonl');
  try {
    await writeLinkFixture(root);
    const first = runWithStderr(root, '--manifest', manifest);
    assert.match(first, /6 settled this run, 0 reused/);

    const second = runWithStderr(root, '--manifest', manifest);
    assert.match(second, /0 settled this run, 6 reused/);
    // Findings on stdout are unchanged regardless of checkpointing.
    assert.match(second, /docs\/absent\.md -> docs\/absent\.md/);

    // Simulate an interrupted run: header plus only the first two records.
    const lines = (await readFile(manifest, 'utf8')).split('\n').filter(Boolean);
    await writeFile(manifest, lines.slice(0, 3).join('\n') + '\n');
    const resumed = runWithStderr(root, '--manifest', manifest);
    assert.match(resumed, /4 settled this run, 2 reused/);

    const full = parseLinkManifest(await readFile(manifest, 'utf8'));
    assert.equal(full.records.length, 6);
    assert.deepEqual(full.records.map(record => record.status).sort(),
      ['exists', 'exists', 'missing', 'skipped', 'skipped', 'skipped'].sort());
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('a corrupt checklist triggers a clear message and a fresh scan', async () => {
  const root = await mkdtemp(join(tmpdir(),'reporelief-corrupt-'));
  const manifest = join(root, 'checklist.jsonl');
  try {
    await writeLinkFixture(root);
    await writeFile(manifest, '{ this is not valid json\n');
    const output = runWithStderr(root, '--manifest', manifest);
    assert.match(output, /corrupt/);
    assert.match(output, /fresh scan|from scratch/);
    assert.match(output, /6 settled this run, 0 reused/);
    // The corrupt file was replaced by a valid checklist.
    const parsed = parseLinkManifest(await readFile(manifest, 'utf8'));
    assert.equal(parsed.records.length, 6);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('checklist defaults inside the scan root and that directory is not audited', async () => {
  const root = await mkdtemp(join(tmpdir(),'reporelief-default-'));
  try {
    await writeLinkFixture(root);
    run(root, '--json');
    const defaultPath = join(root, '.reporelief', 'link-manifest.jsonl');
    const parsed = parseLinkManifest(await readFile(defaultPath, 'utf8'));
    assert.equal(parsed.records.length, 6);
    const report = JSON.parse(run(root, '--json'));
    assert.ok(!report.summary.files || !JSON.stringify(report).includes('.reporelief'));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('editing an existing Markdown file changes its content hash and restarts', async () => {
  const root = await mkdtemp(join(tmpdir(),'reporelief-addlink-'));
  const manifest = join(root, 'checklist.jsonl');
  try {
    await writeLinkFixture(root);
    runWithStderr(root, '--manifest', manifest);
    await appendFile(join(root, 'README.md'), '\n[new](docs/new.md)\n');
    const output = runWithStderr(root, '--manifest', manifest);
    assert.match(output, /scope changed/);
    assert.match(output, /7 settled this run, 0 reused/);
    const parsed = parseLinkManifest(await readFile(manifest, 'utf8'));
    assert.equal(parsed.records.length, 7);
    assert.ok(parsed.records.some(record => record.destination === 'docs/new.md' && record.status === 'missing'));
    // The final report still contains every missing link, old and new.
    assert.match(output, /docs\/absent\.md -> docs\/absent\.md/);
    assert.match(output, /docs\/new\.md -> docs\/new\.md/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('adding a Markdown file changes the scope, restarts and reports clearly', async () => {
  const root = await mkdtemp(join(tmpdir(),'reporelief-scope-'));
  const manifest = join(root, 'checklist.jsonl');
  try {
    await writeLinkFixture(root);
    runWithStderr(root, '--manifest', manifest);
    await writeFile(join(root, 'docs', 'extra.md'), '# Extra\n[also-gone](nope.md)\n');
    const output = runWithStderr(root, '--manifest', manifest);
    assert.match(output, /scope changed/);
    assert.match(output, /7 settled this run, 0 reused/);
    const parsed = parseLinkManifest(await readFile(manifest, 'utf8'));
    assert.equal(parsed.records.length, 7);
    assert.ok(parsed.records.some(record => record.source === 'docs/extra.md' && record.status === 'missing'));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('resumed output is identical to a single full run', async () => {
  const root = await mkdtemp(join(tmpdir(),'reporelief-equal-'));
  const oneShot = join(root, 'one.jsonl');
  const resumed = join(root, 'resumed.jsonl');
  try {
    await writeLinkFixture(root);
    runWithStderr(root, '--manifest', oneShot);
    // Build a partial checklist, then let a second run complete it.
    runWithStderr(root, '--manifest', resumed);
    const lines = (await readFile(resumed, 'utf8')).split('\n').filter(Boolean);
    await writeFile(resumed, lines.slice(0, 4).join('\n') + '\n');
    runWithStderr(root, '--manifest', resumed);
    const full = parseLinkManifest(await readFile(oneShot, 'utf8'));
    const afterResume = parseLinkManifest(await readFile(resumed, 'utf8'));
    assert.deepEqual(afterResume.records, full.records);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('a crash mid-write (no trailing newline) resumes without duplicating records', async () => {
  const root = await mkdtemp(join(tmpdir(),'reporelief-crash-'));
  const manifest = join(root, 'checklist.jsonl');
  try {
    await writeLinkFixture(root);
    runWithStderr(root, '--manifest', manifest);
    // Strip the final newline to emulate the process being killed mid-write.
    const text = await readFile(manifest, 'utf8');
    await writeFile(manifest, text.replace(/\n$/, ''));
    const output = runWithStderr(root, '--manifest', manifest);
    assert.doesNotMatch(output, /corrupt/);
    const parsed = parseLinkManifest(await readFile(manifest, 'utf8'));
    const identities = new Set(parsed.records.map(record => `${record.source}:${record.line}:${record.destination}`));
    assert.equal(identities.size, parsed.records.length, 'no duplicate records after resume');
    assert.equal(parsed.records.length, 6);
  } finally { await rm(root, { recursive: true, force: true }); }
});

#!/usr/bin/env node
import { readdir, readFile, realpath, mkdir, open } from 'node:fs/promises';
import { resolve, join, dirname, relative, sep } from 'node:path';
import { execFileSync } from 'node:child_process';
import { analyze, toMarkdown, normalizePath } from '../src/analyze.js';
import {
  runLinkAudit,
  parseLinkManifest,
  serializeLinkManifest,
  createLinkManifest,
  buildLinkAuditScope,
  LinkManifestError
} from '../src/markdown-links.js';
const args = process.argv.slice(2);
if (args.includes('--help')) {
  console.log('Usage: node bin/reporelief.js [directory] [--json] [--changed BASE] [--manifest PATH]\nReads text files only. Never runs project code. Outputs to stdout.\nThe resumable link checklist defaults to <directory>/.reporelief/link-manifest.jsonl.');
  process.exit(0);
}
const booleanFlags = new Set(['--json']);
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--changed') { if (!args[++i] || args[i].startsWith('-')) throw new Error('--changed requires a git revision'); }
  else if (args[i] === '--manifest') { if (!args[++i] || args[i].startsWith('-')) throw new Error('--manifest requires a file path'); }
  else if (args[i].startsWith('-') && !booleanFlags.has(args[i])) throw new Error(`Unknown option: ${args[i]}`);
}
const directory = args[0] && !args[0].startsWith('-') ? args[0] : '.';
const root = await realpath(resolve(directory));
const manifestIndex = args.indexOf('--manifest');
const manifestPath = manifestIndex < 0
  ? join(root, '.reporelief', 'link-manifest.jsonl')
  : resolve(args[manifestIndex + 1]);
const ignored = new Set(['.git', 'node_modules', 'vendor', 'dist', 'build', 'coverage', '.next', '.venv', '__pycache__', '.reporelief']);
// The checklist is tool state. A custom --manifest path inside the scan root
// must not enter the inventory, or every rerun would invalidate its own scope.
const manifestRelative = manifestPath === root || manifestPath.startsWith(root + sep)
  ? relative(root, manifestPath).replaceAll('\\', '/')
  : null;
const files = {};
const availablePaths = [];
let bytes = 0;
async function walk(relative = '') {
  for (const entry of await readdir(join(root, relative), { withFileTypes: true })) {
    const path = relative ? `${relative}/${entry.name}` : entry.name;
    if (entry.isSymbolicLink() || ignored.has(entry.name) || /^\.env(?:\.|$)/.test(entry.name)) continue;
    if (manifestRelative && path === manifestRelative) continue;
    availablePaths.push(path);
    if (availablePaths.length > 10000) throw new Error('Inventory limit exceeded (10,000 entries).');
    if (entry.isDirectory()) await walk(path);
    else if (entry.isFile()) {
      if (/\.(png|jpg|gif|zip|pdf|lock|woff2?)$/i.test(entry.name)) continue;
      const { stat } = await import('node:fs/promises');
      if ((await stat(join(root, path))).size > 512000) continue;
      const buffer = await readFile(join(root, path));
      if (buffer.includes(0)) continue;
      bytes += buffer.length;
      if (bytes > 20000000 || Object.keys(files).length >= 10000) throw new Error('Scan limit exceeded (20 MB / 10,000 files). Choose a smaller directory.');
      files[path] = buffer.toString('utf8');
    }
  }
}
try {
  await walk();
  const index = args.indexOf('--changed');
  const changed = index < 0 ? [] : execFileSync('git', ['diff', '--name-only', '-z', `${args[index + 1]}...HEAD`, '--'], { cwd: root, encoding: 'utf8', maxBuffer: 2000000 }).split('\0').filter(Boolean);

  const entries = Object.entries(files).map(([path, content]) => [normalizePath(path), content]);
  const inventory = [...new Set(availablePaths.map(normalizePath))];
  const scope = buildLinkAuditScope(entries, inventory);

  let manifest = null;
  let manifestText;
  try {
    manifestText = await readFile(manifestPath, 'utf8');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  if (manifestText !== undefined) {
    try {
      manifest = parseLinkManifest(manifestText);
    } catch (error) {
      if (error instanceof LinkManifestError) {
        console.error(`RepoRelief: link manifest at ${manifestPath} is corrupt (${error.message}); discarding it and starting a fresh scan.`);
      } else {
        throw error;
      }
    }
  }

  // Incremental writes keep the checklist usable even if the process is killed.
  const canResume = Boolean(manifest && manifest.scope?.hash === scope.hash);
  let handle;
  let buffer = [];
  let writeFailed = false;
  const flush = async () => {
    if (writeFailed || !buffer.length || !handle) return;
    const chunk = buffer.join('');
    buffer = [];
    try {
      await handle.write(chunk);
    } catch (error) {
      writeFailed = true;
      console.error(`RepoRelief: could not write link manifest (${error.message}); continuing without checkpointing.`);
    }
  };
  try {
    await mkdir(dirname(manifestPath), { recursive: true });
    // Always start from the validated records. A truncated final line was
    // already dropped by the parser, so rewriting it removes the partial text;
    // appending later cannot duplicate or corrupt a surviving record.
    handle = await open(manifestPath, 'w');
    const seedRecords = canResume ? manifest.records : [];
    await handle.write(serializeLinkManifest(createLinkManifest(seedRecords, scope)));
  } catch (error) {
    writeFailed = true;
    console.error(`RepoRelief: could not write link manifest (${error.message}); continuing without checkpointing.`);
  }

  const linkAudit = await runLinkAudit(entries, inventory, {
    manifest,
    scope,
    onSettled: async record => {
      if (writeFailed) return;
      buffer.push(JSON.stringify(record) + '\n');
      if (buffer.length >= 50) await flush();
    }
  });
  await flush();
  if (handle) await handle.close().catch(() => {});

  if (!writeFailed) {
    if (linkAudit.restarted) {
      console.error(`RepoRelief: link manifest scope changed (files or inventory differ); restarted the link scan from scratch.`);
    }
    const { total, exists, missing, skipped, processed, reused } = linkAudit.stats;
    console.error(`RepoRelief: link checklist ${manifestPath} — ${total} links (${exists} exist, ${missing} missing, ${skipped} skipped); ${processed} settled this run, ${reused} reused from checklist.`);
  }

  const report = analyze(files, changed, availablePaths);
  console.log(args.includes('--json') ? JSON.stringify(report, null, 2) : toMarkdown(report));
} catch (error) { console.error(`RepoRelief: ${error.message}`); process.exitCode = 1; }

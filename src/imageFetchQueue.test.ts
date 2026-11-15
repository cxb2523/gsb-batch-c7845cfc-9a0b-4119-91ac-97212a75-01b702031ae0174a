import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

import {
  decodeImage,
  DuplicateIdError,
  ImageFetchQueue,
  type Fetcher,
  type RunReport,
} from './imageFetchQueue.ts';

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

/** Minimal but structurally valid PNG header (8-byte signature + IHDR). */
function makePng(width: number, height: number): Uint8Array {
  const b = new Uint8Array(33);
  b.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  const dv = new DataView(b.buffer);
  dv.setUint32(8, 13); // IHDR chunk length
  b.set([0x49, 0x48, 0x44, 0x52], 12); // 'IHDR'
  dv.setUint32(16, width);
  dv.setUint32(20, height);
  b[24] = 8; // bit depth
  b[25] = 2; // color type: truecolor
  return b;
}

function assertCountsBalance(report: RunReport): void {
  const { total, success, failed, skipped, unprocessed } = report.summary;
  assert.equal(success + failed + skipped + unprocessed, total);
  assert.equal(report.results.length, total);
}

async function withTempDir(fn: (dir: string) => Promise<void>): Promise<void> {
  const dir = await mkdtemp(join(tmpdir(), 'image-smith-'));
  try {
    await fn(dir);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

async function withServer(
  handler: (url: string) => { status: number; body?: Uint8Array },
  fn: (base: string) => Promise<void>,
): Promise<void> {
  const server: Server = createServer((req, res) => {
    const { status, body } = handler(req.url ?? '/');
    res.writeHead(status, { 'content-type': 'application/octet-stream' });
    res.end(body ?? new Uint8Array(0));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  try {
    await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

// ---------------------------------------------------------------------------
// decoder sanity
// ---------------------------------------------------------------------------

test('decoder extracts format and dimensions', () => {
  const png = decodeImage(makePng(640, 480));
  assert.equal(png.format, 'png');
  assert.equal(png.width, 640);
  assert.equal(png.height, 480);
  assert.throws(() => decodeImage(new Uint8Array(0)), /empty content/);
  assert.throws(() => decodeImage(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8])), /unrecognized/);
});

// ---------------------------------------------------------------------------
// happy path: local files
// ---------------------------------------------------------------------------

test('fetches and decodes a batch of local files', async () => {
  await withTempDir(async (dir) => {
    await writeFile(join(dir, 'a.png'), makePng(100, 50));
    await writeFile(join(dir, 'b.png'), makePng(200, 80));

    const queue = new ImageFetchQueue();
    const report = await queue.run([
      { id: 'a', url: join(dir, 'a.png') },
      { id: 'b', url: join(dir, 'b.png') },
    ]);

    assert.deepEqual(report.summary, { total: 2, success: 2, failed: 0, skipped: 0, unprocessed: 0 });
    assertCountsBalance(report);
    assert.equal(report.images.size, 2);
    assert.equal(report.images.get('a')?.width, 100);
    assert.equal(report.images.get('b')?.height, 80);
  });
});

// ---------------------------------------------------------------------------
// per-item failures: 404, empty body, undecodable content
// ---------------------------------------------------------------------------

test('single failures are recorded with reasons and never abort the batch', async () => {
  const png = makePng(320, 240);
  await withServer(
    (url) => {
      if (url === '/ok1' || url === '/ok2') return { status: 200, body: png };
      if (url === '/missing') return { status: 404 };
      if (url === '/empty') return { status: 200, body: new Uint8Array(0) };
      return { status: 200, body: new TextEncoder().encode('this is not an image') };
    },
    async (base) => {
      const queue = new ImageFetchQueue();
      const report = await queue.run([
        { id: 'ok1', url: `${base}/ok1` },
        { id: 'missing', url: `${base}/missing` },
        { id: 'empty', url: `${base}/empty` },
        { id: 'garbage', url: `${base}/garbage` },
        { id: 'ok2', url: `${base}/ok2` },
      ]);

      assertCountsBalance(report);
      assert.equal(report.summary.success, 2);
      assert.equal(report.summary.failed, 3);

      const byId = new Map(report.results.map((r) => [r.id, r]));
      assert.equal(byId.get('ok1')?.status, 'success');
      assert.equal(byId.get('ok2')?.status, 'success');
      assert.match(byId.get('missing')?.error ?? '', /HTTP 404/);
      assert.match(byId.get('empty')?.error ?? '', /empty content/);
      assert.match(byId.get('garbage')?.error ?? '', /decode failed/);

      // successes survive and are deliverable
      assert.equal(report.images.size, 2);
      assert.equal(report.images.get('ok1')?.format, 'png');
      assert.equal(report.images.get('ok2')?.width, 320);
    },
  );
});

// ---------------------------------------------------------------------------
// duplicate handling
// ---------------------------------------------------------------------------

test('duplicate ids reject the whole batch before any fetch', async () => {
  let fetchCalls = 0;
  const fetcher: Fetcher = async () => {
    fetchCalls++;
    return makePng(1, 1);
  };
  const queue = new ImageFetchQueue({ fetcher });

  await assert.rejects(
    queue.run([
      { id: 'dup', url: 'mem://1' },
      { id: 'other', url: 'mem://2' },
      { id: 'dup', url: 'mem://3' },
    ]),
    (err: unknown) => err instanceof DuplicateIdError && err.duplicateIds.includes('dup'),
  );
  assert.equal(fetchCalls, 0);
});

test('duplicate addresses share a single output slot', async () => {
  let fetchCalls = 0;
  const fetcher: Fetcher = async () => {
    fetchCalls++;
    return makePng(10, 10);
  };
  const queue = new ImageFetchQueue({ fetcher });
  const report = await queue.run([
    { id: 'first', url: 'mem://same' },
    { id: 'second', url: 'mem://same' },
    { id: 'third', url: 'mem://SAME' },
  ]);

  assertCountsBalance(report);
  assert.equal(fetchCalls, 1);
  assert.equal(report.summary.success, 1);
  assert.equal(report.summary.skipped, 2);
  assert.equal(report.images.size, 1);
  assert.ok(report.images.has('first'));

  const second = report.results.find((r) => r.id === 'second');
  assert.equal(second?.skipReason, 'duplicate-url');
  assert.equal(second?.sourceId, 'first');
});

// ---------------------------------------------------------------------------
// abort + rerun
// ---------------------------------------------------------------------------

test('abort settles in-flight work and a rerun skips prior successes', async () => {
  const gate: Record<string, (bytes: Uint8Array) => void> = {};
  const fetchLog: string[] = [];

  // Entry 1 resolves immediately; entries 2 and 3 hang until released and
  // reject with AbortError if the signal fires first.
  const hangingFetcher: Fetcher = (url, signal) => {
    fetchLog.push(url);
    if (url.endsWith('/1')) return Promise.resolve(makePng(1, 1));
    return new Promise<Uint8Array>((resolve, reject) => {
      gate[url] = resolve;
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    });
  };
  let currentFetcher: Fetcher = hangingFetcher;
  const delegate: Fetcher = (url, signal) => currentFetcher(url, signal);

  const entries = [
    { id: 'one', url: 'mem://img/1' },
    { id: 'two', url: 'mem://img/2' },
    { id: 'three', url: 'mem://img/3' },
  ];

  const queue = new ImageFetchQueue({ fetcher: delegate });
  const firstRun = queue.run(entries);

  // Wait until entry 1 settled and entry 2 is in flight.
  while (!fetchLog.includes('mem://img/2')) {
    await new Promise((r) => setImmediate(r));
  }
  queue.abort();

  const report1 = await firstRun;
  assertCountsBalance(report1);
  assert.equal(report1.summary.success, 1);
  assert.equal(report1.summary.unprocessed, 2);
  assert.equal(report1.summary.failed, 0);
  const byId1 = new Map(report1.results.map((r) => [r.id, r]));
  assert.equal(byId1.get('one')?.status, 'success');
  assert.equal(byId1.get('two')?.status, 'unprocessed'); // in-flight, cancelled
  assert.equal(byId1.get('three')?.status, 'unprocessed'); // never started
  // the completed entry is still deliverable
  assert.equal(report1.images.size, 1);
  assert.ok(report1.images.has('one'));

  // Rerun the same list: entry 1 must be skipped, not re-fetched.
  const rerunFetches: string[] = [];
  currentFetcher = async (url) => {
    rerunFetches.push(url);
    return makePng(2, 2);
  };
  const report2 = await queue.run(entries);

  assertCountsBalance(report2);
  assert.deepEqual(rerunFetches.sort(), ['mem://img/2', 'mem://img/3']);
  assert.equal(report2.summary.skipped, 1);
  assert.equal(report2.summary.success, 2);
  const byId2 = new Map(report2.results.map((r) => [r.id, r]));
  assert.equal(byId2.get('one')?.skipReason, 'already-fetched');
  // everything is deliverable after the rerun
  assert.equal(report2.images.size, 3);
});

test('abort before anything starts leaves every entry unprocessed', async () => {
  // Fetcher honours the abort signal, so an in-flight entry is cancelled
  // rather than completing.
  const queue = new ImageFetchQueue({
    fetcher: (_url, signal) =>
      new Promise<Uint8Array>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }),
  });
  queue.abort(); // no run in progress: no-op
  const run = queue.run([
    { id: 'x', url: 'mem://x' },
    { id: 'y', url: 'mem://y' },
  ]);
  queue.abort();
  const report = await run;
  assertCountsBalance(report);
  assert.equal(report.summary.unprocessed, 2);
  assert.equal(report.images.size, 0);
});

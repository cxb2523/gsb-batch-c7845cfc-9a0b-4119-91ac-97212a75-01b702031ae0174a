/**
 * 本地测试：全部成功 / 部分失败 / 空清单（含非法地址、损坏数据）。
 *
 * 全部响应由本机临时 HTTP 服务构造，不依赖外网。
 * 运行：node --test
 */

import { test, before, after, describe } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';

import {
  decodeHtmlEntities,
  extractTitle,
  extractFirstLink,
  fetchPage,
  runCollection,
  loadManifest,
  parseCollectionResult,
} from '../lib/collect-core.mjs';

const SLOW_DELAY_MS = 400;

const server = createServer((req, res) => {
  const url = req.url ?? '/';
  if (url === '/ok') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(
      '<!doctype html><html><head><title>  示例页面 &amp; 标题  </title></head>' +
        '<body><h1>hello</h1><a href="/about">关于</a><a href="https://other.test/x">外链</a></body></html>',
    );
    return;
  }
  if (url === '/ok-no-link') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<html><head><title>无链接页面</title></head><body><p>nothing</p></body></html>');
    return;
  }
  if (url === '/no-title') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<html><head></head><body><a href="/x">x</a></body></html>');
    return;
  }
  if (url === '/empty-title') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<html><head><title>   </title></head><body></body></html>');
    return;
  }
  if (url === '/500') {
    res.writeHead(500, { 'Content-Type': 'text/plain' });
    res.end('Internal Server Error');
    return;
  }
  if (url === '/404') {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not Found');
    return;
  }
  if (url === '/slow') {
    // 不主动响应，模拟服务端挂起，用于触发客户端超时。
    return;
  }
  res.writeHead(404);
  res.end('unexpected route');
});

before(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
});

after(() => {
  server.closeAllConnections();
  server.close();
});

function localUrl(path) {
  const { port } = server.address();
  return `http://127.0.0.1:${port}${path}`;
}

describe('HTML 提取工具函数', () => {
  test('extractTitle 提取标题、压缩空白并解码实体', () => {
    assert.equal(extractTitle('<TITLE>Hello &amp; Bye</TITLE>'), 'Hello & Bye');
    assert.equal(extractTitle('<title>&#x4e2d;&#25991;</title>'), '中文');
    assert.equal(extractTitle('<html><head></head></html>'), null);
    assert.equal(extractTitle('<title>   </title>'), null);
  });

  test('decodeHtmlEntities 处理常见实体', () => {
    assert.equal(decodeHtmlEntities('a&nbsp;b&quot;c&apos;&lt;&gt;&amp;'), 'a b"c\'<>&');
  });

  test('extractFirstLink 取第一个带 href 的链接并解析为绝对地址', () => {
    const base = 'http://127.0.0.1:9/foo/bar';
    const html =
      '<a name="anchor">no href</a><a href="/about">a</a><a href="https://x.test/b">b</a>';
    assert.equal(extractFirstLink(html, base), 'http://127.0.0.1:9/about');
    assert.equal(
      extractFirstLink('<a href="https://x.test/b">b</a>', base),
      'https://x.test/b',
    );
    assert.equal(extractFirstLink('<a>empty</a><p>none</p>', base), null);
  });
});

describe('单条抓取 fetchPage 逐条结算', () => {
  test('成功：保留标题与首个链接', async () => {
    const item = await fetchPage(localUrl('/ok'));
    assert.equal(item.ok, true);
    if (item.ok) {
      assert.equal(item.title, '示例页面 & 标题');
      assert.equal(item.firstLink, localUrl('/about'));
    }
  });

  test('成功但无链接：firstLink 为 null', async () => {
    const item = await fetchPage(localUrl('/ok-no-link'));
    assert.equal(item.ok, true);
    if (item.ok) assert.equal(item.firstLink, null);
  });

  test('失败：HTTP 500 记录原因且不抛异常', async () => {
    const item = await fetchPage(localUrl('/500'));
    assert.equal(item.ok, false);
    if (!item.ok) assert.match(item.reason, /HTTP 状态码异常：500/);
  });

  test('失败：HTTP 404 记录原因', async () => {
    const item = await fetchPage(localUrl('/404'));
    assert.equal(item.ok, false);
    if (!item.ok) assert.match(item.reason, /404/);
  });

  test('失败：缺少标题（无 title 元素与空 title）', async () => {
    for (const path of ['/no-title', '/empty-title']) {
      const item = await fetchPage(localUrl(path));
      assert.equal(item.ok, false);
      if (!item.ok) assert.match(item.reason, /缺少标题/);
    }
  });

  test('失败：单条超时被封装为失败结果', async () => {
    const item = await fetchPage(localUrl('/slow'), { timeoutMs: 150 });
    assert.equal(item.ok, false);
    if (!item.ok) assert.match(item.reason, /请求超时/);
  });

  test('失败：连接拒绝（网络错误）不抛异常', async () => {
    const item = await fetchPage('http://127.0.0.1:1/unreachable', { timeoutMs: 500 });
    assert.equal(item.ok, false);
    if (!item.ok) assert.match(item.reason, /网络请求失败/);
  });
});

describe('整批采集 runCollection', () => {
  test('场景一：全部成功', async () => {
    const result = await runCollection([localUrl('/ok'), localUrl('/ok-no-link')], {
      timeoutMs: 2000,
    });
    assert.equal(result.total, 2);
    assert.equal(result.successCount, 2);
    assert.equal(result.failureCount, 0);
    assert.deepEqual(result.failures, []);
    assert.equal(result.results.every((r) => r.ok), true);
    assert.equal(result.results[0].ok && result.results[0].title, '示例页面 & 标题');
  });

  test('场景二：部分失败（超时 / 非 2xx / 缺标题混合），成功项照常保留', async () => {
    const result = await runCollection(
      [
        localUrl('/ok'),
        localUrl('/500'),
        localUrl('/no-title'),
        localUrl('/slow'),
        localUrl('/404'),
      ],
      { timeoutMs: SLOW_DELAY_MS / 2 },
    );
    assert.equal(result.total, 5);
    assert.equal(result.successCount, 1);
    assert.equal(result.failureCount, 4);
    assert.equal(result.results[0].ok, true);
    assert.equal(result.results.slice(1).every((r) => r.ok === false), true);
    const failedUrls = new Set(result.failures.map((f) => f.url));
    assert.equal(failedUrls.size, 4);
    for (const failure of result.failures) {
      assert.equal(typeof failure.reason, 'string');
      assert.ok(failure.reason.length > 0);
    }
    const slowFailure = result.failures.find((f) => f.url.endsWith('/slow'));
    assert.match(slowFailure.reason, /请求超时/);
  });

  test('可注入自定义 fetch：模拟全部失败时不中断', async () => {
    const fakeFetch = async () => ({ ok: false, status: 503, statusText: 'Busy' });
    const result = await runCollection(['http://a.test/', 'http://b.test/'], {
      fetchImpl: fakeFetch,
    });
    assert.equal(result.successCount, 0);
    assert.equal(result.failureCount, 2);
    assert.match(result.failures[0].reason, /503/);
  });
});

describe('清单校验 loadManifest', () => {
  test('接受数组形态与 {pages:[...]} 形态，非法 URL 被拒绝', () => {
    assert.deepEqual(loadManifest('["http://a.test/"]'), ['http://a.test/']);
    assert.deepEqual(
      loadManifest(JSON.stringify({ pages: ['https://a.test/x', '  https://b.test/ '] })),
      ['https://a.test/x', 'https://b.test/'],
    );
    assert.throws(() => loadManifest('["not-a-url"]'), /地址格式非法/);
    assert.throws(() => loadManifest('["ftp://a.test/"]'), /地址格式非法/);
    assert.throws(() => loadManifest('[""]'), /非空字符串/);
    assert.throws(() => loadManifest('[]'), /清单为空/);
    assert.throws(() => loadManifest(JSON.stringify({ pages: [] })), /清单为空/);
    assert.throws(() => loadManifest('{"foo":1}'), /清单格式非法/);
    assert.throws(() => loadManifest('{broken'), /JSON 解析失败/);
  });
});

describe('首页数据解析 parseCollectionResult（缺失/损坏不白屏）', () => {
  test('合法结果可解析并重新统计', () => {
    const result = parseCollectionResult(
      JSON.stringify({
        generatedAt: '2026-11-16T00:00:00.000Z',
        timeoutMs: 10000,
        results: [
          { url: 'http://a.test/', ok: true, title: 'A', firstLink: 'http://a.test/x' },
          { url: 'http://b.test/', ok: false, reason: 'HTTP 状态码异常：500' },
        ],
      }),
    );
    assert.equal(result.successCount, 1);
    assert.equal(result.failureCount, 1);
    assert.equal(result.failures[0].url, 'http://b.test/');
  });

  test('损坏 JSON 与结构非法均抛出可读错误', () => {
    assert.throws(() => parseCollectionResult('not json'), /不是合法 JSON/);
    assert.throws(() => parseCollectionResult('{}'), /缺少 results 数组/);
    assert.throws(
      () => parseCollectionResult(JSON.stringify({ results: [{ ok: true }] })),
      /缺少 url/,
    );
    assert.throws(
      () =>
        parseCollectionResult(
          JSON.stringify({ results: [{ url: 'http://a.test/', ok: true }] }),
        ),
      /成功记录缺少标题/,
    );
    assert.throws(
      () =>
        parseCollectionResult(
          JSON.stringify({ results: [{ url: 'http://a.test/', ok: false }] }),
        ),
      /失败记录缺少原因/,
    );
  });
});

describe('CLI 脚本端到端（本地 HTTP，无外网）', () => {
  let tempDir;
  const cliPath = join(process.cwd(), 'scripts', 'collect-pages.mjs');

  before(async () => {
    tempDir = await mkdtemp(join(tmpdir(), 'collect-test-'));
  });

  after(async () => {
    await rm(tempDir, { recursive: true, force: true });
  });

  /**
   * @param {string} manifestContent
   * @param {string} [outputName]
   * @returns {Promise<{code:number, stdout:string, stderr:string, outputPath:string}>}
   */
  function runCli(manifestContent, outputName = 'result.json') {
    const manifestPath = join(tempDir, `manifest-${outputName}`);
    const outputPath = join(tempDir, outputName);
    return writeFile(manifestPath, manifestContent, 'utf8').then(
      () =>
        new Promise((resolve) => {
          const child = spawn(
            process.execPath,
            [cliPath, manifestPath, outputPath, '250'],
            { cwd: process.cwd() },
          );
          let stdout = '';
          let stderr = '';
          child.stdout.on('data', (chunk) => (stdout += chunk));
          child.stderr.on('data', (chunk) => (stderr += chunk));
          child.on('close', (code) => resolve({ code, stdout, stderr, outputPath }));
        }),
    );
  }

  test('场景一：全部成功 — 退出码 0，输出统计与静态 JSON', async () => {
    const { code, stdout, outputPath } = await runCli(
      JSON.stringify({ pages: [localUrl('/ok'), localUrl('/ok-no-link')] }),
      'all-success.json',
    );
    assert.equal(code, 0);
    assert.match(stdout, /成功 2 条，失败 0 条/);
    const written = parseCollectionResult(await import('node:fs/promises').then((fs) => fs.readFile(outputPath, 'utf8')));
    assert.equal(written.successCount, 2);
    assert.equal(written.failureCount, 0);
  });

  test('场景二：部分失败 — 退出码 0，成功项保留且打印失败清单', async () => {
    const { code, stdout, outputPath } = await runCli(
      JSON.stringify({
        pages: [localUrl('/ok'), localUrl('/500'), localUrl('/no-title'), localUrl('/slow')],
      }),
      'partial.json',
    );
    assert.equal(code, 0);
    assert.match(stdout, /成功 1 条，失败 3 条/);
    assert.match(stdout, /\[失败清单\]/);
    assert.match(stdout, /HTTP 状态码异常：500/);
    const { readFile } = await import('node:fs/promises');
    const written = parseCollectionResult(await readFile(outputPath, 'utf8'));
    assert.equal(written.total, 4);
    assert.equal(written.successCount, 1);
    assert.equal(written.failureCount, 3);
    assert.equal(written.results[0].ok, true);
  });

  test('场景三：空清单 — 明确报错且非零退出，不产出数据文件', async () => {
    const { code, stderr, outputPath } = await runCli(
      JSON.stringify({ pages: [] }),
      'empty.json',
    );
    assert.notEqual(code, 0);
    assert.match(stderr, /清单为空/);
    await assert.rejects(() => import('node:fs/promises').then((fs) => fs.access(outputPath)));
  });

  test('非法地址 — 明确报错且非零退出', async () => {
    const { code, stderr } = await runCli(
      JSON.stringify({ pages: ['http://ok.test/', 'not-a-valid-url'] }),
      'invalid.json',
    );
    assert.notEqual(code, 0);
    assert.match(stderr, /地址格式非法/);
  });

  test('清单文件不存在 — 非零退出', async () => {
    const outputPath = join(tempDir, 'missing-output.json');
    const result = await new Promise((resolve) => {
      const child = spawn(
        process.execPath,
        [cliPath, join(tempDir, 'no-such-manifest.json'), outputPath, '250'],
        { cwd: process.cwd() },
      );
      let stderr = '';
      child.stderr.on('data', (chunk) => (stderr += chunk));
      child.on('close', (code) => resolve({ code, stderr }));
    });
    assert.notEqual(result.code, 0);
    assert.match(result.stderr, /无法读取清单文件/);
  });
});

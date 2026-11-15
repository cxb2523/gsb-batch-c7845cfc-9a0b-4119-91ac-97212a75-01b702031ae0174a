/*
 * Webtoon Vault 章节采集核心逻辑测试
 *
 * 本地运行（无需安装依赖，Node 18+）：
 *   node --test tests/collector.test.js
 *
 * 覆盖：正常路径、单页失败（部分完成）、整章失败跳过、
 *       连续失败退避与回落、重试上限、持久化与只重试失败章节。
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createCollector, STATUS } = require('../collector.js');

/* ---------- 测试工具 ---------- */

// 内存版 storage，模拟 localStorage
function memStorage() {
  const map = new Map();
  return {
    map,
    load: (k) => (map.has(k) ? map.get(k) : null),
    save: (k, v) => { map.set(k, v); }
  };
}

// 记录每次 delay 的毫秒数，不真实等待
function fakeDelay() {
  const calls = [];
  const fn = (ms) => { calls.push(ms); return Promise.resolve(); };
  fn.calls = calls;
  return fn;
}

// 构造一个假站点：chapters = [{ id, pages: [url...] }]
function makeSite(chapters, faults = {}) {
  const calls = { list: 0, pages: {}, pageFetch: {} };
  return {
    calls,
    fetchChapterList: async () => {
      calls.list++;
      if (faults.listFail) throw new Error('系列页无法打开');
      return chapters.map((c) => ({ id: c.id, title: c.id, url: 'http://x/' + c.id }));
    },
    fetchChapterPages: async (ch) => {
      calls.pages[ch.id] = (calls.pages[ch.id] || 0) + 1;
      if (faults.chapterFail && faults.chapterFail.has(ch.id)) {
        throw new Error('章节页无法打开: ' + ch.id);
      }
      const def = chapters.find((c) => c.id === ch.id);
      return def.pages.map((_, i) => `http://x/${ch.id}/p${i}.jpg`);
    },
    fetchPage: async (ch, pageUrl, idx) => {
      const key = ch.id + '#' + idx;
      calls.pageFetch[key] = (calls.pageFetch[key] || 0) + 1;
      if (faults.pageFail && faults.pageFail.has(key)) {
        throw new Error('页面抓取失败: ' + key);
      }
      return { ok: true };
    }
  };
}

const BASE_OPTS = { baseDelayMs: 100, backoffFactor: 2, maxDelayMs: 1000, maxRetries: 3 };

/* ---------- 正常路径 ---------- */

test('正常路径：全部章节按顺序完成，统计正确', async () => {
  const site = makeSite([
    { id: 'ch1', pages: ['a', 'b', 'c'] },
    { id: 'ch2', pages: ['a', 'b'] }
  ]);
  const delay = fakeDelay();
  const c = createCollector({ ...BASE_OPTS, delay, storage: memStorage(), ...site });

  const sum = await c.run('http://x/series');

  assert.equal(sum.total, 2);
  assert.equal(sum.completed, 2);
  assert.equal(sum.partial, 0);
  assert.equal(sum.failed, 0);
  assert.deepEqual(sum.chapters.map((c2) => c2.status), [STATUS.COMPLETED, STATUS.COMPLETED]);
  assert.equal(sum.chapters[0].okPages, 3);
  assert.equal(sum.chapters[1].okPages, 2);
  assert.equal(site.calls.pageFetch['ch1#0'], 1);
  assert.equal(site.calls.pageFetch['ch2#1'], 1);
});

/* ---------- 单页失败 ---------- */

test('单页失败：保留已成功页，章节标为部分完成并记录失败页序号', async () => {
  const site = makeSite(
    [{ id: 'ch1', pages: ['a', 'b', 'c', 'd'] }],
    { pageFail: new Set(['ch1#2']) } // 第 3 页（序号 2）始终失败
  );
  const c = createCollector({ ...BASE_OPTS, delay: fakeDelay(), storage: memStorage(), ...site });

  const sum = await c.run('http://x/series');

  assert.equal(sum.partial, 1);
  assert.equal(sum.completed, 0);
  const ch = sum.chapters[0];
  assert.equal(ch.status, STATUS.PARTIAL);
  assert.equal(ch.okPages, 3);            // 已成功的页没有丢
  assert.deepEqual(ch.failedPages, [2]);  // 失败页序号被记录
  assert.match(ch.error, /2/);
  // 失败页按 maxRetries 上限重试：1 次首发 + 3 次重试 = 4 次
  assert.equal(site.calls.pageFetch['ch1#2'], 1 + BASE_OPTS.maxRetries);
  // 失败页之后的页面仍然继续尝试
  assert.equal(site.calls.pageFetch['ch1#3'], 1);
});

/* ---------- 整章失败 ---------- */

test('整章失败：跳过该章继续下一章，并保留失败原因', async () => {
  const site = makeSite(
    [
      { id: 'ch1', pages: ['a'] },
      { id: 'ch2', pages: ['a', 'b'] },
      { id: 'ch3', pages: ['a'] }
    ],
    { chapterFail: new Set(['ch2']) }
  );
  const c = createCollector({ ...BASE_OPTS, delay: fakeDelay(), storage: memStorage(), ...site });

  const sum = await c.run('http://x/series');

  assert.equal(sum.completed, 2);
  assert.equal(sum.failed, 1);
  const failed = sum.chapters[1];
  assert.equal(failed.status, STATUS.FAILED);
  assert.match(failed.error, /章节页无法打开/); // 失败原因被保留
  // 后续章节仍被采集
  assert.equal(sum.chapters[2].status, STATUS.COMPLETED);
  assert.equal(site.calls.pageFetch['ch3#0'], 1);
});

/* ---------- 退避与回落 ---------- */

test('退避：连续失败逐步拉长间隔，成功后回落，重试有上限', async () => {
  // ch1 第 0 页一直失败；ch1 第 1 页成功 → 观察间隔回落
  const site = makeSite(
    [{ id: 'ch1', pages: ['a', 'b'] }],
    { pageFail: new Set(['ch1#0']) }
  );
  const delay = fakeDelay();
  const c = createCollector({ ...BASE_OPTS, delay, storage: memStorage(), ...site });

  const sum = await c.run('http://x/series');
  assert.equal(sum.partial, 1);

  // 间隔序列：每次请求前等待当前间隔。
  // 章节列表(100) → 章节页(100) → p0 尝试1(100) 失败后 200
  //   → p0 重试1(200) 失败后 400 → p0 重试2(400) 失败后 800
  //   → p0 重试3(800) 失败后 1000(封顶) → 达到上限放弃
  //   → p1(1000) 成功 → 回落 100
  const calls = delay.calls;
  assert.deepEqual(calls.slice(0, 3), [100, 100, 100]);
  assert.deepEqual(calls.slice(3, 6), [200, 400, 800]);
  assert.equal(calls[6], 1000); // 失败后 p1 用的是封顶后的间隔
  // p1 成功后回落
  const last = calls[calls.length - 1];
  assert.equal(last, 1000);
  // 验证内部状态确实回落
  assert.equal(c._getCurrentDelayMs(), 100);
  // 单调上升段（退避确实在拉长）
  for (let i = 3; i < 6; i++) assert.ok(calls[i + 1] > calls[i]);
});

test('重试上限：整章失败时章节页请求次数 = 1 + maxRetries', async () => {
  const site = makeSite(
    [{ id: 'ch1', pages: ['a'] }],
    { chapterFail: new Set(['ch1']) }
  );
  const c = createCollector({ ...BASE_OPTS, delay: fakeDelay(), storage: memStorage(), ...site });
  const sum = await c.run('http://x/series');
  assert.equal(sum.failed, 1);
  assert.equal(site.calls.pages['ch1'], 1 + BASE_OPTS.maxRetries);
});

/* ---------- 持久化与只重试失败章节 ---------- */

test('刷新后状态可恢复：三类状态可区分，重试只补失败页且不重复计数', async () => {
  const storage = memStorage();
  const chapters = [
    { id: 'ch1', pages: ['a', 'b'] },       // 将完全成功
    { id: 'ch2', pages: ['a', 'b', 'c'] },  // 第 1 页失败 → 部分完成
    { id: 'ch3', pages: ['a'] }             // 整章失败
  ];
  const faults = {
    pageFail: new Set(['ch2#1']),
    chapterFail: new Set(['ch3'])
  };
  let site = makeSite(chapters, faults);
  let c = createCollector({ ...BASE_OPTS, delay: fakeDelay(), storage, ...site });
  let sum = await c.run('http://x/series');
  assert.deepEqual(
    sum.chapters.map((x) => x.status),
    [STATUS.COMPLETED, STATUS.PARTIAL, STATUS.FAILED]
  );
  const ch2FirstOk = site.calls.pageFetch['ch2#0'];
  const ch1Fetches = site.calls.pageFetch['ch1#0'] + site.calls.pageFetch['ch1#1'];

  // 模拟刷新：同一 storage，新建 collector（故障已修复）
  site = makeSite(chapters, {});
  c = createCollector({ ...BASE_OPTS, delay: fakeDelay(), storage, ...site });
  sum = await c.run('http://x/series', { onlyFailed: true });

  // 三类状态全部恢复为已完成
  assert.equal(sum.completed, 3);
  // 已完成章节未被重新抓取
  assert.equal(site.calls.pageFetch['ch1#0'] || 0, 0);
  assert.equal(site.calls.pageFetch['ch1#1'] || 0, 0);
  // 部分完成章节只补失败页：ch2#0 未重抓，ch2#1 重抓一次
  assert.equal(site.calls.pageFetch['ch2#0'] || 0, 0);
  assert.equal(site.calls.pageFetch['ch2#1'], 1);
  // 整章失败的 ch3 被重试并成功
  assert.equal(site.calls.pageFetch['ch3#0'], 1);
  // 首轮计数不被重试影响（首轮 ch1 只抓过一次）
  assert.equal(ch1Fetches, 2);
  assert.equal(ch2FirstOk, 1);

  // 再次刷新后，持久化状态全部为已完成
  const saved = JSON.parse(storage.map.values().next().value);
  assert.ok(saved.chapters.every((x) => x.status === STATUS.COMPLETED));
});

test('持久化文件损坏时按无存档处理，不崩溃', async () => {
  const storage = memStorage();
  storage.save('webtoon-vault:collector:default', '{broken json');
  const site = makeSite([{ id: 'ch1', pages: ['a'] }]);
  const c = createCollector({ ...BASE_OPTS, delay: fakeDelay(), storage, ...site });
  const sum = await c.run('http://x/series');
  assert.equal(sum.completed, 1);
});

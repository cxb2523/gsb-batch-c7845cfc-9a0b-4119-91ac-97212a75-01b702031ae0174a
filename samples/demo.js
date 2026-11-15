/*
 * Webtoon Vault 章节采集样例（本地可运行，无需浏览器/网络）
 *
 * 运行：
 *   node samples/demo.js
 *
 * 用一个内存假站点演示：正常章节、单页失败（部分完成）、
 * 整章失败跳过、退避间隔变化，以及刷新后只重试失败章节。
 */
'use strict';

const { createCollector } = require('../collector.js');

// ---- 假站点：3 章；ch2 第 2 页首轮失败，ch3 章节页首轮打不开 ----
const chapters = [
  { id: 'ch1', pageCount: 3 },
  { id: 'ch2', pageCount: 4 },
  { id: 'ch3', pageCount: 2 }
];
let round = 1;

const site = {
  fetchChapterList: async () =>
    chapters.map((c) => ({ id: c.id, title: '第 ' + c.id.slice(2) + ' 章', url: 'mock://site/' + c.id })),
  fetchChapterPages: async (ch) => {
    if (round === 1 && ch.id === 'ch3') throw new Error('章节页无法打开（模拟 503）');
    const def = chapters.find((c) => c.id === ch.id);
    return Array.from({ length: def.pageCount }, (_, i) => `mock://site/${ch.id}/p${i}.jpg`);
  },
  fetchPage: async (ch, pageUrl, idx) => {
    if (round === 1 && ch.id === 'ch2' && idx === 1) throw new Error('图片 404（模拟）');
    return { ok: true };
  }
};

// 记录退避间隔，不真实等待
const delays = [];
const delay = (ms) => { delays.push(ms); return Promise.resolve(); };

// 内存 storage 模拟 localStorage（刷新 = 新建 collector、复用 storage）
const store = new Map();
const storage = {
  load: (k) => (store.has(k) ? store.get(k) : null),
  save: (k, v) => store.set(k, v)
};

function printSummary(title, sum) {
  console.log('\n== ' + title + ' ==');
  console.log(`共 ${sum.total} 章 | 已完成 ${sum.completed} | 部分完成 ${sum.partial} | 失败 ${sum.failed}`);
  for (const c of sum.chapters) {
    console.log(
      `  ${c.title}  [${c.status}]  成功 ${c.okPages} 页 / 失败 ${c.failedPages.length} 页` +
      (c.error ? `  — ${c.error}` : '')
    );
  }
}

(async () => {
  const opts = {
    ...site,
    delay,
    storage,
    storageKey: 'demo',
    baseDelayMs: 100,
    backoffFactor: 2,
    maxDelayMs: 1000,
    maxRetries: 3
  };

  // 第一轮：存在故障
  let collector = createCollector(opts);
  let sum = await collector.run('mock://site/series');
  printSummary('第一轮（含故障）', sum);
  console.log('请求间隔序列（退避可见）:', delays.join(', '));

  // 模拟刷新 + 故障修复，只重试失败章节
  round = 2;
  delays.length = 0;
  collector = createCollector(opts); // 同一 storage → 状态恢复
  sum = await collector.run('mock://site/series', { onlyFailed: true });
  printSummary('第二轮（刷新后仅重试失败章节）', sum);
})();

#!/usr/bin/env node
/**
 * 构建期页面采集脚本。
 *
 * 用法：
 *   node scripts/collect-pages.mjs [manifestPath] [outputPath] [timeoutMs]
 *
 * 默认：
 *   manifestPath = pages-manifest.json
 *   outputPath   = public/collection-result.json
 *   timeoutMs    = 10000
 *
 * 行为约定：
 * - 读取本地 JSON 清单中的远程页面地址，逐个抓取，提取页面标题与首个链接；
 * - 逐条结算：单条超时 / HTTP 非 2xx / 缺少标题只记录失败，不中断整批；
 * - 整批结束后向 stdout 输出成功数、失败数与失败清单，并写入静态 JSON；
 * - 清单为空或地址格式非法时打印明确错误并以非零状态退出，不静默产出空数据。
 */

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

import { loadManifest, runCollection } from '../lib/collect-core.mjs';

async function main() {
  const manifestPath = resolve(process.cwd(), process.argv[2] ?? 'pages-manifest.json');
  const outputPath = resolve(process.cwd(), process.argv[3] ?? 'public/collection-result.json');
  const timeoutArg = Number(process.argv[4]);
  const timeoutMs = Number.isFinite(timeoutArg) && timeoutArg > 0 ? timeoutArg : 10000;

  let manifestText;
  try {
    manifestText = await readFile(manifestPath, 'utf8');
  } catch (err) {
    const detail = err instanceof Error ? err.message : '未知错误';
    console.error(`[采集失败] 无法读取清单文件 ${manifestPath}：${detail}`);
    process.exit(1);
  }

  let urls;
  try {
    urls = loadManifest(manifestText);
  } catch (err) {
    // 清单为空 / JSON 损坏 / 地址非法：明确报错且非零退出，绝不产出空数据文件。
    console.error(`[采集失败] ${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  }

  console.log(`[采集开始] 共 ${urls.length} 个地址，单条超时 ${timeoutMs}ms`);
  const result = await runCollection(urls, { timeoutMs });

  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8');

  console.log(`[采集结束] 成功 ${result.successCount} 条，失败 ${result.failureCount} 条`);
  if (result.failures.length > 0) {
    console.log('[失败清单]');
    for (const failure of result.failures) {
      console.log(`  - ${failure.url}  →  ${failure.reason}`);
    }
  }
  console.log(`[输出文件] ${outputPath}`);
}

main().catch((err) => {
  // 兜底：任何未预期错误也以非零状态退出，避免构建静默成功。
  console.error(`[采集失败] ${err instanceof Error ? err.stack || err.message : String(err)}`);
  process.exit(1);
});

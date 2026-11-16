/**
 * 页面采集核心逻辑（无运行时依赖，浏览器与 Node 通用）。
 *
 * 设计目标：
 * - 抓取流程逐条结算，单条失败不影响整批；
 * - 失败原因均为可读中文，便于直接展示；
 * - 首页与采集脚本共用同一份结果解析/校验逻辑，避免行为漂移。
 */

/**
 * 解码常见 HTML 实体（命名实体 + 数字实体）。
 * @param {string} text
 * @returns {string}
 */
export function decodeHtmlEntities(text) {
  if (typeof text !== 'string') return '';
  return text
    .replace(/&#x([0-9a-f]+);?/gi, (_, hex) => {
      try {
        return String.fromCodePoint(parseInt(hex, 16));
      } catch {
        return '';
      }
    })
    .replace(/&#(\d+);?/g, (_, dec) => {
      try {
        return String.fromCodePoint(parseInt(dec, 10));
      } catch {
        return '';
      }
    })
    .replace(/&nbsp;/g, ' ')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

/**
 * 提取 <title>，找不到或为空时返回 null。
 * @param {string} html
 * @returns {string | null}
 */
export function extractTitle(html) {
  if (typeof html !== 'string') return null;
  const match = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html);
  if (!match) return null;
  const title = decodeHtmlEntities(match[1]).replace(/\s+/g, ' ').trim();
  return title || null;
}

/**
 * 提取页面中第一个 <a> 的 href，并基于页面地址解析为绝对 URL。
 * 没有任何带 href 的链接时返回 null。
 * @param {string} html
 * @param {string} baseUrl
 * @returns {string | null}
 */
export function extractFirstLink(html, baseUrl) {
  if (typeof html !== 'string') return null;
  const anchorRe = /<a\b[^>]*>/gi;
  const hrefRe = /\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i;
  let anchor;
  while ((anchor = anchorRe.exec(html)) !== null) {
    const hrefMatch = hrefRe.exec(anchor[0]);
    if (!hrefMatch) continue;
    const raw = decodeHtmlEntities(
      (hrefMatch[1] ?? hrefMatch[2] ?? hrefMatch[3] ?? '').trim(),
    );
    if (!raw) continue;
    try {
      return new URL(raw, baseUrl).toString();
    } catch {
      return raw;
    }
  }
  return null;
}

/**
 * 把抓取异常转成可读原因。
 * @param {unknown} err
 * @param {number} timeoutMs
 * @returns {string}
 */
function describeFetchError(err, timeoutMs) {
  const name = err && typeof err === 'object' ? /** @type {Error} */ (err).name : '';
  if (name === 'TimeoutError' || name === 'AbortError') {
    return `请求超时（超过 ${timeoutMs}ms）`;
  }
  const code =
    (err && typeof err === 'object' && /** @type {any} */ (err).cause?.code) ||
    (err && typeof err === 'object' && /** @type {any} */ (err).code);
  if (code) return `网络请求失败：${code}`;
  const message = err instanceof Error ? err.message : '未知错误';
  return `网络请求失败：${message}`;
}

/**
 * 抓取单个页面并结算。
 * 超时、HTTP 非 2xx、缺少标题一律返回 ok:false 并附带原因，不抛异常。
 * @param {string} url
 * @param {{ timeoutMs?: number, fetchImpl?: typeof fetch }} [options]
 * @returns {Promise<import('../types/collection').CollectionItem>}
 */
export async function fetchPage(url, { timeoutMs = 10000, fetchImpl = globalThis.fetch } = {}) {
  try {
    const res = await fetchImpl(url, {
      signal: AbortSignal.timeout(timeoutMs),
      redirect: 'follow',
    });
    if (!res.ok) {
      const statusText = res.statusText ? ` ${res.statusText}` : '';
      return { url, ok: false, reason: `HTTP 状态码异常：${res.status}${statusText}` };
    }
    const html = await res.text();
    const title = extractTitle(html);
    if (!title) {
      return { url, ok: false, reason: '返回内容缺少标题（未找到 <title>）' };
    }
    return { url, ok: true, title, firstLink: extractFirstLink(html, url) };
  } catch (err) {
    return { url, ok: false, reason: describeFetchError(err, timeoutMs) };
  }
}

/**
 * 顺序抓取整批地址，逐条结算，任何单条失败都不中断。
 * @param {string[]} urls
 * @param {{ timeoutMs?: number, fetchImpl?: typeof fetch, now?: () => Date }} [options]
 * @returns {Promise<import('../types/collection').CollectionResult>}
 */
export async function runCollection(
  urls,
  { timeoutMs = 10000, fetchImpl = globalThis.fetch, now = () => new Date() } = {},
) {
  /** @type {import('../types/collection').CollectionItem[]} */
  const results = [];
  for (const url of urls) {
    // 逐条 await：保证逐条结算，失败被封装在 fetchPage 内部，不会抛出中断整批。
    const item = await fetchPage(url, { timeoutMs, fetchImpl });
    results.push(item);
  }
  const failures = results
    .filter((item) => !item.ok)
    .map((item) => ({ url: item.url, reason: /** @type {any} */ (item).reason }));
  return {
    generatedAt: now().toISOString(),
    timeoutMs,
    total: results.length,
    successCount: results.length - failures.length,
    failureCount: failures.length,
    results,
    failures,
  };
}

/**
 * 读取并校验本地清单。
 * 支持两种形态：["https://..."] 或 { "pages": ["https://..."] }。
 * 清单为空、JSON 损坏、结构非法或地址格式非法时抛出带中文说明的 Error。
 * @param {string} text
 * @returns {string[]} 去空白后的绝对 URL 列表
 */
export function loadManifest(text) {
  /** @type {unknown} */
  let data;
  try {
    data = JSON.parse(text);
  } catch (err) {
    throw new Error(`清单 JSON 解析失败：${err instanceof Error ? err.message : '语法错误'}`);
  }
  const pages = Array.isArray(data)
    ? data
    : data && typeof data === 'object' && Array.isArray(/** @type {any} */ (data).pages)
      ? /** @type {any} */ (data).pages
      : null;
  if (!pages) {
    throw new Error('清单格式非法：需为 URL 数组，或包含 pages 数组的 JSON 对象');
  }
  if (pages.length === 0) {
    throw new Error('清单为空：至少需要配置一个页面地址');
  }
  /** @type {string[]} */
  const urls = [];
  pages.forEach((item, index) => {
    if (typeof item !== 'string' || !item.trim()) {
      throw new Error(`清单格式非法：第 ${index + 1} 条地址必须是非空字符串`);
    }
    const candidate = item.trim();
    let parsed;
    try {
      parsed = new URL(candidate);
    } catch {
      throw new Error(`地址格式非法：${candidate}`);
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      throw new Error(`地址格式非法（仅支持 http/https）：${candidate}`);
    }
    urls.push(parsed.toString());
  });
  return urls;
}

/**
 * 供首页使用的静态数据解析与严格校验。
 * 文件缺失由调用方根据响应状态处理；本函数负责 JSON 损坏/结构非法场景，
 * 全部抛出可读错误，避免页面白屏。
 * @param {string} text
 * @returns {import('../types/collection').CollectionResult}
 */
export function parseCollectionResult(text) {
  /** @type {unknown} */
  let data;
  try {
    data = JSON.parse(text);
  } catch (err) {
    throw new Error(`数据文件不是合法 JSON：${err instanceof Error ? err.message : '语法错误'}`);
  }
  if (!data || typeof data !== 'object' || !Array.isArray(/** @type {any} */ (data).results)) {
    throw new Error('数据文件格式损坏：缺少 results 数组');
  }
  const raw = /** @type {any} */ (data);
  /** @type {import('../types/collection').CollectionItem[]} */
  const results = raw.results.map((item, index) => {
    if (!item || typeof item !== 'object' || typeof item.url !== 'string') {
      throw new Error(`数据文件格式损坏：第 ${index + 1} 条记录缺少 url`);
    }
    if (item.ok === true) {
      if (typeof item.title !== 'string' || !item.title) {
        throw new Error(`数据文件格式损坏：${item.url} 的成功记录缺少标题`);
      }
      return {
        url: item.url,
        ok: true,
        title: item.title,
        firstLink: typeof item.firstLink === 'string' ? item.firstLink : null,
      };
    }
    if (item.ok === false) {
      if (typeof item.reason !== 'string' || !item.reason) {
        throw new Error(`数据文件格式损坏：${item.url} 的失败记录缺少原因`);
      }
      return { url: item.url, ok: false, reason: item.reason };
    }
    throw new Error(`数据文件格式损坏：${item.url} 缺少 ok 标记`);
  });
  const failures = results
    .filter((item) => !item.ok)
    .map((item) => ({ url: item.url, reason: /** @type {any} */ (item).reason }));
  return {
    generatedAt: typeof raw.generatedAt === 'string' ? raw.generatedAt : null,
    timeoutMs: typeof raw.timeoutMs === 'number' ? raw.timeoutMs : null,
    total: results.length,
    successCount: results.length - failures.length,
    failureCount: failures.length,
    results,
    failures,
  };
}

/**
 * 采集结果共享类型，供采集脚本与首页共同引用。
 */

export interface SuccessItem {
  url: string;
  ok: true;
  title: string;
  firstLink: string | null;
}

export interface FailureItem {
  url: string;
  ok: false;
  reason: string;
}

export type CollectionItem = SuccessItem | FailureItem;

export interface FailureEntry {
  url: string;
  reason: string;
}

export interface CollectionResult {
  /** ISO 时间戳；仅由构建期脚本保证存在，首页解析时容忍缺失 */
  generatedAt: string | null;
  /** 单条抓取超时（毫秒） */
  timeoutMs: number | null;
  total: number;
  successCount: number;
  failureCount: number;
  /** 逐条结算明细（成功保留标题与首个链接，失败保留原因） */
  results: CollectionItem[];
  /** 失败清单：地址 + 原因 */
  failures: FailureEntry[];
}

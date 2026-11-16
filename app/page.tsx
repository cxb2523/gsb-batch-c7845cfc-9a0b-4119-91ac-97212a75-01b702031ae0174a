'use client';

// .mjs 是纯 JS 共享逻辑，类型在 types/collection.d.ts 中声明。
// @ts-ignore
import { parseCollectionResult } from '../lib/collect-core.mjs';
import { useEffect, useState } from 'react';
import type { CollectionResult } from '../types/collection';
import styles from './page.module.css';

type ViewState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: CollectionResult };

/** 静态导出后数据与页面同源、同基准路径，用相对路径即可兼容子路径部署。 */
function resolveDataUrl(): string {
  return new URL('collection-result.json', document.baseURI).toString();
}

export default function Page() {
  const [state, setState] = useState<ViewState>({ status: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await fetch(resolveDataUrl(), {
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!res.ok) {
          setState({
            status: 'error',
            message: `采集数据文件不可用（HTTP ${res.status}）。请先运行 npm run collect 生成 public/collection-result.json。`,
          });
          return;
        }
        const text = await res.text();
        // 损坏/结构非法时 parseCollectionResult 会抛出可读错误。
        const data = parseCollectionResult(text);
        setState({ status: 'ready', data });
      } catch (err) {
        if ((err as Error)?.name === 'AbortError') return;
        setState({
          status: 'error',
          message: `采集数据读取失败：${err instanceof Error ? err.message : '未知错误'}`,
        });
      }
    })();
    return () => controller.abort();
  }, []);

  return (
    <main className={styles.main}>
      <header className={styles.header}>
        <h1>远程页面采集结果</h1>
        <p className={styles.subtitle}>
          构建期逐个抓取清单中的远程页面，提取页面标题与首个链接；单条失败不中断整批。
        </p>
      </header>

      {state.status === 'loading' && (
        <p className={styles.notice} role="status">
          正在加载本次采集数据…
        </p>
      )}

      {state.status === 'error' && (
        <section className={styles.errorBox} role="alert">
          <h2>数据暂时无法显示</h2>
          <p>{state.message}</p>
        </section>
      )}

      {state.status === 'ready' && <CollectionView data={state.data} />}
    </main>
  );
}

function CollectionView({ data }: { data: CollectionResult }) {
  return (
    <>
      <section className={styles.summary} aria-label="采集统计">
        <div className={styles.statCard}>
          <span className={styles.statValue}>{data.total}</span>
          <span className={styles.statLabel}>总地址数</span>
        </div>
        <div className={`${styles.statCard} ${styles.statSuccess}`}>
          <span className={styles.statValue}>{data.successCount}</span>
          <span className={styles.statLabel}>成功数</span>
        </div>
        <div className={`${styles.statCard} ${styles.statFailure}`}>
          <span className={styles.statValue}>{data.failureCount}</span>
          <span className={styles.statLabel}>失败数</span>
        </div>
        {data.generatedAt && (
          <p className={styles.generatedAt}>
            生成时间：{new Date(data.generatedAt).toLocaleString('zh-CN')}
          </p>
        )}
      </section>

      <section aria-label="成功采集明细">
        <h2 className={styles.sectionTitle}>采集明细</h2>
        <ul className={styles.itemList}>
          {data.results.map((item) =>
            item.ok ? (
              <li key={item.url} className={styles.item}>
                <div className={styles.itemHead}>
                  <span className={`${styles.badge} ${styles.badgeOk}`}>成功</span>
                  <a className={styles.itemUrl} href={item.url} target="_blank" rel="noreferrer">
                    {item.url}
                  </a>
                </div>
                <p className={styles.itemTitle}>标题：{item.title}</p>
                <p className={styles.itemLink}>
                  首个链接：
                  {item.firstLink ? (
                    <a href={item.firstLink} target="_blank" rel="noreferrer">
                      {item.firstLink}
                    </a>
                  ) : (
                    <span className={styles.muted}>页面中未找到链接</span>
                  )}
                </p>
              </li>
            ) : (
              <li key={item.url} className={`${styles.item} ${styles.itemFailed}`}>
                <div className={styles.itemHead}>
                  <span className={`${styles.badge} ${styles.badgeFail}`}>失败</span>
                  <a className={styles.itemUrl} href={item.url} target="_blank" rel="noreferrer">
                    {item.url}
                  </a>
                </div>
                <p className={styles.itemReason}>原因：{item.reason}</p>
              </li>
            ),
          )}
        </ul>
      </section>

      <section aria-label="失败清单">
        <h2 className={styles.sectionTitle}>失败清单</h2>
        {data.failures.length === 0 ? (
          <p className={styles.notice}>本次采集全部成功，没有失败项。</p>
        ) : (
          <ul className={styles.failureList}>
            {data.failures.map((failure) => (
              <li key={failure.url} className={styles.failureRow}>
                <code className={styles.failureUrl}>{failure.url}</code>
                <span className={styles.failureReason}>{failure.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

import { BBC_ORIGIN, config, SERVICE_PATH } from '../../config';
import { bySlug, byTopicId, CATEGORIES } from '../../data/categories';
import { indexArticles } from '../../lib/cache';
import { fetchTextLimited } from '../client';
import { extractPageProps } from '../nextData';
import { dedupeById, normalizeSummary, type NormalizedArticle } from './normalize';

export interface ResolvedTopic {
  slug: string;
  topicId: string;
  title: string | null;
}

/**
 * Accept an ASCII alias (`politics`), the Bengali nav title (`রাজনীতি`), or a
 * raw BBC topic id.
 *
 * Unlisted topic ids are allowed through deliberately: BBC has many more topics
 * than our table names, and a wrong id simply comes back as a 404 from upstream
 * which the route turns into `UNKNOWN_CATEGORY`.
 */
export function resolveTopic(slug?: string): ResolvedTopic | null {
  if (!slug) return null;

  const needle = String(slug).trim();

  const known =
    bySlug.get(needle.toLowerCase()) ??
    CATEGORIES.find((category) => category.title === needle) ??
    byTopicId.get(needle);

  if (known) return { slug: known.slug, topicId: known.topicId, title: known.title };

  if (/^[A-Za-z0-9]{8,}$/.test(needle)) {
    return { slug: needle, topicId: needle, title: null };
  }

  return null;
}

export function topicUrl(topicId: string, page = 1): string {
  const base = `${BBC_ORIGIN}${SERVICE_PATH}/topics/${topicId}`;
  return page > 1 ? `${base}?page=${page}` : base;
}

export interface TopicPage {
  slug: string;
  topicId: string;
  title: string | null;
  description: string | null;
  page: number | null;
  pageCount: number;
  url: string;
  articles: NormalizedArticle[];
}

/** One page of a topic listing. 24 articles per page upstream. */
export async function scrapeTopicPage(topic: ResolvedTopic, page = 1): Promise<TopicPage> {
  const url = topicUrl(topic.topicId, page);
  const pageProps = extractPageProps(await fetchTextLimited(url), url);
  const pageData = pageProps.pageData ?? {};
  const title = pageData.title ?? topic.title ?? null;

  const articles = (pageData.curations ?? [])
    .flatMap((curation) => curation.summaries ?? [])
    .map((summary) => normalizeSummary(summary, title))
    .filter((a): a is NormalizedArticle => a !== null);

  indexArticles(articles);

  return {
    slug: topic.slug,
    topicId: topic.topicId,
    title,
    description: pageData.description ?? null,
    page: pageData.activePage ?? page,
    pageCount: Math.min(pageData.pageCount ?? 1, config.maxTopicPages),
    url,
    articles,
  };
}

export interface TopicCrawl extends TopicPage {
  pagesCrawled: number;
  /**
   * Page numbers (2..n) that could not be fetched or parsed. Page one can never
   * appear here: a page-one failure aborts the whole crawl because its
   * `pageCount` is what bounds the remaining requests.
   */
  failedPages: number[];
}

/**
 * Crawl pages 1..n of a topic.
 *
 * Page one is fetched first because it reports the real `pageCount`, so asking
 * for more pages than exist costs nothing extra. A page-one failure still
 * aborts the request -- without it there is no `pageCount` to plan around.
 *
 * The remaining pages run in parallel but queue through the shared fetch
 * limiter, so a 40-page crawl is still only `MAX_CONCURRENCY` connections at a
 * time. Each page is requested exactly once. A failure on page 2..n (upstream
 * 5xx, timeout or a `__NEXT_DATA__` parse error) skips that page rather than
 * discarding the pages already fetched: it is recorded in `failedPages` and the
 * rest of the crawl proceeds, so `pagesCrawled` counts only pages whose
 * articles made it into the deduped result.
 */
export async function scrapeTopicPages(topic: ResolvedTopic, pages: number): Promise<TopicCrawl> {
  // Not wrapped: a failure here must propagate exactly as a single-page scrape.
  const first = await scrapeTopicPage(topic, 1);
  const total = Math.min(pages, first.pageCount, config.maxTopicPages);

  if (total <= 1) return { ...first, pagesCrawled: 1, failedPages: [] };

  const settled = await Promise.allSettled(
    Array.from({ length: total - 1 }, (_unused, index) => scrapeTopicPage(topic, index + 2)),
  );

  const succeeded: TopicPage[] = [];
  const failedPages: number[] = [];

  settled.forEach((outcome, index) => {
    const page = index + 2;
    if (outcome.status === 'fulfilled') {
      succeeded.push(outcome.value);
    } else {
      failedPages.push(page);
      console.warn(
        `[topic] skipping ${topic.topicId} page ${page} after failure:`,
        (outcome.reason as Error)?.message ?? outcome.reason,
      );
    }
  });

  const articles = dedupeById([first, ...succeeded].flatMap((result) => result.articles));
  indexArticles(articles);

  return {
    ...first,
    page: null,
    pagesCrawled: succeeded.length + 1,
    failedPages,
    articles,
  };
}

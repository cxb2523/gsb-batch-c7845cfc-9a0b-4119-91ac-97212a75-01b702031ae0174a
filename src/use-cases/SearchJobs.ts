import { IHttpClient } from '../interfaces/IHttpClient.js';
import { IHtmlParser } from '../interfaces/IHtmlParser.js';
import { ICache } from '../interfaces/ICache.js';
import { Job, QueryConfig, SearchResult, SearchResultMeta } from '../types/index.js';

export class SearchJobs {
  private httpClient: IHttpClient;
  private htmlParser: IHtmlParser;
  private cache: ICache<SearchResult>;

  constructor(httpClient: IHttpClient, htmlParser: IHtmlParser, cache: ICache<SearchResult>) {
    this.httpClient = httpClient;
    this.htmlParser = htmlParser;
    this.cache = cache;
  }

  async execute(queryConfig: QueryConfig): Promise<SearchResult> {
    const {
      urlBuilder,
      limit,
      cacheKey,
      startPage = 0,
      batchSize = 25,
      proxyUrl,
      maxRetries = 3,
      backoffBaseMs = 1000,
      pageDelayMs = 2000,
      pageDelayJitterMs = 1000,
    } = queryConfig;

    // 1. Check Cache (only complete results are ever stored)
    if (this.cache) {
      const cachedJobs = this.cache.get(cacheKey);
      if (cachedJobs) {
        return cachedJobs;
      }
    }

    const allJobs: Job[] = [];
    // Deduplicates jobs by URL so retries of the same page (and overlapping
    // pages) can never accumulate the same job twice.
    const seenUrls = new Set<string>();

    let start = startPage;
    let hasMore = true;
    let complete = true;
    let failedStart: number | undefined;
    let failureReason: string | undefined;

    const appendJobs = (jobs: Job[]): void => {
      for (const job of jobs) {
        // Jobs without a URL cannot be identity-checked; keep them as-is.
        if (job.url) {
          if (seenUrls.has(job.url)) {
            continue;
          }
          seenUrls.add(job.url);
        }
        allJobs.push(job);
      }
    };

    while (hasMore) {
      const url = urlBuilder(start);
      let pageError: unknown;
      let pageSucceeded = false;
      let pageJobs: Job[] = [];

      // Retry the SAME page up to maxRetries times. Jobs from this page are
      // only appended after a successful parse, so a failed/repeated attempt
      // can never partially accumulate into allJobs.
      for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
          const data = await this.httpClient.get(url, { proxyUrl });
          pageJobs = this.htmlParser.parseJobs(data);
          pageSucceeded = true;
          pageError = undefined;
          break;
        } catch (error: unknown) {
          pageError = error;
          const errorMessage = error instanceof Error ? error.message : 'Unknown error';
          console.error(
            `Error fetching batch at start=${start} (attempt ${attempt}):`,
            errorMessage,
          );

          if (attempt >= maxRetries) {
            break;
          }

          const isRateLimit =
            errorMessage.includes('Rate limit reached') || errorMessage.includes('429');
          if (isRateLimit) {
            // Exponential backoff on rate limiting / transient failures.
            const backoff = Math.pow(2, attempt - 1) * backoffBaseMs;
            console.log(`Rate limit detected. Backing off for ${backoff}ms...`);
            await this._sleep(backoff);
          } else {
            await this._sleep(backoffBaseMs);
          }
        }
      }

      if (!pageSucceeded) {
        // Retry budget exhausted for this page: return what was collected so
        // far and mark the result as incomplete.
        const errorMessage = pageError instanceof Error ? pageError.message : 'Unknown error';
        complete = false;
        failedStart = start;
        failureReason = `Page at start=${start} failed after ${maxRetries} attempt(s): ${errorMessage}`;
        console.error('Max retries reached for page. Returning partial results.');
        break;
      }

      if (!pageJobs || pageJobs.length === 0) {
        // Natural end of the result set.
        hasMore = false;
        break;
      }

      appendJobs(pageJobs);

      if (limit && allJobs.length >= limit) {
        allJobs.length = limit;
        hasMore = false;
        break;
      }

      start += batchSize;

      // Rate limiting / politeness delay between successful pages
      await this._sleep(pageDelayMs + Math.random() * pageDelayJitterMs);
    }

    const result = this._buildResult(allJobs, { complete, failedStart, failureReason });

    // 2. Set Cache — only fully complete results are cached; partial results
    //    must not poison subsequent queries.
    if (this.cache && complete && allJobs.length > 0) {
      this.cache.set(cacheKey, result);
    }

    return result;
  }

  private _buildResult(jobs: Job[], meta: SearchResultMeta): SearchResult {
    // Attach metadata as non-index enumerable properties so array semantics
    // (length, iteration, JSON serialization of entries) stay unchanged.
    Object.defineProperties(jobs, {
      complete: {
        value: meta.complete,
        enumerable: false,
        writable: true,
        configurable: true,
      },
      failedStart: {
        value: meta.failedStart,
        enumerable: false,
        writable: true,
        configurable: true,
      },
      failureReason: {
        value: meta.failureReason,
        enumerable: false,
        writable: true,
        configurable: true,
      },
    });
    return jobs as SearchResult;
  }

  private _sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

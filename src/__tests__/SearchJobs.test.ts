import { describe, it, expect } from 'vitest';
import { SearchJobs } from '../use-cases/SearchJobs.js';
import { IHttpClient } from '../interfaces/IHttpClient.js';
import { IHtmlParser } from '../interfaces/IHtmlParser.js';
import { ICache } from '../interfaces/ICache.js';
import { Job, QueryConfig, SearchResult } from '../types/index.js';

/* ------------------------------------------------------------------ */
/* Fakes                                                               */
/* ------------------------------------------------------------------ */

/** Route = the `start` offset encoded in the request URL. */
function startOf(url: string): number {
  const match = url.match(/[?&]start=(\d+)/);
  return match ? Number(match[1]) : -1;
}

/**
 * Fake HTTP client. Each route (start offset) gets a queue of responses:
 * a plain string resolves successfully, an Error rejects — modelling
 * transient failures such as HTTP 429.
 */
class FakeHttpClient implements IHttpClient {
  public routes = new Map<number, Array<string | Error>>();
  public calls: string[] = [];

  get(url: string): Promise<string> {
    this.calls.push(url);
    const start = startOf(url);
    const queue = this.routes.get(start);
    if (!queue || queue.length === 0) {
      // Unknown / exhausted route acts as the natural empty last page.
      return Promise.resolve('');
    }
    const next = queue.shift()!;
    if (next instanceof Error) {
      return Promise.reject(next);
    }
    return Promise.resolve(next);
  }
}

/** Fake parser: payloads are "p:<start>:u1,u2" style tokens, "" => no jobs. */
class FakeHtmlParser implements IHtmlParser {
  public parseCalls: string[] = [];

  parseJobs(htmlContent: string): Job[] {
    this.parseCalls.push(htmlContent);
    if (!htmlContent) {
      return [];
    }
    const [, , urlsToken] = htmlContent.split(':');
    return urlsToken.split(',').map((url) => makeJob(url));
  }
}

/** Recording cache so tests can assert which keys were written. */
class FakeCache implements ICache<SearchResult> {
  public store = new Map<string, SearchResult>();
  public setKeys: string[] = [];

  set(key: string, value: SearchResult): void {
    this.setKeys.push(key);
    this.store.set(key, value);
  }

  get(key: string): SearchResult | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  clear(): void {
    this.store.clear();
  }

  flush(): void {
    this.store.clear();
    this.setKeys = [];
  }

  size(): number {
    return this.store.size;
  }
}

function makeJob(url: string): Job {
  return {
    title: `Job ${url}`,
    company: 'Acme',
    location: '',
    postedDate: '',
    salary: 'Not specified',
    url,
    companyLogoUrl: '',
    postedTimeAgo: '',
  };
}

function page(start: number, urls: string[]): string {
  return `p:${start}:${urls.join(',')}`;
}

function makeConfig(overrides: Partial<QueryConfig> = {}): QueryConfig {
  return {
    urlBuilder: (start: number) => `https://example.test/jobs?start=${start}`,
    cacheKey: 'cache-key',
    startPage: 0,
    batchSize: 25,
    maxRetries: 3,
    // Zero-delay configuration keeps the tests fast and deterministic.
    backoffBaseMs: 0,
    pageDelayMs: 0,
    pageDelayJitterMs: 0,
    ...overrides,
  };
}

/* ------------------------------------------------------------------ */
/* Tests                                                               */
/* ------------------------------------------------------------------ */

describe('SearchJobs', () => {
  it('scenario A: second page fails once with 429 then succeeds — no duplicate jobs, complete result cached', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new FakeCache();
    const useCase = new SearchJobs(http, parser, cache);

    http.routes.set(0, [page(0, ['u0', 'u1'])]);
    http.routes.set(25, [new Error('Rate limit reached'), page(25, ['u2', 'u3'])]);
    // Third page empty => natural end of results.
    http.routes.set(50, ['']);

    const result = await useCase.execute(makeConfig());

    // Page 25 was requested twice (retry), but its jobs appear only once.
    expect(http.calls.map(startOf)).toEqual([0, 25, 25, 50]);
    expect(result.map((j) => j.url)).toEqual(['u0', 'u1', 'u2', 'u3']);
    expect(new Set(result.map((j) => j.url)).size).toBe(result.length);

    // Complete run: metadata flags and caching.
    expect(result.complete).toBe(true);
    expect(result.failedStart).toBeUndefined();
    expect(result.failureReason).toBeUndefined();
    expect(cache.size()).toBe(1);
    expect(cache.setKeys).toEqual(['cache-key']);

    // The value still behaves like a plain Job[] (metadata is non-enumerable).
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBe(4);
    expect(JSON.parse(JSON.stringify(result))).toHaveLength(4);
  });

  it('scenario B: third page keeps failing — partial results returned, marked incomplete and NOT cached', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new FakeCache();
    const useCase = new SearchJobs(http, parser, cache);

    http.routes.set(0, [page(0, ['u0', 'u1'])]);
    http.routes.set(25, [page(25, ['u2', 'u3'])]);
    http.routes.set(50, [
      new Error('Rate limit reached'),
      new Error('Rate limit reached'),
      new Error('Rate limit reached'),
    ]);

    const result = await useCase.execute(makeConfig());

    // The failing page was retried exactly maxRetries times.
    expect(http.calls.map(startOf)).toEqual([0, 25, 50, 50, 50]);

    // Jobs collected before the failure are preserved; nothing duplicated.
    expect(result.map((j) => j.url)).toEqual(['u0', 'u1', 'u2', 'u3']);
    expect(new Set(result.map((j) => j.url)).size).toBe(result.length);

    // Partial result metadata.
    expect(result.complete).toBe(false);
    expect(result.failedStart).toBe(50);
    expect(result.failureReason).toContain('start=50');
    expect(result.failureReason).toContain('Rate limit reached');

    // Partial results must never enter the cache.
    expect(cache.size()).toBe(0);
    expect(cache.setKeys).toEqual([]);

    // Still an array for backwards compatibility.
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBe(4);
  });

  it('retries of the same successful payload do not accumulate duplicate jobs', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new FakeCache();
    const useCase = new SearchJobs(http, parser, cache);

    // Page 25 fails once; on retry the server returns the same two URLs.
    http.routes.set(0, [page(0, ['u0', 'u1'])]);
    http.routes.set(25, [new Error('429'), page(25, ['u2', 'u3'])]);
    http.routes.set(50, ['']);

    const result = await useCase.execute(makeConfig());
    expect(result.map((j) => j.url)).toEqual(['u0', 'u1', 'u2', 'u3']);
  });

  it('deduplicates overlapping jobs across pages by URL', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new FakeCache();
    const useCase = new SearchJobs(http, parser, cache);

    http.routes.set(0, [page(0, ['u0', 'u1'])]);
    // Page 25 overlaps on u1 and adds u2.
    http.routes.set(25, [page(25, ['u1', 'u2'])]);
    http.routes.set(50, ['']);

    const result = await useCase.execute(makeConfig());
    expect(result.map((j) => j.url)).toEqual(['u0', 'u1', 'u2']);
    expect(result.complete).toBe(true);
  });

  it('a second identical query is served from the cache without HTTP calls', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new FakeCache();
    const useCase = new SearchJobs(http, parser, cache);

    http.routes.set(0, [page(0, ['u0', 'u1'])]);
    http.routes.set(25, ['']);

    const first = await useCase.execute(makeConfig());
    expect(first.complete).toBe(true);
    expect(cache.size()).toBe(1);

    const second = await useCase.execute(makeConfig());

    // No extra HTTP traffic: second run hit the cache.
    expect(http.calls.map(startOf)).toEqual([0, 25]);
    expect(second).toBe(cache.get('cache-key'));
    expect(second.map((j) => j.url)).toEqual(['u0', 'u1']);
    expect(second.complete).toBe(true);
  });

  it('different cache keys do not share one cached result', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new FakeCache();
    const useCase = new SearchJobs(http, parser, cache);

    http.routes.set(0, [page(0, ['u0'])]);
    http.routes.set(25, ['']);
    http.routes.set(50, [page(50, ['u2'])]);
    http.routes.set(75, ['']);

    const fromStart0 = await useCase.execute(makeConfig({ cacheKey: 'key-start-0', startPage: 0 }));
    const fromStart50 = await useCase.execute(
      makeConfig({ cacheKey: 'key-start-50', startPage: 50 }),
    );

    expect(fromStart0.map((j) => j.url)).toEqual(['u0']);
    expect(fromStart50.map((j) => j.url)).toEqual(['u2']);

    // Distinct cache entries; neither query served the other's result.
    expect(cache.size()).toBe(2);
    expect(cache.store.has('key-start-0')).toBe(true);
    expect(cache.store.has('key-start-50')).toBe(true);

    // Re-running start=50 must hit its own cache entry and skip HTTP.
    const callsBefore = http.calls.length;
    const cached = await useCase.execute(makeConfig({ cacheKey: 'key-start-50', startPage: 50 }));
    expect(http.calls.length).toBe(callsBefore);
    expect(cached.map((j) => j.url)).toEqual(['u2']);
  });

  it('an empty first page is a complete (empty) result and is not cached', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new FakeCache();
    const useCase = new SearchJobs(http, parser, cache);

    http.routes.set(0, ['']);

    const result = await useCase.execute(makeConfig());

    expect(result).toEqual([]);
    expect(result.complete).toBe(true);
    expect(result.failedStart).toBeUndefined();
    expect(result.failureReason).toBeUndefined();
    expect(cache.size()).toBe(0);
  });

  it('failure on the very first page returns an empty, incomplete array with failure metadata', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new FakeCache();
    const useCase = new SearchJobs(http, parser, cache);

    http.routes.set(0, [new Error('boom'), new Error('boom'), new Error('boom')]);

    const result = await useCase.execute(makeConfig());

    expect(Array.isArray(result)).toBe(true);
    expect(result).toEqual([]);
    expect(result.complete).toBe(false);
    expect(result.failedStart).toBe(0);
    expect(result.failureReason).toContain('boom');
    expect(http.calls).toHaveLength(3);
    expect(cache.size()).toBe(0);
  });

  it('respects the limit and truncates after deduplication, caching the complete result', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new FakeCache();
    const useCase = new SearchJobs(http, parser, cache);

    http.routes.set(0, [page(0, ['u0', 'u1', 'u2'])]);
    http.routes.set(25, [page(25, ['u3', 'u4'])]);

    const result = await useCase.execute(makeConfig({ limit: 3 }));

    expect(result.map((j) => j.url)).toEqual(['u0', 'u1', 'u2']);
    expect(result.complete).toBe(true);
    expect(cache.size()).toBe(1);
  });
});

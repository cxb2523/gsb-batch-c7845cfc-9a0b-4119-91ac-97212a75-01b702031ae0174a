import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { LinkedInClient } from '../interface-adapters/LinkedInClient.js';
import { InMemoryCache } from '../frameworks/InMemoryCache.js';
import { IHttpClient } from '../interfaces/IHttpClient.js';
import { IHtmlParser } from '../interfaces/IHtmlParser.js';
import { Job, SearchResult } from '../types/index.js';

/* ------------------------------------------------------------------ */
/* Fakes                                                               */
/* ------------------------------------------------------------------ */

function startOf(url: string): number {
  const match = url.match(/[?&]start=(\d+)/);
  return match ? Number(match[1]) : -1;
}

/** Serves a token per request; unknown offsets return empty pages. */
class FakeHttpClient implements IHttpClient {
  public calls: string[] = [];

  get(url: string): Promise<string> {
    this.calls.push(url);
    return Promise.resolve(`p:${startOf(url)}`);
  }
}

class FakeHtmlParser implements IHtmlParser {
  parseJobs(htmlContent: string): Job[] {
    const start = Number(htmlContent.replace('p:', ''));
    if (Number.isNaN(start) || start >= 50) {
      return [];
    }
    return [
      {
        title: `Job ${start}`,
        company: 'Acme',
        location: '',
        postedDate: '',
        salary: 'Not specified',
        url: `https://example.test/job/${start}`,
        companyLogoUrl: '',
        postedTimeAgo: '',
      },
    ];
  }
}

/**
 * Drives the internally-awaited setTimeout calls of the use case while fake
 * timers are active, until the given search promise settles.
 */
async function settle<T>(promise: Promise<T>): Promise<T> {
  for (let i = 0; i < 200; i++) {
    await vi.advanceTimersByTimeAsync(5000);
    if (await Promise.race([promise.then(() => true), Promise.resolve(false)])) {
      break;
    }
  }
  return promise;
}

describe('LinkedInClient cache key', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('includes the start page and limit, so different pages never share a cache entry', async () => {
    const http = new FakeHttpClient();
    const parser = new FakeHtmlParser();
    const cache = new InMemoryCache<SearchResult>();
    const client = new LinkedInClient(http, parser, cache);

    const page0 = await settle(client.search({ keyword: 'engineer', limit: 25, page: 0 }));
    const page1 = await settle(client.search({ keyword: 'engineer', limit: 25, page: 1 }));

    // page 0 walks starts 0 -> 25 -> 50(empty); page 1 starts at 25 -> 50(empty)
    expect(http.calls.map(startOf)).toEqual([0, 25, 50, 25, 50]);
    expect(page0.map((j) => j.url)).toEqual([
      'https://example.test/job/0',
      'https://example.test/job/25',
    ]);
    expect(page1.map((j) => j.url)).toEqual(['https://example.test/job/25']);

    // Two distinct entries keyed by their start offsets.
    expect(cache.size()).toBe(2);

    // Re-running page 0 hits its own cache entry: no new HTTP requests.
    const callsBefore = http.calls.length;
    const cachedPage0 = await settle(client.search({ keyword: 'engineer', limit: 25, page: 0 }));
    expect(http.calls.length).toBe(callsBefore);
    expect(cachedPage0).toBe(page0);
    expect(cachedPage0.complete).toBe(true);

    // A different limit also produces a separate cache entry.
    await settle(client.search({ keyword: 'engineer', limit: 50, page: 0 }));
    expect(cache.size()).toBe(3);
  });
});

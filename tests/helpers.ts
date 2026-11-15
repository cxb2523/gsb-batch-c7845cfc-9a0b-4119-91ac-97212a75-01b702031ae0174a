import { AxiosError, type AxiosResponse } from 'axios';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { vi } from 'vitest';

const FIXTURES = path.join(__dirname, 'fixtures');

export const fixture = (name: string): string => readFileSync(path.join(FIXTURES, name), 'utf8');

export const ARTICLE_ID = fixture('article-id.txt').trim();

/** Map of upstream URL pattern to fixture file. */
const ROUTES: [RegExp, string][] = [
  [/\/bengali\/popular\/read$/, 'popular.html'],
  // Topic pages beyond the first arrive as `?page=n`; every page serves the
  // same recorded response unless a test overrides it.
  [/\/bengali\/topics\/c8y94k95v52t(?:\?page=\d+)?$/, 'category-technology.html'],
  [/\/bengali\/topics\/cdr56gv542vt(?:\?page=\d+)?$/, 'category-india.html'],
  [/\/bengali\/articles\//, 'article.html'],
  [/\/bengali$/, 'home.html'],
];

/** Topic listing URLs, captured so a test can target an individual page. */
const TOPIC_RE = /\/bengali\/topics\/([A-Za-z0-9]+)(?:\?page=(\d+))?$/;

export type UpstreamFailureKind = 'http' | 'timeout' | 'parse';

export interface UpstreamFailure {
  topicId: string;
  /** 1-based page numbers that should fail (page one is a legitimate choice). */
  pages: number[];
  /** How the failure presents. `http` -> 5xx, `timeout` -> ECONNABORTED, `parse` -> 200 without __NEXT_DATA__. */
  kind?: UpstreamFailureKind;
  /** Response status for an `http` failure. Defaults to 503. */
  status?: number;
}

export interface StubUpstreamOptions {
  failures?: UpstreamFailure[];
  /** Serve a different fixture for one topic page, so pages have distinct article sets. */
  pageFixtures?: { topicId: string; page: number; fixture: string }[];
}

function failedResponse(failure: UpstreamFailure, url: string): { status: number; data: string } {
  const kind: UpstreamFailureKind = failure.kind ?? 'http';

  if (kind === 'parse') {
    // An HTTP-200 whose body no longer carries the data blob, mirroring BBC
    // changing their page shape; extractPageProps turns this into a 502.
    return { status: 200, data: '<html><body>the page shape has changed</body></html>' };
  }

  if (kind === 'timeout') {
    throw new AxiosError(`timeout of 10000ms exceeded for ${url}`, 'ECONNABORTED');
  }

  const status = failure.status ?? 503;
  throw new AxiosError(
    `Request failed with status code ${status}`,
    AxiosError.ERR_BAD_RESPONSE,
    undefined,
    undefined,
    {
      status,
      statusText: 'Service Unavailable',
      headers: {},
      config: {},
      data: '',
    } as AxiosResponse,
  );
}

/**
 * Replace the shared axios client's `get` with a fixture lookup, so tests never
 * touch the network. The returned spy lets callers assert how many upstream
 * fetches actually happened, which is what the cache test checks.
 *
 * Pass `failures` to make chosen topic pages fail in realistic ways (upstream
 * 5xx, a timeout, or a 200 whose `__NEXT_DATA__` blob is gone), and
 * `pageFixtures` to give individual pages their own recorded response.
 */
export function stubUpstream(options: StubUpstreamOptions = {}) {
  return vi.fn(async (url: string) => {
    const topicMatch = TOPIC_RE.exec(url);
    if (topicMatch) {
      const [, topicId, rawPage] = topicMatch;
      const page = rawPage ? Number.parseInt(rawPage, 10) : 1;

      const failure = options.failures?.find(
        (candidate) => candidate.topicId === topicId && candidate.pages.includes(page),
      );
      if (failure) return failedResponse(failure, url);

      const override = options.pageFixtures?.find(
        (candidate) => candidate.topicId === topicId && candidate.page === page,
      );
      if (override) return { status: 200, data: fixture(override.fixture) };
    }

    const match = ROUTES.find(([pattern]) => pattern.test(url));

    if (!match) {
      const error: Error & { response?: { status: number } } = new Error(`404 for ${url}`);
      error.response = { status: 404 };
      throw error;
    }

    return { status: 200, data: fixture(match[1]) };
  });
}

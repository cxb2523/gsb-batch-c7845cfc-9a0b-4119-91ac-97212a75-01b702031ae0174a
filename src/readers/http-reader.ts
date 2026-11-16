import { DataReader, DataRecord } from '@/core/interfaces';
import { SchemaValidator } from '@/transformers/schema-validating-reader';
import { BaseReader } from '@/core/base-reader';

/**
 * Decision returned by {@link HttpReaderOptions.onPageError}.
 * - `'continue'`: skip the failed page and move on to the next one.
 * - `'stop'`: end the collection; records yielded so far remain consumable.
 */
export type PageErrorDecision = 'continue' | 'stop';

export interface HttpReaderOptions<T = any> {
  method?: string;
  headers?: Record<string, string>;
  body?: any;
  /**
   * Function to extract the array of records from the API response.
   * Defaults to returning the response itself.
   */
  resultsPath?: (response: any) => any[];
  /**
   * Function to calculate the next URL for pagination. 
   * Receives the full response body and the current URL.
   * Return null to stop pagination.
   */
  nextPageUrl?: (response: any, currentUrl: string) => string | null;
  /**
   * Optional schema for validation and type inference.
   */
  schema?: SchemaValidator<T>;
  /**
   * Milliseconds to wait between page requests. The first request is issued
   * immediately. Defaults to `0`.
   */
  delayMs?: number;
  /**
   * Maximum number of retries for a page that responds with `429` or `503`.
   * Before each retry the reader waits for the delay advertised by the
   * `Retry-After` header when present, otherwise it backs off exponentially.
   * Defaults to `0` (no retries).
   */
  maxRetries?: number;
  /**
   * Invoked when a page cannot be collected: a non-2xx response, a body from
   * which no array can be extracted, a network failure, or an exhausted retry
   * budget. Return `'continue'` to skip the page and move on, or `'stop'` to
   * end the collection. When omitted, the page error is thrown and terminates
   * `read()`.
   */
  onPageError?: (url: string, pageNumber: number, error: Error) => PageErrorDecision | Promise<PageErrorDecision>;
  /**
   * Invoked after a page has been parsed successfully, right before its
   * records are yielded.
   *
   * @param pageNumber 1-based index of the page in the collection.
   * @param pageRecordCount Number of records parsed from this page.
   * @param totalRecordCount Number of records parsed so far, across all pages.
   */
  onPage?: (pageNumber: number, pageRecordCount: number, totalRecordCount: number) => void | Promise<void>;
}

/**
 * Base delay for exponential backoff when a retryable response carries no
 * `Retry-After` header. Matches the default `initialDelayMs` used by
 * `RetryingWriter`.
 */
const RETRY_BASE_DELAY_MS = 1000;

/**
 * Carries the response body that could not produce a usable page so pagination
 * can still be advanced when the consumer chooses to continue.
 */
class PageFailure extends Error {
  public readonly data?: any;

  constructor(message: string, data?: any) {
    super(message);
    this.name = 'PageFailure';
    this.data = data;
  }
}

function sleep(ms: number): Promise<void> {
  if (!Number.isFinite(ms) || ms <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * HttpReader class for streaming data from REST APIs.
 * Supports automated pagination and custom authentication headers.
 *
 * When `onPageError` is configured the reader no longer aborts on a bad page:
 * the callback decides whether to skip it or stop. Successful pages are
 * reported through `onPage`, which makes progress observable.
 */
export class HttpReader<T = DataRecord> extends BaseReader<T> {
  private url: string;
  private options: HttpReaderOptions<T>;
  private schema?: SchemaValidator<T>;

  constructor(url: string, options: HttpReaderOptions<T> = {}) {
    super();
    this.url = url;
    this.options = {
      method: 'GET',
      resultsPath: (res) => (Array.isArray(res) ? res : res.data || res.items || []),
      delayMs: 0,
      maxRetries: 0,
      ...options,
    };
    this.schema = options.schema;
  }

  public async *read(): AsyncIterableIterator<T> {
    let currentUrl: string | null = this.url;
    let pageNumber = 0;
    let totalRecordCount = 0;
    let isFirstRequest = true;

    while (currentUrl) {
      pageNumber++;

      if (!isFirstRequest) {
        await sleep(this.options.delayMs ?? 0);
      }
      isFirstRequest = false;

      let data: any;
      let records: any[] | undefined;
      let pageFailure: Error | undefined;

      try {
        const page = await this.fetchPage(currentUrl);
        data = page.data;

        const extracted = this.options.resultsPath!(data);
        if (!Array.isArray(extracted)) {
          throw new PageFailure('HttpReader: Extracted records are not in an array format. Check resultsPath configuration.', data);
        }
        records = extracted;
      } catch (error) {
        pageFailure = error instanceof Error ? error : new Error(String(error));
        if (error instanceof PageFailure && error.data !== undefined) {
          // Keep a parsable error body around so `nextPageUrl` can still
          // locate the following page when the consumer continues.
          data = error.data;
        }
      }

      if (pageFailure) {
        const onPageError = this.options.onPageError;
        if (!onPageError) {
          throw pageFailure;
        }

        const decision = await onPageError(currentUrl, pageNumber, pageFailure);
        if (decision !== 'continue') {
          return;
        }

        currentUrl = this.resolveNextUrlAfterFailure(data, currentUrl);
        continue;
      }

      const pageRecords = records!;
      totalRecordCount += pageRecords.length;

      if (this.options.onPage) {
        // Deliberately not wrapped: a throwing callback must terminate the
        // collection instead of being swallowed.
        await this.options.onPage(pageNumber, pageRecords.length, totalRecordCount);
      }

      for (const record of pageRecords) {
        if (this.schema) {
          yield this.schema.parse(record);
        } else {
          yield record as T;
        }
      }

      // Pagination logic
      if (this.options.nextPageUrl) {
        currentUrl = this.options.nextPageUrl(data, currentUrl);
      } else {
        currentUrl = null;
      }
    }
  }

  /**
   * Advances pagination past a skipped page using the same `nextPageUrl`
   * contract as successful pages, so `continue` behaves like a page that
   * produced no records.
   *
   * `data` is `undefined` when the failure carried no parsable payload (network
   * error or a plain-text error body); there is nothing to derive the next page
   * from then, so the collection ends gracefully and the records produced so
   * far stay available to the consumer instead of erroring out or looping.
   * Errors thrown by `nextPageUrl` are not swallowed and terminate the
   * collection, matching its behaviour on successful pages.
   */
  private resolveNextUrlAfterFailure(data: any, currentUrl: string): string | null {
    if (data === undefined || !this.options.nextPageUrl) {
      return null;
    }
    return this.options.nextPageUrl(data, currentUrl);
  }

  /**
   * Fetches a single page, retrying `429`/`503` responses up to `maxRetries`
   * times. Errors are thrown as `PageFailure` instances carrying the parsed
   * response body when one is available.
   */
  private async fetchPage(url: string): Promise<{ data: any }> {
    const maxRetries = Math.max(0, Math.floor(this.options.maxRetries ?? 0));
    let attempt = 0;

    while (true) {
      const response = await fetch(url, {
        method: this.options.method,
        headers: {
          'Content-Type': 'application/json',
          ...this.options.headers
        },
        body: this.options.body ? JSON.stringify(this.options.body) : undefined
      });

      if (response.ok) {
        try {
          return { data: await response.json() };
        } catch (error) {
          const message = `HttpReader: Failed to parse JSON response from ${url}: ${error instanceof Error ? error.message : String(error)}`;
          throw new PageFailure(message);
        }
      }

      const errorBody = await this.readErrorBody(response);
      const message = `HttpReader: ${this.options.method} ${url} failed with ${response.status}. Body: ${errorBody}`;

      let parsedBody: any;
      let hasParsedBody = false;
      try {
        parsedBody = JSON.parse(errorBody);
        hasParsedBody = true;
      } catch {
        // The body is not JSON; pagination cannot be advanced from it.
      }

      const isRetryable = response.status === 429 || response.status === 503;
      if (isRetryable && attempt < maxRetries) {
        attempt++;
        const retryAfterMs = this.getRetryAfterMs(response);
        const delay = retryAfterMs !== undefined ? retryAfterMs : RETRY_BASE_DELAY_MS * Math.pow(2, attempt - 1);
        await sleep(delay);
        continue;
      }

      throw new PageFailure(message, hasParsedBody ? parsedBody : undefined);
    }
  }

  /**
   * Best-effort extraction of an error response body for diagnostics and for
   * pagination when the consumer chooses to continue past the failed page.
   */
  private async readErrorBody(response: Response): Promise<string> {
    if (typeof (response as any)?.text !== 'function') {
      return 'No error body';
    }
    return (response as any).text().catch(() => 'No error body');
  }

  /**
   * Reads the `Retry-After` header in milliseconds. Supports both the
   * delay-seconds and HTTP-date forms.
   */
  private getRetryAfterMs(response: Response): number | undefined {
    const raw = this.getHeader(response, 'retry-after');
    if (raw === undefined) {
      return undefined;
    }

    const value = raw.trim();
    if (!value) {
      return undefined;
    }

    if (/^\d+$/.test(value)) {
      return parseInt(value, 10) * 1000;
    }

    const retryAt = Date.parse(value);
    if (!Number.isNaN(retryAt)) {
      return Math.max(0, retryAt - Date.now());
    }

    return undefined;
  }

  /**
   * Header access that tolerates both real `Headers` instances and the plain
   * objects commonly used in tests.
   */
  private getHeader(response: Response, name: string): string | undefined {
    const headers: any = (response as any)?.headers;
    if (!headers) {
      return undefined;
    }

    if (typeof headers.get === 'function') {
      const value = headers.get(name);
      return value === null || value === undefined ? undefined : String(value);
    }

    const key = Object.keys(headers).find((header) => header.toLowerCase() === name.toLowerCase());
    return key === undefined ? undefined : String(headers[key]);
  }
}

export interface Job {
  title: string;
  company: string;
  location: string;
  postedDate: string;
  salary: string;
  url: string;
  companyLogoUrl: string;
  postedTimeAgo: string;
}

export interface QueryOptions {
  host?: string;
  keyword?: string;
  location?: string;
  dateSincePosted?: string;
  jobType?: string;
  remoteFilter?: string;
  salary?: string;
  experienceLevel?: string;
  sortBy?: string;
  limit?: number;
  page?: number;
  has_verification?: boolean;
  under_10_applicants?: boolean;
  proxyUrl?: string;
}

export interface QueryConfig {
  urlBuilder: (start: number) => string;
  limit?: number;
  cacheKey: string;
  startPage?: number;
  batchSize?: number;
  proxyUrl?: string;
  /** Maximum number of retries for a single failing page (default 3). */
  maxRetries?: number;
  /** Base delay in ms before retrying a page; exponential backoff is built on top of it. */
  backoffBaseMs?: number;
  /** Politeness delay in ms between successfully fetched pages. */
  pageDelayMs?: number;
  /** Random extra delay in ms added on top of `pageDelayMs` (default 1000). */
  pageDelayJitterMs?: number;
}

/**
 * Metadata describing how a search run terminated. It is attached onto the
 * returned job array as non-index enumerable properties so the value keeps
 * behaving exactly like a plain `Job[]` (length, iteration, JSON output of the
 * jobs, spread, etc.) while callers can also inspect completeness.
 */
export interface SearchResultMeta {
  /** True when every page up to the natural end (or the requested limit) was fetched. */
  complete: boolean;
  /** `start` offset of the page that failed fatally; undefined when complete. */
  failedStart?: number;
  /** Reason the run stopped early; undefined when complete. */
  failureReason?: string;
}

/**
 * Job array augmented with optional completeness metadata. Backwards
 * compatible: code that only expects `Job[]` keeps working unchanged.
 */
export type SearchResult = Job[] & SearchResultMeta;

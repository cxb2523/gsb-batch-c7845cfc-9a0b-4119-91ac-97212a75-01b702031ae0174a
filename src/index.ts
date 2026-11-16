import { HttpClient } from './frameworks/HttpClient.js';
import { HtmlParser } from './frameworks/HtmlParser.js';
import { InMemoryCache } from './frameworks/InMemoryCache.js';
import { LinkedInClient } from './interface-adapters/LinkedInClient.js';
import { QueryOptions, SearchResult } from './types/index.js';

// Initialize Dependencies (Dependency Injection)
const httpClient = new HttpClient();
const htmlParser = new HtmlParser();
const cache = new InMemoryCache<SearchResult>();

// Initialize Client
const client = new LinkedInClient(httpClient, htmlParser, cache);

/**
 * Main query function to fetch LinkedIn jobs.
 *
 * The returned value is a `Job[]` augmented with optional, non-enumerable
 * metadata fields (`complete`, `failedStart`, `failureReason`) describing
 * whether the full result set was collected. Existing array-based usage
 * (length, iteration, JSON serialization of entries) is unchanged.
 *
 * @param {QueryOptions} queryObject - The query configuration.
 * @returns {Promise<SearchResult>} - List of jobs with completeness metadata.
 */
export const linkedInJobSearch = (queryObject: QueryOptions): Promise<SearchResult> => {
  return client.search(queryObject);
};

// Backward compatibility alias (deprecated)
export const query = linkedInJobSearch;

// Export classes and types for advanced usage or testing
export { HttpClient, HtmlParser, InMemoryCache, LinkedInClient };
export * from './types/index.js';

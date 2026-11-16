# Open LinkedIn Jobs

A powerful, type-safe Node.js package for fetching job listings from LinkedIn's public API.

![NPM Version](https://img.shields.io/npm/v/open-linkedin-jobs)
![License](https://img.shields.io/npm/l/open-linkedin-jobs)
![Downloads](https://img.shields.io/npm/dm/open-linkedin-jobs)

## Features

- **🚀 Clean Architecture**: Built with separation of concerns (Entities, Use Cases, Adapters).
- **💪 TypeScript Support**: Written in TypeScript with full type definitions included.
- **🛡️ Robust & Secure**:
    - Automatic User-Agent rotation using generic modern user agents.
    - Intelligent rate-limiting handling and retry logic.
    - Input sanitization.
- **⚡ Efficient**: Uses `cheerio` for fast HTML parsing and `keep-alive` HTTP connections.
- **📦 Zero Configuration**: Works out of the box with sensible defaults.

## Installation

```bash
npm install open-linkedin-jobs
```

## Usage

### TypeScript (Recommended)

```typescript
import { linkedInJobSearch, QueryOptions, Job } from 'open-linkedin-jobs';

const queryOptions: QueryOptions = {
  keyword: 'Software Engineer',
  location: 'United States',
  dateSincePosted: 'past week',
  jobType: 'full time',
  remoteFilter: 'remote',
  salary: '100000',
  experienceLevel: 'entry level',
  limit: 10
};

linkedInJobSearch(queryOptions).then((jobs: Job[]) => {
  jobs.forEach(job => {
    console.log(`Title: ${job.title}`);
    console.log(`Company: ${job.company}`);
    console.log(`Location: ${job.location}`);
    console.log(`URL: ${job.url}`);
    console.log('---');
  });
});
```

### JavaScript (ES Modules)

```javascript
import { linkedInJobSearch } from 'open-linkedin-jobs';

const jobs = await linkedInJobSearch({
  keyword: 'Product Manager',
  location: 'London, UK',
  limit: 5
});

console.log(jobs);
```

## Configuration

The `linkedInJobSearch(options)` function accepts a `QueryOptions` object:

| Option | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `keyword` | `string` | Job title or keyword | `'Software Engineer'` |
| `location` | `string` | Location to search | `'United States'` |
| `dateSincePosted` | `string` | `'past month'`, `'past week'`, `'24hr'` | `'past week'` |
| `jobType` | `string` | `'full time'`, `'part time'`, `'contract'`, `'temporary'`, `'volunteer'`, `'internship'` | `'full time'` |
| `remoteFilter` | `string` | `'on-site'`, `'remote'`, `'hybrid'` | `'remote'` |
| `salary` | `string` | Annual salary expectation | `'100000'` |
| `experienceLevel` | `string` | `'internship'`, `'entry level'`, `'associate'`, `'senior'`, `'director'`, `'executive'` | `'senior'` |
| `limit` | `number` | Number of jobs to fetch | `10` |
| `sortBy` | `string` | `'recent'` or `'relevant'` | `'recent'` |

## Return Type

Returns a Promise resolving to an array of `Job` objects:

```typescript
interface Job {
  title: string;
  company: string;
  location: string;
  postedDate: string;
  salary: string;
  url: string;
  companyLogoUrl: string;
  postedTimeAgo: string;
}
```

### Completeness metadata

The resolved array is a plain `Job[]` and can be consumed exactly as before,
but it also carries three non-enumerable fields describing how the search
ended. They do not affect `length`, iteration, or `JSON.stringify` output:

```typescript
interface SearchResultMeta {
  complete: boolean;        // false when a page failed after all retries
  failedStart?: number;     // `start` offset of the page that failed
  failureReason?: string;   // human-readable reason for the early stop
}
```

When a page keeps failing after the retry budget, the jobs collected from the
preceding pages are still returned with `complete === false` instead of being
discarded. Jobs are de-duplicated by URL, so retrying the same page never
produces duplicates. Only complete result sets are written to the cache;
partial results are never cached.

```typescript
const jobs = await linkedInJobSearch(queryOptions);

if (!jobs.complete) {
  console.warn(`Incomplete results: page at start=${jobs.failedStart} failed: ${jobs.failureReason}`);
}
```

## Disclaimer & Legal

This package is intended for **educational and research purposes only**.

- This tool scrapes data from LinkedIn's public guest pages.
- Standard scraping caveats apply. LinkedIn may block your IP if you make too many requests too quickly.
- By using this tool, you agree to comply with LinkedIn's Terms of Service.
- The author (Hanish Rao) is not responsible for any misuse of this tool or any consequences resulting from its use.

## License

GNU Affero General Public License v3.0 (AGPL-3.0)

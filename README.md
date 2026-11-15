# Web_Crawler
A scalable, open-source webcrawler that writes website data to file while crawling each new webpage

# Installation
##### Clone this repository:
```
$ git clone https://github.com/Boomslet/Web_Crawler
```

# Usage
##### 1. Install setup.py
```
$ python setup.py install
```
##### 2. Run controller.py
```
%Run controller.py
```

##### 3. Call crawl(*urls) with your desired URL(s):
```Python
>>> summary = crawl('https://github.com/')
```

##### 4. Crawl!
```Python
Successfully crawled https://github.com/
Successfully crawled https://github.com/features
Successfully crawled https://github.com/business
Successfully crawled https://github.com/pricing
```

# Behaviour

Each `crawl(*urls)` call has a well-defined end and an observable result:

- **Same host only.** Only links whose host name matches the start URL's
  host name are followed. Off-site links are never requested or queued;
  they are counted as skipped external links.
- **Fetch once per task.** Links are normalized (fragments dropped,
  scheme/host lower-cased, default ports removed) and atomically claimed
  before being queued, so a normalized URL — including the start page
  itself and pages reached through in-site link cycles — is fetched at
  most once, even when several start URLs run concurrently.
- **Bounded termination.** The queue can only shrink once every
  reachable same-host page has been claimed, so each worker stops on its
  own when its queue drains.
- **Failures are recorded with a reason.** Failed URLs are appended to
  `error_log.txt` as `url -> reason` (for example
  `http://host/missing -> HTTP 404 Not Found`,
  `URLError: ...` or the exception type) and the rest of the queue
  keeps processing.
- **Concurrent safety.** Workers started by one `crawl()` call share one
  thread-safe dedup set and one set of output files. All file writes and
  counters are lock-protected; every record in `dump.txt`, `log.txt` and
  `error_log.txt` carries its full URL, so records from different tasks
  never get mixed.
- **Observable outcome.** `crawl()` blocks until every worker finishes
  and returns a `CrawlSummary`:

```Python
summary = crawl('https://github.com/', 'https://github.com/features')
summary.success            # total pages fetched successfully
summary.failed             # total failed fetches
summary.skipped_external   # total off-site links not followed
summary.results            # one CrawlResult per requested start URL
summary['https://github.com/'].stats()
# {'base_url': ..., 'success': N, 'failed': N, 'skipped_external': N,
#  'successful_urls': [...], 'failed_urls': [(url, reason), ...],
#  'external_urls': [...]}
```

# Verifying locally

A self-contained fixture site (in-site link cycles, off-site links, a
404 page, fragment variants) plus an end-to-end test lives under
`tests/`:

```
$ python -I tests/verify_crawler.py
$ python -I tests/verify_edge_cases.py
```

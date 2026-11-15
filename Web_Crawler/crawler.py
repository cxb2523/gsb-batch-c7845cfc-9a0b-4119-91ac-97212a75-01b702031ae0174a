"""
A simple-to-use web worker that collects
website data as it crawls

Author: Mark Boon
Date: 06/09/2017
Version: 3.0.0
"""

import threading
import urllib.error
import urllib.request
from collections import deque
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


REQUEST_TIMEOUT = 15

DUMP_FILE = "dump.txt"
LOG_FILE = "log.txt"
ERROR_LOG_FILE = "error_log.txt"

_DEFAULT_PORTS = {"http": 80, "https": 443}
_SUPPORTED_SCHEMES = ("http", "https")


def normalize_url(url):
    """Return a canonical form of *url* used for de-duplication.

    Fragments are dropped, scheme/host are lower-cased, default ports
    are removed and an empty path becomes "/". An empty string is
    returned for anything that is not an http(s) URL (mailto:,
    javascript:, ...) so callers can simply ignore it.
    """
    if not url:
        return ""
    url = url.strip()
    if not url:
        return ""

    url, _fragment = urldefrag(url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _SUPPORTED_SCHEMES:
        return ""

    hostname = parts.hostname or ""
    port = parts.port
    if port is None:
        netloc = parts.netloc.lower()
    elif port == _DEFAULT_PORTS.get(scheme):
        netloc = hostname
    else:
        netloc = "{}:{}".format(hostname, port)

    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


class CrawlResult:
    """Observable outcome of a single start URL inside a crawl() call."""

    def __init__(self, base_url):
        self.base_url = base_url
        self.success = 0
        self.failed = 0
        self.skipped_external = 0
        self.successful_urls = []
        self.failed_urls = []        # list of (url, reason)
        self.external_urls = []
        self._lock = threading.Lock()

    def add_success(self, url):
        with self._lock:
            self.success += 1
            self.successful_urls.append(url)

    def add_failure(self, url, reason):
        with self._lock:
            self.failed += 1
            self.failed_urls.append((url, reason))

    def add_external(self, url):
        with self._lock:
            self.skipped_external += 1
            self.external_urls.append(url)

    def stats(self):
        with self._lock:
            return {
                "base_url": self.base_url,
                "success": self.success,
                "failed": self.failed,
                "skipped_external": self.skipped_external,
                "successful_urls": list(self.successful_urls),
                "failed_urls": list(self.failed_urls),
                "external_urls": list(self.external_urls),
            }

    def __repr__(self):
        return (
            "CrawlResult(base_url={!r}, success={}, failed={}, "
            "skipped_external={})".format(
                self.base_url, self.success, self.failed, self.skipped_external
            )
        )


class CrawlSession:
    """Thread-safe state shared by every worker of one crawl() call."""

    def __init__(self):
        self._seen_lock = threading.Lock()
        self._seen = set()
        self._file_lock = threading.Lock()
        self._print_lock = threading.Lock()

    def claim(self, normalized_url):
        """Atomically reserve a normalized URL.

        Returns True only for the first thread claiming it, so every
        normalized URL is queued and fetched at most once per task,
        across all concurrent workers.
        """
        with self._seen_lock:
            if normalized_url in self._seen:
                return False
            self._seen.add(normalized_url)
            return True

    def append_file(self, path, data):
        # One lock for every output file: concurrent workers can never
        # interleave or truncate each other's records.
        with self._file_lock:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(data)

    def report(self, message):
        with self._print_lock:
            print(message)


class Worker(threading.Thread):
    # Kept for the standalone write_file() helper used outside a session.
    _standalone_file_lock = threading.Lock()

    def __init__(self, base_url, session=None):
        super().__init__(daemon=True, name="crawler-{}".format(base_url))
        self.base_url = base_url
        self.base_host = urlsplit(base_url).hostname or ""
        self.queue = deque()
        self.crawled = set()          # normalized URLs this worker fetched
        self.external_seen = set()    # external URLs counted once for this task
        self.session = session or CrawlSession()
        self.result = CrawlResult(base_url)

    @staticmethod
    def write_file(path, data):
        """Append *data* to *path* (thread-safe standalone helper)."""
        with Worker._standalone_file_lock:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(data)

    def run(self):
        self.work()

    def work(self):
        # The start page itself goes through the same shared dedup gate,
        # so duplicate start URLs and cycles back to it are impossible.
        start = normalize_url(self.base_url)
        if not start:
            self._record_failure(self.base_url, "invalid URL")
            return
        if self.session.claim(start):
            self.queue.append(start)

        # The loop ends as soon as the queue drains: every link is
        # claimed before being queued, so the queue can only shrink
        # once the reachable site has been fully explored.
        while self.queue:
            url = self.queue.popleft()
            try:
                with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as response:
                    status = response.status
                    body = response.read()
            except urllib.error.HTTPError as exc:
                # e.g. 404 Not Found / 500 Internal Server Error
                self._record_failure(
                    url, "HTTP {} {}".format(exc.code, exc.reason).strip()
                )
                continue
            except urllib.error.URLError as exc:
                # DNS failure, connection refused, timeout, ...
                self._record_failure(url, "URLError: {}".format(exc.reason))
                continue
            except Exception as exc:
                # Any other transport problem must not kill the queue.
                self._record_failure(
                    url, "{}: {}".format(type(exc).__name__, exc)
                )
                continue

            try:
                soup = BeautifulSoup(body, "lxml")
                text = soup.get_text()
            except Exception as exc:
                self._record_failure(
                    url, "{}: {}".format(type(exc).__name__, exc)
                )
                continue

            # Every record carries its URL, so lines from concurrent
            # workers can always be attributed back to the right task.
            self.session.append_file(
                DUMP_FILE,
                "\n===== {} (HTTP {}) =====\n{}\n".format(url, status, text),
            )
            self.session.append_file(LOG_FILE, url + "\n")
            self.crawled.add(url)
            self.result.add_success(url)
            self.session.report("Successfully crawled {}".format(url))

            for anchor in soup.find_all("a", href=True):
                self._schedule(anchor["href"], found_on=url)

    def _schedule(self, href, found_on):
        normalized = normalize_url(urljoin(found_on, href))
        if not normalized:
            # mailto:/javascript:/... links are not web pages at all.
            return

        if urlsplit(normalized).hostname != self.base_host:
            # Off-site link: never queued, counted once for this task.
            if normalized not in self.external_seen:
                self.external_seen.add(normalized)
                self.result.add_external(normalized)
            return

        # Same-host link. The atomic claim makes the dedup set safe for
        # several workers crawling concurrently.
        if self.session.claim(normalized):
            self.queue.append(normalized)

    def _record_failure(self, url, reason):
        self.session.append_file(
            ERROR_LOG_FILE, "{} -> {}\n".format(url, reason)
        )
        self.result.add_failure(url, reason)
        self.session.report("Failed to crawl {} ({})".format(url, reason))


def crawl(*urls):
    """Crawl each entry of *urls* concurrently and block until all finish.

    All workers share one thread-safe dedup set and one set of output
    files. Returns a CrawlSummary whose ``results`` attribute keeps one
    CrawlResult per requested start URL (in order, so duplicate start
    URLs are both observable) and whose mapping-style access keeps the
    familiar URL -> CrawlResult lookup for distinct URLs. Each result
    exposes separate success / failed / skipped_external counters (and
    the underlying URL lists).
    """
    session = CrawlSession()
    workers = [Worker(link, session) for link in urls]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    return CrawlSummary(workers)


class CrawlSummary:
    """Observable outcome of one crawl() call."""

    def __init__(self, workers):
        self.results = [worker.result for worker in workers]
        self._by_url = {}
        for worker in workers:
            self._by_url.setdefault(worker.base_url, worker.result)
        self.success = sum(result.success for result in self.results)
        self.failed = sum(result.failed for result in self.results)
        self.skipped_external = sum(
            result.skipped_external for result in self.results
        )

    def __getitem__(self, url):
        return self._by_url[url]

    def __iter__(self):
        return iter(self._by_url)

    def __len__(self):
        return len(self._by_url)

    def keys(self):
        return self._by_url.keys()

    def values(self):
        return self._by_url.values()

    def items(self):
        return self._by_url.items()

    def stats(self):
        return {
            "success": self.success,
            "failed": self.failed,
            "skipped_external": self.skipped_external,
            "results": [result.stats() for result in self.results],
        }

    def __repr__(self):
        return (
            "CrawlSummary(success={}, failed={}, skipped_external={}, "
            "tasks={})".format(
                self.success, self.failed, self.skipped_external,
                len(self.results),
            )
        )

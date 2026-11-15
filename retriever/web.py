"""Polite web crawler: robots.txt compliance, rate limiting, content extraction.

Besides the legacy :meth:`WebCrawler.crawl` API, this module offers
:meth:`WebCrawler.crawl_and_index`, which walks the frontier, indexes every
successfully fetched page, and returns a per-page settlement report.  A single
page failing (HTTP error, timeout, robots denial) never aborts the batch, and
pages already indexed stay in the index.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser as robotparser
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Iterable, Iterator
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from retriever.indexer import Document

if TYPE_CHECKING:  # pragma: no cover - typing only
    from retriever.indexer import IndexManager

try:
    import trafilatura  # type: ignore

    HAS_TRAFILATURA = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_TRAFILATURA = False

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "DeepSearchBot/0.1 (+https://example.com/bot)"}


class PageOutcome(str, Enum):
    """Terminal state of one URL in a batch crawl."""

    INDEXED = "indexed"
    ROBOTS_DENIED = "robots_denied"
    FETCH_FAILED = "fetch_failed"
    DUPLICATE = "duplicate"
    UNPROCESSED = "unprocessed"


@dataclass
class CrawledPage:
    """Result of fetching and extracting one URL."""

    url: str
    text: str
    status_code: int
    fetched_at: float = field(default_factory=time.time)
    raw_html: str | None = None


@dataclass
class FetchFailure:
    """Diagnostic details for a page that could not be fetched."""

    status_code: int | None = None
    error: str | None = None


@dataclass
class PageReport:
    """Settlement entry for a single URL.

    ``chunks`` is the number of chunks actually written to the index (always
    ``0`` for pages that were not indexed).  ``duplicate_of`` points at the URL
    where this document's content fingerprint was first seen.
    """

    url: str
    outcome: PageOutcome
    status_code: int | None = None
    chunks: int = 0
    duplicate_of: str | None = None
    error: str | None = None
    depth: int = 0

    @property
    def indexed(self) -> bool:
        """True when this page was newly written to the index."""
        return self.outcome is PageOutcome.INDEXED

    @property
    def duplicate(self) -> bool:
        """True when this page's content was already indexed elsewhere."""
        return self.outcome is PageOutcome.DUPLICATE

    @property
    def duplicate_of_url(self) -> str | None:
        """Alias for callers that prefer an explicit URL name."""
        return self.duplicate_of

    @property
    def chunk_count(self) -> int:
        """Alias for :attr:`chunks`."""
        return self.chunks

    def to_dict(self) -> dict:
        """Plain-data representation suitable for JSON/API responses."""
        return {
            "url": self.url,
            "outcome": self.outcome.value,
            "status_code": self.status_code,
            "chunks": self.chunks,
            "duplicate_of": self.duplicate_of,
            "error": self.error,
            "depth": self.depth,
        }


@dataclass
class CrawlReport:
    """Per-page settlement plus aggregate counts for one batch crawl."""

    pages: list[PageReport] = field(default_factory=list)

    def __iter__(self) -> Iterator[PageReport]:
        return iter(self.pages)

    def __len__(self) -> int:
        return len(self.pages)

    def by_outcome(self, outcome: PageOutcome | str) -> list[PageReport]:
        """Return every page that ended with ``outcome``."""
        wanted = PageOutcome(outcome)
        return [page for page in self.pages if page.outcome is wanted]

    @property
    def indexed_pages(self) -> list[PageReport]:
        return self.by_outcome(PageOutcome.INDEXED)

    @property
    def failed_pages(self) -> list[PageReport]:
        return self.by_outcome(PageOutcome.FETCH_FAILED)

    @property
    def denied_pages(self) -> list[PageReport]:
        return self.by_outcome(PageOutcome.ROBOTS_DENIED)

    @property
    def duplicate_pages(self) -> list[PageReport]:
        return self.by_outcome(PageOutcome.DUPLICATE)

    @property
    def unprocessed_pages(self) -> list[PageReport]:
        return self.by_outcome(PageOutcome.UNPROCESSED)

    @property
    def summary(self) -> dict[str, int]:
        """Aggregate counts keyed by outcome, plus ``total``.

        Every entry in :attr:`pages` contributes to exactly one outcome bucket,
        so the five buckets always sum to ``total``.
        """
        counts = {outcome.value: 0 for outcome in PageOutcome}
        for page in self.pages:
            counts[page.outcome.value] += 1
        counts["total"] = len(self.pages)
        return counts

    def counts(self) -> dict[str, int]:
        """Method alias for :attr:`summary`."""
        return self.summary

    @property
    def indexed_count(self) -> int:
        return self.summary[PageOutcome.INDEXED.value]

    @property
    def success_count(self) -> int:
        return self.indexed_count

    @property
    def failed_count(self) -> int:
        return self.summary[PageOutcome.FETCH_FAILED.value]

    @property
    def denied_count(self) -> int:
        return self.summary[PageOutcome.ROBOTS_DENIED.value]

    @property
    def duplicate_count(self) -> int:
        return self.summary[PageOutcome.DUPLICATE.value]

    @property
    def unprocessed_count(self) -> int:
        return self.summary[PageOutcome.UNPROCESSED.value]

    @property
    def total_chunks(self) -> int:
        """Number of chunks written to the index across all pages."""
        return sum(page.chunks for page in self.pages)

    def to_dict(self) -> dict:
        """Plain-data representation of the full settlement."""
        return {
            "pages": [page.to_dict() for page in self.pages],
            "summary": self.summary,
        }


@dataclass
class _TraversalEvent:
    """Internal BFS event carrying either a fetched page or a settlement."""

    url: str
    depth: int
    outcome: PageOutcome | None = None
    page: CrawledPage | None = None
    status_code: int | None = None
    error: str | None = None


class RateLimiter:
    """Per-host minimum-delay throttle to stay polite with origin servers."""

    def __init__(self, min_delay_ms: int = 500) -> None:
        self.min_delay = min_delay_ms / 1000.0
        self._last_hit: dict[str, float] = {}

    def wait(self, host: str) -> None:
        """Block until this host's cooldown has elapsed."""
        elapsed = time.monotonic() - self._last_hit.get(host, 0.0)
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self._last_hit[host] = time.monotonic()


class RobotsCache:
    """Caches parsed robots.txt rules per host."""

    def __init__(self) -> None:
        self._cache: dict[str, robotparser.RobotFileParser] = {}

    def allowed(self, url: str, agent: str = "*") -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._cache:
            parser = robotparser.RobotFileParser()
            parser.set_url(f"{base}/robots.txt")
            try:
                parser.read()
            except OSError:
                parser.allow_all = True  # unreachable robots.txt -> permissive
            self._cache[base] = parser
        return self._cache[base].can_fetch(agent, url)


class WebCrawler:
    """Breadth-first crawler honoring robots.txt and per-host rate limits."""

    def __init__(self, max_depth: int = 3, max_pages: int = 50,
                 delay_ms: int = 500, timeout_s: float = 10.0,
                 fetcher=None, robots=None) -> None:
        self.max_depth, self.max_pages = max_depth, max_pages
        self.timeout = timeout_s
        self.limiter = RateLimiter(delay_ms)
        self.robots = robots if robots is not None else RobotsCache()
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # Optional injection seam: a callable ``url -> CrawledPage | None``.
        self.fetcher = fetcher
        # Details about the most recent failed ``_fetch`` call (if any).
        self.last_failure: FetchFailure | None = None

    @staticmethod
    def _normalize(url: str) -> str:
        """Strip fragments/trailing slashes so the frontier stays deduplicated."""
        parts = urlparse(url)
        path = parts.path.rstrip("/")
        return urlunparse((parts.scheme, parts.netloc, path,
                           parts.params, parts.query, ""))

    # ------------------------------------------------------------------
    # Public crawl APIs
    # ------------------------------------------------------------------
    def crawl(self, seeds: list[str],
              index: "IndexManager | None" = None) -> list[CrawledPage] | CrawlReport:
        """Run BFS from seed URLs, returning every successfully fetched page.

        With ``index`` omitted this is the legacy interface returning a list of
        :class:`CrawledPage`.  Passing an :class:`IndexManager` indexes each
        fetched page and returns a :class:`CrawlReport` settlement.
        """
        if index is not None:
            return self.crawl_and_index(seeds, index)
        pages = [event.page for event in self._traverse(seeds)
                 if event.page is not None]
        logger.info("crawl finished: %d pages", len(pages))
        return pages

    def crawl_and_index(self, seeds: Iterable[str],
                        index: "IndexManager") -> CrawlReport:
        """Crawl ``seeds`` and index every successfully fetched page.

        Pages are indexed as soon as they are fetched, so a later failure can
        never discard earlier work.  ``max_pages`` counts *successfully fetched*
        pages (duplicates included); once reached, URLs still sitting in the
        frontier are reported as ``unprocessed`` without being fetched.
        """
        report = CrawlReport()
        for event in self._traverse(seeds):
            if event.page is None:
                report.pages.append(PageReport(
                    url=event.url,
                    outcome=event.outcome or PageOutcome.FETCH_FAILED,
                    status_code=event.status_code,
                    error=event.error,
                    depth=event.depth,
                ))
                continue

            page = event.page
            result = index.add_with_report(
                [Document(id=page.url, text=page.text, url=page.url)]
            )[0]
            outcome = PageOutcome(result.status)
            report.pages.append(PageReport(
                url=page.url,
                outcome=outcome,
                status_code=page.status_code,
                chunks=result.chunks,
                duplicate_of=result.duplicate_of,
                depth=event.depth,
            ))

        logger.info("crawl_and_index finished: %s", report.summary)
        return report

    # Convenience aliases: make the intent readable at call sites.
    def crawl_with_report(self, seeds: Iterable[str],
                          index: "IndexManager") -> CrawlReport:
        """Alias for :meth:`crawl_and_index`."""
        return self.crawl_and_index(seeds, index)

    def crawl_and_report(self, seeds: Iterable[str],
                         index: "IndexManager") -> CrawlReport:
        """Alias for :meth:`crawl_and_index`."""
        return self.crawl_and_index(seeds, index)

    # ------------------------------------------------------------------
    # BFS traversal
    # ------------------------------------------------------------------
    def _traverse(self, seeds: Iterable[str]) -> Iterator[_TraversalEvent]:
        """Walk the frontier, yielding one event per distinct URL.

        Failures are reported and skipped; traversal always continues.  Once
        ``max_pages`` successful fetches have happened, the remainder of the
        frontier is drained as ``unprocessed`` events.
        """
        frontier: deque[tuple[str, int]] = deque(
            (self._normalize(seed), 0) for seed in seeds
        )
        visited: set[str] = set()
        fetched = 0

        while frontier:
            url, depth = frontier.popleft()
            if url in visited:
                continue
            visited.add(url)

            if fetched >= self.max_pages:
                yield _TraversalEvent(url, depth, PageOutcome.UNPROCESSED)
                continue

            try:
                allowed = self.robots.allowed(url)
            except Exception as exc:  # per-page isolation: never abort the batch
                logger.warning("robots check failed %s: %s", url, exc)
                yield _TraversalEvent(
                    url, depth, PageOutcome.ROBOTS_DENIED,
                    error=f"robots check failed: {exc}",
                )
                continue
            if not allowed:
                yield _TraversalEvent(url, depth, PageOutcome.ROBOTS_DENIED)
                continue

            host = urlparse(url).netloc
            self.limiter.wait(host)

            self.last_failure = None
            try:
                page = self._fetch(url)
            except Exception as exc:  # defensive: injected fetchers may raise
                failure = self._describe_exception(exc)
                logger.warning("fetch failed %s: %s", url, failure.error)
                yield _TraversalEvent(
                    url, depth, PageOutcome.FETCH_FAILED,
                    status_code=failure.status_code,
                    error=failure.error,
                )
                continue

            if page is None:
                failure = self.last_failure or FetchFailure()
                logger.warning("fetch failed %s: %s", url,
                               failure.error or "unknown error")
                yield _TraversalEvent(
                    url, depth, PageOutcome.FETCH_FAILED,
                    status_code=failure.status_code,
                    error=failure.error,
                )
                continue

            fetched += 1
            yield _TraversalEvent(url, depth, page=page)

            if depth < self.max_depth:
                # Links must come from raw HTML; extracted text has no markup.
                markup = page.raw_html if page.raw_html is not None else page.text
                frontier.extend(
                    (self._normalize(link), depth + 1)
                    for link in self._extract_links(markup or "", url)
                )

    # ------------------------------------------------------------------
    # Fetching/extraction helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _describe_exception(exc: Exception) -> FetchFailure:
        """Translate an exception into a :class:`FetchFailure`."""
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(exc, requests.Timeout):
            return FetchFailure(status, f"timeout: {exc}")
        if isinstance(exc, requests.HTTPError):
            return FetchFailure(
                status, f"HTTP {status}" if status else str(exc)
            )
        return FetchFailure(status, f"{type(exc).__name__}: {exc}")

    def _fetch(self, url: str) -> CrawledPage | None:
        """Download one URL and extract readable content.

        On failure, returns ``None`` and records diagnostics in
        :attr:`last_failure` so the caller can report a status code.
        """
        if self.fetcher is not None:
            try:
                return self.fetcher(url)
            except requests.RequestException as exc:
                self.last_failure = self._describe_exception(exc)
                return None
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            self.last_failure = self._describe_exception(exc)
            logger.warning("fetch failed %s: %s", url, exc)
            return None
        return CrawledPage(url=url, text=self._extract(resp.text),
                           status_code=resp.status_code, raw_html=resp.text)

    @staticmethod
    def _extract(html: str) -> str:
        """Prefer trafilatura main-content extraction; regex-strip fallback."""
        if HAS_TRAFILATURA:
            extracted = trafilatura.extract(html)
            if extracted:
                return extracted
        import re
        return re.sub(r"<[^>]+>", " ", html)[:20000]

    @staticmethod
    def _extract_links(markup: str, base_url: str) -> list[str]:
        """Extract absolute HTTP(S) links from raw HTML (max 20)."""
        import re
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', markup,
                           flags=re.IGNORECASE)
        links: list[str] = []
        for href in hrefs:
            if href.lower().startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(base_url, href)
            if urlparse(absolute).scheme in {"http", "https"}:
                links.append(absolute)
        return links[:20]


def crawl_and_index(seeds: Iterable[str], index: "IndexManager",
                    **crawler_options) -> CrawlReport:
    """Module-level convenience wrapper around :meth:`WebCrawler.crawl_and_index`."""
    return WebCrawler(**crawler_options).crawl_and_index(seeds, index)

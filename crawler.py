"""Resumable website crawler used by the Streamlit UI.

The crawling rules mirror ``scrape.py``:

* start from ``config.START_URL``;
* follow only links on the same domain;
* visit at most ``MAX_PAGES`` pages per round;
* skip binary/asset URLs (``.pdf``, ``.jpg``, ...);
* use a 10 second timeout for every request;
* a failing page is recorded and the crawl continues.

Unlike ``scrape.py`` this module keeps the crawl state (visited set, pending
queue, success/failure lists) on a ``CrawlSession`` object.  A session can be
stopped between two pages and resumed later without refetching pages that were
already visited.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import deque
from typing import Callable, Deque, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import SCRAPED_DATA_PATH

MAX_PAGES = 200
REQUEST_TIMEOUT = 10
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}
# ``scrape.py`` skipped these extensions; a few other obvious binaries are
# included as well since they can never yield readable text.
SKIP_EXTENSIONS = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".zip",
    ".svg",
    ".webp",
    ".mp3",
    ".mp4",
    ".css",
    ".js",
    ".ico",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".gz",
    ".tar",
    ".rar",
    ".7z",
    ".exe",
)

STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_COMPLETED = "completed"


class CrawlPage:
    """A successfully scraped page."""

    __slots__ = ("url", "title", "content")

    def __init__(self, url: str, title: str, content: str) -> None:
        self.url = url
        self.title = title
        self.content = content

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"CrawlPage(url={self.url!r}, title={self.title!r}, "
            f"chars={len(self.content)})"
        )

    def as_record(self) -> dict:
        return {"url": self.url, "title": self.title, "content": self.content}


class CrawlFailure:
    """A visited page that could not be scraped."""

    __slots__ = ("url", "error")

    def __init__(self, url: str, error: str) -> None:
        self.url = url
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"CrawlFailure(url={self.url!r}, error={self.error!r})"


def normalize_url(url: str) -> str:
    """Normalize a URL the same way ``scrape.py`` does.

    Query strings and fragments are dropped so two links pointing at the same
    page are only crawled once.
    """

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def fetch_url(url: str):
    """Default network fetcher (kept separate so tests can inject a fake)."""

    return requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)


def describe_error(exc: BaseException) -> str:
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


def parse_html(content: bytes | str) -> Tuple[str, str, List[str]]:
    """Extract ``(title, text, hrefs)`` from an HTML document.

    ``script``/``style``/``header``/``footer``/``nav``/``aside`` nodes are
    removed before the visible text is collected, which is what ``scrape.py``
    does as well.  Links are collected from the whole document so navigation
    links still get discovered even though their text is not scraped.
    """

    try:
        soup = BeautifulSoup(content, "lxml")
    except Exception:  # pragma: no cover - lxml is a hard requirement, be safe
        soup = BeautifulSoup(content, "html.parser")

    title_tag = soup.title
    title = title_tag.get_text(strip=True) if title_tag is not None else ""
    title = title or "No Title"

    hrefs = [a_tag["href"] for a_tag in soup.find_all("a", href=True)]

    body = soup.find("body")
    if body is None:
        return title, "", hrefs

    for tag in body(["script", "style", "header", "footer", "nav", "aside"]):
        tag.decompose()

    text = body.get_text(separator="\n", strip=True)
    return title, text, hrefs


class CrawlSession:
    """One resumable crawl round.

    A session is a small explicit state machine::

        running -> stopped -> running -> ... -> completed

    ``step()`` visits exactly one pending URL, so the UI can render progress
    between pages and let the user stop the crawl at any time.  Everything
    that matters for resuming (``visited``, ``pending``, ``successes`` and
    ``failures``) lives on the session object.
    """

    def __init__(
        self,
        start_url: str,
        max_pages: int = MAX_PAGES,
        fetcher: Optional[Callable[[str], object]] = None,
    ) -> None:
        if max_pages < 0:
            raise ValueError("max_pages must not be negative")

        self.start_url = normalize_url(start_url)
        self.domain = urlparse(self.start_url).netloc
        self.max_pages = max_pages
        self.fetcher = fetcher or fetch_url

        self.pending: Deque[str] = deque([self.start_url])
        self._pending: Set[str] = {self.start_url}

        self.visited: List[str] = []
        self._visited: Set[str] = set()

        self.successes: List[CrawlPage] = []
        self.failures: List[CrawlFailure] = []

        self.current_url: Optional[str] = None
        self.status = STATUS_RUNNING
        self.start_url_error: Optional[str] = None

        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Introspection helpers used by the UI
    # ------------------------------------------------------------------
    @property
    def total_visited(self) -> int:
        return len(self.visited)

    @property
    def success_count(self) -> int:
        return len(self.successes)

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    @property
    def pending_count(self) -> int:
        return len(self.pending)

    @property
    def is_exhausted(self) -> bool:
        """True when there is nothing left to do within the page limit."""

        with self._lock:
            return not self.pending or len(self.visited) >= self.max_pages

    @property
    def reached_page_limit(self) -> bool:
        with self._lock:
            return len(self.visited) >= self.max_pages and bool(self.pending)

    def snapshot(self) -> dict:
        """Consistent view of the crawl state for rendering."""

        with self._lock:
            return {
                "status": self.status,
                "current_url": self.current_url,
                "visited": len(self.visited),
                "success": len(self.successes),
                "failure": len(self.failures),
                "pending": len(self.pending),
                "max_pages": self.max_pages,
                "reached_page_limit": len(self.visited) >= self.max_pages
                and bool(self.pending),
                "start_url_error": self.start_url_error,
                "successes": [
                    (page.url, page.title, len(page.content))
                    for page in self.successes
                ],
                "failures": [(item.url, item.error) for item in self.failures],
            }

    # ------------------------------------------------------------------
    # Crawl control
    # ------------------------------------------------------------------
    def stop(self) -> str:
        """Request a stop; it takes effect between two pages."""

        with self._lock:
            if self.status == STATUS_RUNNING:
                self.status = STATUS_STOPPED
            return self.status

    def resume(self) -> str:
        with self._lock:
            if self.status == STATUS_STOPPED:
                self.status = STATUS_RUNNING
            return self.status

    def complete(self) -> str:
        """Finish the round.

        A stop only leaves the round paused when there is actually work left;
        if the stop landed on the very last page (or the page limit was hit),
        the round is completed instead of offering a pointless resume.
        """

        with self._lock:
            if self.status == STATUS_RUNNING or self.is_exhausted:
                self.status = STATUS_COMPLETED
            return self.status

    def step(self) -> bool:
        """Visit at most one page.

        Returns ``True`` when a page was processed (success or failure) and
        ``False`` when the round is paused or already done.
        """

        with self._lock:
            if self.status != STATUS_RUNNING:
                return False

            url = self._next_url()
            if url is None:
                self._complete_locked()
                return False

            self.current_url = url
            self.visited.append(url)
            self._visited.add(url)

        try:
            response = self.fetcher(url)
            response.raise_for_status()
            title, text, hrefs = parse_html(response.content)
        except Exception as exc:  # noqa: BLE001 - one bad page must never abort
            with self._lock:
                self._record_failure(url, describe_error(exc))
            return True

        with self._lock:
            if text:
                self.successes.append(CrawlPage(url, title, text))
            else:
                # Every visited page must leave a result behind.
                self._record_failure(url, "页面中没有可提取的正文内容")

            for href in hrefs:
                # Link discovery must never break the round: one malformed
                # link is skipped, every other page is still crawled.
                try:
                    candidate = normalize_url(urljoin(url, href))
                    if self._should_enqueue(candidate):
                        self.pending.append(candidate)
                        self._pending.add(candidate)
                except Exception:  # noqa: BLE001 - skip malformed links
                    continue

        return True

    def run(self) -> str:
        """Run until the queue is empty, the page limit is hit or a stop."""

        while True:
            with self._lock:
                if self.status != STATUS_RUNNING:
                    return self.status
                if self.is_exhausted:
                    self._complete_locked()
                    return self.status
            if not self.step():
                return self.status

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def to_ingest_records(self) -> List[dict]:
        """Records in the list format consumed by ``ingest.py``."""

        with self._lock:
            return [page.as_record() for page in self.successes]

    def save_results(self, path: str = SCRAPED_DATA_PATH) -> bool:
        """Write successful pages to ``path``.

        Returns ``False`` (and leaves an existing file untouched) when the
        round produced no successful page at all.
        """

        return save_scraped_data(self.to_ingest_records(), path)

    # ------------------------------------------------------------------
    # Internal helpers (callers already hold ``self._lock``)
    # ------------------------------------------------------------------
    def _next_url(self) -> Optional[str]:
        while self.pending:
            url = self.pending.popleft()
            self._pending.discard(url)
            if url not in self._visited:
                return url
        return None

    def _record_failure(self, url: str, error: str) -> None:
        self.failures.append(CrawlFailure(url, error))
        if url == self.start_url and self.start_url_error is None:
            self.start_url_error = error

    def _should_enqueue(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        return (
            parsed.netloc == self.domain
            and url not in self._visited
            and url not in self._pending
            and not parsed.path.lower().endswith(SKIP_EXTENSIONS)
        )

    def _complete_locked(self) -> None:
        if self.status == STATUS_RUNNING:
            self.status = STATUS_COMPLETED


class CrawlRunner:
    """Runs a ``CrawlSession`` in a background thread for the Streamlit UI.

    Streamlit script runs block while they execute, so the crawl runs on a
    worker thread; the page polls a thread-safe state snapshot and can call
    :meth:`stop` at any time.  The crawl thread saves the successful pages to
    disk when it ends (natural completion or user stop).
    """

    def __init__(
        self,
        session: CrawlSession,
        save_path: str = SCRAPED_DATA_PATH,
    ) -> None:
        self.session = session
        self.save_path = save_path

        self.error: Optional[str] = None
        self.save_error: Optional[str] = None
        self.saved = False
        self.blank_round = False

        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start (or restart, after a stop) the crawl worker."""

        if self.is_alive():
            # A stop was requested but the worker has not finished the page it
            # is currently fetching.  Flip the session back to running so the
            # worker keeps going instead of ending, rather than ignoring the
            # user's "continue" click.
            self.session.resume()
            return
        self.error = None
        self.save_error = None
        self.saved = False
        self.blank_round = False
        self.session.resume()
        self._thread = threading.Thread(
            target=self._run, name="crawl-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self.session.stop()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_active(self) -> bool:
        """True while the crawl is running (or still winding down)."""

        return self.is_alive()

    # ------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self.session.run()
        except Exception as exc:  # noqa: BLE001 - surface the reason in the UI
            self.error = describe_error(exc)
        finally:
            self.session.complete()
            self._save_results()

    def _save_results(self) -> None:
        if not self.session.successes:
            # Never overwrite an existing data file with an empty round.
            self.blank_round = True
            return
        try:
            save_scraped_data(self.session.to_ingest_records(), self.save_path)
            self.saved = True
        except Exception as exc:  # noqa: BLE001 - surface the reason in the UI
            self.save_error = describe_error(exc)


def save_scraped_data(records: Iterable, path: str = SCRAPED_DATA_PATH) -> bool:
    """Atomically write ``records`` as the JSON list ``ingest.py`` expects.

    ``records`` may be ``CrawlPage`` objects or plain dictionaries.  Returns
    ``False`` without touching ``path`` when there is nothing to save.
    """

    payload = []
    for item in records:
        if isinstance(item, CrawlPage):
            payload.append(item.as_record())
        else:
            payload.append(dict(item))
    if not payload:
        return False

    target_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    os.makedirs(target_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".scraped-", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return True

"""ZenRowsCrawlClient — the typed facade over the Crawl API (New).

A crawl starts from one URL, follows links (up to `depth` hops when
set), and keeps the URLs that match its patterns, up to `max_items`. It
stops at `max_items` or `max_pages`, or when no link is left. It runs as
a job: `create` returns at once with `status=running`, and the URLs
grow as pages finish.

    client = ZenRowsCrawlClient(api_key)
    crawl = client.create("https://example.com/shop/", depth=1,
                          include_patterns=["/product/"])
    client.wait(crawl.crawl_id)
    for result in client.results(crawl.crawl_id):
        print(result.url)

Requests go through the SDK's shared HTTP transport (auth header,
retries, problem+json mapping), which raises `CrawlAPIError`.
"""

import json
import logging
import math
import random
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any, Literal

import httpx

from zenrows.__version__ import __version__
from zenrows.batch._transport import _RETRYABLE_STATUSES, _Transport
from zenrows.crawl.errors import CrawlAPIError, CrawlDownloadError
from zenrows.crawl.models import (
    Crawl,
    CrawlList,
    CrawlResult,
    CrawlStatus,
    CrawlStop,
    CrawlWebhookDict,
    CrawlWithResults,
    DownloadLine,
)

_DEFAULT_BASE_URL = "https://api.zenrows.com/v1"
# The scraping client's User-Agent, so the API reads Crawl requests as
# Python SDK traffic.
DEFAULT_USER_AGENT = f"zenrows/{__version__} python"

_log = logging.getLogger("zenrows.crawl.transport")

# 429 `too_many_crawls` is a capacity limit, not a transient failure.
_CREATE_RETRY_STATUSES = _RETRYABLE_STATUSES - {429}

# A stop is safe to repeat (an ended crawl answers as it ended), so
# `stop` retries these statuses itself even though it is a POST without
# an idempotency key. 503 includes `crawl_busy`: nothing was saved and
# the crawl is still running.
_STOP_RETRY_STATUSES = _RETRYABLE_STATUSES
# The longest a `Retry-After` may make a stop wait, so a large server
# value cannot hang it.
_MAX_STOP_RETRY_AFTER = 30.0

_POLL_INTERVAL = 2.0
_POLL_BACKOFF = 1.5
_MAX_POLL_INTERVAL = 15.0


class CrawlDownload:
    """A crawl's NDJSON download, open and ready to read.

    `status` is the crawl's status when the file was read
    (`X-Crawl-Status`): `running` means the file holds only what the
    crawl has kept so far. Iterate `lines` once for the parsed lines;
    the connection closes when the iteration ends. Use it as a context
    manager to close it without reading to the end.

    If the stream breaks before it ends, the lines parsed up to the
    break are still yielded, then `CrawlDownloadError` (a
    `CrawlAPIError`) is raised: the file is incomplete. The connection
    is closed first, so the error is the last thing the iteration does.
    """

    def __init__(self, response: httpx.Response, close: Callable[[], None]):
        self.status: CrawlStatus | None = _crawl_status(response)
        self._response = response
        self._close = close

    @property
    def lines(self) -> Iterator[DownloadLine]:
        return self._lines()

    def _lines(self) -> Iterator[DownloadLine]:
        try:
            stream = self._response.iter_lines()
            while True:
                try:
                    line = next(stream)
                except StopIteration:
                    return
                except httpx.StreamError:
                    # The response was already read or closed: a second
                    # pass over `lines`, not a body that broke. Let it
                    # surface as the misuse it is.
                    raise
                except Exception as err:
                    # Reading the next line failed, so the stream ended
                    # before its end: the lines yielded so far are good,
                    # but the file is incomplete. Only the read is
                    # guarded — parsing happens below — so a malformed
                    # line still surfaces as the parse error it is.
                    raise CrawlDownloadError(err) from err
                if line.strip():
                    yield DownloadLine.model_validate_json(line)
        finally:
            # Close on a clean end and on a break alike.
            self.close()

    def close(self) -> None:
        self._close()

    def __enter__(self) -> "CrawlDownload":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class ZenRowsCrawlClient:
    """Synchronous, typed client for the ZenRows Crawl API (New).

    `api_key` is required:

        client = ZenRowsCrawlClient(api_key=os.environ["ZENROWS_API_KEY"])

    Every non-2xx raises `CrawlAPIError`; branch on its `status_code`
    and `code`. An account without Crawl enabled gets 403 `REQS008`
    (`CRAWL_NOT_ENABLED_CODE`).
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = 30.0,
        retries: int = 3,
        verify_ssl: bool | str = True,
        user_agent: str = DEFAULT_USER_AGENT,
        httpx_args: dict[str, Any] | None = None,
    ):
        """`base_url` defaults to `https://api.zenrows.com/v1`. `retries` bounds
        automatic retries of transient failures (HTTP 429/502/503/504
        and network errors) on idempotent requests — GETs, and a
        `create` that carries an `idempotency_key` (never on 429 for
        `create`). Retries use jittered exponential backoff and honor
        `Retry-After`; set `retries=0` to disable. It also bounds the
        retries of a `stop` answered 429, 502, 503 or 504."""
        if not api_key:
            raise ValueError("ZenRowsCrawlClient: api_key is required.")
        self._retries = max(0, retries)
        self._t = _Transport(
            base_url=base_url or _DEFAULT_BASE_URL,
            api_key=api_key,
            user_agent=user_agent,
            timeout=timeout,
            retries=retries,
            verify=verify_ssl,
            httpx_args=httpx_args,
            error_from_response=CrawlAPIError.from_response,
            log=_log,
        )

    # ----- lifecycle -----

    def __enter__(self) -> "ZenRowsCrawlClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._t.close()

    @property
    def base_url(self) -> str:
        return self._t.base_url

    # ===== crawls =====

    def create(
        self,
        url: str,
        *,
        depth: int | None = None,
        max_items: int | None = None,
        max_pages: int | None = None,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        output_format: Literal["html", "markdown"] | None = None,
        discovery: list[Literal["links"]] | None = None,
        webhook: CrawlWebhookDict | None = None,
        idempotency_key: str | None = None,
    ) -> Crawl:
        """`POST /crawls` — start a crawl and return it, `running`.

        - `depth`: link hops to follow from `url` (1 to 100,000).
          Omit it for no depth limit: the crawl follows links until
          `max_items` or `max_pages` stops it, or no link is left.
        - `max_items`: stop after keeping this many URLs (server
          default 100).
        - `max_pages`: stop after fetching this many pages, which
          bounds the cost (server default 100).
        - `include_patterns` / `exclude_patterns`: substrings matched
          against each normalized URL. A URL is kept when it matches
          an include pattern (if any) and no exclude pattern. A crawl
          whose patterns reject every link ends `completed` with no
          results and no `error`. A crawl stays on the start URL's
          registrable domain; subdomains count.
        - `output_format="html"` or `"markdown"`: also fetch every kept
          URL's page, as HTML or as Markdown, readable with `content` /
          `download`. Omit for URLs only.
        - `discovery`: how the crawl finds the URLs it follows on each
          page it walks. `["links"]` follows the links in the page's
          HTML, and is what a crawl runs with when you leave it out.
        - `webhook`: get notified when the crawl starts and how it
          ends: `{"url": "https://...", "events": [...], "metadata": {...}}`
          (see `CrawlWebhookDict`). `events` defaults to all four of
          `crawl.started`, `crawl.completed`, `crawl.stopped` and
          `crawl.failed`. Each event is POSTed to `url` as JSON
          (`CrawlWebhookEvent`) and signed with the account's webhook
          signing key; check it with `verify_webhook_signature`. The
          returned crawl's `webhook` shows it. Without a signing key on
          the account, `create` raises 422
          `webhook_signing_key_required`; a bad `url`, `events` or
          `metadata` raises 422 `invalid_webhook`.
        - `idempotency_key`: a retry with the same key and body answers
          with the crawl the first request created.

        Only the arguments you pass are sent. When the account has
        reached its limit of active jobs (3 by default), shared with its
        Batch jobs, `create` raises 429 `too_many_crawls` without
        retrying; retry after the error's `retry_after` seconds.
        """
        body: dict[str, Any] = {"url": url}
        optional: dict[str, Any] = {
            "depth": depth,
            "max_items": max_items,
            "max_pages": max_pages,
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
            "output_format": output_format,
            "discovery": discovery,
            "webhook": webhook,
        }
        body.update({k: v for k, v in optional.items() if v is not None})
        headers = {"Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = self._t.request(
            "POST",
            "/crawls",
            headers=headers,
            content=json.dumps(body).encode("utf-8"),
            retry_statuses=_CREATE_RETRY_STATUSES,
        )
        return Crawl.model_validate(response.json())

    def get(
        self,
        crawl_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> CrawlWithResults:
        """`GET /crawls/{crawl_id}` — the crawl and one page of its
        results (`limit` 1 to 10,000, server default 1,000). Pass the
        page's `next_cursor` as `cursor` for the URLs kept since. For
        every result, prefer `results`."""
        return CrawlWithResults.model_validate(
            self._t.request_json(
                "GET", f"/crawls/{crawl_id}", params={"cursor": cursor, "limit": limit}
            )
        )

    def list(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> CrawlList:
        """`GET /crawls` — one page of the account's crawls, newest
        first (`limit` 1 to 100, server default 20), without results.
        Pass the page's `next_cursor` as `cursor` for the next page; it
        is None on the last page."""
        return CrawlList.model_validate(
            self._t.request_json("GET", "/crawls", params={"cursor": cursor, "limit": limit})
        )

    def results(
        self,
        crawl_id: str,
        *,
        limit: int | None = None,
        follow: bool = False,
        timeout: float | None = None,
        poll_interval: float = _POLL_INTERVAL,
        max_poll_interval: float = _MAX_POLL_INTERVAL,
    ) -> Iterator[CrawlResult]:
        """Yield the URLs the crawl has kept, in order, each once.

        Follows `next_cursor`, `limit` URLs per request. By default it
        returns at the first empty page or null cursor, and does not
        poll: on a running crawl it yields only what the crawl has kept
        so far. Call `wait` first to read every result.

        `follow=True` follows a running crawl instead of stopping at
        the first empty page. When the scan catches up with the crawl
        (an empty page, same cursor back) it waits `poll_interval`
        seconds — doubling up to `max_poll_interval` (2s and 15s by
        default) — and asks again, until the crawl ends (`completed`,
        `stopped` or `failed`) and the cursor is empty. Results
        arriving meanwhile are yielded as they come, so a caller can
        act on URLs while the crawl still runs. Once the crawl has
        ended it returns by the default rule. A null cursor ends the
        scan at once either way: asking again without one would repeat
        results already yielded.

        `timeout` bounds the whole scan in seconds, `follow` or not.
        When it runs out the scan simply stops — no exception — and
        the iteration ends. Leave it out (`None`) to keep following
        until the crawl ends.

        A crawl that failed or stopped is followed to its end too, not
        raised: read its `error` with `get`.
        """
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        interval = max(0.0, poll_interval)
        cap = max(interval, max_poll_interval)
        cursor: str | None = None
        first = True
        while True:
            # `timeout` is the whole scan's budget. The first page is
            # always asked for — what the crawl has kept so far is worth
            # having — and once the budget is spent the scan ends
            # quietly instead of asking again.
            if not first and deadline is not None and time.monotonic() >= deadline:
                return
            first = False
            page = self.get(crawl_id, cursor=cursor, limit=limit)
            yield from page.results
            if page.next_cursor is None:
                return
            cursor = page.next_cursor
            if page.results:
                # Progress: ask again at once, and start the backoff over.
                interval = max(0.0, poll_interval)
                continue
            if not follow or page.status is not CrawlStatus.RUNNING:
                return
            # Caught up with a running crawl: wait, then ask the same
            # cursor again for the URLs kept since.
            wait = min(interval * random.uniform(0.8, 1.2), cap)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                wait = min(wait, remaining)
            time.sleep(wait)
            interval = min(interval * _POLL_BACKOFF, cap)

    def stop(self, crawl_id: str) -> CrawlStop:
        """`POST /crawls/{crawl_id}/stop` — stop a running crawl.

        Idempotent: a crawl that already ended answers as it ended.
        The answer carries no counts; read the final coverage and
        results with `get` once pages already in flight have finished.

        Because repeating a stop is safe, `stop` retries a 429, 502,
        503 (including `crawl_busy`, which means the stop was not saved
        and the crawl is still running) or 504 up to `retries` times,
        waiting `Retry-After` seconds (at most 30; 1 when missing or
        unusable) each time, then raises the last error. Network errors
        are not retried.
        """
        attempt = 0
        while True:
            try:
                return CrawlStop.model_validate(
                    self._t.request_json("POST", f"/crawls/{crawl_id}/stop")
                )
            except CrawlAPIError as err:
                if err.status_code not in _STOP_RETRY_STATUSES or attempt >= self._retries:
                    raise
                time.sleep(_stop_retry_wait(err.retry_after))
                attempt += 1

    # ===== contents =====

    def content(
        self,
        crawl_id: str,
        content_id: str | CrawlResult,
        *,
        download: bool = False,
    ) -> str:
        """`GET /crawls/{crawl_id}/contents/{content_id}` — one kept
        URL's page, as text in the crawl's `output_format` (HTML or
        Markdown).

        `content_id` is a `CrawlResult` whose `content_status` is
        `fetched`, its `content_url`, or its content id (the last
        segment of the `content_url`). Only crawls created with an
        `output_format` have contents; anything else is 404
        `content_not_found`.

        `download=True` asks for the page as a file to save: the server
        sends `Content-Disposition: attachment` with a file name built
        from the content id. The text returned is the same either way.
        """
        if isinstance(content_id, CrawlResult):
            if not content_id.content_url:
                raise ValueError(
                    f"content: {content_id.url} has no content_url "
                    f"(content_status={content_id.content_status})"
                )
            content_id = content_id.content_url
        segment = content_id.rstrip("/").rsplit("/", 1)[-1]
        params = {"download": "true"} if download else None
        return self._t.request("GET", f"/crawls/{crawl_id}/contents/{segment}", params=params).text

    def download(self, crawl_id: str) -> CrawlDownload:
        """`GET /crawls/{crawl_id}/download` — open the NDJSON file and
        return it as a `CrawlDownload`: iterate its `lines` for parsed
        `DownloadLine`s (url, content_status, content), and read its
        `status`.

        On a running crawl the file holds what the crawl has kept so
        far, and `status` is `running`; call `wait` first for all of it.
        """
        stack = ExitStack()
        response = stack.enter_context(self._t.stream("GET", f"/crawls/{crawl_id}/download"))
        return CrawlDownload(response, stack.close)

    # ===== waiter =====

    def wait(self, crawl_id: str, *, timeout: float = 600.0) -> Crawl:
        """Poll the crawl until it ends (`completed`, `stopped` or
        `failed`) or `timeout` seconds pass, and return it.

        A `failed` crawl is returned, not raised: read `error` on it.
        So is a crawl that `stopped` because credits ran out or the API
        key hit a credit cap; `error` says which.
        On timeout the crawl is returned still `running`; call `wait`
        again to keep waiting, or `stop` to end it.
        """
        deadline = time.monotonic() + timeout
        interval = _POLL_INTERVAL
        while True:
            # limit=1 keeps each poll cheap; results are not returned.
            page = self.get(crawl_id, limit=1)
            crawl = Crawl.model_validate(page.model_dump(exclude={"results", "next_cursor"}))
            remaining = deadline - time.monotonic()
            if crawl.status is not CrawlStatus.RUNNING or remaining <= 0:
                return crawl
            time.sleep(min(interval * random.uniform(0.8, 1.2), remaining))
            interval = min(interval * _POLL_BACKOFF, _MAX_POLL_INTERVAL)


def _stop_retry_wait(retry_after: float | None) -> float:
    """Seconds `stop` waits after a 429/502/503/504: the server's
    `Retry-After`, 1s when it is missing or unusable, at most 30s."""
    if retry_after is None or not math.isfinite(retry_after) or retry_after < 0:
        return 1.0
    return min(retry_after, _MAX_STOP_RETRY_AFTER)


def _crawl_status(response: httpx.Response) -> CrawlStatus | None:
    status = response.headers.get("X-Crawl-Status")
    return CrawlStatus(status) if status else None


__all__ = [
    "DEFAULT_USER_AGENT",
    "CrawlDownload",
    "ZenRowsCrawlClient",
]

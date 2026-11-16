"""RFC 9457 problem+json → `CrawlAPIError`.

The Crawl API answers errors as `application/problem+json` with a
stable `code`. Branch on
`status_code` + `code`; display `detail`, don't parse it.
"""

import json
import math

import httpx

from zenrows.batch.errors import ProblemDetail

# The code of the 403 answer to a key whose account does not have
# Crawl enabled.
CRAWL_NOT_ENABLED_CODE = "REQS008"


def _problem_code(raw: bytes) -> str | None:
    # Read from the body: Batch's `ProblemDetail.code` defaults a missing
    # code to "internal".
    try:
        code = json.loads(raw).get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) else None


class CrawlAPIError(Exception):
    """A non-2xx response from the Crawl API.

    `code` is the problem's `code` member: `crawl_not_found`,
    `content_not_found`, `invalid_parameter`, `too_many_crawls`, ...,
    `REQS008` when Crawl is not enabled for the account, and the API's
    usual auth and credit codes; None when the body has no code.
    `problem` holds the whole body, or None when it was not JSON.
    """

    def __init__(
        self,
        status_code: int,
        problem: ProblemDetail | None,
        raw: bytes,
        *,
        retry_after: float | None = None,
    ):
        self.status_code = status_code
        # Seconds the server asked to wait (`Retry-After`), e.g. on 429
        # `too_many_crawls`.
        self.retry_after = retry_after
        self.problem = problem
        self.raw = raw
        self.code: str | None = _problem_code(raw) if problem else None
        self.detail: str | None = problem.detail if problem else None
        if problem and self.code == CRAWL_NOT_ENABLED_CODE:
            msg = f"{status_code} Crawl is not enabled for this account ({self.code})" + (
                f": {problem.detail}" if problem.detail else ""
            )
        elif problem:
            reason = problem.detail or self.code
            msg = f"{status_code} {problem.title}" + (f": {reason}" if reason else "")
        else:
            msg = f"{status_code} (no problem body)"
        super().__init__(msg)

    @classmethod
    def from_response(cls, response: httpx.Response) -> "CrawlAPIError":
        try:
            retry_after: float | None = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = None
        # A negative, NaN or infinite value is no wait a caller can sleep for.
        if retry_after is not None and (not math.isfinite(retry_after) or retry_after < 0):
            retry_after = None
        return cls(
            status_code=response.status_code,
            problem=ProblemDetail.from_response(response),
            raw=response.content,
            retry_after=retry_after,
        )


class CrawlDownloadError(CrawlAPIError):
    """A crawl's NDJSON download ended before it was complete.

    Raised while iterating `CrawlDownload.lines` when the connection
    breaks before the stream ends — a dropped socket, a truncated body.
    The lines parsed before the break have already been yielded; this
    error tells the caller the file is incomplete, so read it again (a
    finished crawl's file holds every result). The connection is closed
    before it is raised.

    It subclasses `CrawlAPIError` so one `except CrawlAPIError` handles
    a bad response and a broken body alike. `status_code` is 0 — there
    was no HTTP answer, the body just stopped — and `cause` is the
    underlying transport error when there was one.
    """

    def __init__(self, cause: BaseException | None = None):
        super().__init__(0, None, b"")
        self.cause = cause
        detail = f": {cause}" if cause else ""
        # Keep the message in `args`, so `str()` reads it like any error.
        self.args = (f"crawl download ended before it was complete{detail}",)


__all__ = ["CRAWL_NOT_ENABLED_CODE", "CrawlAPIError", "CrawlDownloadError"]

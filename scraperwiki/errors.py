"""Exceptions raised by the ScraperWiki library."""


class Error(Exception):
    """All ScraperWiki exceptions are instances of this class
    (usually via a subclass)."""


class CPUTimeExceededError(Error):
    """CPU time limit exceeded."""


class ScrapeError(Error):
    """Base class for failures raised by :func:`scraperwiki.scrape`.

    Every ``ScrapeError`` exposes the URL that failed as ``url`` and includes
    it in the exception message.
    """

    def __init__(self, message, url=None):
        self.url = url
        super().__init__(message)


class ScrapeHTTPError(ScrapeError):
    """The server responded with a non-2xx HTTP status.

    ``status_code`` holds the HTTP status code that was returned.
    """

    def __init__(self, url, status_code, reason=None):
        self.status_code = status_code
        detail = f" ({reason})" if reason else ""
        super().__init__(f"HTTP error {status_code}{detail} for URL {url}", url=url)


class ScrapeNetworkError(ScrapeError):
    """The connection to the server failed (DNS, refused, reset, ...)."""

    def __init__(self, url, reason=None):
        self.reason = reason
        message = f"Connection error for URL {url}"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message, url=url)


class ScrapeTimeoutError(ScrapeError):
    """The request timed out."""

    def __init__(self, url, timeout=None):
        self.timeout = timeout
        if timeout is None:
            message = f"Timed out for URL {url}"
        else:
            message = f"Timed out after {timeout} seconds for URL {url}"
        super().__init__(message, url=url)

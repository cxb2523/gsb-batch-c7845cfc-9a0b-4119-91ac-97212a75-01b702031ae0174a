"""Shared fakes for the scrape + preprocess pipeline tests.

The fakes mirror just enough of the Playwright ``page``/``browser`` API used by
``scraper.scrape_url`` so tests can exercise fault handling without a real
browser or network.
"""

import hashlib
import json

import pytest


class FakeResponse:
    def __init__(self, status: int = 200, status_text: str = "OK"):
        self.status = status
        self.status_text = status_text

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class FakePage:
    """A page that either yields HTML or raises configured exceptions."""

    def __init__(self, html: str = "", response=None, error: Exception = None,
                 navigation_error: Exception = None):
        self._html = html
        self._response = response
        self._error = error
        self._navigation_error = navigation_error
        self.closed = False

    async def goto(self, url, wait_until=None, timeout=None):
        if self._navigation_error is not None:
            raise self._navigation_error
        return self._response

    async def wait_for_load_state(self, state=None, timeout=None):
        if self._error is not None:
            raise self._error

    async def content(self):
        return self._html

    async def close(self):
        self.closed = True


class FakeBrowser:
    """Hands out pre-configured pages in order, one per ``new_page`` call."""

    def __init__(self, pages: list):
        self._pages = list(pages)
        self.created_pages = []
        self.closed = False

    async def new_page(self):
        page = self._pages.pop(0)
        self.created_pages.append(page)
        return page

    async def close(self):
        self.closed = True

    @property
    def leaked_pages(self):
        return [page for page in self.created_pages if not page.closed]


@pytest.fixture
def read_cache():
    def _read(path):
        with open(path, "r", encoding="utf-8") as cache_file:
            content = cache_file.read().strip()
        return json.loads(content) if content else {}
    return _read


@pytest.fixture
def html_page():
    return """
    <html>
      <body>
        <nav id="nav">Navigation links</nav>
        <main id="content">
          <h1>API Reference</h1>
          <p>GET /v1/things returns all things.</p>
        </main>
        <footer id="footer">Footer</footer>
      </body>
    </html>
    """


def md5_of(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()

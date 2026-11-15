"""Shared fixtures for retriever tests (local HTML, fake fetch/robots)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from retriever.indexer import IndexManager  # noqa: E402
from retriever.web import CrawledPage, WebCrawler  # noqa: E402


class FakeRobots:
    """Robots oracle with an explicit deny-list (everything else allowed)."""

    def __init__(self, denied: set[str] | None = None) -> None:
        self.denied = set(denied or ())
        self.checked: list[str] = []

    def allowed(self, url: str, agent: str = "*") -> bool:
        self.checked.append(url)
        return url not in self.denied


class FakeSite:
    """In-memory site: maps URL -> raw HTML; HTTP failures are configured."""

    def __init__(self, pages: dict[str, str] | None = None,
                 errors: dict[str, BaseException] | None = None) -> None:
        self.pages = dict(pages or {})
        self.errors = dict(errors or {})
        self.requested: list[str] = []

    def fetch(self, url: str) -> CrawledPage | None:
        self.requested.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url not in self.pages:
            return None
        html = self.pages[url]
        return CrawledPage(url=url, text=self._extract(html),
                           status_code=200, raw_html=html)

    @staticmethod
    def _extract(html: str) -> str:
        return WebCrawler._extract(html)


@pytest.fixture
def index() -> IndexManager:
    return IndexManager(chunk_size=128)


@pytest.fixture
def fake_robots() -> FakeRobots:
    return FakeRobots()


def make_crawler(site: FakeSite, robots: FakeRobots, **kwargs) -> WebCrawler:
    """Build a crawler wired to an in-memory site with no politeness delay."""
    kwargs.setdefault("delay_ms", 0)
    return WebCrawler(fetcher=site.fetch, robots=robots, **kwargs)

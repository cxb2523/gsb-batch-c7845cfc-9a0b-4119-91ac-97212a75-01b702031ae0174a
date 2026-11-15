"""
Unit tests for per-page failure accounting in multi-page search scraping.

Covers both entry points with a stubbed page fetcher (no network):
- GET /inserate      → scrapers.inserate_ultra_optimized.ultra_optimized_scrape_inserate
- POST /inserate-by-url → scrapers.inserate_by_url.scrape_by_url

A page behavior in the `behavior` dict is one of:
- list of listings            → page succeeds and returns them
- Exception instance          → the fetch raises
- ("fail", "error message")   → the fetch returns failed PageMetrics

Run with:  pytest tests/test_failed_pages.py -v
"""

import asyncio
from datetime import datetime

import pytest

from scrapers.inserate_by_url import scrape_by_url
from scrapers.inserate_ultra_optimized import (
    UltraOptimizedScraper,
    ultra_optimized_scrape_inserate,
)
from utils.asyncio_optimizations import EventLoopOptimizer
from utils.performance import PageMetrics

BY_URL = "https://www.kleinanzeigen.de/s-autos/c216"

CUTOFF = datetime(2026, 11, 10)
NEW = datetime(2026, 11, 15).isoformat()
OLD = datetime(2026, 11, 1).isoformat()


class FakeBrowserManager:
    """Just enough of OptimizedPlaywrightManager for the scraper scaffolding."""

    def __init__(self):
        self._semaphore = asyncio.Semaphore(4)

    def get_performance_metrics(self):
        return {"contexts_in_use": 0, "contexts_in_pool": 0}


def make_listing(adid, published_at=None, is_top_ad=False):
    return {
        "adid": adid,
        "url": f"https://www.kleinanzeigen.de/s-anzeige/ad-{adid}/{adid}",
        "title": f"Ad {adid}",
        "price": "100",
        "location": "10115 Berlin",
        "distance_km": None,
        "description": "",
        "published_at": published_at,
        "is_top_ad": is_top_ad,
    }


def _metrics(page_num, url, success, results_count=0, error=None):
    return PageMetrics(
        page_number=page_num,
        url=url,
        start_time=0.0,
        end_time=0.1,
        success=success,
        retry_count=0 if success else 2,
        error_message=error,
        results_count=results_count,
    )


def patch_fetch(monkeypatch, behavior: dict) -> list:
    """Stub UltraOptimizedScraper.ultra_optimized_fetch_page.

    Returns the list of page numbers actually requested, in request order.
    """
    calls = []

    async def fake_fetch(self, url, page_num, retry_count=2, extra_selectors=None):
        calls.append(page_num)
        outcome = behavior[page_num]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, tuple) and outcome[0] == "fail":
            return [], _metrics(page_num, url, False, error=outcome[1]), {}
        return outcome, _metrics(page_num, url, True, results_count=len(outcome)), {}

    monkeypatch.setattr(
        UltraOptimizedScraper, "ultra_optimized_fetch_page", fake_fetch
    )
    return calls


@pytest.fixture(autouse=True)
def _no_uvloop(monkeypatch):
    """Don't swap the event-loop policy mid test session."""
    monkeypatch.setattr(EventLoopOptimizer, "setup_uvloop", lambda: False)


@pytest.fixture
def fast_sleep(monkeypatch):
    """Collapse the 2s inter-page delay in scrape_by_url."""
    real_sleep = asyncio.sleep

    async def _sleep(delay, *args, **kwargs):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)


def assert_page_stats_consistent(result: dict):
    metrics = result["performance_metrics"]
    failed = result.get("failed_pages", [])
    assert metrics["pages_requested"] == metrics["pages_successful"] + len(failed)
    assert result["pages_fetched"] == metrics["pages_successful"]


# ── GET /inserate (concurrent batches) ───────────────────────────────────────


async def test_inserate_raised_page_recorded_and_fetching_continues(monkeypatch):
    calls = patch_fetch(
        monkeypatch,
        {
            1: [make_listing("a1")],
            2: RuntimeError("page 2 exploded"),
            3: [make_listing("a3")],
            4: [make_listing("a4")],
        },
    )

    result = await ultra_optimized_scrape_inserate(
        browser_manager=FakeBrowserManager(), query="x", page=1, page_count=4
    )

    assert calls == [1, 2, 3, 4]
    assert [r["adid"] for r in result["results"]] == ["a1", "a3", "a4"]

    failed = result["failed_pages"]
    assert len(failed) == 1
    assert failed[0]["page"] == 2
    assert "s-seite:2" in failed[0]["url"]
    assert "page 2 exploded" in failed[0]["error"]

    assert_page_stats_consistent(result)
    assert result["pages_fetched"] == 3
    assert result["performance_metrics"]["pages_requested"] == 4


async def test_inserate_failed_metrics_recorded_and_fetching_continues(monkeypatch):
    calls = patch_fetch(
        monkeypatch,
        {
            1: [make_listing("a1")],
            2: ("fail", "connection reset"),
            3: [make_listing("a3")],
        },
    )

    result = await ultra_optimized_scrape_inserate(
        browser_manager=FakeBrowserManager(), query="x", page=1, page_count=3
    )

    # A failed page must not look like the end of the result list.
    assert calls == [1, 2, 3]
    assert [r["adid"] for r in result["results"]] == ["a1", "a3"]
    assert [f["page"] for f in result["failed_pages"]] == [2]
    assert "connection reset" in result["failed_pages"][0]["error"]
    assert_page_stats_consistent(result)


async def test_inserate_all_success_has_no_failed_pages(monkeypatch):
    calls = patch_fetch(
        monkeypatch,
        {1: [make_listing("a1")], 2: [make_listing("a2")]},
    )

    result = await ultra_optimized_scrape_inserate(
        browser_manager=FakeBrowserManager(), query="x", page=1, page_count=2
    )

    assert calls == [1, 2]
    assert "failed_pages" not in result
    assert result["pages_fetched"] == 2
    assert_page_stats_consistent(result)


async def test_inserate_min_publish_date_ignores_failed_pages(monkeypatch):
    calls = patch_fetch(
        monkeypatch,
        {
            1: [make_listing("a1", NEW)],
            2: RuntimeError("page 2 exploded"),
            3: [make_listing("a3", NEW)],
            4: [make_listing("a4", OLD)],  # old non-top listing → stop here
        },
    )

    result = await ultra_optimized_scrape_inserate(
        browser_manager=FakeBrowserManager(),
        query="x",
        page=1,
        page_count=4,
        min_publish_date=CUTOFF,
    )

    # The failed page must not count as "reached the time boundary": pages
    # after it were still fetched, and the stop came from page 4's old listing.
    assert calls == [1, 2, 3, 4]
    assert [r["adid"] for r in result["results"]] == ["a1", "a3"]
    assert [f["page"] for f in result["failed_pages"]] == [2]
    assert_page_stats_consistent(result)
    assert result["pages_fetched"] == 3


async def test_inserate_old_top_ad_does_not_trigger_date_stop(monkeypatch):
    calls = patch_fetch(
        monkeypatch,
        {
            1: [make_listing("top1", OLD, is_top_ad=True), make_listing("a1", NEW)],
            2: [make_listing("a2", NEW)],
        },
    )

    result = await ultra_optimized_scrape_inserate(
        browser_manager=FakeBrowserManager(),
        query="x",
        page=1,
        page_count=2,
        min_publish_date=CUTOFF,
    )

    assert calls == [1, 2]
    assert "failed_pages" not in result
    # The old top ad is filtered out of the results but must not stop paging.
    assert [r["adid"] for r in result["results"]] == ["a1", "a2"]


# ── POST /inserate-by-url (sequential) ───────────────────────────────────────


async def test_by_url_raised_page_recorded_and_fetching_continues(
    monkeypatch, fast_sleep
):
    calls = patch_fetch(
        monkeypatch,
        {
            1: [make_listing("a1")],
            2: RuntimeError("page 2 exploded"),
            3: [make_listing("a3")],
        },
    )

    result = await scrape_by_url(
        FakeBrowserManager(), BY_URL, max_pages=3
    )

    assert calls == [1, 2, 3]
    assert [r["adid"] for r in result["results"]] == ["a1", "a3"]

    failed = result["failed_pages"]
    assert len(failed) == 1
    assert failed[0]["page"] == 2
    assert "seite:2" in failed[0]["url"]
    assert "page 2 exploded" in failed[0]["error"]

    assert_page_stats_consistent(result)
    assert result["pages_fetched"] == 2
    assert result["performance_metrics"]["pages_requested"] == 3


async def test_by_url_failed_metrics_do_not_look_like_end_of_results(
    monkeypatch, fast_sleep
):
    calls = patch_fetch(
        monkeypatch,
        {
            1: [make_listing("a1")],
            2: ("fail", "timeout"),
            3: [make_listing("a3")],
        },
    )

    result = await scrape_by_url(FakeBrowserManager(), BY_URL, max_pages=3)

    # Old behavior broke the loop on the failed page's empty result list.
    assert calls == [1, 2, 3]
    assert [r["adid"] for r in result["results"]] == ["a1", "a3"]
    assert [f["page"] for f in result["failed_pages"]] == [2]
    assert "timeout" in result["failed_pages"][0]["error"]
    assert_page_stats_consistent(result)


async def test_by_url_all_success_has_no_failed_pages(monkeypatch, fast_sleep):
    calls = patch_fetch(
        monkeypatch,
        {1: [make_listing("a1")], 2: [make_listing("a2")]},
    )

    result = await scrape_by_url(FakeBrowserManager(), BY_URL, max_pages=2)

    assert calls == [1, 2]
    assert "failed_pages" not in result
    assert result["pages_fetched"] == 2
    assert_page_stats_consistent(result)


async def test_by_url_min_publish_date_stops_only_on_successful_page(
    monkeypatch, fast_sleep
):
    calls = patch_fetch(
        monkeypatch,
        {
            1: [make_listing("a1", NEW)],
            2: RuntimeError("page 2 exploded"),
            3: [make_listing("a3", OLD)],  # old non-top listing → stop here
            4: [make_listing("a4", NEW)],
        },
    )

    result = await scrape_by_url(
        FakeBrowserManager(), BY_URL, max_pages=4, min_publish_date=CUTOFF
    )

    # Page 2 failing must not stop paging; the stop comes from page 3's old
    # listing, so page 4 is never requested.
    assert calls == [1, 2, 3]
    assert [r["adid"] for r in result["results"]] == ["a1"]
    assert [f["page"] for f in result["failed_pages"]] == [2]
    assert_page_stats_consistent(result)
    assert result["pages_fetched"] == 2

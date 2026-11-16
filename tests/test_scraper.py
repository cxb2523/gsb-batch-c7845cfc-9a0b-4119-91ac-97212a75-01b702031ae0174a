"""Scraper fault tolerance: one bad page must not sink the batch."""

import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from airbyte_connector_generator_poc.report import PageReport
from airbyte_connector_generator_poc.scraper import (
    DEFAULT_TIMEOUT_MS,
    ScrapeResult,
    scrape_urls_with_browser,
)

from .conftest import FakeBrowser, FakePage, FakeResponse

URL_A = "https://docs.example.com/a"
URL_BROKEN = "https://unreachable.example.com/b"
URL_C = "https://docs.example.com/c"

HTML = "<html><body><main><h1>Docs</h1></main></body></html>"


def _run(coro):
    return asyncio.run(coro)


def test_unreachable_page_is_skipped_and_others_scrape_in_order(html_page):
    browser = FakeBrowser([
        FakePage(html=html_page, response=FakeResponse(200)),
        FakePage(error=None, navigation_error=PlaywrightError(
            "net::ERR_NAME_NOT_RESOLVED at https://unreachable.example.com/b")),
        FakePage(html=html_page, response=FakeResponse(200)),
    ])

    result = _run(scrape_urls_with_browser([URL_A, URL_BROKEN, URL_C], browser))

    assert isinstance(result, ScrapeResult)
    assert list(result.keys()) == [URL_A, URL_C]
    assert result[URL_A] == html_page

    failed = result.failures
    assert len(failed) == 1
    assert failed[0].url == URL_BROKEN
    assert "ERR_NAME_NOT_RESOLVED" in failed[0].error
    assert failed[0].used_for_generation is False


def test_non_2xx_response_is_recorded_as_failure(html_page):
    browser = FakeBrowser([
        FakePage(html="", response=FakeResponse(404, "Not Found")),
        FakePage(html=html_page, response=FakeResponse(200)),
    ])

    result = _run(scrape_urls_with_browser([URL_BROKEN, URL_A], browser))

    assert list(result.keys()) == [URL_A]
    assert result.report.get(URL_BROKEN).error == "HTTP 404 Not Found"
    assert result.report.get(URL_BROKEN).failed_stage == "scrape"


def test_navigation_timeout_is_recorded_as_failure(html_page):
    browser = FakeBrowser([
        FakePage(navigation_error=PlaywrightTimeoutError(
            "Timeout 30000ms exceeded.")),
        FakePage(html=html_page, response=FakeResponse(200)),
    ])

    result = _run(scrape_urls_with_browser([URL_BROKEN, URL_A], browser))

    assert list(result.keys()) == [URL_A]
    error = result.report.get(URL_BROKEN).error
    assert "timeout" in error.lower()
    assert str(DEFAULT_TIMEOUT_MS) in error


def test_pages_are_closed_even_when_they_fail(html_page):
    browser = FakeBrowser([
        FakePage(navigation_error=PlaywrightError("boom")),
        FakePage(html=html_page, response=FakeResponse(200)),
    ])

    _run(scrape_urls_with_browser([URL_BROKEN, URL_A], browser))

    assert browser.leaked_pages == []


def test_all_pages_failing_returns_empty_result_with_every_reason():
    browser = FakeBrowser([
        FakePage(navigation_error=PlaywrightError("net::ERR_CONNECTION_REFUSED")),
        FakePage(navigation_error=PlaywrightError("net::ERR_ADDRESS_UNREACHABLE")),
    ])

    result = _run(scrape_urls_with_browser([URL_A, URL_BROKEN], browser))

    assert dict(result) == {}
    assert [f.url for f in result.failures] == [URL_A, URL_BROKEN]
    assert all(f.error for f in result.failures)


def test_report_passed_in_is_reused_and_ordered():
    report = PageReport([URL_C, URL_A])
    browser = FakeBrowser([
        FakePage(html=HTML, response=FakeResponse(200)),
        FakePage(html=HTML, response=FakeResponse(200)),
    ])

    result = _run(scrape_urls_with_browser(
        [URL_C, URL_A], browser, report=report))

    assert result.report is report
    assert [o.url for o in result.report.outcomes] == [URL_C, URL_A]
    assert list(result.keys()) == [URL_C, URL_A]

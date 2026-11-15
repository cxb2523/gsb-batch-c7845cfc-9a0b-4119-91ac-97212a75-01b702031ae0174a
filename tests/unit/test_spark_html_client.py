"""Unit tests for the batch page collection in SparkHtmlClient.

All tests run against in-memory browser/page doubles (injected via
``browser_factory``); no real browser or network is involved.
"""

import asyncio
from collections import Counter

import pytest

from spark_history_mcp.api.spark_html_client import (
    AuthenticationError,
    PageResult,
    SparkHtmlClient,
)
from spark_history_mcp.config.config import AuthConfig, ServerConfig

BASE = "http://spark-history:18080/history/"


def url_for(path):
    return BASE + path.lstrip("/")


class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakePage:
    def __init__(self, context):
        self.context = context
        self.url = None
        self.viewport = None
        self.closed = False

    async def set_viewport_size(self, size):
        self.viewport = size

    async def goto(self, url):
        self.url = url
        browser = self.context.browser
        browser.goto_counts[url] += 1

        # Optional auth gate: decides the HTTP status based on the context's
        # credentials, simulating an expired/renewed authenticated session.
        if browser.gate is not None:
            status = browser.gate(self.context, url)
            if status is not None:
                return FakeResponse(status)

        behavior = browser.routes.get(url, 200)
        if behavior == "hang":
            # Simulates a page that never finishes loading; relies on the
            # caller's per-page timeout to cancel it.
            await asyncio.sleep(3600)
            return FakeResponse(200)
        if isinstance(behavior, Exception):
            raise behavior
        return FakeResponse(behavior)

    async def wait_for_load_state(self, state):
        assert state == "networkidle"

    async def wait_for_timeout(self, ms):
        # Do not actually wait; rendering wait is irrelevant for the doubles.
        return None

    async def content(self):
        return f"<html>rendered:{self.url}</html>"

    async def screenshot(self, path=None, **kwargs):
        self.screenshot_path = path

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, browser, kwargs):
        self.browser = browser
        self.kwargs = kwargs
        self.pages = []
        self.closed = False

    async def new_page(self):
        page = FakePage(self)
        self.pages.append(page)
        return page

    async def close(self):
        self.closed = True


class FakeBrowser:
    """Browser double.

    routes: mapping of URL -> 200/"hang"/Exception.
    gate: optional callable(context, url) -> status int or None, consulted
          before routes (used to simulate 401/403 auth failures).
    """

    def __init__(self, routes=None, gate=None):
        self.routes = routes or {}
        self.gate = gate
        self.contexts = []
        self.goto_counts = Counter()
        self.closed = False

    async def new_context(self, **kwargs):
        context = FakeContext(self, kwargs)
        self.contexts.append(context)
        return context

    async def close(self):
        self.closed = True


def make_client(browser, timeout=5, auth=None):
    config = ServerConfig(url="http://spark-history:18080", timeout=timeout)
    if auth:
        config.auth = AuthConfig(username=auth[0], password=auth[1])
    factory_calls = []

    async def factory():
        factory_calls.append(1)
        return browser

    client = SparkHtmlClient(config, browser_factory=factory)
    client._factory_calls = factory_calls
    return client


# ----------------------------------------------------------------------
# Batch collection: success, ordering, per-page failure isolation
# ----------------------------------------------------------------------


async def test_collect_pages_all_success_preserves_order_and_closes_browser():
    paths = ["app-1/jobs/", "app-1/stages/", "app-2/executors/"]
    browser = FakeBrowser()
    client = make_client(browser)

    results = await client.collect_pages(paths)

    assert [r.path for r in results] == paths
    assert all(r.success for r in results)
    assert all(r.error is None for r in results)
    for path, result in zip(paths, results, strict=True):
        assert url_for(path) in result.content
    # One browser for the whole batch, closed at the end.
    assert len(client._factory_calls) == 1
    assert browser.closed is True
    # Pages were closed after use.
    for context in browser.contexts:
        assert all(page.closed for page in context.pages)


async def test_collect_pages_timeout_and_error_do_not_stop_other_pages():
    paths = ["ok-1", "slow", "boom", "ok-2"]
    browser = FakeBrowser(
        routes={
            url_for("slow"): "hang",
            url_for("boom"): RuntimeError("render exploded"),
        }
    )
    client = make_client(browser, timeout=5)
    client.page_timeout = 0.2  # keep the test fast

    results = await client.collect_pages(paths)

    assert [r.path for r in results] == paths

    assert results[0].success and "rendered" in results[0].content
    assert results[3].success and "rendered" in results[3].content

    assert not results[1].success
    assert "timed out" in results[1].error
    assert results[1].content is None

    assert not results[2].success
    assert "RuntimeError" in results[2].error
    assert "render exploded" in results[2].error

    assert browser.closed is True


async def test_collect_pages_empty_input():
    browser = FakeBrowser()
    client = make_client(browser)

    assert await client.collect_pages([]) == []
    # No pages -> the browser is never even launched.
    assert len(client._factory_calls) == 0


# ----------------------------------------------------------------------
# Authentication expiry: one re-auth retry per failing page
# ----------------------------------------------------------------------


def expired_first_session_gate(browser):
    """401 for pages loaded in the first context; healthy afterwards."""

    def gate(context, url):
        return 401 if context is browser.contexts[0] else None

    return gate


async def test_auth_expiry_retries_page_once_and_succeeds():
    paths = ["app-1/jobs/", "app-1/stages/"]
    browser = FakeBrowser()
    browser.gate = expired_first_session_gate(browser)
    client = make_client(browser, auth=("user", "pass"))

    results = await client.collect_pages(paths)

    assert all(r.success for r in results)
    assert [r.path for r in results] == paths
    # First page needed the re-auth retry; the renewed session was reused
    # for the second page, which did not need a retry.
    assert results[0].retried is True
    assert results[1].retried is False
    # Exactly one retry: the failing page was requested twice, the other once.
    assert browser.goto_counts[url_for("app-1/jobs/")] == 2
    assert browser.goto_counts[url_for("app-1/stages/")] == 1
    # A new authenticated session (context) was established for the retry.
    assert len(browser.contexts) == 2
    assert browser.contexts[0].closed is True
    renewed_credentials = browser.contexts[1].kwargs["http_credentials"]
    assert renewed_credentials == {"username": "user", "password": "pass"}
    assert browser.closed is True


async def test_auth_retry_still_failing_records_failure_once():
    browser = FakeBrowser(gate=lambda context, url: 401)
    client = make_client(browser, auth=("user", "pass"))

    results = await client.collect_pages(["app-1/jobs/", "app-2/jobs/"])

    assert [r.path for r in results] == ["app-1/jobs/", "app-2/jobs/"]
    assert not any(r.success for r in results)
    assert all("401" in r.error for r in results)
    assert all(r.retried for r in results)
    # Each page was attempted exactly twice: initial try + one retry.
    assert browser.goto_counts[url_for("app-1/jobs/")] == 2
    assert browser.goto_counts[url_for("app-2/jobs/")] == 2
    # One renewed session per failing page, never more.
    assert len(browser.contexts) == 3
    assert browser.closed is True


async def test_auth_failure_without_credentials_is_not_retried():
    browser = FakeBrowser(gate=lambda context, url: 403)
    client = make_client(browser)  # no auth configured

    results = await client.collect_pages(["app-1/jobs/"])

    assert len(results) == 1
    assert not results[0].success
    assert "403" in results[0].error
    assert results[0].retried is False
    assert browser.goto_counts[url_for("app-1/jobs/")] == 1
    assert browser.closed is True


async def test_auth_failure_between_healthy_pages_keeps_results_in_order():
    paths = ["ok", "expired", "ok-again"]
    browser = FakeBrowser()

    # The session expires only for the middle page; the renewed session
    # (second context) is healthy again.
    def gate(context, url):
        if context is browser.contexts[0] and "expired" in url:
            return 401
        return None

    browser.gate = gate
    client = make_client(browser, auth=("user", "pass"))

    results = await client.collect_pages(paths)

    assert [r.path for r in results] == paths
    assert [r.success for r in results] == [True, True, True]
    assert [r.retried for r in results] == [False, True, False]


# ----------------------------------------------------------------------
# Backwards-compatible single-page API
# ----------------------------------------------------------------------


async def test_get_rendered_html_single_page():
    browser = FakeBrowser()
    client = make_client(browser)

    html = await client.get_rendered_html("app-1/jobs/")
    await client.close()

    assert url_for("app-1/jobs/") in html
    assert browser.closed is True


async def test_get_rendered_html_raises_after_failed_retry():
    browser = FakeBrowser(gate=lambda context, url: 401)
    client = make_client(browser, auth=("user", "pass"))

    with pytest.raises(AuthenticationError):
        await client.get_rendered_html("app-1/jobs/")
    await client.close()

    # Initial attempt + exactly one retry.
    assert browser.goto_counts[url_for("app-1/jobs/")] == 2


async def test_page_result_dataclass_defaults():
    result = PageResult(path="p", success=True, content="html")
    assert result.error is None
    assert result.retried is False

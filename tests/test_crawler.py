"""Tests for the resumable crawler (no network access required)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler import (  # noqa: E402
    CrawlSession,
    STATUS_COMPLETED,
    STATUS_STOPPED,
    save_scraped_data,
)


START = "http://example.test/"


def make_html(title, body, links=()):
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    return (
        "<html><head>"
        f"<title>{title}</title>"
        "</head><body>"
        f"<h1>{body}</h1>"
        f"{anchors}"
        "</body></html>"
    )


class FakeResponse:
    def __init__(self, content, status_code=200, raise_exc=None):
        self.content = content.encode("utf-8") if isinstance(content, str) else content
        self.status_code = status_code
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} error")


class FakeSite:
    """A tiny in-memory website.

    ``pages`` maps URL -> HTML string.  ``failures`` maps URL -> exception.
    Unknown URLs return 404.
    """

    def __init__(self, pages, failures=None):
        self.pages = pages
        self.failures = failures or {}
        self.requested = []

    def __call__(self, url):
        self.requested.append(url)
        if url in self.failures:
            exc = self.failures[url]
            if isinstance(exc, BaseException):
                raise exc
            return FakeResponse("", raise_exc=exc)
        if url in self.pages:
            return FakeResponse(self.pages[url])
        return FakeResponse("not found", status_code=404)


def test_failed_page_does_not_stop_the_crawl():
    site = FakeSite(
        {
            START: make_html("Home", "home body", ["/a", "/broken", "/b"]),
            "http://example.test/a": make_html("A", "page a"),
            "http://example.test/b": make_html("B", "page b"),
        }
    )
    session = CrawlSession(START, fetcher=site)
    session.run()

    assert session.status == STATUS_COMPLETED
    assert {page.url for page in session.successes} == {
        START,
        "http://example.test/a",
        "http://example.test/b",
    }
    assert session.success_count == 3
    assert session.failure_count == 1
    failed = session.failures[0]
    assert failed.url == "http://example.test/broken"
    assert "404" in failed.error
    assert session.total_visited == 4


def test_connection_error_is_recorded_and_crawl_continues():
    site = FakeSite(
        {
            START: make_html("Home", "home body", ["/a", "/down", "/b"]),
            "http://example.test/a": make_html("A", "page a"),
            "http://example.test/b": make_html("B", "page b"),
        },
        failures={"http://example.test/down": OSError("connection reset")},
    )
    session = CrawlSession(START, fetcher=site)
    session.run()

    assert session.success_count == 3
    assert session.failure_count == 1
    assert "connection reset" in session.failures[0].error
    # the crawl really did visit the pages queued after the broken one
    assert "http://example.test/b" in [page.url for page in session.successes]


def test_stop_then_resume_does_not_refetch_visited_pages():
    site = FakeSite(
        {
            START: make_html("Home", "home", ["/a", "/b", "/c"]),
            "http://example.test/a": make_html("A", "page a"),
            "http://example.test/b": make_html("B", "page b"),
            "http://example.test/c": make_html("C", "page c"),
        }
    )
    session = CrawlSession(START, fetcher=site)

    assert session.step() is True  # start page
    session.stop()
    visited_before_stop = list(session.visited)
    pending_before_stop = list(session.pending)
    successes_before_stop = list(session.successes)
    failures_before_stop = list(session.failures)

    # A stop must take effect immediately: no further page is fetched.
    assert session.step() is False
    assert session.status == STATUS_STOPPED
    assert session.visited == visited_before_stop
    assert list(session.pending) == pending_before_stop

    # Resume: state carries over, nothing already visited is fetched again.
    assert session.resume() == "running"
    session.run()

    assert session.status == STATUS_COMPLETED
    assert len(site.requested) == len(set(site.requested)), "a URL was fetched twice"
    assert set(site.requested) == {
        START,
        "http://example.test/a",
        "http://example.test/b",
        "http://example.test/c",
    }
    assert session.success_count == 4
    assert session.failure_count == 0
    assert session.total_visited == 4
    assert successes_before_stop[0] in session.successes
    assert failures_before_stop == []


def test_stop_and_resume_with_failures_keeps_counts_correct():
    site = FakeSite(
        {
            START: make_html("Home", "home", ["/a", "/missing"]),
            "http://example.test/a": make_html("A", "page a", ["/deep"]),
            "http://example.test/deep": make_html("Deep", "deep page"),
        }
    )
    session = CrawlSession(START, fetcher=site)

    session.step()  # start -> discovers /a and /missing
    assert session.pending_count == 2
    session.step()  # one of the two discovered pages, then stop
    leftovers = list(session.pending)
    session.stop()
    visited_at_stop = session.total_visited
    failures_at_stop = list(session.failures)
    successes_at_stop = list(session.successes)

    assert session.step() is False
    assert session.total_visited == visited_at_stop
    assert session.failures == failures_at_stop
    assert session.successes == successes_at_stop
    assert list(session.pending) == leftovers

    session.resume()
    session.run()

    assert session.success_count == 3
    assert session.failure_count == 1
    assert session.total_visited == 4
    assert session.success_count + session.failure_count == session.total_visited
    assert len(site.requested) == len(set(site.requested))
    # the failure recorded before the stop is still there, exactly once
    assert [f.url for f in session.failures] == ["http://example.test/missing"]


def test_no_successful_page_leaves_existing_file_untouched(tmp_path):
    data_path = tmp_path / "scraped_content.json"
    original = [{"url": "http://old.test/", "title": "Old", "content": "old body"}]
    data_path.write_text(json.dumps(original, indent=4), encoding="utf-8")
    original_bytes = data_path.read_bytes()

    site = FakeSite(
        {},
        failures={
            START: OSError("START_URL unreachable"),
            "http://oserror.test/unused": OSError("unused"),
        },
    )
    session = CrawlSession(START, fetcher=site)
    session.run()

    assert session.success_count == 0
    assert session.failure_count == 1
    assert session.start_url_error is not None

    assert session.save_results(str(data_path)) is False

    assert data_path.read_bytes() == original_bytes
    assert json.loads(data_path.read_text(encoding="utf-8")) == original


def test_save_results_writes_ingest_compatible_json(tmp_path):
    site = FakeSite(
        {
            START: make_html("Home", "home body", ["/a"]),
            "http://example.test/a": make_html("Page A", "page a body"),
        }
    )
    session = CrawlSession(START, fetcher=site)
    session.run()

    data_path = tmp_path / "scraped_content.json"
    assert session.save_results(str(data_path)) is True

    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 2
    for item in data:
        assert set(item) == {"url", "title", "content"}
        assert item["content"].strip()
    titles = {item["title"] for item in data}
    assert titles == {"Home", "Page A"}


def test_save_scraped_data_refuses_empty_payload(tmp_path):
    data_path = tmp_path / "scraped_content.json"
    data_path.write_text("[]", encoding="utf-8")

    assert save_scraped_data([], str(data_path)) is False
    assert data_path.read_text(encoding="utf-8") == "[]"


def test_crawler_skips_asset_extensions_and_foreign_domains():
    site = FakeSite(
        {
            START: make_html(
                "Home",
                "home",
                [
                    "/doc.pdf",
                    "/image.jpg",
                    "/archive.zip",
                    "/icon.svg",
                    "https://other.test/out",
                    "/real",
                ],
            ),
            "http://example.test/real": make_html("Real", "real page"),
        }
    )
    session = CrawlSession(START, fetcher=site)
    session.run()

    assert set(site.requested) == {START, "http://example.test/real"}
    assert session.failure_count == 0


def test_crawl_respects_max_pages():
    pages = {START: make_html("Home", "home", [f"/p{i}" for i in range(10)])}
    for i in range(10):
        pages[f"http://example.test/p{i}"] = make_html(f"P{i}", f"page {i}")
    site = FakeSite(pages)

    session = CrawlSession(START, max_pages=4, fetcher=site)
    session.run()

    assert session.total_visited == 4
    assert len(site.requested) == 4
    assert session.status == STATUS_COMPLETED
    assert session.reached_page_limit


def test_empty_body_page_is_recorded_as_failure():
    site = FakeSite({START: "<html><head><title>Empty</title></head><body></body></html>"})
    session = CrawlSession(START, fetcher=site)
    session.run()

    assert session.success_count == 0
    assert session.failure_count == 1
    assert session.failures[0].url == START


def test_runner_saves_results_and_reports_counts(tmp_path):
    from crawler import CrawlRunner

    site = FakeSite(
        {
            START: make_html("Home", "home", ["/a"]),
            "http://example.test/a": make_html("A", "page a"),
        }
    )
    session = CrawlSession(START, fetcher=site)
    data_path = tmp_path / "scraped_content.json"
    runner = CrawlRunner(session, save_path=str(data_path))

    runner.start()
    runner._thread.join(timeout=10)

    assert not runner.is_alive()
    assert runner.saved is True
    assert runner.blank_round is False
    assert json.loads(data_path.read_text(encoding="utf-8"))[0]["title"] == "Home"
    assert session.success_count == 2


def test_runner_does_not_overwrite_when_round_has_no_success(tmp_path):
    from crawler import CrawlRunner

    data_path = tmp_path / "scraped_content.json"
    existing = [{"url": "http://old.test/", "title": "Old", "content": "old"}]
    data_path.write_text(json.dumps(existing), encoding="utf-8")
    original = data_path.read_bytes()

    site = FakeSite({}, failures={START: OSError("unreachable")})
    runner = CrawlRunner(CrawlSession(START, fetcher=site), save_path=str(data_path))
    runner.start()
    runner._thread.join(timeout=10)

    assert runner.blank_round is True
    assert runner.saved is False
    assert data_path.read_bytes() == original


def test_runner_stop_then_resume_via_public_api(tmp_path):
    from crawler import CrawlRunner

    class SlowSite(FakeSite):
        def __init__(self, pages, on_request):
            super().__init__(pages)
            self.on_request = on_request

        def __call__(self, url):
            self.on_request(url)
            return super().__call__(url)

    pages = {START: make_html("Home", "home", [f"/p{i}" for i in range(5)])}
    for i in range(5):
        pages[f"http://example.test/p{i}"] = make_html(f"P{i}", f"page {i}")

    stop_after = {"count": 0}
    runner_holder = {}

    def on_request(url):
        stop_after["count"] += 1
        if stop_after["count"] == 2:
            runner_holder["runner"].stop()

    site = SlowSite(pages, on_request)
    session = CrawlSession(START, fetcher=site)
    runner = CrawlRunner(session, save_path=str(tmp_path / "out.json"))
    runner_holder["runner"] = runner

    runner.start()
    runner._thread.join(timeout=10)

    assert not runner.is_alive()
    assert session.status == STATUS_STOPPED
    assert session.pending_count > 0
    fetched_first_round = list(site.requested)
    assert len(fetched_first_round) == 2

    runner.start()
    runner._thread.join(timeout=10)

    assert session.status == STATUS_COMPLETED
    assert len(site.requested) == len(set(site.requested))
    assert session.total_visited == 6
    assert session.success_count == 6
    assert session.failure_count == 0


def test_ingest_rejects_missing_file(tmp_path):
    from ingest import IngestError, load_scraped_records

    with pytest.raises(IngestError):
        load_scraped_records(str(tmp_path / "nope.json"))


def test_scrape_cli_uses_same_rules_and_does_not_write_empty_result(
    tmp_path, monkeypatch
):
    import scrape

    data_path = tmp_path / "scraped_content.json"
    data_path.write_text(
        '[{"url": "http://old.test/", "title": "Old", "content": "x"}]',
        encoding="utf-8",
    )
    original = data_path.read_bytes()

    site = FakeSite({}, failures={START: OSError("unreachable")})
    monkeypatch.setattr(
        scrape,
        "CrawlSession",
        lambda url, max_pages=200: CrawlSession(
            url, max_pages=max_pages, fetcher=site
        ),
    )

    assert scrape.crawl_website(START) == []
    assert scrape.save_scraped_data([], str(data_path)) is False
    assert data_path.read_bytes() == original
